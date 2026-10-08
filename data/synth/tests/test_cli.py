from __future__ import annotations

import argparse

import pytest
from claimpilot.domain import DocType

from generate import DATASET_VERSION, build_parser, parse_doc_types


def test_parse_doc_types_with_aliases() -> None:
    parsed = parse_doc_types("thermal_bill, upi,hotel_folio")
    assert parsed == {DocType.restaurant_bill, DocType.upi_payment, DocType.hotel_folio}
    assert parse_doc_types(None) is None
    assert parse_doc_types("") is None


def test_parse_doc_types_rejects_unknown() -> None:
    with pytest.raises(argparse.ArgumentTypeError, match="unknown doc type"):
        parse_doc_types("restaurant_bill,boarding_pass")


def test_parser_defaults() -> None:
    args = build_parser().parse_args(["all"])
    assert (args.count, args.seed, args.browser, args.only) == (100, 42, "auto", None)
    assert args.out.name == "out"


def test_dataset_version_is_set() -> None:
    assert DATASET_VERSION.startswith("synth-")
