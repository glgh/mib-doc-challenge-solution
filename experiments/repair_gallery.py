#!/usr/bin/env python3
"""Run the restoration ladder over a random sample of packets and render one
panel per stage per scan page, so repair quality can be judged by eye.

This is the `repair_bench` strip (source -> orient -> deskew -> deshred, each cell
carrying its tesseract evidence score and recovered text, with the reading the
guard keeps flagged) driven over N random *train* packets' scan-only pages instead
of the curated hard set. It is the "turn the bands ladder on and look at a random
sample" instrument: every rung of the ladder is applied and shown, cumulatively.

Parallel across pages. Rows are sorted by evidence gain (biggest saves first),
with untouched pages at the bottom; the aggregate line reports the honest
distribution over the whole random sample.

Usage:
  experiments/repair_gallery.py [--n 100] [--seed 8090] [--procs 8] [out.html]
"""
import argparse
import multiprocessing
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))          # so `import pipeline` inside repair_bench resolves

import repair_bench as rb              # noqa: E402
from mib.stages import extract         # noqa: E402

CH = ROOT.parent / "mib-doc-challenge"


def scan_pages(stem):
    """(stem, page_no) for each scan-only page in one packet; [] on any error."""
    out = []
    try:
        with extract.open_document(CH / f"data/train/{stem}.pdf") as doc:
            for page in extract.pages(doc):
                if page.is_scan_only:
                    out.append((stem, page.page_no))
    except Exception:                  # noqa: BLE001  (a bad PDF just contributes nothing)
        pass
    return out


def _one(pair):
    stem, page_no = pair
    try:
        return rb.row_for(stem, page_no, {}, ocr=True)
    except Exception as exc:           # noqa: BLE001
        return {"id": f"{stem} p{page_no}",
                "expect": f"ERROR {type(exc).__name__}: {exc}", "cells": []}


def _evs(row):
    """(source_ev, orient_ev, deskew_ev, deshred_ev); None where a cell is absent."""
    cells = row.get("cells", [])
    return [c.get("ev") for c in cells] + [None] * (4 - len(cells))


def summarize(rows):
    stats = {"pages": len(rows), "mute": 0, "rescued": 0,
             "orient_gain": 0, "deskew_gain": 0, "deshred_gain": 0,
             "any_gain": 0, "net_ev": 0}
    for row in rows:
        src, ori, dsk, dsh = _evs(row)
        if src is None:
            continue
        evs = [e for e in (src, ori, dsk, dsh) if e is not None]
        best = max(evs)
        if src == 0:
            stats["mute"] += 1
            if best > 0:
                stats["rescued"] += 1
        if ori is not None and ori > src:
            stats["orient_gain"] += 1
        if dsk is not None and dsk > (ori if ori is not None else src):
            stats["deskew_gain"] += 1
        if dsh is not None and dsh > (dsk if dsk is not None else src):
            stats["deshred_gain"] += 1
        if best > src:
            stats["any_gain"] += 1
        stats["net_ev"] += best - src
    return stats


def _gain(row):
    src, *_rest = _evs(row)
    evs = [e for e in _evs(row) if e is not None]
    if not evs or src is None:
        return -999
    return max(evs) - src


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out", nargs="?", default=str(ROOT / "output/viz/repair_gallery.html"))
    ap.add_argument("--n", type=int, default=100, help="random train packets to sample")
    ap.add_argument("--seed", type=int, default=8090)
    ap.add_argument("--procs", type=int, default=min(8, (multiprocessing.cpu_count() or 4)))
    args = ap.parse_args()

    stems = sorted(p.stem for p in (CH / "data/train").glob("*.pdf"))
    rng = random.Random(args.seed)
    sample = sorted(rng.sample(stems, min(args.n, len(stems))))
    print(f"sampled {len(sample)} packets (seed {args.seed})", file=sys.stderr)

    with multiprocessing.Pool(args.procs) as pool:
        pairs = [p for batch in pool.map(scan_pages, sample) for p in batch]
        print(f"{len(pairs)} scan-only pages; OCR-ing {4 * len(pairs)} stage images "
              f"on {args.procs} procs", file=sys.stderr)
        t0 = time.time()
        rows = pool.map(_one, pairs)
    print(f"rendered in {time.time() - t0:.0f}s", file=sys.stderr)

    rows.sort(key=_gain, reverse=True)
    st = summarize(rows)
    meta = (f"{len(sample)} random train packets (seed {args.seed}) · "
            f"{st['pages']} scan pages · stages source->orient->deskew->deshred "
            f"(tesseract PSM11; ev + recovered text) · green = guard keeps · "
            f"sorted by evidence gain · "
            f"mute {st['mute']} · rescued {st['rescued']} · any-gain {st['any_gain']} · "
            f"per-rung gains orient {st['orient_gain']} deskew {st['deskew_gain']} "
            f"deshred {st['deshred_gain']} · net evidence +{st['net_ev']}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(rb.render_html(rows, meta))
    print(meta, file=sys.stderr)
    print(f"{out}  {out.stat().st_size / 1024:.0f} KB", file=sys.stderr)


if __name__ == "__main__":
    main()
