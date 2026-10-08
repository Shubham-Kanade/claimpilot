# Demo video script (3 to 4 minutes)

Goal: show a judge, in under four minutes, that ClaimPilot does the whole job (not a toy), catches
fraud, asks almost nothing, and is engineered properly. Record at 1920×1080, browser zoom 110%,
the hosted app or `docker compose up`. Upload the video to YouTube as **Public or Unlisted** (never
Private) and put the link in the README and the design document.

Before recording: open the app, press **Start over** (clean state), keep persona *Asha Menon*.
For the Jev segment run the stack locally with `DECISION_ENGINE=jev` and a key in `.env`.

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
| 2:35 | Switch persona to *Ravi Iyer*; approvals queue | "The approver sees a risk-ranked queue with the evidence. Clean small claims need no review; the flagged ones do. Rejecting needs a reason." |
| 2:55 | Impact page | "Documents, claims, what the AI cost, minutes saved." |
| 3:05 | Architecture slide (README diagram) | "Under the hood: Claude reads, Jev decides, deterministic code checks and routes. The mock finance and corporate systems are real MCP servers. Models are chosen by a bake-off on cost, accuracy and speed: Haiku 5.5 reads a receipt for about five hundredths of a cent." |
| 3:25 | Test and metrics slide | "More than sixteen hundred tests at ninety-eight percent coverage, a typed contract between backend and frontend, CI on every push, and a design document with every decision and its evidence." |
| 3:40 | Live-demo and repo links | "Try it yourself, no login: link below." |

Tips: keep the mouse slow; pause on each flagged receipt for two seconds; do not read numbers aloud
that are on screen. If a take goes wrong, **Start over** resets the demo in one click.
