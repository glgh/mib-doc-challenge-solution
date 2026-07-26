#!/usr/bin/env python3
"""Crop a region of a case page at high zoom, for actually READING the pixels.

Full-page renders and contact sheets compress a line of print to a few pixels;
judging legibility off them is guesswork (2026-07-26 gallery session — the
model needs the same zoom a human uses). This renders straight from the PDF at
print resolution, optionally cropped by page-fraction bounds.

Usage:
  experiments/crop.py MIB-000079 1                    # whole page at zoom 4
  experiments/crop.py MIB-000079 1 --bottom 0.25      # top quarter (headers)
  experiments/crop.py MIB-000033 0 --top 0.05 --bottom 0.2 --zoom 6
Writes output/viz/crops/<case>_p<page>[_<t>-<b>].png and prints the path.
"""
import argparse
import sys
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parent.parent
CH = ROOT.parent / "mib-doc-challenge"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("case_id")
    ap.add_argument("page", type=int)
    ap.add_argument("--top", type=float, default=0.0, help="crop top, page fraction")
    ap.add_argument("--bottom", type=float, default=1.0, help="crop bottom, page fraction")
    ap.add_argument("--left", type=float, default=0.0)
    ap.add_argument("--right", type=float, default=1.0)
    ap.add_argument("--zoom", type=float, default=4.0, help="~72*zoom DPI")
    ap.add_argument("--split", default="train", choices=("train", "validation"))
    args = ap.parse_args()

    doc = fitz.open(CH / f"data/{args.split}/{args.case_id}.pdf")
    page = doc[args.page]
    r = page.rect
    clip = fitz.Rect(r.x0 + args.left * r.width, r.y0 + args.top * r.height,
                     r.x0 + args.right * r.width, r.y0 + args.bottom * r.height)
    pix = page.get_pixmap(matrix=fitz.Matrix(args.zoom, args.zoom), clip=clip)
    out_dir = ROOT / "output/viz/crops"
    out_dir.mkdir(parents=True, exist_ok=True)
    frag = ""
    if (args.top, args.bottom, args.left, args.right) != (0.0, 1.0, 0.0, 1.0):
        frag = f"_{args.top:g}-{args.bottom:g}x{args.left:g}-{args.right:g}"
    out = out_dir / f"{args.case_id}_p{args.page}{frag}.png"
    pix.save(out)
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
