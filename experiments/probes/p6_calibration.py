#!/usr/bin/env python3
"""P6 — probabilistic calibration. Can a P(correct) model beat the shipped
per-branch fitted confidence on Brier (holdout)? Calibration = 20*max(0,1-2*Brier)."""
import csv, json, os, sys
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.isotonic import IsotonicRegression

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
# A replay dir (predictions.jsonl + debug.jsonl) built by scripts/replay.py.
SP = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "output", "replay_post")
truth = {r["case_id"]: r["adjudication"] for r in
         csv.DictReader(open(os.path.join(ROOT, "..", "mib-doc-challenge", "data", "train_labels.csv")))}
pred = {json.loads(l)["case_id"]: json.loads(l) for l in open(f"{SP}/predictions.jsonl")}
dbg = {json.loads(l)["case_id"]: json.loads(l) for l in open(f"{SP}/debug.jsonl")}
splits = json.load(open(os.path.join(ROOT, "data_splits.json")))

def ids(key):
    v = splits[key]
    return v if isinstance(v, list) else v.get("ids") or v.get("cases") or list(v)
dev, hold = set(ids("dev")), set(ids("holdout"))
print(f"splits: dev {len(dev)}, holdout {len(hold)}")

BRANCHES = sorted({dbg[c]["branch"] for c in dbg})
def feats(c):
    d = dbg[c]
    onehot = [1.0 if d["branch"] == b else 0.0 for b in BRANCHES]
    return onehot + [
        float(d.get("n_fields_missing", 0)), float(bool(d.get("has_biometric"))),
        float(bool(d.get("has_flag_evidence"))), float(len(d.get("deny_hits", []))),
        float(len(d.get("review_hits", []))), float(d.get("n_corrections", 0)),
        float(d.get("n_pages", 0)), float(d.get("scan_only_pages", 0)),
        float(len(d.get("flags", []))), float(len(d.get("emit_flags", []))),
    ]

def rows(idset):
    X, y, conf = [], [], []
    for c in idset:
        if c not in pred or c not in truth:
            continue
        X.append(feats(c)); y.append(int(pred[c]["adjudication"] == truth[c]))
        conf.append(float(pred[c]["confidence"]))
    return np.array(X), np.array(y), np.array(conf)

Xd, yd, cd = rows(dev)
Xh, yh, ch = rows(hold)

def brier(p, y): return float(np.mean((p - y) ** 2))
def cal_pts(p, y): return 20 * max(0.0, 1 - 2 * brier(p, y))

# 1) shipped confidence (baseline)
b_ship = brier(ch, yh)
# 2) isotonic recalibration of the shipped confidence (fit dev, apply holdout)
iso = IsotonicRegression(out_of_bounds="clip").fit(cd, yd)
p_iso = iso.predict(ch)
# 3) logistic on features
lr = LogisticRegression(max_iter=2000, C=1.0).fit(Xd, yd)
p_lr = lr.predict_proba(Xh)[:, 1]
# 4) logistic + shipped confidence as an extra feature
lr2 = LogisticRegression(max_iter=2000, C=1.0).fit(np.c_[Xd, cd], yd)
p_lr2 = lr2.predict_proba(np.c_[Xh, ch])[:, 1]

print(f"\nholdout accuracy of the adjudicator: {yh.mean():.3f}  (n={len(yh)})")
print(f"{'model':32} {'Brier':>7} {'cal/20':>7} {'Δcal':>6}")
base = cal_pts(ch, yh)
for name, p in [("shipped confidence (baseline)", ch),
                ("isotonic recal of shipped", p_iso),
                ("logistic on debug features", p_lr),
                ("logistic + shipped conf", p_lr2)]:
    print(f"{name:32} {brier(p,yh):7.4f} {cal_pts(p,yh):7.2f} {cal_pts(p,yh)-base:+6.2f}")

best = max(cal_pts(p, yh) for p in [p_iso, p_lr, p_lr2])
print(f"\nGO/NO-GO (P6): best holdout gain >= 0.3 cal pts -> "
      f"{'GO' if best-base >= 0.3 else 'NO-GO'} ({best-base:+.2f})")
