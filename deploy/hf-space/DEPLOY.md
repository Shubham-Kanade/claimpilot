# Deploying the demo to a Hugging Face Space

The Space is built from two files in this folder (`Dockerfile`, `README.md`). The Dockerfile clones
the public GitHub repository at build time, so GitHub stays the single source of truth and the Space
needs no copy of the code. Nothing here costs money: the free CPU Space is enough, and the demo
answers from recordings, so there is no API key to configure.

## One-time setup (about 10 minutes)

1. Make sure the GitHub repository is public and up to date (the build clones its `main` branch).
2. On huggingface.co: **New** → **Space**. Name it (for example `claimpilot`), pick **Docker** as the
   SDK, **Blank** template, **CPU basic (free)**, visibility **Public**. Create it.
3. Add the two files to the Space repository, either in the web UI (**Files** → **Add file** →
   **Create a new file** / **Upload files**) or with git:
   ```bash
   git clone https://huggingface.co/spaces/<your-user>/claimpilot hf-claimpilot
   cp deploy/hf-space/Dockerfile deploy/hf-space/README.md hf-claimpilot/
   cd hf-claimpilot && git add . && git commit -m "ClaimPilot demo" && git push
   ```
   (If you use git, authenticate with an HF access token with *write* permission as the password.)
4. The Space builds automatically (about 5 to 10 minutes: it compiles the web app and installs the
   Python services). Watch **Logs**. When it shows *Running*, open the app URL
   `https://<your-user>-claimpilot.hf.space`.
5. Verify from your machine:
   ```bash
   uv run --project services/api python scripts/smoke.py --api https://<your-user>-claimpilot.hf.space/api
   ```
   It uploads the sample receipts through the public URL and drives them to an approved claim.

## Choosing the profile (recorded or hybrid)

One image, two ways to run it. Pick at hosting time; switching later is a change of Variables and
Secrets in the Space **Settings** plus a restart, with no rebuild. Decision record: ADR-036.

| | **Recorded** (default) | **Hybrid live** |
|---|---|---|
| What reviewers can read | the 15 sample receipts only (answers replayed from recordings) | the samples (free, from recordings) **and receipts they upload** (read live by Claude) |
| Cost to you | $0 | your Anthropic spend, capped per 24 hours |
| Repeatable for the video | yes, identical every time | samples yes; new receipts vary |
| Variables | none (the image sets `LLM_MODE=replay`) | `LLM_MODE=live`, `LLM_RECORD=1`, `DAILY_LLM_BUDGET_USD=1` |
| Secrets | none | `ANTHROPIC_API_KEY` |

Notes for the hybrid profile:
- Set the key only as a Space **Secret**, never in the Dockerfile or the repository.
- `DAILY_LLM_BUDGET_USD` caps live spend over any rolling 24 hours for **all** visitors together; when it is used up, uploads get a plain "budget used up" message while the samples keep working. A receipt costs about $0.003 (about $0.04 for the whole sample pile if nothing were recorded), so $1 is roughly 300 receipts.
- New recordings are saved to `/data/replay` (a copy of the image's recordings is made on start); the disk is ephemeral, so they vanish on restart. That is harmless.
- The banner, the upload box and the cost labels change by themselves (they read `/v1/meta`); the banner tells visitors that their own receipts go to Anthropic's API and asks for synthetic or non-personal receipts only.
- Jev (System One) is not used in either profile: its calls cannot be recorded (ADR-029), so the demo decides with Claude.
- The demo clock stays pinned to 12 Oct 2026 in both profiles (`DEMO_TODAY`).

## Updating

Push to GitHub, then in the Space open **Settings** → **Factory rebuild** (or push any change to the
Space repo). The build re-clones `main`. To pin a version, set the `REF` build argument in the
Dockerfile to a tag or commit.

## Notes

- Free Spaces sleep after about 48 hours without visitors and wake on the next visit (about a minute). **Open the Space yourself shortly before sharing it or submitting**, so a reviewer never meets a cold start.
- The Space repository holds **copies** of `Dockerfile` and `README.md`; changing `deploy/hf-space/Dockerfile` in GitHub does not change the Space until you copy the file over. `start.py`, the Caddyfile and everything else are cloned from GitHub at build time, so changes to those arrive with a factory rebuild.
- The container is reset on every restart: the SQLite database, uploads and the mock finance system
  live in `/data`, which is not persistent on the free tier. That is intentional for a demo.
- If the build fails at the `git clone` step, check that the repository is public and that `REF`
  exists. If it fails on `npm ci` or `uv sync --locked`, the lock files in the repo are out of date.
- To test the same image locally before deploying:
  `docker build -f deploy/hf-space/Dockerfile --build-arg SOURCE=fetch-local -t claimpilot-demo .`
  then `docker run --rm -p 7860:7860 claimpilot-demo` and open http://localhost:7860.
