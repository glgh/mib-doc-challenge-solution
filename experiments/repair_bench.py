#!/usr/bin/env python3
"""Fast visual before/after for the repair stages in `pipeline.py`.

For each test page: source → deskewed → deshred(border) vs deshred(content),
each as a thumbnail with a one-line caption. Edit `pipeline.py`, rerun this, look
at the images. Geometry-only by default (milliseconds/page, no OCR); pass --ocr
to annotate each cell with tesseract evidence (slow).

Usage:
  experiments/repair_bench.py [--set hard|bag|both] [--ocr] [out.html]

Opens on nothing external; writes a self-contained HTML you can open or publish.
"""
import base64
import io
import json
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CH = ROOT.parent / "mib-doc-challenge"

from mib import imaging                       # noqa: E402
from mib.stages import extract, render        # noqa: E402
import pipeline                               # noqa: E402  (sibling)

HARD = ROOT / "experiments/hard_cases.jsonl"
BAG = ROOT / "experiments/test_pages.txt"
THUMB_W = 300


def source_gray(case, page):
    with extract.open_document(CH / f"data/train/{case}.pdf") as doc:
        with tempfile.TemporaryDirectory(prefix="bench") as tmp:
            return next(render._sources(doc, doc[page], tmp))[2]


def _uri(gray, width=THUMB_W, q=60):
    img = Image.fromarray(gray)
    if img.width > width:
        img = img.resize((width, max(1, round(img.height * width / img.width))),
                         Image.LANCZOS)
    buf = io.BytesIO()
    img.convert("L").save(buf, format="JPEG", quality=q, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def _ev(gray):
    with tempfile.TemporaryDirectory(prefix="bev") as tmp:
        p = Path(tmp) / "x.png"
        p.write_bytes(imaging.to_png_bytes(gray))
        return render.evidence_score(render._tesseract(p))


def _cell(gray, label, ocr):
    ev = f" · ev {_ev(gray)}" if ocr else ""
    return {"uri": _uri(gray), "label": label + ev}


def row_for(case, page, expect, ocr):
    src = source_gray(case, page)
    ori_label, ori = pipeline.orient(src)
    oriented = ori if ori is not None else src
    dsk_label, dsk = pipeline.deskew(oriented)
    base = dsk if dsk is not None else oriented
    cells = [_cell(src, "source", ocr),
             _cell(oriented, ori_label, ocr),
             _cell(base, dsk_label, ocr)]
    for stage in (pipeline.deshred_border, pipeline.deshred_content):
        label, img = stage(base)
        cells.append(_cell(img if img is not None else base, label, ocr))
    exp = ""
    if expect:
        exp = "  ".join(f"{k}={v}" for k, v in expect.items())
    return {"id": f"{case} p{page}", "expect": exp, "cells": cells}


def load_hard():
    for line in HARD.read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            if r["track"] == "geometry":
                yield r["case"], r["page"], r.get("expect", {})


def load_bag():
    for line in BAG.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            case, page = line.split()
            yield case, int(page), {}


CSS = """
body{background:#0f1416;color:#dde3e2;font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;margin:0;padding:1.5rem}
h1{font-size:1.3rem;margin:0 0 .3rem}.sub{color:#7a8b8d;font:12px ui-monospace,Menlo,monospace;margin:0 0 1.5rem}
.row{border-top:1px solid #263436;padding:1rem 0}
.rowhead{display:flex;gap:1rem;align-items:baseline;margin-bottom:.5rem}
.rid{font:13px ui-monospace,Menlo,monospace;color:#dde3e2}
.exp{font:11px ui-monospace,Menlo,monospace;color:#56b3bd}
.strip{display:flex;gap:.6rem;overflow-x:auto;padding-bottom:.5rem}
figure{margin:0;flex:0 0 230px}
.stagenum{color:#56b3bd;font-weight:700}
img{width:100%;height:auto;display:block;background:#fff;border:1px solid #263436}
figcaption{font:11px ui-monospace,Menlo,monospace;color:#a3b3b4;margin-top:.3rem;word-break:break-word}
"""


def render_html(rows, meta):
    body = []
    for r in rows:
        figs = "".join(
            f'<figure><img src="{c["uri"]}" alt="{c["label"]}">'
            f'<figcaption><span class="stagenum">{i}.</span> {c["label"]}</figcaption>'
            f'</figure>' for i, c in enumerate(r["cells"], 1))
        exp = f'<span class="exp">expect: {r["expect"]}</span>' if r["expect"] else ""
        body.append(f'<div class="row"><div class="rowhead"><span class="rid">'
                    f'{r["id"]}</span>{exp}</div><div class="strip">{figs}</div></div>')
    return (f"<!doctype html><meta charset=utf-8><title>repair bench</title>"
            f"<style>{CSS}</style><h1>Repair bench — before / after</h1>"
            f'<p class="sub">{meta}</p>{"".join(body)}')


def main(which="hard", ocr=False, out=None):
    pages = list(load_hard()) if which in ("hard", "both") else []
    if which in ("bag", "both"):
        pages += list(load_bag())
    rows = []
    for i, (case, page, expect) in enumerate(pages):
        rows.append(row_for(case, page, expect, ocr))
        if (i + 1) % 10 == 0:
            print(f"  {i + 1}/{len(pages)}", file=sys.stderr)
    meta = (f"{len(rows)} pages · set={which} · ocr={'on' if ocr else 'off'} · "
            f"stages: source → orient → deskew → deshred(border) vs deshred(content)")
    out = Path(out or ROOT / "output/viz/repair_bench.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(rows, meta))
    print(f"{out}  {out.stat().st_size / 1024:.0f} KB, {len(rows)} pages", file=sys.stderr)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:]]
    ocr = "--ocr" in args
    args = [a for a in args if a != "--ocr"]
    which = "hard"
    if "--set" in args:
        i = args.index("--set")
        which = args[i + 1]
        args = args[:i] + args[i + 2:]
    out = args[0] if args else None
    main(which, ocr, out)
