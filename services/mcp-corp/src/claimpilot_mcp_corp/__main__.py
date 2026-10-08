"""``python -m claimpilot_mcp_corp``: serve the corporate-systems MCP server (streamable HTTP)."""

from __future__ import annotations

import uvicorn

from claimpilot_mcp_corp.directory import Directory
from claimpilot_mcp_corp.policy import PolicyProvider
from claimpilot_mcp_corp.server import create_app
from claimpilot_mcp_corp.settings import Settings


def main() -> None:
    settings = Settings()
    directory = Directory.load(settings.seed_dir)
    policy = PolicyProvider(*settings.policy_candidates())
    uvicorn.run(
        create_app(directory, policy, host=settings.mcp_host),
        host=settings.mcp_host,
        port=settings.mcp_port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
