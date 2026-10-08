from __future__ import annotations

from httpx import AsyncClient

ORIGIN = "http://localhost:3000"


async def test_preflight_allows_the_web_app_and_its_custom_headers(http: AsyncClient):
    resp = await http.options(
        "/v1/claims",
        headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "x-persona, idempotency-key, last-event-id",
        },
    )
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == ORIGIN
    allowed = resp.headers["access-control-allow-headers"].lower()
    assert all(h in allowed for h in ("x-persona", "idempotency-key", "last-event-id"))


async def test_other_origins_are_not_allowed(http: AsyncClient):
    resp = await http.get("/v1/employees", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in resp.headers
    ok = await http.get("/v1/employees", headers={"Origin": ORIGIN})
    assert ok.headers["access-control-allow-origin"] == ORIGIN
