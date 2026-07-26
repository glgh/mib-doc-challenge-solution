#!/usr/bin/env python3
"""Evidence-tier probes for deny-arming and approve-granting values (TODO 5.8).

MIB-000096 is the anchor: `known_non_dip` armed `stale_arrival` on a visa the
OCR ensemble vote fabricated (MED-3 vs truth DIP-1) — "positive evidence"
currently means any non-None value regardless of tier. Numbers first, gates
only after. Two probes off the debug sidecar's per-field provenance
(fname -> (doc_type, source); VOTE_DOC=99 marks vote-settled):

  A  deny-branch firings, bucketed by the weakest tier among the branch's
     arming fields (TEXT < OCR < VOTE), with the truth-adjudication mix per
     bucket. The interesting cell is VOTE/OCR-armed denials of truth-APPROVED/
     NEEDS_REVIEW cases — deny rules demand positive evidence, and a vote-grade
     value is the least positive kind the merge produces.
  B  clean_approve cases whose load-bearing fields (visa/sponsor/date/fee/world)
     are ALL OCR/vote-grade — no clean text layer or signed note underwrites the
     approval. Sizes the private-set exposure of a tier gate (dev approvals are
     currently 67/67, so dev alone cannot price one).

`disqualifying_flag` is out of scope: it arms on flags, whose evidence bound is
experiments/flags_oracle.py (TODO 1.10). `adjudicator_finding` too: the finding
is read from packet text, not the merge, and its audit is TODO 5.9.

Usage: experiments/tier_probe.py [eval_dir] [--split dev|hold|all]
eval_dir must hold debug.jsonl + predictions.jsonl from the current rev
(scripts/replay.py <cache> <eval_dir>).
"""
import argparse
import csv
import json
from collections import Counter
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CH = ROOT.parent / "mib-doc-challenge"

from mib.packet import SRC_TEXT, VOTE_DOC  # noqa: E402

TRUTH = {r["case_id"]: r for r in csv.DictReader(open(CH / "data/train_labels.csv"))}

# Deny branch -> the merged fields whose values arm it (co-arming visa included
# wherever known_non_dip is part of the predicate). Kept in sync with
# policy.adjudicate by test assertion? No — by eyeball; the cascade is 16
# branches and this lists its deny side minus the two out-of-scope ones above.
ARMING = {
    "embargo_world": ["home_world"],
    "embargo_world_partial": ["home_world", "visa_class"],
    "revoked_sponsor": ["sponsor_id", "visa_class"],
    "transit_visa": ["visa_class"],
    "fee_unpaid": ["fee_status"],
    "stale_arrival": ["arrival_date", "visa_class"],
}
LOAD_BEARING = ["visa_class", "sponsor_id", "arrival_date", "fee_status", "home_world"]
TIER_ORDER = {"TEXT": 0, "OCR": 1, "VOTE": 2}


def tier(prov_entry):
    """(doc_type, source) -> TEXT | OCR | VOTE. Manual corrections stamp (0,0)
    and land in TEXT — a signed note is rank-1 evidence, above both."""
    if prov_entry is None:
        return None
    doc_type, source = prov_entry
    if doc_type == VOTE_DOC:
        return "VOTE"
    return "TEXT" if source == SRC_TEXT else "OCR"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("eval_dir", nargs="?", default=str(ROOT / "output/eval"))
    ap.add_argument("--split", default="dev", choices=["dev", "hold", "all"])
    a = ap.parse_args()
    eval_dir = Path(a.eval_dir)

    dev = set(json.loads((ROOT / "data_splits.json").read_text())["dev"])
    want = {"dev": lambda c: c in dev, "hold": lambda c: c not in dev,
            "all": lambda c: True}[a.split]
    debug = {d["case_id"]: d for d in map(json.loads, open(eval_dir / "debug.jsonl"))
             if d["case_id"] in TRUTH and want(d["case_id"])}

    # -- Probe A: deny firings by weakest arming tier -------------------------
    per_branch = {}          # branch -> tier -> Counter(truth adjudication)
    weak_cases = []          # (branch, case, field tiers, truth) for VOTE/OCR rows
    for cid, d in sorted(debug.items()):
        branch = d["branch"]
        if branch not in ARMING:
            continue
        prov = d.get("provenance", {})
        tiers = {f: tier(prov.get(f)) for f in ARMING[branch]}
        # A missing provenance entry means the field merged no value; a deny
        # branch that fired anyway armed on absence — flag it loudly.
        weakest = max((t for t in tiers.values() if t), key=TIER_ORDER.get,
                      default="ABSENT")
        truth_adj = TRUTH[cid]["adjudication"]
        per_branch.setdefault(branch, {}).setdefault(
            weakest, Counter())[truth_adj] += 1
        if weakest != "TEXT":
            weak_cases.append((branch, cid, tiers, truth_adj))

    print(f"== A: deny-branch firings by weakest arming tier ({a.split}) ==")
    print(f"{'branch':22s} {'tier':6s} {'n':>4s}   truth mix")
    for branch in ARMING:
        for t in ("TEXT", "OCR", "VOTE", "ABSENT"):
            c = per_branch.get(branch, {}).get(t)
            if not c:
                continue
            mix = ", ".join(f"{k} {v}" for k, v in c.most_common())
            print(f"{branch:22s} {t:6s} {sum(c.values()):>4d}   {mix}")
    bad = [(b, cid, tiers, adj) for b, cid, tiers, adj in weak_cases
           if adj != "DENIED"]
    print(f"\nVOTE/OCR-armed deny firings: {len(weak_cases)}; "
          f"truth not DENIED: {len(bad)}")
    for b, cid, tiers, adj in bad:
        ts = ", ".join(f"{f}={t}" for f, t in tiers.items())
        print(f"  {cid} {b:20s} truth {adj:12s} {ts}")

    # -- Probe B: clean_approve evidence grade --------------------------------
    grade_mix = Counter()
    all_weak = []
    for cid, d in sorted(debug.items()):
        if d["branch"] != "clean_approve":
            continue
        prov = d.get("provenance", {})
        tiers = {f: tier(prov.get(f)) for f in LOAD_BEARING}
        present = {f: t for f, t in tiers.items() if t}
        n_text = sum(1 for t in present.values() if t == "TEXT")
        grade = "all_ocr_vote" if n_text == 0 else \
            f"{n_text}_text_of_{len(present)}"
        grade_mix[grade, TRUTH[cid]["adjudication"]] += 1
        if n_text == 0:
            all_weak.append((cid, present, TRUTH[cid]["adjudication"]))

    n_approve = sum(grade_mix.values())
    print(f"\n== B: clean_approve load-bearing evidence grade "
          f"({a.split}, {n_approve} cases) ==")
    for (grade, adj), n in sorted(grade_mix.items()):
        print(f"  {grade:16s} truth {adj:12s} {n:>4d}")
    print(f"\napprovals with NO text-grade load-bearing field: {len(all_weak)}")
    for cid, tiers, adj in all_weak:
        ts = ", ".join(f"{f}={t}" for f, t in sorted(tiers.items()))
        print(f"  {cid} truth {adj:12s} {ts}")


if __name__ == "__main__":
    main()
