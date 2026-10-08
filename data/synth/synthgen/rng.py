"""Deterministic random streams derived from the dataset seed.

Every stage asks for its own named stream (e.g. ``derive_rng(42, "persona", 3)``) so adding a
document type or reordering work in one stage never shifts the random draws of another.
String seeds are hashed with SHA-512 by :mod:`random`, so they do not depend on PYTHONHASHSEED.
"""

from __future__ import annotations

import random


def derive_rng(seed: int, *scope: object) -> random.Random:
    """A ``random.Random`` seeded from the dataset seed plus a scope path."""
    return random.Random(":".join(str(part) for part in (seed, *scope)))


def derive_seed(seed: int, *scope: object) -> int:
    """A 32-bit integer seed (for numpy, Faker, augraphy) derived like :func:`derive_rng`."""
    return derive_rng(seed, *scope).getrandbits(32)
