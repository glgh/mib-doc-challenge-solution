#!/usr/bin/env python3
"""Mine the hardest-but-recoverable scan pages corpus-wide, and render them.

The old bag (`test_pages.txt`) was seeded on `base<=1` from the first ~250 packets,
un-ranked — so a turned page repair nails (0->13) sits next to a faint dead page no
geometry moves (0->0), weighted equally. This instead sweeps ALL train packets, scores
each scan page on how hard it reads raw AND how much a repair could recover, routes the
dead/faint floor out to its own list, stratifies the pick across damage classes, and
force-includes the known regression hazards.

Two phases:
  1. census  — every scan-only page, OCR'd raw + through each ungated ladder rung
               (skew / turn1 / turn3 / deshred). Cached to output/viz/hard_census.jsonl.
  2. select  — dead-floor -> hard_faint.txt; the rest ranked (low base_ev, high
               headroom), stratified by class, -> hard_pages.txt; then rendered through
               the repair_bench strip -> hard_100.html for the user to judge by eye.

Usage:
  experiments/mine_hard.py [--n-select 100] [--procs 8] [--hard-max 6] [--no-census]
"""
import argparse
import json
import multiprocessing
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))          # so `import pipeline`/`repair_bench` resolve

import pipeline                        # noqa: E402
import repair_bench as rb              # noqa: E402
from mib import imaging                # noqa: E402
from mib.stages import extract, render  # noqa: E402

CH = ROOT.parent / "mib-doc-challenge"
CENSUS_PATH = ROOT / "output/viz/hard_census.jsonl"
HARD_PAGES = HERE / "hard_pages.txt"
HARD_FAINT = HERE / "hard_faint.txt"
HTML_OUT = ROOT / "output/viz/hard_100.html"

# Known regression / hazard pages — always in the bench as guards (findings.md).
HAZARDS = [("MIB-000037", 0), ("MIB-000165", 1), ("MIB-000237", 1)]
CLASS_ORDER = ["turned", "shred", "skew+shred", "skew", "unclassified"]


def _ocr(gray):
    with tempfile.TemporaryDirectory(prefix="mine") as tmp:
        p = Path(tmp) / "x.png"
        p.write_bytes(imaging.to_png_bytes(gray))
        return render.evidence_score(render._tesseract(p))


def census_page(stem):
    """One row per scan-only page: raw + every ungated rung, plus damage signals."""
    rows = []
    try:
        with extract.open_document(CH / f"data/train/{stem}.pdf") as doc:
            for pg in extract.pages(doc):
                if not pg.is_scan_only:
                    continue
                with tempfile.TemporaryDirectory(prefix="mine") as tmp:
                    src = next(render._sources(doc, doc[pg.page_no], tmp), None)
                    if src is None:
                        continue
                    gray = src[2]
                    base = _ocr(gray)
                    angle = imaging.skew_angle(gray)
                    if abs(angle) >= imaging.MIN_SKEW:
                        upright = imaging.rotate(gray, angle)
                        skew_ev = _ocr(upright)
                    else:
                        upright, skew_ev = None, base
                    t1 = imaging.turn(gray, 1)
                    t3 = imaging.turn(gray, 3)
                    turn1 = _ocr(imaging.rotate(t1, imaging.skew_angle(t1)))
                    turn3 = _ocr(imaging.rotate(t3, imaging.skew_angle(t3)))
                    base_for_deshred = upright if upright is not None else gray
                    desh = imaging.realign_bands(base_for_deshred)
                    deshred_fired = desh is not None
                    deshred_ev = _ocr(desh) if deshred_fired else base
                    olabel, oconf = pipeline.orientation(gray)
                    offs = imaging._band_offsets(gray)
                    if offs is not None and np.count_nonzero(~np.isnan(offs)):
                        spread = float(np.nanmax(offs) - np.nanmin(offs))
                    else:
                        spread = 0.0
                    best = max(base, skew_ev, turn1, turn3, deshred_ev)
                    rows.append({
                        "stem": stem, "page": pg.page_no,
                        "base": base, "skew": skew_ev, "turn1": turn1, "turn3": turn3,
                        "deshred": deshred_ev, "best": best, "headroom": best - base,
                        "angle": round(float(angle), 2), "orient": olabel,
                        "orient_conf": round(float(oconf), 2),
                        "deshred_fired": deshred_fired, "spread": round(spread, 1),
                        "mean": round(float(gray.mean()), 1),
                        "ink_frac": round(float((gray < imaging.INK).mean()), 4),
                    })
    except Exception as exc:  # noqa: BLE001  (a bad PDF just contributes nothing)
        rows.append({"stem": stem, "error": f"{type(exc).__name__}: {exc}"})
    return rows


def run_census(procs):
    stems = sorted(p.stem for p in (CH / "data/train").glob("*.pdf"))
    print(f"census over {len(stems)} packets on {procs} procs...", file=sys.stderr)
    t0 = time.time()
    with multiprocessing.Pool(procs) as pool:
        rows = [r for batch in pool.imap_unordered(census_page, stems) for r in batch]
    CENSUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CENSUS_PATH.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    good = [r for r in rows if "error" not in r]
    print(f"census: {len(good)} scan pages ({len(rows) - len(good)} errors) "
          f"in {time.time() - t0:.0f}s -> {CENSUS_PATH}", file=sys.stderr)
    return good


def load_census():
    rows = [json.loads(l) for l in CENSUS_PATH.read_text().splitlines() if l.strip()]
    return [r for r in rows if "error" not in r]


def is_dead(r):
    """Faint/optical floor: nothing the geometry ladder does reaches it."""
    return r["best"] == 0 and (r["ink_frac"] < 0.01 or r["mean"] > 235)


def classify(r):
    if r["orient"] in ("turn1", "turn3"):
        return "turned"
    skewed = abs(r["angle"]) >= imaging.MIN_SKEW
    if r["deshred_fired"] and skewed:
        return "skew+shred"
    if r["deshred_fired"]:
        return "shred"
    if skewed:
        return "skew"
    return "unclassified"


def select(rows, n, hard_max):
    """dead -> faint list; the rest, base<hard_max, ranked + stratified -> the bench."""
    faint = [r for r in rows if is_dead(r)]
    live = [r for r in rows if not is_dead(r)]
    for r in live:
        r["class"] = classify(r)

    by_key = {(r["stem"], r["page"]): r for r in live}
    buckets = defaultdict(list)
    for r in live:
        if r["base"] < hard_max:                      # "not already good enough" (GOOD_ENOUGH)
            buckets[r["class"]].append(r)
    for k in buckets:
        buckets[k].sort(key=lambda r: (r["base"], -r["headroom"]))  # hardest, most recoverable first

    selected, seen = [], set()

    def take(r):
        key = (r["stem"], r["page"])
        if key not in seen:
            seen.add(key)
            selected.append(r)

    for stem, page in HAZARDS:                         # guards first, even if base>=hard_max
        r = by_key.get((stem, page))
        if r is not None:
            r.setdefault("class", classify(r))
            r["hazard"] = True
            take(r)
        else:
            print(f"  WARN: hazard {stem} p{page} not in census (not scan-only?)", file=sys.stderr)

    # round-robin across classes: equal voice to rare classes, hardest-first within each
    while len(selected) < n and any(buckets[k] for k in buckets):
        for k in CLASS_ORDER:
            if buckets[k] and len(selected) < n:
                take(buckets[k].pop(0))
    return selected, faint


def write_lists(selected, faint):
    lines = ["# hard-but-recoverable scan pages, mined corpus-wide by mine_hard.py",
             "# CASE PAGE  # class base->best (headroom N) [HAZARD]"]
    for r in sorted(selected, key=lambda r: (CLASS_ORDER.index(r["class"]), r["base"], -r["headroom"])):
        haz = " HAZARD" if r.get("hazard") else ""
        lines.append(f"{r['stem']} {r['page']}  # {r['class']} "
                     f"{r['base']}->{r['best']} (headroom {r['headroom']}){haz}")
    HARD_PAGES.write_text("\n".join(lines) + "\n")

    fl = ["# dead/faint floor (best_ev==0, near-zero ink) — optical track, not geometry",
          "# CASE PAGE  # mean=.. ink=.."]
    for r in sorted(faint, key=lambda r: r["mean"]):
        fl.append(f"{r['stem']} {r['page']}  # mean={r['mean']} ink={r['ink_frac']}")
    HARD_FAINT.write_text("\n".join(fl) + "\n")


def _render_one(pair):
    stem, page = pair
    try:
        return rb.row_for(stem, page, {}, ocr=True)
    except Exception as exc:  # noqa: BLE001
        return {"id": f"{stem} p{page}", "expect": f"ERROR {type(exc).__name__}: {exc}",
                "cells": []}


def _gain(row):
    evs = [c.get("ev") for c in row.get("cells", []) if c.get("ev") is not None]
    if not evs:
        return -999
    return max(evs) - (row["cells"][0].get("ev") or 0)


def build_and_render(selected, faint, corpus, procs):
    pairs = [(r["stem"], r["page"]) for r in selected]
    with multiprocessing.Pool(procs) as pool:
        html_rows = pool.map(_render_one, pairs)
    html_rows.sort(key=_gain, reverse=True)

    hist = defaultdict(int)
    for r in selected:
        hist[r["class"]] += 1
    hist_str = " ".join(f"{k} {hist[k]}" for k in CLASS_ORDER if hist[k])
    mean_base_sel = sum(r["base"] for r in selected) / max(1, len(selected))
    mean_base_corpus = sum(r["base"] for r in corpus) / max(1, len(corpus))
    meta = (f"{len(selected)} hard pages mined corpus-wide ({len(corpus)} scan pages) · "
            f"classes: {hist_str} · {sum(1 for r in selected if r.get('hazard'))} hazards · "
            f"mean base_ev {mean_base_sel:.2f} (corpus {mean_base_corpus:.2f}) · "
            f"dead/faint routed out: {len(faint)} · "
            f"stages source->orient->deskew->deshred (PSM11; ev + text) · green = guard keeps · "
            f"sorted by evidence gain")
    HTML_OUT.parent.mkdir(parents=True, exist_ok=True)
    HTML_OUT.write_text(rb.render_html(html_rows, meta))
    print("\n" + meta, file=sys.stderr)
    print(f"{HTML_OUT}  {HTML_OUT.stat().st_size / 1024:.0f} KB", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-select", type=int, default=100)
    ap.add_argument("--hard-max", type=int, default=render.GOOD_ENOUGH,
                    help="a page counts as hard if base_ev < this (default GOOD_ENOUGH)")
    ap.add_argument("--procs", type=int, default=min(8, (multiprocessing.cpu_count() or 4)))
    ap.add_argument("--no-census", action="store_true", help="reuse output/viz/hard_census.jsonl")
    args = ap.parse_args()

    corpus = load_census() if (args.no_census and CENSUS_PATH.exists()) else run_census(args.procs)
    selected, faint = select(corpus, args.n_select, args.hard_max)
    write_lists(selected, faint)
    print(f"selected {len(selected)} hard pages -> {HARD_PAGES.name}; "
          f"{len(faint)} faint -> {HARD_FAINT.name}", file=sys.stderr)
    build_and_render(selected, faint, corpus, args.procs)


if __name__ == "__main__":
    main()
