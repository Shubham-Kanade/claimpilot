"""The MCP server of the mock finance system: tools, health routes and the ASGI app.

Transport: streamable HTTP at ``/mcp`` (stateless, plain JSON responses, so a restart never
strands a client on a dead session). ``/healthz`` and ``/readyz`` sit beside it.

Tool failures a caller can act on (unknown reference, invalid transition, key reuse...) are raised
as ``ToolError``: the client receives ``is_error=True`` with the message, which a model can read.
Anything else is a crash and the SDK reports only a generic message.
"""

from __future__ import annotations

import inspect
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated

import anyio.to_thread
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse

from claimpilot_mcp_finance import __version__
from claimpilot_mcp_finance.models import (
    ClaimRecord,
    ClaimStatus,
    ClaimSummary,
    Decision,
    SubmissionReceipt,
)
from claimpilot_mcp_finance.store import FinanceError, FinanceStore

SERVICE_NAME = "claimpilot-mcp-finance"
MCP_PATH = "/mcp"

INSTRUCTIONS = (
    "Mock finance / reimbursement system. Employees submit finished claims with submit_claim "
    "(idempotent: retries with the same idempotency_key return the original reference) and follow "
    "them with get_claim_status. list_claims, start_review and decide_claim are approver actions."
)

REFERENCE = Field(description="Finance reference of the claim, e.g. 'FIN-2026-000123'.")


@contextmanager
def _as_tool_error() -> Iterator[None]:
    """Report domain failures as tool errors the model can read (the SDK hides anything else)."""
    try:
        yield
    except FinanceError as exc:
        raise ToolError(str(exc)) from exc


class FinanceTools:
    """The tool implementations: thin adapters from tool arguments to the ``FinanceStore``.

    Docstrings and ``Field`` descriptions are the tool schema the model sees.
    """

    def __init__(self, store: FinanceStore) -> None:
        self._store = store

    def submit_claim(
        self,
        claim_id: Annotated[str, Field(description="ClaimPilot claim id being submitted.")],
        employee_id: Annotated[
            str,
            Field(description="Directory id of the employee the claim belongs to, e.g. 'P001'."),
        ],
        title: Annotated[str, Field(description="Short claim title, e.g. 'Pune trip 12-14 Aug'.")],
        total: Annotated[
            float, Field(description="Claim total in the given currency; must be greater than 0.")
        ],
        currency: Annotated[str, Field(description="ISO 4217 currency code, e.g. 'INR'.")],
        document_ids: Annotated[
            list[str],
            Field(description="Ids of the receipt documents in the claim (at least one)."),
        ],
        idempotency_key: Annotated[
            str,
            Field(
                description="Unique key for this submission attempt. Reuse the same key when "
                "retrying so the claim is never created twice."
            ),
        ],
    ) -> SubmissionReceipt:
        """Submit a finished expense claim to finance for reimbursement.

        Only call this after the employee has confirmed the claim. The call is idempotent:
        repeating it with the same idempotency_key and the same claim returns the original
        reference with duplicate=true. Reusing a key for a different claim is an error.
        """
        with _as_tool_error():
            return self._store.submit(
                claim_id=claim_id,
                employee_id=employee_id,
                title=title,
                total=total,
                currency=currency,
                document_ids=document_ids,
                idempotency_key=idempotency_key,
            )

    def get_claim_status(self, reference: Annotated[str, REFERENCE]) -> ClaimRecord:
        """Get a submitted claim's current status and its full status history.

        Statuses move received -> under_review -> approved or rejected.
        """
        with _as_tool_error():
            return self._store.get(reference)

    def list_claims(
        self,
        status: Annotated[
            ClaimStatus | None, Field(description="Only claims in this status; omit for all.")
        ] = None,
        employee_id: Annotated[
            str | None, Field(description="Only this employee's claims; omit for everyone.")
        ] = None,
    ) -> list[ClaimSummary]:
        """List submitted claims in submission order (the approver's queue)."""
        with _as_tool_error():
            return self._store.find(status=status, employee_id=employee_id)

    def start_review(
        self,
        reference: Annotated[str, REFERENCE],
        reviewer_id: Annotated[
            str, Field(description="Directory id of the approver taking the claim.")
        ],
    ) -> ClaimRecord:
        """Approver action: take a 'received' claim into review (received -> under_review)."""
        with _as_tool_error():
            return self._store.start_review(reference, reviewer_id)

    def decide_claim(
        self,
        reference: Annotated[str, REFERENCE],
        decision: Annotated[Decision, Field(description="'approved' or 'rejected'.")],
        approver_id: Annotated[
            str, Field(description="Directory id of the approver making the decision.")
        ],
        comment: Annotated[
            str, Field(description="Note for the employee. Required when rejecting.")
        ] = "",
    ) -> ClaimRecord:
        """Approver action: approve or reject a claim that is received or under review.

        Decisions are final; deciding an already approved or rejected claim is an error.
        """
        with _as_tool_error():
            return self._store.decide(reference, decision, approver_id, comment)


def _hints(title: str, *, read_only: bool, idempotent: bool) -> ToolAnnotations:
    return ToolAnnotations(
        title=title,
        read_only_hint=read_only,
        destructive_hint=False,
        idempotent_hint=idempotent,
        open_world_hint=False,
    )


def create_server(store: FinanceStore) -> MCPServer:
    """Build the MCP server (tools + health routes) over ``store``."""
    server = MCPServer(
        SERVICE_NAME,
        title="ClaimPilot mock finance system",
        instructions=INSTRUCTIONS,
        version=__version__,
    )
    tools = FinanceTools(store)
    registrations = (
        (tools.submit_claim, _hints("Submit claim", read_only=False, idempotent=True)),
        (tools.get_claim_status, _hints("Get claim status", read_only=True, idempotent=True)),
        (tools.list_claims, _hints("List claims", read_only=True, idempotent=True)),
        (tools.start_review, _hints("Start review", read_only=False, idempotent=False)),
        (tools.decide_claim, _hints("Decide claim", read_only=False, idempotent=False)),
    )
    for fn, hints in registrations:
        server.add_tool(
            fn,
            title=hints.title,
            description=inspect.cleandoc(fn.__doc__ or ""),
            annotations=hints,
        )

    @server.custom_route("/healthz", methods=["GET"])
    async def healthz(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "service": SERVICE_NAME, "version": __version__})

    @server.custom_route("/readyz", methods=["GET"])
    async def readyz(_: Request) -> JSONResponse:
        try:
            await anyio.to_thread.run_sync(store.ping)
        except Exception:
            return JSONResponse({"status": "unavailable", "checks": {"database": "error"}}, 503)
        return JSONResponse({"status": "ok", "checks": {"database": "ok"}})

    return server


def create_app(store: FinanceStore, *, host: str = "127.0.0.1") -> Starlette:
    """The ASGI app. ``host`` is the bind address: loopback binds get DNS-rebinding protection."""
    return create_server(store).streamable_http_app(
        streamable_http_path=MCP_PATH, json_response=True, stateless_http=True, host=host
    )
