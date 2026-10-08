# claimpilot-mcp-finance

The **mock finance / reimbursement system** ClaimPilot submits claims to. It is a real MCP server
(official Python `mcp` SDK, **streamable HTTP**), so the chat agent talks to it exactly as it would
to a corporate system. Synthetic data only; there is **no authentication**, so never expose it
publicly (compose publishes it on `127.0.0.1` only).

| | |
|---|---|
| Endpoint | `http://localhost:8101/mcp` (compose: `http://mcp-finance:8101/mcp`) |
| Health | `GET /healthz` (liveness), `GET /readyz` (database reachable) |
| State | SQLite file (`FINANCE_DB`), so claims survive a restart |

## Run

```bash
cd services/mcp-finance
uv sync
uv run python -m claimpilot_mcp_finance                      # state is kept in memory
FINANCE_DB=.data/finance.db uv run python -m claimpilot_mcp_finance   # state persists
# PowerShell: $env:FINANCE_DB = ".data/finance.db"; uv run python -m claimpilot_mcp_finance
```

With Docker (from the repo root): `docker compose -f infra/compose.yml up --build -d --wait mcp-finance`.
The image keeps the database in the `finance-data` volume (`/data/finance.db`).

| Variable | Default | |
|---|---|---|
| `FINANCE_DB` | `:memory:` | SQLite path (`/data/finance.db` in the image) |
| `MCP_HOST` | `127.0.0.1` | bind address (`0.0.0.0` in the image) |
| `MCP_PORT` | `8101` | |
| `LOG_LEVEL` | `info` | |

## Tools

| Tool | Arguments | Result |
|---|---|---|
| `submit_claim` | `claim_id, employee_id, title, total, currency, document_ids[], idempotency_key` | `{reference, status: "received", received_at, duplicate}` |
| `get_claim_status` | `reference` | claim + `history[]` (`status, at, actor, comment`) |
| `list_claims` | `status?, employee_id?` | `{result: [claim summary]}` in submission order (the approver queue) |
| `start_review` | `reference, reviewer_id` | claim (`received` -> `under_review`) |
| `decide_claim` | `reference, decision: approved\|rejected, approver_id, comment?` | claim |

* **Idempotent submission.** The same `idempotency_key` with the same claim returns the original
  reference with `duplicate: true` (document order does not matter). The same key with a different
  claim is an error that names the existing reference.
* **References** are `FIN-<year>-<6-digit counter>`, e.g. `FIN-2026-000123`; the counter is
  deterministic and survives restarts.
* **Status machine:** `received -> under_review -> approved | rejected`. `decide_claim` on a
  `received` claim passes through `under_review` first (both steps are in the history). Decisions are
  final, and rejecting needs a `comment`. Anything else is a readable tool error.
* Tool failures the caller can fix (unknown reference, invalid transition, key reuse, bad argument)
  come back as `is_error` results with a plain message; anything unexpected is a generic error and
  the details stay in the server log.
* `decide_claim`, `start_review` and `list_claims` are approver actions: do not offer them to the
  employee-facing chat agent (`claimpilot.mcp.EMPLOYEE_AGENT_TOOLS` is the allow-list).

## Design notes

* Stateless streamable HTTP with plain JSON responses: a restart never strands a client on a dead
  session, and `curl` works:
  `curl -X POST localhost:8101/mcp -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'`.
* Bound to loopback the server rejects foreign `Host` headers (DNS-rebinding protection); in a
  container (`MCP_HOST=0.0.0.0`) it accepts the compose service name.
* `store.py` holds all rules and knows nothing about MCP; `server.py` adapts it to tools.

## Tests

```bash
uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest --cov
```

Unit tests cover the store (idempotency, transitions, persistence, concurrency) and the tool
surface; integration tests start the server on a free port and drive it with the real MCP client.
Coverage gate: 90%.
