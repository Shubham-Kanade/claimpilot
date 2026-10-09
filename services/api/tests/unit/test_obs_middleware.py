"""``RequestContextMiddleware``: an id on every response, in the log context, and one log line.

The middleware is exercised on a small Starlette app (so each behaviour is isolated) and on the
real ``create_app()`` (so the wiring in ``main.py`` is covered). Streaming is driven through raw
ASGI, because httpx's test transport buffers a whole response before handing it back.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
import structlog.contextvars
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, StreamingResponse
from starlette.routing import Route
from starlette.types import Message, Receive, Scope, Send
from structlog.testing import capture_logs

from claimpilot.main import create_app
from claimpilot.obs.middleware import REQUEST_ID_HEADER, RequestContextMiddleware
from claimpilot.telemetry import current_ids

UUID_HEX = re.compile(r"[0-9a-f]{32}")


async def whoami(_request: Request) -> JSONResponse:
    """What the handler sees: the ledger ids and the request id bound for this request."""
    return JSONResponse({**current_ids(), **structlog.contextvars.get_contextvars()})


async def slow(request: Request) -> JSONResponse:
    await asyncio.sleep(float(request.query_params["delay"]))  # lets concurrent requests overlap
    return await whoami(request)


async def teapot(_request: Request) -> PlainTextResponse:
    return PlainTextResponse("short and stout", status_code=418)


async def boom(_request: Request) -> PlainTextResponse:
    raise RuntimeError("the handler crashed")


async def ok(_request: Request) -> PlainTextResponse:
    return PlainTextResponse("ok")


def app_with(*routes: Route) -> Starlette:
    return Starlette(routes=list(routes), middleware=[Middleware(RequestContextMiddleware)])


def build_app() -> Starlette:
    return app_with(
        Route("/whoami", whoami),
        Route("/slow", slow),
        Route("/teapot", teapot),
        Route("/boom", boom),
        Route("/healthz", ok),
        Route("/healthz/deep", ok),
        Route("/healthzz", ok),
        Route("/readyz", ok),
    )


def http_scope(path: str = "/teapot", headers: list[tuple[bytes, bytes]] | None = None) -> Scope:
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "scheme": "http",
        "headers": headers or [],
    }


@pytest.fixture(autouse=True)
def clean_context() -> Iterator[None]:
    structlog.contextvars.clear_contextvars()
    yield
    structlog.contextvars.clear_contextvars()


@pytest.fixture
async def http() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=build_app(), raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def raw_request(app: Any, headers: list[tuple[bytes, bytes]]) -> list[Message]:
    """One request through raw ASGI, so a server can hand over header bytes httpx would refuse."""
    messages: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        messages.append(message)

    await app(http_scope(headers=headers), receive, send)
    return messages


# --- the request id -----------------------------------------------------------------------------


async def test_a_request_without_an_id_gets_a_generated_one(http: AsyncClient):
    first = await http.get("/teapot")
    second = await http.get("/teapot")

    ids = {first.headers[REQUEST_ID_HEADER], second.headers[REQUEST_ID_HEADER]}
    assert len(ids) == 2 and all(UUID_HEX.fullmatch(i) for i in ids)


async def test_a_valid_incoming_id_is_kept_and_echoed(http: AsyncClient):
    response = await http.get("/teapot", headers={REQUEST_ID_HEADER: "abc-123"})

    assert response.headers[REQUEST_ID_HEADER] == "abc-123"


@pytest.mark.parametrize(
    "sent",
    ["a", "A.b_c-9", "x" * 64, "0" * 32],
    ids=["one-char", "all-allowed-punctuation", "max-length", "uuid-like"],
)
async def test_ids_inside_the_allowed_alphabet_and_length_pass_through(
    http: AsyncClient, sent: str
):
    response = await http.get("/teapot", headers={REQUEST_ID_HEADER: sent})

    assert response.headers[REQUEST_ID_HEADER] == sent


@pytest.mark.parametrize(
    "sent",
    [
        b"x" * 65,
        b"has space",
        b"semi;colon",
        b'quote"d',
        b"slash/inside",
        "café-über".encode(),
        "日本語".encode(),
        b"",
    ],
    ids=["too-long", "space", "semicolon", "quote", "slash", "latin-accents", "cjk", "empty"],
)
async def test_an_invalid_id_is_replaced_by_a_generated_one(sent: bytes):
    app = app_with(Route("/teapot", whoami))

    messages = await raw_request(app, [(b"x-request-id", sent)])

    echoed = dict(messages[0]["headers"])[b"x-request-id"].decode()
    assert UUID_HEX.fullmatch(echoed)
    assert f'"trace_id":"{echoed}"' in messages[1]["body"].decode().replace(" ", "")


@pytest.mark.parametrize(
    "sent",
    [
        b"good\r\nX-Evil: 1",
        b"good\nX-Evil: 1",
        b"good\rX-Evil: 1",
        b"abc-123\n",  # a trailing newline must not slip past an end-of-string anchor
        b"abc-123\r\n",
        b"a\x00b",
    ],
    ids=["crlf", "lf", "cr", "trailing-lf", "trailing-crlf", "nul"],
)
async def test_line_breaks_in_an_id_cannot_forge_a_header_or_a_log_line(sent: bytes):
    with capture_logs() as logs:
        messages = await raw_request(build_app(), [(b"x-request-id", sent)])

    headers = dict(messages[0]["headers"])
    assert UUID_HEX.fullmatch(headers[b"x-request-id"].decode())
    assert b"x-evil" not in headers
    assert "X-Evil" not in str(logs) and "\n" not in str(logs)


async def test_the_header_name_is_case_insensitive(http: AsyncClient):
    response = await http.get("/teapot", headers={"x-REQUEST-id": "abc-123"})

    assert response.headers[REQUEST_ID_HEADER] == "abc-123"


async def test_the_response_keeps_its_own_status_headers_and_body(http: AsyncClient):
    response = await http.get("/teapot")

    assert (response.status_code, response.text) == (418, "short and stout")
    assert response.headers["content-type"].startswith("text/plain")


# --- errors -------------------------------------------------------------------------------------


async def test_a_404_carries_the_id(http: AsyncClient):
    response = await http.get("/missing", headers={REQUEST_ID_HEADER: "abc-123"})

    assert response.status_code == 404
    assert response.headers[REQUEST_ID_HEADER] == "abc-123"


async def test_a_405_carries_the_id(http: AsyncClient):
    response = await http.post("/teapot", headers={REQUEST_ID_HEADER: "abc-123"})

    assert response.status_code == 405
    assert response.headers[REQUEST_ID_HEADER] == "abc-123"


async def test_an_unhandled_error_response_carries_the_id(http: AsyncClient):
    # The person whose request crashed is the one who needs the id to quote in a bug report.
    response = await http.get("/boom", headers={REQUEST_ID_HEADER: "abc-123"})

    assert response.status_code == 500
    assert response.headers[REQUEST_ID_HEADER] == "abc-123"


async def test_an_unhandled_error_is_logged_as_a_500(http: AsyncClient):
    with capture_logs() as logs:
        await http.get("/boom")

    [line] = [entry for entry in logs if entry["event"] == "request"]
    assert (line["method"], line["path"], line["status"]) == ("GET", "/boom", 500)


async def test_the_exception_still_reaches_the_server_and_the_id_is_unbound():
    transport = ASGITransport(app=build_app())  # raise_app_exceptions defaults to True
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with pytest.raises(RuntimeError, match="the handler crashed"):
            await client.get("/boom")

    assert structlog.contextvars.get_contextvars() == {}


# --- the log context ----------------------------------------------------------------------------


async def test_the_id_is_bound_as_request_id_and_trace_id_inside_the_handler(http: AsyncClient):
    response = await http.get("/whoami", headers={REQUEST_ID_HEADER: "abc-123"})

    assert response.json() == {
        "sandbox": None,
        "batch_id": None,
        "trace_id": "abc-123",
        "document_id": None,
        "claim_id": None,
        "request_id": "abc-123",  # only the request's own ids are bound
    }


async def test_a_generated_id_is_what_the_handler_sees_and_the_header_echoes(http: AsyncClient):
    response = await http.get("/whoami")

    seen = response.json()
    assert seen["trace_id"] == seen["request_id"] == response.headers[REQUEST_ID_HEADER]


async def test_nothing_stays_bound_after_the_request(http: AsyncClient):
    await http.get("/whoami", headers={REQUEST_ID_HEADER: "abc-123"})
    await http.get("/missing")

    assert structlog.contextvars.get_contextvars() == {}
    assert current_ids()["trace_id"] is None


async def test_a_context_bound_by_the_caller_is_restored_after_the_request(http: AsyncClient):
    with structlog.contextvars.bound_contextvars(trace_id="outer", batch_id="b1"):
        await http.get("/whoami", headers={REQUEST_ID_HEADER: "abc-123"})
        assert current_ids()["trace_id"] == "outer" and current_ids()["batch_id"] == "b1"


async def test_concurrent_requests_never_share_ids(http: AsyncClient):
    async def fetch(n: int) -> tuple[str, dict[str, Any]]:
        sent = f"req-{n}"
        # the later a request starts, the sooner it finishes: they overlap and finish reversed
        response = await http.get(
            f"/slow?delay={(20 - n) / 200}", headers={REQUEST_ID_HEADER: sent}
        )
        return sent, response.json() | {"echoed": response.headers[REQUEST_ID_HEADER]}

    results = await asyncio.gather(*(fetch(n) for n in range(20)))

    for sent, seen in results:
        assert seen["request_id"] == seen["trace_id"] == seen["echoed"] == sent
    assert structlog.contextvars.get_contextvars() == {}


async def test_a_task_the_handler_starts_inherits_the_request_id():
    # what an embedded batch task relies on: ids bound for a request reach the work it spawns
    seen: list[dict[str, str | None]] = []

    async def spawn(_request: Request) -> PlainTextResponse:
        seen.append(await asyncio.create_task(asyncio.to_thread(current_ids)))
        return PlainTextResponse("ok")

    app = app_with(Route("/spawn", spawn))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.get("/spawn", headers={REQUEST_ID_HEADER: "abc-123"})

    assert seen[0]["trace_id"] == "abc-123"


# --- the request log line -----------------------------------------------------------------------


async def test_one_request_line_per_request_with_method_path_status_and_duration(
    http: AsyncClient,
):
    with capture_logs() as logs:
        await http.get("/teapot")
        await http.post("/teapot")
        await http.get("/missing")

    assert [(e["event"], e["method"], e["path"], e["status"]) for e in logs] == [
        ("request", "GET", "/teapot", 418),
        ("request", "POST", "/teapot", 405),
        ("request", "GET", "/missing", 404),
    ]  # exactly one line each, nothing else
    assert all(e["log_level"] == "info" for e in logs)
    assert all(isinstance(e["duration_ms"], float) and e["duration_ms"] >= 0 for e in logs)


async def test_the_path_is_logged_without_the_query_string(http: AsyncClient):
    with capture_logs() as logs:
        await http.get("/whoami?token=secret")

    [line] = logs
    assert line["path"] == "/whoami" and "secret" not in str(line)


async def test_the_duration_covers_the_handler(http: AsyncClient):
    with capture_logs() as logs:
        await http.get("/slow?delay=0.05")

    [line] = logs
    assert line["duration_ms"] >= 45  # a little under 50 ms allows for timer granularity


async def test_the_request_line_carries_the_request_and_trace_ids(http: AsyncClient):
    # capture_logs replaces the processors, so merge the context back in as the real config does
    with capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs:
        await http.get("/teapot", headers={REQUEST_ID_HEADER: "abc-123"})

    [line] = logs
    assert line["request_id"] == line["trace_id"] == "abc-123"


@pytest.mark.parametrize("path", ["/healthz", "/readyz"])
async def test_health_probes_are_logged_at_debug_only(http: AsyncClient, path: str):
    with capture_logs() as logs:
        await http.get(path)

    [line] = logs
    assert (line["event"], line["log_level"], line["path"]) == ("request", "debug", path)


async def test_a_path_that_only_starts_like_a_probe_is_still_info(http: AsyncClient):
    with capture_logs() as logs:
        await http.get("/healthz/deep")
        await http.get("/healthzz")

    assert [e["log_level"] for e in logs] == ["info", "info"]


# --- streaming ----------------------------------------------------------------------------------


async def test_a_streaming_response_is_not_buffered_and_still_carries_the_id():
    release = asyncio.Event()
    finished = asyncio.Event()

    async def chunks() -> AsyncIterator[str]:
        yield "first\n"
        await release.wait()  # the stream stays open until the test says so
        yield "second\n"
        finished.set()

    async def stream(_request: Request) -> StreamingResponse:
        return StreamingResponse(chunks(), media_type="text/event-stream")

    app = app_with(Route("/teapot", stream))
    messages: list[Message] = []
    first_chunk = asyncio.Event()

    async def receive() -> Message:
        await asyncio.sleep(3600)  # the client never disconnects
        raise AssertionError("unreachable")

    async def send(message: Message) -> None:
        messages.append(message)
        if message["type"] == "http.response.body" and message.get("body"):
            first_chunk.set()

    scope = http_scope(headers=[(b"x-request-id", b"abc-123")])
    with capture_logs() as logs:
        running = asyncio.create_task(app(scope, receive, send))
        try:
            await asyncio.wait_for(first_chunk.wait(), timeout=5)
            # the first chunk is out while the generator is still suspended
            assert not finished.is_set() and not running.done()
            start = messages[0]
            assert start["type"] == "http.response.start" and start["status"] == 200
            assert dict(start["headers"])[b"x-request-id"] == b"abc-123"
            assert messages[1]["body"] == b"first\n"
            assert logs == []  # the request line is written when the stream ends, not before

            release.set()
            await asyncio.wait_for(running, timeout=5)
        finally:
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)

    assert finished.is_set()
    assert [m["body"] for m in messages[1:] if m.get("body")] == [b"first\n", b"second\n"]
    [line] = logs
    assert (line["event"], line["status"], line["path"]) == ("request", 200, "/teapot")


async def test_a_client_that_disconnects_mid_stream_still_gets_a_request_line_and_unbinds():
    async def chunks() -> AsyncIterator[str]:
        yield "first\n"
        await asyncio.sleep(3600)
        yield "never\n"

    async def stream(_request: Request) -> StreamingResponse:
        return StreamingResponse(chunks(), media_type="text/event-stream")

    app = app_with(Route("/teapot", stream))
    first_chunk = asyncio.Event()

    async def receive() -> Message:
        await first_chunk.wait()
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        if message["type"] == "http.response.body" and message.get("body"):
            first_chunk.set()

    with capture_logs() as logs:
        await asyncio.wait_for(app(http_scope(), receive, send), timeout=5)

    [line] = logs
    assert (line["event"], line["status"]) == ("request", 200)
    assert structlog.contextvars.get_contextvars() == {}


# --- other scopes -------------------------------------------------------------------------------


@pytest.mark.parametrize("scope_type", ["websocket", "lifespan"])
async def test_websocket_and_lifespan_scopes_pass_through_untouched(scope_type: str):
    seen: list[Any] = []

    async def inner(scope: Scope, receive: Receive, send: Send) -> None:
        seen.append((scope, receive, send, structlog.contextvars.get_contextvars()))

    scope: Scope = {"type": scope_type, "path": "/ws", "headers": [(b"x-request-id", b"abc-123")]}

    async def receive() -> Message:
        raise AssertionError("not read")

    async def send(_message: Message) -> None:
        raise AssertionError("nothing is sent")

    with capture_logs() as logs:
        await RequestContextMiddleware(inner)(scope, receive, send)

    [(got_scope, got_receive, got_send, bound)] = seen
    assert got_scope is scope and got_receive is receive and got_send is send  # no wrapping
    assert bound == {}  # no id bound for them
    assert logs == []


async def test_the_lifespan_of_the_app_still_runs_behind_the_middleware():
    app = build_app()
    inbox: asyncio.Queue[Message] = asyncio.Queue()
    outbox: asyncio.Queue[Message] = asyncio.Queue()

    await inbox.put({"type": "lifespan.startup"})
    task = asyncio.create_task(app({"type": "lifespan"}, inbox.get, outbox.put))
    assert (await asyncio.wait_for(outbox.get(), timeout=5))["type"] == "lifespan.startup.complete"
    await inbox.put({"type": "lifespan.shutdown"})
    assert (await asyncio.wait_for(outbox.get(), timeout=5))["type"] == "lifespan.shutdown.complete"
    await asyncio.wait_for(task, timeout=5)


# --- the real app -------------------------------------------------------------------------------


@pytest.fixture
async def real() -> AsyncIterator[AsyncClient]:
    app = create_app()  # no container and no lifespan: enough for the routes used here
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def test_the_real_app_echoes_a_valid_id_on_success_and_on_errors(real: AsyncClient):
    for path, status in (("/healthz", 200), ("/no-such-route", 404)):
        response = await real.get(path, headers={REQUEST_ID_HEADER: "abc-123"})
        assert response.status_code == status
        assert response.headers[REQUEST_ID_HEADER] == "abc-123"


async def test_the_real_app_generates_an_id_and_replaces_a_bad_one(real: AsyncClient):
    absent = await real.get("/healthz")
    forged = await real.get("/healthz", headers={REQUEST_ID_HEADER: "bad id with spaces"})

    assert UUID_HEX.fullmatch(absent.headers[REQUEST_ID_HEADER])
    assert UUID_HEX.fullmatch(forged.headers[REQUEST_ID_HEADER])


async def test_the_real_app_logs_the_request_once_and_the_probe_quietly(real: AsyncClient):
    with capture_logs() as logs:
        await real.get("/healthz")
        await real.get("/no-such-route")

    assert [(e["path"], e["status"], e["log_level"]) for e in logs] == [
        ("/healthz", 200, "debug"),
        ("/no-such-route", 404, "info"),
    ]


async def test_a_browser_may_send_and_read_the_request_id_across_origins(real: AsyncClient):
    origin = "http://localhost:3000"
    preflight = await real.options(
        "/v1/batches",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "X-Request-ID",
        },
    )
    actual = await real.get("/healthz", headers={"Origin": origin, REQUEST_ID_HEADER: "abc-123"})

    assert preflight.status_code == 200
    assert "x-request-id" in preflight.headers["access-control-allow-headers"].lower()
    assert "x-request-id" in actual.headers["access-control-expose-headers"].lower()
    assert actual.headers[REQUEST_ID_HEADER] == "abc-123"
