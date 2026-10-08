"""Stable machine codes of the findings the policy engine emits.

The code is what the UI, the approver view and the evals key on, so a code is never renamed. The
clause that each code cites is configured in ``config/policy.yaml``, not here.
"""

from __future__ import annotations

from enum import StrEnum


class PolicyCode(StrEnum):
    late_submission = "late_submission"  # 2.1
    receipt_required = "receipt_required"  # 3.1
    hotel_over_cap = "hotel_over_cap"  # 4.1
    meals_over_limit = "meals_over_limit"  # 5.1
    entertainment_over_cap = "entertainment_over_cap"  # 5.2
    alcohol_not_reimbursable = "alcohol_not_reimbursable"  # 6.1
    air_class_not_economy = "air_class_not_economy"  # 7.1
    train_class_above_grade = "train_class_above_grade"  # 7.2
    trip_conveyance_over_limit = "trip_conveyance_over_limit"  # 7.3
    mobile_over_cap = "mobile_over_cap"  # 8.1
    preapproval_required = "preapproval_required"  # 9.1
    personal_expense_flagged = "personal_expense_flagged"  # 10.1
    headcount_unclear = "headcount_unclear"  # 5.2: an attendee answer that cannot be counted
    # The policy could not be applied to this document. Said out loud and routed to a person,
    # because a rule that cannot run must never look like a rule that passed.
    grade_not_in_policy = "grade_not_in_policy"
    currency_not_inr = "currency_not_inr"
