"""GSTIN (Indian GST Identification Number) validation and generation.

Format (15 chars): 2-digit state code · 10-char PAN · entity no. (1-9, A-Z) · 'Z' · check char.
The check character is a mod-36 checksum over the first 14 characters (alternate weights 1, 2).
Shared by the API's trust checks and the synthetic receipt generator.
"""

from __future__ import annotations

import random
import re
import string

CHARSET = string.digits + string.ascii_uppercase  # value of a char = its index (0-35)

GSTIN_RE = re.compile(r"^(\d{2})([A-Z]{5}\d{4}[A-Z])([1-9A-Z])Z([0-9A-Z])$")

# GST state / UT codes (CBIC). 25 (Daman & Diu) merged into 26 in 2020; 28 is old Andhra Pradesh.
STATE_CODES: dict[str, str] = {
    "01": "Jammu and Kashmir",
    "02": "Himachal Pradesh",
    "03": "Punjab",
    "04": "Chandigarh",
    "05": "Uttarakhand",
    "06": "Haryana",
    "07": "Delhi",
    "08": "Rajasthan",
    "09": "Uttar Pradesh",
    "10": "Bihar",
    "11": "Sikkim",
    "12": "Arunachal Pradesh",
    "13": "Nagaland",
    "14": "Manipur",
    "15": "Mizoram",
    "16": "Tripura",
    "17": "Meghalaya",
    "18": "Assam",
    "19": "West Bengal",
    "20": "Jharkhand",
    "21": "Odisha",
    "22": "Chhattisgarh",
    "23": "Madhya Pradesh",
    "24": "Gujarat",
    "26": "Dadra and Nagar Haveli and Daman and Diu",
    "27": "Maharashtra",
    "29": "Karnataka",
    "30": "Goa",
    "31": "Lakshadweep",
    "32": "Kerala",
    "33": "Tamil Nadu",
    "34": "Puducherry",
    "35": "Andaman and Nicobar Islands",
    "36": "Telangana",
    "37": "Andhra Pradesh",
    "38": "Ladakh",
}


def checksum_char(first14: str) -> str:
    """Compute the 15th (check) character for the first 14 characters of a GSTIN."""
    if len(first14) != 14 or any(c not in CHARSET for c in first14):
        raise ValueError("expected 14 characters from [0-9A-Z]")
    total = 0
    for i, ch in enumerate(first14):
        product = CHARSET.index(ch) * (1 if i % 2 == 0 else 2)
        total += product // 36 + product % 36
    return CHARSET[(36 - total % 36) % 36]


def validate_gstin(value: str) -> tuple[bool, str | None]:
    """Return ``(is_valid, reason_if_invalid)``. Normalises case and surrounding spaces."""
    gstin = value.strip().upper()
    match = GSTIN_RE.match(gstin)
    if not match:
        return False, "format"
    if match.group(1) not in STATE_CODES:
        return False, "state_code"
    if checksum_char(gstin[:14]) != gstin[14]:
        return False, "checksum"
    return True, None


def is_valid_gstin(value: str) -> bool:
    return validate_gstin(value)[0]


def state_of(gstin: str) -> str | None:
    """State/UT name for a GSTIN's first two digits, or None if unknown."""
    return STATE_CODES.get(gstin.strip()[:2])


def generate_gstin(state_code: str, rng: random.Random | None = None) -> str:
    """Generate a syntactically valid (checksum-correct) but fictional GSTIN for synthetic data."""
    if state_code not in STATE_CODES:
        raise ValueError(f"unknown state code {state_code!r}")
    rng = rng or random.Random()
    letters = string.ascii_uppercase
    pan = (
        "".join(rng.choices(letters, k=3))
        + rng.choice("CPHFATBLJG")  # PAN 4th char: holder type (company, person, ...)
        + rng.choice(letters)
        + "".join(rng.choices(string.digits, k=4))
        + rng.choice(letters)
    )
    first14 = f"{state_code}{pan}{rng.choice('123456789')}Z"
    return first14 + checksum_char(first14)
