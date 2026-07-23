#!/usr/bin/env python3
"""Step-0 decision-layer bake-off: MLP vs rules cascade on identical signals.

Builds a feature matrix from output/eval/{debug,predictions}.jsonl (run
eval_local.sh first), does 5-fold stratified CV WITHIN THE DEV SPLIT ONLY,
turns out-of-fold probabilities into decisions via expected-points argmax,
and scores classification raw points + Brier against the rules baseline.

Payoff (truth, pred): correct=8; A/D->NR retreat=2; NR missed=1; A<->D=0;
false approval of DENIED=-4.

Ablations: MLP with/without rules-branch features; multinomial logistic sanity.
"""
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CH = ROOT.parent / "mib-doc-challenge"

from mib.features import featurize  # noqa: E402  (single source of truth)

CLASSES = ["APPROVED", "DENIED", "NEEDS_REVIEW"]

PAYOFF = {}  # (truth, pred) -> raw points
for t in CLASSES:
    for p in CLASSES:
        if t == p:
            PAYOFF[t, p] = 8.0
        elif p == "NEEDS_REVIEW":
            PAYOFF[t, p] = 2.0
        elif t == "NEEDS_REVIEW":
            PAYOFF[t, p] = 1.0
        elif t == "DENIED" and p == "APPROVED":
            PAYOFF[t, p] = -4.0
        else:
            PAYOFF[t, p] = 0.0


def expected_points_decision(probs):
    """probs in CLASSES order -> (decision, expected points per class)."""
    pa, pd, pn = probs
    ev = {
        "APPROVED": 8 * pa + 1 * pn - 4 * pd,
        "DENIED": 8 * pd + 1 * pn + 0 * pa,
        "NEEDS_REVIEW": 8 * pn + 2 * pa + 2 * pd,
    }
    return max(ev, key=ev.get), ev


def score_decisions(decisions, confs, truths):
    pts = sum(PAYOFF[t, d] for d, t in zip(decisions, truths))
    briers = [(c - (d == t)) ** 2 for d, c, t in zip(decisions, confs, truths)]
    cfas = sum(1 for d, t in zip(decisions, truths) if t == "DENIED" and d == "APPROVED")
    acc = sum(d == t for d, t in zip(decisions, truths)) / len(truths)
    return pts, float(np.mean(briers)), cfas, acc


def _proba(m, X):
    cols = {c: i for i, c in enumerate(m.classes_)}
    p = m.predict_proba(X)
    return np.stack([p[:, cols[c]] if c in cols else np.zeros(len(X))
                     for c in CLASSES], axis=1)


def _correctness_calibrator(model_fn, X, y, seed=8090):
    """Fit conf = P(EV-decision correct | raw P(chosen)) via inner CV — honest,
    mirrors the rules' fitted per-branch empirical confidence."""
    inner = StratifiedKFold(n_splits=3, shuffle=True, random_state=seed)
    raw, correct = [], []
    for tr, te in inner.split(X, y):
        m = model_fn()
        m.fit(X[tr], y[tr])
        for row, t in zip(_proba(m, X[te]), y[te]):
            d, _ = expected_points_decision(row)
            raw.append(row[CLASSES.index(d)])
            correct.append(float(d == t))
    lr = LogisticRegression(max_iter=1000)
    lr.fit(np.array(raw).reshape(-1, 1), np.array(correct))
    return lambda c: float(np.clip(lr.predict_proba([[c]])[0, 1], 0.05, 0.95))


def run_model(name, model_fn, X, y, truths, folds, conf_cal=False):
    oof = np.zeros((len(y), 3))
    confs = np.zeros(len(y))
    for tr, te in folds:
        m = model_fn()
        m.fit(X[tr], y[tr])
        oof[te] = _proba(m, X[te])
        calfn = _correctness_calibrator(model_fn, X[tr], y[tr]) if conf_cal else None
        for i in te:
            d, _ = expected_points_decision(oof[i])
            c = oof[i][CLASSES.index(d)]
            confs[i] = calfn(c) if calfn else c
    decisions = [expected_points_decision(row)[0] for row in oof]
    pts, brier, cfas, acc = score_decisions(decisions, confs, truths)
    print(f"{name:34s} raw={pts:7.1f} ({pts / len(y) / 8 * 80:5.2f}/80eq) "
          f"acc={acc:.3f} brier={brier:.4f} CFA={cfas}")
    return decisions, oof


def main(eval_dir=ROOT / "output/eval"):
    eval_dir = Path(eval_dir)
    meta_path = eval_dir / "meta.json"
    if meta_path.exists():
        import mib.config
        mib.config.require_agreement([(str(eval_dir), json.loads(meta_path.read_text()))])
    dev = sorted(json.loads((ROOT / "data_splits.json").read_text())["dev"])
    truth = {r["case_id"]: r["adjudication"]
             for r in csv.DictReader(open(CH / "data/train_labels.csv"))}
    preds = {r["case_id"]: r for r in map(json.loads, open(eval_dir / "predictions.jsonl"))}
    dbgs = {r["case_id"]: r for r in map(json.loads, open(eval_dir / "debug.jsonl"))}
    cases = [c for c in dev if c in preds and c in dbgs]

    truths = [truth[c] for c in cases]
    y = np.array(truths)

    def matrix(with_branch):
        feats = [featurize(preds[c], dbgs[c], with_branch) for c in cases]
        keys = sorted(feats[0])
        return np.array([[float(f[k]) for k in keys] for f in feats]), keys

    X_full, keys_full = matrix(True)
    X_nobr, _ = matrix(False)
    print(f"dev cases: {len(cases)}; features: {len(keys_full)} (with branch), "
          f"{X_nobr.shape[1]} (without)")

    # Rules baseline (same cases, same metric)
    rules_dec = [preds[c]["adjudication"] for c in cases]
    rules_conf = [preds[c]["confidence"] for c in cases]
    pts, brier, cfas, acc = score_decisions(rules_dec, rules_conf, truths)
    print(f"{'RULES baseline':34s} raw={pts:7.1f} ({pts / len(cases) / 8 * 80:5.2f}/80eq) "
          f"acc={acc:.3f} brier={brier:.4f} CFA={cfas}\n")

    folds = list(StratifiedKFold(n_splits=5, shuffle=True, random_state=8090).split(X_full, y))

    def mlp():
        return make_pipeline(StandardScaler(), MLPClassifier(
            hidden_layer_sizes=(32,), alpha=1e-3, max_iter=3000, random_state=8090))

    def logit():
        return make_pipeline(StandardScaler(), LogisticRegression(
            max_iter=5000, C=1.0, random_state=8090))

    from sklearn.calibration import CalibratedClassifierCV

    def cal(base_fn):
        # Sigmoid calibration fit via CV inside the training fold — no test leakage.
        return lambda: CalibratedClassifierCV(base_fn(), cv=3, method="sigmoid")

    def mlp_small():
        return make_pipeline(StandardScaler(), MLPClassifier(
            hidden_layer_sizes=(16,), alpha=1.0, max_iter=3000, random_state=8090))

    mlp_dec, mlp_oof = run_model("MLP(32) + branch features", mlp, X_full, y, truths, folds)
    run_model("MLP(32) no branch features", mlp, X_nobr, y, truths, folds)
    logit_dec, logit_oof = run_model("Logistic + branch features", logit, X_full, y, truths, folds)
    run_model("Logistic + branch, calibrated", cal(logit), X_full, y, truths, folds)
    run_model("MLP(16,a=1) + branch, calibrated", cal(mlp_small), X_full, y, truths, folds)
    logit_dec, logit_oof = run_model(
        "Logistic+cal + conf-correctness", cal(logit), X_full, y, truths, folds, conf_cal=True)
    mlp_dec, mlp_oof = logit_dec, logit_oof  # divergence report on the current winner

    # CFA veto sweep: demote APPROVED -> NEEDS_REVIEW when P(DENIED) is high.
    print("\nCFA veto sweep on the winner (approve blocked when P(D) >= t):")
    for t in (1.0, 0.30, 0.20, 0.15, 0.10, 0.05):
        dec = []
        for row in logit_oof:
            d, _ = expected_points_decision(row)
            if d == "APPROVED" and row[1] >= t:
                d = "NEEDS_REVIEW"
            dec.append(d)
        pts = sum(PAYOFF[tr_, d] for d, tr_ in zip(dec, truths))
        cfa = sum(1 for d, tr_ in zip(dec, truths) if tr_ == "DENIED" and d == "APPROVED")
        print(f"  t={t:4.2f}  raw={pts:7.1f} ({pts / len(y) / 8 * 80:5.2f}/80eq)  CFA={cfa}")

    # Where does the MLP diverge from rules, and does it win?
    print("\nMLP vs rules divergences by rules-branch (n, MLP raw pts − rules raw pts):")
    delta = defaultdict(lambda: [0, 0.0])
    for c, md in zip(cases, mlp_dec):
        rd = preds[c]["adjudication"]
        if md != rd:
            d = delta[dbgs[c]["branch"]]
            d[0] += 1
            d[1] += PAYOFF[truth[c], md] - PAYOFF[truth[c], rd]
    for b, (n, dp) in sorted(delta.items(), key=lambda kv: -kv[1][1]):
        print(f"  {b:24s} n={n:3d}  Δraw={dp:+7.1f}  ({dp / 700 / 8 * 80:+.2f} dev pts)")
    print("\nMLP decision mix:", Counter(mlp_dec))
    print("CFA cases (MLP):", [ (c, round(float(mlp_oof[i][1]),3))
        for i,c in enumerate(cases)
        if truths[i]=="DENIED" and mlp_dec[i]=="APPROVED"][:15])


if __name__ == "__main__":
    main(*sys.argv[1:2])
