#!/usr/bin/env python3
"""Step 0: A/B candidate field-merge strategies against truth on the ensemble
cache — offline, no OCR. Each strategy produces per-field values; we run them
through the REAL signals+policy so extraction, adjudication and CFA are all
faithful. Ranks strategies before any pipeline change.

  experiments/merge_probe.py [cache.jsonl]   (default output/cache/reads_hard.jsonl)
"""
import csv, sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CH = ROOT.parent / "mib-doc-challenge"

from mib import cache, packet, parse, policy, signals, vocab   # noqa: E402
from mib.packet import SRC_TEXT                                 # noqa: E402
from mib.textmatch import normalize as norm                     # noqa: E402
sys.path.insert(0, str(ROOT / "experiments"))
import dump_reads as dr                                         # noqa: E402

TRUTH = {r["case_id"]: r for r in csv.DictReader(open(CH / "data/train_labels.csv"))}
SCALAR = ["applicant_name", "species_code", "home_world", "visa_class",
          "sponsor_id", "arrival_date", "declared_purpose", "fee_status"]
PLAUSIBLE_YEARS = {"2024", "2025", "2026", "2027"}


def reading_field(lines, f):
    kv = parse.parse_kv(lines)
    for k, v in parse.parse_prose(lines).items():
        kv.setdefault(k, v)
    kv = packet._repair_ocr_kv(dict(kv))
    return kv.get(f)


def case_readings(case_rec):
    return [r["lines"] for p in case_rec["pages"] for r in (p["reads"] or [])]


def vote(field, reads_lines, plausible=False):
    """Plurality over valid, normalized parsed values across readings.
    Returns a representative raw value, or None."""
    counts, rep = Counter(), {}
    for lines in reads_lines:
        v = reading_field(lines, field)
        if not v or not parse.valid_value(field, v):
            continue
        if plausible and field == "arrival_date" and v[:4] not in PLAUSIBLE_YEARS:
            continue                      # drop 1900/2020/2028 misreads
        key = norm(v)
        counts[key] += 1
        rep.setdefault(key, v)
    if not counts:
        return None
    top = max(counts, key=lambda k: counts[k])       # plurality (ties: first-seen)
    return rep[top]


def strat_values(name, pkt, reads_lines):
    """Field values under a strategy. Text-layer values always win (precedence);
    the vote only fills fields the text layer didn't supply."""
    prov = {}
    base = packet.merge_fields(pkt, prov)             # current pipeline (text-preferred)
    if name == "best":
        return base
    out = dict(base)
    for f in SCALAR:
        if prov.get(f, (0, 1))[1] == SRC_TEXT:        # keep clean text-layer value
            continue
        v = vote(f, reads_lines, plausible=(name == "plurality_valid"))
        if v:
            out[f] = v
    return out


STRATS = ["best", "plurality", "plurality_valid"]


def main():
    cpath = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "output/cache/reads_hard.jsonl"
    _meta, recs = cache.read(cpath)
    agg = {s: {"fields": 0, "adj_ok": 0, "cfa": 0, "field_by": Counter()} for s in STRATS}
    n_scored = 0
    for rec in recs:
        cid = rec["stem"]
        if cid not in TRUTH:
            continue
        truth = TRUTH[cid]
        # Since the keystone, "best" here is the live pipeline's own merge
        # (which includes the variant vote), so this probe now measures the
        # raw strategies against the shipped behavior rather than against a
        # winner-take-all baseline.
        pages, reads = dr.reconstruct(rec)
        pkt = packet.assemble(pages, reads, cid)
        reads_lines = case_readings(rec)
        n_scored += 1
        for s in STRATS:
            vals = strat_values(s, pkt, reads_lines)
            sig = signals.derive(pkt, vals)
            decision, _branch = policy.adjudicate(vals, sig)
            for f in SCALAR:
                if norm(vals.get(f)) == norm(truth[f]) and norm(truth[f]) not in ("", "none", "unknown"):
                    agg[s]["fields"] += 1
                    agg[s]["field_by"][f] += 1
            agg[s]["adj_ok"] += (decision == truth["adjudication"])
            agg[s]["cfa"] += (truth["adjudication"] == "DENIED" and decision == "APPROVED")

    print(f"scored {n_scored} cases from {cpath.name}\n")
    print(f"{'strategy':16s} {'fields':>7s} {'adj_ok':>7s} {'CFA':>4s}")
    for s in STRATS:
        a = agg[s]
        print(f"{s:16s} {a['fields']:>7d} {a['adj_ok']:>7d} {a['cfa']:>4d}")
    base = agg["best"]["field_by"]
    print("\nper-field recovery vs best (Δ fields matched):")
    for f in SCALAR:
        b = base[f]
        row = "  ".join(f"{s}={agg[s]['field_by'][f]-b:+d}" for s in STRATS if s != "best")
        print(f"  {f:18s} best={b:>3d}   {row}")


if __name__ == "__main__":
    main()
