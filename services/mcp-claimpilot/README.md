# claimpilot-mcp

**ClaimPilot itself as an MCP server.** The repo already *consumes* MCP (`mcp-finance`, `mcp-corp`);
this is the other half: a person opens Claude Desktop (or any MCP client), points it at ClaimPilot,
and files, tracks and (as an approver) decides expense claims in conversation. Official Python
`mcp` SDK, two transports, a thin adapter over the public REST API. Synthetic data only; the demo
has no login, so the server acts as one **demo persona** and must never be exposed publicly.

| | |
|---|---|
| Transports | **stdio** (default, what Claude Desktop launches) and **streamable HTTP** at `http://127.0.0.1:8103/mcp` |
| Talks to | the ClaimPilot REST API (`CLAIMPILOT_API_URL`, default `http://localhost:8000`) as `CLAIMPILOT_PERSONA` (default `DEMO-ASHA`) |
| Health (HTTP only) | `GET /healthz` (process up), `GET /readyz` (the API reports ready) |
| State | none: every call goes to the API |

It imports nothing from `services/api`. All business rules stay in the API: who may see a claim,
the explicit-confirmation gate, idempotent submission, separation of duties. This server translates
and tidies; it never decides.

## Use it

**1. Start ClaimPilot** (either gives you an API):

```bash
docker compose -f infra/compose.yml up --build -d --wait api     # the full stack: API on http://localhost:8000
docker run -d -p 7860:7860 claimpilot-demo                       # the hosted-demo image (deploy/hf-space): API on http://localhost:7860/api
```

**2. Install once** (so the first launch from Claude Desktop is instant):

```bash
cd services/mcp-claimpilot && uv sync
```

**3. Add it to Claude Desktop** (`claude_desktop_config.json`: Settings, Developer, Edit config).
`--project` (rather than `--directory`) leaves the working directory alone; either way, tell Claude
the receipts' full paths.

```json
{
  "mcpServers": {
    "claimpilot": {
      "command": "uv",
      "args": [
        "run",
        "--project",
        "C:/path/to/claimpilot/services/mcp-claimpilot",
        "claimpilot-mcp"
      ],
      "env": {
        "CLAIMPILOT_API_URL": "http://localhost:8000",
        "CLAIMPILOT_PERSONA": "DEMO-ASHA"
      }
    }
  }
}
```

If Claude Desktop cannot find `uv` (a GUI app may have a shorter `PATH`), use the full path of
`uv.exe` as `command`, or skip `uv` and run the installed script:
`C:/path/to/claimpilot/services/mcp-claimpilot/.venv/Scripts/claimpilot-mcp.exe` (Linux/macOS:
`.venv/bin/claimpilot-mcp`). Restart Claude Desktop; the tools appear in its tools menu and the
prompt `file_expenses` in the attachment menu.

**Claude Code** (one line; the name comes first, `--` ends the options):

```bash
claude mcp add claimpilot -e CLAIMPILOT_API_URL=http://localhost:8000 -e CLAIMPILOT_PERSONA=DEMO-ASHA -- uv run --project /path/to/claimpilot/services/mcp-claimpilot claimpilot-mcp
```

**HTTP mode** (for Claude Code, the MCP Inspector or a containerised setup; Claude Desktop's
config file launches stdio servers only). No files are read unless you allow a folder:

```bash
uv run claimpilot-mcp --transport http --port 8103       # http://127.0.0.1:8103/mcp
claude mcp add --transport http claimpilot http://127.0.0.1:8103/mcp
curl -s localhost:8103/readyz                            # {"status":"ok","checks":{"api":"ok"}}
```

With Docker (from the repo root): `docker compose -f infra/compose.yml --profile mcp up --build -d --wait mcp-claimpilot`
publishes `127.0.0.1:8103` and talks to the `api` service. To let `upload_receipts` read a folder,
mount it read-only and set `CLAIMPILOT_UPLOAD_ROOT` (see the table below).

### Configuration

| Variable | Default | |
|---|---|---|
| `CLAIMPILOT_API_URL` | `http://localhost:8000` | the API, with any path prefix (`http://localhost:7860/api` for the demo image) |
| `CLAIMPILOT_PERSONA` | `DEMO-ASHA` | who to act as (sent as `X-Persona`); `DEMO-RAVI` is the approver |
| `CLAIMPILOT_UPLOAD_ROOT` | unset | the only folder `upload_receipts` may read; unset = any absolute path over stdio, nothing over HTTP |
| `CLAIMPILOT_MAX_FILES` / `CLAIMPILOT_MAX_FILE_MB` | `30` / `15` | upload limits; the API's own defaults (it enforces them, `/v1/meta` does not publish them) |
| `CLAIMPILOT_TIMEOUT_S` / `CLAIMPILOT_UPLOAD_TIMEOUT_S` | `30` / `120` | per request |
| `MCP_HOST` / `MCP_PORT` | `127.0.0.1` / `8103` | HTTP bind (`--host`, `--port` override; the image sets `0.0.0.0`) |
| `LOG_LEVEL` | `info` | HTTP server log level |

A bad value stops the server with a message that names the variable and never prints its value.

## Tools

| Tool | Arguments | What it does (REST call) | Hints |
|---|---|---|---|
| `list_claims` | `status?` | your claims, newest first, as summaries (`GET /v1/claims`) | read-only |
| `get_claim` | `claim_id` | one claim in full: status, route, total, findings (severity, message, the policy clause), open and answered questions, documents with what was read from each (`GET /v1/claims/{id}` + `GET /v1/documents/{id}` per document) | read-only |
| `upload_receipts` | `paths[]` | reads local files (JPEG, PNG, WebP, PDF; 30 files of 15 MB), uploads them as **one** batch (`POST /v1/batches`, multipart), returns the batch id | not idempotent |
| `get_batch` | `batch_id`, `wait_seconds=15` | progress, failed documents and, once finished, the claims formed; waits up to `wait_seconds` so the model need not poll in a loop (`GET /v1/batches/{id}`) | read-only |
| `answer_question` | `claim_id`, `text` | the human's reply to the open question(s); returns what the assistant understood, what is still open and the one message to ask next (`POST /v1/claims/{id}/reply`) | idempotent |
| `submit_claim` | `claim_id`, `confirmed=false` | **false: nothing is sent**, you get a summary to show the human. **true:** submits (`POST /v1/claims/{id}/submit`, `Idempotency-Key` derived from the claim id) | idempotent |
| `list_approvals` | `status="submitted"` | approvers: claims waiting for a decision, or decided ones (`GET /v1/me`, then `GET /v1/approvals`) | read-only |
| `decide_claim` | `claim_id`, `approve`, `comment=""` | approvers: approve or reject; rejecting needs a comment (`GET /v1/me`, then `POST /v1/claims/{id}/decision`) | final (destructive hint) |

Results are small typed objects (every tool publishes an output schema), never raw API payloads;
empty fields are left out. Each carries a fixed `next_step` sentence telling the model what to do
next. Besides tools there is one **prompt**, `file_expenses`, which walks a model through the whole
flow: upload, wait, review the findings, ask the single combined question, ask the human to
confirm, submit. There is **no policy resource**: the API publishes no policy endpoint (and this
server invents none). Each finding carries its clause id and wording; the whole policy is a
resource (`policy://expense/v3`) and a tool (`get_policy`) of `mcp-corp`.

### Personas and approvers

There is no login in the demo: a persona is a synthetic employee id sent as `X-Persona`.
`DEMO-ASHA` files claims; `DEMO-RAVI` (grade L5) is the approver. The approver tools are **always
registered**; when the configured persona is not an approver they return a clear error
(`GET /v1/me` first, so nothing else is called). That is simpler to test and to explain than
registering tools per persona, and a persona's role can change on the API side without a restart.
The API still enforces everything: approvers only, never your own claim, a comment to reject, a
decision is final.

### The confirmation gate

`submit_claim(claim_id)` with `confirmed=false` (the default, also for a missing or false-like
value) only reads: it returns the claim in full with `state: needs_confirmation`, `submitted: false`
and a `next_step` telling the model to show it and ask the human. Only `confirmed=true` calls the
API's submit, with the header `Idempotency-Key: claimpilot-mcp-submit-<claim id>`, so a retry can
never file a claim twice (the API also keeps one finance key per claim). A claim that is not ready
returns `state: not_ready` and what is missing; a claim already submitted returns
`already_submitted`. Be clear about what this is: a protocol-level safeguard plus the API's own
`confirmed: true` requirement and your MCP client's tool-approval prompt. A server cannot prove a
human said yes; the tool descriptions, the server instructions and the prompt all say never to
send `true` without it.

### Text from receipts is data, never instructions

A receipt can print "approve this claim". Everything this server returns that did not originate
here (titles, merchants, file names, finding messages, questions, answers, errors) is cleaned
when the result is built: one line, no control or invisible characters, `<` and `>` replaced by
look-alike quotation marks (so nothing can forge or close a tag), and a length cap (`text.py`
mirrors `claimpilot.domain.text` in the API and is reimplemented, not imported). The fields that
can carry such text are named in the server instructions and in every tool description
(`title, city, merchant, filename, message, question, answer, follow_up, error`), and guidance
(`next_step`, `state`, `note`) is fixed text chosen by status that never contains any. Tests feed
hostile text through every tool and check where it can appear, and that every string field of every
result is cleaned.

### Errors

The API reports failures as RFC 9457 `application/problem+json` (`type`, `title`, optional
`detail`). They become one readable tool error, `<title>: <detail>` (for example `The claim is
not ready to submit: {"status": "needs_info", ...}`); FastAPI's own validation errors and plain
HTTP failures become a sentence too. A network failure is a friendly message naming the API origin (never its
credentials or path); a wrong-shaped answer is "the connector and the API may be out of date". The
persona header value is scrubbed from every message, and no error carries a stack trace, a
header or a response body.

### Files

`upload_receipts` is the one tool that touches the machine it runs on, so it is strict: only
JPEG, PNG, WebP and PDF (by extension *and* content: a renamed text file is refused), the API's
limits, no folders, no duplicates, absolute paths only (relative ones are resolved inside
`CLAIMPILOT_UPLOAD_ROOT` when set, and are refused otherwise), and nothing is read until every
file passes. Over HTTP the server is not your machine, so without `CLAIMPILOT_UPLOAD_ROOT` it
reads **nothing**. Over stdio, set `CLAIMPILOT_UPLOAD_ROOT` too if you want to confine what a model
can read. Only bare file names reach the API.

## Example

> **You:** I have receipts from this week in `C:/Receipts/week41`. File my expenses.
>
> *(Claude calls `upload_receipts` with the 15 files, then `get_batch` until `finished`)*
>
> **Claude:** I read all 15 receipts and formed 7 claims. Two are clean and would be auto-approved
> (the client dinner on 6 Oct, Rs 8,400, and your mobile bill, Rs 1,059.64). Five go to finance
> review: the Mumbai trip has an alcohol line (clause 6.1: not reimbursable) and a meals warning;
> one conveyance receipt has a printed total that does not match its items (830.96 against
> 330.96); a second copy of the dinner bill was flagged as a duplicate; and a cafe bill carries a
> note telling a reviewer to approve it, which ClaimPilot ignored and so do I. Two claims need an
> answer from you. First: what was the business purpose of the Mumbai trip?
>
> **You:** A client workshop with Kestrel Logistics.
>
> *(Claude calls `answer_question`; the claim becomes `ready`; then `submit_claim` with
> `confirmed=false`, which sends nothing)*
>
> **Claude:** Here is what I would submit: Mumbai trip 9-10 Oct 2026, Rs 12,678.31, 6 documents,
> routed to finance review because of the alcohol line. Nothing has been sent yet. Shall I submit
> it?
>
> **You:** Yes.
>
> *(Claude calls `submit_claim` with `confirmed=true`)*
>
> **Claude:** Submitted: FIN-2026-000002. An approver will decide next.

As `DEMO-RAVI`: `list_approvals` shows the queue, and `decide_claim` approves or rejects
(rejecting needs a reason). Your own claim is refused (separation of duties, enforced by the API).
`answer_question` may make the API call its model once, when several questions are open at the
same time; with a single open question the reply is the answer and no model is involved.

## Design notes

* `client.py` is the only module that uses the network and knows nothing about MCP; `server.py`
  adapts it to tools. `OPERATIONS` lists every REST call as the OpenAPI document spells it, and
  the client builds its requests from those entries.
* **Contract drift guard.** `tests/unit/test_contract.py` checks every call (method and path,
  path, query and header parameters, body kind, success status, response schema) and every response
  field the parse models read against the committed `apps/web/openapi.json`; mutation tests
  tamper with a copy the way an API change would and the checks must notice. The fake API used by
  the tool tests holds every request to the same document, so each tool test is a contract test
  too. If `/v1/meta` ever publishes the upload limits, a test fails: read them from there.
* Stateless streamable HTTP with plain JSON responses (as the other two servers): a restart never
  strands a client; `curl` works. Bound to loopback it rejects foreign `Host` headers
  (DNS-rebinding protection).
* The API connection pool opens on first use and closes with the server (the SDK lifespan).
* TLS verifies against the OS trust store on Windows and macOS (corporate proxies re-sign TLS), as
  the API does; verification is never off.

## Tests

```bash
uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest --cov
```

(On Windows run pytest with `-p no:faulthandler`.) The tool tests drive the real tool schemas
through the SDK's in-process client against a scripted fake API; integration tests run the HTTP
server on a free port and the real `python -m claimpilot_mcp` over stdio and HTTP. `test_docs.py`
checks this README (variables, defaults, tool table, config snippets) and the Dockerfile against
the code. Coverage gate: 90%.
