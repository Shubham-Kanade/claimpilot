"""A small async client for the ClaimPilot REST API: the only place this server uses the network.

The server is a thin adapter: every rule (who may see a claim, the explicit-confirmation gate,
idempotent submission, separation of duties) stays in the API. This module only speaks HTTP. It
knows the API's wire format and nothing about MCP, and it never lets an httpx or pydantic
exception, a request header or the persona escape: every failure leaves as a ``ClaimPilotError``
with a message meant to be read.

``OPERATIONS`` lists each REST call exactly as the OpenAPI document spells it. The client builds
its requests from these entries, and ``tests/unit/test_contract.py`` checks every one against the
committed ``apps/web/openapi.json``, so an API change breaks a test instead of a tool at run time.
"""

from __future__ import annotations

import json
import logging
import ssl
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import quote, urlsplit

import httpx
from pydantic import BaseModel, TypeAdapter, ValidationError

from claimpilot_mcp import __version__
from claimpilot_mcp.api_models import (
    ApiBatch,
    ApiBatchCreated,
    ApiClaim,
    ApiDocument,
    ApiMe,
    ApiReply,
)
from claimpilot_mcp.errors import ApiProblem, ApiProtocolError, ApiUnavailable, ClaimPilotError
from claimpilot_mcp.files import Upload
from claimpilot_mcp.settings import Settings
from claimpilot_mcp.text import plain_text

logger = logging.getLogger(__name__)

PERSONA_HEADER = "X-Persona"
IDEMPOTENCY_HEADER = "Idempotency-Key"
USER_AGENT = f"claimpilot-mcp/{__version__}"
CONNECT_TIMEOUT_S = 5.0
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
PROBLEM_LIMIT = 300  # characters of an API error message passed on to the model


@dataclass(frozen=True, slots=True)
class Operation:
    """One REST call this server makes."""

    method: str
    path: str  # the OpenAPI path template, e.g. /v1/claims/{claim_id}
    query: tuple[str, ...] = ()  # query parameters it may send
    headers: tuple[str, ...] = ()  # header parameters it sends
    body: Literal["json", "multipart"] | None = None
    success: int = 200


READY = Operation("GET", "/readyz")
ME = Operation("GET", "/v1/me", headers=(PERSONA_HEADER,))
LIST_CLAIMS = Operation("GET", "/v1/claims", query=("status",), headers=(PERSONA_HEADER,))
GET_CLAIM = Operation("GET", "/v1/claims/{claim_id}", headers=(PERSONA_HEADER,))
GET_DOCUMENT = Operation("GET", "/v1/documents/{document_id}", headers=(PERSONA_HEADER,))
CREATE_BATCH = Operation(
    "POST", "/v1/batches", headers=(PERSONA_HEADER,), body="multipart", success=202
)
GET_BATCH = Operation("GET", "/v1/batches/{batch_id}", headers=(PERSONA_HEADER,))
REPLY = Operation("POST", "/v1/claims/{claim_id}/reply", headers=(PERSONA_HEADER,), body="json")
SUBMIT = Operation(
    "POST",
    "/v1/claims/{claim_id}/submit",
    headers=(PERSONA_HEADER, IDEMPOTENCY_HEADER),
    body="json",
)
LIST_APPROVALS = Operation("GET", "/v1/approvals", query=("status",), headers=(PERSONA_HEADER,))
DECIDE = Operation("POST", "/v1/claims/{claim_id}/decision", headers=(PERSONA_HEADER,), body="json")
OPERATIONS = (
    READY,
    ME,
    LIST_CLAIMS,
    GET_CLAIM,
    GET_DOCUMENT,
    CREATE_BATCH,
    GET_BATCH,
    REPLY,
    SUBMIT,
    LIST_APPROVALS,
    DECIDE,
)

_CLAIMS = TypeAdapter(list[ApiClaim])


def ssl_context() -> ssl.SSLContext | bool:
    """A verifying SSL context for httpx (``True`` is httpx's default CA bundle).

    Corporate proxies re-sign TLS with their own root CA, which browsers trust through the OS
    store but Python's bundled list does not; on Windows and macOS verification therefore uses the
    OS trust store. Verification is never turned off.
    """
    if sys.platform in ("win32", "darwin"):
        import truststore

        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    return True


def _origin(url: str) -> str:
    """``scheme://host[:port]`` of a URL: what a person needs to recognise it, nothing more."""
    parts = urlsplit(url)
    host = f"[{parts.hostname}]" if ":" in (parts.hostname or "") else parts.hostname
    return f"{parts.scheme}://{host}" + (f":{parts.port}" if parts.port else "")


def _render(detail: Any) -> str:
    return (
        detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False, default=str)
    )


def _validation_message(errors: list[Any]) -> str:
    """FastAPI's own request-validation body: ``{"detail": [{"loc": [...], "msg": "..."}]}``."""
    problems: list[str] = []
    for error in errors[:3]:
        if isinstance(error, dict) and isinstance(error.get("msg"), str):
            where = ".".join(str(part) for part in error.get("loc", ()) if part != "body")
            problems.append(f"{where}: {error['msg']}" if where else error["msg"])
    return "Invalid request: " + "; ".join(problems) if problems else "Invalid request."


class ClaimPilotClient:
    """The ClaimPilot REST API as typed async methods, acting as one persona."""

    def __init__(
        self,
        base_url: str,
        persona: str,
        *,
        timeout_s: float = 30.0,
        upload_timeout_s: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._origin = _origin(self._base)
        self._local = urlsplit(self._base).hostname in LOOPBACK_HOSTS
        self._persona = persona
        self._timeout = httpx.Timeout(timeout_s, connect=min(CONNECT_TIMEOUT_S, timeout_s))
        self._upload_timeout = httpx.Timeout(
            upload_timeout_s, connect=min(CONNECT_TIMEOUT_S, upload_timeout_s)
        )
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    @classmethod
    def from_settings(
        cls, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> ClaimPilotClient:
        return cls(
            settings.claimpilot_api_url,
            settings.claimpilot_persona,
            timeout_s=settings.claimpilot_timeout_s,
            upload_timeout_s=settings.claimpilot_upload_timeout_s,
            transport=transport,
        )

    # -- connection ------------------------------------------------------------------------

    def _http(self) -> httpx.AsyncClient:
        """The shared connection pool, opened on first use (and again after ``aclose``)."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers={"Accept": "application/json", "User-Agent": USER_AGENT},
                timeout=self._timeout,
                transport=self._transport,
                verify=ssl_context(),
                # Proxies named in the environment are for the wider network: a call to this very
                # machine never goes through one.
                trust_env=not self._local,
            )
        return self._client

    async def aclose(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            await client.aclose()

    # -- requests and errors ---------------------------------------------------------------

    async def _send(
        self,
        op: Operation,
        *,
        path: Mapping[str, str] | None = None,
        query: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        body: Any = None,
        files: Sequence[tuple[str, tuple[str, bytes, str]]] | None = None,
        limits: httpx.Timeout | None = None,
    ) -> httpx.Response:
        quoted = {name: quote(value, safe="") for name, value in (path or {}).items()}
        # The persona goes only to the calls that take it (the table says which).
        sent = {PERSONA_HEADER: self._persona} if PERSONA_HEADER in op.headers else {}
        try:
            response = await self._http().request(
                op.method,
                self._base + op.path.format(**quoted),
                params=query,
                headers={**sent, **(headers or {})},
                json=body,
                files=files,
                timeout=limits or self._timeout,
            )
        except httpx.TimeoutException as exc:
            raise ApiUnavailable(
                f"The ClaimPilot API at {self._origin} did not answer in time. "
                "It may be busy: try again in a moment."
            ) from exc
        except httpx.HTTPError as exc:
            raise ApiUnavailable(
                f"Cannot reach the ClaimPilot API at {self._origin}. Check that it is running "
                "and that CLAIMPILOT_API_URL is right."
            ) from exc
        if not response.is_success:
            raise self._problem(response)
        return response

    def _clean(self, text: str) -> str:
        """An API message as a model may read it: the persona (a header value) never appears."""
        return plain_text(text.replace(self._persona, "[persona]"), PROBLEM_LIMIT)

    def _problem(self, response: httpx.Response) -> ApiProblem:
        """An error response as an ``ApiProblem``.

        The API sends RFC 9457 ``application/problem+json`` (``type`` is a stable code, ``title``
        the sentence, ``detail`` an optional object); FastAPI's request validation and plain
        ``HTTPException`` use ``{"detail": ...}`` instead; a gateway may send neither.
        """
        status = response.status_code
        if status == 401:  # the API's own message would repeat the persona back
            return ApiProblem(
                "ClaimPilot does not accept the configured persona. Check CLAIMPILOT_PERSONA: "
                "it must be an employee id the API knows.",
                status=status,
            )
        try:
            body = response.json()
        except ValueError:
            body = None
        if isinstance(body, dict):
            title, detail = body.get("title"), body.get("detail")
            if isinstance(title, str) and title:
                code = body.get("type")
                message = title if not detail else f"{title}: {_render(detail)}"
                return ApiProblem(
                    self._clean(message),
                    status=status,
                    code=code if isinstance(code, str) else None,
                )
            if isinstance(detail, list):
                return ApiProblem(self._clean(_validation_message(detail)), status=status)
            if isinstance(detail, str) and detail:
                message = self._clean(f"ClaimPilot answered {status}: {detail}")
                return ApiProblem(message, status=status)
        if status >= 500:
            message = f"The ClaimPilot API had a problem (HTTP {status}). Try again shortly."
        elif 300 <= status < 400:
            message = (
                f"The ClaimPilot API redirected the request (HTTP {status}). Check that "
                "CLAIMPILOT_API_URL points at the API itself."
            )
        else:
            message = f"The ClaimPilot API rejected the request (HTTP {status})."
        return ApiProblem(message, status=status)

    def _parse[T: BaseModel](self, op: Operation, model: type[T], response: httpx.Response) -> T:
        try:
            return model.model_validate_json(response.content)
        except ValidationError as exc:
            raise self._unexpected(op, exc) from exc

    def _parse_claims(self, op: Operation, response: httpx.Response) -> list[ApiClaim]:
        try:
            return _CLAIMS.validate_json(response.content)
        except ValidationError as exc:
            raise self._unexpected(op, exc) from exc

    @staticmethod
    def _unexpected(op: Operation, error: ValidationError) -> ApiProtocolError:
        # Field paths only: the values are receipt data and stay out of logs and messages.
        fields = sorted({".".join(str(part) for part in e["loc"]) for e in error.errors()})
        logger.warning("%s %s: unexpected response, fields %s", op.method, op.path, fields)
        return ApiProtocolError(
            "The ClaimPilot API answered in a format this connector does not understand; the "
            "two may be out of date with each other."
        )

    # -- the API ---------------------------------------------------------------------------

    async def ready(self) -> bool:
        """Whether the API reports itself ready (its own ``/readyz``)."""
        try:
            await self._send(READY)
        except ClaimPilotError:
            return False
        return True

    async def me(self) -> ApiMe:
        return self._parse(ME, ApiMe, await self._send(ME))

    async def list_claims(self, status: str | None = None) -> list[ApiClaim]:
        query = {"status": status} if status else None
        return self._parse_claims(LIST_CLAIMS, await self._send(LIST_CLAIMS, query=query))

    async def get_claim(self, claim_id: str) -> ApiClaim:
        response = await self._send(GET_CLAIM, path={"claim_id": claim_id})
        return self._parse(GET_CLAIM, ApiClaim, response)

    async def get_document(self, document_id: str) -> ApiDocument:
        response = await self._send(GET_DOCUMENT, path={"document_id": document_id})
        return self._parse(GET_DOCUMENT, ApiDocument, response)

    async def create_batch(self, uploads: Sequence[Upload]) -> ApiBatchCreated:
        files = [("files", (u.name, u.content, u.media_type)) for u in uploads]
        response = await self._send(CREATE_BATCH, files=files, limits=self._upload_timeout)
        return self._parse(CREATE_BATCH, ApiBatchCreated, response)

    async def get_batch(self, batch_id: str) -> ApiBatch:
        response = await self._send(GET_BATCH, path={"batch_id": batch_id})
        return self._parse(GET_BATCH, ApiBatch, response)

    async def reply(self, claim_id: str, text: str) -> ApiReply:
        response = await self._send(REPLY, path={"claim_id": claim_id}, body={"text": text})
        return self._parse(REPLY, ApiReply, response)

    async def submit(self, claim_id: str, idempotency_key: str) -> ApiClaim:
        response = await self._send(
            SUBMIT,
            path={"claim_id": claim_id},
            headers={IDEMPOTENCY_HEADER: idempotency_key},
            body={"confirmed": True},
        )
        return self._parse(SUBMIT, ApiClaim, response)

    async def list_approvals(self, status: str) -> list[ApiClaim]:
        response = await self._send(LIST_APPROVALS, query={"status": status})
        return self._parse_claims(LIST_APPROVALS, response)

    async def decide(self, claim_id: str, approved: bool, comment: str) -> ApiClaim:
        response = await self._send(
            DECIDE,
            path={"claim_id": claim_id},
            body={"approved": approved, "comment": comment},
        )
        return self._parse(DECIDE, ApiClaim, response)
