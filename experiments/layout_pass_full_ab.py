#!/usr/bin/env python3
"""Full-corpus A/B for the layout-pass tier (TODO 6.7), from ONE armed cache.

Replays an armed grid cache (with the PSM-3 reads) two ways through the real
downstream pipeline: reads as-is (`on`) and with the `+psm3` reads dropped
(`off`). Same code, same reads bar the pass -> the layout pass in isolation, no
second regen and no cross-rev baseline needed.

Writes score-ready dirs (out_on/, out_off/ with predictions.jsonl + debug.jsonl)
for `scripts/score_split.py`, and prints the on-vs-off diff against truth:
FIXED/BROKE per field, adjudication moves, and the CFA tripwire, split-aware.

Usage: layout_pass_full_ab.py [--cache train_grid.jsonl] [--out-dir output/lp_ab]
"""
import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CH = ROOT.parent / "mib-doc-challenge"

from mib import cache, emit, runner  # noqa: E402

EXTRACT = ["applicant_name", "species_code", "home_world", "visa_class",
           "sponsor_id", "arrival_date", "declared_purpose", "risk_flags",
           "fee_status"]


def norm(s):
    return " ".join(str(s or "").strip().split()).casefold()


def match(field, a, b):
    if field == "risk_flags":
        return set(norm(a).split()) == set(norm(b).split())
    return norm(a) == norm(b)


def _drop_psm3(rbp):
    return {p: [r for r in reads if not r.variant.endswith("+psm3")]
            for p, reads in rbp.items()}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cache", default=str(ROOT / "output/cache/train_grid.jsonl"))
    ap.add_argument("--out-dir", default=str(ROOT / "output/lp_ab"))
    a = ap.parse_args()
    out = Path(a.out_dir)
    (out / "on").mkdir(parents=True, exist_ok=True)
    (out / "off").mkdir(parents=True, exist_ok=True)

    truth = {r["case_id"]: r for r in csv.DictReader(open(CH / "data/train_labels.csv"))}
    splits = json.loads((ROOT / "data_splits.json").read_text())
    split_of = {c: "dev" for c in splits["dev"]}
    split_of.update({c: "holdout" for c in splits["holdout"]})

    meta, records = cache.read(a.cache)
    on_recs, off_recs, on_dbg, off_dbg = [], [], [], []
    fired_cases = 0
    for rec in records:
        if rec.get("error"):
            continue
        pages, rbp = cache.to_case(rec["pages"])
        base = _drop_psm3(rbp)
        if sum(len(v) for v in rbp.values()) != sum(len(v) for v in base.values()):
            fired_cases += 1
        r_on, d_on = runner.predict_from_evidence(pages, rbp, rec["stem"])
        r_off, d_off = runner.predict_from_evidence(pages, base, rec["stem"])
        on_recs.append(r_on); off_recs.append(r_off)
        on_dbg.append(d_on); off_dbg.append(d_off)
    on_recs, off_recs = emit.dedupe(on_recs), emit.dedupe(off_recs)

    for name, recs_, dbg in (("on", on_recs, on_dbg), ("off", off_recs, off_dbg)):
        (out / name / "predictions.jsonl").write_text(
            "".join(json.dumps(r, sort_keys=True) + "\n" for r in recs_))
        (out / name / "debug.jsonl").write_text(
            "".join(json.dumps(d, sort_keys=True) + "\n" for d in dbg))

    on = {r["case_id"]: r for r in on_recs}
    off = {r["case_id"]: r for r in off_recs}
    field_delta = defaultdict(lambda: Counter())      # per split -> Counter over "field:FIXED/BROKE"
    adj = []
    cfa_on = cfa_off = 0
    for cid in on:
        if cid not in truth:
            continue
        sp = split_of.get(cid, "?")
        t, o, f = truth[cid], on[cid], off.get(cid, {})
        for fld in EXTRACT:
            tv = t.get(fld) or ""
            if match(fld, o.get(fld, ""), tv) and not match(fld, f.get(fld, ""), tv):
                field_delta[sp][f"{fld}:FIXED"] += 1
            elif match(fld, f.get(fld, ""), tv) and not match(fld, o.get(fld, ""), tv):
                field_delta[sp][f"{fld}:BROKE"] += 1
        if o.get("adjudication") != f.get("adjudication"):
            adj.append((sp, cid, f.get("adjudication"), o.get("adjudication"),
                        t.get("adjudication")))
        cfa_on += (o.get("adjudication") == "APPROVED" and t.get("adjudication") == "DENIED")
        cfa_off += (f.get("adjudication") == "APPROVED" and t.get("adjudication") == "DENIED")

    print(f"cache: {a.cache}")
    print(f"cases where the pass fired (>=1 psm3 read): {fired_cases}")
    print(f"wrote {out}/on and {out}/off  ->  score with: "
          f"scripts/score_split.py {out}/on dev  (and .../off dev)\n")

    for sp in ("dev", "holdout"):
        c = field_delta[sp]
        fixed = sum(v for k, v in c.items() if k.endswith("FIXED"))
        broke = sum(v for k, v in c.items() if k.endswith("BROKE"))
        detail = "  ".join(f"{k}={v}" for k, v in sorted(c.items()))
        print(f"[{sp}] field FIXED {fixed} / BROKE {broke}   {detail}")

    print(f"\nCFA (false approvals): off {cfa_off} -> on {cfa_on}")
    print("adjudication moves (pass off -> on | truth):")
    if not adj:
        print("  none")
    for sp, cid, o, n, tv in adj:
        tag = " <-- NEW CFA" if n == "APPROVED" and tv == "DENIED" else \
              " (correct)" if n == tv else ""
        print(f"  [{sp}] {cid}: {o} -> {n} | truth {tv}{tag}")


if __name__ == "__main__":
    main()
