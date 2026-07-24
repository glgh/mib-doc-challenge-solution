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

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
CH = ROOT.parent / "mib-doc-challenge"
sys.path.insert(0, str(ROOT))

from mib import imaging  # noqa: E402
from mib.stages import extract  # noqa: E402
from mib.textmatch import normalize as _tnorm, present as _present  # noqa: E402

CHECK_FIELDS = ["species_code", "home_world", "visa_class", "sponsor_id",
                "arrival_date", "declared_purpose", "fee_status", "applicant_name",
                "case_id"]


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


def tess_tsv(img, psm):
    """Per-word TSV: return [(conf: float, text: str)] for non-empty words.
    conf column is index 10, text index 11 (tesseract 5 layout)."""
    if img is None:
        return []
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        img.save(f.name)
        try:
            p = subprocess.run(["tesseract", f.name, "stdout", "--psm", str(psm), "tsv"],
                               capture_output=True, text=True, timeout=30)
            out = []
            for line in p.stdout.splitlines()[1:]:
                cols = line.split("\t")
                if len(cols) >= 12 and cols[11].strip():
                    try:
                        out.append((float(cols[10]), cols[11]))
                    except ValueError:
                        pass
            return out
        except subprocess.TimeoutExpired:
            return []
        finally:
            os.unlink(f.name)


def tess_conf_filter(img, psm, min_conf):
    """Whole-page text with per-word tokens below min_conf dropped."""
    return " ".join(t for c, t in tess_tsv(img, psm) if c >= min_conf)


def tess_wl(img, psm, whitelist):
    """OCR with tessedit_char_whitelist restricting the character set."""
    if img is None:
        return ""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        img.save(f.name)
        try:
            p = subprocess.run(
                ["tesseract", f.name, "stdout", "--psm", str(psm),
                 "-c", f"tessedit_char_whitelist={whitelist}"],
                capture_output=True, text=True, timeout=30)
            return p.stdout
        except subprocess.TimeoutExpired:
            return ""
        finally:
            os.unlink(f.name)


# --- top-left crop candidates (see plan Phase 1) -----------------------------
# The legible field block concentrates top-left; cropping to it drops the
# shredded lower page / passport / stamps that derail PSM 11's whole-page
# segmentation. These feed the sweep; the winner graduates to render.reads_for.

def _crop_tl(img, hf, wf):
    """Blind top-left fraction: (0,0) .. (wf*W, hf*H)."""
    if img is None:
        return None
    return img.crop((0, 0, max(1, int(img.width * wf)), max(1, int(img.height * hf))))


def _deshred_pil(img):
    """render.reads_for's deshred, as a PIL op, for the 'which base to crop' arm."""
    if img is None:
        return None
    gray = np.asarray(img.convert("L"))
    out = imaging.realign_bands(gray)
    return Image.fromarray(out if out is not None else gray)


def _content_crop(img, hcap=0.60, wcap=0.70, margin=15):
    """Content-driven bbox: tighten to the actual ink inside a top-left window,
    so the crop adapts to each form's field-block extent instead of a blind
    fraction. Reuses imaging.ink_mask (adaptive on faint scans)."""
    if img is None:
        return None
    gray = np.asarray(img.convert("L"))
    H, W = gray.shape
    h0, w0 = max(1, int(H * hcap)), max(1, int(W * wcap))
    win = imaging.ink_mask(gray[:h0, :w0])
    ys, xs = np.nonzero(win)
    if len(ys) < 50:
        return img.crop((0, 0, w0, h0))
    x0, x1, y0, y1 = int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())
    return img.crop((max(0, x0 - margin), max(0, y0 - margin),
                     min(W, x1 + margin), min(H, y1 + margin)))


def _reg_crop_variants():
    """Register the crop sweep grid into VARIANTS. Named cropR_/cropD_ (render vs
    deshred base), HHxWW fraction, pN psm; content-driven as cropRbbox_pN."""
    def ren(hf, wf, psm):
        return lambda e, r: [tess(_crop_tl(r, hf, wf), psm)]

    def desh(hf, wf, psm):
        return lambda e, r: [tess(_crop_tl(_deshred_pil(r), hf, wf), psm)]

    for h in (20, 25, 30, 35, 40, 45, 55, 65):
        for w in (55, 60, 65, 70):
            for psm in (11, 6):
                VARIANTS[f"cropR_{h}x{w}_p{psm}"] = ren(h / 100, w / 100, psm)
    for h in (25, 30, 35, 40):
        for w in (60, 65):
            for psm in (11, 6):
                VARIANTS[f"cropD_{h}x{w}_p{psm}"] = desh(h / 100, w / 100, psm)
    for psm in (11, 6):
        VARIANTS[f"cropRbbox_p{psm}"] = (lambda p: lambda e, r: [tess(_content_crop(r), p)])(psm)
    # Faithful baseline: production's non-crop variant union (source + turns + skew +
    # deshred, PSM 11) so additive gain isn't credited for fields turns/deshred already
    # recover. Slow (one variant, many OCRs) — use only as --baseline on finalists.
    VARIANTS["prod_union_p11"] = lambda e, r: _prod_union(e, r, 11)


def _prod_union(e, r, psm=11):
    texts = []
    for img in (e, r):
        if img is None:
            continue
        gray = np.asarray(img.convert("L"))
        texts.append(tess(Image.fromarray(gray), psm))
        for q in (1, 3):
            texts.append(tess(Image.fromarray(imaging.turn(gray, q)), psm))
        ang = imaging.skew_angle(gray)
        if abs(ang) >= imaging.MIN_SKEW:
            texts.append(tess(Image.fromarray(imaging.rotate(gray, ang)), psm))
        out = imaging.realign_bands(gray)
        if out is not None:
            texts.append(tess(Image.fromarray(out), psm))
    return texts


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
    # --- A1.1 per-word-confidence-filtered whole-page text (recovery lever) ---
    "ren_tsv_c40_p11": lambda e, r: [tess_conf_filter(r, 11, 40)],
    "ren_tsv_c60_p11": lambda e, r: [tess_conf_filter(r, 11, 60)],
    # --- A1.2 char-whitelist structured passes (sponsor_id / case_id / date) ---
    "wl_spn_p7": lambda e, r: [tess_wl(r, 7, "SPN-0123456789")],
    "wl_spn_p11": lambda e, r: [tess_wl(r, 11, "SPN-0123456789")],
    "wl_mib_p7": lambda e, r: [tess_wl(r, 7, "MIB-0123456789")],
    "wl_mib_p11": lambda e, r: [tess_wl(r, 11, "MIB-0123456789")],
    "wl_date_p7": lambda e, r: [tess_wl(r, 7, "0123456789-/")],
    "wl_date_p11": lambda e, r: [tess_wl(r, 11, "0123456789-/")],
    # --- A1.3 binarization threshold sweep at psm 11 ---
    "ren_bin150_p11": lambda e, r: [tess(binarize(r, 150), 11)],
    "ren_bin170_p11": lambda e, r: [tess(binarize(r, 170), 11)],
    "ren_bin190_p11": lambda e, r: [tess(binarize(r, 190), 11)],
    "ren_bin210_p11": lambda e, r: [tess(binarize(r, 210), 11)],
}
_reg_crop_variants()


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

# --- A2: does tesseract per-word confidence separate correct-value tokens? -----
import re as _re

_STRUCT_FIELDS = ["sponsor_id", "arrival_date", "case_id"]


def _subtoks(value, minlen=3):
    """Alphanumeric sub-tokens (casefolded, len>=minlen) of a normalized value."""
    return {t for t in _re.split(r"[^a-z0-9]+", _tnorm(value)) if len(t) >= minlen}


def _conf_sep_case(cid):
    """For one case: pool per-word (conf, is_on_truth) over its scan pages, plus
    per-structured-field (matched-token conf vs page-mean conf) records."""
    truth = TRUTH[cid]
    pooled = []              # (conf, on_truth_bool)
    field_recs = []          # (field, matched_conf_mean, page_conf_mean)
    with extract.open_document(CH / f"data/train/{cid}.pdf") as doc:
        scan_nos = [p.page_no for p in extract.pages(doc) if p.is_scan_only]
        for no in scan_nos:
            _, ren = page_images(doc, doc[no])
            words = tess_tsv(ren, 11)
            if not words:
                continue
            page_text = _tnorm(" ".join(t for _, t in words))
            page_confs = [c for c, _ in words]
            page_mean = sum(page_confs) / len(page_confs)
            # subtokens that belong to a truth value actually present on this page
            on_truth = set()
            for f in CHECK_FIELDS:
                if _present(truth[f], page_text):
                    on_truth |= _subtoks(truth[f])
            for c, t in words:
                is_on = bool(_subtoks(t) & on_truth)
                pooled.append((c, is_on))
            # per structured field: conf of the token(s) carrying its value
            for f in _STRUCT_FIELDS:
                v = _tnorm(truth[f])
                if v in ("", "none", "unknown") or not _present(truth[f], page_text):
                    continue
                vtoks = _subtoks(truth[f])
                mc = [c for c, t in words if _subtoks(t) & vtoks]
                if mc:
                    field_recs.append((f, sum(mc) / len(mc), page_mean))
    return pooled, field_recs


def run_conf_sep(sample):
    with Pool(4) as pool:
        results = pool.map(_conf_sep_case, sample)
    pooled, field_recs = [], []
    for p, fr in results:
        pooled.extend(p)
        field_recs.extend(fr)
    on = [c for c, o in pooled if o]
    off = [c for c, o in pooled if not o]
    allc = [c for c, _ in pooled]

    def stats(xs):
        if not xs:
            return "n=0"
        xs = sorted(xs)
        mean = sum(xs) / len(xs)
        med = xs[len(xs) // 2]
        return f"n={len(xs):5d}  mean={mean:5.1f}  median={med:5.1f}"

    print("\n=== A2: per-word confidence separation (psm 11 tsv on rendered scan pages) ===")
    print(f"on-truth tokens (part of a correct truth value):  {stats(on)}")
    print(f"off-truth tokens (everything else):               {stats(off)}")
    print(f"ALL tokens on page (overall):                     {stats(allc)}")
    if on and allc:
        d = sum(on) / len(on) - sum(allc) / len(allc)
        d2 = sum(on) / len(on) - (sum(off) / len(off) if off else 0)
        print(f"separation: on-truth mean − page mean = {d:+.1f};  on-truth − off-truth = {d2:+.1f}")
    print("\nper structured field (matched-token conf vs page-mean conf, per case where value is on-page):")
    print(f"{'field':14s} {'n_cases':>7s} {'matched_conf':>13s} {'page_conf':>10s} {'delta':>7s}")
    for f in _STRUCT_FIELDS:
        rs = [(m, p) for ff, m, p in field_recs if ff == f]
        if not rs:
            print(f"{f:14s} {0:>7d}")
            continue
        mm = sum(m for m, _ in rs) / len(rs)
        pp = sum(p for _, p in rs) / len(rs)
        print(f"{f:14s} {len(rs):>7d} {mm:>13.1f} {pp:>10.1f} {mm - pp:>+7.1f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants", default=",".join(VARIANTS))
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--baseline", default=None,
                    help="report each variant's ADDITIVE field recovery over this "
                         "variant (what best()/voting can bank); e.g. ren_raw_psm11")
    ap.add_argument("--conf-sep", action="store_true",
                    help="A2: run the per-word-confidence separation analysis instead "
                         "of the variant table")
    ap.add_argument("--examples", type=int, default=0,
                    help="after the additive table, show up to N concrete "
                         "baseline-miss / variant-hit examples (re-OCRs those cases)")
    args = ap.parse_args()
    variants = [v for v in args.variants.split(",") if v in VARIANTS]
    if args.baseline and args.baseline not in variants:
        variants = [args.baseline] + variants

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

    if args.conf_sep:
        print(f"conf-sep sample: {len(sample)} scan cases", flush=True)
        run_conf_sep(sample)
        return

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

    # Additive recovery over a full-page baseline = what add-semantics (best/vote)
    # can actually bank; the per-variant table above is each variant in isolation.
    base = args.baseline
    additive_hits = []  # (variant, case_id, field)
    if base and base in variants:
        print(f"\nADDITIVE field recovery over baseline '{base}' "
              f"(cases where variant recovers a field the baseline misses):")
        print(f"{'variant':16s} " + " ".join(f"{f[:7]:>7s}" for f in CHECK_FIELDS)
              + "   flags    TOTAL")
        for v in variants:
            if v == base:
                continue
            cols, tot = [], 0
            for f in CHECK_FIELDS:
                g = 0
                for cid, o in results:
                    if o[v][0][f] and not o[base][0][f]:
                        g += 1
                        additive_hits.append((v, cid, f))
                tot += g
                cols.append(f"{g:>7d}")
            flg = sum(max(0, o[v][0]["risk_flag_components"] - o[base][0]["risk_flag_components"])
                      for _, o in results)
            print(f"{v:16s} " + " ".join(cols) + f"   {flg:>5d}   {tot:>6d}")

    if args.examples and additive_hits:
        show_examples(additive_hits[: args.examples], base)


def show_examples(hits, base):
    """Re-OCR each (variant, case, field) additive hit and print truth vs
    baseline text vs variant text so the recovery is concretely visible."""
    print(f"\n=== concrete examples (baseline '{base}' misses, variant recovers) ===")
    for v, cid, f in hits:
        truth = TRUTH[cid]
        with extract.open_document(CH / f"data/train/{cid}.pdf") as doc:
            scan_nos = [p.page_no for p in extract.pages(doc) if p.is_scan_only]
            btxt, vtxt = [], []
            for no in scan_nos:
                emb, ren = page_images(doc, doc[no])
                btxt.extend(VARIANTS[base](emb, ren))
                vtxt.extend(VARIANTS[v](emb, ren))
        bn = _tnorm(" ".join(btxt))
        vn = _tnorm(" ".join(vtxt))
        print(f"\n{cid}  field={f}  truth={truth[f]!r}  variant={v}")
        print(f"  baseline text : ...{bn[:220]}...")
        print(f"  variant  text : ...{vn[:220]}...")


if __name__ == "__main__":
    main()
