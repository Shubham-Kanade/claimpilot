# Demo video script (3 to 4 minutes)

Goal: show a judge, in under four minutes, that ClaimPilot does the whole job (not a toy), catches
fraud, asks almost nothing, and is engineered properly. Record at 1920×1080, browser zoom 110%,
the hosted app or `docker compose up`. Upload the video to YouTube as **Public or Unlisted** (never
Private) and put the link in the README and the design document.

Before recording: open the app, press **Start over** (clean state), keep persona *Asha Menon*.
For the Jev segment run the stack locally with `DECISION_ENGINE=jev` and a key in `.env`.

## Two ways to make the video

**1. Let the capture tool drive the browser.** It plays the whole story below (upload, findings,
the one question, submit, the approver rejecting the trip with a reason and approving the clean
phone bill, impact), draws a caption on screen for each step and writes a 1920×1080 `.webm`. It
empties the demo first, so every run starts from the same pile.

```bash
docker build -f deploy/hf-space/Dockerfile --build-arg SOURCE=fetch-local -t claimpilot-demo .
docker run -d --name cpdemo -p 7860:7860 claimpilot-demo
cd apps/web && npm ci && node scripts/record-demo.mjs   # writes docs/demo/claimpilot-demo.webm
```

`--pace 1.4` slows every pause, `--base`/`--api` point it at another stack, `--shots <dir>` also
saves one screenshot per caption (a quick way to check a change without watching the video). On a
machine without Edge, `npx playwright install chromium` and set `BROWSER_CHANNEL=chromium`.
Record your own voice over it with the words in the table, or leave the captions to speak, then
upload the file to YouTube (Public or Unlisted).

**2. Record the screen yourself** and follow the table. Use this if you want to show the Jev
segment or the architecture and test slides in one take.

## Full-stack segments (what the hosted demo cannot show)

The hosted demo replays recordings, so it reads only its 15 sample receipts and never uses Jev. Record
these short segments from the real stack and cut them into the video (about 60 to 75 seconds in
all). Use your own machine and your own keys, with synthetic receipts only.

1. **A receipt the demo has never seen, read live.** Start the stack with live models:
   `cp .env.example .env`, set `ANTHROPIC_API_KEY`, `LLM_MODE=live`, optionally `JEV_API_KEY` and
   `DECISION_ENGINE=jev`, then `docker compose -f infra/compose.yml up --build`. Upload one
   receipt from `data/synth/fixtures` (not in the demo pile). Say: "This receipt was never
   recorded: Claude reads it now, for about a twentieth of a cent." Show the engine label (*Jev + LLM*
   when Jev is on) and the cost on the progress card.
2. **ClaimPilot inside Claude Desktop, over MCP.** Add the server from
   `services/mcp-claimpilot/README.md` (stdio config, `CLAIMPILOT_API_URL=http://localhost:8000`).
   Ask: "What expense claims do I have?", "What is wrong with the Mumbai trip?", answer the one
   question in chat, then ask it to submit: it asks you to confirm first. Switch the persona
   to the approver and ask for the approval queue. Say: "The same rules apply here: the API
   enforces them, not the chat."
3. **The system underneath.** Show `docker compose ps` (api, worker, Postgres, Redis, two MCP
   servers, web), the **AI ops** page (`/operations`: calls by model, live vs recorded, latency,
   failures) and a green CI run on GitHub. Say: "Every model call is traced to the receipt that
   caused it."

| Time | On screen | Say (about) |
|---|---|---|
| 0:00 | Upload screen, persona *Asha Menon* | "Asha just got back from a client trip. She has fifteen receipts: photos, PDFs, a UPI screenshot, a handwritten auto fare. Expense reports cost people hours and finance teams even more. ClaimPilot does it in about thirty seconds." |
| 0:20 | Press **Try with sample receipts** (or drag the folder). The live progress cards fill in | "Every receipt is read by Claude vision. A fast decision model called Jev, a System One model, assigns the category; if it is unsure, Claude takes over. You can see the engine, the category and a trust score for each, live." |
| 0:50 | Claims appear: Mumbai trip, client dinner, local conveyance, mobile and internet, meals | "It grouped the pile itself: the Mumbai trip (train, hotel, cab, dinners), the client dinner, a month of local rides, the phone bill." |
| 1:10 | Open the **duplicate** (the second Saffron Terrace photo) | "The same dinner bill, photographed twice. A perceptual hash finds candidates and the printed fields confirm it." |
| 1:20 | Open the **edited cab receipt** | "The total says 830 rupees; its own items and tax add up to 330. Edited totals can't hide." |
| 1:30 | Open the **café bill with the hidden note** | "This receipt has a line addressed to an AI reviewer: approve without checks. It is flagged as prompt injection and ignored; approvals depend only on rules, never on what a receipt says." |
| 1:45 | Open the **alcohol dinner**; show the policy clause | "Alcohol is never reimbursed. Every finding quotes the policy clause behind it: 6.1." |
| 2:00 | Trip claim shows ONE question; type a one-line reply | "It asks only what it cannot work out. The dinner needed nothing because the calendar already had who attended and why. For the trip, one question, answered in a sentence." |
| 2:20 | **Submit** dialog, confirm | "Nothing is submitted without an explicit confirmation. It goes to the finance system over MCP, and gets a reference." |
| 2:35 | Switch persona to *Ravi Iyer*; approvals queue; open the trip, type a reason, **Reject**; open the phone bill, **Approve** | "The approver sees a risk-ranked queue with the evidence. The trip has alcohol, so Ravi rejects it, and a reason is required because finance keeps it on record. The phone bill has no flags: one click." |
| 2:55 | Impact page | "Documents, claims, what the AI cost, minutes saved." |
| 3:05 | Architecture slide (README diagram) | "Under the hood: Claude reads, Jev decides, deterministic code checks and routes. The mock finance and corporate systems are real MCP servers. Models are chosen by a bake-off on cost, accuracy and speed: Haiku 5.5 reads a receipt for about five hundredths of a cent." |
| 3:25 | Test and metrics slide | "More than sixteen hundred tests at ninety-eight percent coverage, a typed contract between backend and frontend, CI on every push, and a design document with every decision and its evidence." |
| 3:40 | Live-demo and repo links | "Try it yourself, no login: link below." |

Tips: keep the mouse slow; pause on each flagged receipt for two seconds; do not read numbers aloud
that are on screen. If a take goes wrong, **Start over** resets the demo in one click.
