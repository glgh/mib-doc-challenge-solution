#!/usr/bin/env python3
"""Price the flags half of deferred variant selection — offline, no OCR.

`best()` collapses each scan page to one reading before signals ever scans for
risk flags, so a flag legible only in a losing variant is thrown away. This
probe replays the ensemble cache (experiments/dump_reads.py) and A/Bs flag
strategies through the REAL signals+policy stack:

  best     shipped baseline — flags from the winning reading only
  union    baseline ∪ every flag asserted by ANY variant reading (k>=1)
  quorum2  baseline ∪ flags asserted by >=2 variant readings

Per-variant scanning mirrors `packet.assemble`: the decoy-page filter (case-id
mismatch, with the OCR plausible-misread tolerance) applies per reading, and
`_flags_in_line`'s legend/negation guards run per line as in production. (The
flag-doc-type gate this probe used to mirror was deleted from production —
BACKGROUND §3: P=1.00 with the gate removed; the guards are the safety
mechanism.) risk_flags is scored the way scripts/evaluate.py does: exact set
equality, all-or-nothing.

  experiments/flag_probe.py [cache.jsonl]   (default output/cache/reads_hard.jsonl)

`--values` mode is the safety table for the whole-value resolver (TODO 2.2):
every `Observed flags:`-labelled value across the ensemble that the token
matcher did NOT already resolve, scored by `vocab.match_flag_value` (argmax
flag, weighted score, margin) with cross-variant agreement per page, joined to
truth. Thresholds are chosen off this table, never guessed.

  experiments/flag_probe.py --values [cache.jsonl]   (default train_bands.jsonl)
"""
import csv
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CH = ROOT.parent / "mib-doc-challenge"

from mib import cache, packet, parse, policy, signals, textmatch  # noqa: E402
sys.path.insert(0, str(ROOT / "experiments"))
import dump_reads as dr                                           # noqa: E402

TRUTH = {r["case_id"]: r for r in csv.DictReader(open(CH / "data/train_labels.csv"))}
STRATS = ["best", "union", "quorum2"]


def flag_set(v):
    """Pipe-delimited field -> set, the way evaluate.py normalizes it."""
    return {t.strip().lower() for t in (v or "").split("|")
            if t.strip() and t.strip().lower() != "none"}


def variant_flag_reads(case_rec, case_id):
    """(doc_type, flags) per variant reading that would bear flags in assemble:
    same decoy filter; no doc-type gate (deleted from production)."""
    out = []
    for p in case_rec["pages"]:
        for r in (p["reads"] or []):
            lines = r["lines"]
            ids = set(parse.page_case_ids(lines))
            if ids and case_id not in ids and \
                    not any(textmatch.plausible_misread(case_id, i) for i in ids):
                continue                                  # decoy page for another applicant
            dtype = parse.detect_doc_type(lines)
            flags = set()
            for line in lines:
                flags |= signals._flags_in_line(line)
            if flags:
                out.append((dtype, flags))
    return out


def strat_observed(name, base_observed, fr):
    if name == "best":
        return set(base_observed)
    counts = Counter(f for _, flags in fr for f in flags)
    need = 1 if name == "union" else 2
    return set(base_observed) | {f for f, n in counts.items() if n >= need}


_KV_RE = __import__("re").compile(r"^([A-Za-z0-9][A-Za-z _0-9]{1,28}?)\s*[:.;]\s*(.+)$")


def value_table(cpath):
    """--values mode: mine every labelled observed-flags value the token matcher
    missed, with truth verdicts and per-page cross-variant agreement."""
    import json

    from mib import vocab

    dev = set(json.loads((ROOT / "data_splits.json").read_text())["dev"])
    _meta, recs = cache.read(cpath)
    rows = []
    for rec in recs:
        cid = rec.get("stem")
        if cid not in TRUTH or rec.get("error"):
            continue
        tflags = flag_set(TRUTH[cid]["risk_flags"])
        for p in rec["pages"]:
            for r in (p.get("reads") or []):
                lines = r["lines"]
                ids = set(parse.page_case_ids(lines))
                if ids and cid not in ids and \
                        not any(textmatch.plausible_misread(cid, i) for i in ids):
                    continue                      # decoy page, as in assemble
                for line in lines:
                    m = _KV_RE.match(line.strip())
                    if not m:
                        continue
                    strict = parse.key_for(m.group(1)) == "observed_flags"
                    loose = parse._loose_key_for(m.group(1)) == "observed_flags"
                    if not (strict or loose):
                        continue
                    if signals._flags_in_line(line):
                        continue                  # token path already resolves it
                    value = m.group(2).strip()
                    flag, score, margin = vocab.match_flag_value(value)
                    rows.append(dict(
                        case=cid, split="dev" if cid in dev else "hold",
                        page=p["page_no"], variant=r["variant"], strict=strict,
                        value=value, flag=flag, score=score, margin=margin,
                        true=(flag in tflags)))
    # Cross-variant agreement: independent readings of one page whose values
    # argmax to the same flag (margin-guarded at 0.1 to keep debris out).
    agree = Counter()
    for row in rows:
        if row["flag"] and row["margin"] >= 0.1:
            agree[(row["case"], row["page"], row["flag"])] += 1
    for row in rows:
        row["agree"] = agree.get((row["case"], row["page"], row["flag"]), 0)
    rows.sort(key=lambda r: (-r["score"], r["case"]))
    print(f"{len(rows)} unresolved labelled values "
          f"({sum(r['true'] for r in rows)} argmax-true) from {cpath.name}")
    print(f"{'case':10s} {'sp':4s} pg {'strict':6s} {'flag':22s} "
          f"{'score':>5s} {'marg':>5s} {'agr':>3s} {'true':>5s}  value")
    for r in rows:
        print(f"{r['case']:10s} {r['split']:4s} {r['page']:2d} "
              f"{str(r['strict']):6s} {str(r['flag']):22s} {r['score']:5.2f} "
              f"{r['margin']:5.2f} {r['agree']:3d} {str(r['true']):>5s}  "
              f"{r['value'][:48]}")
    return rows


def main():
    cpath = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "output/cache/reads_hard.jsonl"
    _meta, recs = cache.read(cpath)
    agg = {s: Counter() for s in STRATS}
    n_scored = 0
    truth_missed = truth_recovered = 0          # flag instances best misses / union rescues
    diffs = []
    for rec in recs:
        cid = rec["stem"]
        if cid not in TRUTH:
            continue
        truth = TRUTH[cid]
        tflags = flag_set(truth["risk_flags"])
        # Since the keystone, the baseline packet already carries variant_docs
        # (and after the flag-union change, emit_flags includes them), so the
        # "best" row here is the shipped behavior, not winner-take-all.
        pages, reads = dr.reconstruct(rec)
        pkt = packet.assemble(pages, reads, cid)
        values = packet.merge_fields(pkt)
        sig = signals.derive(pkt, values)
        inferred = sig["flags"] - sig["emit_flags"]
        fr = variant_flag_reads(rec, pkt.case_id)
        n_scored += 1
        missed = tflags - sig["emit_flags"]
        truth_missed += len(missed)
        truth_recovered += len(missed & strat_observed("union", sig["emit_flags"], fr))
        for s in STRATS:
            observed = strat_observed(s, sig["emit_flags"], fr)
            has_ev = sig["has_flag_evidence"] or any(
                d == parse.DOC_BIOMETRIC and flags & observed for d, flags in fr)
            sig2 = {**sig, "emit_flags": observed, "flags": inferred | observed,
                    "has_flag_evidence": has_ev}
            decision, _branch = policy.adjudicate(values, sig2)
            a = agg[s]
            a["rf_match"] += (observed == tflags)
            a["adj_ok"] += (decision == truth["adjudication"])
            a["cfa"] += (truth["adjudication"] == "DENIED" and decision == "APPROVED")
            a["flags_true"] += len((observed - sig["emit_flags"]) & tflags)
            a["flags_false"] += len((observed - sig["emit_flags"]) - tflags)
            if s != "best" and observed != sig["emit_flags"]:
                base_ok = sig["emit_flags"] == tflags
                diffs.append((cid, s, sorted(observed - sig["emit_flags"]),
                              tflags, base_ok, observed == tflags))

    print(f"scored {n_scored} cases from {cpath.name}")
    print(f"truth-flag instances best misses entirely: {truth_missed} "
          f"(union recovers {truth_recovered})\n")
    print(f"{'strategy':10s} {'rf_match':>8s} {'adj_ok':>7s} {'CFA':>4s} "
          f"{'+true':>6s} {'+false':>7s}")
    for s in STRATS:
        a = agg[s]
        print(f"{s:10s} {a['rf_match']:>8d} {a['adj_ok']:>7d} {a['cfa']:>4d} "
              f"{a['flags_true']:>6d} {a['flags_false']:>7d}")
    if diffs:
        print("\ncases where a strategy adds flags (vs best):")
        for cid, s, added, tflags, base_ok, now_ok in diffs:
            verdicts = ",".join(f"{f}({'TRUE' if f in tflags else 'FALSE'})" for f in added)
            print(f"  {cid} {s:8s} +{verdicts:50s} rf {'ok' if base_ok else 'X'}"
                  f"->{'ok' if now_ok else 'X'}")


if __name__ == "__main__":
    if "--values" in sys.argv:
        sys.argv.remove("--values")
        value_table(Path(sys.argv[1]) if len(sys.argv) > 1
                    else ROOT / "output/cache/train_bands.jsonl")
    else:
        main()
