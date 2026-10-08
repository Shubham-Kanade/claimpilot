"""Persona roster export: who each ``persona_id`` in the dataset is (grade, base city, month).

Grouping and policy evaluation need an employee profile per document. In production it comes
from the corporate directory (``mcp-corp``); for the synthetic dataset it is exported here so
evals can run against ground truth. Personas are re-derived from the seed, never stored twice.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from synthgen.personas import make_persona


def build_roster(persona_ids: Iterable[str], seed: int) -> list[dict[str, str]]:
    roster = []
    for persona_id in sorted(set(persona_ids)):
        persona = make_persona(seed, int(persona_id[1:]) - 1)  # ids are P{index + 1:03d}
        roster.append(
            {
                "id": persona.id,
                "name": persona.name,
                "employee_id": persona.employee_id,
                "grade": persona.grade,
                "base_city": persona.base_city.name,
                "base_state_code": persona.base_city.state_code,
                "month": persona.month.isoformat(),
                "email": persona.email,
            }
        )
    return roster


def write_roster(path: Path, persona_ids: Iterable[str], seed: int) -> None:
    roster = build_roster((p for p in persona_ids if p), seed)
    path.write_text(json.dumps(roster, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
