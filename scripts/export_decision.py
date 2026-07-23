#!/usr/bin/env python3
"""Train the Step-0 winner and export numpy weights for mib.decision.

Fits CalibratedClassifierCV(StandardScaler + LogisticRegression, cv=3, sigmoid)
on the chosen split's cases, plus the 1-D correctness calibrator (inner-CV,
same recipe as the bake-off), then writes mib/decision_model.npz and verifies
the numpy forward pass reproduces sklearn's predict_proba to ~1e-9.

Usage: scripts/export_decision.py [eval_dir] [--split dev|all] [--out path]
Training on dev (default) keeps holdout honest; refit with --split all only at
packaging time.
"""
import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np
from sklearn.calibration import CalibratedClassifierCV
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CH = ROOT.parent / "mib-doc-challenge"

import mib.config  # noqa: E402
from mib import features  # noqa: E402
from mib.decision import predict_proba as np_predict_proba  # noqa: E402

CLASSES = features.CLASSES
SEED = 8090


def base_model():
    return make_pipeline(StandardScaler(), LogisticRegression(
        max_iter=5000, C=1.0, random_state=SEED))


def ev_decision(probs):
    pa, pd, pn = probs
    ev = {"APPROVED": 8 * pa + 1 * pn - 4 * pd,
          "DENIED": 8 * pd + 1 * pn,
          "NEEDS_REVIEW": 8 * pn + 2 * pa + 2 * pd}
    return max(ev, key=ev.get)


def proba_in_class_order(m, X):
    cols = {c: i for i, c in enumerate(m.classes_)}
    p = m.predict_proba(X)
    return np.stack([p[:, cols[c]] for c in CLASSES], axis=1)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("eval_dir", nargs="?", default=str(ROOT / "output/eval_skew"))
    ap.add_argument("--split", choices=("dev", "all"), default="dev")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent.parent
                                         / "mib/decision_model.npz"))
    args = ap.parse_args()
    eval_dir = Path(args.eval_dir)

    meta_path = eval_dir / "meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else None
    mib.config.require_agreement([(str(eval_dir), meta)])

    splits = json.loads((ROOT / "data_splits.json").read_text())
    wanted = sorted(splits["dev"]) if args.split == "dev" else sorted(
        splits["dev"] + splits["holdout"])
    truth = {r["case_id"]: r["adjudication"]
             for r in csv.DictReader(open(CH / "data/train_labels.csv"))}
    preds = {r["case_id"]: r for r in map(json.loads, open(eval_dir / "predictions.jsonl"))}
    dbgs = {r["case_id"]: r for r in map(json.loads, open(eval_dir / "debug.jsonl"))}
    cases = [c for c in wanted if c in preds and c in dbgs]

    order = features.names(with_branch=True)
    X = np.array([features.vector(preds[c], dbgs[c], order=order) for c in cases])
    y = np.array([truth[c] for c in cases])
    print(f"training on {len(cases)} cases ({args.split}), {X.shape[1]} features")

    model = CalibratedClassifierCV(base_model(), cv=3, method="sigmoid")
    model.fit(X, y)

    # Correctness calibrator: P(EV decision correct | P(chosen)), inner CV.
    inner = StratifiedKFold(n_splits=3, shuffle=True, random_state=SEED)
    raw, correct = [], []
    for tr, te in inner.split(X, y):
        m = CalibratedClassifierCV(base_model(), cv=3, method="sigmoid")
        m.fit(X[tr], y[tr])
        for row, t in zip(proba_in_class_order(m, X[te]), y[te]):
            d = ev_decision(row)
            raw.append(row[CLASSES.index(d)])
            correct.append(float(d == t))
    conf_lr = LogisticRegression(max_iter=1000)
    conf_lr.fit(np.array(raw).reshape(-1, 1), np.array(correct))

    out = {
        "feature_names": np.array(order),
        "with_branch": np.array(True),
        "classes": np.array(CLASSES),
        "n_members": np.array(len(model.calibrated_classifiers_)),
        "conf_a": np.array(conf_lr.coef_[0, 0]),
        "conf_b": np.array(conf_lr.intercept_[0]),
        "trained_on": np.array(f"{args.split}:{len(cases)}"),
        "substrate_restore": np.array((meta or {}).get("restore", "unknown")),
    }
    for i, cc in enumerate(model.calibrated_classifiers_):
        pipe = cc.estimator
        scaler, lr = pipe.named_steps["standardscaler"], pipe.named_steps["logisticregression"]
        if list(lr.classes_) != CLASSES:
            raise SystemExit(f"unexpected class order in member {i}: {lr.classes_}")
        out[f"scaler_mean_{i}"] = scaler.mean_
        out[f"scaler_scale_{i}"] = scaler.scale_
        out[f"coef_{i}"] = lr.coef_
        out[f"intercept_{i}"] = lr.intercept_
        out[f"cal_a_{i}"] = np.array([c.a_ for c in cc.calibrators])
        out[f"cal_b_{i}"] = np.array([c.b_ for c in cc.calibrators])

    # Verify the numpy forward pass against sklearn before writing anything.
    sk = proba_in_class_order(model, X)
    ours = np.array([_np_proba(x, out) for x in X])
    err = float(np.abs(sk - ours).max())
    print(f"numpy-vs-sklearn max |dp| = {err:.2e}")
    if err > 1e-6:
        raise SystemExit("forward-pass mismatch — not exporting")

    np.savez(args.out, **out)
    print(f"wrote {args.out} "
          f"({Path(args.out).stat().st_size / 1024:.0f} KiB, {out['n_members']} members)")


def _np_proba(x, m):
    return np_predict_proba(x, m)


if __name__ == "__main__":
    main()
