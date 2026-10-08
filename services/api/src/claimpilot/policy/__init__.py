"""Policy-as-code: the company expense policy as numbered, citable clauses plus rule logic.

``Policy.load()`` reads ``config/policy.yaml``; ``evaluate_document`` and ``evaluate_claim`` return
evidence-carrying ``Finding``s that quote the clause they come from. Pure and deterministic: no
network, no LLM at evaluation time (ARCHITECTURE: ``policy`` must not call LLMs).
"""

from claimpilot.policy.cities import normalise_city, same_city
from claimpilot.policy.codes import PolicyCode
from claimpilot.policy.model import DEFAULT_POLICY_PATH, Policy, UnknownClause
from claimpilot.policy.money import format_inr
from claimpilot.policy.receipt_text import alcohol_items, attendee_headcount, hotel_stay
from claimpilot.policy.rules import Clause, ClauseBase, RuleClause

__all__ = [
    "DEFAULT_POLICY_PATH",
    "Clause",
    "ClauseBase",
    "Policy",
    "PolicyCode",
    "RuleClause",
    "UnknownClause",
    "alcohol_items",
    "attendee_headcount",
    "format_inr",
    "hotel_stay",
    "normalise_city",
    "same_city",
]
