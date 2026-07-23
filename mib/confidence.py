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


def for_branch(branch):
    if branch in _FITTED:
        return _FITTED[branch]
    return FALLBACK.get(branch, 0.5)
