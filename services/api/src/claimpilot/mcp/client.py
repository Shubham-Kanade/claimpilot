"""MCP tool client: a small protocol, the streamable-HTTP implementation and a fake for tests.

Callers depend on ``McpToolClient`` and get a typed one from ``get_finance_client(settings)`` /
``get_corp_client(settings)``. Every failure is mapped to a ``McpError`` subclass; SDK and httpx
exceptions never leak past this module:

* ``McpUnavailableError`` (retryable): the server cannot be reached, timed out or dropped us.
* ``McpToolError``: the tool ran and reported a failure. The message is meant for the model
  (``"idempotency_key 'k' was already used for a different claim payload..."``).
* ``McpProtocolError``: anything else that is not right (unknown resource, unexpected payload).

``StreamableHttpClient`` opens one short-lived session per operation: MCP sessions are bound to
the asyncio task that opened them, and API request handlers and chat turns run in different tasks,
so there is no long-lived session to share. Arguments and results are never logged (PII).
"""

from __future__ import annotations

import copy
import inspect
import json
import ssl
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Mapping
from contextlib import asynccontextmanager
from typing import Any, Protocol, runtime_checkable

import anyio
import httpx2
import structlog
from mcp import Client, MCPError
from mcp.client.streamable_http import streamable_http_client
from mcp.types import (
    CONNECTION_CLOSED,
    REQUEST_TIMEOUT,
    CallToolResult,
    Implementation,
    ReadResourceResult,
    TextContent,
    TextResourceContents,
)
from pydantic import BaseModel, ConfigDict, Field

from claimpilot import __version__
from claimpilot.net import ssl_context

log = structlog.get_logger(__name__)

DEFAULT_TIMEOUT = 15.0  # seconds for one whole operation (connect + every round trip)
DEFAULT_CONNECT_TIMEOUT = 3.0
MAX_TOOL_PAGES = 20
# What the SDK reports when the server answers a request with a non-2xx status that is not a
# JSON-RPC error (a gateway 502, a crash, a wrong method): treated as the server being unavailable.
HTTP_FAILURE_MESSAGE = "Server returned an error response"


# -- errors --------------------------------------------------------------------------------------


class McpError(Exception):
    """Base for every error raised by an ``McpToolClient``."""

    retryable: bool = False

    def __init__(self, message: str, *, server: str | None = None, tool: str | None = None) -> None:
        super().__init__(message)
        self.server = server
        self.tool = tool


class McpUnavailableError(McpError):
    """The server cannot be reached, timed out or closed the connection: worth retrying later."""

    retryable = True


class McpToolError(McpError):
    """The tool ran and reported a failure; the message is written for the model to read."""


class McpProtocolError(McpError):
    """The server answered, but not with what the contract promises."""


# -- the protocol --------------------------------------------------------------------------------


class McpToolSpec(BaseModel):
    """A tool as a server advertises it (the parts ClaimPilot uses)."""

    model_config = ConfigDict(frozen=True)

    name: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})
    output_schema: dict[str, Any] | None = None
    title: str | None = None
    read_only: bool | None = Field(default=None, description="The server's read-only hint.")


@runtime_checkable
class McpToolClient(Protocol):
    """What the rest of ClaimPilot needs from an MCP server."""

    async def list_tools(self) -> list[McpToolSpec]: ...

    async def call_tool(
        self, name: str, arguments: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        """Run a tool and return its structured result (non-object results come as ``result``)."""
        ...

    async def read_resource(self, uri: str) -> str:
        """The text of a resource."""
        ...


class TypedClient:
    """Base of the typed clients: satisfies ``McpToolClient`` by delegating to an inner client."""

    def __init__(self, inner: McpToolClient) -> None:
        self._inner = inner

    async def list_tools(self) -> list[McpToolSpec]:
        return await self._inner.list_tools()

    async def call_tool(
        self, name: str, arguments: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        return await self._inner.call_tool(name, arguments)

    async def read_resource(self, uri: str) -> str:
        return await self._inner.read_resource(uri)


# -- streamable HTTP -----------------------------------------------------------------------------


def _leaves(exc: BaseException) -> Iterator[BaseException]:
    """The real errors inside (possibly nested) exception groups: the SDK runs task groups."""
    if isinstance(exc, BaseExceptionGroup):
        for inner in exc.exceptions:
            yield from _leaves(inner)
    else:
        yield exc


def _message_text(result: CallToolResult) -> str:
    return "\n".join(b.text for b in result.content if isinstance(b, TextContent)).strip()


class StreamableHttpClient:
    """The official MCP client over streamable HTTP, one short-lived session per operation."""

    def __init__(
        self,
        url: str,
        *,
        name: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        verify: ssl.SSLContext | bool | None = None,
    ) -> None:
        self.url = url
        self.name = name or url
        self._timeout = timeout
        self._connect_timeout = connect_timeout
        self._verify = verify

    def _tls(self) -> ssl.SSLContext | bool:
        """TLS verification: the OS trust store behind a corporate proxy (``claimpilot.net``).

        Plain-HTTP URLs (localhost, the Docker network) need none, so the trust store is only
        loaded for https.
        """
        if self._verify is not None:
            return self._verify
        return ssl_context() if self.url.lower().startswith("https://") else True

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[Client]:
        timeout = httpx2.Timeout(self._timeout, connect=self._connect_timeout)
        async with httpx2.AsyncClient(timeout=timeout, verify=self._tls()) as http:
            client = Client(
                streamable_http_client(self.url, http_client=http),
                read_timeout_seconds=self._timeout,
                cache=None,
                client_info=Implementation(name="claimpilot-api", version=__version__),
            )
            async with client:
                yield client

    async def _run[T](
        self, what: str, operation: Callable[[Client], Awaitable[T]], *, tool: str | None = None
    ) -> T:
        try:
            with anyio.fail_after(self._timeout):
                async with self._session() as client:
                    return await operation(client)
        except Exception as exc:
            error = self._translate(exc, what, tool)
            log.warning(
                "mcp.call_failed",
                server=self.name,
                operation=what,
                tool=tool,
                error=type(error).__name__,
                cause=type(exc).__name__,
            )
            raise error from exc

    def _translate(self, exc: BaseException, what: str, tool: str | None) -> McpError:
        server = self.name
        leaves = list(_leaves(exc))
        for leaf in leaves:
            if isinstance(leaf, TimeoutError | httpx2.TimeoutException):
                return McpUnavailableError(f"{server}: {what} timed out", server=server, tool=tool)
            if isinstance(leaf, httpx2.HTTPError | OSError):
                return McpUnavailableError(
                    f"{server}: cannot reach the MCP server ({type(leaf).__name__})",
                    server=server,
                    tool=tool,
                )
            if isinstance(leaf, MCPError):
                if leaf.code in (REQUEST_TIMEOUT, CONNECTION_CLOSED) or (
                    leaf.message == HTTP_FAILURE_MESSAGE
                ):
                    return McpUnavailableError(
                        f"{server}: {what} failed: {leaf.message}", server=server, tool=tool
                    )
                return McpProtocolError(f"{server}: {leaf.message}", server=server, tool=tool)
        first = leaves[0]
        return McpProtocolError(
            f"{server}: unexpected {type(first).__name__} during {what}", server=server, tool=tool
        )

    async def list_tools(self) -> list[McpToolSpec]:
        async def fetch(client: Client) -> list[McpToolSpec]:
            specs: list[McpToolSpec] = []
            cursor: str | None = None
            for _ in range(MAX_TOOL_PAGES):
                page = await client.list_tools(cursor=cursor)
                specs.extend(
                    McpToolSpec(
                        name=tool.name,
                        description=tool.description or "",
                        input_schema=tool.input_schema,
                        output_schema=tool.output_schema,
                        title=tool.title,
                        read_only=tool.annotations.read_only_hint if tool.annotations else None,
                    )
                    for tool in page.tools
                )
                cursor = page.next_cursor
                if cursor is None:
                    break
            return specs

        return await self._run("list tools", fetch)

    async def call_tool(
        self, name: str, arguments: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        result = await self._run(
            f"call {name}", lambda c: c.call_tool(name, dict(arguments or {})), tool=name
        )
        return self._unpack(result, name)

    def _unpack(self, result: CallToolResult, tool: str) -> dict[str, Any]:
        text = _message_text(result)
        if result.is_error:
            detail = text.removeprefix(f"Error executing tool {tool}: ") or "the tool failed"
            raise McpToolError(detail, server=self.name, tool=tool)
        structured = result.structured_content
        if isinstance(structured, dict):
            return dict(structured)
        if structured is not None:  # a list or scalar: modern servers may return any JSON value
            return {"result": structured}
        try:
            parsed = json.loads(text)
        except ValueError:
            return {"text": text}
        return parsed if isinstance(parsed, dict) else {"result": parsed}

    async def read_resource(self, uri: str) -> str:
        result: ReadResourceResult = await self._run(f"read {uri}", lambda c: c.read_resource(uri))
        texts = [c.text for c in result.contents if isinstance(c, TextResourceContents)]
        if not texts:
            raise McpProtocolError(f"{self.name}: resource {uri} has no text", server=self.name)
        return "\n".join(texts)


# -- a fake for tests ----------------------------------------------------------------------------

Handler = Callable[[dict[str, Any]], dict[str, Any] | Awaitable[dict[str, Any]]]
FakeResult = dict[str, Any] | Handler | McpError


class FakeMcpClient:
    """In-memory ``McpToolClient`` for tests; records every call.

    Register a tool with a canned result, a handler receiving the arguments (sync or async), or an
    ``McpError`` to raise. Unknown tools fail like a real server does (``McpToolError``).
    """

    def __init__(self, *, name: str = "fake") -> None:
        self.name = name
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.failure: McpError | None = None  # when set, every operation raises it (an outage)
        self._tools: dict[str, tuple[McpToolSpec, FakeResult]] = {}
        self._resources: dict[str, str] = {}

    def add_tool(
        self,
        name: str,
        result: FakeResult,
        *,
        description: str = "",
        input_schema: dict[str, Any] | None = None,
        read_only: bool | None = None,
    ) -> McpToolSpec:
        spec = McpToolSpec(
            name=name,
            description=description or f"Fake tool {name}.",
            input_schema=input_schema or {"type": "object", "properties": {}},
            read_only=read_only,
        )
        self._tools[name] = (spec, result)
        return spec

    def add_resource(self, uri: str, text: str) -> None:
        self._resources[uri] = text

    async def list_tools(self) -> list[McpToolSpec]:
        self._check_available()
        return [spec for spec, _ in self._tools.values()]

    async def call_tool(
        self, name: str, arguments: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        self._check_available()
        sent = copy.deepcopy(dict(arguments or {}))
        self.calls.append((name, sent))
        if name not in self._tools:
            raise McpToolError(f"Unknown tool: {name}", server=self.name, tool=name)
        result = self._tools[name][1]
        if isinstance(result, McpError):
            raise result
        if callable(result):
            produced = result(copy.deepcopy(sent))
            if inspect.isawaitable(produced):
                produced = await produced
            return copy.deepcopy(produced)
        return copy.deepcopy(result)

    async def read_resource(self, uri: str) -> str:
        self._check_available()
        if uri not in self._resources:
            raise McpProtocolError(f"Unknown resource: {uri}", server=self.name)
        return self._resources[uri]

    def _check_available(self) -> None:
        if self.failure is not None:
            raise self.failure
