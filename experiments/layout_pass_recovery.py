#!/usr/bin/env python3
"""Targeted recovery A/B for the layout-pass tier (TODO 6.7, slice C).

The cheap real success rate before the full-corpus regen: on ONLY the cases the
truncation tell fires on, compare the emitted fields with the PSM-3 pass off vs
on, against truth — through the REAL pipeline (`predict_from_evidence`), so the
closed-vocab snap that turns a partial read like `Tit reeport` back into
`Titan Freeport` is included. ~60 cases (minutes), not the ~38-min full regen.

Baseline comes from the off cache (no OCR); armed runs `predict` with
MIB_LAYOUT_PASS=psm3. Reports FIXED/BROKE on the truncated fields, plus any
collateral field regressions and adjudication moves (the CFA tripwire).

Usage: experiments/layout_pass_recovery.py [--cache OFF_CACHE]
"""
import argparse
import csv
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CH = ROOT.parent / "mib-doc-challenge"
TRAIN = CH / "data/train"

os.environ["MIB_LAYOUT_PASS"] = "psm3"           # arm before any worker forks

from mib import cache, config, runner, textmatch  # noqa: E402
from mib.stages import render                      # noqa: E402

ANCHORS = {"MIB-000101", "MIB-000114", "MIB-000096"}
FIELD = {"case id": "case_id", "applicant": "applicant_name",
         "registry name": "applicant_name", "purpose": "declared_purpose",
         "declared purpose": "declared_purpose", "species code": "species_code",
         "home world": "home_world", "visa class": "visa_class",
         "sponsor id": "sponsor_id", "arrival date": "arrival_date",
         "observed flags": "risk_flags"}
ALL_FIELDS = ["applicant_name", "species_code", "home_world", "visa_class",
              "sponsor_id", "arrival_date", "declared_purpose", "risk_flags",
              "fee_status"]


def match(field, a, b):
    if field == "risk_flags":
        return set(textmatch.normalize(a).split()) == set(textmatch.normalize(b).split())
    return textmatch.normalize(a) == textmatch.normalize(b)


def _ab_one(stem):
    """Worker: ONE armed OCR run, then S3-S5 with the psm3 read included (on) and
    dropped (off). Same code, same reads bar the pass -> a pin-the-tree-safe
    isolation of the layout pass (immune to any parallel-stream code drift)."""
    try:
        pages, rbp = runner.read_case(TRAIN / f"{stem}.pdf")     # default = psm3 armed
        base = {p: [r for r in reads if not r.variant.endswith("+psm3")]
                for p, reads in rbp.items()}
        trunc = set()
        for reads in base.values():
            for lab in render.extraction_gaps(reads).truncated:
                if FIELD.get(lab):
                    trunc.add(FIELD[lab])
        rec_on, _ = runner.predict_from_evidence(pages, rbp, stem)
        rec_off, _ = runner.predict_from_evidence(pages, base, stem)
        return stem, sorted(trunc), rec_on, rec_off
    except Exception as exc:
        return stem, None, {"_err": f"{type(exc).__name__}: {exc}"}, {}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cache", default=str(ROOT / "output/cache/train_grid.jsonl"))
    a = ap.parse_args()

    truth = {}
    with open(CH / "data/train_labels.csv") as f:
        for row in csv.DictReader(f):
            truth[row["case_id"]] = row

    # candidate cases: those with a truncated field on the off cache (just picks
    # which cases to OCR; the worker recomputes truncation from fresh reads).
    meta, recs = cache.read(a.cache)
    candidates = []
    for rec in recs:
        if rec.get("error") or rec["stem"] not in truth:
            continue
        _pages, rbp = cache.to_case(rec["pages"])
        if any(render.extraction_gaps(reads).truncated for reads in rbp.values()):
            candidates.append(rec["stem"])

    print(f"candidate truncated cases: {len(candidates)}")
    print(f"running pin-the-tree-safe PSM-3 A/B ({config.workers()} workers)...\n")

    from multiprocessing import Pool
    ab = {}          # stem -> (trunc_fields, rec_on, rec_off)
    with Pool(config.workers()) as pool:
        for stem, trunc, on, off in pool.imap_unordered(_ab_one, candidates):
            ab[stem] = (trunc, on, off)

    per_field = defaultdict(lambda: Counter())
    collateral_broke = []      # (stem, field): a NON-targeted field off==truth -> on!=truth
    adjudication_moves = []    # (stem, off, on, truth)
    anchors = []
    errors = []
    n_inst = 0
    for stem, (fields, on, off) in ab.items():
        if fields is None or on.get("_err"):
            errors.append((stem, on.get("_err"))); continue
        n_inst += len(fields)
        t = truth[stem]
        for fld in fields:
            tv = t.get(fld) or ""
            fo, fn = off.get(fld, ""), on.get(fld, "")
            if match(fld, fn, tv) and not match(fld, fo, tv):
                per_field[fld]["FIXED"] += 1
            elif match(fld, fo, tv) and not match(fld, fn, tv):
                per_field[fld]["BROKE"] += 1
            else:
                per_field[fld]["same"] += 1
            if stem in ANCHORS:
                anchors.append((stem, fld, fo, fn, tv))
        # collateral: any OTHER field that regressed
        for fld in ALL_FIELDS:
            if fld in fields:
                continue
            tv = t.get(fld) or ""
            if match(fld, off.get(fld, ""), tv) and not match(fld, on.get(fld, ""), tv):
                collateral_broke.append((stem, fld))
        if off.get("adjudication") != on.get("adjudication"):
            adjudication_moves.append((stem, off.get("adjudication"),
                                       on.get("adjudication"), t.get("adjudication")))

    print("targeted-field recovery (PSM-3 off -> on, vs truth):")
    tot = Counter()
    for fld in sorted(per_field):
        c = per_field[fld]; tot.update(c)
        print(f"  {fld:16} FIXED {c['FIXED']}  BROKE {c['BROKE']}  same {c['same']}")
    print(f"\nTOTAL over {n_inst} truncated field-instances  "
          f"FIXED {tot['FIXED']}  BROKE {tot['BROKE']}  same {tot['same']}")
    print(f"collateral field regressions (non-targeted): {len(collateral_broke)}"
          + (f"  {collateral_broke[:12]}" if collateral_broke else ""))
    print("adjudication moves on these cases (CFA tripwire):")
    if not adjudication_moves:
        print("  none")
    for stem, o, n, tv in adjudication_moves:
        cfa = " <-- CFA" if n == "APPROVED" and tv == "DENIED" else ""
        print(f"  {stem}: {o} -> {n}  (truth {tv}){cfa}")
    if errors:
        print(f"\nerrors: {len(errors)}  {errors[:5]}")

    print("\nanchors (field: off -> on | truth):")
    for stem, fld, fo, fn, tv in anchors:
        mark = "FIXED" if match(fld, fn, tv) and not match(fld, fo, tv) else \
               "BROKE" if match(fld, fo, tv) and not match(fld, fn, tv) else "same"
        print(f"  {stem} {fld}: '{fo}' -> '{fn}' | truth '{tv}'  [{mark}]")


if __name__ == "__main__":
    main()
