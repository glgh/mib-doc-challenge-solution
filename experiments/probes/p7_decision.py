#!/usr/bin/env python3
"""P7 (bonus) — can the probabilistic signal DRIVE decisions?

Model P(truth=class | features), then pick the action maximizing expected raw
classification points under the asymmetric cost matrix (the -4 false-approval
priced in). Compare to the current policy on the frozen holdout — and count CFAs,
because the repo's prior learned decider inverted to -0.50 with 14 CFAs."""
import csv, json, os, sys
import numpy as np
from sklearn.linear_model import LogisticRegression

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
# A replay dir (predictions.jsonl + debug.jsonl) built by scripts/replay.py.
SP = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "output", "replay_post")
truth = {r["case_id"]: r["adjudication"] for r in
         csv.DictReader(open(os.path.join(ROOT, "..", "mib-doc-challenge", "data", "train_labels.csv")))}
pred = {json.loads(l)["case_id"]: json.loads(l) for l in open(f"{SP}/predictions.jsonl")}
dbg = {json.loads(l)["case_id"]: json.loads(l) for l in open(f"{SP}/debug.jsonl")}
splits = json.load(open(os.path.join(ROOT, "data_splits.json")))
def ids(k):
    v = splits[k]; return v if isinstance(v, list) else v.get("ids") or list(v)
dev, hold = set(ids("dev")), set(ids("holdout"))

CLS = ["APPROVED", "DENIED", "NEEDS_REVIEW"]
# raw points earned for taking action a when truth is t
EARN = {("APPROVED","APPROVED"):8, ("APPROVED","DENIED"):0,  ("APPROVED","NEEDS_REVIEW"):2,
        ("DENIED","DENIED"):8,     ("DENIED","APPROVED"):-4, ("DENIED","NEEDS_REVIEW"):2,
        ("NEEDS_REVIEW","NEEDS_REVIEW"):8, ("NEEDS_REVIEW","APPROVED"):1, ("NEEDS_REVIEW","DENIED"):1}
def earn(t, a): return EARN[(t, a)]

BR = sorted({dbg[c]["branch"] for c in dbg})
def feats(c):
    d = dbg[c]
    return [1.0 if d["branch"] == b else 0.0 for b in BR] + [
        float(d.get("n_fields_missing", 0)), float(bool(d.get("has_biometric"))),
        float(bool(d.get("has_flag_evidence"))), float(len(d.get("deny_hits", []))),
        float(len(d.get("review_hits", []))), float(d.get("n_corrections", 0)),
        float(d.get("n_pages", 0)), float(d.get("scan_only_pages", 0)),
        float(len(d.get("flags", []))), float(len(d.get("emit_flags", [])))]

def rows(idset):
    cs = [c for c in idset if c in pred and c in truth]
    return cs, np.array([feats(c) for c in cs]), np.array([CLS.index(truth[c]) for c in cs])
dcs, Xd, yd = rows(dev)
hcs, Xh, yh = rows(hold)

clf = LogisticRegression(max_iter=3000, C=0.5, class_weight="balanced").fit(Xd, yd)
P = clf.predict_proba(Xh)                       # P(truth=class | features), holdout

def score_and_cfa(actions):
    raw = sum(earn(truth[c], a) for c, a in zip(hcs, actions))
    cfa = sum(1 for c, a in zip(hcs, actions) if truth[c] == "DENIED" and a == "APPROVED")
    return raw, cfa

# current policy
cur_actions = [pred[c]["adjudication"] for c in hcs]
cur_raw, cur_cfa = score_and_cfa(cur_actions)

# EV-optimal action under predicted class probabilities (full)
def ev_action(prow, forbid_approve=False):
    best, ba = -9, "NEEDS_REVIEW"
    for a in CLS:
        if forbid_approve and a == "APPROVED":
            continue
        ev = sum(prow[CLS.index(t)] * earn(t, a) for t in CLS)
        if ev > best:
            best, ba = ev, a
    return ba
ev_actions   = [ev_action(P[i]) for i in range(len(hcs))]
safe_actions = [ev_action(P[i], forbid_approve=True) for i in range(len(hcs))]
ev_raw, ev_cfa = score_and_cfa(ev_actions)
sf_raw, sf_cfa = score_and_cfa(safe_actions)

n = len(hcs); mx = 8 * n
def to80(raw): return 80 * raw / mx
print(f"holdout n={n}  (max raw {mx})\n")
print(f"{'policy':34} {'raw':>6} {'/80-scaled':>11} {'CFAs':>6}")
print(f"{'current (shipped)':34} {cur_raw:6} {to80(cur_raw):11.2f} {cur_cfa:6}")
print(f"{'EV-optimal (full, -4 priced in)':34} {ev_raw:6} {to80(ev_raw):11.2f} {ev_cfa:6}")
print(f"{'EV-optimal, APPROVE forbidden':34} {sf_raw:6} {to80(sf_raw):11.2f} {sf_cfa:6}")
print(f"\ndecision shifts (current -> EV-full):")
from collections import Counter
sh = Counter((c, e) for c, e in zip(cur_actions, ev_actions) if c != e)
for (a, b), k in sh.most_common():
    print(f"  {a:14} -> {b:14} : {k}")
print(f"\nprior to beat: the deleted learned decider was -0.50 with 14 CFAs (STATUS.md).")
