"""Forgiving city-name comparison shared by policy (city tiers) and claims (base city vs trip).

Extraction returns whatever the bill prints, so the same city shows up as "Bangalore",
"Bengaluru", "Bengaluru, KA" or "BENGALURU - 560001". Everything that compares cities goes
through :func:`normalise_city` so those all agree.
"""

from __future__ import annotations

import re
import unicodedata

# Old and alternate names mapped to the spelling used in the policy. Matching is by whole word,
# so "Delhi NCR" becomes "delhi ncr" and still contains the Tier-1 city "delhi".
_ALIASES: dict[str, str] = {
    "new delhi": "delhi",
    "bangalore": "bengaluru",
    "bombay": "mumbai",
    "madras": "chennai",
    "calcutta": "kolkata",
    "poona": "pune",
    "gurgaon": "gurugram",
    "cochin": "kochi",
    "panjim": "panaji",
    "bhubaneshwar": "bhubaneswar",
    "trivandrum": "thiruvananthapuram",
    "baroda": "vadodara",
    "vizag": "visakhapatnam",
    "mysore": "mysuru",
    "pondicherry": "puducherry",
    "allahabad": "prayagraj",
}
_ALIAS_RE = re.compile(
    r"\b(" + "|".join(sorted(map(re.escape, _ALIASES), key=len, reverse=True)) + r")\b"
)
# Where the city name ends: "Pune, MH", "Pune (Hinjewadi)", "Pune - 411057" (hyphen or en dash).
_AFTER_CITY = re.compile(r"[,(/|\-" + chr(0x2013) + "]")


def _letters_only(text: str) -> str:
    """Keep letters, digits and combining marks (Devanagari vowel signs); the rest become spaces."""
    return "".join(
        ch if ch.isalnum() or unicodedata.category(ch).startswith("M") else " " for ch in text
    )


def normalise_city(name: str | None) -> str | None:
    """Canonical lower-case form of a city name, or ``None`` when there is nothing to compare."""
    if not name:
        return None
    head = _AFTER_CITY.split(name, maxsplit=1)[0]
    cleaned = " ".join(_letters_only(head.casefold()).split())
    if not cleaned:
        return None
    return _ALIAS_RE.sub(lambda m: _ALIASES[m.group(1)], cleaned)


def same_city(a: str | None, b: str | None) -> bool:
    """True when both names are present and denote the same city."""
    left, right = normalise_city(a), normalise_city(b)
    return left is not None and left == right
