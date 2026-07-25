#!/usr/bin/env python3
"""Parse-realized crop probe (and voting ceiling) on the dev scan sample.

The ocr_bench union metric is ambiguous: it overstates vs a single pass and
understates the crop's real mechanism (a clean crop reading winning best() and
getting *parsed*). This drives the REAL downstream (predict_from_evidence) A/B:
best() over production reads, with vs without crop reads — so we see the actual
field recovery + adjudication effect, not a presence proxy.

  A = current   : best_lines(prod_reads)
  B = +crop      : best_lines(prod_reads + crop_reads)
  V = vote       : per-field weighted vote across ALL scan readings (prod+crop)
                   — approximate (ignores text-layer precedence), a ceiling signal.

Reports per-field correct-recovery counts A/B/V vs truth, and adjudication/CFA.
"""
import csv
import json
import random
import sys
import tempfile
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CH = ROOT.parent / "mib-doc-challenge"
sys.path.insert(0, str(ROOT))

from mib import imaging, runner                      # noqa: E402
from mib.stages import extract, render               # noqa: E402
from mib.packet import _repair_ocr_kv                # noqa: E402
from mib.records import Read                         # noqa: E402
from mib import parse                                # noqa: E402
from mib.textmatch import normalize as _tnorm, present as _present  # noqa: E402

TRUTH = {r["case_id"]: r for r in csv.DictReader(open(CH / "data/train_labels.csv"))}
CHECK = ["species_code", "home_world", "visa_class", "sponsor_id",
         "arrival_date", "declared_purpose", "fee_status", "applicant_name"]
HF, WF = 0.33, 0.60


def _crop_reads(doc, page_no):
    """The crop variant Phase 3 would add: top-left crop of each source, and of the
    deshredded source, OCR'd at PSM 11."""
    reads = []
    page = doc[page_no]
    with tempfile.TemporaryDirectory() as tmp:
        for name, _enc, gray in render._sources(doc, page, tmp):
            H, W = gray.shape
            desh = imaging.realign_bands(gray)
            variants = [("crop", gray[:int(H * HF), :int(W * WF)])]
            if desh is not None:
                variants.append(("cropdesh", desh[:int(H * HF), :int(W * WF)]))
            for label, img in variants:
                p = Path(tmp) / "c.png"
                p.write_bytes(imaging.to_png_bytes(img))
                lines = render._tesseract(p, 11)
                reads.append(Read(page_no=page_no, lines=lines, variant=f"{name}+{label}",
                                  quality=render.evidence_score(lines), cost_ms=0))
    return reads


def _fields_from_reading(lines):
    kv = parse.parse_kv(lines)
    for f, v in parse.parse_prose(lines).items():
        kv.setdefault(f, v)
    return _repair_ocr_kv(kv)


def _correct(field, truth_value, predicted):
    return bool(predicted) and bool(_present(truth_value, _tnorm(predicted)))


def probe_case(cid):
    truth = TRUTH[cid]
    with extract.open_document(CH / f"data/train/{cid}.pdf") as doc:
        pages = extract.pages(doc)
        prod, crop = {}, {}
        for p in pages:
            if p.is_scan_only:
                prod[p.page_no] = render.reads_for(doc, doc[p.page_no], p.page_no)
                crop[p.page_no] = _crop_reads(doc, p.page_no)
        readsB = {no: prod[no] + crop[no] for no in prod}
        recA, _ = runner.predict_from_evidence(pages, prod, cid)
        recB, _ = runner.predict_from_evidence(pages, readsB, cid)
        prod_kv = [_fields_from_reading(r.lines) for no in prod for r in prod[no]]
        crop_kv = [_fields_from_reading(r.lines) for no in prod for r in crop[no]]
    res = {"cid": cid, "truth_adj": truth["adjudication"],
           "adjA": recA["adjudication"], "adjB": recB["adjudication"]}
    for f in CHECK:
        a = _correct(f, truth[f], recA.get(f, ""))
        b = _correct(f, truth[f], recB.get(f, ""))
        # per-field merge ceilings: does ANY reading parse the correct value?
        prod_ceil = any(_correct(f, truth[f], kv.get(f, "")) for kv in prod_kv)
        all_ceil = prod_ceil or any(_correct(f, truth[f], kv.get(f, "")) for kv in crop_kv)
        res[f] = (a, b, prod_ceil, all_ceil)
    return res


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 120
    dev = sorted(json.loads((ROOT / "data_splits.json").read_text())["dev"])
    debug = {json.loads(l)["case_id"]: json.loads(l)
             for l in open(ROOT / "output/eval/debug.jsonl")}
    scan = [c for c in dev if debug.get(c, {}).get("scan_only_pages", 0) > 0]
    rng = random.Random(8090)
    by_adj = {}
    for c in scan:
        by_adj.setdefault(TRUTH[c]["adjudication"], []).append(c)
    sample = []
    for adj, cs in sorted(by_adj.items()):
        rng.shuffle(cs)
        sample.extend(cs[: max(1, n * len(cs) // len(scan))])
    sample = sorted(sample)[:n]
    print(f"probe sample: {len(sample)} dev scan cases", flush=True)

    with Pool(4) as pool:
        results = pool.map(probe_case, sample)

    # A = current best(); B = best()+crop (both realized through the real pipeline).
    # prodCeil = per-field merge ceiling over production reads; allCeil = +crop.
    # (ceil - A) = headroom a per-field vote/merge could bank; (allCeil - prodCeil)
    # = the crop's unique contribution to that ceiling.
    print(f"\n{'field':16s}   A(cur) B(+crop) B-A |  prodCeil  allCeil  ceil-A  crop+ceil")
    for f in CHECK:
        a = sum(r[f][0] for r in results)
        b = sum(r[f][1] for r in results)
        pc = sum(r[f][2] for r in results)
        ac = sum(r[f][3] for r in results)
        print(f"{f:16s}   {a:5d}  {b:5d}  {b-a:+3d} |  {pc:6d}   {ac:6d}   {ac-a:+4d}    {ac-pc:+4d}")
    # adjudication + CFA (truth DENIED but predicted APPROVED)
    def cfa(key):
        return sum(1 for r in results if r["truth_adj"] == "DENIED" and r[key] == "APPROVED")
    flips = [(r["cid"], r["adjA"], r["adjB"]) for r in results if r["adjA"] != r["adjB"]]
    print(f"\nadjudication: CFA A={cfa('adjA')}  B={cfa('adjB')}   "
          f"cases where crop changed adjudication: {len(flips)}")
    for cid, a, b in flips[:20]:
        print(f"    {cid}: {a} -> {b}  (truth {TRUTH[cid]['adjudication']})")


if __name__ == "__main__":
    main()
