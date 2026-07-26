#!/usr/bin/env python3
"""Border coverage / one-edge recovery / residual jaggedness probe.

Three questions about `imaging._band_offsets` (the deshred boundary reader), asked
over the census under-deshred pool (deshred fired with gain<=1 on a still-poor page,
or spread>=30px with no fire):

1. COVERAGE — of the rows that show any dark span, how many pass the width test and
   actually get measured? Low coverage = the realigner is flying blind on most rows.
2. ONE-EDGE HEADROOM — rows that fail the width test with the clip signature (one
   border line at the crop edge, span short of border width): the surviving edge
   still gives the offset the pair test throws away. Counted per side.
3. RESIDUAL JAGGEDNESS — after the shipped realign, re-measure the trusted left
   edges on the output: max-min spread in px. Caveat: trusted rows are by
   construction the measurable ones, so this is blind to clipped bands. The
   edge-trace metric (`edge_stats`) closes that: it tracks the leftmost dark
   pixel of every row, no width test, and reports its robust spread and the
   fraction of rows deviating >5px from the median — the number the eye reads
   as "jagged border".

Also simulates an augmented realign (trusted + clip-recovered rows through the same
hold-last-shift walk) and reports how many pages it moves beyond the shipped one.
With --viz N, renders the top pages by recovered rows as PNG strips (deskewed base
vs shipped deshred vs augmented deshred, each OCR'd for evidence).

Usage:
  experiments/probe_border_cover.py [--procs 8] [--viz 6] [--out out.jsonl]
"""
import argparse
import json
import multiprocessing
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

import pipeline                        # noqa: E402
import repair_bench as rb              # noqa: E402
from mib import imaging                # noqa: E402

CENSUS = ROOT / "output/viz/hard_census.jsonl"
OUT = ROOT / "output/viz/border_cover.jsonl"
WIDTH_TOL = 6                          # same tolerance as _band_offsets
CLIP_MARGIN = 3                        # px from the crop edge that counts as "at the edge"


def _spans(gray):
    """Per row: (leftmost dark x, rightmost dark x) or (-1, -1). Same mask as the
    shipped detector (thresh=150 catches border/form-rule ink)."""
    dark = imaging.ink_mask(gray, thresh=150)
    out = []
    for row in dark:
        xs = np.flatnonzero(row)
        out.append((int(xs[0]), int(xs[-1])) if len(xs) >= 2 else (-1, -1))
    return out


def classify(gray):
    """Row-by-row account of what the border reader sees.

    Returns None when the shipped gates say "no border" (mirrors _band_offsets),
    else a dict with per-row classes and two offset arrays: `trusted` (what the
    shipped detector measures) and `augmented` (trusted + clip-recovered rows).
    """
    W = gray.shape[1]
    spans = _spans(gray)
    widths = [r - l for l, r in spans if l >= 0 and r > l]
    if len(widths) < 20:
        return None
    bw = int(np.median(widths))
    if bw < W // 2:
        return None
    trusted = np.full(len(spans), np.nan)
    augmented = np.full(len(spans), np.nan)
    n_trust = n_rclip = n_lclip = n_dark = 0
    for y, (l, r) in enumerate(spans):
        if l < 0:
            continue
        n_dark += 1
        w = r - l
        if abs(w - bw) <= WIDTH_TOL:
            trusted[y] = augmented[y] = l
            n_trust += 1
        elif w < bw - WIDTH_TOL and r >= W - 1 - CLIP_MARGIN:
            augmented[y] = l              # slid right, right line off-crop
            n_rclip += 1
        elif w < bw - WIDTH_TOL and l <= CLIP_MARGIN:
            augmented[y] = r - bw         # slid left, left line off-crop
            n_lclip += 1
    if n_trust < 20:
        return None
    return {"bw": bw, "trusted": trusted, "augmented": augmented,
            "n_dark": n_dark, "n_trust": n_trust,
            "n_rclip": n_rclip, "n_lclip": n_lclip}


def realign_from(gray, offsets):
    """The shipped hold-last-shift walk, over an arbitrary offset array."""
    reference = np.nanmedian(offsets)
    if np.isnan(reference):
        return None
    out = gray.copy()
    shift = 0
    moved = False
    for y in range(gray.shape[0]):
        if not np.isnan(offsets[y]):
            shift = int(round(reference - offsets[y]))
        if shift:
            out[y] = np.roll(gray[y], shift)
            moved = True
    return out if moved else None


def jag(gray):
    """Residual jaggedness: max-min of the trusted left edges, px; None if the
    border is unreadable on this image."""
    c = classify(gray)
    if c is None:
        return None
    t = c["trusted"]
    return float(np.nanmax(t) - np.nanmin(t))


def edge_stats(gray, extent):
    """Visual jaggedness of the border, width-test-free: within the border's
    vertical extent the leftmost dark pixel per row is its left line, clipped or
    not. Rows outside the extent trace content (headers, stamps), not border, so
    they are excluded. Robust spread (p98-p2) and the fraction of rows >5px off
    the median. Measure on pad-shifted images, not rolled ones — wrapped pixels
    land at the far edge and read as false jag."""
    y0, y1 = extent
    xs0 = np.array([l for l, _ in _spans(gray[y0:y1 + 1]) if l >= 0], dtype=float)
    if len(xs0) < 20:
        return None
    med = float(np.median(xs0))
    return {"spread": round(float(np.percentile(xs0, 98) - np.percentile(xs0, 2)), 1),
            "ragged_frac": round(float((np.abs(xs0 - med) > 5).mean()), 3)}


def _extent(offsets):
    """(first, last) row index with a measured offset — the border's vertical span."""
    idx = np.flatnonzero(~np.isnan(offsets))
    return int(idx[0]), int(idx[-1])


def realign_padded(gray, offsets):
    """Same walk as `realign_from`, but shifted rows are padded with paper white
    instead of wrapped, so downstream edge tracing sees no roll artifacts."""
    reference = np.nanmedian(offsets)
    if np.isnan(reference):
        return None
    out = gray.copy()
    shift = 0
    moved = False
    for y in range(gray.shape[0]):
        if not np.isnan(offsets[y]):
            shift = int(round(reference - offsets[y]))
        if shift:
            row = np.full_like(gray[y], 255)
            if shift > 0:
                row[shift:] = gray[y][:-shift]
            else:
                row[:shift] = gray[y][-shift:]
            out[y] = row
            moved = True
    return out if moved else None


def probe_page(pair):
    stem, page = pair
    try:
        src = rb.source_gray(stem, page)
        _, ori = pipeline.orient(src)
        oriented = ori if ori is not None else src
        _, dsk = pipeline.deskew(oriented)
        base = dsk if dsk is not None else oriented
        c = classify(base)
        row = {"stem": stem, "page": page}
        if c is None:
            row.update(border=False)
            return row
        shipped = imaging.realign_bands(base)
        aug = realign_from(base, c["augmented"])
        differs = (aug is not None and
                   (shipped is None or not np.array_equal(shipped, aug)))
        row.update(
            border=True, bw=c["bw"],
            cover=round(c["n_trust"] / max(c["n_dark"], 1), 3),
            n_trust=c["n_trust"], n_rclip=c["n_rclip"], n_lclip=c["n_lclip"],
            jag_src=jag(base),
            jag_shipped=jag(shipped) if shipped is not None else jag(base),
            jag_aug=jag(aug) if aug is not None else None,
            edge_src=edge_stats(base, _extent(c["trusted"])),
            edge_shipped=edge_stats(
                realign_padded(base, c["trusted"]) if shipped is not None else base,
                _extent(c["trusted"])),
            edge_aug=edge_stats(realign_padded(base, c["augmented"]),
                                _extent(c["augmented"])) if aug is not None else None,
            aug_differs=bool(differs))
        return row
    except Exception as exc:           # noqa: BLE001
        return {"stem": stem, "page": page, "error": f"{type(exc).__name__}: {exc}"}


def pool_pages():
    rows = [json.loads(l) for l in CENSUS.read_text().splitlines() if l.strip()]
    rows = [r for r in rows if "error" not in r]
    live = [r for r in rows
            if not (r["best"] == 0 and (r["ink_frac"] < 0.01 or r["mean"] > 235))]
    return [(r["stem"], r["page"]) for r in live
            if (r["deshred_fired"] and r["deshred"] - r["base"] <= 1 and r["best"] <= 4)
            or (not r["deshred_fired"] and r["spread"] >= 30 and r["best"] <= 6)]


def viz(results, n, out_dir):
    """PNG strips for the top-N pages by clip-recovered rows: deskewed base vs
    shipped deshred vs augmented deshred, each OCR'd. Repair quality is judged by
    the user off these images, not here."""
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 22)
    top = sorted((r for r in results if r.get("aug_differs")),
                 key=lambda r: -(r["n_rclip"] + r["n_lclip"]))[:n]
    paths = []
    for r in top:
        src = rb.source_gray(r["stem"], r["page"])
        _, ori = pipeline.orient(src)
        oriented = ori if ori is not None else src
        _, dsk = pipeline.deskew(oriented)
        base = dsk if dsk is not None else oriented
        c = classify(base)
        shipped = imaging.realign_bands(base)
        aug = realign_from(base, c["augmented"])
        cells = []
        for g, name in ((base, "deskewed base"),
                        (shipped if shipped is not None else base,
                         "shipped deshred" if shipped is not None else "shipped (no-op)"),
                        (aug, "augmented deshred")):
            ev, _ = rb._read(g)
            j = jag(g)
            cells.append((g, f"{name} · ev {ev} · jag {j:.0f}px" if j is not None
                          else f"{name} · ev {ev}"))
        cw = 640
        ims = []
        for g, _ in cells:
            im = Image.fromarray(g).convert("L").convert("RGB")
            ims.append(im.resize((cw, max(1, round(im.height * cw / im.width))),
                                 Image.LANCZOS))
        ch = max(im.height for im in ims)
        cap_h, pad, head_h = 34, 10, 44
        board = Image.new("RGB", (3 * (cw + pad) + pad, head_h + ch + cap_h + pad),
                          (15, 20, 22))
        d = ImageDraw.Draw(board)
        d.text((pad, 10),
               f"{r['stem']} p{r['page']} · trusted {r['n_trust']} · "
               f"clip-recovered L{r['n_lclip']}/R{r['n_rclip']}",
               font=font, fill=(221, 227, 226))
        for i, (im, (g, lab)) in enumerate(zip(ims, cells)):
            x = pad + i * (cw + pad)
            board.paste(im, (x, head_h))
            d.text((x, head_h + ch + 8), lab, font=font, fill=(163, 179, 180))
        p = out_dir / f"border_cover_{r['stem']}_p{r['page']}.png"
        board.save(p)
        paths.append(p)
        print(p, file=sys.stderr)
    return paths


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--procs", type=int, default=8)
    ap.add_argument("--viz", type=int, default=0)
    ap.add_argument("--viz-dir", default=str(ROOT / "output/viz"))
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    pages = pool_pages()
    print(f"probing {len(pages)} under-deshred pages on {args.procs} procs",
          file=sys.stderr)
    with multiprocessing.Pool(args.procs) as mp:
        results = mp.map(probe_page, pages)
    Path(args.out).write_text("\n".join(json.dumps(r) for r in results) + "\n")

    ok = [r for r in results if "error" not in r]
    bordered = [r for r in ok if r["border"]]
    recov = [r for r in bordered if r["n_rclip"] + r["n_lclip"] > 0]
    differs = [r for r in bordered if r["aug_differs"]]
    jagged = [r for r in bordered
              if r["jag_shipped"] is not None and r["jag_shipped"] > 5]
    print(f"pages {len(ok)} · border readable {len(bordered)} · "
          f"clip-recoverable rows on {len(recov)} pages · "
          f"augmented changes output on {len(differs)} · "
          f"residual jag>5px after shipped deshred on {len(jagged)}")
    if bordered:
        cov = sorted(r["cover"] for r in bordered)
        js = sorted(r["jag_shipped"] for r in bordered if r["jag_shipped"] is not None)
        print(f"coverage median {cov[len(cov) // 2]:.2f} · "
              f"jag_shipped median {js[len(js) // 2]:.0f}px "
              f"p90 {js[int(len(js) * .9)]:.0f}px")
        es = [r["edge_shipped"] for r in bordered if r["edge_shipped"]]
        if es:
            sp = sorted(e["spread"] for e in es)
            rf = sorted(e["ragged_frac"] for e in es)
            ragged = sum(1 for e in es if e["ragged_frac"] > 0.05)
            print(f"edge-trace after shipped: spread median {sp[len(sp) // 2]:.0f}px "
                  f"p90 {sp[int(len(sp) * .9)]:.0f}px · ragged_frac median "
                  f"{rf[len(rf) // 2]:.3f} · pages ragged_frac>0.05: {ragged}")
    if args.viz:
        viz(ok, args.viz, Path(args.viz_dir))


if __name__ == "__main__":
    main()
