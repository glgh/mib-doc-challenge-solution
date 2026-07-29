#!/usr/bin/env python3
"""How much of the dev score is the fitted constants fitting themselves?

Four constants in the rules path are fitted by looking at train labels:

  policy.STALE_CUTOFF            the 180-day staleness boundary
  policy.FULL_EMBARGO_WORLDS     mined: worlds whose cases are all denied
  policy.PARTIAL_EMBARGO_WORLDS  mined: worlds whose non-DIP cases are all denied
  vocab.REVOKED_SPONSORS         3 published ids + 3 mined from train labels
  mib/confidence_table.json      per-branch P(correct), fitted on dev, scored on dev

The last one is openly circular and the others are selected on the same 700 cases
they are scored on, so dev 119.10 is an optimistic number by an unknown margin.
This measures the margin without spending a holdout read: 5-fold within dev,
refit every constant from the in-fold cases only, predict the held-out fold, and
score the concatenated out-of-fold predictions with the official scorer.

Runs off a page-text cache, so the whole audit is seconds. Everything it varies
lives downstream of the cache boundary; OCR and variant selection are frozen.

Usage: scripts/audit_constants.py [cache.jsonl] [--out DIR] [--folds N]
"""
import argparse
import csv
import json
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CH = ROOT.parent / "mib-doc-challenge"

from mib import cache, confidence, config, emit, policy, runner, vocab  # noqa: E402

# Published in FIELD_MANUAL.md, so policy rather than inference: these are never
# re-mined, and a fold that happens not to contain them must still know them.
MANUAL_SPONSORS = {"SPN-0007", "SPN-0139", "SPN-4040"}
SHRINK_K = 10
CLAMP = (0.05, 0.95)
# Mining floors, scaled down from the whole-corpus versions in the docstrings of
# policy.py / vocab.py because a fold sees 4/5 of dev, not all of train.
MIN_WORLD_CASES = 6
MIN_SPONSOR_CASES = 4


def _parse(d):
    try:
        y, m, dd = map(int, (d or "").split("-"))
        return date(y, m, dd)
    except ValueError:
        return None


# All three miners read the *label* columns of train_labels.csv, not the values
# the pipeline extracted. That is what a person deriving these constants does,
# and it is the only way the audit measures label-fitting rather than extraction
# noise: mining `home_world` from an 87.9%-accurate extractor drops Wolf-1061c
# from the partial-embargo list (22/26 non-DIP denied instead of the true 32/32)
# and manufactures a false approval that has nothing to do with overfitting.


def mine_stale_cutoff(rows, ids, default):
    """Largest gap in the in-fold arrival-date distribution -> its midpoint.

    Mirrors the max-margin band-midpoint choice for `STALE_CUTOFF` in policy.py
    (the "Max-margin choice: the band midpoint" note). The boundary is visible as
    an empty band in the arrival dates themselves, so no label is consulted
    beyond taking the dates from the labelled column.
    """
    dates = sorted({d for c in ids for d in [_parse(rows[c]["arrival_date"])]
                    if d and date(2025, 6, 1) < d < date(2026, 6, 1)})
    if len(dates) < 2:
        return default
    gap, lo = max((b - a, a) for a, b in zip(dates, dates[1:]))
    return lo + timedelta(days=gap.days // 2) if gap.days >= 20 else default


def mine_embargo_worlds(rows, ids):
    """-> (full, partial). Full: every case denied. Partial: every non-DIP denied."""
    full, partial = set(), set()
    by_world = defaultdict(list)
    for c in ids:
        by_world[rows[c]["home_world"]].append(c)
    for world, cases in by_world.items():
        if not world or len(cases) < MIN_WORLD_CASES:
            continue
        non_dip = [c for c in cases if rows[c]["visa_class"] != "DIP-1"]
        if all(rows[c]["adjudication"] == "DENIED" for c in cases):
            full.add(world)
        elif non_dip and all(rows[c]["adjudication"] == "DENIED" for c in non_dip):
            partial.add(world)
    return full, partial


def mine_revoked_sponsors(rows, ids):
    """Sponsor ids whose in-fold non-DIP cases are unanimously denied."""
    by_spn = defaultdict(list)
    for c in ids:
        spn = rows[c]["sponsor_id"]
        if spn and spn != "SPN-0000":
            by_spn[spn].append(c)
    mined = set()
    for spn, cases in by_spn.items():
        non_dip = [c for c in cases if rows[c]["visa_class"] != "DIP-1"]
        if len(non_dip) >= MIN_SPONSOR_CASES and \
                all(rows[c]["adjudication"] == "DENIED" for c in non_dip):
            mined.add(spn)
    return mined | MANUAL_SPONSORS


def fit_confidence_table(pred, truth, branch, ids, shrink_k=SHRINK_K):
    """Per-branch P(correct), shrunk toward the predicted class's base rate.

    Same estimator as scripts/fit_confidence.py; duplicated rather than imported
    because that script writes mib/confidence_table.json as a side effect and the
    audit must not touch the shipped table.
    """
    by_branch, by_decision, calls = (defaultdict(lambda: [0, 0]),
                                     defaultdict(lambda: [0, 0]), defaultdict(list))
    for c in ids:
        hit = pred[c]["adjudication"] == truth[c]
        by_branch[branch[c]][0] += hit
        by_branch[branch[c]][1] += 1
        by_decision[pred[c]["adjudication"]][0] += hit
        by_decision[pred[c]["adjudication"]][1] += 1
        calls[branch[c]].append(pred[c]["adjudication"])
    prior = {k: h / n for k, (h, n) in by_decision.items()}
    table = {}
    for b, (hits, n) in by_branch.items():
        p = sum(prior.get(c, 0.5) for c in calls[b]) / len(calls[b])
        table[b] = round(min(CLAMP[1], max(CLAMP[0], (hits + shrink_k * p) / (n + shrink_k))), 3)
    return table


class patched:
    """Swap the fitted constants for one fold, then put them back.

    The sets are mutated in place, not rebound: policy does
    `from .vocab import REVOKED_SPONSORS` and features aliases the same objects,
    so rebinding a name would leave those aliases pointing at the original.
    STALE_CUTOFF is a date, so it has to be rebound — `_is_stale` reads the
    module global per call, which makes that safe.
    """

    def __init__(self, cutoff, full, part, revoked, conf_table):
        self.new = (cutoff, full, part, revoked, conf_table)

    def __enter__(self):
        cutoff, full, part, revoked, conf = self.new
        self.old = (policy.STALE_CUTOFF, set(policy.FULL_EMBARGO_WORLDS),
                    set(policy.PARTIAL_EMBARGO_WORLDS), set(vocab.REVOKED_SPONSORS),
                    dict(confidence._FITTED))
        policy.STALE_CUTOFF = cutoff
        for target, value in ((policy.FULL_EMBARGO_WORLDS, full),
                              (policy.PARTIAL_EMBARGO_WORLDS, part),
                              (vocab.REVOKED_SPONSORS, revoked)):
            target.clear()
            target.update(value)
        confidence._FITTED.clear()
        confidence._FITTED.update(conf)
        return self

    def __exit__(self, *exc):
        cutoff, full, part, revoked, conf = self.old
        policy.STALE_CUTOFF = cutoff
        for target, value in ((policy.FULL_EMBARGO_WORLDS, full),
                              (policy.PARTIAL_EMBARGO_WORLDS, part),
                              (vocab.REVOKED_SPONSORS, revoked)):
            target.clear()
            target.update(value)
        confidence._FITTED.clear()
        confidence._FITTED.update(conf)
        return False


def folds(ids, k):
    """Deterministic k-way split; sorted ids round-robin, no RNG to pin."""
    ids = sorted(ids)
    return [[c for j, c in enumerate(ids) if j % k == i] for i in range(k)]


def run(cases, ids):
    """predict_from_evidence over `ids` under whatever constants are installed."""
    out, dbg = [], []
    for cid in ids:
        pages, ocr_lines = cases[cid]
        record, debug = runner.predict_from_evidence(pages, ocr_lines, cid)
        out.append(record)
        dbg.append(debug)
    return out, dbg


def main(cache_path, out_root, k, refit, shrink_k=SHRINK_K):
    meta, records = cache.read(cache_path)
    config.require_agreement([(str(cache_path), meta)])
    cases = {r["stem"]: cache.to_case(r["pages"]) for r in records if not r.get("error")}

    dev = [c for c in json.loads((ROOT / "data_splits.json").read_text())["dev"]
           if c in cases]
    rows = {r["case_id"]: r for r in csv.DictReader(open(CH / "data/train_labels.csv"))}
    truth = {c: r["adjudication"] for c, r in rows.items()}

    # Mining inputs come from a baseline pass under the shipped constants: the
    # values a fold mines over are the ones the pipeline actually extracts.
    base_recs, base_dbg = run(cases, dev)
    pred = {r["case_id"]: r for r in base_recs}
    branch = {d["case_id"]: d["branch"] for d in base_dbg}
    # `dev` is keyed by filename stem; case_id is voted from content and agrees on
    # all 1,000 train packets, but only mined-over cases where it does are usable.
    usable = [c for c in dev if c in pred and c in truth]

    print(f"cache {cache_path}  dev {len(dev)}  usable {len(usable)}  folds {k}")
    print(f"refitting: {','.join(refit)}\n")
    print(f"{'fold':>4} {'n':>4}  {'cutoff':>12} {'full':>6} {'part':>6} "
          f"{'revoked':>8}  mined ids")
    oof = []
    for i, fold in enumerate(folds(usable, k)):
        held = set(fold)
        infold = [c for c in usable if c not in held]
        # Each constant is refit only when named, so a run can attribute the gap
        # to one of them instead of reporting the sum.
        cutoff = (mine_stale_cutoff(rows, infold, policy.STALE_CUTOFF)
                  if "cutoff" in refit else policy.STALE_CUTOFF)
        # Copies, never the live objects: `patched` mutates its targets in place,
        # so handing it `policy.FULL_EMBARGO_WORLDS` itself would clear the set
        # and then "restore" it from the emptied alias.
        full, part = (mine_embargo_worlds(rows, infold) if "worlds" in refit
                      else (set(policy.FULL_EMBARGO_WORLDS),
                            set(policy.PARTIAL_EMBARGO_WORLDS)))
        revoked = (mine_revoked_sponsors(rows, infold) if "sponsors" in refit
                   else set(vocab.REVOKED_SPONSORS))
        table = (fit_confidence_table(pred, truth, branch, infold, shrink_k)
                 if "confidence" in refit else dict(confidence._FITTED))
        with patched(cutoff, full, part, revoked, table):
            recs, _ = run(cases, fold)
        oof.extend(recs)
        print(f"{i:>4} {len(fold):>4}  {cutoff!s:>12} {len(full):>6} {len(part):>6} "
              f"{len(revoked):>8}  {sorted(set(revoked) - MANUAL_SPONSORS)}")

    out_root = Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "predictions.jsonl").write_text(
        "".join(json.dumps(r, sort_keys=True) + "\n" for r in emit.dedupe(oof)))
    (out_root / "meta.json").write_text(json.dumps(
        config.stamp(artifact="eval", audit="constants_oof", folds=k, refit=sorted(refit),
                     replayed_from=str(cache_path), replayed_from_meta=meta,
                     n_records=len(oof)), indent=2, sort_keys=True) + "\n")
    print(f"\nwrote {len(oof)} out-of-fold predictions to {out_root}")
    print("score with: scripts/score_split.py %s dev" % out_root)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cache", nargs="?",
                    default=str(ROOT / f"output/cache/train_{config.RESTORE}.jsonl"))
    ap.add_argument("--out", default=str(ROOT / "output/audit_oof"))
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--shrink", type=int, default=SHRINK_K)
    ap.add_argument("--refit", default="cutoff,worlds,sponsors,confidence",
                    help="comma-separated subset to refit; the rest stay shipped")
    a = ap.parse_args()
    sys.exit(main(a.cache, a.out, a.folds, set(a.refit.split(",")), a.shrink))
