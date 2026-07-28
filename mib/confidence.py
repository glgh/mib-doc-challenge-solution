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
}

_table_path = Path(__file__).parent / "confidence_table.json"
_FITTED = json.loads(_table_path.read_text()) if _table_path.exists() else {}

# Cell-keyed refinement (TODO 5.7): within a branch, a would-be-review decision
# that ALSO independently trips a `review_flag` is empirically more often
# correct than one standing alone (dev OOF +0.159 cal, review_flag co-fire lifts
# fee_unknown/waived_non_dip accuracy ~0.4 -> ~0.77). Only branches that observed
# both bits on the fit split are present; everything else backs off to the
# per-branch value, which backs off to FALLBACK. Keyed "1"/"0" = review_flag
# co-fires or not. Fitted alongside confidence_table.json by fit_confidence.py.
_cells_path = Path(__file__).parent / "confidence_cells.json"
_CELLS = json.loads(_cells_path.read_text()) if _cells_path.exists() else {}


def for_branch(branch):
    if branch in _FITTED:
        return _FITTED[branch]
    return FALLBACK.get(branch, 0.5)


def for_case(branch, review_cofire):
    """Per-case confidence: the branch value refined by whether a second,
    independent review reason co-fired. Confidence-only — never changes a
    decision (the adjudication and branch are already fixed upstream). Backs off
    to for_branch when the branch did not split on this bit."""
    cell = _CELLS.get(branch)
    if cell is not None:
        v = cell.get("1" if review_cofire else "0")
        if v is not None:
            return v
    return for_branch(branch)
