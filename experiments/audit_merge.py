#!/usr/bin/env python3
"""Merge-rule structure audit: ablate each accreted merge/validity rule
individually and replay S3-S5 against the grid cache (STATUS question 8 at the
merge seam — the constants audit covers label-fitted numbers, this covers which
RULES exist).

For each rule the ablation REMOVES it and measures on dev, so the verdict reads:
  carries = fields the rule is winning right now (removal breaks them)
  harms   = fields the rule is actively losing (removal fixes them)
  lateral = churn where neither side matches truth
A rule with carries=0 is dead weight on the current substrate — a deletion
candidate regardless of its historical row. Split-half consistency (case-id
parity) marks whether a rule's wins concentrate in one half — thin evidence.

The function copies (`make_vote`, the merge_fields copy) are verified no-ops by
the `control` command before any ablation number is read.

Usage:
  .venv/bin/python experiments/audit_merge.py list
  .venv/bin/python experiments/audit_merge.py control [--cache C]
  .venv/bin/python experiments/audit_merge.py run [names...] [--cache C] [--no-score]

Artifacts: output/eval/audit_<name>/ (predictions + official dev score),
output/viz/audit_merge.jsonl (per-field diff detail, truth-marked).
"""
import argparse
import contextlib
import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
CH = ROOT.parent / "mib-doc-challenge"

from mib import cache, config, emit, packet, parse, runner, signals, textmatch, vocab  # noqa: E402

EVAL_BASE = ROOT / "output/eval"
OUT = ROOT / "output/viz/audit_merge.jsonl"
DEV = set(json.loads((ROOT / "data_splits.json").read_text())["dev"])
TRUTH = {r["case_id"]: r for r in csv.DictReader(open(CH / "data/train_labels.csv"))}
FIELDS = ["applicant_name", "species_code", "home_world", "visa_class",
          "sponsor_id", "arrival_date", "declared_purpose", "fee_status",
          "risk_flags", "adjudication"]


# --- patch plumbing ----------------------------------------------------------

@contextlib.contextmanager
def patched(*patches):
    """patches: (module, attr_name, replacement). Restores on exit."""
    saved = [(m, n, getattr(m, n)) for m, n, _v in patches]
    try:
        for m, n, v in patches:
            setattr(m, n, v)
        yield
    finally:
        for m, n, v in saved:
            setattr(m, n, v)


# --- parametrized copy of packet._variant_vote -------------------------------
# One copy drives every vote ablation; `control` proves the all-on form is a
# no-op against shipped. Keep in sync with packet._variant_vote.

def make_vote(colon_filter=True, edge_keys=True, collapse=True,
              conf_tiebreak=True, page_balance=True, vocab_rank="first"):
    """vocab_rank: 'first' (shipped: membership tested on the first-seen
    representative), 'any' (any entry snappable counts), or 'off'."""

    def vote(field_name, kvs):
        groups = {}
        for seq, kv in enumerate(kvs):
            v = kv.get(field_name)
            if not v or not parse.valid_value(field_name, v):
                continue
            if colon_filter and re.search(r":\s*\S", v):
                continue
            key = packet._vote_key(v) if edge_keys else textmatch.normalize(v)
            if not key:
                continue
            norm = textmatch.normalize(v)
            clean = norm == packet._edge_strip(norm)
            groups.setdefault(key, []).append(
                (v, clean, packet._line_conf(kv, v), seq, kv.get("_page_no")))
        if field_name == "applicant_name" and collapse:
            collapsed = {}
            for key, entries in groups.items():
                collapsed.setdefault(packet._collapse(key), []).extend(entries)
            groups = collapsed
        if not groups:
            return None, 0

        def snap_ok(rep):
            return (vocab.repairable_purpose(rep)
                    if field_name == "declared_purpose"
                    else vocab.snap(field_name, rep) is not None)

        def group_rank(entries):
            snappable = True
            if vocab_rank != "off" and field_name in (
                    "declared_purpose", "species_code", "home_world",
                    "visa_class", "fee_status"):
                if vocab_rank == "any":
                    snappable = any(snap_ok(e[0]) for e in entries)
                else:
                    snappable = snap_ok(entries[0][0])
            pages = len({p for *_x, p in entries}) if page_balance else 0
            best_conf = (max((c for _v, _cl, c, _s, _p in entries
                              if c is not None), default=-1.0)
                         if conf_tiebreak else -1.0)
            first_seen = min(s for _v, _cl, _c, s, _p in entries)
            return (not snappable, -pages, -len(entries), -best_conf, first_seen)

        entries = min(groups.values(), key=group_rank)

        def rep_rank(e):
            v, clean, conf, seq, _page = e
            return (not clean, -len(textmatch.normalize(v)),
                    -(conf if conf is not None else -1.0), seq)

        return min(entries, key=rep_rank)[0], len(entries)

    return vote


# --- merge_fields copy (shield switchable) -----------------------------------
# Keep in sync with packet.merge_fields; `control` proves shield=True is a no-op.

def make_merge(shield=True):
    def merge(pkt, provenance=None):
        prov = {} if provenance is None else provenance
        values = {}
        for cand in sorted(packet.candidates(pkt), key=packet._preference):
            if not cand.valid or cand.field_name in values:
                continue
            values[cand.field_name] = cand.value
            prov[cand.field_name] = (cand.doc_type, cand.source)
        ocr_kvs = ([kv for _, kv in pkt.variant_docs] +
                   [kv for _, src, kv in pkt.docs if src == packet.SRC_OCR])
        if ocr_kvs:
            for fname in parse.FIELDS:
                if shield and prov.get(fname, (0, packet.SRC_OCR))[1] == packet.SRC_TEXT:
                    continue  # clean text-layer value stays
                value, _agree = packet._variant_vote(fname, ocr_kvs)
                if value:
                    values[fname] = value.strip()
                    prov[fname] = (packet.VOTE_DOC, packet.SRC_OCR)
        challenged = packet._name_corroboration(pkt, values.get("applicant_name"))
        if challenged is not None:
            values["applicant_name"] = challenged[0].strip()
            prov["applicant_name"] = challenged[1]
        for fname, value in packet.manual_corrections(pkt).items():
            values[fname] = value
            prov[fname] = (0, 0)
        return values
    return merge


# --- valid_value wrappers (rules that live inline in parse.valid_value) ------

_ORIG_VALID = parse.valid_value


def vv_no_name_shape(field, value):
    """Pre-row-64 applicant_name validity: base + marker checks only."""
    if field == "applicant_name":
        if not value or value.lower() in ("n/a", "unknown", ""):
            return False
        return not (parse._DAMAGE_MARKER_RE.match(value.strip())
                    or parse._damage_markerish(value))
    return _ORIG_VALID(field, value)


def vv_no_year_window(field, value):
    """Pre-row-62 arrival_date validity: any real ISO date."""
    if field == "arrival_date":
        if not value or not parse.DATE_RE.fullmatch(value):
            return False
        from datetime import date
        try:
            date.fromisoformat(value)
        except ValueError:
            return False
        return True
    return _ORIG_VALID(field, value)


# --- signals.derive wrapper --------------------------------------------------

_ORIG_DERIVE = signals.derive


def derive_biometric_primary_only(pkt, values):
    """Pre-row-60 has_biometric: primary docs only, losing variants ignored."""
    sig = _ORIG_DERIVE(pkt, values)
    sig["has_biometric"] = pkt.has_doc(parse.DOC_BIOMETRIC)
    return sig


# --- the registry ------------------------------------------------------------
# name -> (row it ships from, what removal means, [patches])

ABLATIONS = {
    "pref_trust_first": (
        "15b", "reverse _preference to (doc_type, source) — trust before source",
        lambda: [(packet, "_preference", lambda c: (c.doc_type, c.source))]),
    "vote_off": (
        "30", "no variant vote at all — doc merge + corroboration only",
        lambda: [(packet, "_variant_vote", lambda f, kvs: (None, 0))]),
    "vote_exact_keys": (
        "45", "vote keys without edge-punctuation pooling",
        lambda: [(packet, "_variant_vote", make_vote(edge_keys=False))]),
    "vote_no_collapse": (
        "49", "no rn/m ri/n stroke-collapse pooling for names",
        lambda: [(packet, "_variant_vote", make_vote(collapse=False))]),
    "vote_keep_colon": (
        "49", "inner-colon values allowed to vote",
        lambda: [(packet, "_variant_vote", make_vote(colon_filter=False))]),
    "vote_no_conf_tiebreak": (
        "49", "group ties fall to first-seen, engine conf unused",
        lambda: [(packet, "_variant_vote", make_vote(conf_tiebreak=False))]),
    "vote_no_page_balance": (
        "58", "rank groups by read count, not distinct pages",
        lambda: [(packet, "_variant_vote", make_vote(page_balance=False))]),
    "vote_no_vocab_rank": (
        "60", "unsnappable groups compete equally on closed-vocab fields",
        lambda: [(packet, "_variant_vote", make_vote(vocab_rank="off"))]),
    "vote_pre_grid_rank": (
        "58+60", "both grid-era rank keys off (the row-49 rank)",
        lambda: [(packet, "_variant_vote",
                  make_vote(page_balance=False, vocab_rank="off"))]),
    "vote_vocab_rank_any": (
        "60v", "VARIANT not ablation: snappable if ANY entry snaps, not first",
        lambda: [(packet, "_variant_vote", make_vote(vocab_rank="any"))]),
    "no_corroboration": (
        "63", "no multi-document name challenge",
        lambda: [(packet, "_name_corroboration", lambda p, inc: None)]),
    "no_name_shape": (
        "64", "applicant_name validity without the two-token census shape",
        lambda: [(parse, "valid_value", vv_no_name_shape)]),
    "no_year_window": (
        "62", "arrival_date validity without the 2020-2030 year window",
        lambda: [(parse, "valid_value", vv_no_year_window)]),
    "no_markerish": (
        "58a", "damage markers rejected only by the strict [..] regex",
        lambda: [(parse, "_damage_markerish", lambda v: False)]),
    "no_text_shield": (
        "30", "vote may overrule clean text-layer values",
        lambda: [(packet, "merge_fields", make_merge(shield=False))]),
    "fee_no_unknown_tier": (
        "61", "fee ladder without the explicit-unknown statement tier",
        lambda: [(packet, "_fee_unknown_stated", lambda lines: False)]),
    "fee_fallback_off": (
        "54+61", "no fee imputation — silent fee displays as unknown",
        lambda: [(packet, "fee_fallback", lambda p: "unknown")]),
    "biometric_primary_only": (
        "60", "has_biometric ignores losing variants",
        lambda: [(signals, "derive", derive_biometric_primary_only)]),
}

CONTROLS = {
    "control_vote_copy": lambda: [(packet, "_variant_vote", make_vote())],
    "control_merge_copy": lambda: [(packet, "merge_fields", make_merge(shield=True))],
}


# --- replay + scoring --------------------------------------------------------

def load_cache(cache_path):
    meta, recs = cache.read(cache_path)
    config.require_agreement([(str(cache_path), meta)])
    if meta and meta.get("restore") != "grid":
        print(f"WARNING: cache restore={meta.get('restore')!r}, audit rows are "
              f"quoted for the grid substrate")
    return meta, [r for r in recs if not r.get("error")]


def replay(recs):
    out = []
    for rec in recs:
        pages, reads_by_page = cache.to_case(rec["pages"])
        record, _debug = runner.predict_from_evidence(pages, reads_by_page,
                                                      rec["stem"])
        out.append(record)
    return {r["case_id"]: r for r in emit.dedupe(out)}


def score_dir(name, preds, cmeta, cache_path, do_score=True):
    out_dir = EVAL_BASE / f"audit_{name}"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "predictions.jsonl").write_text(
        "".join(json.dumps(preds[c], sort_keys=True) + "\n" for c in sorted(preds)))
    (out_dir / "meta.json").write_text(json.dumps(config.stamp(
        artifact="eval", probe=f"audit_merge:{name}",
        restore=(cmeta or {}).get("restore"),
        replayed_from=str(cache_path)), indent=2, sort_keys=True) + "\n")
    if not do_score:
        return None
    import subprocess
    truth_rows = [r for r in TRUTH.values() if r["case_id"] in DEV]
    truth_path = out_dir / "truth_dev.csv"
    with open(truth_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(truth_rows[0].keys()))
        w.writeheader()
        w.writerows(sorted(truth_rows, key=lambda r: r["case_id"]))
    pred_path = out_dir / "predictions_dev.jsonl"
    pred_path.write_text("".join(
        json.dumps(preds[c], sort_keys=True) + "\n"
        for c in sorted(preds) if c in DEV))
    r = subprocess.run(
        [sys.executable, str(CH / "scripts/evaluate.py"),
         "--truth", str(truth_path), "--submission", str(pred_path),
         "--output-json", str(out_dir / "evaluation_dev.json")],
        capture_output=True, text=True)
    if r.returncode not in (0,):
        print(f"  evaluate.py rc={r.returncode}: {r.stderr.strip()[:200]}")
    ev = json.loads((out_dir / "evaluation_dev.json").read_text())["scores"]
    return {k: round(ev[k], 2) for k in
            ("total_score", "classification_score", "extraction_score",
             "calibration_score")}


def diff_rows(name, base, alt):
    rows = []
    for cid in sorted(set(base) & set(alt) & DEV):
        for f in FIELDS:
            a, b = base[cid].get(f), alt[cid].get(f)
            if a == b:
                continue
            t = TRUTH.get(cid, {}).get(f)
            verdict = ("harms" if b == t else "carries" if a == t else "lateral")
            rows.append({"rule": name, "case_id": cid, "field": f,
                         "shipped": a, "ablated": b, "truth": t,
                         "verdict": verdict})
    return rows


def cfa_count(preds):
    return sum(1 for cid, p in preds.items()
               if cid in DEV and p.get("adjudication") == "APPROVED"
               and TRUTH.get(cid, {}).get("adjudication") == "DENIED")


def summarize(name, row_desc, rows, scores, base_cfa, alt_cfa):
    carries = [r for r in rows if r["verdict"] == "carries"]
    harms = [r for r in rows if r["verdict"] == "harms"]
    lateral = [r for r in rows if r["verdict"] == "lateral"]
    adj = [r for r in rows if r["field"] == "adjudication"]

    def halves(sub):
        h = [0, 0]
        for r in sub:
            h[int(r["case_id"][4:]) % 2] += 1
        return f"{h[0]}/{h[1]}"

    return {
        "rule": name, "row": row_desc[0], "removal_means": row_desc[1],
        "carries": len(carries), "harms": len(harms), "lateral": len(lateral),
        "adj_moves": len(adj), "cfa_delta": alt_cfa - base_cfa,
        "carries_by_half": halves(carries), "harms_by_half": halves(harms),
        "dev": scores,
    }


def run(cache_path, names, do_score=True):
    cmeta, recs = load_cache(cache_path)
    print(f"{len(recs)} cases from {cache_path}")
    base = replay(recs)
    base_scores = score_dir("baseline", base, cmeta, cache_path, do_score)
    base_cfa = cfa_count(base)
    print(f"baseline dev: {base_scores}  CFA={base_cfa}")

    all_rows, summaries = [], []
    for name in names:
        row_desc = ABLATIONS[name][:2]
        with patched(*ABLATIONS[name][2]()):
            alt = replay(recs)
        rows = diff_rows(name, base, alt)
        scores = score_dir(name, alt, cmeta, cache_path, do_score)
        s = summarize(name, row_desc, rows, scores, base_cfa, cfa_count(alt))
        summaries.append(s)
        all_rows.extend(rows)
        print(f"{name:24s} carries={s['carries']:<3} harms={s['harms']:<3} "
              f"lateral={s['lateral']:<3} adj={s['adj_moves']:<2} "
              f"dCFA={s['cfa_delta']:+d}  halves C {s['carries_by_half']} "
              f"H {s['harms_by_half']}  "
              f"dev={scores['total_score'] if scores else '-'}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w") as f:
        for s in summaries:
            f.write(json.dumps({"summary": s}) + "\n")
        for r in all_rows:
            f.write(json.dumps(r) + "\n")
    print(f"\nwrote {len(summaries)} summaries + {len(all_rows)} diff rows to {OUT}")

    dead = [s["rule"] for s in summaries
            if s["carries"] == 0 and s["rule"] != "vote_vocab_rank_any"]
    if dead:
        print(f"deletion candidates (carries=0 on this substrate): {dead}")


def control(cache_path):
    cmeta, recs = load_cache(cache_path)
    base = replay(recs)
    ok = True
    for name, patches in CONTROLS.items():
        with patched(*patches()):
            alt = replay(recs)
        rows = diff_rows(name, base, alt)
        status = "IDENTICAL" if not rows else f"{len(rows)} DIFFS — copy drift!"
        ok = ok and not rows
        print(f"{name:22s} {status}")
        for r in rows[:10]:
            print(f"    {r['case_id']}.{r['field']}: "
                  f"{r['shipped']!r} -> {r['ablated']!r}")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=["list", "control", "run"])
    ap.add_argument("names", nargs="*")
    ap.add_argument("--cache",
                    default=str(ROOT / "output/cache/train_grid.jsonl"))
    ap.add_argument("--no-score", action="store_true",
                    help="skip the official scorer (diff counts only)")
    a = ap.parse_args()
    if a.cmd == "list":
        for n, (row, desc, _p) in ABLATIONS.items():
            print(f"{n:24s} row {row:6s} removal = {desc}")
    elif a.cmd == "control":
        sys.exit(control(a.cache))
    else:
        run(a.cache, a.names or list(ABLATIONS), do_score=not a.no_score)
