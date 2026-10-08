---
name: add-mcp-tool
description: Add or change a tool or resource on the mock enterprise MCP servers (mcp-finance, mcp-corp) or the ClaimPilot MCP server, and wire it into the chat agent. Use when the agent needs a new capability from a "corporate system".
---
# Add an MCP tool

The servers use the official Python `mcp` SDK (FastMCP style) over streamable HTTP. Each one lives in `services/mcp-<name>/`.

1. **Define the tool** in `services/mcp-<name>/src/.../server.py`:
   - typed parameters with docstrings (these become the tool description and schema the model sees)
   - deterministic mock behaviour backed by a JSON or SQLite seed in `seed/`
   - Read-only data such as the policy document goes in as an MCP **resource**, not a tool.
2. **Side-effecting tools** (`submit_claim`, etc.) require an `idempotency_key` parameter and return a stable reference ID.
3. **Bridge it into the agent:** `claimpilot.chat.mcp_bridge` discovers the tools from the configured MCP servers (`MCP_SERVERS` env) and exposes them to the Tool Runner as strict tools.
   - Any tool listed in `APPROVAL_REQUIRED` (in `chat/policy.py`) is blocked until the user confirms.
4. **Tests:**
   - server unit tests (call the tool function directly)
   - an integration test that starts the server in-process and lists and calls tools through an MCP client
   - an agent trajectory test that asserts the tool is used, and that approval-gated tools are never called before confirmation
5. **Docs:** add the tool to the table in `docs/ARCHITECTURE.md` if it is user-visible.
