#!/usr/bin/env python3
"""Layer C3 probe: do cross-document DISAGREEMENT and INJECTION PRESENCE predict
the true adjudication (and our errors)? Directional, n~=60 stratified dev sample.

Read-only measurement. Runs the real runner path (read_case -> assemble ->
predict_from_evidence), inspects packet.candidates() for discarded conflicts, and
Page.hidden_lines for injection presence. Never sources a value from hidden text.

Usage: scripts/crossdoc_probe.py [n]
"""
import csv
import json
import random
import sys
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CH = ROOT.parent / "mib-doc-challenge"
TRAIN = CH / "data" / "train"

from mib import packet, runner, signals  # noqa: E402
from mib.parse import FIELDS  # noqa: E402
from mib.textmatch import normalize  # noqa: E402

# --- disagreement config ---------------------------------------------------
# Structured/closed fields: a normalized difference is a genuinely different
# entity (OCR variants get vocab-snapped upstream in assemble._repair_ocr_kv),
# so we compare exactly. Free-text fields: OCR noise ("Miravoss"/"Mirayoss")
# must NOT count as a conflict, so we cluster by similarity and only call it a
# conflict when two docs differ by more than the OCR-noise threshold.
STRUCTURED = {"sponsor_id", "arrival_date", "visa_class", "fee_status", "species_code"}
FUZZY = {"applicant_name", "home_world", "declared_purpose"}
FUZZY_SAME = 0.85  # ratio >= this => same value (OCR variant), below => real conflict

ADJ_TOKENS = ("APPROVED", "DENIED", "NEEDS_REVIEW")
INJECTION_HINTS = ("ignore", "system", "prompt", "assistant", "answer key",
                   "answer_key", "confidence", "instruction", "you are", "must output",
                   "correct answer", "override")


def _same_fuzzy(a, b):
    return SequenceMatcher(None, a, b).ratio() >= FUZZY_SAME


def field_disagreements(pkt):
    """-> {field: [distinct normalized values]} for fields where >=2 DOCUMENTS
    supply conflicting VALID values. Documents keyed by page_no."""
    per_field_docvals = defaultdict(dict)  # field -> {page_no: set(norm values)}
    for cand in packet.candidates(pkt):
        if not cand.valid:
            continue
        v = normalize(cand.value)
        if not v:
            continue
        per_field_docvals[cand.field_name].setdefault(cand.page_no, set()).add(v)

    out = {}
    for fname, docvals in per_field_docvals.items():
        # one representative value set per document (page); union then cluster
        all_vals = sorted({v for s in docvals.values() for v in s})
        if len(all_vals) < 2:
            continue
        # cluster into distinct value groups
        clusters = []  # list of representative strings
        for v in all_vals:
            placed = False
            for c in clusters:
                same = (v == c) if fname in STRUCTURED else _same_fuzzy(v, c)
                if same:
                    placed = True
                    break
            if not placed:
                clusters.append(v)
        if len(clusters) < 2:
            continue
        # require the conflicting clusters to come from >=2 distinct documents
        docs_supplying = set()
        for pno, vals in docvals.items():
            docs_supplying.add(pno)
        if len(docs_supplying) < 2:
            continue
        out[fname] = clusters
    return out


def injection_scan(pages):
    """-> (has_hidden, has_answerkey, sample_line)."""
    hidden = [ln for p in pages for ln in p.hidden_lines if ln.strip()]
    has_hidden = bool(hidden)
    answerkey = False
    sample = ""
    for ln in hidden:
        low = ln.lower()
        if any(tok in ln for tok in ADJ_TOKENS) or any(h in low for h in INJECTION_HINTS):
            answerkey = True
            if not sample:
                sample = ln[:120]
            break
    if not sample and hidden:
        sample = hidden[0][:120]
    return has_hidden, answerkey, sample


def analyze_one(stem):
    pdf = TRAIN / f"{stem}.pdf"
    try:
        pages, ocr_lines = runner.read_case(pdf)
        pkt = packet.assemble(pages, ocr_lines, stem)
        values = packet.merge_fields(pkt)
        sig = signals.derive(pkt, values)
        record, debug = runner.predict_from_evidence(pages, ocr_lines, stem)
        disagree = field_disagreements(pkt)
        has_hidden, answerkey, sample = injection_scan(pages)
        return {
            "stem": stem,
            "pred": record["adjudication"],
            "disagree": {k: v for k, v in disagree.items()},
            "sig_flags": sorted(sig["flags"]),
            "has_hidden": has_hidden,
            "answerkey": answerkey,
            "sample": sample,
            "n_docs": len(pkt.docs),
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001
        return {"stem": stem, "error": f"{type(exc).__name__}: {exc}"}


def main(n=60):
    labels = {r["case_id"]: r for r in csv.DictReader(open(CH / "data/train_labels.csv"))}
    dev = json.load(open(ROOT / "data_splits.json"))["dev"]
    by_adj = defaultdict(list)
    for cid in dev:
        by_adj[labels[cid]["adjudication"]].append(cid)

    rng = random.Random(8090)
    total_dev = len(dev)
    sample = []
    for adj, cids in sorted(by_adj.items()):
        k = round(n * len(cids) / total_dev)
        picks = rng.sample(sorted(cids), k)
        sample.extend(picks)
    sample = sorted(set(sample))
    print(f"# sample n={len(sample)} stratified over truth adjudication (seed 8090)")
    strat = Counter(labels[c]["adjudication"] for c in sample)
    print(f"# strata: {dict(strat)}")

    with Pool(4) as pool:
        results = pool.map(analyze_one, sample)

    errs = [r for r in results if r["error"]]
    results = [r for r in results if not r["error"]]
    for r in results:
        r["truth"] = labels[r["stem"]]["adjudication"]
        r["truth_flags"] = set(labels[r["stem"]]["risk_flags"].split("|")) - {"none"}
        r["correct"] = (r["pred"] == r["truth"])
    if errs:
        print(f"# {len(errs)} read errors: {[(e['stem'], e['error']) for e in errs][:5]}")

    N = len(results)
    base_nr = sum(1 for r in results if r["truth"] == "NEEDS_REVIEW") / N
    base_denied = sum(1 for r in results if r["truth"] == "DENIED") / N
    base_appr = sum(1 for r in results if r["truth"] == "APPROVED") / N
    base_err = sum(1 for r in results if not r["correct"]) / N
    print(f"\n## base rates (n={N}): "
          f"P(NR)={base_nr:.2f} P(DENIED)={base_denied:.2f} P(APPROVED)={base_appr:.2f} "
          f"P(we_wrong)={base_err:.2f}")

    # ============ 1. CROSS-DOC DISAGREEMENT ============
    print("\n" + "=" * 70)
    print("## 1. CROSS-DOC DISAGREEMENT")
    print("=" * 70)

    any_dis = [r for r in results if r["disagree"]]
    print(f"\ncases with disagreement on ANY field: {len(any_dis)}/{N}")
    if any_dis:
        p_nr = sum(1 for r in any_dis if r["truth"] == "NEEDS_REVIEW") / len(any_dis)
        print(f"  P(NR | any disagreement) = {p_nr:.2f}   vs base {base_nr:.2f}   "
              f"(lift {p_nr - base_nr:+.2f})")
        print(f"  truth breakdown of disagreement cases: "
              f"{dict(Counter(r['truth'] for r in any_dis))}")

    # per-field table
    print(f"\n{'field':<18}{'#dis':>5}{'P(NR|dis)':>11}{'lift':>7}"
          f"{'idconf':>8}{'spmis':>7}{'demote_cost':>12}{'mask_DENIED':>12}")
    for fname in FIELDS:
        hits = [r for r in results if fname in r["disagree"]]
        if not hits:
            continue
        p_nr = sum(1 for r in hits if r["truth"] == "NEEDS_REVIEW") / len(hits)
        idconf = sum(1 for r in hits if "identity_conflict" in r["truth_flags"])
        spmis = sum(1 for r in hits if "sponsor_mismatch" in r["truth_flags"])
        # demotion cost: true APPROVED/DENIED we currently get RIGHT, would lose 8->2
        demote = sum(1 for r in hits
                     if r["truth"] in ("APPROVED", "DENIED") and r["correct"])
        # masking a correctly-deniable case: true DENIED we currently DENY correctly
        mask_den = sum(1 for r in hits
                       if r["truth"] == "DENIED" and r["pred"] == "DENIED")
        print(f"{fname:<18}{len(hits):>5}{p_nr:>11.2f}{p_nr - base_nr:>+7.2f}"
              f"{idconf:>8}{spmis:>7}{demote:>12}{mask_den:>12}")

    # alignment: name-disagreement vs identity_conflict; sponsor-disagreement vs sponsor_mismatch
    print("\nalignment of specific disagreements with truth review-flags:")
    name_dis = [r for r in results if "applicant_name" in r["disagree"]]
    spn_dis = [r for r in results if "sponsor_id" in r["disagree"]]
    truth_idconf = [r for r in results if "identity_conflict" in r["truth_flags"]]
    truth_spmis = [r for r in results if "sponsor_mismatch" in r["truth_flags"]]
    print(f"  applicant_name disagreement: {len(name_dis)}; "
          f"of them truth has identity_conflict: "
          f"{sum(1 for r in name_dis if 'identity_conflict' in r['truth_flags'])}")
    print(f"  truth identity_conflict cases: {len(truth_idconf)}; "
          f"of them name-disagreement detected: "
          f"{sum(1 for r in truth_idconf if 'applicant_name' in r['disagree'])} "
          f"(current sig catches: "
          f"{sum(1 for r in truth_idconf if 'identity_conflict' in r['sig_flags'])})")
    print(f"  sponsor_id disagreement: {len(spn_dis)}; "
          f"of them truth has sponsor_mismatch: "
          f"{sum(1 for r in spn_dis if 'sponsor_mismatch' in r['truth_flags'])}")
    print(f"  truth sponsor_mismatch cases: {len(truth_spmis)}; "
          f"of them sponsor-disagreement detected: "
          f"{sum(1 for r in truth_spmis if 'sponsor_id' in r['disagree'])} "
          f"(current sig catches: "
          f"{sum(1 for r in truth_spmis if 'sponsor_mismatch' in r['sig_flags'])})")

    # cost + CFA safety of a blanket demote-on-any-disagreement policy
    would_demote = [r for r in any_dis if r["pred"] != "NEEDS_REVIEW"]
    lose_right = [r for r in would_demote
                  if r["correct"] and r["truth"] in ("APPROVED", "DENIED")]
    mask_deniable = [r for r in would_demote
                     if r["truth"] == "DENIED" and r["pred"] == "DENIED"]
    gain = [r for r in would_demote if r["truth"] == "NEEDS_REVIEW" and not r["correct"]]
    print(f"\nblanket 'demote any-disagreement -> NEEDS_REVIEW' cost/benefit:")
    print(f"  cases it would change (pred != NR): {len(would_demote)}")
    print(f"  wrongly demote a case we get RIGHT (8->2 each): {len(lose_right)} "
          f"-> {[r['stem'] for r in lose_right][:5]}")
    print(f"  of those, true DENIED we correctly deny (masking deniable): {len(mask_deniable)} "
          f"-> {[r['stem'] for r in mask_deniable][:5]}")
    print(f"  correctly rescued to NR (was wrong): {len(gain)} "
          f"-> {[r['stem'] for r in gain][:5]}")
    print(f"  CFA created by demotion: 0 (demotion moves toward NR, cannot approve)")

    print(f"\nexample disagreement cases:")
    for r in any_dis[:6]:
        print(f"  {r['stem']} truth={r['truth']} pred={r['pred']} "
              f"fields={list(r['disagree'])} vals={ {k: v for k,v in r['disagree'].items()} }")

    # ============ 2. INJECTION PRESENCE ============
    print("\n" + "=" * 70)
    print("## 2. INJECTION PRESENCE")
    print("=" * 70)
    inj_hidden = [r for r in results if r["has_hidden"]]
    inj_key = [r for r in results if r["answerkey"]]
    print(f"\ncases with ANY hidden/off-crop text: {len(inj_hidden)}/{N} "
          f"({len(inj_hidden)/N:.2f})")
    print(f"cases with answer-key/directive hidden line: {len(inj_key)}/{N} "
          f"({len(inj_key)/N:.2f})")

    for label, grp in (("hidden-present", inj_hidden), ("answerkey-present", inj_key)):
        if not grp:
            print(f"\n[{label}] none")
            continue
        tb = Counter(r["truth"] for r in grp)
        p_nr = tb["NEEDS_REVIEW"] / len(grp)
        p_den = tb["DENIED"] / len(grp)
        p_app = tb["APPROVED"] / len(grp)
        err = sum(1 for r in grp if not r["correct"]) / len(grp)
        print(f"\n[{label}] n={len(grp)}  truth={dict(tb)}")
        print(f"  P(NR)={p_nr:.2f} (base {base_nr:.2f}, lift {p_nr-base_nr:+.2f})")
        print(f"  P(DENIED)={p_den:.2f} (base {base_denied:.2f}, lift {p_den-base_denied:+.2f})")
        print(f"  P(APPROVED)={p_app:.2f} (base {base_appr:.2f}, lift {p_app-base_appr:+.2f})")
        print(f"  P(we_wrong)={err:.2f} (base {base_err:.2f}, lift {err-base_err:+.2f})")

    print(f"\nexample injection cases:")
    for r in inj_key[:6]:
        print(f"  {r['stem']} truth={r['truth']} pred={r['pred']} "
              f"correct={r['correct']} sample={r['sample']!r}")

    # dump raw for programmatic re-read
    dump = ROOT / "scratchpad_crossdoc.json" if False else None
    raw = {"n": N, "base": {"nr": base_nr, "denied": base_denied,
                            "approved": base_appr, "err": base_err},
           "results": [{k: (sorted(v) if isinstance(v, set) else v)
                        for k, v in r.items()} for r in results]}
    out = Path("/private/tmp/claude-501/-Users-foo-repos-mib-doc-challenge-solution/"
               "e74b1240-70bf-444e-aa31-9c5904e691e2/scratchpad/crossdoc_raw.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(raw, indent=1, default=str))
    print(f"\n# raw dumped to {out}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 60)
