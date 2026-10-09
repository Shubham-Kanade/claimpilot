"""``configure_logging``: one JSON object per line, with the bound ids, for every kind of logger.

Logging is process-global, so every test runs against a fixture that puts the root logger, the
uvicorn loggers and structlog's configuration back the way they were: nothing here may change how
another test (or pytest's own log capture) behaves.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import structlog
import structlog.contextvars

from claimpilot.obs.logs import configure_logging
from claimpilot.telemetry import bound_ids

UVICORN = ("uvicorn", "uvicorn.error", "uvicorn.access")
IDS = {
    "sandbox": "visitor-aaaaaaaaaaaa",
    "batch_id": "b1",
    "trace_id": "t1",
    "document_id": "d1",
    "claim_id": "c1",
}


@pytest.fixture(autouse=True)
def restore_logging() -> Iterator[None]:
    """Snapshot what ``configure_logging`` touches, and put it back afterwards."""
    root = logging.getLogger()
    loggers = [logging.getLogger(name) for name in UVICORN]
    saved_root = (list(root.handlers), root.level)
    saved_loggers = [(lg, list(lg.handlers), lg.propagate, lg.level) for lg in loggers]
    structlog.contextvars.clear_contextvars()
    yield
    structlog.contextvars.clear_contextvars()
    root.handlers[:] = saved_root[0]
    root.setLevel(saved_root[1])
    for lg, handlers, propagate, level in saved_loggers:
        lg.handlers[:] = handlers
        lg.propagate = propagate
        lg.setLevel(level)
    structlog.reset_defaults()


@pytest.fixture
def uvicorn_the_way_uvicorn_leaves_it() -> None:
    """uvicorn installs its own stream handlers and stops its loggers propagating."""
    for name in UVICORN:
        lg = logging.getLogger(name)
        lg.handlers[:] = [logging.StreamHandler()]
        lg.propagate = False
        lg.setLevel(logging.NOTSET)  # levels are uvicorn's to set; other tests may have left any


def lines(capsys: pytest.CaptureFixture[str]) -> list[str]:
    return [line for line in capsys.readouterr().err.splitlines() if line.strip()]


def records(capsys: pytest.CaptureFixture[str]) -> list[dict[str, Any]]:
    return [json.loads(line) for line in lines(capsys)]


def ours() -> list[logging.Handler]:
    return [h for h in logging.getLogger().handlers if getattr(h, "_claimpilot_handler", False)]


# --- JSON ---------------------------------------------------------------------------------------


def test_a_structlog_line_is_one_json_object_with_level_logger_time_and_event(capsys):
    configure_logging("json")

    before = datetime.now(UTC)
    structlog.get_logger("claimpilot.http").info("request", method="GET", status=200)

    [line] = lines(capsys)
    entry = json.loads(line)
    assert entry["event"] == "request" and entry["method"] == "GET" and entry["status"] == 200
    assert entry["level"] == "info" and entry["logger"] == "claimpilot.http"
    stamp = datetime.fromisoformat(entry["timestamp"])
    assert stamp.utcoffset() == timedelta(0)  # UTC
    assert before <= stamp <= datetime.now(UTC)


def test_every_bound_id_is_on_the_line(capsys):
    configure_logging("json")

    with bound_ids(**IDS), structlog.contextvars.bound_contextvars(request_id="r1"):
        structlog.get_logger("claimpilot.pipeline").info("batch started")

    [entry] = records(capsys)
    assert {k: entry[k] for k in (*IDS, "request_id")} == {**IDS, "request_id": "r1"}


def test_ids_that_are_not_bound_are_left_out_not_null(capsys):
    configure_logging("json")

    with bound_ids(trace_id="t1"):
        structlog.get_logger("claimpilot.x").info("hello")

    [entry] = records(capsys)
    assert entry["trace_id"] == "t1"
    assert not {"sandbox", "batch_id", "document_id", "claim_id"} & set(entry)


def test_a_plain_logging_line_is_json_and_carries_the_bound_ids_too(capsys):
    configure_logging("json")

    with bound_ids(**IDS):
        logging.getLogger("claimpilot.x").info("reply interpretation unavailable: %s", "refused")

    [entry] = records(capsys)
    assert entry["event"] == "reply interpretation unavailable: refused"
    assert entry["level"] == "info" and entry["logger"] == "claimpilot.x"
    assert {k: entry[k] for k in IDS} == IDS
    assert datetime.fromisoformat(entry["timestamp"]).tzinfo is not None


def test_a_plain_logging_line_outside_any_context_has_no_ids(capsys):
    configure_logging("json")

    logging.getLogger("claimpilot.x").warning("nothing bound")

    [entry] = records(capsys)
    assert entry["level"] == "warning"
    assert not set(IDS) & set(entry)


def test_the_level_is_named_for_every_severity(capsys):
    configure_logging("json", "DEBUG")
    log = logging.getLogger("claimpilot.x")

    for level in ("debug", "info", "warning", "error", "critical"):
        getattr(log, level)("m")

    assert [e["level"] for e in records(capsys)] == [
        "debug",
        "info",
        "warning",
        "error",
        "critical",
    ]


def test_a_message_with_newlines_stays_on_one_line(capsys):
    configure_logging("json")

    structlog.get_logger("claimpilot.x").info("first\nsecond\r\nthird")
    logging.getLogger("claimpilot.x").info("plain\nrecord")

    out = lines(capsys)
    assert len(out) == 2  # a newline in a message cannot start a forged second line
    assert json.loads(out[0])["event"] == "first\nsecond\r\nthird"
    assert json.loads(out[1])["event"] == "plain\nrecord"


def test_a_structlog_exception_is_rendered_under_exception(capsys):
    configure_logging("json")

    try:
        raise ValueError("bad receipt")
    except ValueError:
        structlog.get_logger("claimpilot.x").exception("batch failed")

    [entry] = records(capsys)
    assert entry["event"] == "batch failed" and entry["level"] == "error"
    assert "Traceback" in entry["exception"] and "ValueError: bad receipt" in entry["exception"]


def test_a_plain_logging_exception_is_rendered_under_exception(capsys):
    configure_logging("json")

    try:
        raise ValueError("bad receipt")
    except ValueError:
        logging.getLogger("claimpilot.x").exception("batch %s failed", "b1")

    [entry] = records(capsys)
    assert entry["event"] == "batch b1 failed"
    assert "Traceback" in entry["exception"] and "ValueError: bad receipt" in entry["exception"]


def test_a_value_that_is_not_json_does_not_lose_the_line(capsys):
    configure_logging("json")

    structlog.get_logger("claimpilot.x").info("odd", path=object(), when=datetime(2026, 10, 9))

    [entry] = records(capsys)
    assert entry["event"] == "odd"


# --- levels ---------------------------------------------------------------------------------------


def test_the_default_level_is_info(capsys):
    configure_logging("json")

    structlog.get_logger("claimpilot.x").debug("hidden")
    logging.getLogger("claimpilot.x").debug("hidden")
    logging.getLogger("claimpilot.x").info("shown")

    assert [e["event"] for e in records(capsys)] == ["shown"]


@pytest.mark.parametrize("level", ["DEBUG", "debug", "Debug"])
def test_the_level_name_is_case_insensitive(capsys, level):
    configure_logging("json", level)

    logging.getLogger("claimpilot.x").debug("shown")

    assert [e["event"] for e in records(capsys)] == ["shown"]
    assert logging.getLogger().level == logging.DEBUG


def test_a_higher_level_hides_the_lower_ones(capsys):
    configure_logging("json", "ERROR")

    logging.getLogger("claimpilot.x").warning("hidden")
    structlog.get_logger("claimpilot.x").error("shown")

    assert [e["event"] for e in records(capsys)] == ["shown"]


def test_an_unknown_level_name_is_refused_loudly():
    with pytest.raises(ValueError, match="Unknown level"):
        configure_logging("json", "LOUD")


# --- console --------------------------------------------------------------------------------------


def test_the_console_format_is_readable_text_with_the_ids(capsys):
    configure_logging("console")

    with bound_ids(**IDS):
        structlog.get_logger("claimpilot.http").info("request", status=200)
        logging.getLogger("claimpilot.x").warning("plain line")

    first, second = lines(capsys)
    assert "request" in first and "status=200" in first and "trace_id=t1" in first
    assert "plain line" in second and "batch_id=b1" in second
    for line in (first, second):
        assert not line.lstrip().startswith("{") and "\x1b[" not in line  # no JSON, no colour codes


def test_the_console_format_renders_exceptions(capsys):
    configure_logging("console")

    try:
        raise ValueError("bad receipt")
    except ValueError:
        logging.getLogger("claimpilot.x").exception("batch failed")

    out = capsys.readouterr().err
    assert "batch failed" in out and "ValueError: bad receipt" in out


def test_switching_format_replaces_the_renderer(capsys):
    configure_logging("console")
    configure_logging("json")

    structlog.get_logger("claimpilot.x").info("now json")

    [entry] = records(capsys)
    assert entry["event"] == "now json"


# --- handlers -----------------------------------------------------------------------------------


def test_calling_it_twice_installs_exactly_one_handler_and_each_line_once(capsys):
    before = len(logging.getLogger().handlers)
    configure_logging("json")
    configure_logging("json")
    configure_logging("json")

    assert len(ours()) == 1 and len(logging.getLogger().handlers) == before + 1
    structlog.get_logger("claimpilot.x").info("once")
    logging.getLogger("claimpilot.x").info("once too")
    assert [e["event"] for e in records(capsys)] == ["once", "once too"]


def test_other_handlers_on_the_root_logger_are_left_alone():
    other = logging.NullHandler()
    logging.getLogger().addHandler(other)
    try:
        configure_logging("json")

        assert other in logging.getLogger().handlers
    finally:
        logging.getLogger().removeHandler(other)


def test_uvicorn_loggers_are_routed_through_the_root_handler(
    capsys, uvicorn_the_way_uvicorn_leaves_it
):
    configure_logging("json")

    for name in UVICORN:
        lg = logging.getLogger(name)
        assert lg.handlers == [] and lg.propagate is True

    with bound_ids(trace_id="t1"):
        logging.getLogger("uvicorn.error").info("Started server process [1]")
        logging.getLogger("uvicorn.access").warning("slow client")
    first, second = records(capsys)
    assert (first["logger"], first["event"]) == ("uvicorn.error", "Started server process [1]")
    assert second["logger"] == "uvicorn.access" and second["trace_id"] == "t1"


def test_chatty_libraries_are_quieted_to_warnings(capsys):
    """uvicorn's access line repeats the middleware's request line; HTTP clients log every call."""
    configure_logging("json")

    for name in ("uvicorn.access", "httpx", "httpx2", "httpcore", "mcp"):
        logging.getLogger(name).info("chatter")
        logging.getLogger(name).warning("worth keeping")

    assert [r["event"] for r in records(capsys)] == ["worth keeping"] * 5


def test_the_handler_writes_to_stderr_not_stdout(capsys):
    configure_logging("json")

    logging.getLogger("claimpilot.x").info("to stderr")

    captured = capsys.readouterr()
    assert captured.out == "" and "to stderr" in captured.err


# --- no import-time side effects -----------------------------------------------------------------


def test_importing_the_app_does_not_configure_logging():
    # a fresh interpreter, because this process has already imported the app
    probe = (
        "import logging\n"
        "root = logging.getLogger()\n"
        "before = (list(root.handlers), root.level)\n"
        "import claimpilot.main, claimpilot.worker, claimpilot.pipeline.process\n"
        "assert (list(root.handlers), root.level) == before, 'import changed the root logger'\n"
        "assert not any(getattr(h, '_claimpilot_handler', False) for h in root.handlers)\n"
        "assert logging.getLogger('uvicorn').propagate is True\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, timeout=120, check=False
    )
    assert done.returncode == 0, done.stderr


def test_creating_the_app_does_not_configure_logging():
    from claimpilot.main import create_app

    before = (list(logging.getLogger().handlers), logging.getLogger().level)

    create_app()

    assert (list(logging.getLogger().handlers), logging.getLogger().level) == before
    assert ours() == []
