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
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parent.parent
CH = ROOT.parent / "mib-doc-challenge"

CLASSES = ["APPROVED", "DENIED", "NEEDS_REVIEW"]
VISAS = ["XW-1", "XW-2", "DIP-1", "MED-3", "TRANSIT-7"]
FEES = ["paid", "waived", "unpaid", "unknown"]
FLAGS = ["memory_tampering", "planetary_embargo", "active_warrant", "biohazard_red",
         "identity_conflict", "sponsor_mismatch", "illegible_biometrics", "rescinded_denial"]
BRANCHES = ["adjudicator_finding", "disqualifying_flag", "embargo_world",
            "embargo_world_partial", "revoked_sponsor", "transit_visa", "fee_unpaid",
            "fee_unknown", "stale_arrival", "waived_non_dip", "missing_arrival",
            "review_flag", "missing_sponsor", "missing_visa", "b13_census", "clean_approve"]
REVOKED = {"SPN-0007", "SPN-0139", "SPN-4040", "SPN-2718", "SPN-7331", "SPN-9090"}
FULL_EMBARGO = {"TRAPPIST-1e", "Eris Relay"}
PARTIAL_EMBARGO = {"Wolf-1061c"}
STALE_CUTOFF = date(2026, 1, 2)

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


def staleness_days(arrival):
    try:
        y, m, d = map(int, arrival.split("-"))
        return max(-365, min(365, (STALE_CUTOFF - date(y, m, d)).days)) / 365.0
    except (ValueError, AttributeError):
        return 0.0


def featurize(pred, dbg, with_branch=True):
    f = {}
    visa = pred["visa_class"]
    for v in VISAS:
        f[f"visa={v}"] = visa == v
    f["visa=unknown"] = visa not in VISAS
    fee = pred["fee_status"]
    for v in FEES:
        f[f"fee={v}"] = fee == v
    sponsor = pred["sponsor_id"]
    f["sponsor_present"] = sponsor != "SPN-0000"
    f["sponsor_revoked"] = sponsor in REVOKED
    f["dip_no_sponsor"] = (visa == "DIP-1") and sponsor == "SPN-0000"
    arrival = pred["arrival_date"]
    f["arrival_present"] = arrival != "1900-01-01"
    f["staleness"] = staleness_days(arrival) if f["arrival_present"] else 0.0
    flags = set(dbg["flags"])
    for fl in FLAGS:
        f[f"flag={fl}"] = fl in flags
    f["n_flags"] = len(flags)
    reg = dbg.get("registry_status", "")
    f["registry=CLEAR"] = reg == "CLEAR"
    f["registry=FLAGGED"] = bool(reg) and reg != "CLEAR"
    f["registry=absent"] = not reg
    finding = dbg.get("finding")
    for c in CLASSES:
        f[f"finding={c}"] = finding == c
    f["finding=none"] = finding is None
    docs = set(dbg["doc_types"])
    for dt, name in [(1, "adjudicator"), (2, "intake"), (3, "biometric"),
                     (4, "sponsor"), (5, "registry"), (6, "fee")]:
        f[f"has_{name}"] = dt in docs
    f["n_pages"] = dbg.get("n_pages", 0)
    f["n_scan_pages"] = dbg.get("scan_only_pages", 0)
    f["hidden_present"] = dbg.get("hidden_lines", 0) > 0
    f["n_fields_missing"] = dbg.get("n_fields_missing", 0)
    f["n_corrections"] = dbg.get("n_corrections", 0)
    f["waiver_present"] = bool(dbg.get("waiver_code"))
    world = pred["home_world"]
    f["embargo_world"] = world in FULL_EMBARGO
    f["partial_embargo_world"] = world in PARTIAL_EMBARGO
    prov = dbg.get("provenance", {})
    srcs = [v[1] for v in prov.values()]
    f["n_ocr_fields"] = sum(srcs)
    f["worst_doc_rank"] = max((v[0] for v in prov.values()), default=9)
    if with_branch:
        for b in BRANCHES:
            f[f"branch={b}"] = dbg["branch"] == b
        for c in CLASSES:
            f[f"rules={c}"] = dbg.get("rules_decision", pred["adjudication"]) == c
    return f


def score_decisions(decisions, confs, truths):
    pts = sum(PAYOFF[t, d] for d, t in zip(decisions, truths))
    briers = [(c - (d == t)) ** 2 for d, c, t in zip(decisions, confs, truths)]
    cfas = sum(1 for d, t in zip(decisions, truths) if t == "DENIED" and d == "APPROVED")
    acc = sum(d == t for d, t in zip(decisions, truths)) / len(truths)
    return pts, float(np.mean(briers)), cfas, acc


def run_model(name, model_fn, X, y, truths, folds):
    oof = np.zeros((len(y), 3))
    for tr, te in folds:
        m = model_fn()
        m.fit(X[tr], y[tr])
        cols = {c: i for i, c in enumerate(m.classes_)}
        p = m.predict_proba(X[te])
        for j, c in enumerate(CLASSES):
            oof[te, j] = p[:, cols[c]] if c in cols else 0.0
    decisions, confs = [], []
    for row in oof:
        d, _ = expected_points_decision(row)
        decisions.append(d)
        confs.append(row[CLASSES.index(d)])
    pts, brier, cfas, acc = score_decisions(decisions, confs, truths)
    print(f"{name:34s} raw={pts:7.1f} ({pts / len(y) / 8 * 80:5.2f}/80eq) "
          f"acc={acc:.3f} brier={brier:.4f} CFA={cfas}")
    return decisions, oof


def main():
    dev = sorted(json.loads((ROOT / "data_splits.json").read_text())["dev"])
    truth = {r["case_id"]: r["adjudication"]
             for r in csv.DictReader(open(CH / "data/train_labels.csv"))}
    preds = {r["case_id"]: r for r in map(json.loads, open(ROOT / "output/eval/predictions.jsonl"))}
    dbgs = {r["case_id"]: r for r in map(json.loads, open(ROOT / "output/eval/debug.jsonl"))}
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

    mlp_dec, mlp_oof = run_model("MLP(32) + branch features", mlp, X_full, y, truths, folds)
    run_model("MLP(32) no branch features", mlp, X_nobr, y, truths, folds)
    run_model("Logistic + branch features", logit, X_full, y, truths, folds)

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
    main()
