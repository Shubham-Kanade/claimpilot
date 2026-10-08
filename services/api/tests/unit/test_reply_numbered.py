"""Replies that follow the prompt's numbering are split without any model call."""

from __future__ import annotations

from datetime import date

import pytest

from claimpilot.domain import Claim, ClaimMode
from claimpilot.domain.claims import OpenQuestion, QuestionKind
from claimpilot.llm.fake import FakeLLM
from claimpilot.pipeline.reply import interpret_reply, parse_numbered


@pytest.mark.parametrize(
    ("text", "count", "expected"),
    [
        ("1. Rahul and Anita\n2. Quarterly review", 2, ["Rahul and Anita", "Quarterly review"]),
        ("1) Rahul and Anita 2) Quarterly review", 2, ["Rahul and Anita", "Quarterly review"]),
        ("1. a 2. b 3. c", 3, ["a", "b", "c"]),
        ("  1.   padded  \n\n  2.  answers  ", 2, ["padded", "answers"]),
        ("1. Only the first", 2, None),  # a number is missing
        ("2. second 1. first", 2, None),  # out of order
        ("1. one 2. ", 2, None),  # nothing after a number
        ("Rahul and Anita, 2 of us, quarterly review", 2, None),  # digits are not numbering
        ("It was 1.5 hours for 2.5 people", 2, None),  # decimals are not numbering
        ("no numbers at all", 2, None),
        ("1. x", 0, None),
    ],
)
def test_parse_numbered(text, count, expected):
    assert parse_numbered(text, count) == expected


def two_questions() -> Claim:
    return Claim(
        id="clm",
        employee_id="P001",
        title="Client dinner",
        mode=ClaimMode.event,
        document_ids=["d1"],
        total=100.0,
        start_date=date(2026, 8, 14),
        open_questions=[
            OpenQuestion(id="q-att", kind=QuestionKind.attendees, text="Who came?"),
            OpenQuestion(id="q-why", kind=QuestionKind.business_purpose, text="Why?"),
        ],
    )


async def test_a_numbered_reply_is_mapped_in_order_without_calling_the_model(models_registry):
    llm = FakeLLM(models_registry, env={})
    answers = await interpret_reply(llm, two_questions(), "1. Rahul, Anita\n2. Contract renewal")
    assert answers == {"q-att": "Rahul, Anita", "q-why": "Contract renewal"}
    assert llm.requests == []


async def test_a_numbered_reply_works_even_with_no_model_at_all():
    answers = await interpret_reply(None, two_questions(), "1. Rahul 2. Renewal")
    assert answers == {"q-att": "Rahul", "q-why": "Renewal"}


async def test_a_free_text_reply_without_a_model_is_not_understood():
    assert await interpret_reply(None, two_questions(), "Rahul came for the renewal") == {}


async def test_nothing_to_answer_means_nothing_understood():
    claim = two_questions()
    answered = claim.model_copy(
        update={
            "open_questions": [
                q.model_copy(update={"answer": "done"}) for q in claim.open_questions
            ]
        }
    )
    assert await interpret_reply(None, answered, "1. a 2. b") == {}
