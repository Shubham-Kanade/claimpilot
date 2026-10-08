---
name: deploy
description: Build, push and deploy ClaimPilot, then smoke-test the deployment. Manual only.
disable-model-invocation: true
---
# Deploy

Hosting is decided in M4 (see ADR-006). Until then, "deploy" means the local production-like stack.

## Local production-like
```bash
docker compose -f infra/compose.yml -f infra/compose.prod.yml up --build -d
curl -fsS http://localhost:8000/readyz && curl -fsS http://localhost:3000/
```

## Remote (once hosting is chosen)
1. Make sure `main` is green in CI and the eval gates passed on the latest `evals.yml` run.
2. Tag: `git tag v0.X.Y && git push --tags`. `cd.yml` builds images and pushes them to GHCR (`ghcr.io/<user>/claimpilot-{api,web,mcp-finance,mcp-corp}`).
3. The CD job deploys to the chosen target (fill this in on M4) and runs the smoke test (`scripts/smoke.sh <base-url>`): healthz, readyz, one replay-mode upload → claim.
4. Reset the demo data: `uv run python -m claimpilot.scripts.reset_demo --env prod`.
5. Record the deployed URL and version in TASKS.md (done log).

**Never deploy with `LLM_MODE=live` and no spend limit.** Confirm that the Console workspace has a monthly cap first.
