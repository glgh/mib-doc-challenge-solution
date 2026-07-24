#!/usr/bin/env python3
"""Offline OCR bench: measure key/value recovery per OCR variant on a frozen
sample of dev scan pages, without running the full pipeline.

Sample: dev cases with scan-only pages, stratified over truth adjudication
(seed 8090, committed to data_splits.json-adjacent scratch is NOT needed —
the sample is derived deterministically here).

Ground truth per page is approximated from train_labels.csv: a variant "recovers"
a field on a case if any of its OCR text, after loose normalization, contains the
truth value (or a recognizable key line for flags). This is a *relative* yardstick
for comparing variants cheaply — absolute extraction quality still comes from
eval_local.sh.

Usage: scripts/ocr_bench.py [--variants raw11,raw6,bin11,bin6,render11,...] [--n 50]
"""
import argparse
import csv
import io
import json
import os
import random
import subprocess
import sys
import tempfile
import time
from multiprocessing import Pool
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
CH = ROOT.parent / "mib-doc-challenge"
sys.path.insert(0, str(ROOT))

from mib.stages import extract  # noqa: E402
from mib.textmatch import normalize as _tnorm, present as _present  # noqa: E402

CHECK_FIELDS = ["species_code", "home_world", "visa_class", "sponsor_id",
                "arrival_date", "declared_purpose", "fee_status", "applicant_name"]


def tess(img, psm):
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        img.save(f.name)
        try:
            p = subprocess.run(["tesseract", f.name, "stdout", "--psm", str(psm)],
                               capture_output=True, text=True, timeout=30)
            return p.stdout
        except subprocess.TimeoutExpired:
            return ""
        finally:
            os.unlink(f.name)


def binarize(img, threshold=190):
    return img.convert("L").point(lambda x: 0 if x < threshold else 255)


def page_images(doc, page):
    """(embedded_raster_or_None, rendered_200dpi)"""
    embedded = None
    for xref, *_ in page.get_images(full=True):
        try:
            raw = Image.open(io.BytesIO(doc.extract_image(xref)["image"]))
        except Exception:
            continue
        if raw.width >= 1000:
            embedded = raw
            break
    pix = page.get_pixmap(dpi=200)
    rendered = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    return embedded, rendered


# variant name -> function(embedded, rendered) -> list of OCR texts
VARIANTS = {
    "emb_raw_psm11": lambda e, r: [tess(e, 11)] if e else [],
    "emb_raw_psm6": lambda e, r: [tess(e, 6)] if e else [],
    "emb_bin_psm11": lambda e, r: [tess(binarize(e), 11)] if e else [],
    "emb_bin_psm6": lambda e, r: [tess(binarize(e), 6)] if e else [],
    "ren_raw_psm11": lambda e, r: [tess(r, 11)],
    "ren_raw_psm6": lambda e, r: [tess(r, 6)],
    "ren_bin_psm11": lambda e, r: [tess(binarize(r), 11)],
    "ren_raw_psm3": lambda e, r: [tess(r, 3)],
}


def recovered(field, truth_value, texts):
    """Token-boundary match on the scorer's own normalization, so a short value no
    longer matches spuriously inside a longer one (`paid` ⊄ `unpaid`)."""
    return bool(_present(truth_value, _tnorm(" ".join(texts))))


def bench_case(args):
    cid, variants = args
    truth = TRUTH[cid]
    out = {}
    with extract.open_document(CH / f"data/train/{cid}.pdf") as doc:
        # Bench the exact pages production OCRs — `Page.is_scan_only`, not a
        # separate 400-char heuristic that scores a different page set.
        scan_nos = [p.page_no for p in extract.pages(doc) if p.is_scan_only]
        for vname in variants:
            fn = VARIANTS[vname]
            texts = []
            t0 = time.time()
            for no in scan_nos:
                emb, ren = page_images(doc, doc[no])
                texts.extend(fn(emb, ren))
            hits = {f: recovered(f, truth[f], texts) for f in CHECK_FIELDS}
            # flags: count truth components found
            tflags = [] if truth["risk_flags"] == "none" else truth["risk_flags"].split("|")
            hits["risk_flag_components"] = sum(recovered("rf", fl, texts) for fl in tflags)
            hits["_n_truth_flags"] = len(tflags)
            out[vname] = (hits, time.time() - t0)
    return cid, out


TRUTH = {r["case_id"]: r for r in csv.DictReader(open(CH / "data/train_labels.csv"))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants", default=",".join(VARIANTS))
    ap.add_argument("--n", type=int, default=50)
    args = ap.parse_args()
    variants = [v for v in args.variants.split(",") if v in VARIANTS]

    dev = sorted(json.loads((ROOT / "data_splits.json").read_text())["dev"])
    debug = {json.loads(l)["case_id"]: json.loads(l)
             for l in open(ROOT / "output/eval/debug.jsonl")}
    scan_cases = [c for c in dev if debug.get(c, {}).get("scan_only_pages", 0) > 0]
    rng = random.Random(8090)
    by_adj = {}
    for c in scan_cases:
        by_adj.setdefault(TRUTH[c]["adjudication"], []).append(c)
    sample = []
    for adj, cs in sorted(by_adj.items()):
        rng.shuffle(cs)
        sample.extend(cs[: max(1, args.n * len(cs) // len(scan_cases))])
    sample = sorted(sample)[: args.n]
    print(f"bench sample: {len(sample)} scan cases; variants: {variants}", flush=True)

    t0 = time.time()
    with Pool(4) as pool:
        results = pool.map(bench_case, [(c, variants) for c in sample])
    wall = time.time() - t0

    print(f"\n{'variant':16s} " + " ".join(f"{f[:7]:>7s}" for f in CHECK_FIELDS)
          + "   flags   sec/case")
    total_flags = sum(r[1][variants[0]][0]["_n_truth_flags"] for r in results)
    for v in variants:
        cols = []
        for f in CHECK_FIELDS:
            n = sum(1 for _, out in results if out[v][0][f])
            cols.append(f"{n:>7d}")
        fl = sum(out[v][0]["risk_flag_components"] for _, out in results)
        sec = sum(out[v][1] for _, out in results) / len(results)
        print(f"{v:16s} " + " ".join(cols) + f"   {fl}/{total_flags}   {sec:6.2f}")
    print(f"\nwall {wall:.0f}s for {len(sample)} cases x {len(variants)} variants")


if __name__ == "__main__":
    main()
