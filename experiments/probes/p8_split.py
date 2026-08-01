#!/usr/bin/env python3
"""P8 — hunt the NEEDS_REVIEW split. Inside the fee_unknown + b13_census buckets,
does legitimate visible evidence separate approvable-from-deniable? And is there a
CFA-safe wedge (commit confident-DENY, which can never cause a false approval)?"""
import csv, json, os, sys
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.tree import DecisionTreeClassifier, export_text
from sklearn.metrics import roc_auc_score

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
from mib import policy
# A replay dir (predictions.jsonl + debug.jsonl) built by scripts/replay.py.
SP = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "output", "replay_post")
truth = {r["case_id"]: r["adjudication"] for r in
         csv.DictReader(open(os.path.join(ROOT, "..", "mib-doc-challenge", "data", "train_labels.csv")))}
pred = {json.loads(l)["case_id"]: json.loads(l) for l in open(f"{SP}/predictions.jsonl")}
dbg = {json.loads(l)["case_id"]: json.loads(l) for l in open(f"{SP}/debug.jsonl")}
splits = json.load(open(os.path.join(ROOT, "data_splits.json")))
def sids(k):
    v = splits[k]; return set(v if isinstance(v, list) else v.get("ids") or list(v))
dev, hold = sids("dev"), sids("holdout")

EMB = set(getattr(policy, "FULL_EMBARGO_WORLDS", set())) | set(getattr(policy, "PARTIAL_EMBARGO_WORLDS", set()))
VISAS = ["XW-1", "XW-2", "DIP-1", "MED-3", "TRANSIT-7"]
FEAT_NAMES = ([f"visa_{v}" for v in VISAS] + ["world_embargo", "sponsor_absent", "arrival_missing",
              "n_flags", "has_biometric", "n_pages", "n_fields_missing", "scan_only",
              "n_corrections", "n_deny_hits", "n_review_hits"] + [f"doc_{i}" for i in range(12)])
def feats(c):
    p, d = pred[c], dbg[c]
    f = [1.0 if p.get("visa_class") == v else 0.0 for v in VISAS]
    f += [float(p.get("home_world") in EMB),
          float(p.get("sponsor_id") in (None, "SPN-0000")),
          float(not p.get("arrival_date") or p.get("arrival_date") == "1900-01-01"),
          float(len(set(p.get("risk_flags", "none").split("|")) - {"none"})),
          float(bool(d.get("has_biometric"))), float(d.get("n_pages", 0)),
          float(d.get("n_fields_missing", 0)), float(d.get("scan_only_pages", 0)),
          float(d.get("n_corrections", 0)), float(len(d.get("deny_hits", []))),
          float(len(d.get("review_hits", [])))]
    dt = set(d.get("doc_types", []))
    f += [1.0 if i in dt else 0.0 for i in range(12)]
    return f

BUCKETS = ("fee_unknown", "b13_census")
bucket = [c for c in truth if c in dbg and dbg[c]["branch"] in BUCKETS]
def rows(idset):
    cs = [c for c in bucket if c in idset]
    return cs, np.array([feats(c) for c in cs]), np.array([truth[c] for c in cs])
dcs, Xd, td = rows(dev); hcs, Xh, th = rows(hold)
print(f"bucket cases: dev {len(dcs)}, holdout {len(hcs)}")
for nm, t in (("dev", td), ("holdout", th)):
    u, c = np.unique(t, return_counts=True)
    print(f"  {nm} truth mix: {dict(zip(u, c))}")

# --- (1) is there ANY APP-vs-DEN separation from visible evidence? (holdout AUC) ---
mask_d = td != "NEEDS_REVIEW"; mask_h = th != "NEEDS_REVIEW"
yd = (td[mask_d] == "APPROVED").astype(int); yh = (th[mask_h] == "APPROVED").astype(int)
lr = LogisticRegression(max_iter=3000, C=0.5, class_weight="balanced").fit(Xd[mask_d], yd)
auc = roc_auc_score(yh, lr.predict_proba(Xh[mask_h])[:, 1]) if len(set(yh)) > 1 else float("nan")
print(f"\n(1) APP-vs-DEN within bucket — holdout AUC (visible evidence): {auc:.3f}  (0.5=no signal)")

# --- (2) CFA-safe wedge: commit a high-P(DENIED) subset NR->DENY ---
yd_den = (td == "DENIED").astype(int)
clf = LogisticRegression(max_iter=3000, C=0.5, class_weight="balanced").fit(Xd, yd_den)
pden_h = clf.predict_proba(Xh)[:, 1]
# payoff of committing an NR case to DENY: DEN +6, APP -2, NR -7
delta = {"DENIED": 6, "APPROVED": -2, "NEEDS_REVIEW": -7}
best = (0.0, 1.01, 0, 0)  # (net, thr, n_committed, n_true_den)
for thr in np.round(np.arange(0.50, 1.001, 0.02), 2):
    commit = pden_h >= thr
    net = sum(delta[th[i]] for i in range(len(hcs)) if commit[i])
    if net > best[0]:
        best = (net, thr, int(commit.sum()), int(sum(th[commit] == "DENIED")))
net, thr, ncom, nden = best
print(f"\n(2) CFA-safe NR->DENY commit (holdout): best net +{net} raw at P(DEN)>={thr}")
print(f"    commits {ncom} cases, {nden} truly DENIED  (0 CFAs by construction — DENY can't false-approve)")
print(f"    scaled to /80 over full 1000: ~{80*net/ (8*len(hcs)) :.2f} pts on this holdout slice "
      f"(x{1000/len(hcs):.1f} if it holds corpus-wide, but expect shrinkage)")

# --- (3) what does a shallow tree key on? (interpretable rule search) ---
tree = DecisionTreeClassifier(max_depth=3, min_samples_leaf=15,
                              class_weight="balanced").fit(Xd, yd_den)
print("\n(3) shallow tree for P(DENIED) — the rule it finds:")
print(export_text(tree, feature_names=FEAT_NAMES, max_depth=3))
print("Known ceiling (not re-derived): the hidden-key inverse-verdict signal is 188/188 on train")
print("(experiments row 79) but parked as spec-tension — using hidden adversarial text to decide.")
