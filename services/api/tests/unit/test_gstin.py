from __future__ import annotations

import random

import pytest

from claimpilot.domain.gstin import (
    STATE_CODES,
    checksum_char,
    generate_gstin,
    is_valid_gstin,
    state_of,
    validate_gstin,
)

# Widely published sample GSTIN (Maharashtra) used in GST documentation examples.
KNOWN_VALID = "27AAPFU0939F1ZV"


def test_known_sample_is_valid():
    assert validate_gstin(KNOWN_VALID) == (True, None)


def test_normalises_case_and_whitespace():
    assert is_valid_gstin(f"  {KNOWN_VALID.lower()} ")


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ("27AAPFU0939F1ZW", "checksum"),  # last char altered
        ("27AAPFU0939F1Z", "format"),  # too short
        ("27AAPFU0939F1XV", "format"),  # 14th char must be 'Z'
        ("2AAAPFU0939F1ZV", "format"),
        ("99AAPFU0939F1ZV", "state_code"),
        ("", "format"),
    ],
)
def test_invalid_gstins(value, reason):
    assert validate_gstin(value) == (False, reason)


def test_single_char_typo_is_always_caught():
    # the checksum must reject any single substitution in the PAN part
    for i in range(2, 12):
        for ch in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            if ch == KNOWN_VALID[i]:
                continue
            mutated = KNOWN_VALID[:i] + ch + KNOWN_VALID[i + 1 :]
            if validate_gstin(mutated)[1] == "format":
                continue  # broke the format instead (e.g. letter where a digit belongs)
            assert not is_valid_gstin(mutated), mutated


def test_generated_gstins_are_valid_and_keep_state():
    rng = random.Random(42)
    for code in STATE_CODES:
        gstin = generate_gstin(code, rng)
        assert is_valid_gstin(gstin), gstin
        assert gstin[:2] == code
        assert state_of(gstin) == STATE_CODES[code]


def test_generation_is_deterministic_with_seed():
    assert generate_gstin("29", random.Random(7)) == generate_gstin("29", random.Random(7))


def test_generate_rejects_unknown_state():
    with pytest.raises(ValueError):
        generate_gstin("99")


def test_checksum_input_validation():
    with pytest.raises(ValueError):
        checksum_char("short")
    with pytest.raises(ValueError):
        checksum_char("27aapfu0939f1z")  # lowercase not allowed at this level


def test_state_of_unknown():
    assert state_of("99XXXXX0000X1Z0") is None
