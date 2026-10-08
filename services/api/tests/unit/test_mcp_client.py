"""MCP client layer: the protocol, FakeMcpClient and the error mapping of StreamableHttpClient."""

from __future__ import annotations

import ssl
from typing import Any

import httpx2
import pytest
from mcp import MCPError
from mcp.types import (
    CONNECTION_CLOSED,
    INTERNAL_ERROR,
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    REQUEST_TIMEOUT,
    BlobResourceContents,
    CallToolResult,
    ReadResourceResult,
    TextContent,
)

from claimpilot.mcp import client as client_module
from claimpilot.mcp.client import (
    FakeMcpClient,
    McpError,
    McpProtocolError,
    McpToolClient,
    McpToolError,
    McpToolSpec,
    McpUnavailableError,
    StreamableHttpClient,
    TypedClient,
)

# -- errors -------------------------------------------------------------------------------------


def test_only_unavailability_is_retryable():
    assert McpUnavailableError("down").retryable is True
    assert McpToolError("no").retryable is False
    assert McpProtocolError("odd").retryable is False
    assert McpError("base").retryable is False


def test_errors_carry_server_and_tool():
    error = McpToolError("rejected", server="finance", tool="submit_claim")
    assert (str(error), error.server, error.tool) == ("rejected", "finance", "submit_claim")
    assert all(
        issubclass(e, McpError) for e in (McpUnavailableError, McpToolError, McpProtocolError)
    )


# -- FakeMcpClient ------------------------------------------------------------------------------


async def test_fake_returns_canned_results_and_records_calls():
    fake = FakeMcpClient()
    fake.add_tool("echo", {"ok": True})
    assert await fake.call_tool("echo", {"a": 1}) == {"ok": True}
    assert await fake.call_tool("echo") == {"ok": True}
    assert fake.calls == [("echo", {"a": 1}), ("echo", {})]


async def test_fake_handlers_may_be_sync_or_async():
    fake = FakeMcpClient()
    fake.add_tool("double", lambda args: {"value": args["n"] * 2})

    async def triple(args: dict[str, Any]) -> dict[str, Any]:
        return {"value": args["n"] * 3}

    fake.add_tool("triple", triple)
    assert await fake.call_tool("double", {"n": 4}) == {"value": 8}
    assert await fake.call_tool("triple", {"n": 4}) == {"value": 12}


async def test_fake_isolates_callers_from_its_own_state():
    fake = FakeMcpClient()
    canned: dict[str, Any] = {"items": [1]}
    fake.add_tool("get", canned)
    arguments = {"nested": {"x": 1}}
    first = await fake.call_tool("get", arguments)
    first["items"].append(2)
    arguments["nested"]["x"] = 99
    assert await fake.call_tool("get") == {"items": [1]}
    assert fake.calls[0] == ("get", {"nested": {"x": 1}})


async def test_fake_can_raise_a_registered_error():
    fake = FakeMcpClient()
    fake.add_tool("submit", McpToolError("key reused", tool="submit"))
    with pytest.raises(McpToolError, match="key reused"):
        await fake.call_tool("submit", {"k": 1})
    assert fake.calls == [("submit", {"k": 1})]  # the attempt is still recorded


async def test_fake_unknown_tool_fails_like_a_server():
    fake = FakeMcpClient(name="finance")
    with pytest.raises(McpToolError, match="Unknown tool: nope") as caught:
        await fake.call_tool("nope")
    assert caught.value.server == "finance" and caught.value.tool == "nope"


async def test_fake_resources():
    fake = FakeMcpClient()
    fake.add_resource("policy://x", "text")
    assert await fake.read_resource("policy://x") == "text"
    with pytest.raises(McpProtocolError, match="Unknown resource: policy://y"):
        await fake.read_resource("policy://y")


async def test_fake_outage_affects_every_operation():
    fake = FakeMcpClient()
    fake.add_tool("t", {})
    fake.add_resource("r://x", "x")
    fake.failure = McpUnavailableError("finance is down")
    for operation in (
        fake.list_tools(),
        fake.call_tool("t"),
        fake.read_resource("r://x"),
    ):
        with pytest.raises(McpUnavailableError, match="finance is down"):
            await operation
    assert fake.calls == []  # an outage is not a call
    fake.failure = None
    assert await fake.call_tool("t") == {}


async def test_fake_lists_its_tools_with_defaults():
    fake = FakeMcpClient()
    spec = fake.add_tool("a", {}, read_only=True)
    fake.add_tool(
        "b",
        {},
        description="Does b.",
        input_schema={"type": "object", "properties": {"x": {"type": "string"}}},
    )
    tools = await fake.list_tools()
    assert [t.name for t in tools] == ["a", "b"]
    assert spec.description == "Fake tool a." and spec.read_only is True
    assert tools[1].description == "Does b."
    assert tools[1].input_schema["properties"] == {"x": {"type": "string"}}
    assert tools[0].input_schema == {"type": "object", "properties": {}}


def test_the_protocol_is_satisfied_structurally():
    fake = FakeMcpClient()
    assert isinstance(fake, McpToolClient)
    assert isinstance(StreamableHttpClient("http://localhost:1/mcp"), McpToolClient)
    assert isinstance(TypedClient(fake), McpToolClient)
    assert not isinstance(object(), McpToolClient)


async def test_typed_client_base_delegates_everything():
    fake = FakeMcpClient()
    fake.add_tool("t", {"v": 1})
    fake.add_resource("r://x", "text")
    typed = TypedClient(fake)
    assert [t.name for t in await typed.list_tools()] == ["t"]
    assert await typed.call_tool("t", {"a": 1}) == {"v": 1}
    assert await typed.read_resource("r://x") == "text"
    assert fake.calls == [("t", {"a": 1})]


def test_tool_specs_are_immutable_values():
    spec = McpToolSpec(name="t")
    assert spec.input_schema == {"type": "object"}
    assert spec.description == "" and spec.output_schema is None and spec.read_only is None
    with pytest.raises(ValueError, match="frozen"):
        spec.name = "other"  # type: ignore[misc]


# -- StreamableHttpClient: TLS ------------------------------------------------------------------


def test_plain_http_never_loads_the_trust_store(monkeypatch: pytest.MonkeyPatch):
    def boom() -> bool:
        raise AssertionError("the trust store must not be loaded for http")

    monkeypatch.setattr(client_module, "ssl_context", boom)
    assert StreamableHttpClient("http://localhost:8101/mcp")._tls() is True


def test_https_verifies_against_the_os_trust_store(monkeypatch: pytest.MonkeyPatch):
    context = ssl.create_default_context()
    monkeypatch.setattr(client_module, "ssl_context", lambda: context)
    assert StreamableHttpClient("https://mcp.example.com/mcp")._tls() is context
    assert StreamableHttpClient("HTTPS://mcp.example.com/mcp")._tls() is context


def test_an_explicit_verify_setting_wins(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(client_module, "ssl_context", lambda: pytest.fail("not consulted"))
    context = ssl.create_default_context()
    assert StreamableHttpClient("https://x/mcp", verify=context)._tls() is context
    assert StreamableHttpClient("https://x/mcp", verify=False)._tls() is False


def test_the_client_is_named_after_its_server_or_its_url():
    assert StreamableHttpClient("http://h:1/mcp", name="finance").name == "finance"
    assert StreamableHttpClient("http://h:1/mcp").name == "http://h:1/mcp"


# -- StreamableHttpClient: error mapping --------------------------------------------------------

http = StreamableHttpClient("http://finance:8101/mcp", name="finance")


def nested(*errors: Exception) -> ExceptionGroup:
    """The shape the SDK's anyio task groups produce."""
    return ExceptionGroup("unhandled errors in a TaskGroup", [ExceptionGroup("inner", errors)])


@pytest.mark.parametrize(
    "failure",
    [
        httpx2.ConnectError("refused"),
        httpx2.ConnectTimeout("slow"),
        httpx2.ReadError("reset"),
        httpx2.RemoteProtocolError("garbage"),
        ConnectionResetError("reset"),
        OSError("network is unreachable"),
        MCPError(CONNECTION_CLOSED, "Connection closed"),
        MCPError(REQUEST_TIMEOUT, "Request 'tools/call' timed out"),
        MCPError(INTERNAL_ERROR, "Server returned an error response"),  # a gateway 502, say
    ],
)
def test_connection_problems_are_unavailability(failure: Exception):
    for raised in (failure, nested(failure)):
        error = http._translate(raised, "call submit_claim", "submit_claim")
        assert isinstance(error, McpUnavailableError)
        assert error.retryable and error.server == "finance" and error.tool == "submit_claim"
        assert error.args[0].startswith("finance: ")


@pytest.mark.parametrize("timeout", [TimeoutError(), httpx2.ReadTimeout("read")])
def test_timeouts_say_so(timeout: Exception):
    error = http._translate(nested(timeout), "call submit_claim", "submit_claim")
    assert isinstance(error, McpUnavailableError)
    assert str(error) == "finance: call submit_claim timed out"


def test_connection_errors_name_the_exception_but_not_the_url():
    error = http._translate(httpx2.ConnectError("[Errno 111] refused"), "list tools", None)
    assert str(error) == "finance: cannot reach the MCP server (ConnectError)"
    assert "8101" not in str(error)


def test_protocol_level_errors_keep_the_servers_message():
    error = http._translate(nested(MCPError(INVALID_PARAMS, "Unknown resource: x")), "read x", None)
    assert isinstance(error, McpProtocolError) and not error.retryable
    assert str(error) == "finance: Unknown resource: x"


def test_a_url_that_is_not_an_mcp_endpoint_is_a_protocol_error():
    error = http._translate(nested(MCPError(METHOD_NOT_FOUND, "Not Found")), "list tools", None)
    assert isinstance(error, McpProtocolError) and not error.retryable
    assert str(error) == "finance: Not Found"


def test_a_server_side_error_that_is_not_a_failure_response_is_not_retryable():
    error = http._translate(MCPError(INTERNAL_ERROR, "policy not available"), "read x", None)
    assert isinstance(error, McpProtocolError)


def test_anything_else_is_a_protocol_error_naming_the_exception():
    error = http._translate(RuntimeError("Invalid structured content"), "call t", "t")
    assert isinstance(error, McpProtocolError)
    assert str(error) == "finance: unexpected RuntimeError during call t"
    assert "Invalid structured content" not in str(error)  # server-controlled text stays out


def test_the_first_recognised_error_in_a_group_decides():
    error = http._translate(
        nested(RuntimeError("noise"), httpx2.ConnectError("refused")), "x", None
    )
    assert isinstance(error, McpUnavailableError)


# -- StreamableHttpClient: result unpacking -----------------------------------------------------


def text(value: str) -> TextContent:
    return TextContent(type="text", text=value)


def result(*blocks: str, structured: Any = None, is_error: bool = False) -> CallToolResult:
    return CallToolResult(
        content=[text(b) for b in blocks], structured_content=structured, is_error=is_error
    )


def test_structured_objects_are_returned_as_a_copy():
    payload = {"reference": "FIN-1", "duplicate": False}
    unpacked = http._unpack(result("{}", structured=payload), "submit_claim")
    assert unpacked == payload and unpacked is not payload


@pytest.mark.parametrize("value", [[1, 2], "plain", 7, True])
def test_other_structured_json_values_are_wrapped(value: Any):
    assert http._unpack(result("x", structured=value), "t") == {"result": value}


def test_unstructured_json_text_is_parsed():
    assert http._unpack(result('{"a": 1}'), "t") == {"a": 1}
    assert http._unpack(result("[1, 2]"), "t") == {"result": [1, 2]}


def test_unstructured_plain_text_is_wrapped():
    assert http._unpack(result("hello", "world"), "t") == {"text": "hello\nworld"}


def test_tool_failures_become_tool_errors_with_the_servers_reason():
    failed = result(
        "Error executing tool submit_claim: total must be greater than zero", is_error=True
    )
    with pytest.raises(McpToolError) as caught:
        http._unpack(failed, "submit_claim")
    assert str(caught.value) == "total must be greater than zero"
    assert (caught.value.server, caught.value.tool) == ("finance", "submit_claim")


def test_a_tool_failure_without_detail_keeps_what_the_server_said():
    with pytest.raises(McpToolError, match=r"^Error executing tool submit_claim$"):
        http._unpack(result("Error executing tool submit_claim", is_error=True), "submit_claim")
    with pytest.raises(McpToolError, match=r"^the tool failed$"):
        http._unpack(result(is_error=True), "submit_claim")
    with pytest.raises(McpToolError, match=r"^Unknown tool: nope$"):
        http._unpack(result("Unknown tool: nope", is_error=True), "nope")


async def test_reading_a_resource_joins_its_text(monkeypatch: pytest.MonkeyPatch):
    from mcp.types import TextResourceContents

    async def fake_run(what: str, operation: Any, *, tool: str | None = None) -> ReadResourceResult:
        return ReadResourceResult(
            contents=[
                TextResourceContents(uri="policy://x", text="one"),
                TextResourceContents(uri="policy://x", text="two"),
            ]
        )

    monkeypatch.setattr(http, "_run", fake_run)
    assert await http.read_resource("policy://x") == "one\ntwo"


async def test_a_resource_without_text_is_a_protocol_error(monkeypatch: pytest.MonkeyPatch):
    async def fake_run(what: str, operation: Any, *, tool: str | None = None) -> ReadResourceResult:
        return ReadResourceResult(
            contents=[BlobResourceContents(uri="policy://x", blob="AAAA", mime_type="image/png")]
        )

    monkeypatch.setattr(http, "_run", fake_run)
    with pytest.raises(McpProtocolError, match="has no text"):
        await http.read_resource("policy://x")


class Pager:
    """A stand-in SDK client whose tools/list is paginated."""

    def __init__(self, pages: int) -> None:
        self.pages = pages
        self.cursors: list[str | None] = []

    async def list_tools(self, *, cursor: str | None = None) -> Any:
        from mcp.types import ListToolsResult, Tool

        self.cursors.append(cursor)
        index = int(cursor or 0)
        more = str(index + 1) if index + 1 < self.pages else None
        tool = Tool(name=f"tool_{index}", input_schema={"type": "object"})
        return ListToolsResult(tools=[tool], next_cursor=more)


async def test_list_tools_follows_pagination(monkeypatch: pytest.MonkeyPatch):
    pager = Pager(pages=3)

    async def run(what: str, operation: Any, *, tool: str | None = None) -> Any:
        return await operation(pager)

    monkeypatch.setattr(http, "_run", run)
    specs = await http.list_tools()
    assert [s.name for s in specs] == ["tool_0", "tool_1", "tool_2"]
    assert pager.cursors == [None, "1", "2"]


async def test_list_tools_stops_after_a_sane_number_of_pages(monkeypatch: pytest.MonkeyPatch):
    pager = Pager(pages=10_000)  # a server that never stops paginating

    async def run(what: str, operation: Any, *, tool: str | None = None) -> Any:
        return await operation(pager)

    monkeypatch.setattr(http, "_run", run)
    specs = await http.list_tools()
    assert len(specs) == client_module.MAX_TOOL_PAGES


class StopSession(Exception):
    """Raised by the spy below to end a session before any network I/O."""


@pytest.mark.parametrize(
    ("url", "expects_context"),
    [("https://mcp.example.com/mcp", True), ("http://mcp:8101/mcp", False)],
)
async def test_sessions_are_opened_with_the_configured_timeouts_and_tls(
    monkeypatch: pytest.MonkeyPatch, url: str, expects_context: bool
):
    context = ssl.create_default_context()
    monkeypatch.setattr(client_module, "ssl_context", lambda: context)
    captured: dict[str, Any] = {}

    def spy(**kwargs: Any) -> Any:
        captured.update(kwargs)
        raise StopSession

    monkeypatch.setattr(client_module.httpx2, "AsyncClient", spy)
    target = StreamableHttpClient(url, name="finance", timeout=7.0, connect_timeout=1.5)
    with pytest.raises(McpProtocolError, match="unexpected StopSession"):
        await target.list_tools()
    assert captured["verify"] is (context if expects_context else True)
    timeout = captured["timeout"]
    assert (timeout.connect, timeout.read, timeout.write) == (1.5, 7.0, 7.0)
