"""Start over: the public demo's reset, and the flags the UI reads to know it is the demo."""

from __future__ import annotations

from httpx import AsyncClient

from claimpilot.pipeline.repo import NewFile

from .conftest import ASHA, MEERA, RAVI, World, as_persona, make_claim, seed_claim


async def upload(world: World, employee: str, name: str) -> str:
    """A batch with one stored file; returns the storage key."""
    key = f"{employee}/{name}.png"
    await world.container.storage.put(key, b"x")
    await world.repo.create_batch(employee, [NewFile(name, "a" * 64, key)])
    return key


async def test_start_over_is_off_unless_the_demo_is_on(http: AsyncClient, world: World):
    resp = await http.post("/v1/demo/reset", headers=as_persona(ASHA))
    assert resp.status_code == 404 and resp.json()["type"] == "demo_disabled"


async def test_an_employee_starts_over_without_touching_anyone_else(
    http: AsyncClient, world: World
):
    world.container.settings.demo_mode = True
    await seed_claim(world, make_claim("clm-asha", employee_id="P001"))
    await seed_claim(world, make_claim("clm-meera", employee_id="P002"))
    mine = await upload(world, "P001", "mine")
    theirs = await upload(world, "P002", "theirs")

    resp = await http.post("/v1/demo/reset", headers=as_persona(ASHA))

    assert resp.status_code == 200
    assert resp.json() == {"batches": 2, "documents": 2, "claims": 1}
    assert await world.container.repo.get_claim("clm-asha") is None
    assert await world.container.repo.get_claim("clm-meera") is not None
    assert mine not in world.container.storage.objects  # type: ignore[attr-defined]
    assert theirs in world.container.storage.objects  # type: ignore[attr-defined]
    listing = await http.get("/v1/claims", headers=as_persona(ASHA))
    assert listing.json() == []


async def test_the_approver_empties_the_whole_demo(http: AsyncClient, world: World):
    world.container.settings.demo_mode = True
    await seed_claim(world, make_claim("clm-asha", employee_id="P001"))
    await seed_claim(world, make_claim("clm-meera", employee_id="P002"))

    resp = await http.post("/v1/demo/reset", headers=as_persona(RAVI))

    assert resp.json()["claims"] == 2
    assert (await http.get("/v1/approvals", headers=as_persona(RAVI))).json() == []
    assert await world.container.repo.get_claim("clm-meera") is None


async def test_starting_over_forgets_the_audit_trail_of_what_was_deleted(
    http: AsyncClient, world: World
):
    world.container.settings.demo_mode = True
    await seed_claim(world, make_claim("clm-asha"))
    await http.post(
        "/v1/claims/clm-asha/answers",
        json={"answers": {"q-attendees": "Orion: A. Rao"}},
        headers=as_persona(ASHA),
    )
    assert [e.action for e in await world.repo.audit_trail("clm-asha")] == ["claim_answered"]

    await http.post("/v1/demo/reset", headers=as_persona(ASHA))

    assert await world.repo.audit_trail("clm-asha") == []
    assert [e.action for e in await world.repo.audit_trail("P001")] == ["demo_reset"]


async def test_starting_over_with_nothing_to_delete_is_fine(http: AsyncClient, world: World):
    world.container.settings.demo_mode = True
    resp = await http.post("/v1/demo/reset", headers=as_persona(MEERA))
    assert resp.status_code == 200 and resp.json() == {"batches": 0, "documents": 0, "claims": 0}


async def test_an_unknown_persona_cannot_reset(http: AsyncClient, world: World):
    world.container.settings.demo_mode = True
    resp = await http.post("/v1/demo/reset", headers={"X-Persona": "NOBODY"})
    assert resp.status_code == 401


async def test_meta_tells_the_ui_it_is_the_demo(client: AsyncClient, monkeypatch):
    from claimpilot import meta
    from claimpilot.config import Settings

    monkeypatch.setattr(meta, "get_settings", lambda: Settings(demo_mode=True, runtime="embedded"))
    body = (await client.get("/v1/meta")).json()
    assert body["demo"] is True and body["runtime"] == "embedded"


async def test_meta_publishes_the_llm_profile_and_the_upload_limits(
    client: AsyncClient, monkeypatch
):
    from claimpilot import meta
    from claimpilot.config import Settings

    monkeypatch.setattr(
        meta,
        "get_settings",
        lambda: Settings(
            llm_mode="live",
            llm_record=True,
            daily_llm_budget_usd=2.5,
            max_batch_files=20,
            max_upload_mb=6,
        ),
    )
    body = (await client.get("/v1/meta")).json()
    assert body["llm_mode"] == "live" and body["llm_record"] is True
    assert body["daily_llm_budget_usd"] == 2.5
    assert (body["max_batch_files"], body["max_upload_mb"]) == (20, 6)


async def test_meta_defaults_to_the_distributed_non_demo_setup(client: AsyncClient):
    body = (await client.get("/v1/meta")).json()
    assert body["demo"] is False and body["runtime"] == "distributed"
