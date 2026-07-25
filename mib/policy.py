"""Adjudication rule engine.

Ordered cascade per the MIB field manual + train-validated inferences (see
docs/label-mining.md, docs/fraud-signals.md; every rule's hit/collateral was
measured via scripts/mine_signals.py before inclusion). Branches are named so
confidence is calibrated per-branch and eval residuals attribute to the rule
that fired.
"""
from datetime import date

from .parse import DISQUALIFYING_FLAGS, REVIEW_FLAGS
from .vocab import REVOKED_SPONSORS  # noqa: F401  (re-exported: policy owns the rule)

# TRAPPIST-1e / Eris Relay: 50/50 denied incl. DIP-1 (embargo pierces immunity).
FULL_EMBARGO_WORLDS = {"TRAPPIST-1e", "Eris Relay"}
# Wolf-1061c: 51/51 denied non-DIP; DIP-1 cases behave normally.
PARTIAL_EMBARGO_WORLDS = {"Wolf-1061c"}

# No visible receipt date exists in the corpus, so the 180-day staleness rule
# needs a boundary constant. Train has a 48-day empty band between the latest
# stale-denied arrival (2025-12-09) and the earliest fresh one (2026-01-26);
# any cutoff inside fits "perfectly". Max-margin choice: the band midpoint.
# vocab.snap repairs future-impossible OCR years (>=2028, one glyph off 2026)
# but never past ones: a past year entering this comparison may be a misread,
# yet "fixing" a genuine 2024 would un-stale a real denial — and a wrong DENIED
# costs 0 while a false APPROVED costs -4. The stale rule sees past years as-is.
STALE_CUTOFF = date(2026, 1, 2)


def _is_stale(arrival):
    try:
        y, m, d = map(int, (arrival or "").split("-"))
        return date(y, m, d) < STALE_CUTOFF
    except ValueError:
        return False


def adjudicate(values, sig):
    """Return (decision, branch_name)."""
    flags = sig["flags"]
    fee = (values.get("fee_status") or "unknown").lower()
    visa = values.get("visa_class")
    sponsor = values.get("sponsor_id")
    # Deny rules require POSITIVE evidence of their preconditions: an unknown
    # visa must not arm the non-DIP-only denials (it produced 3 over-denials
    # of true DIP-1 packets whose visa field failed to extract).
    known_non_dip = visa is not None and visa != "DIP-1"
    non_dip_or_unknown = visa != "DIP-1"

    if sig["finding"]:
        return sig["finding"], "adjudicator_finding"
    if flags & DISQUALIFYING_FLAGS:
        return "DENIED", "disqualifying_flag"
    if values.get("home_world") in FULL_EMBARGO_WORLDS:
        return "DENIED", "embargo_world"
    if values.get("home_world") in PARTIAL_EMBARGO_WORLDS and known_non_dip:
        return "DENIED", "embargo_world_partial"
    if sponsor in REVOKED_SPONSORS and known_non_dip:
        return "DENIED", "revoked_sponsor"
    if visa == "TRANSIT-7":
        return "DENIED", "transit_visa"
    if fee == "unpaid":
        return "DENIED", "fee_unpaid"
    if fee == "unknown":
        return "NEEDS_REVIEW", "fee_unknown"
    if _is_stale(values.get("arrival_date")) and known_non_dip:
        return "DENIED", "stale_arrival"
    # Waiver-code presence is NOT approval evidence: the only code in the corpus
    # is DIP-WAIVER, and on non-DIP packets those cases are 46% denied.
    if fee == "waived" and non_dip_or_unknown:
        return "NEEDS_REVIEW", "waived_non_dip"
    if not values.get("arrival_date"):
        return "NEEDS_REVIEW", "missing_arrival"
    if flags & REVIEW_FLAGS:
        return "NEEDS_REVIEW", "review_flag"
    if not sponsor and non_dip_or_unknown:
        return "NEEDS_REVIEW", "missing_sponsor"
    if not visa:
        return "NEEDS_REVIEW", "missing_visa"
    # Risk-concealment census (docs/organizer-guidance.md): a would-be approval
    # with no readable B-13 is the under-determined shape — the incriminating
    # evidence may simply be absent. Organizer ruling: NEEDS_REVIEW, never
    # APPROVED. Train-measured cost ~2.6 classification pts for CFA 52→~0;
    # OCR (plan Step 2) reclaims packets whose B-13 lives in scan pages.
    #
    # "Readable" means the risk line was read, not merely that a slip was
    # detected. MIB-000672 carried a B-13 whose flag line OCR'd to debris; with
    # only a presence check the packet looked complete and was approved against a
    # truth of DENIED. A detected slip we cannot read the flags from is exactly
    # the concealment shape this rule exists for.
    if not sig["has_biometric"] or not sig["has_flag_evidence"]:
        return "NEEDS_REVIEW", "b13_census"
    return "APPROVED", "clean_approve"
