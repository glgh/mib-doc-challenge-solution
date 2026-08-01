#!/usr/bin/env python3
"""Census the three ways to type a scan page, to size OTHER-page recovery.

`detect_doc_type` is title-only and reads only `best_read(reads)`, so a page
whose title a *losing* variant recovered, or whose body labels plainly name the
form with the title gone, types OTHER. This probe measures, over the full grid
cache, how much of that is recoverable and whether the body-signature idea is
safe — without a ground-truth doc-type label and without changing behavior.

Three typers per scan page:
  winner   detect_doc_type(best_read.lines)            — production today
  title    lowest non-OTHER title over NON-DECOY variants (Cause A)
  body     distinctive field-label signature (Cause B), title-independent

Validation without labels: the `title` type is high-precision (fuzzy >=0.66,
0 cross-type conflicts corpus-wide), so pages it types are used as PSEUDO-LABELS
to score the `body` signature — the confusion matrix exposes the intake<->sponsor
ambiguity before any code ships.

Usage: experiments/doc_type_audit.py [cache.jsonl]
"""
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mib import cache, packet, parse                              # noqa: E402
from mib.records import best_read                                 # noqa: E402

DEFAULT_CACHE = ROOT / "output/cache/train_grid.jsonl"
NAME = {1: "ADJUDICATOR", 2: "INTAKE", 3: "BIOMETRIC", 4: "SPONSOR",
        5: "REGISTRY", 6: "FEE", 9: "OTHER"}
KNOWN = [1, 2, 3, 4, 5, 6]

_SEP_RE = re.compile(r"^([A-Za-z0-9][A-Za-z _0-9]{1,28}?)\s*[:.;]\s*.+$")
_DECOY_FURNITURE = re.compile(r"synthetic hiring challenge|sample denial|specimen",
                              re.I)


def labels_present(lines):
    """Canonical field-labels whose LABEL text appears on the page, regardless
    of whether the value parsed — a garbled value still names the form. Uses the
    label head before the separator (safer than the whole line) and the same
    `parse.key_for` fuzzy label map the parser trusts."""
    out = set()
    for ln in lines:
        s = ln.strip()
        if not s:
            continue
        m = _SEP_RE.match(s)
        head = m.group(1) if m else s
        k = parse.key_for(head)
        if k:
            out.add(k)
    return out


def body_type(labels):
    """Type a page from its distinctive body field-labels — most form-unique
    first. Ordering IS the precedence; the audit's confusion matrix is what
    validates (or refutes) each arm before this ships."""
    if labels & {"biometric_confidence", "observed_flags"}:
        return parse.DOC_BIOMETRIC
    if labels & {"registry_status", "registry_name"}:
        return parse.DOC_REGISTRY
    if labels & {"waiver_code"}:
        return parse.DOC_FEE
    if labels & {"finding"}:
        return parse.DOC_ADJUDICATOR
    if labels & {"visa_class", "arrival_date"}:
        return parse.DOC_INTAKE
    if labels & {"fee_status"}:
        return parse.DOC_FEE
    if {"applicant_name", "sponsor_id"} <= labels or "declared_purpose" in labels:
        return parse.DOC_SPONSOR
    return parse.DOC_OTHER


def title_type_over_variants(reads, case_id):
    """Lowest (most-trusted) non-OTHER type any NON-DECOY variant's title gives,
    with the variant that carried it. Mirrors the injection guards the merge
    already applies to variant readings."""
    best, via = parse.DOC_OTHER, None
    for r in reads:
        if not r.lines or packet._decoy(r.lines, case_id, ocr=True):
            continue
        t = parse.detect_doc_type(r.lines)
        if t != parse.DOC_OTHER and t < best:
            best, via = t, r.variant
    return best, via


def union_labels(reads, case_id):
    out = set()
    for r in reads:
        if not r.lines or packet._decoy(r.lines, case_id, ocr=True):
            continue
        out |= labels_present(r.lines)
    return out


def main(cache_path=DEFAULT_CACHE):
    meta, recs = cache.read(cache_path)
    n_scan = 0
    winner_ct = Counter()
    recovered_title = Counter()      # winner OTHER -> title recovers, by type
    recovered_body = Counter()       # winner OTHER & no title -> body recovers, by type
    still_other = 0
    confusion = defaultdict(Counter)  # title (pseudo-label) -> body prediction
    body_on_decoy = []                # decoy-ish pages the body signature would type

    for r in recs:
        if r.get("error"):
            continue
        cid = r["stem"]
        pages, reads_by_page = cache.to_case(r["pages"])
        for p in pages:
            reads = reads_by_page.get(p.page_no)
            if not reads:
                continue  # scan-only pages carry an ensemble; text pages don't
            n_scan += 1
            w = best_read(reads)
            wt = parse.detect_doc_type(w.lines if w else [])
            winner_ct[wt] += 1
            tt, _via = title_type_over_variants(reads, cid)
            labels = union_labels(reads, cid)
            bt = body_type(labels)

            # Body-signature accuracy, scored against the high-precision title.
            if tt != parse.DOC_OTHER:
                confusion[tt][bt] += 1

            # Decoy safety: does the body signature fire on furniture-only pages?
            joined = " ".join((w.lines if w else []))
            if _DECOY_FURNITURE.search(joined) and tt == parse.DOC_OTHER \
                    and bt != parse.DOC_OTHER:
                body_on_decoy.append((cid, p.page_no, NAME[bt]))

            # Recovery accounting on pages the winner leaves OTHER.
            if wt == parse.DOC_OTHER:
                if tt != parse.DOC_OTHER:
                    recovered_title[tt] += 1
                elif bt != parse.DOC_OTHER:
                    recovered_body[bt] += 1
                else:
                    still_other += 1

    winner_other = winner_ct[parse.DOC_OTHER]
    print(f"scan pages: {n_scan}   winner-typed OTHER: {winner_other}\n")

    print("== Cause A — title recovered by a non-decoy variant ==")
    tot = sum(recovered_title.values())
    for dt in KNOWN:
        if recovered_title[dt]:
            print(f"   {NAME[dt]:11} {recovered_title[dt]}")
    print(f"   TOTAL recovered by title: {tot}\n")

    print("== Cause B — no title on any variant, body signature types it ==")
    totb = sum(recovered_body.values())
    for dt in KNOWN:
        if recovered_body[dt]:
            print(f"   {NAME[dt]:11} {recovered_body[dt]}")
    print(f"   TOTAL recovered by body signature: {totb}")
    print(f"   still OTHER after both: {still_other}")
    print(f"   => OTHER recovery: {tot + totb}/{winner_other} "
          f"({100*(tot+totb)/max(1,winner_other):.0f}%)\n")

    print("== Body-signature accuracy vs title pseudo-labels (rows=title, cols=body) ==")
    hdr = "  ".join(f"{NAME[c][:5]:>5}" for c in KNOWN + [9])
    print(f"   {'title/body':>12}  {hdr}")
    correct = total = 0
    for tt in KNOWN:
        row = confusion[tt]
        cells = "  ".join(f"{row[c]:>5}" for c in KNOWN + [9])
        n = sum(row.values())
        correct += row[tt]
        total += n
        acc = f"{100*row[tt]/n:.0f}%" if n else "-"
        print(f"   {NAME[tt]:>12}  {cells}   n={n} acc={acc}")
    print(f"   overall body-vs-title agreement: {correct}/{total} "
          f"({100*correct/max(1,total):.0f}%)\n")

    print("== Decoy safety ==")
    if body_on_decoy:
        print(f"   WARNING: body signature fires on {len(body_on_decoy)} furniture page(s):")
        for c in body_on_decoy[:12]:
            print("     ", c)
    else:
        print("   body signature fires on 0 decoy-furniture pages")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CACHE)
