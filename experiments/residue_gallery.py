#!/usr/bin/env python3
"""Assemble the read-creation residue crops into a self-contained HTML gallery.

Reads output/viz/residue_manifest.json + the PNG crops on disk, re-encodes them
web-compact (grayscale JPEG, max-width 1400), and writes a single HTML file with
everything inlined. Cards grouped by pool; each shows raw + contrast-stretched
crop, truth vs predicted, the OCR reads at that spot, and a bucket tag.
"""
import base64
import io
import json
import sys
from html import escape
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "output/viz/crops/residue"
MANIFEST = ROOT / "output/viz/residue_manifest.json"
DEST = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "output/viz/residue_gallery.html"

# bucket assignment (WALL = value not truthfully present; TARGET = degraded ink
# present; OPEN = not yet eyeballed, shown for the user's own judgement).
# Only cases actually inspected this session are asserted; rest are OPEN.
BUCKET = {
    "MIB-000067": ("WALL", "form prints [REGISTRY LOST] — value removed"),
    "MIB-000018": ("WALL", "form prints [SPECIES WHITEOUT] — value removed"),
    "MIB-000003": ("WALL", "form reads 'Soltari Veevara' — a decoy; truth is elsewhere"),
    "MIB-000023": ("WALL", "attestation prints [NAME CUT OUT] — value removed"),
    "MIB-000114": ("WALL", "destroyed scan; clearest text is the injection answer-key"),
    "MIB-000008": ("TARGET", "intake ink present but degraded — single-digit misread"),
}


def encode(path, max_w=1400, q=80):
    img = Image.open(path).convert("L")
    if img.width > max_w:
        h = round(img.height * max_w / img.width)
        img = img.resize((max_w, h), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=q, optimize=True)
    return base64.b64encode(buf.getvalue()).decode()


POOL_ORDER = ["closed-vocab", "risk", "scalar"]
POOL_TITLE = {
    "closed-vocab": "Closed-vocab fields (species / home world / purpose)",
    "risk": "Risk flags",
    "scalar": "Identity fields (name / sponsor / date)",
}


def pool_of(tag):
    if tag.startswith("closed-vocab"):
        return "closed-vocab"
    if tag.startswith("flag"):
        return "risk"
    return "scalar"


def main():
    man = json.loads(MANIFEST.read_text())
    groups = {p: [] for p in POOL_ORDER}
    for m in man:
        groups[pool_of(m["pool"])].append(m)

    cards = []
    for pool in POOL_ORDER:
        items = groups[pool]
        if not items:
            continue
        cards.append(f'<h2 class="pool">{escape(POOL_TITLE[pool])}'
                     f'<span class="ct">{len(items)}</span></h2>')
        for m in items:
            case, field = m["case"], m["field"]
            bkt, note = BUCKET.get(case, ("OPEN", "your call — read the pixels"))
            raw = encode(OUT / f"{case}_{field}.png")
            stretch = encode(OUT / f"{case}_{field}_stretch.png")
            mode = "text-layer form" if m["anchor_found"] else "scan · whole page"
            reads = "".join(
                f'<li>{escape(r)}</li>' for r in m["reads"]) or '<li class="none">— no line held the label at this spot —</li>'
            pred = m["predicted"] or "unknown"
            cards.append(f'''
<article class="card b-{bkt.lower()}">
  <header>
    <div class="id">{escape(case)}</div>
    <div class="field">{escape(field)}</div>
    <div class="badges">
      <span class="badge mode">{escape(mode)}</span>
      <span class="badge bkt">{bkt}</span>
    </div>
  </header>
  <div class="vals">
    <div class="v truth"><label>truth</label><code>{escape(m["truth"] or "—")}</code></div>
    <div class="v pred"><label>pipeline</label><code>{escape(pred)}</code></div>
  </div>
  <p class="note">{escape(note)}</p>
  <div class="crops">
    <figure><figcaption>rendered as OCR sees it</figcaption>
      <img loading="lazy" src="data:image/jpeg;base64,{raw}" alt="raw crop {escape(case)}"></figure>
    <figure><figcaption>contrast-stretched — does ink survive?</figcaption>
      <img loading="lazy" src="data:image/jpeg;base64,{stretch}" alt="stretched crop {escape(case)}"></figure>
  </div>
  <details><summary>OCR reads at this spot ({len(m["reads"])})</summary>
    <ul class="reads">{reads}</ul></details>
</article>''')

    html = TEMPLATE.replace("__CARDS__", "\n".join(cards)).replace(
        "__N__", str(len(man)))
    DEST.write_text(html)
    size = len(html) / 1024
    print(f"wrote {DEST} ({size:.0f} KB, {len(man)} cards)")


TEMPLATE = r"""<title>Read-creation residue — MIB extraction frontier</title>
<style>
:root{
  --ground:#0e1116; --panel:#161b22; --panel2:#1b222c; --line:#273040;
  --ink:#e6edf3; --mut:#8b98a8; --faint:#5b6675;
  --paper:#f4f1ea; --paper-ink:#1a1a1a;
  --teal:#4fd1c5; --amber:#e3b341; --red:#e0736e;
  --target:#e3b341; --wall:#e0736e; --open:#6f7b8c;
}
@media (prefers-color-scheme: light){
  :root{ --ground:#eef1f5; --panel:#ffffff; --panel2:#f6f8fb; --line:#d9dfe8;
    --ink:#1a2029; --mut:#5a6572; --faint:#8b95a3; }
}
:root[data-theme="dark"]{ --ground:#0e1116; --panel:#161b22; --panel2:#1b222c;
  --line:#273040; --ink:#e6edf3; --mut:#8b98a8; --faint:#5b6675; }
:root[data-theme="light"]{ --ground:#eef1f5; --panel:#ffffff; --panel2:#f6f8fb;
  --line:#d9dfe8; --ink:#1a2029; --mut:#5a6572; --faint:#8b95a3; }
*{box-sizing:border-box}
body{margin:0;background:var(--ground);color:var(--ink);
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  line-height:1.5;-webkit-font-smoothing:antialiased}
.wrap{max-width:1000px;margin:0 auto;padding:56px 24px 96px}
.mono{font-family:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace}
.eyebrow{font:600 12px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace;
  letter-spacing:.16em;text-transform:uppercase;color:var(--teal);margin:0 0 14px}
h1{font-size:33px;line-height:1.12;margin:0 0 16px;letter-spacing:-.02em;
  text-wrap:balance;font-weight:650}
.lede{font-size:17px;color:var(--mut);max-width:65ch;margin:0 0 34px}
.lede b{color:var(--ink);font-weight:600}
.legend{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:0 0 20px}
.leg{background:var(--panel);border:1px solid var(--line);border-radius:10px;
  padding:14px 16px;border-left-width:3px}
.leg.target{border-left-color:var(--target)}
.leg.wall{border-left-color:var(--wall)}
.leg.open{border-left-color:var(--open)}
.leg h3{margin:0 0 4px;font-size:13px;letter-spacing:.02em;
  font-family:ui-monospace,monospace}
.leg.target h3{color:var(--target)} .leg.wall h3{color:var(--wall)}
.leg.open h3{color:var(--open)}
.leg p{margin:0;font-size:12.5px;color:var(--mut)}
@media (max-width:720px){.legend{grid-template-columns:1fr}}
.pool{font-size:14px;letter-spacing:.04em;text-transform:uppercase;color:var(--mut);
  border-top:1px solid var(--line);padding-top:22px;margin:40px 0 18px;
  font-family:ui-monospace,monospace;display:flex;align-items:center;gap:10px}
.pool .ct{background:var(--panel2);border:1px solid var(--line);border-radius:20px;
  padding:1px 9px;font-size:12px;color:var(--faint)}
.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;
  padding:20px;margin:0 0 18px;border-left:3px solid var(--open)}
.card.b-target{border-left-color:var(--target)}
.card.b-wall{border-left-color:var(--wall)}
.card header{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;margin-bottom:14px}
.card .id{font:600 15px ui-monospace,SFMono-Regular,Menlo,monospace;letter-spacing:.02em}
.card .field{color:var(--teal);font:500 13px ui-monospace,monospace}
.badges{margin-left:auto;display:flex;gap:7px}
.badge{font:600 11px ui-monospace,monospace;letter-spacing:.04em;padding:3px 9px;
  border-radius:20px;border:1px solid var(--line);color:var(--mut);background:var(--panel2)}
.badge.bkt{color:var(--open);border-color:color-mix(in srgb,var(--open) 45%,var(--line))}
.b-target .badge.bkt{color:var(--target);border-color:color-mix(in srgb,var(--target) 50%,transparent)}
.b-wall .badge.bkt{color:var(--wall);border-color:color-mix(in srgb,var(--wall) 50%,transparent)}
.vals{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:12px}
.v{flex:1;min-width:180px;background:var(--panel2);border:1px solid var(--line);
  border-radius:9px;padding:9px 12px}
.v label{display:block;font:600 10px ui-monospace,monospace;letter-spacing:.12em;
  text-transform:uppercase;color:var(--faint);margin-bottom:3px}
.v code{font:500 14px ui-monospace,SFMono-Regular,Menlo,monospace;word-break:break-word}
.v.truth code{color:var(--teal)} .v.pred code{color:var(--amber)}
.note{margin:0 0 16px;font-size:13.5px;color:var(--mut);font-style:italic}
.crops{display:grid;grid-template-columns:1fr;gap:14px}
figure{margin:0}
figcaption{font:600 11px ui-monospace,monospace;letter-spacing:.05em;
  text-transform:uppercase;color:var(--faint);margin-bottom:6px}
.crops img{width:100%;display:block;border-radius:8px;background:var(--paper);
  border:1px solid var(--line)}
details{margin-top:14px;border-top:1px dashed var(--line);padding-top:10px}
summary{cursor:pointer;font:600 12px ui-monospace,monospace;color:var(--mut);
  letter-spacing:.03em}
summary:hover{color:var(--ink)}
ul.reads{margin:10px 0 0;padding-left:0;list-style:none;
  font:13px/1.7 ui-monospace,SFMono-Regular,Menlo,monospace}
ul.reads li{color:var(--ink);padding:2px 0;border-bottom:1px solid var(--panel2);
  word-break:break-word}
ul.reads li.none{color:var(--faint);font-style:italic}
footer{margin-top:48px;padding-top:20px;border-top:1px solid var(--line);
  font-size:12.5px;color:var(--faint)}
</style>
<div class="wrap">
  <p class="eyebrow">Extraction frontier · dev-700 · grid substrate</p>
  <h1>The read-creation residue, under the loupe</h1>
  <p class="lede">TODO&nbsp;1.8 names the remaining extraction mass <b>read-creation</b>:
    pages where no OCR read holds the true value in any form. These are 16 such
    cases, cropped to the field region at print resolution and shown twice — as
    the OCR sees them, and contrast-stretched to reveal whether ink survives.
    The question each crop answers: <b>is the value physically there to be read,
    or was it deliberately removed?</b> That decides whether a trained recognizer
    can touch this residue at all.</p>
  <div class="legend">
    <div class="leg target"><h3>TARGET</h3><p>Ink present but degraded — a
      recognizer specialized to the generator could plausibly recover it.</p></div>
    <div class="leg wall"><h3>WALL</h3><p>Value removed (damage marker, whiteout,
      cut-out), decoyed, or destroyed past the injection. Nothing to recognize.</p></div>
    <div class="leg open"><h3>OPEN</h3><p>Not eyeballed this session — shown for
      your own judgement. Read the pixels; the OCR reads are one panel down.</p></div>
  </div>
  __CARDS__
  <footer>Crops rendered straight from the source PDFs via
    <span class="mono">experiments/residue_crops.py</span>; contrast is PIL
    autocontrast (cutoff 0.5). Dev split only. Truth from the label file; pipeline
    value from the arb / flags oracles on <span class="mono">train_grid.jsonl</span>.</footer>
</div>
"""

if __name__ == "__main__":
    main()
