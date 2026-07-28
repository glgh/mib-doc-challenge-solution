#!/usr/bin/env python3
"""Why risk_flags 'unreachable' cases are unrecoverable — shown, not asserted.

Three columns of the story, rendered crisp (fitz) with the OCR beside each:
  CONTROL        a readable B-13 biometric slip where the flag WAS recovered
  DESTROYED SCAN the slip is present but OCR is garbage across every variant
                 -> source vs best-restored frame + OCR both (is repair enough?)
  EVIDENCE ABSENT no flag document in the packet at all (registry CLEAR, no slip,
                 no hidden text, embedded image is a 512^2 placeholder)

Usage: experiments/flag_gallery.py   (writes output/viz/flag_gallery.html + PNGs)
"""
import base64, io, sys, tempfile
from pathlib import Path
import fitz, numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CH = ROOT.parent / "mib-doc-challenge"
from mib import cache, imaging                       # noqa: E402
from mib.stages import extract, render               # noqa: E402
import pipeline                                       # noqa: E402

meta, recs = cache.read("output/cache/train_grid.jsonl")
byid = {r["stem"]: r for r in recs}
outdir = ROOT / "output/viz/flag_gallery"; outdir.mkdir(parents=True, exist_ok=True)

# scan pages to show before/after (control + destroyed); evidence-absent packets
SCAN = [("MIB-000009", 2, "control", "illegible_biometrics", "slip readable — flag WAS recovered"),
        ("MIB-000018", 1, "destroyed", "illegible_biometrics", "B-13 slip, OCR ~ 'SS'"),
        ("MIB-000018", 2, "destroyed", "illegible_biometrics", "B-13 slip, OCR ~ ''")]
ABSENT = [("MIB-000463", "planetary_embargo"), ("MIB-000246", "biohazard_red")]


def fitz_png(case, page, zoom=2.2, width=760):
    doc = fitz.open(CH / f"data/train/{case}.pdf")
    pix = doc[page].get_pixmap(matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csGRAY)
    img = Image.open(io.BytesIO(pix.tobytes("png")))
    if img.width > width:
        img = img.resize((width, round(img.height * width / img.width)), Image.LANCZOS)
    return img


def emb_png(case, page, width=300):
    doc = fitz.open(CH / f"data/train/{case}.pdf")
    xref = doc[page].get_images(full=True)[0][0]
    pix = fitz.Pixmap(doc, xref)
    if pix.n > 4: pix = fitz.Pixmap(fitz.csRGB, pix)
    img = Image.open(io.BytesIO(pix.tobytes("png")))
    if img.width > width:
        img = img.resize((width, round(img.height * width / img.width)), Image.LANCZOS)
    return img


def uri(img, q=75):
    buf = io.BytesIO(); img.convert("L").save(buf, "JPEG", quality=q, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def ocr(gray):
    with tempfile.TemporaryDirectory(prefix="fg") as tmp:
        p = Path(tmp) / "x.png"; p.write_bytes(imaging.to_png_bytes(gray))
        return render._tesseract(p)


def src_gray(case, page):
    with extract.open_document(CH / f"data/train/{case}.pdf") as doc:
        with tempfile.TemporaryDirectory(prefix="fg") as tmp:
            return next(render._sources(doc, doc[page], tmp))[2]


def best_restored(src):
    cands = [("deskew/deshred", None)]
    _, o = pipeline.orient(src); base = o if o is not None else src
    _, d = pipeline.deskew(base); base = d if d is not None else base
    out = [("deskew", base)]
    _, sh = pipeline.deshred_border(base)
    if sh is not None: out.append(("deshred", sh))
    lc = imaging.realign_local(base)
    if lc is not None: out.append(("local", lc))
    scored = [(render.page_score(ls := ocr(g)), n, g, ls) for n, g in out]
    scored.sort(key=lambda x: -x[0]); return scored[0]


def np_uri(gray, width=380):
    img = Image.fromarray(gray)
    if img.width > width: img = img.resize((width, round(img.height*width/img.width)), Image.LANCZOS)
    return uri(img)


def txt(lines): return "\n".join(l for l in lines if l is not None) if lines else "(nothing recovered)"

H = ["<meta charset=utf-8><style>",
     "body{font:13px system-ui;margin:22px;background:#0f0f10;color:#ddd;max-width:1150px}",
     "h2{border-top:2px solid #444;padding-top:12px;margin-top:28px}",
     ".g{display:grid;grid-template-columns:1fr 1fr;gap:12px;align-items:start}",
     "img{border:1px solid #333;background:#fff;max-width:100%}",
     "pre{white-space:pre-wrap;background:#191919;padding:7px;border:1px solid #333;max-height:230px;overflow:auto;font:11.5px ui-monospace}",
     ".cap{color:#9cf;font-weight:600;margin:5px 0 3px}.t{color:#f89}.ok{color:#7d7}", "</style>",
     "<h1>risk_flags 'unreachable' — what's actually on the page</h1>"]

# --- degraded / control scans: before vs after repair ---
H.append("<h2>1 · Degraded biometric-slip scans — is repair enough?</h2>")
for cid, pg, kind, flag, note in SCAN:
    src = src_gray(cid, pg)
    s_lines = ocr(src)
    r_score, r_name, r_gray, r_lines = best_restored(src)
    fitz_png(cid, pg).save(outdir / f"{cid}_p{pg}.png")
    cls = "ok" if kind == "control" else "t"
    H.append(f"<h3 class={cls}>{cid} p{pg} — {kind.upper()} — truth {flag} ({note})</h3>")
    H.append("<div class=g>")
    H.append(f"<div><div class=cap>BEFORE — source</div><img src='{np_uri(src)}'></div>")
    H.append(f"<div><div class=cap>AFTER — {r_name} (page_score {r_score:.1f})</div><img src='{np_uri(r_gray)}'></div>")
    H.append(f"<div><div class=cap>BEFORE OCR</div><pre>{txt(s_lines)}</pre></div>")
    H.append(f"<div><div class=cap>AFTER OCR</div><pre>{txt(r_lines)}</pre></div>")
    H.append("</div>")
    print(f"{cid} p{pg} {kind}: before={len([l for l in s_lines if l.strip()])} lines, after={r_name} {r_score:.1f}")

# --- evidence absent: full packet + placeholder image ---
H.append("<h2>2 · Evidence absent — no flag document to repair</h2>")
for cid, flag in ABSENT:
    rec = byid[cid]
    H.append(f"<h3 class=t>{cid} — truth {flag} — packet has NO biometric slip / flags panel</h3>")
    strip = []
    for i, p in enumerate(rec["pages"]):
        label = (p["visible_lines"][0] if p["visible_lines"] else "(scan)")
        strip.append(f"<div style='flex:1'><div class=cap>p{i}: {label}</div><img src='{uri(fitz_png(cid,i,width=330))}'></div>")
    H.append("<div style='display:flex;gap:10px'>" + "".join(strip) + "</div>")
    # the embedded image a stamp could hide in
    try:
        H.append(f"<div class=cap>embedded raster on p0 (where a stamp would live) — 512² placeholder:</div>")
        H.append(f"<img src='{uri(emb_png(cid,0))}'>")
    except Exception as e:
        H.append(f"<div>(embedded image: {e})</div>")
    print(f"{cid}: {len(rec['pages'])} pages, evidence-absent")

out = ROOT / "output/viz/flag_gallery.html"; out.write_text("\n".join(H))
print("wrote", out)
