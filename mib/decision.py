"""Learned decider: numpy-only forward pass over the frozen feature vector.

Replicates the Step-0 winner offline-trained by scripts/export_decision.py:
CalibratedClassifierCV(StandardScaler + LogisticRegression, cv=3, sigmoid),
EV-argmax decision, and a 1-D correctness calibrator for the emitted
confidence. No sklearn at runtime — weights live in mib/decision_model.npz.

The npz carries the feature-name order it was trained with; `load()` refuses a
model whose names disagree with mib.features.names(), so a feature edit without
a retrain fails loudly instead of silently scoring garbage.
"""
import os
from pathlib import Path

import numpy as np

from mib import features

CLASSES = features.CLASSES
MODEL_PATH = Path(__file__).parent / "decision_model.npz"

_cache = {}


def load(path=MODEL_PATH):
    if "m" not in _cache:
        with np.load(path, allow_pickle=False) as z:
            m = {k: z[k] for k in z.files}
        trained = [str(n) for n in m["feature_names"]]
        current = features.names(with_branch=bool(m["with_branch"]))
        if trained != current:
            raise RuntimeError(
                "decision_model.npz feature order disagrees with mib.features — retrain")
        _cache["m"] = m
    return _cache["m"]


def _expit(x):
    return 1.0 / (1.0 + np.exp(-x))


def predict_proba(x, m):
    """x: 1-D feature vector -> calibrated P(A/D/NR), averaged over CV members."""
    x = np.asarray(x, dtype=float)
    probs = np.zeros(3)
    for i in range(int(m["n_members"])):
        z = (x - m[f"scaler_mean_{i}"]) / m[f"scaler_scale_{i}"]
        df = z @ m[f"coef_{i}"].T + m[f"intercept_{i}"]
        p = _expit(-(m[f"cal_a_{i}"] * df + m[f"cal_b_{i}"]))
        s = p.sum()
        probs += p / s if s > 0 else np.full(3, 1 / 3)
    return probs / int(m["n_members"])


def expected_points(probs):
    pa, pd, pn = probs
    return {
        "APPROVED": 8 * pa + 1 * pn - 4 * pd,
        "DENIED": 8 * pd + 1 * pn,
        "NEEDS_REVIEW": 8 * pn + 2 * pa + 2 * pd,
    }


def veto_threshold():
    """P(DENIED) above which an EV-argmax APPROVED is demoted to NEEDS_REVIEW.
    Default 1.0 = pure EV pricing; the Phase-3 ship decision may lower it."""
    try:
        return float(os.environ.get("MIB_CFA_VETO", "1.0"))
    except ValueError:
        return 1.0


def decide(record, debug):
    """(record, debug) as emitted by solution.predict_from_pages ->
    (decision, confidence, probs) from the learned decider."""
    m = load()
    x = features.vector(record, debug, with_branch=bool(m["with_branch"]),
                        order=[str(n) for n in m["feature_names"]])
    probs = predict_proba(x, m)
    ev = expected_points(probs)
    decision = max(ev, key=ev.get)
    if decision == "APPROVED" and probs[1] >= veto_threshold():
        decision = "NEEDS_REVIEW"
    p_chosen = float(probs[CLASSES.index(decision)])
    a, b = float(m["conf_a"]), float(m["conf_b"])
    confidence = float(np.clip(_expit(a * p_chosen + b), 0.05, 0.95))
    return decision, round(confidence, 4), [round(float(p), 4) for p in probs]
