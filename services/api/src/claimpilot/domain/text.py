"""Text that came off a receipt, made safe to show or quote.

Receipts are untrusted input. Anything copied from one into a title, a question, a finding or a
prompt goes through here: one line, no control characters, no double quotes (they would end a quoted
phrase early), and a length limit, so a crafted value cannot add line breaks or a wall of text.
"""

from __future__ import annotations


def plain_text(value: str | None, limit: int = 40) -> str:
    if not value:
        return ""
    printable = "".join(ch if ch.isprintable() else " " for ch in value)
    return " ".join(printable.replace('"', "'").split())[:limit].rstrip()
