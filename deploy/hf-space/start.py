"""Process supervisor for the single-container demo.

Starts the two mock MCP servers, applies the database migrations, then the API, the web server and
the reverse proxy, each only after the one before it accepts connections. When any of them dies the
supervisor stops the rest and exits non-zero, so the host restarts the whole container (a restart is
also what resets the demo). Standard library only: it runs before anything else is installed.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
import socket
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(os.environ.get("CLAIMPILOT_ROOT", "/app"))
READY_TIMEOUT_S = 90.0


@dataclass(frozen=True)
class Service:
    name: str
    command: list[str]
    port: int  # the port that must accept connections before the next service starts
    cwd: Path = ROOT
    env: dict[str, str] = field(default_factory=dict)


def venv_python(service: str) -> str:
    return str(ROOT / service / ".venv" / "bin" / "python")


def services() -> list[Service]:
    return [
        Service("mcp-finance", [venv_python("mcp-finance"), "-m", "claimpilot_mcp_finance"], 8101),
        Service("mcp-corp", [venv_python("mcp-corp"), "-m", "claimpilot_mcp_corp"], 8102),
        Service(
            "api",
            [venv_python("api"), "-m", "uvicorn", "claimpilot.main:app"]
            + ["--host", "127.0.0.1", "--port", "8000", "--no-access-log"],
            8000,
            cwd=ROOT / "api",
            env={"LOG_FORMAT": "json"},  # one JSON line per event; the app logs each request itself
        ),
        Service(
            "web",
            ["node", "server.js"],
            3000,
            cwd=ROOT / "web",
            env={"PORT": "3000", "HOSTNAME": "127.0.0.1", "NODE_ENV": "production"},
        ),
        Service("proxy", ["caddy", "run", "--config", str(ROOT / "Caddyfile")], 7860),
    ]


def migrate() -> None:
    """Create or upgrade the SQLite schema before the API opens it."""
    subprocess.run(
        [venv_python("api"), "-m", "alembic", "upgrade", "head"], cwd=ROOT / "api", check=True
    )


def prepare_replay_dir() -> None:
    """Make the recordings writable when the demo records what it reads live.

    ``LLM_RECORD=1`` (the "hybrid" profile: recorded answers are replayed for free, anything new goes
    to the live model and is recorded) needs a writable recordings folder, but the image's own copy
    is read-only for the Space's unprivileged user. Work on a copy in /data (ephemeral, like the
    rest of the demo's state) and point the API at it.
    """
    if os.environ.get("LLM_RECORD", "").lower() not in {"1", "true", "yes", "on"}:
        return
    source = Path(os.environ.get("REPLAY_DIR", ROOT / "api" / "replay"))
    target = Path(os.environ.get("RECORDINGS_COPY", "/data/replay"))
    shutil.copytree(source, target, dirs_exist_ok=True)
    os.environ["REPLAY_DIR"] = str(target)
    print(f"[start] recording enabled: recordings copied to {target}", flush=True)


def accepts(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


async def wait_for(service: Service, process: asyncio.subprocess.Process) -> None:
    deadline = asyncio.get_running_loop().time() + READY_TIMEOUT_S
    while not accepts(service.port):
        if process.returncode is not None:
            raise RuntimeError(f"{service.name} exited with {process.returncode} while starting")
        if asyncio.get_running_loop().time() > deadline:
            raise RuntimeError(f"{service.name} did not open port {service.port}")
        await asyncio.sleep(0.25)
    print(f"[start] {service.name} is up on :{service.port}", flush=True)


async def stop(processes: list[asyncio.subprocess.Process]) -> None:
    for process in reversed(processes):
        if process.returncode is None:
            process.terminate()
    for process in processes:
        try:
            await asyncio.wait_for(process.wait(), timeout=10)
        except TimeoutError:
            process.kill()


async def run() -> int:
    migrate()
    prepare_replay_dir()
    processes: list[asyncio.subprocess.Process] = []
    loop = asyncio.get_running_loop()
    stopping = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stopping.set)
    try:
        for service in services():
            process = await asyncio.create_subprocess_exec(
                *service.command, cwd=service.cwd, env={**os.environ, **service.env}
            )
            processes.append(process)
            await wait_for(service, process)
        print("[start] ClaimPilot is ready", flush=True)
        watchers = [asyncio.ensure_future(p.wait()) for p in processes]
        stop_signal = asyncio.ensure_future(stopping.wait())
        await asyncio.wait([*watchers, stop_signal], return_when=asyncio.FIRST_COMPLETED)
        if stopping.is_set():
            return 0  # asked to stop (the host is shutting the container down)
        died = [
            s.name for s, p in zip(services(), processes, strict=True) if p.returncode is not None
        ]
        print(f"[start] {', '.join(died)} exited; stopping the container", file=sys.stderr)
        return 1  # a service must never exit on its own: let the host restart everything
    except RuntimeError as exc:
        print(f"[start] {exc}", file=sys.stderr, flush=True)
        return 1
    finally:
        await stop(processes)


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
