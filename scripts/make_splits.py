#!/usr/bin/env python3
"""Freeze a stratified dev/holdout split of train (seed-fixed, committed).

Day-to-day evals score dev; holdout is read only at milestones (calibration
gate, pre-submission) to bound adaptive overfitting from iterating against
the same cases.
"""
import csv
import json
import random
from collections import defaultdict
from pathlib import Path

SEED = 8090
HOLDOUT_FRAC = 0.3
ROOT = Path(__file__).resolve().parent.parent
CH = ROOT.parent / "mib-doc-challenge"


def main():
    rows = list(csv.DictReader(open(CH / "data/train_labels.csv")))
    by_class = defaultdict(list)
    for r in rows:
        by_class[r["adjudication"]].append(r["case_id"])

    rng = random.Random(SEED)
    dev, holdout = [], []
    for cls in sorted(by_class):
        ids = sorted(by_class[cls])
        rng.shuffle(ids)
        k = round(len(ids) * HOLDOUT_FRAC)
        holdout += ids[:k]
        dev += ids[k:]

    out = ROOT / "data_splits.json"
    out.write_text(json.dumps(
        {"seed": SEED, "dev": sorted(dev), "holdout": sorted(holdout)}, indent=0) + "\n")
    print(f"wrote {out}: dev={len(dev)} holdout={len(holdout)}")


if __name__ == "__main__":
    main()
