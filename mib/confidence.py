"""Branch → confidence. Hand-set placeholders pending Step 3 (empirical
per-branch accuracy on frozen train splits). Low values on branches whose
train accuracy is known-poor (census/waived retreats are usually 2-point
outcomes, not correct labels)."""

BRANCH_CONFIDENCE = {
    "adjudicator_finding": 0.9,
    "disqualifying_flag": 0.85,
    "embargo_world": 0.9,
    "embargo_world_partial": 0.85,
    "revoked_sponsor": 0.85,
    "transit_visa": 0.75,
    "fee_unpaid": 0.8,
    "fee_unknown": 0.65,
    "stale_arrival": 0.85,
    "waived_non_dip": 0.3,
    "missing_arrival": 0.65,
    "review_flag": 0.65,
    "missing_sponsor": 0.6,
    "missing_visa": 0.55,
    "b13_census": 0.3,
    "clean_approve": 0.8,
}


def for_branch(branch):
    return BRANCH_CONFIDENCE.get(branch, 0.5)
