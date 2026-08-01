"""Discovery-natured cross-tab miner (2026-07-29).

Not a hypothesis test. Joins truth + predictions + derived-signal sidecar on
case_id and sweeps every categorical feature against three outcomes, ranking
cells by lift over base rate with a minimum-support floor so small-n noise
doesn't surface. The point is to let the data volunteer structure we haven't
looked for, then hand the strongest leads to a falsification pass.

Outcomes:
  A. adj_wrong      our adjudication != truth adjudication
  B. <field>_miss   our field != truth field (per extraction field)
  C. label itself   truth adjudication distribution (policy structure)

Usage: .venv/bin/python experiments/discovery_xtab.py
"""
import json
import math
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CH = REPO.parent / "mib-doc-challenge"
TRUTH = CH / "data" / "train_labels.csv"
PRED = REPO / "output" / "replay_vf_c" / "predictions.jsonl"
DEBUG = REPO / "output" / "replay_vf_c" / "debug.jsonl"

FIELDS = ["applicant_name", "species_code", "home_world", "visa_class",
          "sponsor_id", "arrival_date", "declared_purpose", "risk_flags",
          "fee_status"]
REVOKED = {"SPN-0007", "SPN-0139", "SPN-4040"}


def norm(s):
    return (s or "").strip().lower()


def load_csv(path):
    import csv
    with open(path) as f:
        return {r["case_id"]: r for r in csv.DictReader(f)}


def load_jsonl(path):
    out = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                d = json.loads(line)
                out[d["case_id"]] = d
    return out


def features(cid, truth, pred, dbg):
    """Categorical features for one case, from labels + derived signals."""
    t, p, d = truth[cid], pred.get(cid, {}), dbg.get(cid, {})
    f = {}
    # ---- label-side features ----
    f["visa_class"] = t["visa_class"] or "(blank)"
    f["declared_purpose"] = norm(t["declared_purpose"]) or "(blank)"
    f["species_code"] = t["species_code"] or "(blank)"
    f["home_world"] = t["home_world"] or "(blank)"
    f["fee_status"] = t["fee_status"] or "(blank)"
    flags = [x for x in (t["risk_flags"] or "none").split("|") if x]
    f["risk_flag_set"] = t["risk_flags"] or "none"
    f["n_risk_flags"] = str(len(flags)) if t["risk_flags"] != "none" else "0"
    for fl in flags:
        f[f"has_flag:{fl}"] = "1"
    f["sponsor_revoked"] = "1" if t["sponsor_id"] in REVOKED else "0"
    f["sponsor_blank"] = "1" if not t["sponsor_id"] else "0"
    yr = (t["arrival_date"] or "")[:4]
    f["arrival_year"] = yr or "(blank)"
    # ---- derived-signal features ----
    f["branch"] = d.get("branch", "(none)")
    f["has_biometric"] = "1" if d.get("has_biometric") else "0"
    f["registry_status"] = d.get("registry_status") or "(blank)"
    f["finding"] = d.get("finding") or "(blank)"
    f["waiver_code"] = d.get("waiver_code") or "(blank)"
    f["n_pages"] = str(d.get("n_pages", "?"))
    f["has_hidden"] = "1" if d.get("hidden_lines", 0) else "0"
    f["has_struck"] = "1" if d.get("struck") else "0"
    f["n_fields_missing"] = str(d.get("n_fields_missing", "?"))
    for dt in d.get("doc_types", []):
        f[f"doc:{dt}"] = "1"
    for fl in d.get("flags", []):
        f[f"sigflag:{fl}"] = "1"
    return f


def outcomes(cid, truth, pred):
    t, p = truth[cid], pred.get(cid, {})
    o = {}
    o["adj_wrong"] = 1 if norm(p.get("adjudication")) != norm(t["adjudication"]) else 0
    for fld in FIELDS:
        o[f"{fld}_miss"] = 1 if norm(p.get(fld)) != norm(t[fld]) else 0
    return o


def mine(cids, truth, pred, dbg, outcome_key, min_support=15, top=18):
    """Rank feature=value cells by |lift| on a binary outcome."""
    base_pos = sum(outcomes(c, truth, pred)[outcome_key] for c in cids)
    base = base_pos / len(cids)
    cells = defaultdict(lambda: [0, 0])  # (feat,val) -> [n, pos]
    for c in cids:
        o = outcomes(c, truth, pred)[outcome_key]
        for k, v in features(c, truth, pred, dbg).items():
            cell = cells[(k, v)]
            cell[0] += 1
            cell[1] += o
    rows = []
    for (k, v), (n, pos) in cells.items():
        if n < min_support:
            continue
        rate = pos / n
        lift = rate / base if base else 0.0
        # Wilson-ish weight: prefer cells whose deviation is unlikely by chance.
        z = (rate - base) / math.sqrt(base * (1 - base) / n) if base not in (0, 1) else 0
        rows.append((abs(z), lift, rate, n, pos, k, v))
    rows.sort(reverse=True)
    print(f"\n{'='*78}\nOUTCOME: {outcome_key}   base rate {base:.3f} "
          f"({base_pos}/{len(cids)})\n{'='*78}")
    print(f"{'z':>5} {'lift':>5} {'rate':>5} {'n':>4} {'pos':>4}  feature = value")
    for z, lift, rate, n, pos, k, v in rows[:top]:
        print(f"{z:5.1f} {lift:5.2f} {rate:5.2f} {n:4d} {pos:4d}  {k} = {v}")


def label_xtab(cids, truth, feat_key):
    """Truth-adjudication distribution within each level of a label feature."""
    tab = defaultdict(lambda: defaultdict(int))
    for c in cids:
        val = features(c, truth, {}, {}).get(feat_key, "(none)")
        tab[val][truth[c]["adjudication"]] += 1
    print(f"\n{'='*78}\nLABEL STRUCTURE: adjudication by {feat_key}\n{'='*78}")
    print(f"{'level':<28} {'APPROV':>6} {'DENIED':>6} {'REVIEW':>6} {'n':>5}  "
          f"{'skew':>18}")
    order = sorted(tab.items(), key=lambda kv: -sum(kv[1].values()))
    for val, dist in order:
        a, dn, r = dist["APPROVED"], dist["DENIED"], dist["NEEDS_REVIEW"]
        n = a + dn + r
        if n < 12:
            continue
        top = max(dist, key=dist.get)
        print(f"{val[:28]:<28} {a:6d} {dn:6d} {r:6d} {n:5d}  "
              f"{top}:{dist[top]/n:.0%}")


def main():
    truth = load_csv(TRUTH)
    pred = load_jsonl(PRED)
    dbg = load_jsonl(DEBUG)
    cids = sorted(set(truth) & set(pred) & set(dbg))
    print(f"joined {len(cids)} cases  (truth {len(truth)} / pred {len(pred)} "
          f"/ dbg {len(dbg)})")

    # A. where adjudication errors concentrate
    mine(cids, truth, pred, dbg, "adj_wrong")

    # B. where each extraction field misses concentrate
    for fld in ["risk_flags", "species_code", "home_world", "visa_class",
                "sponsor_id", "arrival_date", "fee_status", "applicant_name",
                "declared_purpose"]:
        mine(cids, truth, pred, dbg, f"{fld}_miss")

    # C. label policy structure the manual doesn't spell out
    for fk in ["visa_class", "declared_purpose", "n_risk_flags",
               "sponsor_revoked", "fee_status", "home_world"]:
        label_xtab(cids, truth, fk)


if __name__ == "__main__":
    main()
