"""The truth must describe what is printed: check the rendered HTML text (no browser needed)."""

from __future__ import annotations

import html
import re

import pytest
from claimpilot.domain import ExtractedReceipt

from synthgen.catalog import INJECTION_LINES
from synthgen.render import make_environment, render_html
from synthgen.spec import DocSpec
from synthgen.textfmt import format_date, format_time, indian_grouping

TAGS = re.compile(r"<[^>]+>")
STYLE = re.compile(r"<style>.*?</style>", re.S)
DATE_LIKE = re.compile(r"\b\d{1,2}[/.-](\d{1,2}|[A-Z][a-z]{2})[/.-]\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b")


@pytest.fixture(scope="module")
def env():
    return make_environment()


def printed_text(spec: DocSpec, env) -> str:
    markup = STYLE.sub("", render_html(spec, env))
    return " ".join(html.unescape(TAGS.sub(" ", markup)).split())


def amount_printed(amount: float, text: str) -> bool:
    variants = {indian_grouping(amount), f"{amount:.2f}"}
    if amount == int(amount):
        variants.add(f"{int(amount)}/-")
    return any(variant in text for variant in variants)


def assert_receipt_printed(receipt: ExtractedReceipt, text: str, spec_id: str) -> None:
    for value in (
        receipt.merchant_name,
        receipt.merchant_gstin,
        receipt.merchant_city,
        receipt.invoice_number,
        receipt.upi_reference,
        receipt.travel_from,
        receipt.travel_to,
    ):
        if value:
            assert value in text, f"{spec_id}: {value!r} not printed"
    for item in receipt.line_items:
        assert item.description in text, f"{spec_id}: {item.description!r} not printed"
        assert amount_printed(item.amount, text), f"{spec_id}: line amount {item.amount}"
    for amount in (
        receipt.total,
        receipt.subtotal,
        receipt.service_charge,
        receipt.discount,
        receipt.taxes.cgst,
        receipt.taxes.sgst,
        receipt.taxes.igst,
    ):
        if amount is not None:
            assert amount_printed(amount, text), f"{spec_id}: amount {amount} not printed"


def test_every_truth_field_is_printed(all_specs: list[DocSpec], env) -> None:
    for spec in all_specs:
        assert_receipt_printed(spec.truth.receipt, printed_text(spec, env), spec.id)


def test_injection_text_printed_only_when_flagged(all_specs: list[DocSpec], env) -> None:
    for spec in all_specs:
        text = printed_text(spec, env)
        printed = [line for line in INJECTION_LINES if line in text]
        if spec.truth.receipt.contains_instructions:
            assert printed == [spec.extras["injection_text"]], spec.id
        else:
            assert not printed, spec.id


def test_missing_date_documents_print_no_date(all_specs: list[DocSpec], env) -> None:
    missing = [spec for spec in all_specs if "missing_date" in spec.truth.tags]
    assert missing
    for spec in missing:
        assert not DATE_LIKE.search(printed_text(spec, env)), spec.id


def test_date_and_time_printed_in_the_spec_format(all_specs: list[DocSpec], env) -> None:
    for spec in all_specs:
        receipt, text = spec.truth.receipt, printed_text(spec, env)
        if receipt.date:
            assert format_date(receipt.date, spec.style["date_style"]) in text, spec.id
        if receipt.time:
            assert format_time(receipt.time, spec.style.get("twelve_hour", False)) in text
