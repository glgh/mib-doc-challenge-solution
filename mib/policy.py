"""Adjudication rule engine.

Ordered cascade per the MIB field manual (+ train-validated inferences).
Each branch is named so confidence can be calibrated per-branch and eval
residuals can be attributed to the exact rule that fired.
"""
from .parse import DISQUALIFYING_FLAGS, REVIEW_FLAGS

REVOKED_SPONSORS = {"SPN-0007", "SPN-0139", "SPN-4040"}


def adjudicate(values, sig):
    """Return (decision, branch_name)."""
    flags = sig["flags"]
    fee = (values.get("fee_status") or "unknown").lower()
    visa = values.get("visa_class")
    sponsor = values.get("sponsor_id")
    has_waiver = bool(sig["waiver_code"])

    if sig["finding"]:
        return sig["finding"], "adjudicator_finding"
    if flags & DISQUALIFYING_FLAGS:
        return "DENIED", "disqualifying_flag"
    if sponsor in REVOKED_SPONSORS:
        return "DENIED", "revoked_sponsor"
    if visa == "TRANSIT-7":
        return "DENIED", "transit_visa"
    if fee == "unpaid" and not has_waiver:
        return "DENIED", "fee_unpaid"
    if fee == "unknown":
        return "NEEDS_REVIEW", "fee_unknown"
    if fee == "waived" and visa != "DIP-1" and not has_waiver:
        return "NEEDS_REVIEW", "waived_non_dip"
    if not values.get("arrival_date"):
        return "NEEDS_REVIEW", "missing_arrival"
    if flags & REVIEW_FLAGS:
        return "NEEDS_REVIEW", "review_flag"
    if not sponsor and visa != "DIP-1":
        return "NEEDS_REVIEW", "missing_sponsor"
    if not visa:
        return "NEEDS_REVIEW", "missing_visa"
    return "APPROVED", "clean_approve"
