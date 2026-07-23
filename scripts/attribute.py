#!/usr/bin/env python3
"""Attribute lost classification points to the stage that can recover them.

The eval prints one number per section; this says *what to work on*. For each
(branch, decision) cell it compares what we score now against two ceilings:

  relabel  — the best we could do by predicting a different single label for
             the whole cell. This is the entire headroom of a cost-sensitive
             decision rule or an ML model over today's features.
  split    — what remains even after relabeling: the cell is internally mixed,
             so recovering it needs a finer partition.

`split` says the cell must be divided; it does NOT say new evidence is required,
and reading it that way misdirects the roadmap. Two branches with similar `split`
can need opposite work: on dev, `fee_unknown` has its triggering field visible in
0.0% of cases (the fee genuinely is not in the document), while `b13_census` has
98.0% of its field values recoverable and is losing points with the evidence
already in hand. Ask which kind a cell is with
`scripts/recoverable.py <dump> <eval_dir> <split> <branch>`.

Cells are keyed by (branch, decision), not branch alone: `adjudicator_finding`
emits whatever the note says, so it is not a pure branch and conditioning on
the branch alone mixes three different decisions together.

Usage: scripts/attribute.py [eval_dir] [dev|holdout|all]
"""
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CH = ROOT.parent / "mib-doc-challenge"

from mib import config  # noqa: E402

CLASSES = ("APPROVED", "DENIED", "NEEDS_REVIEW")

# Payoff[truth][pred], mirroring evaluate.py:classification_points.
PAYOFF = {
    "APPROVED":     {"APPROVED": 8, "DENIED": 0, "NEEDS_REVIEW": 2},
    "DENIED":       {"APPROVED": -4, "DENIED": 8, "NEEDS_REVIEW": 2},
    "NEEDS_REVIEW": {"APPROVED": 1, "DENIED": 1, "NEEDS_REVIEW": 8},
}
MAX_RAW = 8.0
SECTION_POINTS = 80.0


def expected_points(dist):
    """dist: truth -> count. Returns {pred: expected raw points per case}."""
    n = sum(dist.values())
    return {p: sum(dist[t] / n * PAYOFF[t][p] for t in CLASSES) for p in CLASSES}


def load(eval_dir, split):
    eval_dir = Path(eval_dir)
    truth = {r["case_id"]: r["adjudication"]
             for r in csv.DictReader(open(CH / "data/train_labels.csv"))}
    ids = None if split == "all" else set(
        json.loads((ROOT / "data_splits.json").read_text())[split])
    preds, branches = {}, {}
    for line in open(eval_dir / "predictions.jsonl"):
        r = json.loads(line)
        preds[r["case_id"]] = r["adjudication"]
    for line in open(eval_dir / "debug.jsonl"):
        r = json.loads(line)
        branches[r["case_id"]] = r["branch"]
    scored_ids = set(truth) if ids is None else (set(truth) & ids)
    cases = [c for c in preds if c in scored_ids and c in branches]
    return truth, preds, branches, cases, scored_ids


def report(eval_dir="output/eval", split="dev"):
    truth, preds, branches, cases, scored_ids = load(eval_dir, split)
    meta_path = Path(eval_dir) / "meta.json"
    config.require_agreement([(str(eval_dir), json.loads(meta_path.read_text())
                              if meta_path.exists() else None)])

    # Normalize the way the scorer does: it accumulates CLASSIFICATION_MAX_RAW
    # once per *truth* row (evaluate.py:build_results), present or not. Scaling
    # by rows we happen to have instead would rescore the split as if the cases
    # we dropped had never existed — the numbers agree exactly until the runner
    # starts losing cases, which is precisely when this report gets consulted.
    scale = SECTION_POINTS / (MAX_RAW * len(scored_ids))
    missing = sorted(scored_ids - set(cases))

    cells = defaultdict(Counter)
    for c in cases:
        cells[(branches[c], preds[c])][truth[c]] += 1

    rows = []
    for (branch, now), dist in cells.items():
        n = sum(dist.values())
        E = expected_points(dist)
        best = max(E, key=E.get)
        rows.append({
            "branch": branch, "now": now, "n": n, "dist": dist,
            "scored": E[now] * n * scale,
            "relabel": (E[best] - E[now]) * n * scale,
            "split": (MAX_RAW - E[best]) * n * scale,
            "best": best, "acc": dist[now] / n,
        })
    rows.sort(key=lambda r: -(r["relabel"] + r["split"]))

    print(f"== classification attribution: {eval_dir} / {split} "
          f"({len(cases)} of {len(scored_ids)} scored cases) ==\n")
    print(f"{'branch':22s}{'pred':>5}{'n':>5}{'acc':>6}{'scored':>8}"
          f"{'relabel':>9}{'split':>8}  recover by")
    for r in rows:
        if r["relabel"] + r["split"] < 0.05:
            continue
        how = (f"relabel -> {r['best'][:2]}" if r["relabel"] > r["split"]
               else "finer partition")
        print(f"{r['branch']:22s}{r['now'][:2]:>5}{r['n']:5d}{r['acc']:6.2f}"
              f"{r['scored']:8.1f}{r['relabel']:9.2f}{r['split']:8.2f}  {how}")

    relabel = sum(r["relabel"] for r in rows)
    split_ = sum(r["split"] for r in rows)
    scored = sum(r["scored"] for r in rows)
    print(f"\n{'TOTAL':22s}{'':5}{len(cases):5d}{'':6}{scored:8.1f}"
          f"{relabel:9.2f}{split_:8.2f}")
    print(f"\n  scored now                    {scored:6.2f} / {SECTION_POINTS:.0f}")
    print(f"  + better decisions (ML lever) {relabel:6.2f}   <- fit on this split, optimistic")
    print(f"  + a finer partition           {split_:6.2f}   <- new evidence, or better "
          f"use of evidence we already have")
    if missing:
        # These have no branch, so no cell above accounts for them; without this
        # line they would read as "nothing to recover" rather than "not looked at".
        print(f"  + emitting a row at all       {MAX_RAW * len(missing) * scale:6.2f}"
              f"   <- {len(missing)} case(s) absent from predictions, "
              f"unattributable (see P2)")
    return {"scored": scored, "relabel": relabel, "split": split_,
            "missing": len(missing)}


if __name__ == "__main__":
    report(sys.argv[1] if len(sys.argv) > 1 else "output/eval",
           sys.argv[2] if len(sys.argv) > 2 else "dev")
