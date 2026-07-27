#!/usr/bin/env python3
"""Policy-rule structure audit: the cascade half of STATUS question 8.

Ablates each DENY/REVIEW rule individually and prices it on dev — ANALYTICALLY,
from a replay's sidecar hit-sets (row 73's deny_hits/review_hits), with no
re-replay: removing rule R re-lands each case on the next fired predicate in
its recorded hit-sets, which is exactly what policy.adjudicate would return
(nothing downstream of adjudicate reads the branch except confidence.for_branch,
and extraction fields are untouched, so classification + calibration deltas are
exact). This makes the audit immune to concurrent edits of mib/ — it reads only
frozen artifacts — and instant.

The reconstruction is validated two ways before any number is read:
  1. control: recomputing every case with NO rule removed must reproduce the
     recorded (adjudication, branch) on all cases;
  2. --verify <rule>: a real patched replay of that one ablation, compared
     case-by-case against the analytic prediction.

Usage:
  .venv/bin/python experiments/ablate_policy.py run [--eval-dir D]
  .venv/bin/python experiments/ablate_policy.py verify <rule> [--eval-dir D] [--cache C]

Default eval dir: output/replay_r74refit (row-74 substrate; needs schema with
deny_hits/review_hits in debug.jsonl). Detail: output/viz/ablate_policy.jsonl.
"""
import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CH = ROOT.parent / "mib-doc-challenge"

from mib import confidence, policy  # noqa: E402

DEV = set(json.loads((ROOT / "data_splits.json").read_text())["dev"])
TRUTH = {r["case_id"]: r["adjudication"]
         for r in csv.DictReader(open(CH / "data/train_labels.csv"))}
OUT = ROOT / "output/viz/ablate_policy.jsonl"

# Classification raw points per case (CLAUDE.md scoring digest).
def raw_points(pred, truth):
    if pred == truth:
        return 8
    if pred == "NEEDS_REVIEW":            # true A/D sent to review
        return 2
    if truth == "NEEDS_REVIEW":           # missed a true review
        return 1
    if pred == "APPROVED" and truth == "DENIED":
        return -4                         # catastrophic false approval
    return 0                              # wrong A<->D the other way


def load(eval_dir):
    debugs = [json.loads(l) for l in open(eval_dir / "debug.jsonl")]
    preds = {json.loads(l)["case_id"]: json.loads(l)
             for l in open(eval_dir / "predictions.jsonl")}
    missing = [d["case_id"] for d in debugs if "deny_hits" not in d]
    if missing:
        sys.exit(f"eval dir predates row 73 (no hit-sets): {missing[:3]}...")
    return debugs, preds


def reland(d, removed=None):
    """(decision, branch) with `removed` rule deleted, from recorded hit-sets."""
    if d["finding"]:
        return d["finding"], "adjudicator_finding"
    for name in d["deny_hits"]:
        if name != removed:
            return "DENIED", name
    for name in d["review_hits"]:
        if name != removed:
            return "NEEDS_REVIEW", name
    return "APPROVED", "clean_approve"


def calib_score(rows):
    """20*max(0, 1-2*mean_brier) over (conf, correct) pairs."""
    briers = [(c - (1.0 if ok else 0.0)) ** 2 for c, ok in rows]
    return 20 * max(0.0, 1 - 2 * sum(briers) / len(briers))


def run(eval_dir):
    debugs, preds = load(eval_dir)

    # Control: the reconstruction must reproduce the recorded run exactly.
    bad = [d["case_id"] for d in debugs
           if (preds[d["case_id"]]["adjudication"], d["branch"]) != reland(d)]
    if bad:
        sys.exit(f"CONTROL FAILED — reconstruction diverges on {len(bad)} "
                 f"cases (first: {bad[:5]}); do not read any ablation number")
    print(f"control: reconstruction reproduces (adjudication, branch) on "
          f"{len(debugs)}/{len(debugs)} cases")

    dev_dbg = [d for d in debugs if d["case_id"] in DEV]
    base_raw = sum(raw_points(preds[d["case_id"]]["adjudication"],
                              TRUTH[d["case_id"]]) for d in dev_dbg)
    base_calib_rows = [(confidence.for_branch(d["branch"]),
                        preds[d["case_id"]]["adjudication"] == TRUTH[d["case_id"]])
                       for d in dev_dbg]
    base_calib = calib_score(base_calib_rows)
    print(f"dev baseline: class_raw={base_raw} calib={base_calib:.4f}\n")

    detail, table = [], []
    for rule in policy.DENY_BRANCHES + policy.REVIEW_BRANCHES:
        moved = carries = harms = lateral = cfa = 0
        raw = 0
        calib_rows = []
        for d in dev_dbg:
            cid = d["case_id"]
            truth = TRUTH[cid]
            old = preds[cid]["adjudication"]
            new, new_branch = reland(d, removed=rule)
            calib_rows.append((confidence.for_branch(new_branch), new == truth))
            raw += raw_points(new, truth)
            if new != old:
                moved += 1
                if old == truth and new != truth:
                    carries += 1
                elif old != truth and new == truth:
                    harms += 1
                else:
                    lateral += 1
                if new == "APPROVED" and truth == "DENIED":
                    cfa += 1
                detail.append({"rule": rule, "case_id": cid, "old": old,
                               "new": new, "new_branch": new_branch,
                               "truth": truth})
        d_class = (raw - base_raw) * 80 / 5600
        d_calib = calib_score(calib_rows) - base_calib
        table.append((rule, moved, carries, harms, lateral, cfa,
                      d_class, d_calib))

    table.sort(key=lambda r: r[6])
    print(f"{'rule':22} {'moved':>5} {'carry':>5} {'harm':>4} {'lat':>4} "
          f"{'CFA':>3} {'d_class':>8} {'d_calib':>8}")
    for rule, moved, carries, harms, lat, cfa, dc, dcal in table:
        print(f"{rule:22} {moved:>5} {carries:>5} {harms:>4} {lat:>4} "
              f"{cfa:>3} {dc:>+8.3f} {dcal:>+8.3f}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w") as f:
        for row in detail:
            f.write(json.dumps(row, sort_keys=True) + "\n")
    print(f"\nper-case detail: {OUT} ({len(detail)} moves)")


def verify(rule, eval_dir, cache_path):
    """Real patched replay of one ablation vs the analytic prediction."""
    from mib import cache as mcache, config, runner
    debugs, preds = load(eval_dir)
    want = {d["case_id"]: reland(d, removed=rule) for d in debugs}

    keep_deny = tuple((n, p) for n, p in policy.DENY_RULES if n != rule)
    keep_review = tuple((n, p) for n, p in policy.REVIEW_RULES if n != rule)
    saved = (policy.DENY_RULES, policy.REVIEW_RULES)
    policy.DENY_RULES, policy.REVIEW_RULES = keep_deny, keep_review
    try:
        meta, recs = mcache.read(cache_path)
        config.require_agreement([(str(cache_path), meta)])
        got = {}
        for rec in recs:
            if rec.get("error"):
                continue
            pages, reads_by_page = mcache.to_case(rec["pages"])
            record, dbg = runner.predict_from_evidence(pages, reads_by_page,
                                                       rec["stem"])
            got[record["case_id"]] = (record["adjudication"], dbg["branch"])
    finally:
        policy.DENY_RULES, policy.REVIEW_RULES = saved

    diff = [c for c in want if want[c] != got.get(c)]
    print(f"verify {rule}: {len(want) - len(diff)}/{len(want)} match; "
          f"diverging: {diff[:5] if diff else 'none'}")
    sys.exit(1 if diff else 0)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "verify"])
    ap.add_argument("rule", nargs="?")
    ap.add_argument("--eval-dir", default=str(ROOT / "output/replay_r74refit"))
    ap.add_argument("--cache", default=str(ROOT / "output/cache/train_grid.jsonl"))
    a = ap.parse_args()
    if a.cmd == "run":
        run(Path(a.eval_dir))
    else:
        if not a.rule:
            sys.exit("verify needs a rule name")
        verify(a.rule, Path(a.eval_dir), Path(a.cache))
