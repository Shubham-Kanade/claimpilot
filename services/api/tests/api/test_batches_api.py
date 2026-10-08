from __future__ import annotations

import json

from httpx import AsyncClient

from claimpilot.pipeline.events import BatchDone, BatchStarted, DocumentExtracted

from .conftest import ASHA, MEERA, PDF, PNG, RAVI, World, as_persona


def files(*items: tuple[str, bytes]):
    return [("files", (name, data, "application/octet-stream")) for name, data in items]


async def upload(http: AsyncClient, persona=ASHA, *items: tuple[str, bytes]):
    return await http.post(
        "/v1/batches",
        files=files(*(items or (("a.png", PNG), ("b.pdf", PDF)))),
        headers=as_persona(persona),
    )


async def test_upload_stores_files_creates_batch_and_enqueues(http: AsyncClient, world: World):
    resp = await upload(http)
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "queued" and [d["filename"] for d in body["documents"]] == [
        "a.png",
        "b.pdf",
    ]
    assert body["events_url"] == f"/v1/batches/{body['batch_id']}/events"
    assert world.enqueued == [body["batch_id"]]

    stored = world.container.storage.objects  # type: ignore[attr-defined]
    assert sorted(k.rsplit(".", 1)[1] for k in stored) == ["pdf", "png"]
    assert all(k.startswith(body["batch_id"] + "/") for k in stored)

    view = (await http.get(f"/v1/batches/{body['batch_id']}", headers=as_persona(ASHA))).json()
    assert view["status"] == "queued" and view["total"] == 2 and len(view["documents"]) == 2
    trail = await world.repo.audit_trail(body["batch_id"])
    assert [e.action for e in trail] == ["batch_uploaded"] and trail[0].actor == "P001"


async def test_filenames_are_sanitised(http: AsyncClient):
    resp = await upload(http, ASHA, ("..\\..\\evil/../../x.png", PNG), ("   ", PNG))
    names = [d["filename"] for d in resp.json()["documents"]]
    assert names == ["x.png", "receipt-2"]


async def test_the_declared_content_type_is_never_trusted(http: AsyncClient):
    resp = await http.post(
        "/v1/batches",
        files=[("files", ("r.png", b"GIF89a not an image we support", "image/png"))],
        headers=as_persona(ASHA),
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["type"] == "unsupported_files"
    assert body["detail"]["files"][0]["filename"] == "r.png"


async def test_rejects_oversized_and_too_many_files(http: AsyncClient, world: World):
    big = PNG + b"\x00" * (1024 * 1024)  # over max_upload_mb=1
    resp = await upload(http, ASHA, ("big.png", big))
    assert resp.status_code == 413 and resp.json()["type"] == "file_too_large"

    resp = await upload(http, ASHA, *[(f"{i}.png", PNG) for i in range(4)])  # max_batch_files=3
    assert resp.status_code == 413 and resp.json()["type"] == "too_many_files"
    assert world.enqueued == []  # nothing was queued for a rejected upload


async def test_upload_requires_a_known_persona(http: AsyncClient):
    resp = await http.post("/v1/batches", files=files(("a.png", PNG)))
    assert resp.status_code == 401 and resp.json()["type"] == "missing_persona"
    resp = await http.post(
        "/v1/batches", files=files(("a.png", PNG)), headers={"X-Persona": "nobody"}
    )
    assert resp.status_code == 401 and resp.json()["type"] == "unknown_persona"
    assert resp.headers["content-type"].startswith("application/problem+json")


async def test_batches_are_private_to_their_owner_but_visible_to_approvers(http: AsyncClient):
    batch_id = (await upload(http)).json()["batch_id"]
    assert (await http.get(f"/v1/batches/{batch_id}", headers=as_persona(MEERA))).status_code == 404
    assert (await http.get(f"/v1/batches/{batch_id}", headers=as_persona(RAVI))).status_code == 200
    missing = await http.get("/v1/batches/nope", headers=as_persona(ASHA))
    assert missing.status_code == 404 and missing.json()["type"] == "batch_not_found"


def started(batch_id: str, total: int = 1) -> BatchStarted:
    return BatchStarted(batch_id=batch_id, total=total)


async def test_history_returns_events_as_json_with_offset(http: AsyncClient, world: World):
    batch_id = (await upload(http)).json()["batch_id"]
    await world.container.events.publish(started(batch_id, 2))
    await world.container.events.publish(
        BatchDone(batch_id=batch_id, processed=2, failed=0, claims=1, cost_usd=0.001)
    )
    everything = (
        await http.get(f"/v1/batches/{batch_id}/history", headers=as_persona(ASHA))
    ).json()
    assert [e["type"] for e in everything] == ["batch_started", "batch_done"]
    later = await http.get(f"/v1/batches/{batch_id}/history?after=1", headers=as_persona(ASHA))
    assert [e["type"] for e in later.json()] == ["batch_done"]


async def read_sse(http: AsyncClient, url: str, headers: dict[str, str]) -> list[dict[str, str]]:
    frames: list[dict[str, str]] = []
    async with http.stream("GET", url, headers=headers) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        current: dict[str, str] = {}
        async for line in resp.aiter_lines():
            if line == "":
                if current:
                    frames.append(current)
                current = {}
            elif not line.startswith(":"):
                key, _, value = line.partition(": ")
                current[key] = value
    return frames


async def test_sse_streams_every_event_then_closes_at_the_terminal_one(
    http: AsyncClient, world: World
):
    batch_id = (await upload(http)).json()["batch_id"]
    bus = world.container.events
    await bus.publish(started(batch_id))
    extracted = DocumentExtracted(
        batch_id=batch_id,
        document_id="d1",
        filename="a.png",
        position=0,
        doc_type="cab_receipt",
        merchant="Zip Cabs",
        total=320.0,
        category="local_conveyance",
        category_confidence=0.9,
        engine="jev",
        cached=False,
        cost_usd=0.0,
    )
    await bus.publish(extracted)
    await bus.publish(BatchDone(batch_id=batch_id, processed=1, failed=0, claims=0, cost_usd=0.0))

    frames = await read_sse(http, f"/v1/batches/{batch_id}/events", as_persona(ASHA))
    assert [f["event"] for f in frames] == ["batch_started", "document_extracted", "batch_done"]
    assert [f["id"] for f in frames] == ["0", "1", "2"]
    assert json.loads(frames[1]["data"])["merchant"] == "Zip Cabs"


async def test_sse_resumes_after_last_event_id(http: AsyncClient, world: World):
    batch_id = (await upload(http)).json()["batch_id"]
    bus = world.container.events
    await bus.publish(started(batch_id))
    await bus.publish(BatchDone(batch_id=batch_id, processed=0, failed=0, claims=0, cost_usd=0.0))
    headers = {**as_persona(ASHA), "Last-Event-ID": "0"}
    frames = await read_sse(http, f"/v1/batches/{batch_id}/events", headers)
    assert [f["event"] for f in frames] == ["batch_done"] and frames[0]["id"] == "1"


async def test_sse_is_private_too(http: AsyncClient):
    batch_id = (await upload(http)).json()["batch_id"]
    resp = await http.get(f"/v1/batches/{batch_id}/events", headers=as_persona(MEERA))
    assert resp.status_code == 404
