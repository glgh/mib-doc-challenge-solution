"""Branch → confidence. Currently v0's hand-set values; plan Step 3 replaces
these with empirical per-branch accuracy measured on frozen train splits."""

BRANCH_CONFIDENCE = {
    "adjudicator_finding": 0.9,
    "disqualifying_flag": 0.85,
    "revoked_sponsor": 0.85,
    "transit_visa": 0.75,
    "fee_unpaid": 0.8,
    "fee_unknown": 0.65,
    "waived_non_dip": 0.6,
    "missing_arrival": 0.65,
    "review_flag": 0.65,
    "missing_sponsor": 0.6,
    "missing_visa": 0.55,
    "clean_approve": 0.8,
}


def for_branch(branch):
    return BRANCH_CONFIDENCE.get(branch, 0.5)
