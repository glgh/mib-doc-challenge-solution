"""Cross-document conflict probe (2026-07-29).

Lead from visual inspection of MIB-000250: identity_conflict was recoverable not
from any stamp but from a real disagreement between two trusted documents — the
intake Form named a different applicant / visa class than the Sponsor letter.
This asks, corpus-wide: how often do trusted docs disagree on applicant_name or
visa_class, and does that disagreement predict a truth identity_conflict flag —
i.e. is it a real signal or just OCR noise / field-grain name poisoning (row-67)?

risk_flags is scored EXACT-SET all-or-nothing (weight 8), so a false-positive
identity_conflict emission CORRUPTS a field we currently get right. The bar is
therefore precision, not recall: this probe measures the false-positive rate
against truth before any emission rule is considered.

Reads the frozen page-text cache and rebuilds packets exactly as the pipeline
does, so per-document values are the real merged candidates.

Usage: .venv/bin/python experiments/xdoc_conflict_probe.py
"""
import csv
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from mib import cache, packet  # noqa: E402
from mib.parse import (DOC_ADJUDICATOR, DOC_BIOMETRIC, DOC_INTAKE, DOC_REGISTRY,  # noqa: E402
                       DOC_SPONSOR)

CH = ROOT.parent / "mib-doc-challenge"
truth = {r["case_id"]: r for r in csv.DictReader(open(CH / "data/train_labels.csv"))}
DOC_NAME = {DOC_INTAKE: "intake", DOC_REGISTRY: "registry", DOC_SPONSOR: "sponsor",
            DOC_BIOMETRIC: "biometric", DOC_ADJUDICATOR: "adjudicator"}


def norm_name(s):
    return " ".join((s or "").strip().lower().split())


def per_doc(pkt, fname):
    """{doc_label: value} for trusted docs that carry a non-empty `fname`."""
    out = {}
    for dtype, src, kv in pkt.docs:
        if dtype not in DOC_NAME:
            continue
        v = (kv.get(fname) or "").strip()
        if v:
            out.setdefault(DOC_NAME[dtype], v)
    return out


def main():
    _meta, records = cache.read(ROOT / "output/cache/train_grid.jsonl")
    name_conf = []   # cases with a cross-doc applicant_name disagreement
    visa_conf = []   # cases with a cross-doc visa_class disagreement
    for rec in records:
        if rec.get("error"):
            continue
        pages, ocr_lines = cache.to_case(rec["pages"])
        pkt = packet.assemble(pages, ocr_lines, fallback_case_id=rec["stem"])
        cid = pkt.case_id
        if cid not in truth:
            continue
        names = per_doc(pkt, "applicant_name")
        if len({norm_name(v) for v in names.values()}) > 1:
            name_conf.append((cid, names))
        visas = per_doc(pkt, "visa_class")
        if len({v.upper() for v in visas.values()}) > 1:
            visa_conf.append((cid, visas))

    def has_ic(cid):
        return "identity_conflict" in (truth[cid]["risk_flags"] or "")

    print(f"=== cross-doc APPLICANT_NAME disagreement: {len(name_conf)} cases ===")
    ic = [c for c, _ in name_conf if has_ic(c)]
    print(f"  of those, truth carries identity_conflict: {len(ic)}  "
          f"precision {len(ic)/max(1,len(name_conf)):.2f}")
    print(f"  truth adjudication of the mismatches: "
          f"{Counter(truth[c]['adjudication'] for c,_ in name_conf)}")
    print(f"  truth risk_flags of the mismatches: "
          f"{Counter(truth[c]['risk_flags'] for c,_ in name_conf).most_common(8)}")
    print("  sample (cid | truth_flags | per-doc names):")
    for c, names in name_conf[:20]:
        print(f"    {c}  [{truth[c]['risk_flags']}]  {names}")

    print(f"\n=== cross-doc VISA_CLASS disagreement: {len(visa_conf)} cases ===")
    ic2 = [c for c, _ in visa_conf if has_ic(c)]
    print(f"  of those, truth carries identity_conflict: {len(ic2)}  "
          f"precision {len(ic2)/max(1,len(visa_conf)):.2f}")
    print(f"  truth risk_flags of the mismatches: "
          f"{Counter(truth[c]['risk_flags'] for c,_ in visa_conf).most_common(8)}")
    for c, visas in visa_conf[:20]:
        print(f"    {c}  [{truth[c]['risk_flags']}]  {visas}")

    # Recall ceiling: how many true identity_conflict cases are even reachable
    all_ic = [c for c in truth if has_ic(c)]
    caught = {c for c, _ in name_conf} | {c for c, _ in visa_conf}
    print(f"\n=== recall ceiling ===")
    print(f"  truth identity_conflict cases: {len(all_ic)}")
    print(f"  caught by ANY cross-doc disagreement: "
          f"{len([c for c in all_ic if c in caught])}/{len(all_ic)}")


if __name__ == "__main__":
    main()
