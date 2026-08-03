"""Adjudication rule engine.

Three severity tiers per the MIB field manual + train-validated inferences
(see docs/BACKGROUND.md sections 2-3; every rule's hit/collateral was measured
via scripts/mine_signals.py before inclusion): a signed adjudicator finding
passes through untouched, then ANY deny rule fires DENIED, then ANY review
rule fires NEEDS_REVIEW, else APPROVED. Severity monotonicity is structural —
a review rule cannot preempt a deny rule from any position in the lists — and
order *within* a tier is attribution priority only: it names the branch that
gets credit (per-branch confidence calibration, eval-residual attribution) and
is decision-invariant, per the co-fire audit (experiments/cofire_probe.py,
experiments.md row 39).
"""
from collections import namedtuple
from datetime import date

from .parse import DISQUALIFYING_FLAGS, REVIEW_FLAGS
from .vocab import REVOKED_SPONSORS  # re-exported: policy owns the revoked-sponsor rule

# TRAPPIST-1e / Eris Relay: 50/50 denied incl. DIP-1 (embargo pierces immunity).
FULL_EMBARGO_WORLDS = {"TRAPPIST-1e", "Eris Relay"}
# Wolf-1061c: 51/51 denied non-DIP; DIP-1 cases behave normally.
PARTIAL_EMBARGO_WORLDS = {"Wolf-1061c"}

# No visible receipt date exists in the corpus, so the 180-day staleness rule
# needs a boundary constant. Train has a 48-day empty band between the latest
# stale-denied arrival (2025-12-09) and the earliest fresh non-DIP one
# (2026-01-26; the lone arrival between them, 2026-01-15, is a staleness-exempt
# DIP-1). Any cutoff inside fits "perfectly". Max-margin choice: the band midpoint.
# The slack is a train-only property: on the 5,000-case validation set the band
# narrows to 2 days (2025-12-31 / 2026-01-02, experiments.md row 24). The cutoff
# still sits inside the gap and the branch fires at the same ~1.3% rate, so the
# constant transfers — but 6 validation cases lie within ±7 days of it, so treat
# it as a logged risk with no margin, not a robust boundary. Note the epoch
# itself (arrivals cluster around 2026) is a corpus assumption this constant and
# vocab.snap's year repair both hardcode.
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


_Ctx = namedtuple("_Ctx", "values sig flags fee visa sponsor known_non_dip non_dip_or_unknown")


def _ctx(values, sig):
    """The per-case evaluation context every rule predicate reads."""
    visa = values.get("visa_class")
    return _Ctx(
        values=values,
        sig=sig,
        flags=sig["flags"],
        fee=(values.get("fee_status") or "unknown").lower(),
        visa=visa,
        sponsor=values.get("sponsor_id"),
        # Deny rules require POSITIVE evidence of their preconditions: an unknown
        # visa must not arm the non-DIP-only denials (it produced 3 over-denials
        # of true DIP-1 packets whose visa field failed to extract).
        known_non_dip=visa is not None and visa != "DIP-1",
        non_dip_or_unknown=visa != "DIP-1",
    )


# Tier 1 — deny rules: positive-evidence disqualifiers. ANY hit fires DENIED;
# list order is attribution priority only (the first hit names the branch) and
# can never change the decision.
DENY_RULES = (
    ("disqualifying_flag", lambda c: c.flags & DISQUALIFYING_FLAGS),
    ("embargo_world", lambda c: c.values.get("home_world") in FULL_EMBARGO_WORLDS),
    ("embargo_world_partial",
     lambda c: c.values.get("home_world") in PARTIAL_EMBARGO_WORLDS and c.known_non_dip),
    ("revoked_sponsor", lambda c: c.sponsor in REVOKED_SPONSORS and c.known_non_dip),
    ("transit_visa", lambda c: c.visa == "TRANSIT-7"),
    ("fee_unpaid", lambda c: c.fee == "unpaid"),
    # Stale is deny-tier while fee-unknown is review-tier (the cascade's one
    # positive-evidence-vs-ignorance inversion, fixed in row 39 — and now
    # structural: a positively-evidenced stale arrival is deny-grade regardless
    # of fee state, and no review rule can preempt it from any position). The
    # truth-side cell "fee genuinely unknown AND stale AND non-DIP" is empty
    # across all 1,000 train labels (thin — ~1.6 expected under independence),
    # and all 8 train cases the pipeline lands there carry a polluted
    # fee_unknown, 7 truth DENIED. NR->DENIED is also the CFA-safe direction
    # to be wrong in.
    ("stale_arrival", lambda c: _is_stale(c.values.get("arrival_date")) and c.known_non_dip),
)

# Tier 2 — review rules: ignorance / uncertainty shapes. ANY hit (when no deny
# rule fired) fires NEEDS_REVIEW; list order is attribution priority only.
REVIEW_RULES = (
    ("fee_unknown", lambda c: c.fee == "unknown"),
    # Waiver-code presence is NOT approval evidence: the only code in the corpus
    # is DIP-WAIVER, and on non-DIP packets those cases are 46% denied.
    ("waived_non_dip", lambda c: c.fee == "waived" and c.non_dip_or_unknown),
    ("missing_arrival", lambda c: not c.values.get("arrival_date")),
    ("review_flag", lambda c: c.flags & REVIEW_FLAGS),
    ("missing_sponsor", lambda c: not c.sponsor and c.non_dip_or_unknown),
    ("missing_visa", lambda c: not c.visa),
    # Risk-concealment census (docs/BACKGROUND.md): a would-be approval
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
    ("b13_census", lambda c: not c.sig["has_biometric"] or not c.sig["has_flag_evidence"]),
)

DENY_BRANCHES = tuple(name for name, _ in DENY_RULES)
REVIEW_BRANCHES = tuple(name for name, _ in REVIEW_RULES)


def adjudicate(values, sig):
    """Return (decision, branch_name).

    Tier 0: a signed adjudicator finding passes through (its decision is
    variable, which is why it sits above the tiers rather than inside one).
    Tier 1: any deny rule -> DENIED. Tier 2: any review rule -> NEEDS_REVIEW.
    Else APPROVED via clean_approve.
    """
    if sig["finding"]:
        return sig["finding"], "adjudicator_finding"
    c = _ctx(values, sig)
    for name, fires in DENY_RULES:
        if fires(c):
            return "DENIED", name
    for name, fires in REVIEW_RULES:
        if fires(c):
            return "NEEDS_REVIEW", name
    # A hidden injected key claiming APPROVED caps the only unforced approval at
    # NEEDS_REVIEW: a signed adjudicator finding (tier 0) and every denial have
    # already been decided above, so this touches clean_approve alone and only
    # ever in the CFA-safe direction. The key's presence triggers caution; its
    # content is never trusted. No-op on train (0 clean_approves carry one);
    # insurance for the one uncovered CFA route (a private embargo world sailing
    # to clean_approve while carrying the generator's lying approval key).
    if sig.get("injected_approval"):
        return "NEEDS_REVIEW", "injected_approval_review"
    return "APPROVED", "clean_approve"


def fired(values, sig):
    """Every fired predicate per tier: (deny_hits, review_hits) name tuples.

    The per-case co-fire matrix for the debug sidecar: adjudicate() reports the
    first hit of the highest non-empty tier; this reports everything that
    fired, which is what residual mining and any future multi-signal rule (the
    field manual's "multiple review-only flags may combine" hint) would read.
    Note: experiments/probe_arbitration.py monkeypatches only adjudicate(), so
    under that probe fired() sees the unpatched inputs — harmless today (the
    probe discards debug sidecars) but worth knowing.
    """
    c = _ctx(values, sig)
    return (tuple(name for name, fires in DENY_RULES if fires(c)),
            tuple(name for name, fires in REVIEW_RULES if fires(c)))
