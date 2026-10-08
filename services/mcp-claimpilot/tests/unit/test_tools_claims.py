"""Reading and filling in claims through the SDK client: list, get, upload, batch, answer.

The REST side is ``FakeApi``, which also holds every request to the committed OpenAPI document.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from mcp.server.mcpserver import MCPServer

from claimpilot_mcp.client import ClaimPilotClient
from claimpilot_mcp.server import (
    MAX_DOCUMENTS,
    create_server,
)
from claimpilot_mcp.settings import Settings
from tests import factories as f
from tests.conftest import Sleeper
from tests.fakeapi import Call, FakeApi
from tests.helpers import JPEG, PDF, call, data, error_of, script_claim

# -- list_claims ---------------------------------------------------------------------------------


async def test_list_claims_summarises_each_claim(api: FakeApi, server: MCPServer):
    api.json("GET", "/v1/claims", [f.claim(), f.submitted_claim(id="clm-2", title="Mobile Oct")])
    result = data(await call(server, "list_claims"))
    assert result["count"] == 2
    first, second = result["claims"]
    assert first == {
        "claim_id": f.CLAIM_ID,
        "employee_id": f.PERSONA,
        "title": "Mumbai trip 9-10 Oct 2026",
        "mode": "trip",
        "status": "needs_info",
        "route": "finance_review",
        "total": 12678.31,
        "currency": "INR",
        "start_date": "2026-10-09",
        "end_date": "2026-10-10",
        "document_count": 2,
        "open_question_count": 1,
        "high_findings": 0,
        "warnings": 1,
        "flags": ["meals_over_limit"],
    }
    assert second["submission_reference"] == "FIN-2026-000001"
    assert "note" not in result  # compact: absent fields are left out


async def test_list_claims_passes_the_status_filter_to_the_api(api: FakeApi, server: MCPServer):
    api.json("GET", "/v1/claims", [])
    result = data(await call(server, "list_claims", status="needs_info"))
    assert result == {"count": 0, "claims": []}
    assert api.calls[0].query == {"status": "needs_info"}


async def test_list_claims_rejects_a_status_the_api_does_not_have(api: FakeApi, server: MCPServer):
    result = await call(server, "list_claims", status="bogus")
    assert "status" in error_of(result)
    assert api.calls == []


async def test_a_long_list_is_cut_with_a_note(api: FakeApi, server: MCPServer):
    api.json("GET", "/v1/claims", [f.claim(id=f"clm-{i}") for i in range(60)])
    result = data(await call(server, "list_claims"))
    assert (result["count"], len(result["claims"])) == (60, 50)
    assert result["note"] == "Showing the first 50 of 60; use the status filter to narrow."


async def test_flags_list_each_serious_finding_once_most_serious_first(
    api: FakeApi, server: MCPServer
):
    findings = [
        f.finding(code="late_submission", severity="info"),
        f.finding(code="meals_over_limit", severity="warn"),
        f.finding(code="duplicate_image", severity="high"),
        f.finding(code="meals_over_limit", severity="warn", document_id="other"),
    ]
    api.json("GET", "/v1/claims", [f.claim(findings=findings)])
    (summary,) = data(await call(server, "list_claims"))["claims"]
    assert summary["flags"] == ["duplicate_image", "meals_over_limit"]
    assert (summary["high_findings"], summary["warnings"]) == (1, 2)


# -- get_claim -----------------------------------------------------------------------------------


async def test_get_claim_returns_everything_a_person_needs_to_review_it(
    api: FakeApi, server: MCPServer
):
    findings = [
        f.finding(code="meals_over_limit", severity="warn"),
        f.finding(
            code="alcohol_not_reimbursable",
            severity="high",
            message="This bill includes alcohol (2 lines, 1,540).",
            clause_id="6.1",
            clause_text="Alcohol is never reimbursed.",
        ),
    ]
    answered = f.question(id="q-attendees", kind="attendees", text="Who attended?", answer="Neha")
    script_claim(api, f.claim(findings=findings, open_questions=[f.question(), answered]))
    claim = data(await call(server, "get_claim", claim_id=f.CLAIM_ID))

    assert (claim["title"], claim["total"], claim["status"], claim["route"]) == (
        "Mumbai trip 9-10 Oct 2026",
        12678.31,
        "needs_info",
        "finance_review",
    )
    assert [fd["severity"] for fd in claim["findings"]] == ["high", "warn"]  # worst first
    assert claim["findings"][0] == {
        "severity": "high",
        "code": "alcohol_not_reimbursable",
        "message": "This bill includes alcohol (2 lines, 1,540).",
        "clause_id": "6.1",
        "clause_text": "Alcohol is never reimbursed.",
        "document_id": f.DOC_IDS[0],
    }
    assert claim["open_questions"] == [
        {
            "id": "q-business_purpose-1bde8f60",
            "kind": "business_purpose",
            "question": "What was the business purpose of the Mumbai trip 9-10 Oct 2026?",
        }
    ]
    assert claim["answered_questions"] == [
        {"id": "q-attendees", "kind": "attendees", "question": "Who attended?", "answer": "Neha"}
    ]
    assert claim["open_question_count"] == 1
    assert claim["city"] == "Mumbai"
    assert claim["next_step"].startswith("Questions are open")


async def test_get_claim_lists_its_documents_with_what_was_read_from_them(
    api: FakeApi, server: MCPServer
):
    script_claim(api)
    claim = data(await call(server, "get_claim", claim_id=f.CLAIM_ID))
    assert [d["document_id"] for d in claim["documents"]] == list(f.DOC_IDS)
    assert claim["documents"][0] == {
        "document_id": f.DOC_IDS[0],
        "filename": "03-cab-mumbai-station-to-hotel.png",
        "status": "processed",
        "doc_type": "cab_receipt",
        "category": "local_conveyance",
        "merchant": "Chai Point Express Cabs",
        "date": "2026-10-09",
        "total": 293.16,
        "currency": "INR",
        "trust_verdict": "ok",
    }
    assert sorted(
        c.args["document_id"] for c in api.calls_to("GET", "/v1/documents/{document_id}")
    ) == (sorted(f.DOC_IDS))


async def test_a_document_that_cannot_be_read_is_listed_by_id_and_the_claim_still_loads(
    api: FakeApi, server: MCPServer
):
    api.json("GET", "/v1/claims/{claim_id}", f.claim())

    def one_fails(call: Call) -> httpx.Response:
        if call.args["document_id"] == f.DOC_IDS[1]:
            return f.problem(404, "document_not_found", "No such document")
        return httpx.Response(200, json=f.document(call.args["document_id"]))

    api.respond("GET", "/v1/documents/{document_id}", one_fails)
    claim = data(await call(server, "get_claim", claim_id=f.CLAIM_ID))
    assert claim["documents"][0]["merchant"] == "Chai Point Express Cabs"
    assert claim["documents"][1] == {"document_id": f.DOC_IDS[1]}


async def test_a_document_that_failed_processing_shows_its_error(api: FakeApi, server: MCPServer):
    api.json("GET", "/v1/claims/{claim_id}", f.claim(document_ids=["d1"]))
    broken = f.document("d1", status="failed", error="unsupported file type", document=None)
    api.json("GET", "/v1/documents/{document_id}", broken)
    (document,) = data(await call(server, "get_claim", claim_id=f.CLAIM_ID))["documents"]
    assert document["status"] == "failed"
    assert document["error"] == "unsupported file type"
    assert "merchant" not in document


async def test_only_the_first_documents_are_looked_up_one_by_one(api: FakeApi, server: MCPServer):
    ids = [f"doc-{i:03d}" for i in range(MAX_DOCUMENTS + 2)]
    script_claim(api, f.claim(document_ids=ids))
    claim = data(await call(server, "get_claim", claim_id=f.CLAIM_ID))
    assert [d["document_id"] for d in claim["documents"]] == ids
    assert len(api.calls_to("GET", "/v1/documents/{document_id}")) == MAX_DOCUMENTS
    assert claim["documents"][-1] == {"document_id": ids[-1]}  # beyond the cap: id only
    assert claim["document_count"] == MAX_DOCUMENTS + 2


async def test_a_claim_without_documents_has_an_empty_list(api: FakeApi, server: MCPServer):
    script_claim(api, f.claim(document_ids=[], status="draft"))
    claim = data(await call(server, "get_claim", claim_id=f.CLAIM_ID))
    assert claim["documents"] == []
    assert claim["next_step"] == "Show the human where the claim stands."


@pytest.mark.parametrize(
    ("status", "start"),
    [
        ("needs_info", "Questions are open"),
        ("ready", "Complete."),
        ("submitted", "Submitted and waiting"),
        ("approved", "Approved."),
        ("rejected", "Rejected by the approver"),
    ],
)
async def test_the_next_step_follows_the_status(
    api: FakeApi, server: MCPServer, status: str, start: str
):
    script_claim(api, f.ready_claim(status=status))
    assert data(await call(server, "get_claim", claim_id=f.CLAIM_ID))["next_step"].startswith(start)


async def test_an_unknown_claim_is_the_apis_own_message(api: FakeApi, server: MCPServer):
    api.respond(
        "GET", "/v1/claims/{claim_id}", lambda _: f.problem(404, "claim_not_found", "No such claim")
    )
    assert error_of(await call(server, "get_claim", claim_id="clm-nope")).endswith("No such claim")


@pytest.mark.parametrize(
    "bad_id", ["", "../admin", "a/b", "a b", "-lead", "x" * 101, "id\n1", "a?b"]
)
async def test_ids_that_could_change_the_route_never_reach_the_api(
    api: FakeApi, server: MCPServer, bad_id: str
):
    result = await call(server, "get_claim", claim_id=bad_id)
    assert result.is_error
    assert api.calls == []


# -- upload_receipts -----------------------------------------------------------------------------


async def test_upload_receipts_sends_the_files_as_one_batch(
    api: FakeApi, server: MCPServer, tmp_path: Path
):
    (tmp_path / "taxi.jpg").write_bytes(JPEG)
    (tmp_path / "hotel.pdf").write_bytes(PDF)
    api.json("POST", "/v1/batches", f.batch_created(["taxi.jpg", "hotel.pdf"]), status=202)
    result = data(
        await call(
            server,
            "upload_receipts",
            paths=[str(tmp_path / "taxi.jpg"), str(tmp_path / "hotel.pdf")],
        )
    )
    (post,) = api.calls
    assert [(p.name, p.filename, p.content_type, p.data) for p in post.parts] == [
        ("files", "taxi.jpg", "image/jpeg", JPEG),
        ("files", "hotel.pdf", "application/pdf", PDF),
    ]
    assert str(tmp_path) not in str(post.parts)  # only bare file names leave this machine
    assert result["batch_id"] == f.BATCH_ID
    assert result["status"] == "queued"
    assert result["files"] == [
        {"document_id": "doc-0", "filename": "taxi.jpg"},
        {"document_id": "doc-1", "filename": "hotel.pdf"},
    ]
    assert "get_batch" in result["next_step"]


async def test_a_list_given_as_a_json_string_still_works(
    api: FakeApi, server: MCPServer, tmp_path: Path
):
    """Claude Desktop tends to pass lists as JSON text; the SDK parses it."""
    (tmp_path / "taxi.jpg").write_bytes(JPEG)
    api.json("POST", "/v1/batches", f.batch_created(["taxi.jpg"]), status=202)
    quoted = str(tmp_path / "taxi.jpg").replace("\\", "/")
    result = await call(server, "upload_receipts", paths=f'["{quoted}"]')
    assert data(result)["batch_id"] == f.BATCH_ID


async def test_a_bad_file_stops_everything_before_the_api_is_called(
    api: FakeApi, server: MCPServer, tmp_path: Path
):
    (tmp_path / "taxi.jpg").write_bytes(JPEG)
    (tmp_path / "notes.txt").write_bytes(b"hello")
    result = await call(
        server,
        "upload_receipts",
        paths=[str(tmp_path / "taxi.jpg"), str(tmp_path / "notes.txt"), str(tmp_path / "nope.jpg")],
    )
    message = error_of(result)
    assert "Nothing was uploaded." in message
    assert "notes.txt: only JPEG, PNG, WebP and PDF" in message
    assert "nope.jpg: file not found" in message
    assert api.calls == []


async def test_the_limits_come_from_the_settings(
    api: FakeApi, settings: Settings, client: ClaimPilotClient, tmp_path: Path
):
    server = create_server(
        settings.model_copy(update={"claimpilot_max_files": 1}), client, allow_any_path=True
    )
    for name in ("a.jpg", "b.jpg"):
        (tmp_path / name).write_bytes(JPEG)
    result = await call(
        server, "upload_receipts", paths=[str(tmp_path / "a.jpg"), str(tmp_path / "b.jpg")]
    )
    assert "At most 1 files per upload, got 2." in error_of(result)
    assert api.calls == []


async def test_an_empty_list_of_paths_is_rejected_by_the_schema(api: FakeApi, server: MCPServer):
    assert (await call(server, "upload_receipts", paths=[])).is_error
    assert api.calls == []


async def test_over_http_files_are_only_read_from_the_upload_folder(
    api: FakeApi, settings: Settings, client: ClaimPilotClient, tmp_path: Path
):
    inside = tmp_path / "receipts"
    inside.mkdir()
    (inside / "taxi.jpg").write_bytes(JPEG)
    (tmp_path / "private.jpg").write_bytes(JPEG)
    api.json("POST", "/v1/batches", f.batch_created(["taxi.jpg"]), status=202)

    closed = create_server(settings, client, allow_any_path=False)
    assert "CLAIMPILOT_UPLOAD_ROOT" in error_of(
        await call(closed, "upload_receipts", paths=[str(inside / "taxi.jpg")])
    )
    assert api.calls == []

    rooted = settings.model_copy(update={"claimpilot_upload_root": inside})
    server = create_server(rooted, client, allow_any_path=False)
    assert data(await call(server, "upload_receipts", paths=["taxi.jpg"]))["batch_id"]
    outside = await call(server, "upload_receipts", paths=[str(tmp_path / "private.jpg")])
    assert "outside the folder" in error_of(outside)
    assert len(api.calls) == 1  # only the first upload reached the API


async def test_the_apis_own_refusals_are_passed_on(api: FakeApi, server: MCPServer, tmp_path: Path):
    (tmp_path / "taxi.jpg").write_bytes(JPEG)
    api.respond(
        "POST",
        "/v1/batches",
        lambda _: f.problem(
            422,
            "unsupported_files",
            "Some files are not JPEG, PNG, WebP or PDF",
            {"files": [{"filename": "taxi.jpg", "reason": "unsupported file type"}]},
        ),
    )
    message = error_of(await call(server, "upload_receipts", paths=[str(tmp_path / "taxi.jpg")]))
    assert message.startswith("Error executing tool upload_receipts: Some files are not JPEG")
    assert "unsupported file type" in message


async def test_a_batch_the_api_finds_too_large_is_reported(
    api: FakeApi, server: MCPServer, tmp_path: Path
):
    (tmp_path / "taxi.jpg").write_bytes(JPEG)
    api.respond(
        "POST",
        "/v1/batches",
        lambda _: f.problem(413, "too_many_files", "At most 30 files per upload"),
    )
    result = await call(server, "upload_receipts", paths=[str(tmp_path / "taxi.jpg")])
    assert "At most 30 files per upload" in error_of(result)


# -- get_batch -----------------------------------------------------------------------------------


async def test_a_finished_batch_returns_its_claims_without_waiting(
    api: FakeApi, server: MCPServer, sleeper: Sleeper
):
    api.json("GET", "/v1/batches/{batch_id}", f.batch())
    result = data(await call(server, "get_batch", batch_id=f.BATCH_ID))
    assert (result["status"], result["finished"]) == ("done", True)
    assert (result["total"], result["processed"], result["failed"]) == (2, 2, 0)
    assert result["failed_documents"] == []
    assert [c["claim_id"] for c in result["claims"]] == [f.CLAIM_ID]
    assert result["next_step"].startswith("Processing is done.")
    assert len(api.calls) == 1
    assert sleeper.calls == []


async def test_get_batch_waits_for_processing_to_finish(
    api: FakeApi, server: MCPServer, sleeper: Sleeper
):
    working = f.batch(status="processing", processed=0, claims=[], finished_at=None)
    halfway = f.batch(status="processing", processed=1, claims=[])
    api.sequence("GET", "/v1/batches/{batch_id}", [working, halfway, f.batch()])
    result = data(await call(server, "get_batch", batch_id=f.BATCH_ID))
    assert result["finished"] is True
    assert len(result["claims"]) == 1
    assert len(api.calls) == 3
    assert sleeper.calls == [1.0, 1.0]


async def test_get_batch_gives_up_waiting_after_wait_seconds(
    api: FakeApi, server: MCPServer, sleeper: Sleeper
):
    api.json("GET", "/v1/batches/{batch_id}", f.batch(status="processing", processed=1, claims=[]))
    result = data(await call(server, "get_batch", batch_id=f.BATCH_ID, wait_seconds=3))
    assert (result["status"], result["finished"], result["processed"]) == ("processing", False, 1)
    assert result["next_step"] == "Still processing. Call get_batch again to keep waiting."
    assert len(api.calls) == 4  # the first look, then one per second
    assert sleeper.calls == [1.0, 1.0, 1.0]


async def test_wait_seconds_zero_looks_once(api: FakeApi, server: MCPServer, sleeper: Sleeper):
    api.json("GET", "/v1/batches/{batch_id}", f.batch(status="queued", processed=0, claims=[]))
    result = data(await call(server, "get_batch", batch_id=f.BATCH_ID, wait_seconds=0))
    assert result["finished"] is False
    assert len(api.calls) == 1
    assert sleeper.calls == []


@pytest.mark.parametrize("seconds", [-1, 31])
async def test_wait_seconds_outside_its_range_is_rejected(
    api: FakeApi, server: MCPServer, seconds: int
):
    assert (await call(server, "get_batch", batch_id=f.BATCH_ID, wait_seconds=seconds)).is_error
    assert api.calls == []


async def test_documents_that_failed_are_named_with_their_reason(api: FakeApi, server: MCPServer):
    broken = f.document(
        "d3", filename="blurry.jpg", status="failed", error="unreadable", document=None
    )
    api.json(
        "GET",
        "/v1/batches/{batch_id}",
        f.batch(documents=[f.document(), broken], failed=1, processed=2),
    )
    result = data(await call(server, "get_batch", batch_id=f.BATCH_ID))
    assert result["failed_documents"] == [{"filename": "blurry.jpg", "error": "unreadable"}]
    assert result["failed"] == 1


async def test_a_failed_batch_says_so(api: FakeApi, server: MCPServer):
    api.json(
        "GET",
        "/v1/batches/{batch_id}",
        f.batch(status="failed", error="the worker crashed", claims=[], documents=[]),
    )
    result = data(await call(server, "get_batch", batch_id=f.BATCH_ID))
    assert (result["status"], result["finished"]) == ("failed", True)
    assert result["error"] == "the worker crashed"
    assert result["next_step"].startswith("Processing failed.")


async def test_a_finished_batch_with_no_claims_points_at_the_failures(
    api: FakeApi, server: MCPServer
):
    api.json("GET", "/v1/batches/{batch_id}", f.batch(claims=[]))
    result = data(await call(server, "get_batch", batch_id=f.BATCH_ID))
    assert result["next_step"].startswith("Processing finished but no claims were formed.")


async def test_an_unknown_batch_is_the_apis_own_message(api: FakeApi, server: MCPServer):
    api.respond(
        "GET",
        "/v1/batches/{batch_id}",
        lambda _: f.problem(404, "batch_not_found", "No such batch"),
    )
    assert error_of(await call(server, "get_batch", batch_id="b-1")).endswith("No such batch")


# -- answer_question -----------------------------------------------------------------------------


async def test_answer_question_returns_what_was_understood_and_that_the_claim_is_ready(
    api: FakeApi, server: MCPServer
):
    api.json("POST", "/v1/claims/{claim_id}/reply", f.reply())
    result = data(
        await call(
            server,
            "answer_question",
            claim_id=f.CLAIM_ID,
            text="Client workshop with Kestrel Logistics",
        )
    )
    assert api.calls[0].body == {"text": "Client workshop with Kestrel Logistics"}
    assert result["understood"] == [
        {
            "question_id": "q-business_purpose-1bde8f60",
            "question": "What was the business purpose of the Mumbai trip 9-10 Oct 2026?",
            "answer": "Client workshop with Kestrel Logistics",
        }
    ]
    assert result["open_questions"] == []
    assert (result["status"], result["ready_to_submit"]) == ("ready", True)
    assert "follow_up" not in result
    assert result["next_step"].startswith("Everything is answered.")


async def test_answer_question_returns_what_is_still_open_and_the_one_message_to_ask(
    api: FakeApi, server: MCPServer
):
    attendees = f.question(id="q-attendees", kind="attendees", text="Who attended?")
    still = f.claim(open_questions=[attendees, f.question(answer="Workshop")])
    api.json(
        "POST",
        "/v1/claims/{claim_id}/reply",
        f.reply(
            claim_payload=still,
            understood={"q-business_purpose-1bde8f60": "Workshop"},
            follow_up="I still need one detail:\n1. Who attended?",
        ),
    )
    result = data(await call(server, "answer_question", claim_id=f.CLAIM_ID, text="Workshop"))
    assert result["open_questions"] == [
        {"id": "q-attendees", "kind": "attendees", "question": "Who attended?"}
    ]
    assert result["follow_up"] == "I still need one detail: 1. Who attended?"  # one line
    assert result["ready_to_submit"] is False
    assert result["status"] == "needs_info"
    assert result["next_step"].startswith("Ask the human what follow_up asks")


async def test_a_reply_that_answered_nothing_says_so(api: FakeApi, server: MCPServer):
    api.json(
        "POST",
        "/v1/claims/{claim_id}/reply",
        f.reply(claim_payload=f.claim(), understood={}, follow_up="1. What was the purpose?"),
    )
    result = data(await call(server, "answer_question", claim_id=f.CLAIM_ID, text="hmm"))
    assert result["understood"] == []
    assert len(result["open_questions"]) == 1


async def test_a_reply_to_a_claim_with_nothing_open_gives_the_guidance_for_its_status(
    api: FakeApi, server: MCPServer
):
    api.json(
        "POST",
        "/v1/claims/{claim_id}/reply",
        f.reply(claim_payload=f.submitted_claim(), understood={}, follow_up=None),
    )
    result = data(await call(server, "answer_question", claim_id=f.CLAIM_ID, text="anything"))
    assert (result["status"], result["ready_to_submit"]) == ("submitted", False)
    assert result["next_step"].startswith("Submitted and waiting for an approver")


async def test_a_locked_claim_cannot_be_answered(api: FakeApi, server: MCPServer):
    api.respond(
        "POST",
        "/v1/claims/{claim_id}/reply",
        lambda _: f.problem(409, "claim_locked", "a submitted claim can no longer be changed"),
    )
    result = await call(server, "answer_question", claim_id=f.CLAIM_ID, text="Workshop")
    assert "a submitted claim can no longer be changed" in error_of(result)


async def test_only_the_owner_can_answer(api: FakeApi, server: MCPServer):
    api.respond(
        "POST",
        "/v1/claims/{claim_id}/reply",
        lambda _: f.problem(403, "not_your_claim", "Only the owner can answer"),
    )
    result = await call(server, "answer_question", claim_id=f.CLAIM_ID, text="Workshop")
    assert error_of(result).endswith("Only the owner can answer")


@pytest.mark.parametrize("text", ["", "x" * 2001])
async def test_an_answer_outside_the_apis_length_limits_is_rejected_early(
    api: FakeApi, server: MCPServer, text: str
):
    assert (await call(server, "answer_question", claim_id=f.CLAIM_ID, text=text)).is_error
    assert api.calls == []


async def test_a_blank_answer_is_the_apis_to_refuse(api: FakeApi, server: MCPServer):
    api.respond(
        "POST",
        "/v1/claims/{claim_id}/reply",
        lambda _: f.problem(422, "blank_answer", "An answer cannot be blank"),
    )
    result = await call(server, "answer_question", claim_id=f.CLAIM_ID, text="   ")
    assert error_of(result).endswith("An answer cannot be blank")
