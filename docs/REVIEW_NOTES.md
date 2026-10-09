# Review notes (9 Oct 2026)

A quick pass over the finished build. This file lists issues only; nothing here has been fixed yet.
The severity is about the 12 Oct submission, not about production use.

## High: can cost us during judging
| # | Issue | Where | Suggested fix |
|---|---|---|---|
| 1 | **Visitors share one sandbox.** On the hosted demo, "Try with sample receipts" resets Asha's data before it uploads, and "Start over" as Ravi clears everyone's. Two judges using the Space at the same time will wipe each other's claims halfway through. | `api/demo.py`, `components/upload/UploadScreen.tsx`, ADR-030 | Give each browser its own persona copy (for example `DEMO-ASHA-<random>`, created on first visit) or scope demo data by a browser session id. At minimum, say so on the banner. |
| 2 | **CI has never run on the last ~40 commits.** Nothing is pushed, so the new Playwright job, the MCP matrix entry, the gitleaks config and the OpenAPI drift check have only run locally. | `.github/workflows/ci.yml` | Push today and fix whatever is red, leaving a day of slack before 12 Oct. |
| 3 | **The PDF of the design document was never looked at.** It was rebuilt, but I could not render it here to check that the Mermaid diagrams came out. | `docs/TECHNICAL_DESIGN.pdf` | Open it once and skim the diagrams before submitting. |

## Medium
| # | Issue | Where | Suggested fix |
|---|---|---|---|
| 4 | **Upload limits disagree.** The upload screen says 30 files and 15 MB per file. The hosted image allows 20 files and 6 MB (Dockerfile env and Caddy `max_size 130MB`). A 10 MB phone photo gets a friendly 413 error instead of instant feedback. | `apps/web/src/lib/upload/limits.ts`, `deploy/hf-space/Dockerfile`, `Caddyfile` | Publish `max_batch_files` and `max_upload_mb` in `GET /v1/meta` and have the UI read them. |
| 5 | **LLM calls cannot be traced back to a receipt.** `llm_calls` holds the route, model, tokens, cost, latency, error and request hash, but no `batch_id` or `document_id`. When a document fails you can see that a call failed, but not which document's call it was without matching timestamps. | `db/models.py` `LlmCall`, `llm/ledger.py` | Add `batch_id`, `document_id` and `trace_id` columns (a migration). Pass them through a context variable set in `process_batch`. |
| 6 | **No structured logs, no trace ids, no OpenTelemetry.** The plan named OpenTelemetry and Langfuse, but neither was built (the docs make no claim that they were). Logs are plain uvicorn output. | `main.py`, `wiring.py` | JSON logs with a request id and batch id. Optionally add an OTel GenAI span per LLM call, sent to Langfuse. |
| 7 | **The persona header is the only identity.** `X-Persona` is a plain header, so any caller can act as any employee or as the approver. This is acceptable for a synthetic demo and documented in TDD §8, but separation of duties holds only within the demo's honesty system. | `api/deps.py` | See the answer on authentication. Keep it, but make sure the TDD calls it a demo-only shortcut. |
| 8 | **Rare hydration error on `/batches/<id>`.** React error #418 appeared once on a cold first load while the machine was under heavy load. It did not reproduce in 30 further tries, and React recovers by rendering on the client. CI retries once. | `components/batch/BatchScreen.tsx`, `e2e/no-errors.spec.ts` | Watch CI. If it shows up there, render nothing data-dependent until `useHydrated()` is true on that screen. |

## Low
| # | Issue | Where |
|---|---|---|
| 9 | `data/synth/demo/README.md` says the pile makes 6 claims; the real stack makes 7 (the ₹120 UPI receipt becomes "Miscellaneous Oct 2026"). | `data/synth/demo/README.md` |
| 10 | OpenAPI marks `default_factory` fields as optional, and 401/403 responses are not declared, so the generated TypeScript types are looser than the API. | `services/api`, `apps/web/src/lib/api/schema.d.ts` |
| 11 | Category accuracy on the held-out set is 85% against a ≥ 90% target in TDD §1.6. The remainder becomes a question to the employee, which the TDD says, but a judge may read it as a missed target. | `docs/TECHNICAL_DESIGN.md` §1.6 |
| 12 | The free Space sleeps after about 48 hours without visitors, and the first visit then takes about a minute. Judges may open the link days after 12 Oct. | `deploy/hf-space/DEPLOY.md` |
| 13 | The MCP server's `submit_claim(confirmed=true)` trusts the model to have asked the human. Real protection is the MCP client's tool-approval prompt; the README says so. | `services/mcp-claimpilot` |
| 14 | The duplicate dinner forms its own claim, so the claims list shows two identical "Client dinner 6 Oct 2026" cards. The top-flag line tells them apart, but a "possible duplicate of …" link between them would read better. | `claims/` grouping, web claims list |

## Checked and fine
- **Secrets and challenge brief:** gitleaks over the full history finds no leaks (one allowlisted fake persona id in a test), and none of the challenge brief files are in history.
- **Hosted image from a clean clone of HEAD:** it builds, and `scripts/smoke.py` prints SMOKE OK.
- **Tests:**

  | Suite | Result |
  |---|---|
  | API | 1,742 tests, 98.7% coverage |
  | Web | 539 tests, 98% coverage |
  | Playwright | 100 passed, 9 skipped by design (phone-only on desktop), 1 failed (item 8) |
  | MCP servers | 86 + 130 + 350 tests |
  | Synthetic data generator | 111 tests |

- **Hosted demo spend:** it runs in replay mode with no API key, so a visitor can never spend money.
