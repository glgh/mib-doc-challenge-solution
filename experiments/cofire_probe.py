#!/usr/bin/env python3
"""Co-fire matrix: where the cascade's ORDER is load-bearing, and what it costs.

`policy.adjudicate` is first-match-wins, so branch order only matters on cases
where two or more branch predicates are true at once. Nothing has ever measured
that overlap set (STATUS question 8 — the cascade's *structure* is unaudited).
This probe evaluates ALL predicates independently per case (not just the first
that fires), joins truth, and answers:

  1. How many cases co-fire >=2 predicates, and which pairs dominate?
  2. Per populated predicate-set cell: the shipped decision (what first-match
     yields) vs the EV-optimal decision under the raw scoring matrix
     (correct=8, A/D->NR=2, NR->A/D=1, A<->D=0, false-approve-denied=-4).
  3. Which order choices could change decisions at all, vs which only move
     calibration/attribution mass between same-decision branches.

`--truth` runs the field-based predicates on the LABEL columns instead of the
extracted values (finding/census unknowable from labels -> never fire) and
prints the truth-branch -> pipeline-branch migration matrix: how extraction
noise moves cases across branch membership (purity vs fidelity).

  experiments/cofire_probe.py [cache.jsonl] [--truth]   (default train_bands.jsonl)

Read-only probe over the existing full cache + dev split; writes nothing.
"""
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CH = ROOT.parent / "mib-doc-challenge"

from mib import cache, packet, parse, policy, signals  # noqa: E402

# Raw per-case classification points: SCORE[truth][pred].
SCORE = {
    "APPROVED": {"APPROVED": 8, "DENIED": 0, "NEEDS_REVIEW": 2},
    "DENIED": {"APPROVED": -4, "DENIED": 8, "NEEDS_REVIEW": 2},
    "NEEDS_REVIEW": {"APPROVED": 1, "DENIED": 1, "NEEDS_REVIEW": 8},
}

DECISION = {name: "DENIED" for name in policy.DENY_BRANCHES}
DECISION.update({name: "NEEDS_REVIEW" for name in policy.REVIEW_BRANCHES})
DECISION["clean_approve"] = "APPROVED"

# Attribution order, minus clean_approve (the complement) — derived from the
# policy tier lists since the tier refactor, so it cannot drift. The probe's
# independence now lives entirely in the hand-mirrored predicates() below,
# which first_match runs against policy.adjudicate (mismatches must be 0).
ORDER = ["adjudicator_finding", *policy.DENY_BRANCHES, *policy.REVIEW_BRANCHES]


def predicates(values, sig):
    """Every branch predicate, evaluated independently — mirrors policy.adjudicate."""
    flags = sig["flags"]
    fee = (values.get("fee_status") or "unknown").lower()
    visa = values.get("visa_class")
    sponsor = values.get("sponsor_id")
    known_non_dip = visa is not None and visa != "DIP-1"
    non_dip_or_unknown = visa != "DIP-1"
    return {
        "adjudicator_finding": bool(sig["finding"]),
        "disqualifying_flag": bool(flags & parse.DISQUALIFYING_FLAGS),
        "embargo_world": values.get("home_world") in policy.FULL_EMBARGO_WORLDS,
        "embargo_world_partial": (
            values.get("home_world") in policy.PARTIAL_EMBARGO_WORLDS and known_non_dip),
        "revoked_sponsor": sponsor in policy.REVOKED_SPONSORS and known_non_dip,
        "transit_visa": visa == "TRANSIT-7",
        "fee_unpaid": fee == "unpaid",
        "fee_unknown": fee == "unknown",
        "stale_arrival": policy._is_stale(values.get("arrival_date")) and known_non_dip,
        "waived_non_dip": fee == "waived" and non_dip_or_unknown,
        "missing_arrival": not values.get("arrival_date"),
        "review_flag": bool(flags & parse.REVIEW_FLAGS),
        "missing_sponsor": not sponsor and non_dip_or_unknown,
        "missing_visa": not visa,
        "b13_census": not sig["has_biometric"] or not sig["has_flag_evidence"],
    }


def first_match(preds, finding):
    for name in ORDER:
        if preds[name]:
            return (finding, name) if name == "adjudicator_finding" else (DECISION[name], name)
    return "APPROVED", "clean_approve"


def ev_best(truth_counts):
    """(decision, ev_raw_points) maximizing expected raw score for a truth mix."""
    best = None
    for pred in ("DENIED", "NEEDS_REVIEW", "APPROVED"):  # safe-first tie-break
        ev = sum(SCORE[t][pred] * n for t, n in truth_counts.items())
        if best is None or ev > best[1]:
            best = (pred, ev)
    return best


def load_truth():
    truth = {}
    with open(CH / "data/train_labels.csv") as f:
        for row in csv.DictReader(f):
            truth[row["case_id"]] = row
    return truth


def truth_predicates(row):
    """Field-based predicates from the LABEL columns (finding/census unknowable)."""
    flags = set(row["risk_flags"].split("|")) if row["risk_flags"] != "none" else set()
    fee = (row["fee_status"] or "unknown").lower()
    visa = row["visa_class"] or None
    sponsor = row["sponsor_id"] or None
    known_non_dip = visa is not None and visa != "DIP-1"
    non_dip_or_unknown = visa != "DIP-1"
    return {
        "adjudicator_finding": False,
        "disqualifying_flag": bool(flags & parse.DISQUALIFYING_FLAGS),
        "embargo_world": row["home_world"] in policy.FULL_EMBARGO_WORLDS,
        "embargo_world_partial": (
            row["home_world"] in policy.PARTIAL_EMBARGO_WORLDS and known_non_dip),
        "revoked_sponsor": sponsor in policy.REVOKED_SPONSORS and known_non_dip,
        "transit_visa": visa == "TRANSIT-7",
        "fee_unpaid": fee == "unpaid",
        "fee_unknown": fee == "unknown",
        "stale_arrival": policy._is_stale(row["arrival_date"]) and known_non_dip,
        "waived_non_dip": fee == "waived" and non_dip_or_unknown,
        "missing_arrival": not row["arrival_date"],
        "review_flag": bool(flags & parse.REVIEW_FLAGS),
        "missing_sponsor": not sponsor and non_dip_or_unknown,
        "missing_visa": not visa,
        "b13_census": False,
    }


def main(cache_path, truth_mode=False):
    dev = set(json.loads((ROOT / "data_splits.json").read_text())["dev"])
    truth = load_truth()
    meta, records = cache.read(cache_path)
    print(f"cache: {cache_path}  restore={meta.get('restore')} rev={meta.get('git_rev')}"
          f"{' DIRTY' if meta.get('git_dirty') else ''}"
          f"{' SUBSET=' + str(meta['subset']) if meta.get('subset') else ''}")

    rows = []  # (case_id, preds, fired_branch, fired_decision, truth_adj)
    mismatches = 0
    for rec in records:
        if rec.get("error") or rec["stem"] not in dev:
            continue
        pages, ocr_lines = cache.to_case(rec["pages"])
        pkt = packet.assemble(pages, ocr_lines, fallback_case_id=rec["stem"])
        values = packet.merge_fields(pkt, {})
        sig = signals.derive(pkt, values)
        preds = predicates(values, sig)
        decision, branch = first_match(preds, sig["finding"])
        real_decision, real_branch = policy.adjudicate(values, sig)
        if (decision, branch) != (real_decision, real_branch):
            mismatches += 1
        rows.append((pkt.case_id, preds, real_branch, real_decision,
                     truth[rec["stem"]]["adjudication"]))
    print(f"{len(rows)} dev cases; probe-vs-policy first-match mismatches: {mismatches}"
          " (must be 0)\n")

    if truth_mode:
        migration = Counter()
        for cid, preds, branch, _, _ in rows:
            t_preds = truth_predicates(truth[cid])
            _, t_branch = first_match(t_preds, None)
            migration[(t_branch, branch)] += 1
        print("== truth-branch (label fields) -> pipeline-branch migration"
              " (diagonal = faithful membership) ==")
        for (tb, pb), n in sorted(migration.items(), key=lambda kv: -kv[1]):
            marker = " " if tb == pb else "*"
            print(f"  {marker} {tb:22s} -> {pb:22s} {n:4d}")
        return

    # 1. Multiplicity
    sizes = Counter(sum(p.values()) for _, p, _, _, _ in rows)
    print("== predicate multiplicity (0 true => clean_approve) ==")
    for k in sorted(sizes):
        print(f"  {k} predicates true: {sizes[k]:4d} cases")

    # 2. Pair co-fire counts
    pairs = Counter()
    for _, preds, _, _, _ in rows:
        live = [n for n in ORDER if preds[n]]
        for i, a in enumerate(live):
            for b in live[i + 1:]:
                pairs[(a, b)] += 1
    print("\n== co-fire pairs (cascade order; * = the pair's decisions differ) ==")
    for (a, b), n in sorted(pairs.items(), key=lambda kv: -kv[1]):
        da, db = DECISION.get(a, "?"), DECISION.get(b, "?")
        marker = "*" if da != db else " "
        print(f"  {marker} {a:22s} + {b:22s} {n:4d}   [{da[:2]}|{db[:2]}]")

    # 3. Per-cell EV audit (cells keyed by the full true-predicate set)
    cells = defaultdict(list)
    for cid, preds, branch, decision, tadj in rows:
        key = tuple(n for n in ORDER if preds[n]) or ("clean_approve",)
        cells[key].append((cid, branch, decision, tadj))
    print("\n== cells where the shipped decision is NOT the cell's EV argmax ==")
    print("   (EV computed on this cell's truth mix; fit on dev, optimistic)")
    total_gap = 0
    for key, members in sorted(cells.items(), key=lambda kv: -len(kv[1])):
        tc = Counter(t for _, _, _, t in members)
        shipped = Counter(d for _, _, d, _ in members)
        # a cell can ship mixed decisions (adjudicator_finding passes its finding through)
        shipped_pts = sum(SCORE[t][d] for _, _, d, t in members)
        best_dec, best_pts = ev_best(tc)
        if best_pts > shipped_pts:
            total_gap += best_pts - shipped_pts
            truth_s = "/".join(f"{t[:2]}:{n}" for t, n in sorted(tc.items()))
            ship_s = "+".join(f"{d[:2]}:{n}" for d, n in sorted(shipped.items()))
            print(f"  n={len(members):3d}  {'&'.join(key)}")
            print(f"        truth {truth_s:24s} shipped {ship_s} ({shipped_pts} raw)"
                  f"  ev-best {best_dec} ({best_pts} raw)  gap +{best_pts - shipped_pts}")
    print(f"\n  total EV gap on populated cells: +{total_gap} raw"
          f" = +{total_gap / 5600 * 80:.2f} dev classification pts (optimistic, in-sample)")

    # 4. The one severity inversion in the shipped order
    inv = [(cid, t) for cid, p, _, _, t in rows if p["fee_unknown"] and p["stale_arrival"]]
    print(f"\n== fee_unknown & stale_arrival (the NR-preempts-DENY inversion) ==")
    print(f"  {len(inv)} dev case(s): {Counter(t for _, t in inv) or '—'}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    main(Path(args[0]) if args else ROOT / "output/cache/train_bands.jsonl",
         truth_mode="--truth" in sys.argv)
