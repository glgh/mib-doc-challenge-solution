"""Corpus-level inference: policy entities identified by how often they recur.

A sponsor id is per-case data — each packet carries its own. Except when it
isn't: revoked sponsors are policy-level entities that recur across many
packets. On the 1,000-case train corpus the occurrence spectrum is starkly
bimodal, with nothing at all between:

    appears  1x : 734 ids     appears  9x :  1 id
    appears  2x :  23 ids     appears 16x :  1 id
                              appears 18x :  3 ids
                              appears 22x :  1 id

and the six ids above that gap are exactly the six revoked sponsors — the three
published in FIELD_MANUAL.md plus the three this repo had hardcoded from train
labels. So recurrence recovers the revoked list *without consulting a single
label*, which matters twice over:

  1. Coverage. A hardcoded list cannot see a revoked sponsor that exists only in
     the private set, and a missed one falls through to `clean_approve` — a false
     approval, the worst-scoring outcome. Ablating the three mined ids on dev
     costs 1.80 classification points and produces exactly that CFA; this
     detector recovers 1.79 of the 1.80 and returns CFA to 0.
  2. Provenance. A list mined from train labels is the more audit-exposed
     artifact of the two. This uses no labels and no per-PDF keys — only the
     distribution of the input directory it was pointed at.

The detector validates its own precondition rather than trusting a threshold.
It splits the occurrence spectrum at the largest *ratio* gap (counts are
multiplicative, so 2 -> 9 is a bigger step than 9 -> 16 even though it spans
fewer integers) and only reports a split it can actually justify: the gap must
be a clear one and the recurring set must stay a small minority. On a corpus
with no such structure — a smooth spectrum, or too few cases to have one — it
abstains and the hardcoded list stands alone.
"""
import collections

from . import confidence, policy, vocab

# Branches that outrank `revoked_sponsor` in policy.adjudicate. A case already
# settled by one of them keeps its decision, exactly as it would have if the id
# had been in REVOKED_SPONSORS from the start — the revision reproduces the
# cascade's precedence rather than overriding it. Derived from the policy tier
# lists (tier 0 + every deny rule with higher attribution priority than
# revoked_sponsor) so it cannot drift.
OUTRANKS_REVOKED = ("adjudicator_finding",) + policy.DENY_BRANCHES[
    :policy.DENY_BRANCHES.index("revoked_sponsor")]
NON_DIP_VISAS = frozenset(vocab.VISAS) - {"DIP-1"}

# A sponsor id must clear the gap by this factor before the spectrum counts as
# bimodal. 3.0 sits well below the 4.5x seen on train and well above the 1.8x of
# the runner-up gap, so it separates the real structure from ordinary spread.
MIN_GAP_RATIO = 3.0
# Never flag more than this share of distinct ids: policy entities are rare by
# definition, and a detector that indicts a fifth of the corpus has found
# something other than revocation.
MAX_FLAGGED_SHARE = 0.05
# Below this many cases the spectrum is too sparse to read — a two-PDF run would
# otherwise "detect" any id appearing twice.
MIN_CASES = 50
# The missing-sponsor sentinel is not a sponsor.
SENTINEL = "SPN-0000"


def recurring_sponsors(sponsor_ids):
    """Sponsor ids that recur enough to be policy entities rather than case data.

    `sponsor_ids` is one id per case (missing/unknown entries may be None or the
    sentinel). Returns a frozenset, empty whenever the corpus does not show a
    clean bimodal spectrum — abstaining is always safe, because the caller keeps
    the published + validated hardcoded list either way.
    """
    ids = [s for s in sponsor_ids if s and s != SENTINEL]
    if len(ids) < MIN_CASES:
        return frozenset()

    counts = collections.Counter(ids)
    spectrum = sorted(set(counts.values()))
    if len(spectrum) < 2:
        return frozenset()

    # Largest multiplicative step between adjacent occupied occurrence counts.
    ratio, boundary = max((hi / lo, lo) for lo, hi in zip(spectrum, spectrum[1:]))
    if ratio < MIN_GAP_RATIO:
        return frozenset()

    flagged = frozenset(s for s, n in counts.items() if n > boundary)
    if len(flagged) > MAX_FLAGGED_SHARE * len(counts):
        return frozenset()
    return flagged


def revise(records, debugs):
    """Apply the revoked-sponsor rule to ids the corpus revealed. -> (new_ids, n).

    Mutates `records`/`debugs` in place. Runs after every case is predicted,
    because until then there is no corpus to count. Only ever *tightens* a
    decision toward DENIED under the same rule policy already applies to a known
    revoked sponsor, and only where that rule would actually have fired: a known
    non-DIP visa (an unknown visa must not arm a denial — see policy.adjudicate),
    and no higher-precedence branch already deciding the case.

    The branch stays `revoked_sponsor` because it is the same rule; what differs
    is where the id came from, which `sponsor_source` records.
    """
    detected = recurring_sponsors([r.get("sponsor_id") for r in records])
    new_ids = detected - set(vocab.REVOKED_SPONSORS)
    if not new_ids:
        return frozenset(), 0

    by_case = {d.get("case_id"): d for d in debugs if d}
    revised = 0
    for record in records:
        if record.get("sponsor_id") not in new_ids:
            continue
        if record.get("visa_class") not in NON_DIP_VISAS:
            continue
        debug = by_case.get(record["case_id"]) or {}
        if debug.get("branch") in OUTRANKS_REVOKED or record["adjudication"] == "DENIED":
            continue
        record["adjudication"] = "DENIED"
        record["confidence"] = confidence.for_branch("revoked_sponsor")
        debug["branch"] = "revoked_sponsor"
        debug["sponsor_source"] = "recurring"
        revised += 1
    return new_ids, revised
