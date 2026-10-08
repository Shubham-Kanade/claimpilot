"""Turn the employee's single free-text reply into answers for the claim's open questions.

The assistant asks everything still open in ONE message (``combined_prompt``), so the employee
replies once. When exactly one question is open the reply *is* the answer and no model is
called (free, and nothing to misinterpret). With several open questions a small LLM call
(Haiku 5.5, structured output) splits the reply per question, copying only what was said and
leaving the rest empty; whatever stays unanswered is asked again, still in a single message.
A reply that already follows the prompt's numbering ("1. ... 2. ...") is split without any model.
"""

from __future__ import annotations

import logging
import re
from functools import cache
from importlib.resources import files
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, create_model

from claimpilot.claims import combined_prompt
from claimpilot.domain.claims import Claim
from claimpilot.llm.client import LLMClient
from claimpilot.llm.errors import LLMError

logger = logging.getLogger(__name__)

ROUTE = "reply_parse"
PROMPT_VERSION = "reply_v1"


@cache
def system_prompt(version: str = PROMPT_VERSION) -> str:
    return (files("claimpilot.pipeline") / "prompts" / f"{version}.md").read_text("utf-8")


@cache
def reply_model(question_texts: tuple[str, ...]) -> type[BaseModel]:
    """A flat object with one required string field per open question (``a0``, ``a1`` ...).

    Question ids contain hyphens, so fields are positional; ``interpret_reply`` maps them back.
    Cached per set of questions, so the same questions always share one schema class.
    """
    fields: dict[str, Any] = {
        f"a{i}": (str, Field(description=f"Answer to: {text} (empty string if not answered)"))
        for i, text in enumerate(question_texts)
    }
    return create_model("ReplyAnswers", __config__=ConfigDict(extra="forbid"), **fields)


_MARKER = re.compile(r"(?:^|(?<=\s))(\d{1,2})[.)]\s+")


def parse_numbered(text: str, count: int) -> list[str] | None:
    """Answers written as "1. ... 2. ..." in the order the questions were asked, or None.

    Every number from 1 to ``count`` must appear once, in order, with something after it; a reply
    that merely contains digits ("2 people came") is not mistaken for numbering.
    """
    if count < 1:
        return None
    wanted, starts = 1, []
    for match in _MARKER.finditer(text):
        if int(match.group(1)) == wanted:
            starts.append(match)
            wanted += 1
    if wanted != count + 1:
        return None
    ends = [m.start() for m in starts[1:]] + [len(text)]
    answers = [text[m.end() : end].strip() for m, end in zip(starts, ends, strict=True)]
    return answers if all(answers) else None


async def interpret_reply(llm: LLMClient | None, claim: Claim, text: str) -> dict[str, str]:
    """Question id -> answer, for the questions the reply actually answers."""
    questions = tuple(claim.unanswered)
    if not questions:
        return {}
    if len(questions) == 1:
        return {questions[0].id: text.strip()}
    if numbered := parse_numbered(text, len(questions)):
        return {q.id: answer for q, answer in zip(questions, numbered, strict=True)}
    if llm is None:
        return {}
    listing = "\n".join(f"{i + 1}. {q.text}" for i, q in enumerate(questions))
    message = f"Questions:\n{listing}\n\nEmployee's reply:\n<reply>\n{text}\n</reply>"
    try:
        result = await llm.parse(
            ROUTE,
            system=system_prompt(),
            content=[{"type": "text", "text": message}],
            output_model=reply_model(tuple(q.text for q in questions)),
            thinking="off",
        )
    except LLMError as exc:  # unavailable, refused, replay miss ...: degrade, never crash the chat
        logger.warning("reply interpretation unavailable: %s", exc)
        return {}
    raw = result.parsed.model_dump()
    return {q.id: raw[f"a{i}"].strip() for i, q in enumerate(questions) if raw[f"a{i}"].strip()}


def follow_up_message(claim: Claim, *, understood: bool) -> str | None:
    """What to say next: the combined prompt for whatever is still open (None when done)."""
    prompt = combined_prompt(claim)
    if prompt is None or understood:
        return prompt
    return "I couldn't match that to my questions, so I'll ask again.\n" + prompt
