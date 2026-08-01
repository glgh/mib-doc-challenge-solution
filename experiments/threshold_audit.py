#!/usr/bin/env python3
"""Scientific audit of threshold_probe's conclusions. Consumes the raw dump so
every check reruns in <1s. Each check tries to BREAK a claim, not confirm it."""
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = json.load(open(ROOT / "output" / "viz" / "threshold_probe_raw.json"))
truth = {r["case_id"]: r for r in
         csv.DictReader(open(ROOT.parent / "mib-doc-challenge" /
                             "data" / "train_labels.csv"))}
DEV = set(RAW["dev"])
snap = RAW["snap"]          # [case, field, s1, s2, margin, best]
flag = RAW["flag"]          # [case, flag, score, margin]
emitted = RAW["emitted"]
n_scan = RAW["n_scan"]


def line(t):
    print(t)


# ============================================================================
# CHECK 0 — did the monkeypatch change pipeline behavior? (probe validity)
# ============================================================================
line("=" * 78)
line("CHECK 0  probe faithfulness: in-probe emissions vs committed grid replay")
ref_path = ROOT / "output" / "eval_head" / "predictions.jsonl"
ref = {json.loads(l)["case_id"]: json.loads(l) for l in open(ref_path)}
FIELDS12 = ["applicant_name", "species_code", "home_world", "visa_class",
            "sponsor_id", "arrival_date", "declared_purpose", "risk_flags",
            "fee_status", "adjudication"]
diff = 0
for c, rec in emitted.items():
    r = ref.get(c)
    if not r:
        continue
    for f in FIELDS12:
        if str(rec.get(f, "")) != str(r.get(f, "")):
            diff += 1
line(f"  field-level diffs vs eval_head: {diff}  "
     f"({'CLEAN — probe did not alter behavior' if diff == 0 else 'PROBE ALTERS OUTPUT — suspect'})")

# ============================================================================
# CHECK A — holdout leakage on the flag-band cases
# ============================================================================
line("\n" + "=" * 78)
line("CHECK A  leakage: are the flag-band cases dev or holdout?")
for c in ["MIB-000252", "MIB-000577", "MIB-000990", "MIB-000376", "MIB-000657"]:
    line(f"  {c}: {'DEV' if c in DEV else 'HOLDOUT — cannot tune on this'}")

# ============================================================================
# CHECK B — per-CASE (not per-read) fee margin vs precision
# ============================================================================
line("\n" + "=" * 78)
line("CHECK B  fee margin->precision, deduped to ONE row per case (max-margin fire)")
line("  claim under test: high margin => LOWER precision (the inverted signal)")
by_case = {}
for c, f, s1, s2, m, best in snap:
    if f != "fee_status" or best is None or c not in DEV:
        continue
    if str(emitted.get(c, {}).get("fee_status", "")) != best:
        continue
    if c not in by_case or m > by_case[c][0]:
        by_case[c] = (m, best)
buckets = [(0.05, 0.15), (0.15, 0.30), (0.30, 0.60), (0.60, 9)]
for lo, hi in buckets:
    rows = [(c, best) for c, (m, best) in by_case.items() if lo <= m < hi]
    if rows:
        ok = sum(truth[c]["fee_status"].lower() == b.lower() for c, b in rows)
        line(f"    margin [{lo:.2f},{hi:.2f}): cases={len(rows):3d}  prec={ok/len(rows):.2f}")

# ============================================================================
# CHECK C — render-coupling confound: is high fee margin just n_scan_pages?
# ============================================================================
line("\n" + "=" * 78)
line("CHECK C  render confound: does high fee margin proxy scan-pages / DENIED?")
line("  if margin tracks scan-ness AND DENIED, it's the forbidden render leak")
rows = []
for c, (m, best) in by_case.items():
    rows.append((m, n_scan.get(c, 0), truth[c]["adjudication"], best,
                 truth[c]["fee_status"]))
for lo, hi in buckets:
    b = [r for r in rows if lo <= r[0] < hi]
    if not b:
        continue
    scan = sum(1 for r in b if r[1] > 0) / len(b)
    den = sum(1 for r in b if r[2] == "DENIED") / len(b)
    prec = sum(1 for r in b if r[4].lower() == r[3].lower()) / len(b)
    line(f"    margin [{lo:.2f},{hi:.2f}): n={len(b):3d}  "
         f"has-scan={scan:.2f}  DENIED={den:.2f}  fee-prec={prec:.2f}")
# is the wrongness explained by scan-ness alone? partial view: within scanned only
line("  -- restricted to cases WITH scan pages (holds render state ~fixed):")
scanned = [r for r in rows if r[1] > 0]
for lo, hi in buckets:
    b = [r for r in scanned if lo <= r[0] < hi]
    if b:
        prec = sum(1 for r in b if r[4].lower() == r[3].lower()) / len(b)
        line(f"    margin [{lo:.2f},{hi:.2f}): n={len(b):3d}  fee-prec={prec:.2f}")

# ============================================================================
# CHECK D — survivorship: snap precision-by-margin WITHOUT emitted==best filter
# ============================================================================
line("\n" + "=" * 78)
line("CHECK D  survivorship: fee margin->precision on ALL fires (no merge-win filter)")
line("  (per-read; compares to the emitted-only view to size the selection bias)")
allf = {}
for c, f, s1, s2, m, best in snap:
    if f != "fee_status" or best is None or c not in DEV:
        continue
    # correctness of the SNAP itself vs truth, regardless of whether it won
    key = (c, best, round(m, 3))
    allf[key] = (m, truth[c]["fee_status"].lower() == best.lower())
for lo, hi in buckets:
    b = [v for v in allf.values() if lo <= v[0] < hi]
    if b:
        ok = sum(x[1] for x in b) / len(b)
        line(f"    margin [{lo:.2f},{hi:.2f}): fires={len(b):3d}  snap-correct={ok:.2f}")

# ============================================================================
# CHECK E — does bar 0.40 + margin>=0.10 actually give quorum(N>=2) on the losses?
# ============================================================================
line("\n" + "=" * 78)
line("CHECK E  flag recovery feasibility: reads with score>=0.40 & margin>=0.10")
line("  quorum needs N>=2 such reads for the TRUE flag; also count NEW phantoms")
# per (case, flag): count qualifying reads at proposed bar
from collections import defaultdict
q = defaultdict(int)
for c, fl, s, m in flag:
    if s >= 0.40 and m >= 0.10:
        q[(c, fl)] += 1
# recoveries: dev cases whose truth has a flag now emitted as none/missing, reachable
line("  candidate TRUE-flag recoveries (dev, truth-has, currently not emitted):")
rec_n = 0
for (c, fl), cnt in sorted(q.items()):
    if c not in DEV:
        continue
    has = fl in truth[c]["risk_flags"].split("|")
    now = fl in str(emitted.get(c, {}).get("risk_flags", "")).split("|")
    if has and not now and cnt >= 2:
        line(f"     RECOVER {c} {fl:22s} qualifying-reads={cnt}")
        rec_n += 1
# phantoms: qualifying quorum for a flag truth does NOT have
line("  NEW PHANTOMS this bar would admit (dev, truth-lacks, quorum>=2):")
ph_n = 0
for (c, fl), cnt in sorted(q.items()):
    if c not in DEV:
        continue
    has = fl in truth[c]["risk_flags"].split("|")
    now = fl in str(emitted.get(c, {}).get("risk_flags", "")).split("|")
    if not has and not now and cnt >= 2:
        line(f"     PHANTOM  {c} {fl:22s} qualifying-reads={cnt}")
        ph_n += 1
line(f"  => dev recoveries {rec_n} / new phantoms {ph_n} "
     f"(this is the score bar 0.44->0.40 A/B, approximated)")
