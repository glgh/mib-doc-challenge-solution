#!/usr/bin/env python3
"""Where the remaining points are, and whether that map transfers to validation.

Decomposes the gap to 150 into its scoring sections, then splits the two movable
sections by the structure that actually holds the loss:

  * classification headroom  -> by policy branch (the review pools dominate)
  * extraction headroom      -> by schema field (weighted miss mass)

Both are MEANS over cases (evaluate.py normalizes classification to
80*Sraw/(8N) and extraction to 50*Sraw/Smax), so headroom does NOT scale with
case count -- it is a property of the per-case distribution. That is exactly why
the same map can be projected onto validation: feed a val debug.jsonl and the
script rescales each branch's train-measured per-case loss by the branch's val
occupancy, holding within-branch behavior fixed. The gap between the train and
projected-val section totals is the only thing a distribution shift can move.

Inputs are the artifacts a replay + evaluate already produce:
  --scores   case_scores.jsonl   (evaluate.py --case-scores-jsonl)  [required]
  --debug    debug.jsonl         (replay out_dir)  -> branch per case  [required]
  --val-debug debug.jsonl        (replay of the val cache)  -> transfer table  [optional]

  experiments/headroom.py --scores S --debug D [--val-debug V]

Read-only; writes nothing. Provenance is the caller's job -- scores and debug
must come from the same replay (same cache, same rev) or the join is meaningless.
"""
import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Per-case maxima, from evaluate.py: classification is 8 raw/case, extraction is
# the field-weight sum (45) when no field is label-marked unrecoverable.
CLASS_MAX = 8.0
FIELD_WEIGHTS = {
    "risk_flags": 8, "species_code": 6, "applicant_name": 5, "home_world": 5,
    "visa_class": 5, "sponsor_id": 5, "arrival_date": 4, "fee_status": 4,
    "declared_purpose": 3,
}


def load(path):
    return [json.loads(l) for l in open(path)]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scores", required=True, help="case_scores.jsonl from evaluate.py")
    ap.add_argument("--debug", required=True, help="debug.jsonl from replay (branch per case)")
    ap.add_argument("--val-debug", help="val debug.jsonl -> transfer projection")
    a = ap.parse_args()

    scores = {s["case_id"]: s for s in load(a.scores)}
    branch = {d["case_id"]: d["branch"] for d in load(a.debug)}
    N = len(scores)

    # --- section totals (points, on the 150 scale) ---
    class_lost = sum((CLASS_MAX - s["classification_raw"]) for s in scores.values()) * (80.0 / (CLASS_MAX * N))
    emiss = sum(fr["max_points"] for s in scores.values()
                for fr in s["field_results"].values() if fr["status"] == "missed")
    emax = sum(fr["max_points"] for s in scores.values() for fr in s["field_results"].values())
    extr_lost = 50.0 * emiss / emax
    print(f"# headroom decomposition  (N={N})")
    print(f"  classification lost: {class_lost:6.2f} / 80")
    print(f"  extraction     lost: {extr_lost:6.2f} / 50")
    print()

    # --- classification by branch ---
    per = defaultdict(lambda: {"n": 0, "craw_lost": 0.0, "conserv": 0, "cfa": 0})
    for cid, s in scores.items():
        b = branch.get(cid, "?")
        r = per[b]; r["n"] += 1
        r["craw_lost"] += CLASS_MAX - s["classification_raw"]  # 0..8 raw, or 12 on a CFA
        if s["pred_adjudication"] == "NEEDS_REVIEW" and s["truth_adjudication"] != "NEEDS_REVIEW":
            r["conserv"] += 1
        if s.get("catastrophic_false_approval"):
            r["cfa"] += 1
    print("## classification headroom by branch")
    print(f"{'branch':22s} {'n':>4} {'tr%':>5} {'pts/80':>7} {'conserv':>7} {'CFA':>4}")
    for b, r in sorted(per.items(), key=lambda kv: -kv[1]["craw_lost"]):
        pts = r["craw_lost"] * (80.0 / (CLASS_MAX * N))
        if pts < 0.05:
            continue
        print(f"{b:22s} {r['n']:>4} {100*r['n']/N:5.1f} {pts:7.2f} {r['conserv']:>7} {r['cfa']:>4}")
    print()

    # --- extraction by field ---
    fmiss = Counter(); fn = Counter()
    for s in scores.values():
        for f, fr in s["field_results"].items():
            if fr["status"] == "missed":
                fmiss[f] += fr["max_points"]; fn[f] += 1
    print("## extraction headroom by field")
    print(f"{'field':16s} {'wt':>3} {'miss':>5} {'pts/50':>7}")
    for f in sorted(FIELD_WEIGHTS, key=lambda x: -fmiss[x]):
        print(f"{f:16s} {FIELD_WEIGHTS[f]:>3} {fn[f]:>5} {fmiss[f]*50.0/emax:7.3f}")

    # --- validation transfer projection ---
    if a.val_debug:
        vbranch = Counter(d["branch"] for d in load(a.val_debug))
        NV = sum(vbranch.values())
        # per-branch mean raw class loss (train) -> project onto val occupancy
        proj = 0.0
        rows = []
        for b, r in per.items():
            mean_raw = r["craw_lost"] / r["n"] if r["n"] else 0.0
            vshare = vbranch.get(b, 0) / NV
            contrib = 10.0 * vshare * mean_raw  # 80*(vshare*mean_raw)/8
            proj += contrib
            rows.append((b, 100*r["n"]/N, 100*vbranch.get(b, 0)/NV, contrib))
        print()
        print(f"## validation transfer  (train N={N}, val N={NV})")
        print(f"{'branch':22s} {'tr%':>5} {'val%':>5} {'d%pt':>6} {'proj_pts/80':>11}")
        for b, tp, vp, contrib in sorted(rows, key=lambda x: -x[3]):
            if contrib < 0.05 and abs(vp - tp) < 1.0:
                continue
            print(f"{b:22s} {tp:5.1f} {vp:5.1f} {vp-tp:+6.1f} {contrib:11.2f}")
        print(f"\n  projected val classification headroom: {proj:.2f} / 80  "
              f"(train {class_lost:.2f})")


if __name__ == "__main__":
    main()
