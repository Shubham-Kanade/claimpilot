"""Payloads shaped like the ClaimPilot API's responses (synthetic data, fictional merchants)."""

from __future__ import annotations

from typing import Any

import httpx

PERSONA = "DEMO-ASHA"
CLAIM_ID = "clm-DEMO-ASHA-3e75eb6a21"
BATCH_ID = "0309acb347854a0ebc9c10103452c999"
DOC_IDS = ("d5d6d0ebe6a14f80bffa4ecb3aed7544", "f2dec4d4804b46d6859e76106903e69a")


def problem(
    status: int, code: str, title: str, detail: dict[str, Any] | None = None
) -> httpx.Response:
    """The API's error shape: RFC 9457 ``application/problem+json``."""
    body: dict[str, Any] = {"type": code, "title": title, "status": status}
    if detail is not None:
        body["detail"] = detail
    return httpx.Response(status, json=body, headers={"content-type": "application/problem+json"})


def finding(**over: Any) -> dict[str, Any]:
    return {
        "code": "meals_over_limit",
        "severity": "warn",
        "message": "Meals on 10 Oct 2026 add up to 3,178, above the 2,000 daily limit.",
        "fields": ["total"],
        "expected": 2000.0,
        "actual": 3178.0,
        "source": "policy",
        "clause_id": "5.1",
        "clause_text": "Meals are reimbursed up to the daily limit for the city tier.",
        "document_id": DOC_IDS[0],
        **over,
    }


def question(**over: Any) -> dict[str, Any]:
    return {
        "id": "q-business_purpose-1bde8f60",
        "kind": "business_purpose",
        "text": "What was the business purpose of the Mumbai trip 9-10 Oct 2026?",
        "document_ids": [],
        "answer": None,
        **over,
    }


def claim(**over: Any) -> dict[str, Any]:
    """A claim waiting for one answer, with one warning."""
    return {
        "id": CLAIM_ID,
        "employee_id": PERSONA,
        "title": "Mumbai trip 9-10 Oct 2026",
        "mode": "trip",
        "status": "needs_info",
        "document_ids": list(DOC_IDS),
        "total": 12678.31,
        "currency": "INR",
        "start_date": "2026-10-09",
        "end_date": "2026-10-10",
        "city": "Mumbai",
        "findings": [finding()],
        "open_questions": [question()],
        "submission_reference": None,
        "batch_id": BATCH_ID,
        "route": "finance_review",
        **over,
    }


def ready_claim(**over: Any) -> dict[str, Any]:
    """The same claim once its question is answered."""
    answered = [question(answer="Client workshop with Kestrel Logistics")]
    return claim(**{"status": "ready", "open_questions": answered, **over})


def submitted_claim(**over: Any) -> dict[str, Any]:
    return ready_claim(**{"status": "submitted", "submission_reference": "FIN-2026-000001", **over})


def document(doc_id: str = DOC_IDS[0], **over: Any) -> dict[str, Any]:
    """A processed receipt as ``GET /v1/documents/{id}`` returns it."""
    return {
        "id": doc_id,
        "filename": "03-cab-mumbai-station-to-hotel.png",
        "position": 2,
        "status": "processed",
        "error": None,
        "document": {
            "id": doc_id,
            "filename": "03-cab-mumbai-station-to-hotel.png",
            "sha256": "0" * 64,
            "receipt": {
                "doc_type": "cab_receipt",
                "merchant_name": "Chai Point Express Cabs",
                "date": "2026-10-09",
                "currency": "INR",
                "total": 293.16,
                "line_items": [{"description": "Ride", "amount": 279.2}],
            },
            "decisions": {
                "category": "local_conveyance",
                "category_confidence": 0.93,
                "alcohol_present": 0.0,
                "personal_expense": 0.02,
                "engine": "llm",
            },
            "findings": [],
            "boxes": {},
        },
        "trust_score": 100,
        "verdict": "ok",
        **over,
    }


def batch(**over: Any) -> dict[str, Any]:
    """A finished batch of two receipts that formed one claim."""
    return {
        "id": BATCH_ID,
        "employee_id": PERSONA,
        "status": "done",
        "total": 2,
        "processed": 2,
        "failed": 0,
        "created_at": "2026-10-08T14:15:05.288104",
        "finished_at": "2026-10-08T14:15:07.375930",
        "error": None,
        "documents": [document(DOC_IDS[0]), document(DOC_IDS[1], filename="taxi.jpg")],
        "claims": [claim()],
        **over,
    }


def batch_created(filenames: list[str]) -> dict[str, Any]:
    return {
        "batch_id": BATCH_ID,
        "status": "queued",
        "documents": [{"id": f"doc-{i}", "filename": name} for i, name in enumerate(filenames)],
        "events_url": f"/v1/batches/{BATCH_ID}/events",
    }


def me(*, approver: bool = False, employee_id: str = PERSONA) -> dict[str, Any]:
    return {
        "employee": {
            "id": employee_id,
            "name": "Asha Menon",
            "employee_id": "EMP90001",
            "grade": "L3",
            "base_city": "Pune",
            "base_state_code": "27",
        },
        "is_approver": approver,
    }


def reply(*, claim_payload: dict[str, Any] | None = None, **over: Any) -> dict[str, Any]:
    """``POST /v1/claims/{id}/reply``: the one question answered, nothing left to ask."""
    updated = claim_payload or ready_claim()
    return {
        "claim": updated,
        "understood": {"q-business_purpose-1bde8f60": "Client workshop with Kestrel Logistics"},
        "follow_up": None,
        **over,
    }
