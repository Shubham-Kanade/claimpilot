"""End-to-end smoke run against a live stack (``docker compose up``): the demo, as a script.

Uploads a pile of receipts as a demo employee, follows the live progress stream, answers whatever
is asked, submits with explicit confirmation, and has the approver decide. Exits non-zero at the
first broken step, so it doubles as the post-deploy check.

    uv run --project services/api python scripts/smoke.py [--api URL] [--dir PATH] [--persona ID]

Every run works in its own demo sandbox (a fresh random id, ``--sandbox`` to pin one), so it is
safe to repeat against a stack that already holds data and never disturbs anyone else's.

This spends real money when the stack runs with ``LLM_MODE=live`` (see infra/compose.record.yml);
with ``LLM_MODE=replay`` and recorded responses it is free.
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
import time
from pathlib import Path
from typing import Any

import httpx

REPO = Path(__file__).resolve().parents[1]
PILES = (REPO / "data" / "synth" / "demo" / "docs", REPO / "data" / "synth" / "fixtures" / "docs")
APPROVER = "DEMO-RAVI"
MEDIA = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".pdf": "application/pdf",
}

# What a cooperative employee would say, by question kind.
ANSWERS = {
    "attendees": "Neha Rao and Rohan Kapoor (Kestrel Logistics)",
    "business_purpose": "Client visit and quarterly review with Kestrel Logistics",
    "missing_date": "2026-10-08",
    "confirm_personal": "No, this was a business expense",
    "self_declaration": "Auto fare to the client office, no receipt was available",
    "other": "Tea and snacks for a client meeting",
}


class Failure(SystemExit):
    def __init__(self, message: str) -> None:
        super().__init__(f"\nSMOKE FAILED: {message}")


def step(title: str) -> None:
    print(f"\n== {title}")


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise Failure(message)


def call(client: httpx.Client, method: str, path: str, *, ok: int = 200, **kw: Any) -> Any:
    response = client.request(method, path, **kw)
    if response.status_code != ok:
        raise Failure(
            f"{method} {path} gave {response.status_code}, expected {ok}: {response.text[:300]}"
        )
    return response.json() if response.content else None


def wait_ready(client: httpx.Client, seconds: float = 90.0) -> None:
    deadline = time.monotonic() + seconds
    while True:
        try:
            if client.get("/readyz").status_code == 200:
                return
        except httpx.HTTPError:
            pass
        expect(time.monotonic() < deadline, "the API did not become ready (GET /readyz)")
        time.sleep(2)


def follow(client: httpx.Client, batch_id: str, persona: dict[str, str], timeout: float) -> None:
    """Read the SSE stream to the end, printing one line per event."""
    started = time.monotonic()
    event, final = "", False
    with client.stream(
        "GET", f"/v1/batches/{batch_id}/events", headers=persona, timeout=timeout
    ) as stream:
        expect(stream.status_code == 200, f"events stream gave {stream.status_code}")
        for line in stream.iter_lines():
            if line.startswith("event:"):
                event = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data = json.loads(line.split(":", 1)[1])
                seen = f"{time.monotonic() - started:5.1f}s"
                if event == "document_extracted":
                    money = data["total"] if data["total"] is not None else "?"
                    print(
                        f"  {seen} read     {data['filename'][:34]:34} {data['doc_type']:16} "
                        f"{money!s:>9} {data['category']:20} ({data['category_confidence']:.2f}, "
                        f"{data['engine']}{', cached' if data['cached'] else ''})"
                    )
                elif event == "document_checked":
                    print(
                        f"  {seen} checked  {data['document_id'][:8]}  "
                        f"trust {data['trust_score']:3} {data['verdict']:7} "
                        f"findings {data['findings']}"
                    )
                elif event == "document_failed":
                    print(f"  {seen} FAILED   {data['filename']}: {data['error']}")
                else:
                    print(f"  {seen} {event}")
                if event in ("batch_done", "batch_failed"):
                    final = True
                    expect(event == "batch_done", f"the batch failed: {data.get('error')}")
                    break
    expect(final, "the event stream ended before the batch finished")


def show_claims(claims: list[dict[str, Any]]) -> None:
    for c in claims:
        flags = [f["code"] for f in c["findings"]]
        print(
            f"  {c['id'][:14]:14} {c['mode']:7} {c['status']:10} {c.get('route')!s:15} "
            f"{c['total']:>10,.2f}  {c['title'][:44]:44} questions {len(c['open_questions'])} "
            f"flags {flags}"
        )


def answer_all(client: httpx.Client, claims: list[dict[str, Any]], persona: dict[str, str]) -> None:
    for claim in claims:
        for _ in range(3):
            current = call(client, "GET", f"/v1/claims/{claim['id']}", headers=persona)
            open_ids = [q for q in current["open_questions"] if not q.get("answer")]
            if not open_ids:
                break
            prompt = call(client, "GET", f"/v1/claims/{claim['id']}/prompt", headers=persona)
            expect(prompt["prompt"], f"claim {claim['id']} has open questions but no prompt")
            print(f"\n  assistant (one message for {claim['title']}):")
            for text in prompt["prompt"].splitlines():
                print(f"    | {text}")
            answers = {q["id"]: ANSWERS.get(q["kind"], ANSWERS["other"]) for q in open_ids}
            done = call(
                client,
                "POST",
                f"/v1/claims/{claim['id']}/answers",
                json={"answers": answers},
                headers=persona,
            )
            print(f"  -> answered {len(answers)}; now {done['status']}, route {done['route']}")
        else:
            raise Failure(f"claim {claim['id']} still has open questions after three rounds")


def submit_all(
    client: httpx.Client, claims: list[dict[str, Any]], persona: dict[str, str]
) -> list[str]:
    submitted: list[str] = []
    for claim in claims:
        current = call(client, "GET", f"/v1/claims/{claim['id']}", headers=persona)
        expect(current["status"] == "ready", f"{claim['id']} is {current['status']}, not ready")
        path = f"/v1/claims/{claim['id']}/submit"
        call(client, "POST", path, ok=422, json={"confirmed": False}, headers=persona)  # the gate
        key = {**persona, "Idempotency-Key": f"smoke-{claim['id']}"}
        first = call(client, "POST", path, json={"confirmed": True}, headers=key)
        again = call(client, "POST", path, json={"confirmed": True}, headers=key)
        expect(first["status"] == "submitted", f"{claim['id']} was not submitted")
        expect(
            first["submission_reference"] == again["submission_reference"],
            "a repeated submission produced a second reference (not idempotent)",
        )
        print(f"  {claim['title'][:50]:50} -> {first['submission_reference']}")
        submitted.append(claim["id"])
    return submitted


def decide(client: httpx.Client, ids: list[str], sandbox: str) -> None:
    approver = {"X-Persona": APPROVER, "X-Sandbox": sandbox}
    queue = call(client, "GET", "/v1/approvals", headers=approver)
    queued = {c["id"] for c in queue}
    expect(set(ids) <= queued, f"approver queue misses claims: {sorted(set(ids) - queued)}")
    print(f"  approver queue: {len(queue)} claims awaiting a decision")
    if not ids:
        return
    ok = call(
        client, "POST", f"/v1/claims/{ids[0]}/decision", json={"approved": True}, headers=approver
    )
    expect(ok["status"] == "approved", "approval did not stick")
    print(f"  approved  {ids[0][:14]}")
    if len(ids) > 1:
        path = f"/v1/claims/{ids[1]}/decision"
        call(client, "POST", path, ok=422, json={"approved": False}, headers=approver)  # reason
        no = call(
            client,
            "POST",
            path,
            json={"approved": False, "comment": "Please attach the original invoice"},
            headers=approver,
        )
        expect(no["status"] == "rejected", "rejection did not stick")
        print(f"  rejected  {ids[1][:14]} (a reason is required)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument(
        "--dir", type=Path, default=next((p for p in PILES if p.is_dir()), PILES[-1])
    )
    parser.add_argument("--persona", default="DEMO-ASHA", help="employee id sent as X-Persona")
    parser.add_argument(
        "--sandbox",
        default=f"smoke-{secrets.token_hex(12)}",
        help="demo sandbox id sent as X-Sandbox (default: a fresh random one)",
    )
    parser.add_argument("--limit", type=int, default=0, help="upload only the first N files")
    parser.add_argument(
        "--timeout", type=float, default=300.0, help="seconds to wait for the batch"
    )
    parser.add_argument("--no-decide", action="store_true", help="stop after submission")
    args = parser.parse_args()

    files = sorted(p for p in args.dir.iterdir() if p.suffix.lower() in MEDIA)
    files = files[: args.limit] if args.limit else files
    expect(bool(files), f"no receipts found in {args.dir}")
    persona = {"X-Persona": args.persona, "X-Sandbox": args.sandbox}

    with httpx.Client(base_url=args.api, timeout=60) as client:
        step(f"API at {args.api}")
        wait_ready(client)
        meta = call(client, "GET", "/v1/meta")
        print(
            f"  llm mode {meta.get('llm_mode')}, routes {json.dumps(meta.get('routes', {}))[:200]}"
        )
        people = {e["id"] for e in call(client, "GET", "/v1/employees")}
        expect(
            {args.persona, APPROVER} <= people,
            f"unknown persona; the directory has {sorted(people)}",
        )

        step(f"Upload {len(files)} receipts from {args.dir} as {args.persona}")
        handles = [("files", (f.name, f.read_bytes(), MEDIA[f.suffix.lower()])) for f in files]
        batch = call(client, "POST", "/v1/batches", ok=202, files=handles, headers=persona)
        print(f"  batch {batch['batch_id']}")

        step("Live progress (server-sent events)")
        follow(client, batch["batch_id"], persona, args.timeout)
        view = call(client, "GET", f"/v1/batches/{batch['batch_id']}", headers=persona)
        failed = [d for d in view["documents"] if d["status"] == "failed"]
        expect(not failed, f"documents failed: {[(d['filename'], d['error']) for d in failed]}")
        flagged = [d for d in view["documents"] if d["verdict"] in ("review", "block")]
        print(
            f"  {len(view['documents'])} documents read, {len(flagged)} flagged by the trust checks"
        )
        for d in flagged:
            print(f"    flagged: {d['filename']} (trust {d['trust_score']}, {d['verdict']})")

        step(f"Claims ({len(view['claims'])})")
        expect(bool(view["claims"]), "the pile produced no claims")
        show_claims(view["claims"])

        step("Ask once, answer once")
        answer_all(client, view["claims"], persona)
        claims = call(client, "GET", "/v1/claims", headers=persona)
        mine = [c for c in claims if c["batch_id"] == batch["batch_id"]]
        show_claims(mine)

        step("Submit (explicit confirmation + idempotency key)")
        submitted = submit_all(client, mine, persona)

        if not args.no_decide:
            step("Approver decisions")
            decide(client, submitted, args.sandbox)

        step("Impact")
        stats = call(client, "GET", "/v1/stats", headers=persona)
        print(f"  {json.dumps(stats, indent=2)}")

    print("\nSMOKE OK")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
