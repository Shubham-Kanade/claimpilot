"""Adversarial documents for trust and policy checks.

Kinds (each becomes a tag on the truth):

- ``duplicate``: an existing base receipt re-photographed (new id, new crop/angle/degradation,
  ``duplicate_of`` set). Same printed content, so the receipt truth is identical.
- ``tampered``: the printed arithmetic does not add up (edited total, inflated GST or an inflated
  line). The truth records what is printed, so it fails reconciliation on purpose.
- ``injection``: a printed line addressed to an AI system; ``contains_instructions=True``.
- ``over_policy``: alcohol on a dinner bill, or a hotel night above Rs 10,000.
- ``missing_date``: no date (or time) printed; the truth date is None.

Fresh adversarial documents come from dedicated personas (index 900+) so they never collide
with scenario documents.
"""

from __future__ import annotations

import random
from collections.abc import Collection, Sequence

from claimpilot.domain import DocType, ExpenseCategory, ExtractedReceipt, LineItem, TaxBreakup

from synthgen.assemble import PHOTO_PRESETS, SCREEN_CAPTURE, assemble, base_tags, doc_id
from synthgen.builders import (
    BuildContext,
    build_default,
    build_handwritten_bill,
    build_hotel_folio,
    build_restaurant_bill,
)
from synthgen.catalog import INJECTION_LINES
from synthgen.geo import CITIES
from synthgen.gst import money, reconciles
from synthgen.personas import make_faker, make_persona
from synthgen.rng import derive_rng, derive_seed
from synthgen.spec import PDF_DOC_TYPES, DocSpec, Draft

KINDS = ("injection", "tampered", "duplicate", "over_policy", "missing_date")
ADVERSARIAL_PERSONA_OFFSET = 900
OVER_POLICY_HOTEL_TARIFF = (10_500, 18_000)
AWAY_DOC_TYPES = frozenset({DocType.hotel_folio, DocType.flight_ticket, DocType.train_ticket})

COMPATIBLE: dict[str, frozenset[DocType]] = {
    "injection": frozenset(DocType) - {DocType.other},
    "tampered": frozenset(
        {
            DocType.restaurant_bill,
            DocType.cab_receipt,
            DocType.hotel_folio,
            DocType.gst_invoice,
            DocType.handwritten_bill,
        }
    ),
    "duplicate": frozenset(DocType) - PDF_DOC_TYPES - {DocType.other},
    "over_policy": frozenset({DocType.restaurant_bill, DocType.hotel_folio}),
    "missing_date": frozenset(
        {DocType.restaurant_bill, DocType.handwritten_bill, DocType.fuel_slip, DocType.cab_receipt}
    ),
}


def _context(seed: int, index: int, doc_type: DocType) -> BuildContext:
    persona = make_persona(seed, ADVERSARIAL_PERSONA_OFFSET + index)
    rng = derive_rng(seed, "adversarial-doc", index)
    city = persona.base_city
    if doc_type in AWAY_DOC_TYPES:
        city = rng.choice([c for c in CITIES if c != persona.base_city])
    return BuildContext(
        rng=rng,
        fake=make_faker(seed, "adversarial-doc", index),
        persona=persona,
        city=city,
        day=persona.month.replace(day=rng.randint(3, 28)),
    )


# --- mutations -------------------------------------------------------------------------------


def inject_instruction(draft: Draft, rng: random.Random) -> Draft:
    draft.extras["injection_text"] = rng.choice(INJECTION_LINES)
    draft.receipt = draft.receipt.model_copy(update={"contains_instructions": True})
    return draft


def tamper(receipt: ExtractedReceipt, rng: random.Random) -> ExtractedReceipt:
    """Break the printed arithmetic in one of three ways typical of edited bills."""
    has_tax = any(v for v in (receipt.taxes.cgst, receipt.taxes.sgst, receipt.taxes.igst))
    ways = ["total_edited", "line_inflated", *(["gst_inflated"] if has_tax else [])]
    way = rng.choice(ways)
    whole_rupees = receipt.handwritten
    if way == "total_edited":
        bump = rng.choice((100, 500, 1000, 2000)) if whole_rupees else rng.choice((90, 450, 1000))
        tampered = receipt.model_copy(update={"total": money((receipt.total or 0) + bump)})
    elif way == "line_inflated":
        items = list(receipt.line_items)
        position = rng.randrange(len(items))
        item = items[position]
        items[position] = LineItem(
            description=item.description,
            quantity=item.quantity,
            unit_price=item.unit_price,
            amount=money(item.amount + rng.choice((100, 200, 500))),
        )
        tampered = receipt.model_copy(update={"line_items": items})
    else:
        taxes = receipt.taxes
        doubled = TaxBreakup(
            cgst=money(taxes.cgst * 2) if taxes.cgst else None,
            sgst=money(taxes.sgst * 2) if taxes.sgst else None,
            igst=money(taxes.igst * 2) if taxes.igst else None,
            cess=taxes.cess,
            gst_rate_percent=taxes.gst_rate_percent,
        )
        tampered = receipt.model_copy(update={"taxes": doubled})
    if reconciles(tampered):
        raise RuntimeError(f"tampering ({way}) did not break the arithmetic")
    return tampered


def remove_date(receipt: ExtractedReceipt) -> ExtractedReceipt:
    return receipt.model_copy(update={"date": None, "time": None})


def _fresh_draft(kind: str, doc_type: DocType, ctx: BuildContext) -> Draft:
    rng = ctx.rng
    if kind == "over_policy" and doc_type == DocType.restaurant_bill:
        return build_restaurant_bill(
            ctx,
            covers=rng.randint(2, 5),
            upscale=True,
            alcohol=True,
            category=ExpenseCategory.client_entertainment,
        )
    if kind == "over_policy":
        tariff = float(50 * round(rng.uniform(*OVER_POLICY_HOTEL_TARIFF) / 50))
        return build_hotel_folio(ctx, nights=rng.randint(1, 3), tariff=tariff)
    if kind == "tampered" and doc_type == DocType.handwritten_bill:
        return build_handwritten_bill(ctx, kind="kirana")  # auto slips have no line items
    return build_default(doc_type, ctx)


def _fresh(kind: str, doc_type: DocType, *, seed: int, index: int, new_id: str) -> DocSpec:
    ctx = _context(seed, index, doc_type)
    draft = _fresh_draft(kind, doc_type, ctx)
    if kind == "injection":
        draft = inject_instruction(draft, ctx.rng)
    elif kind == "tampered":
        draft.receipt = tamper(draft.receipt, ctx.rng)
    elif kind == "missing_date":
        draft.receipt = remove_date(draft.receipt)
    return assemble(draft, doc_id=new_id, seed=seed, persona_id=ctx.persona.id, extra_tags=[kind])


def make_duplicate(original: DocSpec, *, seed: int, new_id: str) -> DocSpec:
    """Re-capture of an existing receipt with a different look than the original."""
    rng = derive_rng(seed, "duplicate", new_id)
    if original.doc_type == DocType.upi_payment:
        preset = SCREEN_CAPTURE
    else:
        preset = rng.choice([p for p in PHOTO_PRESETS if p != original.degrade])
    receipt = original.truth.receipt
    truth = original.truth.model_copy(
        update={
            "id": new_id,
            "duplicate_of": original.id,
            "tags": [*base_tags(preset, receipt.languages, receipt.handwritten), "duplicate"],
        }
    )
    return original.model_copy(
        update={
            "truth": truth,
            "degrade": preset,
            "render_seed": derive_seed(seed, "render", new_id),
        }
    )


def generate_adversarial_specs(
    base_specs: Sequence[DocSpec],
    *,
    seed: int,
    count: int,
    first_index: int,
    only: Collection[DocType] | None = None,
) -> list[DocSpec]:
    """``count`` adversarial documents, cycling through the kinds that ``only`` allows."""
    allowed = frozenset(only) if only else frozenset(DocType) - {DocType.other}
    originals = [
        spec
        for spec in base_specs
        if spec.doc_type in COMPATIBLE["duplicate"] and not spec.truth.duplicate_of
    ]
    kinds = [
        kind for kind in KINDS if (originals if kind == "duplicate" else COMPATIBLE[kind] & allowed)
    ]
    if count and not kinds:
        raise ValueError("no adversarial kind is compatible with the selected doc types")
    pick_rng = derive_rng(seed, "adversarial-originals")
    duplicate_sources = pick_rng.sample(originals, len(originals))

    specs: list[DocSpec] = []
    for index in range(count):
        kind = kinds[index % len(kinds)]
        new_id = doc_id(seed, first_index + index)
        if kind == "duplicate":
            source = duplicate_sources[(index // len(kinds)) % len(duplicate_sources)]
            specs.append(make_duplicate(source, seed=seed, new_id=new_id))
            continue
        types = sorted(COMPATIBLE[kind] & allowed)
        doc_type = derive_rng(seed, "adversarial-type", index).choice(types)
        specs.append(_fresh(kind, doc_type, seed=seed, index=index, new_id=new_id))
    return specs
