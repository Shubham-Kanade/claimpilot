"""Configuration from the environment and the process entry point."""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import uvicorn
from pydantic import ValidationError
from starlette.applications import Starlette

from claimpilot_mcp import __main__ as entrypoint
from claimpilot_mcp.settings import Settings

# -- settings ------------------------------------------------------------------------------------


def test_defaults_point_at_a_local_api_as_the_demo_employee():
    settings = Settings()
    assert settings.claimpilot_api_url == "http://localhost:8000"
    assert settings.claimpilot_persona == "DEMO-ASHA"
    assert settings.claimpilot_timeout_s == 30.0
    assert settings.claimpilot_upload_timeout_s == 120.0
    assert (settings.claimpilot_max_files, settings.claimpilot_max_file_mb) == (30, 15)
    assert settings.claimpilot_upload_root is None
    assert settings.mcp_host == "127.0.0.1"
    assert settings.mcp_port == 8103
    assert settings.log_level == "info"


def test_environment_overrides(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("CLAIMPILOT_API_URL", "https://space.example.test/api")
    monkeypatch.setenv("CLAIMPILOT_PERSONA", "DEMO-RAVI")
    monkeypatch.setenv("CLAIMPILOT_TIMEOUT_S", "5")
    monkeypatch.setenv("CLAIMPILOT_UPLOAD_TIMEOUT_S", "60.5")
    monkeypatch.setenv("CLAIMPILOT_MAX_FILES", "10")
    monkeypatch.setenv("CLAIMPILOT_MAX_FILE_MB", "4")
    monkeypatch.setenv("CLAIMPILOT_UPLOAD_ROOT", str(tmp_path))
    monkeypatch.setenv("MCP_HOST", "0.0.0.0")
    monkeypatch.setenv("MCP_PORT", "9999")
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    settings = Settings()
    assert settings.claimpilot_api_url == "https://space.example.test/api"
    assert settings.claimpilot_persona == "DEMO-RAVI"
    assert (settings.claimpilot_timeout_s, settings.claimpilot_upload_timeout_s) == (5.0, 60.5)
    assert (settings.claimpilot_max_files, settings.claimpilot_max_file_mb) == (10, 4)
    assert settings.claimpilot_upload_root == tmp_path.resolve()
    assert (settings.mcp_host, settings.mcp_port, settings.log_level) == (
        "0.0.0.0",
        9999,
        "WARNING",
    )


def test_empty_variables_count_as_unset(monkeypatch: pytest.MonkeyPatch):
    """An empty CLAIMPILOT_UPLOAD_ROOT must never turn into the current folder."""
    monkeypatch.setenv("CLAIMPILOT_UPLOAD_ROOT", "")
    monkeypatch.setenv("CLAIMPILOT_PERSONA", "")
    settings = Settings()
    assert settings.claimpilot_upload_root is None
    assert settings.claimpilot_persona == "DEMO-ASHA"


def test_the_url_is_trimmed_and_loses_its_trailing_slash():
    assert (
        Settings(claimpilot_api_url="  http://api:8000/  ").claimpilot_api_url == "http://api:8000"
    )
    assert (
        Settings(claimpilot_api_url="https://h.test/api/").claimpilot_api_url
        == "https://h.test/api"
    )


@pytest.mark.parametrize(
    "url", ["localhost:8000", "ftp://host", "http://", "api.test", "http://api.test?x=1", "/api"]
)
def test_a_url_that_is_not_an_http_address_is_refused(url: str):
    with pytest.raises(ValidationError, match="CLAIMPILOT_API_URL must be an http"):
        Settings(claimpilot_api_url=url)


@pytest.mark.parametrize("persona", ["DEMO-ASHA", "DEMO-RAVI", "P001", "a.b_c@d", " DEMO-ASHA "])
def test_employee_ids_are_accepted(persona: str):
    assert Settings(claimpilot_persona=persona).claimpilot_persona == persona.strip()


@pytest.mark.parametrize(
    "persona", ["", "   ", "bad persona", "x\nInjected: header", "-leading", "a" * 65, "a/b", "a;b"]
)
def test_anything_that_could_split_a_header_is_refused(persona: str):
    with pytest.raises(ValidationError, match="CLAIMPILOT_PERSONA must be an employee id"):
        Settings(claimpilot_persona=persona)


def test_validation_errors_never_echo_the_value():
    with pytest.raises(ValidationError) as raised:
        Settings(claimpilot_persona="SECRET-VALUE\nX")
    assert "SECRET-VALUE" not in str(raised.value)


@pytest.mark.parametrize(
    "overrides",
    [
        {"mcp_port": 0},
        {"mcp_port": 65536},
        {"claimpilot_timeout_s": 0},
        {"claimpilot_upload_timeout_s": -1},
        {"claimpilot_max_files": 0},
        {"claimpilot_max_file_mb": 0},
    ],
)
def test_numbers_must_make_sense(overrides: dict[str, Any]):
    with pytest.raises(ValidationError):
        Settings(**overrides)


def test_the_upload_folder_is_expanded_and_made_absolute(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    root = Settings(claimpilot_upload_root=Path("~/receipts")).claimpilot_upload_root
    assert root == (tmp_path / "receipts").resolve()
    assert root is not None and root.is_absolute()


# -- the entry point -----------------------------------------------------------------------------


def stub_server(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace create_server with a stub that records how it was called and how it was run."""
    seen: dict[str, Any] = {}

    def fake_create_server(settings: Settings, client: Any, *, allow_any_path: bool) -> Any:
        seen.update(settings=settings, client=client, allow_any_path=allow_any_path)
        return SimpleNamespace(run=lambda transport: seen.update(transport=transport))

    monkeypatch.setattr(entrypoint, "create_server", fake_create_server)
    return seen


def stub_uvicorn(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    def fake_run(app: Any, **kwargs: Any) -> None:
        seen["app"] = app
        seen.update(kwargs)

    monkeypatch.setattr(uvicorn, "run", fake_run)
    return seen


def test_stdio_is_the_default_and_may_read_any_local_path(monkeypatch: pytest.MonkeyPatch):
    seen = stub_server(monkeypatch)
    entrypoint.main([])
    assert seen["transport"] == "stdio"
    assert seen["allow_any_path"] is True
    assert seen["settings"].claimpilot_persona == "DEMO-ASHA"
    assert seen["client"] is not None


def test_the_http_client_does_not_log_every_request(monkeypatch: pytest.MonkeyPatch):
    loggers = [logging.getLogger(name) for name in ("httpx", "httpcore")]
    for logger in loggers:
        monkeypatch.setattr(logger, "level", logging.INFO)
    stub_server(monkeypatch)
    entrypoint.main([])
    assert [logger.level for logger in loggers] == [logging.WARNING, logging.WARNING]


def test_stdio_can_be_chosen_explicitly(monkeypatch: pytest.MonkeyPatch):
    seen = stub_server(monkeypatch)
    entrypoint.main(["--transport", "stdio"])
    assert seen["transport"] == "stdio"


def test_http_serves_the_app_on_the_default_loopback_port(monkeypatch: pytest.MonkeyPatch):
    seen = stub_uvicorn(monkeypatch)
    entrypoint.main(["--transport", "http"])
    assert isinstance(seen["app"], Starlette)
    assert (seen["host"], seen["port"], seen["log_level"]) == ("127.0.0.1", 8103, "info")


def test_http_takes_host_port_and_log_level_from_the_environment(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MCP_HOST", "0.0.0.0")
    monkeypatch.setenv("MCP_PORT", "8123")
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    seen = stub_uvicorn(monkeypatch)
    entrypoint.main(["--transport", "http"])
    assert (seen["host"], seen["port"], seen["log_level"]) == ("0.0.0.0", 8123, "warning")


def test_command_line_flags_beat_the_environment(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MCP_HOST", "0.0.0.0")
    monkeypatch.setenv("MCP_PORT", "8123")
    seen = stub_uvicorn(monkeypatch)
    entrypoint.main(["--transport", "http", "--host", "127.0.0.1", "--port", "9000"])
    assert (seen["host"], seen["port"]) == ("127.0.0.1", 9000)


@pytest.mark.parametrize("port", ["0", "70000", "-5", "http"])
def test_a_bad_port_is_a_usage_error(port: str, capsys: pytest.CaptureFixture[str]):
    with pytest.raises(SystemExit) as raised:
        entrypoint.main(["--transport", "http", "--port", port])
    assert raised.value.code == 2
    assert "port number from 1 to 65535" in capsys.readouterr().err


def test_an_unknown_transport_is_a_usage_error(capsys: pytest.CaptureFixture[str]):
    with pytest.raises(SystemExit) as raised:
        entrypoint.main(["--transport", "sse"])
    assert raised.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_version_is_printed_to_stdout(capsys: pytest.CaptureFixture[str]):
    with pytest.raises(SystemExit) as raised:
        entrypoint.main(["--version"])
    assert raised.value.code == 0
    assert capsys.readouterr().out.startswith("claimpilot-mcp 0.1.0")


def test_bad_configuration_exits_with_a_message_that_names_the_variable_not_the_value(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setenv("CLAIMPILOT_PERSONA", "SECRET VALUE")
    monkeypatch.setenv("CLAIMPILOT_API_URL", "ftp://nope")
    seen = stub_server(monkeypatch)
    with pytest.raises(SystemExit) as raised:
        entrypoint.main([])
    assert raised.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""  # over stdio stdout belongs to the protocol
    assert "invalid configuration" in captured.err
    assert "CLAIMPILOT_PERSONA: CLAIMPILOT_PERSONA must be an employee id" in captured.err
    assert "CLAIMPILOT_API_URL" in captured.err
    assert "SECRET" not in captured.err
    assert "transport" not in seen  # nothing was started


def test_an_error_that_belongs_to_no_field_is_still_reported(capsys: pytest.CaptureFixture[str]):
    error = ValidationError.from_exception_data(
        "Settings",
        [
            {
                "type": "value_error",
                "loc": (),
                "input": "x",
                "ctx": {"error": ValueError("bad thing")},
            }
        ],
    )
    assert entrypoint._config_error(error) == 2  # pyright: ignore[reportPrivateUsage]
    assert "SETTINGS: bad thing" in capsys.readouterr().err
