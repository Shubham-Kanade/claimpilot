---
name: submission-check
description: Walk the AI Innovation Lab S2 submission checklist (missing any item means elimination). Covers a public repo with no secrets or brief files, the technical design document, YouTube demo link, hosted URL, CI and coverage gates, and README links. Use each evening and before the final submission on 12 Oct 2026.
---
# Submission check

The organisers said that missing **any** item below means elimination. Report each item as ✅, ❌ or ⚠️ with evidence. Never mark an item ✅ without checking it.

## 1. Code and repo
- [ ] The code is on the user's **personal GitHub** and the repo is **public**. Check logged-out: `curl -sI https://github.com/<user>/<repo>` gives 200.
- [ ] No secrets in the **whole history**: `gitleaks git --no-banner` (or the CI `secrets` job is green).
- [ ] No challenge brief files or internal documents anywhere in history:
  `git log --all --name-only --format= | grep -iE "business cases|email\.txt"` must print nothing.
- [ ] The latest CI run on `main` is green, including both coverage gates (api ≥ 85%, web ≥ 80% lines).

## 2. Fully functional
- [ ] The golden path works on the hosted URL (or `docker compose up` from a clean clone): upload receipts → draft claim → one question → confirm → submitted.
- [ ] The `docs/TASKS.md` ★ items for the current milestone are done. List any cut ★ items explicitly.

## 3. Technical design document (`docs/TECHNICAL_DESIGN.md`, plus a PDF export at M5)
All four parts the organisers require must be present and filled in, with no 🚧 left at M5:
- [ ] Project overview **including the track selected** (§1)
- [ ] System architecture and **flow diagrams** (§2; the Mermaid diagrams render on GitHub)
- [ ] **Setup and deployment instructions** (§5), tested from a clean clone
- [ ] **Code explanations and assumptions** (§6, §8)
- [ ] Testing results table filled with real numbers (§7)

## 4. Demo video
- [ ] A YouTube link exists and plays **while logged out**. Visibility is public or unlisted, never private.
- [ ] The video shows the key features and the UX: drop receipts → live extraction → trust flags (duplicate, AI-generated, injection) → policy citation → one question → submit → approver view. Length 3–4 minutes.
- [ ] The link is in the README and the TDD header.

## 5. Hosting (when hosted)
The demo is a Hugging Face Space built from `deploy/hf-space` (ADR-030).
- [ ] The Space URL loads **logged out**, with no login or access request (demo personas instead).
- [ ] `https://<space>.hf.space/api/readyz` is green and `uv run --project services/api python scripts/smoke.py --api https://<space>.hf.space/api` prints `SMOKE OK` (it replays the recordings: every model answer must hit).
- [ ] No API key is configured on the Space (replay only, so a visitor can never spend money); the Space is public.
- [ ] The Space's `Dockerfile` has `REF=main` (or the tag you submit) and the GitHub repo is public with that ref pushed.
- [ ] The URL is in the README and the TDD header.

## 6. Final submission
- [ ] The README top section links: TDD, demo video, hosted app, CI badge.
- [ ] Collect the links in what the user sends the organisers: GitHub repo URL, TDD link or PDF, YouTube URL, hosted URL. Draft that message for the user to send; don't send it.

Record the result in `docs/TASKS.md` (done log) with the date.
