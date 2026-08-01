#!/usr/bin/env python3
"""Visual gallery for the euro-sign (€) OCR-confusion probe.

For each (case, page, variant) where a cached read showed a '€' glyph near a
label line, reconstruct the EXACT restored image that variant's chain would
have produced (same geometry code path as `render.reads_for`, minus the
tesseract call) and show it next to the raw source frame. House rule: repair-
quality judgment is the user's — this renders the comparison and changes
nothing in mib/.

Usage: experiments/euro_glyph_gallery.py [out.html]
"""
import base64
import io
import sys
import tempfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CH = ROOT.parent / "mib-doc-challenge"

from mib import config, imaging                # noqa: E402
from mib.stages import extract, render         # noqa: E402

THUMB_W = 700

# (case, page, variant, ocr_line, truth_date, note)
CASES = [
    ("MIB-000065", 1, "embedded+turn1+skew",
     "Zb-€0-9202 81eq JeAWY", "2026-03-12", "rotated (turn1) read"),
    ("MIB-000114", 0, "embedded",
     "Arrival Date: 2026-06-0€", "2026-06-06", "unrotated read"),
    ("MIB-000118", 3, "render+turn3+skew",
     "Z2-€0-9707 sed eA", "2026-03-22", "rotated (turn3) read"),
    ("MIB-000124", 1, "render+turn1+skew",
     "diis ueog aWjewolg -€L-8 WHOS", "2026-04-29", "biometric slip line, not the date field"),
    ("MIB-000146", 4, "embedded+turn1+skew",
     "92-€0-9702 91d jeauy", "2026-03-26", "rotated (turn1) read"),
    ("MIB-000270", 3, "embedded+turn1",
     "81-€0-9z0z a1eC JeAuy", "2026-03-18", "rotated (turn1) read"),
    ("MIB-000315", 1, "embedded+turn1+skew+deshred",
     "S16000-GIN <i €sn", "2025-12-09", "not a date line (case-id area)"),
    ("MIB-000607", 1, "embedded+turn1",
     "90-€0-9202 ied jeAWy", "2026-03-06", "rotated (turn1) read"),
]


def _uri(gray, width=THUMB_W, q=70):
    img = Image.fromarray(gray)
    if img.width > width:
        img = img.resize((width, max(1, round(img.height * width / img.width))),
                         Image.LANCZOS)
    buf = io.BytesIO()
    img.convert("L").save(buf, format="JPEG", quality=q, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def parse_variant(variant):
    parts = variant.split("+")
    name = parts[0]
    rest = parts[1:]
    mod = None
    if rest and rest[-1] in ("adapt", "autocon", "equalize"):
        mod = rest[-1]
        rest = rest[:-1]
    q = 0
    if rest and rest[0].startswith("turn"):
        q = int(rest[0][4:])
        rest = rest[1:]
    return name, q, tuple(rest), mod


def restored_image(doc, page, variant, plan):
    name, q, chain_parts, mod = parse_variant(variant)
    with tempfile.TemporaryDirectory(prefix="euro") as tmp:
        sources = {n: gray for n, _e, gray in render._sources(
            doc, page, tmp, plan.get("render_base", "up200"))}
    gray = sources[name]
    skew_deg = imaging.orientation_profile(gray)[q]["skew_deg"]
    target = (f"turn{q}",) + chain_parts if q else chain_parts
    if q == 0 and chain_parts == ():
        # The bare source read never goes through _orientation_chains — S2
        # emits it directly from `gray` in the base tier.
        match = gray
    else:
        match = None
        for chain, image in render._orientation_chains(gray, q, skew_deg, plan["geom"]):
            if chain == target:
                match = image
        if match is None:
            raise ValueError(f"no chain {target!r} for {name} q={q} (variant={variant!r})")
    if mod:
        match = render._OPTICAL_MODULES[mod](match)
    return gray, match


def row_for(case, page, variant, ocr_line, truth, note, plan):
    with extract.open_document(CH / f"data/train/{case}.pdf") as doc:
        src, fixed = restored_image(doc, doc[page], variant, plan)
    return {
        "id": f"{case} p{page}", "variant": variant, "ocr_line": ocr_line,
        "truth": truth, "note": note,
        "src_uri": _uri(src), "fixed_uri": _uri(fixed),
    }


CSS = """
body{background:#0f1416;color:#dde3e2;font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;margin:0;padding:1.5rem}
h1{font-size:1.3rem;margin:0 0 .3rem}.sub{color:#7a8b8d;font:12px ui-monospace,Menlo,monospace;margin:0 0 1.5rem}
.row{border-top:1px solid #263436;padding:1rem 0}
.rowhead{font:13px ui-monospace,Menlo,monospace;color:#dde3e2;margin-bottom:.5rem}
.rid{color:#56b3bd;font-weight:700}.meta{color:#a3b3b4}
.ocrline{color:#e0c060}.truth{color:#5ad17f}.note{color:#f08a5a}
.strip{display:flex;gap:1rem}
figure{margin:0;flex:1 1 50%}
img{width:100%;height:auto;display:block;background:#fff;border:1px solid #263436}
figcaption{font:11px ui-monospace,Menlo,monospace;color:#a3b3b4;margin-top:.3rem}
"""


def _esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_html(rows):
    body = []
    for r in rows:
        body.append(
            f'<div class="row"><div class="rowhead">'
            f'<span class="rid">{_esc(r["id"])}</span> '
            f'<span class="meta">variant={_esc(r["variant"])}</span><br>'
            f'<span class="ocrline">ocr: {_esc(r["ocr_line"])}</span> '
            f'<span class="truth">truth: {_esc(r["truth"])}</span> '
            f'<span class="note">({_esc(r["note"])})</span>'
            f'</div><div class="strip">'
            f'<figure><img src="{r["src_uri"]}"><figcaption>1. raw source frame</figcaption></figure>'
            f'<figure><img src="{r["fixed_uri"]}"><figcaption>2. restored frame fed to OCR ({_esc(r["variant"])})</figcaption></figure>'
            f'</div></div>')
    return (f"<!doctype html><meta charset=utf-8><title>euro-glyph gallery</title>"
            f"<style>{CSS}</style><h1>€-glyph OCR confusion — raw vs restored</h1>"
            f'<p class="sub">8 hard-set cases where a cached read showed a euro-sign glyph near a label line</p>'
            f'{"".join(body)}')


def main(out=None):
    plan = config.grid_plan()
    rows = []
    for case, page, variant, ocr_line, truth, note in CASES:
        print(f"  {case} p{page} {variant}", file=sys.stderr)
        rows.append(row_for(case, page, variant, ocr_line, truth, note, plan))
    out = Path(out or ROOT / "output/viz/euro_glyph_gallery.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(rows))
    print(f"{out}  {out.stat().st_size / 1024:.0f} KB, {len(rows)} rows", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
