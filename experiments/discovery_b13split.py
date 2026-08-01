"""Split b13_census by its two fused sub-conditions and test against truth.

fire = (not has_biometric) OR (has_biometric AND not has_flag_evidence)
  A. no slip at all      -> maybe none required; absence != concealment
  B. slip, flags unread  -> the genuine concealment shape (MIB-000672)

If truth-APPROVED concentrate in A and truth-DENIED in B, splitting the branch
recovers over-review on A without re-arming the CFA the rule was built to stop.
Also profile each sub-group by visa/fee/registry to look for a safe-approve key.
"""
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CH = REPO.parent / "mib-doc-challenge"
truth = {r["case_id"]: r for r in csv.DictReader(open(CH / "data/train_labels.csv"))}
pred = {json.loads(l)["case_id"]: json.loads(l)
        for l in open(REPO / "output/replay_vf_c/predictions.jsonl")}
dbg = {json.loads(l)["case_id"]: json.loads(l)
       for l in open(REPO / "output/replay_vf_c/debug.jsonl")}

cases = [c for c, d in dbg.items() if d.get("branch") == "b13_census"]
A = [c for c in cases if not dbg[c]["has_biometric"]]                 # no slip
B = [c for c in cases if dbg[c]["has_biometric"]]                     # slip, unread


def dist(cs):
    return dict(Counter(truth[c]["adjudication"] for c in cs))


print(f"b13_census total {len(cases)}")
print(f"  A no-slip        n={len(A):3d}  {dist(A)}")
print(f"  B slip-unread    n={len(B):3d}  {dist(B)}")

for name, cs in [("A no-slip", A), ("B slip-unread", B)]:
    print(f"\n=== {name}: profile of truth-DENIED vs truth-APPROVED ===")
    for adj in ["APPROVED", "DENIED", "NEEDS_REVIEW"]:
        sub = [c for c in cs if truth[c]["adjudication"] == adj]
        if not sub:
            continue
        visa = Counter(truth[c]["visa_class"] for c in sub)
        fee = Counter(truth[c]["fee_status"] for c in sub)
        reg = Counter((dbg[c].get("registry_status") or "-") for c in sub)
        find = Counter((dbg[c].get("finding") or "-") for c in sub)
        print(f"  {adj:<12} n={len(sub):3d}")
        print(f"       visa={dict(visa)}")
        print(f"       fee ={dict(fee)}  registry={dict(reg)}")
        print(f"       finding={dict(find)}")

# The dangerous cell: within A (no slip), what do the truth-DENIED look like?
# If they are separable (flag/fee/visa), an approve-A rule could exempt them.
print("\n=== A no-slip truth-DENIED cases (the CFA risk if we approve A) ===")
for c in [c for c in A if truth[c]["adjudication"] == "DENIED"]:
    t = truth[c]
    print(f"  {c}  visa={t['visa_class']:<10} fee={t['fee_status']:<8} "
          f"flags={t['risk_flags']:<30} sponsor={t['sponsor_id']}")
