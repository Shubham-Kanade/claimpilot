"""``claimpilot-mcp``: serve ClaimPilot over MCP, on stdio (default) or streamable HTTP.

    claimpilot-mcp                                  # stdio: what Claude Desktop launches
    claimpilot-mcp --transport http --port 8103     # streamable HTTP at http://127.0.0.1:8103/mcp

Everything else (the API to talk to, the persona, limits) comes from the environment: see
``settings.py``. Over stdio nothing but protocol messages may reach stdout, so errors go to stderr.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence

import uvicorn
from pydantic import ValidationError

from claimpilot_mcp import __version__
from claimpilot_mcp.client import ClaimPilotClient
from claimpilot_mcp.server import create_app, create_server
from claimpilot_mcp.settings import Settings


def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError:
        port = 0
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("must be a port number from 1 to 65535")
    return port


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="claimpilot-mcp",
        description="ClaimPilot as an MCP server: file, track and decide expense claims.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--transport",
        choices=("stdio", "http"),
        default="stdio",
        help="stdio (default, for Claude Desktop) or streamable HTTP at /mcp",
    )
    parser.add_argument("--host", help="HTTP bind address (default: MCP_HOST, else 127.0.0.1)")
    parser.add_argument("--port", type=_port, help="HTTP port (default: MCP_PORT, else 8103)")
    return parser


def _quiet_http_logs() -> None:
    """httpx logs one INFO line per request, URL included: noise in a client's server log."""
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


def _config_error(error: ValidationError) -> int:
    """Say what is wrong with the environment, never what the values were (one is a header)."""
    print("claimpilot-mcp: invalid configuration", file=sys.stderr)
    for item in error.errors(include_url=False, include_input=False, include_context=False):
        name = str(item["loc"][0]).upper() if item["loc"] else "SETTINGS"
        print(f"  {name}: {item['msg'].removeprefix('Value error, ')}", file=sys.stderr)
    return 2


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        settings = Settings()
    except ValidationError as exc:
        sys.exit(_config_error(exc))

    _quiet_http_logs()
    client = ClaimPilotClient.from_settings(settings)
    if args.transport == "stdio":
        # The server closes the client when the session ends (its lifespan).
        create_server(settings, client, allow_any_path=True).run("stdio")
        return
    host = args.host or settings.mcp_host
    port = args.port or settings.mcp_port
    uvicorn.run(
        create_app(settings, client, host=host),
        host=host,
        port=port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
