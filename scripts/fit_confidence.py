#!/usr/bin/env python3
"""Fit per-branch confidence from dev-split empirical accuracy.

Reads output/eval/debug.jsonl (branch per case) + predictions + truth labels,
computes P(adjudication correct | branch) on the DEV split only, shrinks
small branches toward their decision-class base rate (Laplace, k=10), clamps
to [0.05, 0.95], and writes mib/confidence_table.json for runtime use.

Honesty note: branches that are organizer-sanctioned retreats (b13_census,
waived_non_dip) *should* score low — the label usually disagrees by design.
"""
import csv
import json
from collections import defaultdict
from pathlib import Path

SHRINK_K = 10
CLAMP = (0.05, 0.95)
ROOT = Path(__file__).resolve().parent.parent
CH = ROOT.parent / "mib-doc-challenge"


def main():
    dev_ids = set(json.loads((ROOT / "data_splits.json").read_text())["dev"])
    truth = {r["case_id"]: r["adjudication"]
             for r in csv.DictReader(open(CH / "data/train_labels.csv"))}
    preds = {r["case_id"]: r["adjudication"]
             for r in map(json.loads, open(ROOT / "output/eval/predictions.jsonl"))}
    branches = {r["case_id"]: r["branch"]
                for r in map(json.loads, open(ROOT / "output/eval/debug.jsonl"))}

    by_branch = defaultdict(lambda: [0, 0])   # branch -> [hits, n]
    by_decision = defaultdict(lambda: [0, 0])  # predicted class -> [hits, n]
    for cid in dev_ids:
        if cid not in preds or cid not in branches:
            continue
        hit = preds[cid] == truth[cid]
        by_branch[branches[cid]][0] += hit
        by_branch[branches[cid]][1] += 1
        by_decision[preds[cid]][0] += hit
        by_decision[preds[cid]][1] += 1

    class_prior = {cls: h / n for cls, (h, n) in by_decision.items()}
    # branch -> decision mapping comes from observation (branches are pure)
    branch_decision = {}
    for cid in dev_ids:
        if cid in branches and cid in preds:
            branch_decision.setdefault(branches[cid], preds[cid])

    table = {}
    for branch, (hits, n) in sorted(by_branch.items()):
        prior = class_prior.get(branch_decision.get(branch), 0.5)
        shrunk = (hits + SHRINK_K * prior) / (n + SHRINK_K)
        table[branch] = round(min(CLAMP[1], max(CLAMP[0], shrunk)), 3)
        print(f"{branch:24s} n={n:3d} raw={hits / n if n else 0:.3f} "
              f"prior={prior:.3f} -> {table[branch]}")

    out = ROOT / "mib/confidence_table.json"
    out.write_text(json.dumps(table, indent=2, sort_keys=True) + "\n")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
