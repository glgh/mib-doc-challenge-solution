#!/usr/bin/env python3
"""Conservative-review worklist: dev cases we emit NEEDS_REVIEW where truth is
decisive (APPROVED/DENIED). These are the rank-1-recovery candidates — a
finding/flag/stamp that we're not acting on could flip them to the right answer.
Decompose by truth direction and by the branch we landed on, so we can see how
much is CFA-safe (truth DENIED) vs CFA-risky (truth APPROVED).

Reads a replay dir (predictions.jsonl + debug.jsonl). Usage:
  .venv/bin/python experiments/cons_review.py [replay_dir]
"""
import csv, json, sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CH = ROOT.parent / "mib-doc-challenge"
RD = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/p6_replay")
truth = {r["case_id"]: r for r in csv.DictReader(open(CH / "data/train_labels.csv"))}
pred = {json.loads(l)["case_id"]: json.loads(l) for l in open(RD / "predictions.jsonl")}
dbg  = {json.loads(l)["case_id"]: json.loads(l) for l in open(RD / "debug.jsonl")}
dev = set(json.loads((ROOT / "data_splits.json").read_text())["dev"])

cons = [c for c in dev if c in pred and c in truth
        and pred[c]["adjudication"] == "NEEDS_REVIEW"
        and truth[c]["adjudication"] in ("APPROVED", "DENIED")]

print(f"dev conservative reviews (we said NR, truth decisive): {len(cons)}")
by_truth = Counter(truth[c]["adjudication"] for c in cons)
print(f"  truth DENIED  (CFA-safe direction to flip): {by_truth['DENIED']}")
print(f"  truth APPROVED (CFA-risky direction):       {by_truth['APPROVED']}")

print("\nNR branch distribution (conservative reviews only):")
for b, n in Counter(dbg[c]["branch"] for c in cons).most_common():
    print(f"  {b:34s} {n}")

DISQ = {"memory_tampering", "planetary_embargo", "active_warrant", "biohazard_red"}
def fset(v):
    return {t.strip().lower() for t in (v or "").split("|")
            if t.strip() and t.strip().lower() != "none"}

denied_missed_disq = []
for c in cons:
    if truth[c]["adjudication"] != "DENIED":
        continue
    missed = (fset(truth[c]["risk_flags"]) & DISQ) - fset(pred[c]["risk_flags"])
    if missed:
        denied_missed_disq.append((c, sorted(missed), dbg[c]["branch"]))
print(f"\ntruth-DENIED conservative reviews with a MISSED disqualifying flag: "
      f"{len(denied_missed_disq)}")
for c, mf, b in sorted(denied_missed_disq):
    print(f"  {c}: missed {mf}  branch={b}")

# For the truth-DENIED slice: what would let us deny? Show their debug signals.
print("\ntruth-DENIED conservative reviews — deny-signal audit:")
for c in sorted(cons):
    if truth[c]["adjudication"] != "DENIED":
        continue
    d = dbg[c]
    print(f"  {c}: branch={d['branch']:26s} deny_hits={d.get('deny_hits')} "
          f"review_hits={d.get('review_hits')} truth_flags={truth[c]['risk_flags']}")
