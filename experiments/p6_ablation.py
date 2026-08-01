#!/usr/bin/env python3
"""P6 graduation ablation: reproduce the +0.74 holdout calibration win, then
decompose it into render-coupled vs content features and pick the SAFE model.

Why this matters: the winning probe model uses n_pages / scan_only_pages /
has_biometric — the render-derived features the STATUS hazard bars decision
models from, because the generator couples scan damage to the label and that
coupling HALVES on validation (row 24). Holdout cannot catch this (holdout and
dev share the train damage rate). So before shipping we ask: does a RENDER-FREE
P(correct) still beat the shipped per-branch confidence on holdout, and by how
much? Ship the render-free model if it clears the bar; only fall back to the
full model with eyes open if render features are load-bearing.

Reads a current replay (predictions.jsonl + debug.jsonl over train_grid).
Usage: .venv/bin/python experiments/p6_ablation.py [replay_dir]
"""
import csv
import json
import sys
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.isotonic import IsotonicRegression
from sklearn.model_selection import StratifiedKFold

ROOT = Path(__file__).resolve().parent.parent
RD = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/p6_replay")
truth = {r["case_id"]: r["adjudication"] for r in
         csv.DictReader(open(ROOT.parent / "mib-doc-challenge/data/train_labels.csv"))}
pred = {json.loads(l)["case_id"]: json.loads(l) for l in open(RD / "predictions.jsonl")}
dbg = {json.loads(l)["case_id"]: json.loads(l) for l in open(RD / "debug.jsonl")}
sp = json.load(open(ROOT / "data_splits.json"))
dev, hold = set(sp["dev"]), set(sp["holdout"])
print(f"replay {RD}  |  dev {len(dev)} holdout {len(hold)}\n")

BRANCHES = sorted({dbg[c]["branch"] for c in dbg})

# feature groups -----------------------------------------------------------
def content_feats(d):
    return {
        "n_fields_missing": float(d.get("n_fields_missing", 0)),
        "n_deny_hits": float(len(d.get("deny_hits", []))),
        "n_review_hits": float(len(d.get("review_hits", []))),
        "n_corrections": float(d.get("n_corrections", 0)),
        "n_flags": float(len(d.get("flags", []))),
        "n_emit_flags": float(len(d.get("emit_flags", []))),
    }

def render_feats(d):
    return {
        "n_pages": float(d.get("n_pages", 0)),
        "scan_only_pages": float(d.get("scan_only_pages", 0)),
        "has_biometric": float(bool(d.get("has_biometric"))),
    }

CONTENT = list(content_feats(next(iter(dbg.values()))).keys())
RENDER = list(render_feats(next(iter(dbg.values()))).keys())


def vec(c, use_content, use_render):
    d = dbg[c]
    onehot = [1.0 if d["branch"] == b else 0.0 for b in BRANCHES]
    x = list(onehot)
    if use_content:
        x += [content_feats(d)[k] for k in CONTENT]
    if use_render:
        x += [render_feats(d)[k] for k in RENDER]
    return x


def rows(idset, use_content, use_render):
    X, y, conf = [], [], []
    for c in idset:
        if c not in pred or c not in truth:
            continue
        X.append(vec(c, use_content, use_render))
        y.append(int(pred[c]["adjudication"] == truth[c]))
        conf.append(float(pred[c]["confidence"]))
    return np.array(X), np.array(y), np.array(conf)


brier = lambda p, y: float(np.mean((p - y) ** 2))
cal = lambda p, y: 20 * max(0.0, 1 - 2 * brier(p, y))

_, yh_all, ch = rows(hold, False, False)
base = cal(ch, yh_all)
print(f"holdout adjudicator accuracy {yh_all.mean():.3f}   shipped conf: "
      f"Brier {brier(ch, yh_all):.4f}  cal {base:.2f}/20\n")

# isotonic recal of shipped conf (a monotone reshape, no new features) -----
Xd0, yd0, cd = rows(dev, False, False)
iso = IsotonicRegression(out_of_bounds="clip").fit(cd, yd0)
print(f"{'model':34} {'Brier':>7} {'cal/20':>7} {'Δcal':>6}")
print(f"{'isotonic recal of shipped conf':34} {brier(iso.predict(ch), yh_all):7.4f} "
      f"{cal(iso.predict(ch), yh_all):7.2f} {cal(iso.predict(ch), yh_all)-base:+6.2f}")

# logistic variants: each with and without the shipped conf as an extra col --
VARIANTS = [
    ("logit content-only",        True,  False),
    ("logit content+render(FULL)", True,  True),
    ("logit render-only",         False, True),
]
results = {}
for name, uc, ur in VARIANTS:
    Xd, yd, _ = rows(dev, uc, ur)
    Xh, yh, _ = rows(hold, uc, ur)
    for addconf in (False, True):
        Xd2 = np.c_[Xd, cd] if addconf else Xd
        Xh2 = np.c_[Xh, ch] if addconf else Xh
        lr = LogisticRegression(max_iter=5000, C=1.0).fit(Xd2, yd)
        ph = lr.predict_proba(Xh2)[:, 1]
        tag = name + (" +conf" if addconf else "")
        results[tag] = cal(ph, yh) - base
        print(f"{tag:34} {brier(ph, yh):7.4f} {cal(ph, yh):7.2f} {cal(ph, yh)-base:+6.2f}")

# within-dev 5-fold CV for the two shippable candidates (variance caveat) ----
print("\n# within-dev 5-fold OOF (variance check; +conf variants)")
for name, uc, ur in [("content-only +conf", True, False), ("FULL +conf", True, True)]:
    X, y, c = rows(dev, uc, ur)
    Xc = np.c_[X, c]
    skf = StratifiedKFold(5, shuffle=True, random_state=8090)
    oof = np.zeros(len(y))
    for tr, te in skf.split(Xc, y):
        oof[te] = LogisticRegression(max_iter=5000, C=1.0).fit(Xc[tr], y[tr]).predict_proba(Xc[te])[:, 1]
    ship_dev = cal(c, y)
    print(f"  {name:22} OOF cal {cal(oof, y):.2f}  vs shipped {ship_dev:.2f}  "
          f"Δ {cal(oof, y)-ship_dev:+.2f}")

print("\nDECISION: ship the render-free (content-only) model if its holdout Δ "
      "clears ~+0.3 and its CV Δ is positive; the render gain is not bankable "
      "(damage halves on val).")
