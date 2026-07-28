#!/usr/bin/env python3
"""Theoretical ceiling of the hidden answer-key inverse-verdict signal.

Census: some packets carry a hidden answer key whose *verdict* is systematically
false (anti-diagonal). This prices, in classification points, what perfectly
exploiting that anti-diagonal could buy over the current pipeline — separating
the definite half (key=DENIED -> truth APPROVED) from the ambiguous half
(key=APPROVED -> truth DENIED-or-NR, which the signal alone cannot split).

Usage: inverse_verdict_probe.py <predictions.jsonl>
"""
import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CH = ROOT.parent / "mib-doc-challenge"
CACHE = ROOT / "output/cache/train_grid.jsonl"
SPLITS = json.loads((ROOT / "data_splits.json").read_text())
DEV = set(SPLITS["dev"])

KEY_RE = re.compile(r"\b(APPROVED|DENIED|NEEDS_REVIEW)\b\W{0,3}0\.\d\d")


def raw(pred, truth):
    if pred == truth:
        return 8
    if truth in ("APPROVED", "DENIED") and pred == "NEEDS_REVIEW":
        return 2
    if truth == "NEEDS_REVIEW" and pred in ("APPROVED", "DENIED"):
        return 1
    if truth == "DENIED" and pred == "APPROVED":
        return -4          # CFA
    return 0               # wrong A<->D, or invalid


def key_verdict(hidden_lines):
    for h in hidden_lines:
        m = KEY_RE.search(h)
        if m:
            return m.group(1)
    return None


def main():
    preds_path = sys.argv[1] if len(sys.argv) > 1 else "output/replay_grid_full2/predictions.jsonl"
    truth = {r["case_id"]: r["adjudication"]
             for r in csv.DictReader(open(CH / "data/train_labels.csv"))}
    pred = {json.loads(l)["case_id"]: json.loads(l)["adjudication"]
            for l in open(preds_path)}

    keyed = {}
    with open(CACHE) as f:
        f.readline()
        for line in f:
            r = json.loads(line)
            hl = [h for p in r["pages"] for h in p.get("hidden_lines", [])]
            v = key_verdict(hl)
            if v:
                keyed[r["stem"]] = v

    def report(pop_name, pop):
        cases = [c for c in keyed if c in pop and c in truth and c in pred]
        table = Counter((keyed[c], truth[c]) for c in cases)
        cur = sum(raw(pred[c], truth[c]) for c in cases)
        ceil = 8 * len(cases)                       # perfect on all keyed cases

        # signal-driven best policy
        sig = 0
        cfa_train = 0
        for c in cases:
            k, t = keyed[c], truth[c]
            if k == "DENIED":
                p = "APPROVED"                       # signal: truth is APPROVED
            else:                                    # k == APPROVED
                p = "DENIED"                          # best constant vs D/NR mix
            sig += raw(p, t)
            if t == "DENIED" and p == "APPROVED":
                cfa_train += 1
        # conservative: only act on the definite half (key=DENIED->APPROVED),
        # leave key=APPROVED cases to the pipeline
        cons = 0
        for c in cases:
            k, t = keyed[c], truth[c]
            if k == "DENIED":
                cons += raw("APPROVED", t)
            else:
                cons += raw(pred[c], t)

        N = 1000 if pop_name == "full-1000" else len(pop)
        pt = lambda d: d / (8 * N) * 80              # raw delta -> class points
        print(f"\n=== {pop_name}  (keyed cases: {len(cases)}) ===")
        print(f"anti-diagonal table (key -> truth):")
        for (k, t), n in sorted(table.items()):
            print(f"    key={k:9s} truth={t:12s} {n}")
        print(f"current pipeline raw on keyed  : {cur}")
        print(f"signal-max raw (aggressive)    : {sig}   (train CFAs introduced: {cfa_train})")
        print(f"conservative raw (definite half): {cons}")
        print(f"absolute ceiling (perfect)     : {ceil}")
        print(f"-- class points over current (normalized /{N}) --")
        print(f"  aggressive signal : +{pt(sig-cur):.3f}")
        print(f"  conservative      : +{pt(cons-cur):.3f}")
        print(f"  absolute ceiling  : +{pt(ceil-cur):.3f}")

    report("full-1000", set(truth))
    report("dev-700", DEV)


if __name__ == "__main__":
    main()
