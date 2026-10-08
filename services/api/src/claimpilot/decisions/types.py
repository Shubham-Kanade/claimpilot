"""System One decision types: typed questions in, typed answers out.

A *question* is one of Jev's three primitives (ADR-002):
* ``choice``: pick one of N labelled options (probabilities over the options);
* ``noul``: the probability that a yes/no statement is true;
* ``score``: a position along 2-10 ordered levels.

Both engines (Jev, and the LLM fallback with the same shape) answer the same ``Question`` set,
so they are interchangeable and directly comparable (latency, cost, accuracy).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

QuestionKind = Literal["choice", "score", "noul"]

MAX_CHOICE_OPTIONS = 255  # Jev limit
MIN_SCORE_LEVELS, MAX_SCORE_LEVELS = 2, 10


@dataclass(frozen=True, slots=True)
class Question:
    key: str
    kind: QuestionKind
    instructions: str
    # choice: {option: description | None}; score: ordered level descriptions; noul: unused
    criteria: Mapping[str, str | None] | Sequence[str] | None = None
    version: int = 1

    def __post_init__(self) -> None:
        if self.kind == "choice":
            if not isinstance(self.criteria, Mapping) or not self.criteria:
                raise ValueError(f"choice question '{self.key}' needs a criteria map")
            if len(self.criteria) > MAX_CHOICE_OPTIONS:
                raise ValueError(f"choice question '{self.key}' has too many options")
        elif self.kind == "score":
            levels = self.criteria
            if isinstance(levels, Mapping) or levels is None:
                raise ValueError(f"score question '{self.key}' needs a list of levels")
            if not MIN_SCORE_LEVELS <= len(levels) <= MAX_SCORE_LEVELS:
                raise ValueError(f"score question '{self.key}' needs 2-10 levels")

    @property
    def options(self) -> tuple[str, ...]:
        assert isinstance(self.criteria, Mapping)
        return tuple(self.criteria)

    def to_jev(self) -> dict[str, Any]:
        """The question in Jev's wire format."""
        body: dict[str, Any] = {"type": self.kind, "instructions": self.instructions}
        if isinstance(self.criteria, Mapping):
            body["criteria"] = dict(self.criteria)
        elif self.criteria is not None:
            body["criteria"] = list(self.criteria)
        return body

    @property
    def signature(self) -> tuple[Any, ...]:
        crit = tuple(self.criteria.items()) if isinstance(self.criteria, Mapping) else self.criteria
        return (self.key, self.kind, self.instructions, tuple(crit or ()), self.version)


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    kind: QuestionKind
    value: str | float = Field(description="choice: the option; score: 0-based level; noul: P(yes)")
    confidence: float = Field(ge=0, le=1)
    probabilities: dict[str, float] = Field(default_factory=dict)
    engine: str = Field(description="Which engine produced this answer")

    @property
    def as_float(self) -> float:
        return float(self.value)

    @property
    def as_str(self) -> str:
        return str(self.value)


@dataclass(frozen=True, slots=True)
class DecisionResult:
    answers: dict[str, Answer]
    engine: str  # "jev", "llm", or "jev+llm" when the cascade escalated
    latency_ms: int
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    escalated: tuple[str, ...] = field(default=())  # question keys re-asked on the fallback

    def __getitem__(self, key: str) -> Answer:
        return self.answers[key]


class DecisionError(Exception):
    """A decision engine could not answer (the cascade may fall back to another engine)."""


class DecisionAuthError(DecisionError):
    """The API key is missing or rejected."""


class DecisionRequestError(DecisionError):
    """The engine rejected the request itself (a bug in our question definitions)."""


class DecisionUnavailableError(DecisionError):
    """Timeouts, rate limits, 5xx, connectivity: retried, then surfaced."""


class DecisionEngine(Protocol):
    name: str

    async def decide(
        self, state: Mapping[str, Any] | str, questions: Sequence[Question]
    ) -> DecisionResult: ...
