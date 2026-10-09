"""The request id: echoed on every response, and remembered by the batch an upload creates.

The id of the upload request becomes the batch's trace id, so the worker (which never sees the
request) can stamp it on every log line and cost-ledger row the batch causes.
"""

from __future__ import annotations

import asyncio
import re

import pytest
from httpx import AsyncClient

from .conftest import ASHA, PNG, World, as_persona

HEADER = "X-Request-ID"
UUID_HEX = re.compile(r"[0-9a-f]{32}")


def one_file(name: str = "a.png"):
    return [("files", (name, PNG, "application/octet-stream"))]


async def upload(http: AsyncClient, **headers: str):
    return await http.post("/v1/batches", files=one_file(), headers={**as_persona(ASHA), **headers})


async def test_the_upload_stores_the_callers_request_id_as_the_batchs_trace(
    http: AsyncClient, world: World
):
    resp = await upload(http, **{"X-Request-ID": "abc-123"})

    assert resp.status_code == 202
    assert resp.headers[HEADER] == "abc-123"
    assert await world.repo.batch_trace_id(resp.json()["batch_id"]) == "abc-123"


async def test_without_a_request_id_the_stored_trace_is_the_one_the_response_echoes(
    http: AsyncClient, world: World
):
    resp = await upload(http)

    echoed = resp.headers[HEADER]
    assert UUID_HEX.fullmatch(echoed)
    assert await world.repo.batch_trace_id(resp.json()["batch_id"]) == echoed


@pytest.mark.parametrize("sent", ["has space", "x" * 65, "semi;colon", "café"])
async def test_an_invalid_request_id_is_replaced_and_the_replacement_is_stored(
    http: AsyncClient, world: World, sent: str
):
    resp = await http.post(
        "/v1/batches",
        files=one_file(),
        headers=[(b"x-persona", ASHA.id.encode()), (b"x-request-id", sent.encode())],  # raw bytes
    )

    echoed = resp.headers[HEADER]
    assert UUID_HEX.fullmatch(echoed) and echoed != sent
    assert await world.repo.batch_trace_id(resp.json()["batch_id"]) == echoed


async def test_each_upload_gets_its_own_trace(http: AsyncClient, world: World):
    first = await upload(http)
    second = await upload(http)

    traces = {await world.repo.batch_trace_id(r.json()["batch_id"]) for r in (first, second)}
    assert len(traces) == 2 and None not in traces


async def test_concurrent_uploads_keep_their_own_traces(http: AsyncClient, world: World):
    sent = [f"upload-{n}" for n in range(8)]

    responses = await asyncio.gather(*(upload(http, **{"X-Request-ID": s}) for s in sent))

    for expected, resp in zip(sent, responses, strict=True):
        assert resp.headers[HEADER] == expected
        assert await world.repo.batch_trace_id(resp.json()["batch_id"]) == expected


async def test_a_retried_upload_with_the_same_id_makes_a_new_batch_that_shares_the_trace(
    http: AsyncClient, world: World
):
    # a client retry reuses its request id: both batches trace back to that one id
    first = await upload(http, **{"X-Request-ID": "retry-1"})
    second = await upload(http, **{"X-Request-ID": "retry-1"})

    ids = [r.json()["batch_id"] for r in (first, second)]
    assert ids[0] != ids[1]
    assert [await world.repo.batch_trace_id(i) for i in ids] == ["retry-1", "retry-1"]


async def test_the_trace_is_stored_in_the_demo_sandbox_too(http: AsyncClient, world: World):
    world.container.settings.demo_mode = True

    resp = await upload(http, **{"X-Request-ID": "abc-123", "X-Sandbox": "visitor-aaaaaaaaaaaa"})

    assert resp.status_code == 202
    assert await world.repo.batch_trace_id(resp.json()["batch_id"]) == "abc-123"


# --- error responses carry the id too -----------------------------------------------------------


@pytest.mark.parametrize(
    ("request_kwargs", "status", "problem"),
    [
        ({"headers": {}}, 401, "missing_persona"),
        ({"headers": {"X-Persona": "NOBODY"}}, 401, "unknown_persona"),
    ],
    ids=["no-persona", "unknown-persona"],
)
async def test_a_problem_response_carries_the_request_id(
    http: AsyncClient, request_kwargs: dict, status: int, problem: str
):
    resp = await http.post(
        "/v1/batches",
        files=one_file(),
        headers={**request_kwargs["headers"], HEADER: "abc-123"},
    )

    assert resp.status_code == status and resp.json()["type"] == problem
    assert resp.headers[HEADER] == "abc-123"


async def test_a_rejected_upload_carries_the_request_id_and_creates_no_batch(
    http: AsyncClient, world: World
):
    resp = await http.post(
        "/v1/batches",
        files=[("files", ("r.png", b"GIF89a not an image we support", "image/png"))],
        headers={**as_persona(ASHA), HEADER: "abc-123"},
    )

    assert resp.status_code == 422 and resp.headers[HEADER] == "abc-123"
    assert world.enqueued == []


async def test_a_missing_batch_carries_the_request_id(http: AsyncClient):
    resp = await http.get("/v1/batches/nope", headers={**as_persona(ASHA), HEADER: "abc-123"})

    assert resp.status_code == 404 and resp.headers[HEADER] == "abc-123"


async def test_a_request_validation_error_carries_the_request_id(http: AsyncClient):
    resp = await http.post("/v1/batches", headers={**as_persona(ASHA), HEADER: "abc-123"})

    assert resp.status_code == 422 and resp.headers[HEADER] == "abc-123"


async def test_probes_carry_the_request_id(http: AsyncClient):
    resp = await http.get("/healthz", headers={HEADER: "probe-1"})

    assert resp.status_code == 200 and resp.headers[HEADER] == "probe-1"
