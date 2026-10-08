from __future__ import annotations

from httpx import AsyncClient

from claimpilot.domain import DocType, ExpenseCategory, ExtractedReceipt
from claimpilot.domain.claims import Decisions, ProcessedDocument
from claimpilot.pipeline.repo import NewFile

from .conftest import ASHA, MEERA, PNG, RAVI, World, as_persona


async def seeded_document(world: World, *, employee: str = "P001") -> tuple[str, str]:
    key = "b/receipt.png"
    await world.container.storage.put(key, PNG)
    batch_id, (doc_id,) = await world.repo.create_batch(employee, [NewFile("r.png", "a" * 64, key)])
    processed = ProcessedDocument(
        id=doc_id,
        filename="r.png",
        sha256="a" * 64,
        receipt=ExtractedReceipt(
            doc_type=DocType.cab_receipt, total=320.0, merchant_name="Zip Cabs"
        ),
        decisions=Decisions(
            category=ExpenseCategory.local_conveyance,
            category_confidence=0.95,
            alcohol_present=0.0,
            personal_expense=0.0,
            engine="jev",
        ),
    )
    await world.repo.save_document(
        doc_id, processed, phash="ff00", fingerprint="fp", trust={"score": 95, "verdict": "clean"}
    )
    return batch_id, doc_id


async def test_document_detail(http: AsyncClient, world: World):
    _, doc_id = await seeded_document(world)
    body = (await http.get(f"/v1/documents/{doc_id}", headers=as_persona(ASHA))).json()
    assert body["status"] == "processed" and body["trust_score"] == 95
    assert body["document"]["receipt"]["merchant_name"] == "Zip Cabs"
    assert body["document"]["decisions"]["engine"] == "jev"


async def test_document_file_is_served_safely(http: AsyncClient, world: World):
    _, doc_id = await seeded_document(world)
    resp = await http.get(f"/v1/documents/{doc_id}/file", headers=as_persona(ASHA))
    assert resp.status_code == 200 and resp.content == PNG
    assert resp.headers["content-type"] == "image/png"  # sniffed from the bytes
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["cache-control"].startswith("private")


async def test_documents_are_private_but_approvers_can_see_them(http: AsyncClient, world: World):
    _, doc_id = await seeded_document(world)
    for url in (f"/v1/documents/{doc_id}", f"/v1/documents/{doc_id}/file"):
        assert (await http.get(url, headers=as_persona(MEERA))).status_code == 404
        assert (await http.get(url, headers=as_persona(RAVI))).status_code == 200
    missing = await http.get("/v1/documents/nope", headers=as_persona(ASHA))
    assert missing.status_code == 404 and missing.json()["type"] == "document_not_found"


async def test_document_file_gone_from_storage(http: AsyncClient, world: World):
    _, doc_id = await seeded_document(world)
    await world.container.storage.delete("b/receipt.png")
    resp = await http.get(f"/v1/documents/{doc_id}/file", headers=as_persona(ASHA))
    assert resp.status_code == 404 and resp.json()["type"] == "file_unavailable"


async def test_employee_list_needs_no_login(http: AsyncClient):
    resp = await http.get("/v1/employees")
    assert resp.status_code == 200
    assert {e["id"] for e in resp.json()} == {"P001", "P002", "DEMO-RAVI"}


async def test_me_reports_the_persona_and_approver_flag(http: AsyncClient):
    asha = (await http.get("/v1/me", headers=as_persona(ASHA))).json()
    assert asha["employee"]["name"] == "Asha Menon" and asha["is_approver"] is False
    ravi = (await http.get("/v1/me", headers=as_persona(RAVI))).json()
    assert ravi["is_approver"] is True
    assert (await http.get("/v1/me")).status_code == 401
