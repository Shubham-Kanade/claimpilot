from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from claimpilot.config import Settings
from claimpilot.decisions import (
    DOCUMENT_QUESTIONS,
    CascadeEngine,
    DecisionAuthError,
    DecisionRequestError,
    DecisionUnavailableError,
    JevEngine,
    LLMEngine,
    Question,
    decide_document,
    document_state,
    get_engine,
    to_decisions,
)
from claimpilot.decisions.jev import JEV_INPUT_USD_PER_MTOK
from claimpilot.decisions.llm_engine import answer_model
from claimpilot.decisions.types import Answer, DecisionResult
from claimpilot.domain import DocType, ExpenseCategory, ExtractedReceipt, LineItem
from claimpilot.llm.fake import FakeLLM

RECEIPT = ExtractedReceipt(
    doc_type=DocType.restaurant_bill,
    merchant_name="Chai Point Express",
    merchant_gstin="27AAPFU0939F1ZV",
    merchant_city="Pune",
    invoice_number="INV-77",
    date="2026-10-03",
    total=472.5,
    line_items=[
        LineItem(description="Masala Dosa", amount=180.0),
        LineItem(description="Draught Beer 330ml", amount=320.0),
    ],
)


# --- questions & state ----------------------------------------------------------------------


def test_question_validation():
    with pytest.raises(ValueError, match="criteria map"):
        Question("k", "choice", "?", criteria=None)
    with pytest.raises(ValueError, match="2-10 levels"):
        Question("k", "score", "?", criteria=["only one"])
    with pytest.raises(ValueError, match="list of levels"):
        Question("k", "score", "?", criteria={"a": None})
    with pytest.raises(ValueError, match="too many"):
        Question("k", "choice", "?", criteria={str(i): None for i in range(256)})


def test_question_wire_format():
    assert DOCUMENT_QUESTIONS[0].to_jev()["type"] == "choice"
    assert set(DOCUMENT_QUESTIONS[0].to_jev()["criteria"]) == {c.value for c in ExpenseCategory}
    assert DOCUMENT_QUESTIONS[1].to_jev() == {
        "type": "noul",
        "instructions": DOCUMENT_QUESTIONS[1].instructions,
    }
    score = Question("risk", "score", "How risky?", criteria=["low", "high"])
    assert score.to_jev()["criteria"] == ["low", "high"]


def test_state_is_minimal_and_drops_empty_values():
    state = document_state(RECEIPT)
    assert "merchant_gstin" not in str(state) and "INV-77" not in str(state)
    assert state["merchant"] == "Chai Point Express"
    assert state["items"][1] == {"item": "Draught Beer 330ml", "amount": 320.0}
    assert "route" not in state and "note" not in state


def test_state_notes_instructions_and_route():
    r = RECEIPT.model_copy(
        update={"contains_instructions": True, "travel_from": "Pune", "travel_to": "Delhi"}
    )
    state = document_state(r)
    assert state["route"] == "Pune to Delhi" and "ignore it" in state["note"]


# --- Jev engine -----------------------------------------------------------------------------

JEV_OK = {
    "model": "jev-1.13.0",
    "answers": {
        "category": {
            "type": "choice",
            "choice": "client_entertainment",
            "confidence": 0.93,
            "probabilities": {"client_entertainment": 0.93, "meals": 0.07},
        },
        "alcohol_present": {"type": "noul", "noul": 0.98},
        "personal_expense": {"type": "noul", "noul": 0.04},
    },
    "usage": {"input_tokens": 500, "output_tokens": 80},
}


def jev(handler: Callable[[httpx.Request], httpx.Response]) -> tuple[JevEngine, list[float]]:
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return JevEngine("test-key", client=client, sleep=sleep), sleeps  # pragma: allowlist secret


async def test_jev_request_and_response_mapping():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["authorization"]
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=JEV_OK)

    engine, _ = jev(handler)
    result = await engine.decide(document_state(RECEIPT), DOCUMENT_QUESTIONS)

    assert seen["auth"] == "Bearer test-key" and seen["url"].endswith("/v1/systemone")
    assert seen["body"]["model"] == "jev-latest"
    assert set(seen["body"]["questions"]) == {"category", "alcohol_present", "personal_expense"}
    assert seen["body"]["state"]["merchant"] == "Chai Point Express"
    assert result["category"].as_str == "client_entertainment"
    assert result["category"].confidence == 0.93
    assert result["alcohol_present"].as_float == 0.98
    assert result["alcohol_present"].confidence == pytest.approx(0.98)
    assert result.engine == "jev" and result.input_tokens == 500
    assert result.cost_usd == pytest.approx(500 * JEV_INPUT_USD_PER_MTOK / 1e6)


async def test_jev_score_answer():
    question = Question("risk", "score", "How risky?", criteria=["none", "low", "high"])

    def handler(_: httpx.Request) -> httpx.Response:
        body = {"answers": {"risk": {"type": "score", "score": 1.4, "confidence": 0.6}}}
        return httpx.Response(200, json=body)

    engine, _ = jev(handler)
    result = await engine.decide("state", [question])
    assert result["risk"].as_float == 1.4 and result["risk"].kind == "score"


@pytest.mark.parametrize("status", [429, 529, 503])
async def test_jev_retries_with_backoff_then_succeeds(status):
    calls = {"n": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(status, headers={"retry-after": "2"})
        return httpx.Response(200, json=JEV_OK)

    engine, sleeps = jev(handler)
    result = await engine.decide(document_state(RECEIPT), DOCUMENT_QUESTIONS)
    assert calls["n"] == 3 and result["category"].confidence == 0.93
    assert len(sleeps) == 2 and all(s >= 2 for s in sleeps)  # Retry-After honoured


async def test_jev_backs_off_even_without_retry_after():
    calls = {"n": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=JEV_OK) if calls["n"] == 2 else httpx.Response(529)

    engine, sleeps = jev(handler)
    await engine.decide(document_state(RECEIPT), DOCUMENT_QUESTIONS)
    assert len(sleeps) == 1 and sleeps[0] > 0


async def test_jev_gives_up_after_max_attempts():
    engine, sleeps = jev(lambda _: httpx.Response(503))
    with pytest.raises(DecisionUnavailableError, match="HTTP 503"):
        await engine.decide("s", DOCUMENT_QUESTIONS)
    assert len(sleeps) == 3


async def test_jev_transport_errors_are_retried():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    engine, _ = jev(handler)
    with pytest.raises(DecisionUnavailableError, match="ConnectError"):
        await engine.decide("s", DOCUMENT_QUESTIONS)


async def test_jev_auth_and_request_errors_are_not_retried():
    engine, sleeps = jev(lambda _: httpx.Response(401))
    with pytest.raises(DecisionAuthError):
        await engine.decide("s", DOCUMENT_QUESTIONS)
    engine, sleeps = jev(lambda _: httpx.Response(422, text="bad question"))
    with pytest.raises(DecisionRequestError, match="422"):
        await engine.decide("s", DOCUMENT_QUESTIONS)
    assert sleeps == []


async def test_jev_missing_or_mistyped_answers_are_request_errors():
    partial = {"answers": {"category": JEV_OK["answers"]["category"]}}
    engine, _ = jev(lambda _: httpx.Response(200, json=partial))
    with pytest.raises(DecisionRequestError, match="no answer"):
        await engine.decide("s", DOCUMENT_QUESTIONS)
    wrong = {"answers": {**JEV_OK["answers"], "alcohol_present": {"type": "choice", "choice": "x"}}}
    engine, _ = jev(lambda _: httpx.Response(200, json=wrong))
    with pytest.raises(DecisionRequestError, match="does not match"):
        await engine.decide("s", DOCUMENT_QUESTIONS)


def test_jev_from_settings_needs_key():
    with pytest.raises(DecisionAuthError):
        JevEngine.from_settings(Settings(jev_api_key=None))
    engine = JevEngine.from_settings(Settings(jev_api_key="k"))  # type: ignore[arg-type]
    assert engine.name == "jev"


async def test_jev_aclose():
    engine, _ = jev(lambda _: httpx.Response(200, json=JEV_OK))
    await engine.aclose()


# --- LLM engine -----------------------------------------------------------------------------


def llm_engine(models_registry, payload: dict | None = None):
    llm = FakeLLM(models_registry, env={})
    model = answer_model(DOCUMENT_QUESTIONS)
    llm.register(
        model,
        payload
        or {
            "category": "meals",
            "category_confidence": 0.8,
            "alcohol_present": 0.9,
            "personal_expense": 0.1,
        },
    )
    return LLMEngine(llm), llm


def test_answer_model_is_flat_cached_and_within_structured_output_limits():
    model = answer_model(DOCUMENT_QUESTIONS)
    assert answer_model(DOCUMENT_QUESTIONS) is model
    schema = model.model_json_schema()
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])  # nothing optional
    assert len(schema["properties"]["category"]["enum"]) == len(ExpenseCategory)
    assert all("anyOf" not in p for p in schema["properties"].values())


def test_answer_model_with_score_question():
    model = answer_model([Question("risk", "score", "How risky?", criteria=["none", "high"])])
    fields = model.model_json_schema()["properties"]
    assert set(fields) == {"risk", "risk_confidence"}
    assert "0 = none; 1 = high" in fields["risk"]["description"]


async def test_llm_engine_maps_answers_and_cost(models_registry):
    engine, llm = llm_engine(models_registry)
    result = await engine.decide(document_state(RECEIPT), DOCUMENT_QUESTIONS)
    assert result["category"].as_str == "meals" and result["category"].confidence == 0.8
    assert result["alcohol_present"].as_float == 0.9
    assert result.engine == "llm" and result.cost_usd > 0
    body = llm.requests[0].body
    assert body["thinking"] == {"type": "disabled"}  # Haiku 5.5 with thinking off
    text = body["messages"][0]["content"][0]["text"]
    assert text.startswith("<state>") and "Draught Beer" in text and "alcoholic" in text


async def test_llm_engine_clamps_out_of_range_numbers(models_registry):
    engine, _ = llm_engine(
        models_registry,
        {
            "category": "meals",
            "category_confidence": 1.7,
            "alcohol_present": -0.2,
            "personal_expense": 3.0,
        },
    )
    result = await engine.decide("state", DOCUMENT_QUESTIONS)
    assert result["category"].confidence == 1.0
    assert result["alcohol_present"].as_float == 0.0 and result["personal_expense"].as_float == 1.0


# --- cascade --------------------------------------------------------------------------------


class StubEngine:
    def __init__(self, name: str, answers: dict[str, Answer] | Exception, cost: float = 0.0):
        self.name, self._answers, self._cost = name, answers, cost
        self.asked: list[list[str]] = []

    async def decide(self, state, questions):
        self.asked.append([q.key for q in questions])
        if isinstance(self._answers, Exception):
            raise self._answers
        return DecisionResult(
            answers={q.key: self._answers[q.key] for q in questions},
            engine=self.name,
            latency_ms=10,
            cost_usd=self._cost,
        )


def answers(engine: str, category: str, confidence: float) -> dict[str, Answer]:
    return {
        "category": Answer(
            key="category", kind="choice", value=category, confidence=confidence, engine=engine
        ),
        "alcohol_present": Answer(
            key="alcohol_present", kind="noul", value=0.1, confidence=0.9, engine=engine
        ),
        "personal_expense": Answer(
            key="personal_expense", kind="noul", value=0.1, confidence=0.9, engine=engine
        ),
    }


async def test_cascade_keeps_confident_primary_answers():
    primary = StubEngine("jev", answers("jev", "meals", 0.95))
    fallback = StubEngine("llm", answers("llm", "misc", 0.9))
    result = await CascadeEngine(primary, fallback).decide("s", DOCUMENT_QUESTIONS)
    assert result["category"].as_str == "meals" and fallback.asked == []
    assert result.escalated == ()


async def test_cascade_escalates_only_unsure_questions():
    primary = StubEngine("jev", answers("jev", "meals", 0.4), cost=0.001)
    fallback = StubEngine("llm", answers("llm", "client_entertainment", 0.9), cost=0.002)
    engine = CascadeEngine(primary, fallback, min_confidence=0.7)
    result = await engine.decide("s", DOCUMENT_QUESTIONS)

    assert fallback.asked == [["category"]]  # noul answers never trigger escalation
    assert result["category"].as_str == "client_entertainment"
    assert result["category"].engine == "llm" and result["alcohol_present"].engine == "jev"
    assert result.escalated == ("category",) and result.engine == "jev+llm"
    assert result.cost_usd == pytest.approx(0.003) and result.latency_ms == 20


async def test_cascade_falls_back_when_primary_is_unavailable():
    primary = StubEngine("jev", DecisionUnavailableError("down"))
    fallback = StubEngine("llm", answers("llm", "meals", 0.9))
    result = await CascadeEngine(primary, fallback).decide("s", DOCUMENT_QUESTIONS)
    assert result.engine == "llm" and fallback.asked == [[q.key for q in DOCUMENT_QUESTIONS]]


async def test_cascade_surfaces_bad_credentials():
    primary = StubEngine("jev", DecisionAuthError("bad key"))
    fallback = StubEngine("llm", answers("llm", "meals", 0.9))
    with pytest.raises(DecisionAuthError):
        await CascadeEngine(primary, fallback).decide("s", DOCUMENT_QUESTIONS)
    assert fallback.asked == []


# --- service --------------------------------------------------------------------------------


async def test_decide_document_maps_to_domain(models_registry):
    engine, _ = llm_engine(models_registry)
    decisions, result = await decide_document(engine, RECEIPT)
    assert decisions.category is ExpenseCategory.meals
    assert decisions.alcohol_present == 0.9 and decisions.engine == "llm"
    assert result.engine == "llm"


def test_unknown_category_degrades_to_misc_with_zero_confidence():
    result = DecisionResult(
        answers=answers("jev", "teleportation", 0.99), engine="jev", latency_ms=1
    )
    decisions = to_decisions(result)
    assert decisions.category is ExpenseCategory.misc and decisions.category_confidence == 0.0


def test_get_engine_selects_by_setting(models_registry):
    llm = FakeLLM(models_registry, env={})
    assert get_engine(Settings(decision_engine="llm"), llm).name == "llm"
    cascade = get_engine(Settings(decision_engine="jev", jev_api_key="k"), llm)  # type: ignore[arg-type]
    assert cascade.name == "jev+llm"


async def test_jev_ignores_non_numeric_retry_after():
    calls = {"n": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"})
        return httpx.Response(200, json=JEV_OK)

    engine, sleeps = jev(handler)
    await engine.decide("s", DOCUMENT_QUESTIONS)
    assert len(sleeps) == 1 and sleeps[0] < 2  # fell back to the computed backoff
