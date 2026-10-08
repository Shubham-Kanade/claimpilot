---
title: ClaimPilot
emoji: 🧾
colorFrom: indigo
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
short_description: AI expense & reimbursement agent - from a pile of receipts to ready-to-submit claims
---

# ClaimPilot (live demo)

An AI expense and reimbursement agent built for **AI Innovation Lab, Season 2** (Business Case 1).
Drop a pile of receipts and ClaimPilot reads them, checks them against a fictional expense policy,
catches duplicates, edited totals and prompt-injection text, groups them into claims, asks you only
what it cannot work out, and submits to a mocked finance system once you confirm.

**Try it:** open the app, keep the persona *Asha Menon*, press **Try with sample receipts**, answer
the one question, confirm the submission, then switch to *Ravi Iyer* to approve it. No login.

About this demo:
- All data is **synthetic**: the receipts, the people, the company and its policy are fictional.
- The sample receipts are read from **recorded model answers**, so this page costs nothing to run and
  behaves the same every time. To read your own receipts, run ClaimPilot locally with your own API key.
- It is a single-container build of the full system (web, API, two mock MCP servers, SQLite). The
  state is reset whenever the Space restarts, or when you press **Start over**.

Source, architecture and the technical design document: https://github.com/Shubham-Kanade/claimpilot
