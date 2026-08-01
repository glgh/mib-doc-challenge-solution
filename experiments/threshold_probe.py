#!/usr/bin/env python3
"""Look at the DATA behind every hard threshold, on the CURRENT grid substrate.

Instruments the real matchers (vocab._weighted_closest, match_flag_value) and
runs the real replay(train_grid), so we see the soft score + runner-up margin
behind every snap/flag decision, joined to truth. Answers:

  1. Do the mined true/false gaps still hold now that detection improved? (drift)
  3. Does the discarded margin predict correctness? (keep-prob-longer / confidence)
  4. Which bars have nothing sitting near them? (thresholds we don't need)

Dev split only for correctness stats (holdout stays out).
"""
import csv
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mib import vocab, runner, cache  # noqa: E402

LABELS = ROOT.parent / "mib-doc-challenge" / "data" / "train_labels.csv"
CACHE = ROOT / "output" / "cache" / "train_grid.jsonl"
import json  # noqa: E402
SPLIT = json.load(open(ROOT / "data_splits.json"))

truth = {}
with open(LABELS) as fh:
    for row in csv.DictReader(fh):
        truth[row["case_id"]] = row

DEV = set(SPLIT["dev"])  # canonical frozen dev ids

# ---- instrument the closed-vocab snapper -----------------------------------
_orig_wc = vocab._weighted_closest
CUR = {"case": None}
snap_log = []  # (case, field, s1, s2, margin, best)


def _wc_logged(field, value):
    vn = vocab._norm_vocab(value)
    if not vn:
        return None
    scored = sorted(((vocab._weighted_sim(vn, n), o)
                     for n, o in vocab._WEIGHTED_TABLES[field]), reverse=True)
    (s1, best), s2 = scored[0], scored[1][0]
    snapped = s1 >= vocab._SNAP_BARS[field] and s1 - s2 >= vocab._SNAP_MARGIN
    snap_log.append((CUR["case"], field, s1, s2, s1 - s2, best if snapped else None))
    return best if snapped else None


vocab._weighted_closest = _wc_logged

# ---- instrument the flag value matcher -------------------------------------
_orig_fv = vocab.match_flag_value
flag_log = []  # (case, best_flag, score, margin)


def _fv_logged(value):
    res = _orig_fv(value)
    if res:
        flag_log.append((CUR["case"], res[0], res[1], res[2]))
    return res


vocab.match_flag_value = _fv_logged

# ---- run the real replay, tagging each case --------------------------------
meta, records = cache.read(CACHE)
emitted = {}
n_scan = {}   # scan pages per case (render-coupling confound check)
for rec in records:
    if rec.get("error"):
        continue
    CUR["case"] = rec["stem"]
    n_scan[rec["stem"]] = sum(1 for p in rec["pages"] if p.get("is_scan_only"))
    pages, ocr_lines = cache.to_case(rec["pages"])
    record, _debug = runner.predict_from_evidence(pages, ocr_lines, rec["stem"])
    emitted[rec["stem"]] = record

print(f"replayed {len(emitted)} cases; snap calls {len(snap_log)}; "
      f"flag fires {len(flag_log)}\n")

# ---- DUMP raw rows so the scientific audits run offline (no re-replay) ------
DUMP = ROOT / "output" / "viz" / "threshold_probe_raw.json"
DUMP.write_text(json.dumps({
    "snap": snap_log,       # (case, field, s1, s2, margin, best-or-None)
    "flag": flag_log,       # (case, flag, score, margin)
    "emitted": emitted,     # case -> record
    "n_scan": n_scan,
    "dev": sorted(DEV),
}))
print(f"raw dump -> {DUMP}\n")


def dev(case):
    return case in DEV


# ============================================================================
# (1)+(3)+(4)  CLOSED-VOCAB SNAP: margin vs correctness, per field
# ============================================================================
FIELDS = ["fee_status", "visa_class", "species_code", "home_world",
          "declared_purpose"]
print("=" * 78)
print("SNAP: precision as a function of runner-up margin (dev only)")
print("  bar = _SNAP_BARS[field], _SNAP_MARGIN = 0.05 (the discarded quantity)")
print("=" * 78)
for field in FIELDS:
    bar = vocab._SNAP_BARS[field]
    # snap-fires that actually got EMITTED as this field's value
    fires = [(c, s1, s2, m, best) for (c, f, s1, s2, m, best) in snap_log
             if f == field and best is not None and dev(c)
             and str(emitted.get(c, {}).get(field, "")) == best]
    if not fires:
        print(f"\n{field:16s} bar={bar}: no emitted snaps on dev")
        continue
    # correctness vs truth
    def ok(c, best):
        return truth[c][field].strip().lower() == best.strip().lower()
    n = len(fires)
    corr = sum(ok(c, best) for c, *_r, best in fires)
    # bucket by s1 (absolute score) and by margin
    print(f"\n{field:16s} bar={bar}  emitted-snaps={n}  precision={corr/n:.2f}")
    # near-bar occupancy: how many fires sit within 0.05 above the score bar
    near = [(c, s1, m, best) for (c, s1, s2, m, best) in fires if s1 - bar < 0.05]
    near_ok = sum(ok(c, best) for c, s1, m, best in near)
    print(f"   within 0.05 of score-bar: {len(near)}  "
          f"(correct {near_ok}/{len(near)}) "
          f"-> {'BAR IS LIVE' if near else 'nothing near bar'}")
    # margin buckets
    for lo, hi in [(0.05, 0.15), (0.15, 0.30), (0.30, 0.60), (0.60, 9)]:
        b = [(c, best) for (c, s1, s2, m, best) in fires if lo <= m < hi]
        if b:
            bo = sum(ok(c, best) for c, best in b)
            print(f"   margin [{lo:.2f},{hi:.2f}): n={len(b):3d}  "
                  f"correct={bo/len(b):.2f}")

# ============================================================================
# (1)  FLAG VALUE BARS: is the true/false band still empty on the grid?
# ============================================================================
print("\n" + "=" * 78)
print("FLAG value matches: score/margin split by TRUTH (does the flag hold?)")
print("  shipped bars: single 0.55/0.15 ; quorum 0.44/0.10 ; band 0.40-0.44")
print("=" * 78)
tp, fp = [], []
for (c, flag, score, margin) in flag_log:
    if not dev(c):
        continue
    has = flag in truth[c]["risk_flags"].split("|")
    (tp if has else fp).append((score, margin, c, flag))
def dist(rows, name):
    if not rows:
        print(f"  {name}: none"); return
    ss = sorted(r[0] for r in rows)
    print(f"  {name}: n={len(rows)}  score min={ss[0]:.2f} "
          f"p10={ss[len(ss)//10]:.2f} med={ss[len(ss)//2]:.2f} max={ss[-1]:.2f}")
dist(tp, "TRUE  (flag really present)")
dist(fp, "FALSE (flag not in truth)  <- these are the ones the bar must exclude")
band = [(s, m, c, f) for (s, m, c, f) in tp + fp if 0.40 <= s <= 0.44]
print(f"  in the 'empty' 0.40-0.44 band: {len(band)}")
for s, m, c, f in sorted(band)[:12]:
    has = f in truth[c]["risk_flags"].split("|")
    print(f"     {c} {f:20s} score={s:.3f} margin={m:.3f} "
          f"truth={'HAS' if has else 'no '}")
