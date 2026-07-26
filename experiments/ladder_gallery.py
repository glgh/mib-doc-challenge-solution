#!/usr/bin/env python3
"""Per-page repair-ladder gallery: one composite PNG per case page showing every
geometric variant image the pipeline OCR'd, its evidence score, and the risk
line it read. Winner (best_read) framed in green.

Usage:
  experiments/ladder_gallery.py [case:page ...] [--out-dir output/viz]

Pages are 1-indexed (as in docs/TODO.md's anchor table). With no arguments the
four flag-autopsy anchors from the 2026-07-25 session are rendered.
"""
import argparse
import re
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CH = ROOT.parent / "mib-doc-challenge"

from mib.stages import extract, render       # noqa: E402

OUT = ROOT / "output/viz"
FONT = ImageFont.load_default(22)
FONT_SM = ImageFont.load_default(17)

RISK_RE = re.compile(r"(?i)(observed|obs\w{0,5}\b|f[il1]?ags|flags|fags|RISK PANEL)")

THUMB_H = 300
STRIP_W = 620
PAD = 14


def risk_line(lines):
    for l in lines:
        if RISK_RE.search(l):
            return l.strip()
    return None


def variants_for(doc, page):
    """(variant_name, gray_image) for every ladder rung, both sources."""
    with tempfile.TemporaryDirectory(prefix="gal") as tmp:
        for name, _encoded, gray in render._sources(doc, page, tmp):
            yield name, gray
            for vname, img in render._restorations(gray):
                yield f"{name}+{vname}", img


def ocr(gray):
    with tempfile.NamedTemporaryFile(suffix=".png") as f:
        Image.fromarray(gray).convert("L").save(f.name)
        lines = render._tesseract(f.name)
    return lines, render.evidence_score(lines)


def cell(gray, caption_lines, won):
    """One variant as a PIL image: full-page thumb + top-strip crop + caption."""
    img = Image.fromarray(gray).convert("L")
    thumb = img.resize((max(1, round(img.width * THUMB_H / img.height)), THUMB_H),
                       Image.LANCZOS)
    strip_src = img.crop((0, 0, img.width, round(img.height * 0.30)))
    strip = strip_src.resize(
        (STRIP_W, max(1, round(strip_src.height * STRIP_W / strip_src.width))),
        Image.LANCZOS)
    cap_h = 34 + 26 * len(caption_lines)
    w = PAD * 3 + thumb.width + STRIP_W
    h = PAD * 2 + cap_h + max(THUMB_H, strip.height)
    out = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(out)
    y = PAD
    for i, txt in enumerate(caption_lines):
        d.text((PAD, y), txt, fill="black" if i == 0 else (70, 70, 70),
               font=FONT if i == 0 else FONT_SM)
        y += 30 if i == 0 else 26
    y += 6
    out.paste(thumb, (PAD, y))
    out.paste(strip, (PAD * 2 + thumb.width, y))
    d.rectangle([PAD, y, PAD + thumb.width, y + thumb.height], outline=(180, 180, 180))
    d.rectangle([PAD * 2 + thumb.width, y, PAD * 2 + thumb.width + strip.width,
                 y + strip.height], outline=(180, 180, 180))
    if won:
        d.rectangle([1, 1, w - 2, h - 2], outline=(0, 150, 0), width=5)
    return out


def gallery(case, page_no, note):
    with extract.open_document(CH / f"data/train/{case}.pdf") as doc:
        entries = []
        for vname, gray in variants_for(doc, doc[page_no]):
            lines, q = ocr(gray)
            entries.append((vname, gray, lines, q))
    best_q = max(q for _, _, _, q in entries)
    winner = next(i for i, e in enumerate(entries) if e[3] == best_q)
    cells = []
    for i, (vname, gray, lines, q) in enumerate(entries):
        rl = risk_line(lines)
        cap = [f"{vname}   evidence={q}" + ("   << CHOSEN" if i == winner else ""),
               f"risk line: {rl[:64]}" if rl else "risk line: (not read)"]
        cells.append(cell(gray, cap, i == winner))
    w = max(c.width for c in cells)
    header_h = 64
    total = Image.new("RGB", (w, header_h + sum(c.height + 8 for c in cells)), "white")
    d = ImageDraw.Draw(total)
    d.text((PAD, 12), f"{case} page {page_no + 1} - repair ladder ({note})",
           fill="black", font=FONT)
    d.text((PAD, 40), "left: full page after repair | right: top strip (field block) of the same image",
           fill=(70, 70, 70), font=FONT_SM)
    y = header_h
    for c in cells:
        total.paste(c, (0, y))
        y += c.height + 8
    path = OUT / f"ladder_{case}_p{page_no + 1}.png"
    total.save(path)
    print(path)


# The 2026-07-25 flag-autopsy anchors (docs/TODO.md), rendered when no jobs given.
DEFAULT_JOBS = [
    ("MIB-000252", 1, "printed token, OCR-mangled"),
    ("MIB-000595", 2, "printed token, shredded page"),
    ("MIB-000990", 0, "printed token, rotated+debris"),
    ("MIB-000747", 4, "damage marker, reads clean"),
]

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("jobs", nargs="*", metavar="case:page",
                    help="e.g. MIB-000252:2 (page 1-indexed)")
    ap.add_argument("--out-dir", default=str(OUT))
    args = ap.parse_args()
    OUT = Path(args.out_dir)
    OUT.mkdir(parents=True, exist_ok=True)
    jobs = DEFAULT_JOBS
    if args.jobs:
        jobs = []
        for spec in args.jobs:
            case, page = spec.rsplit(":", 1)
            jobs.append((case, int(page) - 1, "requested"))
    for case, pno, note in jobs:
        gallery(case, pno, note)
