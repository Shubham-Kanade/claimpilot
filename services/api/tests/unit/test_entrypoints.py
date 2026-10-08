from __future__ import annotations

import json

import pytest

from claimpilot import export_openapi, worker


def test_export_openapi_prints_valid_document(capsys: pytest.CaptureFixture[str]) -> None:
    export_openapi.main()
    doc = json.loads(capsys.readouterr().out)
    assert doc["info"]["title"] == "ClaimPilot API"
    assert {"/healthz", "/readyz", "/v1/meta"} <= doc["paths"].keys()


async def test_worker_ping_job() -> None:
    assert await worker.ping({}) == "pong"


def test_worker_settings_registers_jobs() -> None:
    assert worker.ping in worker.WorkerSettings.functions
    assert worker.WorkerSettings.job_timeout > 0
