from __future__ import annotations


async def test_healthz(client):
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_readyz_all_ok(app, client):
    async def ok() -> None:
        return None

    app.state.readiness_checks = {"redis": ok, "postgres": ok}
    resp = await client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ready", "checks": {"redis": "ok", "postgres": "ok"}}


async def test_readyz_reports_failed_dependency(app, client):
    async def ok() -> None:
        return None

    async def down() -> None:
        raise ConnectionRefusedError

    app.state.readiness_checks = {"redis": ok, "postgres": down}
    resp = await client.get("/readyz")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["checks"]["postgres"] == "error: ConnectionRefusedError"


async def test_meta_lists_every_route(client):
    resp = await client.get("/v1/meta")
    assert resp.status_code == 200
    routes = {r["route"]: r for r in resp.json()["routes"]}
    assert {"extraction", "agent_chat", "policy_compile"} <= routes.keys()
    assert all(r["model_id"].startswith("claude-") for r in routes.values())
