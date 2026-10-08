"""Typed client for the mock finance system (``services/mcp-finance``).

Models mirror the server's tool results. They ignore unknown fields so the server can grow
without breaking the API. A result that does not fit raises ``McpProtocolError``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from claimpilot.config import Settings
from claimpilot.mcp.client import (
    DEFAULT_TIMEOUT,
    McpProtocolError,
    StreamableHttpClient,
    TypedClient,
)

FinanceStatus = Literal["received", "under_review", "approved", "rejected"]
Decision = Literal["approved", "rejected"]


class _Result(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")


class SubmissionReceipt(_Result):
    """What finance returns when it receives a claim."""

    reference: str
    status: Literal["received"] = "received"
    received_at: datetime
    duplicate: bool = False  # True: this idempotency key was used before; nothing new was created


class StatusEvent(_Result):
    status: FinanceStatus
    at: datetime
    actor: str | None = None
    comment: str = ""


class FinanceClaimSummary(_Result):
    """A claim as finance lists it (no history)."""

    reference: str
    claim_id: str
    employee_id: str
    title: str
    total: float
    currency: str
    document_ids: list[str]
    status: FinanceStatus
    received_at: datetime
    updated_at: datetime


class FinanceClaim(FinanceClaimSummary):
    """A claim with its full status history, oldest first."""

    history: list[StatusEvent]


def _parse[T: BaseModel](model: type[T], data: Any, tool: str) -> T:
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        fields = sorted({".".join(str(part) for part in err["loc"]) for err in exc.errors()})
        raise McpProtocolError(
            f"finance returned an unexpected {tool} result (fields: {', '.join(fields)})",
            server="finance",
            tool=tool,
        ) from exc


class FinanceClient(TypedClient):
    """Submit claims to finance and follow them; approver actions are here too."""

    async def submit_claim(
        self,
        *,
        claim_id: str,
        employee_id: str,
        title: str,
        total: float,
        currency: str,
        document_ids: Sequence[str],
        idempotency_key: str,
    ) -> SubmissionReceipt:
        """Submit a confirmed claim. Retrying with the same key returns the original receipt."""
        result = await self.call_tool(
            "submit_claim",
            {
                "claim_id": claim_id,
                "employee_id": employee_id,
                "title": title,
                "total": total,
                "currency": currency,
                "document_ids": list(document_ids),
                "idempotency_key": idempotency_key,
            },
        )
        return _parse(SubmissionReceipt, result, "submit_claim")

    async def get_claim_status(self, reference: str) -> FinanceClaim:
        result = await self.call_tool("get_claim_status", {"reference": reference})
        return _parse(FinanceClaim, result, "get_claim_status")

    async def list_claims(
        self, *, status: FinanceStatus | None = None, employee_id: str | None = None
    ) -> list[FinanceClaimSummary]:
        arguments = {"status": status, "employee_id": employee_id}
        result = await self.call_tool(
            "list_claims", {k: v for k, v in arguments.items() if v is not None}
        )
        items = result.get("result")
        if not isinstance(items, list):
            raise McpProtocolError(
                "finance returned an unexpected list_claims result",
                server="finance",
                tool="list_claims",
            )
        return [_parse(FinanceClaimSummary, item, "list_claims") for item in items]

    async def start_review(self, reference: str, reviewer_id: str) -> FinanceClaim:
        result = await self.call_tool(
            "start_review", {"reference": reference, "reviewer_id": reviewer_id}
        )
        return _parse(FinanceClaim, result, "start_review")

    async def decide_claim(
        self, reference: str, decision: Decision, approver_id: str, comment: str = ""
    ) -> FinanceClaim:
        result = await self.call_tool(
            "decide_claim",
            {
                "reference": reference,
                "decision": decision,
                "approver_id": approver_id,
                "comment": comment,
            },
        )
        return _parse(FinanceClaim, result, "decide_claim")


def get_finance_client(settings: Settings, *, timeout: float = DEFAULT_TIMEOUT) -> FinanceClient:
    """A client for the finance MCP server at ``settings.mcp_finance_url``."""
    return FinanceClient(
        StreamableHttpClient(settings.mcp_finance_url, name="finance", timeout=timeout)
    )
