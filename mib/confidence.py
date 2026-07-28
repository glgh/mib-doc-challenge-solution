"""Branch → confidence.

Primary source: mib/confidence_table.json, fitted by scripts/fit_confidence.py
from dev-split empirical accuracy (Laplace-shrunk, clamped — see that script).
The hand-set table below is the fallback when no fitted table exists.
"""
import json
from pathlib import Path

FALLBACK = {
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
    # Never fires on train (no fitted value); ~0.3 matches the epistemic review
    # branches. Census-implied ceiling ≈0.32 (key=APPROVED → 51/157 truth NR).
    "injected_approval_review": 0.3,
}

_table_path = Path(__file__).parent / "confidence_table.json"
_FITTED = json.loads(_table_path.read_text()) if _table_path.exists() else {}

# Cell-keyed refinement (TODO 5.7): within a branch, two independent bits refine
# the per-branch confidence, both raising Brier honesty on the epistemic review
# pools without touching any decision (dev OOF +0.159 then +0.190 cal):
#   1. review_flag co-fires — a would-be-review call corroborated by a second,
#      independent review reason is more often correct (fee_unknown/waived_non_dip
#      accuracy ~0.4 -> ~0.77).
#   2. fee imputed 'paid' — a fee_unknown NR whose fee is merely SILENT (base-rate
#      'paid', not an ambiguous unpaid/waived) is usually a case truth left
#      decidable and we over-reviewed: accuracy 0.29 vs 0.81 (row 54's known
#      false-review pool, which we cannot safely flip at 1 CFA but CAN be honest
#      about here). Content-vs-label, not a render artifact.
# Cell key = "<review_flag><fee_paid>" (each 0/1). Only cells the fit found with
# support and a real move from the branch value are stored; anything else backs
# off to the per-branch value, which backs off to FALLBACK. Fitted alongside
# confidence_table.json by fit_confidence.py.
_cells_path = Path(__file__).parent / "confidence_cells.json"
_CELLS = json.loads(_cells_path.read_text()) if _cells_path.exists() else {}


def for_branch(branch):
    if branch in _FITTED:
        return _FITTED[branch]
    return FALLBACK.get(branch, 0.5)


def for_case(branch, review_cofire, fee_paid):
    """Per-case confidence: the branch value refined by (review_flag co-fires,
    fee imputed 'paid'). Confidence-only — never changes a decision (the
    adjudication and branch are already fixed upstream). Backs off to for_branch
    for any cell the fit did not store."""
    cell = _CELLS.get(branch)
    if cell is not None:
        v = cell.get(f"{int(bool(review_cofire))}{int(bool(fee_paid))}")
        if v is not None:
            return v
    return for_branch(branch)
