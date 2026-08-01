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
            return next(render._sources(doc, doc[page]))[2]


def _uri(gray, width=THUMB_W, q=60):
    img = Image.fromarray(gray)
    if img.width > width:
        img = img.resize((width, max(1, round(img.height * width / img.width))),
                         Image.LANCZOS)
    buf = io.BytesIO()
    img.convert("L").save(buf, format="JPEG", quality=q, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def _read(gray):
    """OCR once; return (page_score, recovered lines)."""
    with tempfile.TemporaryDirectory(prefix="bev") as tmp:
        p = Path(tmp) / "x.png"
        p.write_bytes(imaging.to_png_bytes(gray))
        lines = render._tesseract(p)
    return render.page_score(lines), lines


def _cell(gray, label, ocr):
    """One stage thumbnail. With --ocr, carry the ev score AND the recovered
    text, so fidelity is judged by eye on the page + the lines it yields — not by
    the ev scalar alone (which is coarse and misaligned, see the plan)."""
    ev, text = (None, None)
    if ocr:
        ev, text = _read(gray)
    return {"uri": _uri(gray), "label": label, "ev": ev, "text": text}


def row_for(case, page, expect, ocr):
    """source → orient → deskew → deshred(border), guarded.

    deshred/content is dropped (abandoned — findings.md). The last two cells are
    the guard's two candidates: the pre-deshred base and the border-deshredded
    image; when --ocr, the one the guard (`best()` by ev) would keep is flagged.
    """
    src = source_gray(case, page)
    ori_label, ori = pipeline.orient(src)
    oriented = ori if ori is not None else src
    dsk_label, dsk = pipeline.deskew(oriented)
    base = dsk if dsk is not None else oriented
    dsh_label, dsh = pipeline.deshred_border(base)
    deshredded = dsh if dsh is not None else base

    cells = [_cell(src, "source", ocr),
             _cell(oriented, ori_label, ocr),
             _cell(base, "deskew → guard base", ocr),
             _cell(deshredded, dsh_label, ocr)]

    # The guard: keep whichever of {pre-deshred base, post-deshred} scores higher.
    # Strict '>' so a tie keeps the pre-deshred (cheaper, earlier) reading.
    if ocr and dsh is not None:
        base_ev, desh_ev = cells[2]["ev"], cells[3]["ev"]
        if desh_ev > base_ev:
            cells[3]["win"] = True
        else:
            cells[2]["win"] = True

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
figure{margin:0;flex:0 0 260px;border:1px solid transparent;border-radius:4px;padding:.3rem}
figure.win{border-color:#5ad17f;background:#12211a}
.stagenum{color:#56b3bd;font-weight:700}
.ev{color:#e0c060}.pick{color:#5ad17f;font-weight:700}
img{width:100%;height:auto;display:block;background:#fff;border:1px solid #263436}
figcaption{font:11px ui-monospace,Menlo,monospace;color:#a3b3b4;margin-top:.3rem;word-break:break-word}
.ocrtext{font:10px/1.35 ui-monospace,Menlo,monospace;color:#8fa3a4;background:#0a0f10;border:1px solid #1b2628;border-radius:3px;margin-top:.3rem;padding:.35rem;max-height:190px;overflow:auto;white-space:pre-wrap}
"""


def _esc(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def render_html(rows, meta):
    body = []
    for r in rows:
        figs = []
        for i, c in enumerate(r["cells"], 1):
            ev = f' · <span class="ev">ev {c["ev"]}</span>' if c.get("ev") is not None else ""
            pick = ' <span class="pick">◀ guard keeps</span>' if c.get("win") else ""
            text = ""
            if c.get("text") is not None:
                body_txt = _esc("\n".join(c["text"])) if c["text"] else "(no lines)"
                text = f'<div class="ocrtext">{body_txt}</div>'
            cls = " win" if c.get("win") else ""
            figs.append(
                f'<figure class="cell{cls}"><img src="{c["uri"]}" alt="{_esc(c["label"])}">'
                f'<figcaption><span class="stagenum">{i}.</span> {_esc(c["label"])}{ev}{pick}'
                f'</figcaption>{text}</figure>')
        exp = f'<span class="exp">expect: {_esc(r["expect"])}</span>' if r["expect"] else ""
        body.append(f'<div class="row"><div class="rowhead"><span class="rid">'
                    f'{_esc(r["id"])}</span>{exp}</div><div class="strip">{"".join(figs)}</div></div>')
    return (f"<!doctype html><meta charset=utf-8><title>repair bench</title>"
            f"<style>{CSS}</style><h1>Repair bench — before / after (with recovered text)</h1>"
            f'<p class="sub">{_esc(meta)}</p>{"".join(body)}')


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
            f"stages: source → orient → deskew(guard base) → deshred(border); "
            f"green = the reading the guard keeps")
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
