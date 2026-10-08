# Architecture

```mermaid
flowchart LR
  subgraph Client
    W[Next.js PWA<br/>drop zone · camera · chat · claim cards]
  end
  subgraph Backend["services/api (FastAPI modular monolith)"]
    API[REST + SSE]
    ING[ingest]
    EXT[extraction<br/>Claude vision → JSON]
    DEC[decisions<br/>DecisionEngine]
    POL[policy-as-code]
    TRU[trust score]
    CLM[claims state machine]
    CHAT[chat agent<br/>Tool Runner]
  end
  WK[Arq worker]
  R[(Redis<br/>queue · pub/sub · cache)]
  PG[(Postgres<br/>claims · audit · llm_calls)]
  S3[(MinIO / S3<br/>files)]
  C[Claude API<br/>System Two]
  J[Jev<br/>System One]
  MF[mcp-finance<br/>mock finance]
  MC[mcp-corp<br/>calendar · directory · policy]

  W -- REST/SSE --> API
  API --> ING --> R --> WK
  WK --> EXT --> C
  WK --> DEC --> J
  DEC -. fallback .-> C
  WK --> POL & TRU --> CLM --> PG
  ING --> S3
  CHAT --> C
  CHAT -- MCP --> MF & MC
  API --> CHAT
```

## Module boundaries (services/api/src/claimpilot)
| Module | Owns | Must not |
|---|---|---|
| `ingest` | upload validation, storage, job enqueue | call LLMs |
| `extraction` | image/PDF → `ExtractedReceipt` via the LLM layer, OCR boxes | make policy decisions |
| `decisions` | `DecisionEngine` protocol and the Jev/LLM adapters | know about HTTP |
| `policy` | rules compiled from the policy doc, evaluation with clause citations | call LLMs at evaluation time |
| `trust` | duplicates (pHash + fingerprint), arithmetic/GST checks, EXIF, C2PA, injection signals | mutate claims |
| `claims` | grouping, claim state machine (complete / missing / questionable), submission | parse documents |
| `chat` | conversational agent, tool bridge to MCP, approval gate | bypass `claims` for submission |
| `llm` | model registry, capability shim, cost ledger, replay mode | be imported by `policy` |

## Key flows
1. **Upload → claim:**
   1. `POST /v1/uploads` stores the files and enqueues a job.
   2. The worker extracts (with cache), decides (Jev/LLM), runs trust and policy, then groups.
   3. SSE events stream progress.
   4. The claim lands as `draft` with open questions.
2. **Clarify → submit:**
   1. The chat agent asks one combined question.
   2. The user answers and the claim moves to `ready`.
   3. The user confirms.
   4. `submit_claim` goes through the approval gate to `mcp-finance`.

## Cross-cutting
- **Config:** 12-factor env vars (`.env.example`). The model registry lives in `services/api/config/models.yaml`.
- **Observability:** OpenTelemetry (GenAI semantic conventions) → Langfuse. JSON logs.
- **Health:** `/healthz` (liveness) and `/readyz` (dependencies) on every service.
