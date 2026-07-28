#!/usr/bin/env python3
"""Fit per-branch confidence from dev-split empirical accuracy.

Reads output/eval/debug.jsonl (branch per case) + predictions + truth labels,
computes P(adjudication correct | branch) on the DEV split only, shrinks
small branches toward their decision-class base rate (Laplace, k=10), clamps
to [0.05, 0.95], and writes mib/confidence_table.json for runtime use.

Honesty note: branches that are organizer-sanctioned retreats (b13_census,
waived_non_dip) *should* score low — the label usually disagrees by design.

The fitted table is a function of the config that produced the eval it was fitted
on, so the config is recorded beside it in mib/confidence_table.meta.json.

Usage: scripts/fit_confidence.py [eval_dir]
"""
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

SHRINK_K = 10
CLAMP = (0.05, 0.95)
# A branch gets a review_flag cell split only with >= this many co-fire cases
# on the fit; below it the split is noise (dev OOF: >=5 keeps the full +0.159,
# >=8 starves waived_non_dip). Cells whose two bits round equal are dropped as
# behavioural no-ops (the high-confidence deny branches).
MIN_CELL_YES = 5
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CH = ROOT.parent / "mib-doc-challenge"

from mib import config  # noqa: E402


def main(eval_dir=None):
    eval_dir = Path(eval_dir or ROOT / "output/eval")
    meta_path = eval_dir / "meta.json"
    eval_meta = json.loads(meta_path.read_text()) if meta_path.exists() else None
    config.require_agreement([(str(eval_dir), eval_meta)])

    dev_ids = set(json.loads((ROOT / "data_splits.json").read_text())["dev"])
    truth = {r["case_id"]: r["adjudication"]
             for r in csv.DictReader(open(CH / "data/train_labels.csv"))}
    pred_rows = {r["case_id"]: r
                 for r in map(json.loads, open(eval_dir / "predictions.jsonl"))}
    preds = {cid: r["adjudication"] for cid, r in pred_rows.items()}
    debug = {r["case_id"]: r
             for r in map(json.loads, open(eval_dir / "debug.jsonl"))}
    branches = {cid: r["branch"] for cid, r in debug.items()}

    by_branch = defaultdict(lambda: [0, 0])   # branch -> [hits, n]
    by_decision = defaultdict(lambda: [0, 0])  # predicted class -> [hits, n]
    branch_calls = defaultdict(list)           # branch -> [predicted class, ...]
    for cid in dev_ids:
        if cid not in preds or cid not in branches:
            continue
        hit = preds[cid] == truth[cid]
        by_branch[branches[cid]][0] += hit
        by_branch[branches[cid]][1] += 1
        by_decision[preds[cid]][0] += hit
        by_decision[preds[cid]][1] += 1
        branch_calls[branches[cid]].append(preds[cid])

    class_prior = {cls: h / n for cls, (h, n) in by_decision.items()}

    # Shrink each branch toward the base rate of the class it actually predicts.
    # Branches are NOT pure: `adjudicator_finding` emits whatever the signed note
    # says, so on dev it splits 36 APPROVED / 84 DENIED / 52 NEEDS_REVIEW. Taking
    # the branch's class from whichever case happened to be read first therefore
    # drew the prior for a class most of that branch's cases were not assigned.
    # The prior is a per-case property, so mix it per case.
    table = {}
    branch_shrunk = {}                          # pre-clamp value the cells back off to
    for branch, (hits, n) in sorted(by_branch.items()):
        calls = branch_calls[branch]
        prior = (sum(class_prior.get(c, 0.5) for c in calls) / len(calls)
                 if calls else 0.5)
        shrunk = (hits + SHRINK_K * prior) / (n + SHRINK_K)
        branch_shrunk[branch] = shrunk
        table[branch] = round(min(CLAMP[1], max(CLAMP[0], shrunk)), 3)
        mixed = "" if len(set(calls)) <= 1 else f" (mixed: {len(set(calls))} classes)"
        print(f"{branch:24s} n={n:3d} raw={hits / n if n else 0:.3f} "
              f"prior={prior:.3f} -> {table[branch]}{mixed}")

    # Cell-keyed refinement (TODO 5.7): within a branch, refine confidence by two
    # bits, each shrunk toward the branch value — (1) whether an independent
    # review_flag co-fired, (2) whether the imputed fee is merely 'paid' (a silent
    # fee_unknown NR we usually over-reviewed, row 54). Key "<rf><fee>". Keep only
    # cells with real support that MOVE off the branch value; anything else backs
    # off identically via for_case. Both raise Brier honesty, decision untouched.
    def cell_key(cid):
        d = debug[cid]
        rf = "review_flag" in d.get("review_hits", []) and d["branch"] != "review_flag"
        fee_paid = pred_rows[cid].get("fee_status") == "paid"
        return f"{int(rf)}{int(fee_paid)}"

    by_cell = defaultdict(lambda: [0, 0])       # (branch, "<rf><fee>") -> [hits, n]
    for cid in dev_ids:
        if cid not in pred_rows or cid not in debug:
            continue
        by_cell[(branches[cid], cell_key(cid))][0] += preds[cid] == truth[cid]
        by_cell[(branches[cid], cell_key(cid))][1] += 1
    cells = {}
    for (branch, key), (hits, n) in sorted(by_cell.items()):
        if n < MIN_CELL_YES:
            continue                            # thin cell: back off to the branch
        bc = branch_shrunk.get(branch, 0.5)
        val = round(min(CLAMP[1], max(CLAMP[0], (hits + SHRINK_K * bc) / (n + SHRINK_K))), 3)
        if val != table.get(branch):            # a cell equal to the branch is a no-op
            cells.setdefault(branch, {})[key] = val
    for branch, v in sorted(cells.items()):
        detail = " ".join(f"{k}={v[k]}(n{by_cell[(branch, k)][1]})" for k in sorted(v))
        print(f"  cell {branch:20s} <rf><fee>: {detail}")

    out = ROOT / "mib/confidence_table.json"
    out.write_text(json.dumps(table, indent=2, sort_keys=True) + "\n")
    cells_out = ROOT / "mib/confidence_cells.json"
    cells_out.write_text(json.dumps(cells, indent=2, sort_keys=True) + "\n")
    # Sidecar rather than a key inside the table: mib/confidence.py looks the
    # table up by branch name and must keep seeing branch -> float, nothing else.
    (ROOT / "mib/confidence_table.meta.json").write_text(json.dumps(
        config.stamp(artifact="confidence_table", fitted_on=str(eval_dir),
                     fitted_on_meta=eval_meta, split="dev", shrink_k=SHRINK_K,
                     clamp=list(CLAMP), n_branches=len(table),
                     n_cell_branches=len(cells),
                     cell_key="review_flag_cofire+fee_paid"),
        indent=2, sort_keys=True) + "\n")
    print(f"wrote {out} + {cells_out.name} ({len(cells)} split branches)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
