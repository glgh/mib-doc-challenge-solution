#!/usr/bin/env python3
"""Lightweight-but-sound threshold sweep: for each candidate bar value, run the
REAL replay (dev-only) end-to-end and score with the OFFICIAL evaluator. No
intermediate-population inference (that's what misled the first probe) — every
number here is a scored dev-700 result on the grid substrate.

Loads the cache once and reuses parsed pages across values, so a whole batch is
a few minutes. Patches are closures that mutate a live constant and return an
undo, so baseline is exactly restored between runs.
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CH = ROOT.parent / "mib-doc-challenge"
from mib import cache, runner, emit, vocab, parse, signals  # noqa: E402

CACHE = ROOT / "output" / "cache" / "train_grid.jsonl"
DEV = set(json.loads((ROOT / "data_splits.json").read_text())["dev"])
WORK = Path("/tmp/thr_sweep")
WORK.mkdir(exist_ok=True)

# --- load + parse dev cases once --------------------------------------------
_meta, records = cache.read(CACHE)
CASES = []  # (stem, pages, ocr_lines)
for rec in records:
    if rec.get("error") or rec["stem"] not in DEV:
        continue
    pages, ocr_lines = cache.to_case(rec["pages"])
    CASES.append((rec["stem"], pages, ocr_lines))
print(f"loaded {len(CASES)} dev cases\n")


import csv  # noqa: E402
# dev-filtered truth, written once — so normalization scales against the dev max
# (score_split does this; passing full truth deflates the absolute numbers).
_TRUTH_DEV = WORK / "truth_dev.csv"
_rows = [r for r in csv.DictReader(open(CH / "data/train_labels.csv"))
         if r["case_id"] in DEV]
with open(_TRUTH_DEV, "w", newline="") as _f:
    _w = csv.DictWriter(_f, fieldnames=_rows[0].keys())
    _w.writeheader(); _w.writerows(_rows)


def score(return_records=False):
    """Replay all dev cases at the current constants, return scored dict."""
    out = []
    for stem, pages, ocr_lines in CASES:
        record, _ = runner.predict_from_evidence(pages, ocr_lines, stem)
        out.append(record)
    out = emit.dedupe(out)
    (WORK / "predictions.jsonl").write_text(
        "".join(json.dumps(r, sort_keys=True) + "\n" for r in out))
    subprocess.run([sys.executable, str(CH / "scripts/evaluate.py"),
                    "--truth", str(_TRUTH_DEV),
                    "--submission", str(WORK / "predictions.jsonl"),
                    "--output-json", str(WORK / "ev.json")],
                   capture_output=True)
    d = json.load(open(WORK / "ev.json"))
    res = {"total": d["scores"]["total_score"],
           "class": d["scores"]["classification_score"],
           "extr": d["scores"]["extraction_score"],
           "calib": d["scores"]["calibration_score"],
           "cfa": d["raw"]["catastrophic_false_approvals"]}
    if return_records:
        res["records"] = {r["case_id"]: r for r in out}
    return res


# --- candidate patches (label -> apply(), returns undo()) --------------------
def set_snap_bar(field, val):
    def apply():
        old = vocab._SNAP_BARS[field]
        vocab._SNAP_BARS[field] = val
        return lambda: vocab._SNAP_BARS.__setitem__(field, old)
    return apply

def set_attr(mod, name, val):
    def apply():
        old = getattr(mod, name)
        setattr(mod, name, val)
        return lambda: setattr(mod, name, old)
    return apply

CANDIDATES = [
    ("baseline", None),
    # home_world is the only mover in pass 1; map its curve at true magnitude
    ("world_bar 0.70->0.65", set_snap_bar("home_world", 0.65)),
    ("world_bar 0.70->0.60", set_snap_bar("home_world", 0.60)),
    ("world_bar 0.70->0.55", set_snap_bar("home_world", 0.55)),
    # re-confirm the two other non-zero pass-1 rows at true (dev) magnitude
    ("loose_key 0.55->0.45", set_attr(parse, "LOOSE_KEY_CUTOFF", 0.45)),
    ("header_fuzzy 0.66->0.72", set_attr(parse, "HEADER_FUZZY_CUTOFF", 0.72)),
]

EMBARGO = set(getattr(__import__("mib.policy", fromlist=["FULL_EMBARGO_WORLDS"]),
                      "FULL_EMBARGO_WORLDS", set()))
base = None
base_rec = None
print(f"{'candidate':28s} {'total':>8} {'Δtot':>7} {'class':>7} {'extr':>7} "
      f"{'calib':>7} {'cfa':>4}")
print("-" * 74)
for label, apply in CANDIDATES:
    undo = apply() if apply else (lambda: None)
    try:
        s = score(return_records=(label == "baseline" or "world_bar 0.70->0.60" in label))
    finally:
        undo()
    if base is None:
        base = s["total"]; base_rec = s["records"]
    d = s["total"] - base
    flag = "  <-- CFA!" if s["cfa"] else ("  *" if abs(d) >= 0.03 else "")
    print(f"{label:28s} {s['total']:8.2f} {d:+7.2f} {s['class']:7.2f} "
          f"{s['extr']:7.2f} {s['calib']:7.2f} {s['cfa']:4d}{flag}")
    # --- embargo-safety drill on the world_bar=0.60 candidate ---
    if "world_bar 0.70->0.60" in label:
        rec = s["records"]
        truth = {r["case_id"]: r for r in _rows}
        print("   home_world / adjudication changes vs baseline:")
        for c in sorted(rec):
            b, n = base_rec[c], rec[c]
            if b["home_world"] != n["home_world"] or b["adjudication"] != n["adjudication"]:
                hw_ok = "=truth" if n["home_world"] == truth[c]["home_world"] else "!=truth(%s)" % truth[c]["home_world"]
                emb = " EMBARGO-WORLD" if n["home_world"] in EMBARGO else ""
                adjm = "" if b["adjudication"] == n["adjudication"] else \
                    f"  ADJ {b['adjudication']}->{n['adjudication']} (truth {truth[c]['adjudication']})"
                print(f"     {c}: world {b['home_world']!r}->{n['home_world']!r} "
                      f"{hw_ok}{emb}{adjm}")
