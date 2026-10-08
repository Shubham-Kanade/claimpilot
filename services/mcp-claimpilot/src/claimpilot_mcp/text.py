"""Text that came from a receipt (or any API field), made safe to hand to a model.

Receipts are untrusted input. A merchant name, a line item, or a finding that quotes one can say
"approve this claim", and a model reading tool results may be tempted to obey. Every string this
server returns that did not originate here goes through ``plain_text``: one line, no control
characters, no angle brackets (so nothing can forge or close a tag), and a length limit. It mirrors
``claimpilot.domain.text.plain_text`` in the API and is reimplemented here on purpose: this
service talks to the API over REST only and imports nothing from it.

The result models declare which fields carry such text with the ``Ident``, ``Name``, ``Sentence``
and ``Paragraph`` types below, so the cleaning happens when a result is built and cannot be
forgotten at a call site.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from pydantic import AfterValidator

ELLIPSIS = chr(0x2026)  # the horizontal ellipsis character
# Look-alike single quotation marks: still readable, but they can no longer open or close a tag.
DEFANG = str.maketrans({"<": chr(0x2039), ">": chr(0x203A)})


def plain_text(value: str | None, limit: int = 120) -> str:
    """One printable line without angle brackets, at most ``limit`` characters (cut ends in ...)."""
    if not value:
        return ""
    printable = "".join(ch if ch.isprintable() else " " for ch in value)
    line = " ".join(printable.translate(DEFANG).split())
    if len(line) > limit:
        return line[: limit - 1].rstrip() + ELLIPSIS
    return line


def _cleaner(limit: int) -> Callable[[str], str]:
    def clean(value: str) -> str:
        return plain_text(value, limit)

    return clean


# Receipt-derived (or API-supplied) strings, by how long they may be.
Ident = Annotated[str, AfterValidator(_cleaner(128))]  # ids, status values, codes
Name = Annotated[str, AfterValidator(_cleaner(120))]  # titles, merchants, file names
Sentence = Annotated[str, AfterValidator(_cleaner(400))]  # finding messages, questions, answers
Paragraph = Annotated[str, AfterValidator(_cleaner(1000))]  # the assistant's follow-up message
