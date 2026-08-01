"""Drill into the two strongest discovery leads (2026-07-29):
  1. branch=b13_census  — 78% adj-wrong, the single biggest error pocket.
  2. home-world embargo  — TRAPPIST-1e / Eris Relay 100% DENIED, Wolf-1061c 73%.
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

print("### 1. b13_census branch: predicted vs truth adjudication")
conf = defaultdict(int)
examples = defaultdict(list)
for c, d in dbg.items():
    if d.get("branch") != "b13_census":
        continue
    pv, tv = pred[c]["adjudication"], truth[c]["adjudication"]
    conf[(pv, tv)] += 1
    if pv != tv:
        examples[(pv, tv)].append(c)
print(f"{'PRED':<14}{'TRUTH':<14}{'n':>4}")
for (pv, tv), n in sorted(conf.items(), key=lambda kv: -kv[1]):
    mark = "  <- WRONG" if pv != tv else "  ok"
    print(f"{pv:<14}{tv:<14}{n:>4}{mark}")
print("\ntruth distribution among b13_census cases:",
      dict(Counter(truth[c]["adjudication"]
                   for c, d in dbg.items() if d.get("branch") == "b13_census")))
print("sample wrong cases:")
for k, cs in examples.items():
    print(f"  {k}: {cs[:6]}")

print("\n### 2. home-world x adjudication x planetary_embargo")
worlds = ["TRAPPIST-1e", "Eris Relay", "Wolf-1061c", "Mars Dome-7", "Gliese-581g"]
for w in worlds:
    cs = [c for c in truth if truth[c]["home_world"] == w]
    adj = Counter(truth[c]["adjudication"] for c in cs)
    emb = sum(1 for c in cs if "planetary_embargo" in (truth[c]["risk_flags"] or ""))
    anyflag = sum(1 for c in cs if (truth[c]["risk_flags"] or "none") != "none")
    print(f"{w:<14} n={len(cs):3d}  {dict(adj)}  "
          f"planetary_embargo={emb}  any_flag={anyflag}")

print("\n### 3. does ANY risk flag ever co-occur with APPROVED (truth)?")
appr_flagged = [c for c in truth
                if truth[c]["adjudication"] == "APPROVED"
                and (truth[c]["risk_flags"] or "none") != "none"]
print(f"APPROVED cases with a risk flag: {len(appr_flagged)}  {appr_flagged[:10]}")
# and do WE ever approve a flagged case (CFA risk)?
we_appr_flagged = [c for c in pred
                   if pred[c]["adjudication"] == "APPROVED"
                   and (pred[c].get("risk_flags") or "none") != "none"]
print(f"WE approve with a risk flag: {len(we_appr_flagged)}  {we_appr_flagged[:10]}")
