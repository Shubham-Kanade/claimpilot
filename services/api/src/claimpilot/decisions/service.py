"""Document-level decisions: run the questions and map the answers into the domain contract."""

from __future__ import annotations

from collections.abc import Sequence

from claimpilot.config import Settings
from claimpilot.decisions.cascade import CascadeEngine
from claimpilot.decisions.jev import JevEngine
from claimpilot.decisions.llm_engine import LLMEngine
from claimpilot.decisions.questions import DOCUMENT_QUESTIONS, document_state
from claimpilot.decisions.types import DecisionEngine, DecisionResult
from claimpilot.domain import ExpenseCategory, ExtractedReceipt
from claimpilot.domain.claims import Decisions
from claimpilot.llm.client import LLMClient

FALLBACK_CATEGORY = ExpenseCategory.misc


def to_decisions(result: DecisionResult) -> Decisions:
    category = result["category"]
    try:
        label = ExpenseCategory(category.as_str)
    except ValueError:  # an engine invented an option: degrade to misc with zero confidence
        label, confidence = FALLBACK_CATEGORY, 0.0
    else:
        confidence = category.confidence
    return Decisions(
        category=label,
        category_confidence=confidence,
        alcohol_present=result["alcohol_present"].as_float,
        personal_expense=result["personal_expense"].as_float,
        engine=result.engine,
    )


async def decide_document(
    engine: DecisionEngine, receipt: ExtractedReceipt, *, calendar: Sequence[str] | None = None
) -> tuple[Decisions, DecisionResult]:
    result = await engine.decide(document_state(receipt, calendar), DOCUMENT_QUESTIONS)
    return to_decisions(result), result


def get_engine(settings: Settings, llm: LLMClient) -> DecisionEngine:
    """``jev`` = Jev with confidence-gated LLM fallback; ``llm`` = LLM only."""
    llm_engine = LLMEngine(llm)
    if settings.decision_engine == "jev":
        jev = JevEngine.from_settings(settings)
        return CascadeEngine(jev, llm_engine, min_confidence=settings.decision_min_confidence)
    return llm_engine
