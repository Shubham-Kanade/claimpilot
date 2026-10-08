"""``python -m claimpilot_mcp_finance``: serve the finance MCP server over streamable HTTP."""

from __future__ import annotations

import uvicorn

from claimpilot_mcp_finance.server import create_app
from claimpilot_mcp_finance.settings import Settings
from claimpilot_mcp_finance.store import FinanceStore


def main() -> None:
    settings = Settings()
    store = FinanceStore(settings.finance_db)
    try:
        uvicorn.run(
            create_app(store, host=settings.mcp_host),
            host=settings.mcp_host,
            port=settings.mcp_port,
            log_level=settings.log_level.lower(),
        )
    finally:
        store.close()


if __name__ == "__main__":
    main()
