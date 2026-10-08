"""Claims: grouping a pile of documents into claims, the questions to ask, and the state machine.

Pure functions over the shared contract types (``claimpilot.domain``): no I/O, no LLM, no clock
unless ``today`` is left out. The pipeline composes them (see :func:`finalize_claim`).
"""

from claimpilot.claims.grouping import group_documents
from claimpilot.claims.questions import (
    CalendarEvent,
    apply_calendar,
    build_questions,
    combined_prompt,
    has_receipt_evidence,
)
from claimpilot.claims.service import build_claims, finalize_claim
from claimpilot.claims.state import (
    AUTO_APPROVE_LIMIT,
    InvalidTransition,
    UnknownQuestion,
    answer_question,
    confirm_and_submit,
    decide,
    refresh_status,
    route,
)

__all__ = [
    "AUTO_APPROVE_LIMIT",
    "CalendarEvent",
    "InvalidTransition",
    "UnknownQuestion",
    "answer_question",
    "apply_calendar",
    "build_claims",
    "build_questions",
    "combined_prompt",
    "confirm_and_submit",
    "decide",
    "finalize_claim",
    "group_documents",
    "has_receipt_evidence",
    "refresh_status",
    "route",
]
