"""What to ask the employee, and how to ask it once.

"Ask only what's necessary": a question is built only when the claim cannot be completed without
the answer, and answers already on the claim (given earlier, read from the calendar or stated on
the receipt) are never asked again. Every question carries a stable id (``q-<kind>-<hash>``), the
documents it is about, and plain text, so the chat agent can put all of them in ONE message
(:func:`combined_prompt`) and route the reply back by id.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from collections.abc import Sequence
from typing import Protocol

from claimpilot.claims import labels
from claimpilot.claims.state import refresh_status
from claimpilot.domain import (
    Claim,
    ClaimMode,
    ExpenseCategory,
    ExtractedReceipt,
    OpenQuestion,
    ProcessedDocument,
    QuestionKind,
)

# Defaults mirror policy.yaml (3.1 and 10.1); finalize_claim passes the loaded policy's values.
DEFAULT_SELF_DECLARATION_LIMIT = 200.0
DEFAULT_PERSONAL_THRESHOLD = 0.5

_KIND_ORDER = (
    QuestionKind.attendees,
    QuestionKind.business_purpose,
    QuestionKind.missing_date,
    QuestionKind.confirm_personal,
    QuestionKind.self_declaration,
    QuestionKind.other,
)
_STATED_ATTENDEES = re.compile(
    r"\b(?:attendees?|guests?|participants?|attended by)\s*[:\-]\s*(.{3,200})", re.IGNORECASE
)
_STATED_PURPOSE = re.compile(r"\b(?:purpose|reason|occasion)\s*[:\-]\s*(.{3,200})", re.IGNORECASE)
CLIENT_EVENT_KINDS = frozenset({"client_dinner", "client_meeting"})


def has_receipt_evidence(receipt: ExtractedReceipt) -> bool:
    """Is there anything beyond a total on the document: an invoice number, UPI reference ...?

    A UPI screenshot counts (it proves the payment); a handwritten auto-fare slip with nothing but
    a name and an amount does not.
    """
    return bool(
        receipt.invoice_number
        or receipt.upi_reference
        or receipt.merchant_gstin
        or receipt.line_items
    )


def _qid(kind: QuestionKind, scope: Sequence[str]) -> str:
    digest = hashlib.sha256("|".join([kind.value, *sorted(scope)]).encode()).hexdigest()[:8]
    return f"q-{kind.value}-{digest}"


def _stated_on_document(doc: ProcessedDocument, pattern: re.Pattern[str]) -> str | None:
    """A value the receipt itself states ("Guests: ...", "Purpose: ..."), if any.

    Never taken from a document that contains instructions to an AI system: receipt text is data,
    and a crafted receipt must not answer questions for the employee.
    """
    if doc.receipt.contains_instructions:
        return None
    for item in doc.receipt.line_items:
        if match := pattern.search(item.description):
            value = labels.safe_text(match.group(1), limit=200)
            if value:
                return f"from receipt: {value}"
    return None


def _question(
    kind: QuestionKind,
    scope: Sequence[str],
    document_ids: Sequence[str],
    text: str,
    answer: str | None = None,
) -> OpenQuestion:
    return OpenQuestion(
        id=_qid(kind, scope), kind=kind, text=text, document_ids=list(document_ids), answer=answer
    )


def _amount_and_day(doc: ProcessedDocument) -> str:
    day = doc.expense_date
    return labels.rupees(doc.amount) + (f" on {labels.short_day_label(day)}" if day else "")


def _needs_declaration(doc: ProcessedDocument, limit: float) -> bool:
    """Small conveyance with no real receipt: the policy accepts the employee's own word (3.1)."""
    return (
        doc.category is ExpenseCategory.local_conveyance
        and doc.receipt.total is not None
        and doc.amount <= limit
        and not has_receipt_evidence(doc.receipt)
    )


def build_questions(
    claim: Claim,
    docs: Sequence[ProcessedDocument],
    *,
    self_declaration_limit: float = DEFAULT_SELF_DECLARATION_LIMIT,
    personal_threshold: float = DEFAULT_PERSONAL_THRESHOLD,
) -> list[OpenQuestion]:
    """The questions this claim needs answered, with any existing answers carried over.

    * trip: the business purpose (once per trip)
    * client entertainment: who attended and why, unless the receipt already says
    * a document without a readable date: the date
    * a document System One thinks is personal: confirm it is business, or drop it
    * small conveyance with no receipt: a self-declaration (one question for all of them)
    """
    by_id = {doc.id: doc for doc in docs}
    members = [by_id[i] for i in claim.document_ids if i in by_id]
    wanted: list[OpenQuestion] = []

    if claim.mode is ClaimMode.trip and members:
        wanted.append(
            _question(
                QuestionKind.business_purpose,
                [claim.id],
                claim.document_ids,
                f"What was the business purpose of the {claim.title}?",
            )
        )
    for doc in members:
        day = doc.expense_date
        when = f" on {labels.short_day_label(day)}" if day else ""
        if doc.category is ExpenseCategory.client_entertainment:
            what = f"the client dinner{when} ({labels.rupees(doc.amount)})"
            wanted.append(
                _question(
                    QuestionKind.attendees,
                    [doc.id],
                    [doc.id],
                    f"Who attended {what}? Please give names and company.",
                    _stated_on_document(doc, _STATED_ATTENDEES),
                )
            )
            wanted.append(
                _question(
                    QuestionKind.business_purpose,
                    [doc.id],
                    [doc.id],
                    f"What was the business purpose of {what}?",
                    _stated_on_document(doc, _STATED_PURPOSE),
                )
            )
        if day is None:
            wanted.append(
                _question(
                    QuestionKind.missing_date,
                    [doc.id],
                    [doc.id],
                    f"What is the date on {labels.describe(doc)}? It is missing or illegible.",
                )
            )
        if doc.decisions.personal_expense >= personal_threshold:
            wanted.append(
                _question(
                    QuestionKind.confirm_personal,
                    [doc.id],
                    [doc.id],
                    f"{labels.sentence(labels.describe(doc))} looks personal. Is it a "
                    "business expense? If yes, say what it was for; if not, I will leave it out.",
                )
            )
    undeclared = [d for d in members if _needs_declaration(d, self_declaration_limit)]
    if undeclared:
        items = ", ".join(_amount_and_day(d) for d in undeclared)
        if len(undeclared) == 1:
            text = (
                f"There is no receipt for this small conveyance item ({items}). Policy lets you "
                "declare it yourself: please confirm it was for work and give the route or purpose."
            )
        else:
            text = (
                f"There is no receipt for these small conveyance items ({items}). Policy lets you "
                "declare them yourself: please confirm they were for work and give the route or "
                "purpose of each."
            )
        ids = [d.id for d in undeclared]
        wanted.append(_question(QuestionKind.self_declaration, ids, ids, text))

    previous = {q.id: q for q in claim.open_questions}
    merged = [
        q.model_copy(update={"answer": previous[q.id].answer})
        if q.id in previous and previous[q.id].answered
        else q
        for q in wanted
    ]
    return sorted(merged, key=lambda q: _KIND_ORDER.index(q.kind))  # stable within a kind


def combined_prompt(claim: Claim) -> str | None:
    """ONE message asking every unanswered question of the claim; ``None`` if nothing to ask."""
    open_questions = claim.unanswered
    if not open_questions:
        return None
    count = len(open_questions)
    intro = (
        f"To finish {labels.quote(claim.title)} I need one detail:"
        if count == 1
        else f"To finish {labels.quote(claim.title)} I need a few details:"
    )
    lines = [f"{i}. {q.text}" for i, q in enumerate(open_questions, 1)]
    outro = "You can answer in one message." if count > 1 else ""
    return "\n".join([intro, *lines, *([outro] if outro else [])])


# --- calendar -----------------------------------------------------------------------------------


class CalendarEvent(Protocol):
    """What we need from a calendar entry (the real ones come from the corporate calendar MCP)."""

    @property
    def date(self) -> dt.date: ...
    @property
    def title(self) -> str: ...
    @property
    def attendees(self) -> Sequence[str]: ...
    @property
    def kind(self) -> str: ...


def _is_client_event(event: CalendarEvent) -> bool:
    return str(event.kind).casefold() in CLIENT_EVENT_KINDS


def _event_for_document(
    doc: ProcessedDocument, events: Sequence[CalendarEvent]
) -> CalendarEvent | None:
    """The one client event on the document's date; ``None`` when there is none or it is unclear.

    ``events`` are already limited to client dinners and meetings.
    """
    day = doc.expense_date
    if day is None:
        return None
    same_day = [e for e in events if e.date == day]
    preferred = "client_dinner" if doc.category is ExpenseCategory.client_entertainment else None
    narrowed = [e for e in same_day if str(e.kind).casefold() == preferred]
    pool = narrowed or same_day
    return pool[0] if len(pool) == 1 else None  # two candidates: better to ask than to guess


def _calendar_answer(
    question: OpenQuestion,
    claim: Claim,
    by_id: dict[str, ProcessedDocument],
    events: Sequence[CalendarEvent],
) -> str | None:
    if claim.mode is ClaimMode.trip and question.kind is QuestionKind.business_purpose:
        if claim.start_date is None or claim.end_date is None:
            return None
        during = sorted(
            (e for e in events if claim.start_date <= e.date <= claim.end_date),
            key=lambda e: (e.date, e.title),
        )
        titles = [e.title for e in during]
        return f"from calendar: {'; '.join(titles)}" if titles else None
    for doc_id in question.document_ids:
        doc = by_id.get(doc_id)
        event = _event_for_document(doc, events) if doc else None
        if event is None:
            continue
        if question.kind is QuestionKind.business_purpose:
            return f"from calendar: {event.title}"
        if question.kind is QuestionKind.attendees and event.attendees:
            return f"from calendar: {event.title} (attendees: {', '.join(event.attendees)})"
    return None


def apply_calendar(
    claim: Claim, docs: Sequence[ProcessedDocument], events: Sequence[CalendarEvent]
) -> Claim:
    """Answer attendee and purpose questions from a calendar event on the same day.

    Only client dinners and client meetings count, and only when exactly one fits (a clash is
    asked about, not guessed). Answers record where they came from, e.g.
    ``from calendar: Client dinner - Orion Retail``. Answered questions are left alone, so this is
    safe to call repeatedly.
    """
    client_events = [e for e in events if _is_client_event(e)]
    by_id = {doc.id: doc for doc in docs}
    updated: list[OpenQuestion] = []
    for question in claim.open_questions:
        answer = None
        if not question.answered and question.kind in (
            QuestionKind.attendees,
            QuestionKind.business_purpose,
        ):
            answer = _calendar_answer(question, claim, by_id, client_events)
        updated.append(question.model_copy(update={"answer": answer}) if answer else question)
    return refresh_status(claim.model_copy(update={"open_questions": updated}))
