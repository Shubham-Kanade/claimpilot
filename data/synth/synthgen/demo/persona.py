"""Who the demo pile belongs to, and when "now" is.

Asha Menon is ``DEMO-ASHA`` in ``services/mcp-corp/seed/employees.json`` (and her calendar is in
``calendar.json`` next to it); this module repeats that record so the pile can be written without
reading the other service. A test of the API keeps the two in step.
"""

from __future__ import annotations

from datetime import date

DEMO_PERSONA_ID = "DEMO-ASHA"
TRIP_ID = f"{DEMO_PERSONA_ID}-T1"
DEMO_TODAY = date(2026, 10, 12)  # the clock the demo is pinned to (the policy window is 90 days)
DEMO_SPLIT = "demo"  # the manifest's split: not dev and not test, so no eval tunes on it
STORY_SEED = 2026  # picks the random PAN part of each GSTIN; unrelated to the CLI --seed

DEMO_EMPLOYEE: dict[str, str] = {
    "id": DEMO_PERSONA_ID,
    "name": "Asha Menon",
    "employee_id": "EMP90001",
    "grade": "L3",
    "base_city": "Pune",
    "base_state_code": "27",
    "manager_id": "DEMO-RAVI",
    "email": "asha.menon@example.com",
}
