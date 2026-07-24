#!/usr/bin/env python3
"""Diagnose fee_status: real confusion matrix (truth vs predicted, exact match so
'unknown' is credited), and for each miss whether a fee-receipt doc was even found
and whether 'fee' appears anywhere in the trusted text. Distinguishes 'no receipt
in packet' (genuinely unknown) from 'receipt present but not read/parsed'."""
import csv
import json
import random
import sys
from collections import Counter
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CH = ROOT.parent / "mib-doc-challenge"
sys.path.insert(0, str(ROOT))

from mib import parse, runner                        # noqa: E402
from mib.stages import extract                       # noqa: E402

TRUTH = {r["case_id"]: r for r in csv.DictReader(open(CH / "data/train_labels.csv"))}


def probe(cid):
    pdf = CH / f"data/train/{cid}.pdf"
    rec, dbg = runner.predict(pdf)
    truth = TRUTH[cid]["fee_status"]
    pred = rec["fee_status"]
    doc_types = dbg.get("doc_types", [])
    has_fee_doc = parse.DOC_FEE in doc_types
    # does the word 'fee' appear in any page's visible or (best) OCR text?
    fee_word = False
    with extract.open_document(pdf) as doc:
        for p in extract.pages(doc):
            if any("fee" in l.lower() for l in p.visible_lines):
                fee_word = True
                break
    return {"cid": cid, "truth": truth, "pred": pred, "has_fee_doc": has_fee_doc,
            "fee_word_visible": fee_word, "scan_pages": dbg.get("scan_only_pages", 0),
            "fee_provenance": dbg.get("provenance", {}).get("fee_status", [])}


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
    print(f"fee probe: {len(sample)} dev scan cases", flush=True)

    with Pool(4) as pool:
        res = pool.map(probe, sample)

    correct = sum(1 for r in res if r["truth"] == r["pred"])
    print(f"\nfee_status accuracy (exact, credits 'unknown'): {correct}/{len(res)}")
    print("\nconfusion (truth -> pred):")
    conf = Counter((r["truth"], r["pred"]) for r in res)
    for (t, p), n in sorted(conf.items(), key=lambda x: -x[1]):
        tag = "OK" if t == p else "MISS"
        print(f"  {t:8s} -> {p:8s}  {n:3d}  {tag}")

    miss = [r for r in res if r["truth"] != r["pred"]]
    print(f"\n{len(miss)} misses — receipt/context breakdown:")
    print(f"  truth!=unknown, no fee doc found      : "
          f"{sum(1 for r in miss if r['truth']!='unknown' and not r['has_fee_doc'])}")
    print(f"  truth!=unknown, fee doc found (read fail): "
          f"{sum(1 for r in miss if r['truth']!='unknown' and r['has_fee_doc'])}")
    print(f"  'fee' visible in text on a miss        : "
          f"{sum(1 for r in miss if r['fee_word_visible'])}")
    print("\nsample misses:")
    for r in miss[:25]:
        print(f"  {r['cid']}: truth={r['truth']:8s} pred={r['pred']:8s} "
              f"fee_doc={r['has_fee_doc']} fee_word={r['fee_word_visible']} "
              f"prov={r['fee_provenance']}")


if __name__ == "__main__":
    main()
