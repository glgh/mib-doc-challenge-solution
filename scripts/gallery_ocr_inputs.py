#!/usr/bin/env python3
"""A browsable contact sheet of the pixels that actually reach OCR.

The restoration ladder OCRs several variants of a weak page and acts on the
best-scoring one (`mib.stages.render.best`). This renders that winning image for
every scan page in the census sweep — the exact bytes tesseract read — as a
filterable grid, so the corpus can be eyeballed rather than argued about.

It reuses the census scores (`scripts/visualize_restore.py --census`) to pick
each page's winning variant, then renders only that one image. No OCR runs here,
so it is cheap and does not disturb a concurrent dump.

Usage: scripts/gallery_ocr_inputs.py [out.html] [census.jsonl]
"""
import base64
import io
import json
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CH = ROOT.parent / "mib-doc-challenge"

import visualize_restore as vr                # noqa: E402  (sibling script)

THUMB_W = 300          # contact-sheet cell; legible headers, dense grid
VARIANTS = ("base", "skew", "turn1", "turn3", "bands")

# How each variant reads on the grid, and which filter bucket it lands in.
VARIANT_META = {
    "base":  ("upright", "clean", "Read as scanned — no repair needed."),
    "skew":  ("deskewed", "skew", "Tilted; rotated flat before OCR."),
    "turn1": ("turned 90&deg;", "turn", "Stored side-on; quarter-turned back."),
    "turn3": ("turned 270&deg;", "turn", "Stored side-on; quarter-turned back."),
    "bands": ("deskew &rarr; deshred", "shred", "Shredded; deskewed then bands realigned."),
}


def _uri(gray, width=THUMB_W, quality=58):
    img = Image.fromarray(gray)
    if img.width > width:
        img = img.resize((width, max(1, round(img.height * width / img.width))),
                         Image.LANCZOS)
    buf = io.BytesIO()
    img.convert("L").save(buf, format="JPEG", quality=quality, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def winning_variant(row):
    """(variant, score) OCR acted on — highest evidence, ties toward cheaper."""
    scored = [(row[v], -VARIANTS.index(v), v) for v in VARIANTS if v in row]
    scored.append((row["base"], 0, "base"))
    best = max(scored)
    return best[2], best[0]


def render_variant(gray, variant):
    """The exact pixels the winning variant fed to OCR."""
    if variant == "base":
        return gray
    if variant in ("skew", "bands"):
        return vr.restore(gray, variant)
    if variant.startswith("turn"):
        return vr.restore(gray, variant)
    raise ValueError(variant)


def build_cell(row):
    stem, page_no = row["stem"], row["page"]
    variant, score = winning_variant(row)
    _source, gray = vr.source_gray(stem, page_no)
    shown = render_variant(gray, variant)
    if shown is None:                       # restoration not applicable; fall back
        variant, score, shown = "base", row["base"], gray
    label, bucket, _blurb = VARIANT_META[variant]
    rescued = row["base"] == 0 and score > 0
    return {
        "stem": stem, "page": page_no, "variant": variant, "label": label,
        "bucket": "rescued" if rescued else bucket, "raw_bucket": bucket,
        "score": score, "base": row["base"], "rescued": rescued,
        "uri": _uri(shown),
    }


# --- report -----------------------------------------------------------------

BUCKETS = [
    ("all", "All pages"),
    ("rescued", "Rescued from nothing"),
    ("skew", "Deskewed"),
    ("turn", "Quarter-turned"),
    ("shred", "Deshredded"),
    ("clean", "Read as-is"),
    ("mute", "Still silent"),
]


def cell_html(c):
    tags = [c["raw_bucket"]]
    if c["rescued"]:
        tags.append("rescued")
    if c["score"] == 0:
        tags.append("mute")
    delta = (f'<span class="up">0&rarr;{c["score"]}</span>' if c["rescued"]
             else (f'<span class="flat">{c["score"]}</span>' if c["score"] == c["base"]
                   else f'<span class="up">{c["base"]}&rarr;{c["score"]}</span>'))
    return f"""
  <figure class="cell" data-tags="{' '.join(tags)}">
    <img loading="lazy" src="{c['uri']}" alt="{c['stem']} page {c['page']}, {c['variant']}">
    <figcaption>
      <span class="id">{c['stem']}<i>&middot;p{c['page']}</i></span>
      <span class="var v-{c['raw_bucket']}">{c['label']}</span>
      <span class="ev">evidence {delta}</span>
    </figcaption>
  </figure>"""


def report(cells, meta):
    cells = sorted(cells, key=lambda c: (-c["rescued"], c["raw_bucket"], -c["score"]))
    counts = {b: 0 for b, _ in BUCKETS}
    counts["all"] = len(cells)
    for c in cells:
        counts[c["raw_bucket"]] = counts.get(c["raw_bucket"], 0) + 1
        if c["rescued"]:
            counts["rescued"] += 1
        if c["score"] == 0:
            counts["mute"] = counts.get("mute", 0) + 1
    chips = "\n".join(
        f'<button class="chip{" on" if b == "all" else ""}" data-filter="{b}">'
        f'{label} <b>{counts.get(b, 0)}</b></button>'
        for b, label in BUCKETS if counts.get(b, 0) or b == "all")
    return TEMPLATE.replace("{{chips}}", chips)\
                   .replace("{{cells}}", "\n".join(cell_html(c) for c in cells))\
                   .replace("{{meta}}", meta)


TEMPLATE = r"""<title>The images that reach OCR</title>
<style>
:root {
  --ground:#e6eae9; --surface:#f9fbfa; --sunk:#dde3e2;
  --ink:#101819; --ink-2:#3d4c4e; --ink-3:#66787b;
  --rule:#c6d0cf; --accent:#10707b; --accent-soft:#d3e6e7;
  --clean:#66787b; --skew:#3f7c8c; --turn:#8a6d1f; --shred:#9a3946; --rescued:#2b7256;
  --serif:"Iowan Old Style","Palatino Linotype",Palatino,"Book Antiqua",Georgia,serif;
  --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  --mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace;
}
@media (prefers-color-scheme:dark){:root{
  --ground:#0b1113; --surface:#131b1d; --sunk:#0a0f11;
  --ink:#dee6e5; --ink-2:#a3b3b4; --ink-3:#7a8b8d;
  --rule:#263436; --accent:#56b3bd; --accent-soft:#16353a;
  --clean:#7a8b8d; --skew:#6aa7b6; --turn:#c6a34e; --shred:#d8828d; --rescued:#62b691;
}}
:root[data-theme="light"]{--ground:#e6eae9;--surface:#f9fbfa;--sunk:#dde3e2;--ink:#101819;--ink-2:#3d4c4e;--ink-3:#66787b;--rule:#c6d0cf;--accent:#10707b;--accent-soft:#d3e6e7;--clean:#66787b;--skew:#3f7c8c;--turn:#8a6d1f;--shred:#9a3946;--rescued:#2b7256;}
:root[data-theme="dark"]{--ground:#0b1113;--surface:#131b1d;--sunk:#0a0f11;--ink:#dee6e5;--ink-2:#a3b3b4;--ink-3:#7a8b8d;--rule:#263436;--accent:#56b3bd;--accent-soft:#16353a;--clean:#7a8b8d;--skew:#6aa7b6;--turn:#c6a34e;--shred:#d8828d;--rescued:#62b691;}

*{box-sizing:border-box}
body{background:var(--ground);color:var(--ink);font-family:var(--sans);margin:0;
  padding:0 1.25rem 5rem;line-height:1.55}
.wrap{max-width:76rem;margin:0 auto}
h1{font-family:var(--serif);font-weight:600;font-size:clamp(1.9rem,4.5vw,2.9rem);
  letter-spacing:-0.015em;margin:0;text-wrap:balance}
.eyebrow{font-family:var(--mono);font-size:0.7rem;letter-spacing:0.14em;
  text-transform:uppercase;color:var(--accent);margin:0 0 0.35rem}
.lede{font-family:var(--serif);font-size:1.15rem;color:var(--ink-2);max-width:52ch;
  margin:1rem 0 0}
.meta{font-family:var(--mono);font-size:0.7rem;color:var(--ink-3);margin:1.4rem 0 0}
header{padding:3.5rem 0 2rem;border-bottom:1px solid var(--rule)}

.bar{position:sticky;top:0;z-index:5;background:color-mix(in srgb,var(--ground) 88%,transparent);
  backdrop-filter:blur(8px);border-bottom:1px solid var(--rule);
  padding:0.8rem 0;margin:0 -1.25rem 1.5rem;display:flex;flex-wrap:wrap;gap:0.4rem;
  justify-content:center}
.chip{font-family:var(--mono);font-size:0.74rem;color:var(--ink-2);cursor:pointer;
  background:var(--surface);border:1px solid var(--rule);border-radius:999px;
  padding:0.3rem 0.7rem;transition:all .12s}
.chip:hover{border-color:var(--accent)}
.chip.on{background:var(--accent);border-color:var(--accent);color:#fff}
.chip b{font-variant-numeric:tabular-nums;opacity:0.7;margin-left:0.15rem}
.chip.on b{opacity:0.85}

.grid{display:grid;gap:1px;background:var(--rule);border:1px solid var(--rule);
  grid-template-columns:repeat(auto-fill,minmax(200px,1fr))}
.cell{margin:0;background:var(--surface);display:flex;flex-direction:column}
.cell.hide{display:none}
.cell img{width:100%;height:auto;display:block;background:#fff;
  border-bottom:1px solid var(--rule)}
figcaption{padding:0.5rem 0.6rem 0.6rem;display:flex;flex-direction:column;gap:0.2rem}
.id{font-family:var(--mono);font-size:0.74rem;color:var(--ink)}
.id i{color:var(--ink-3);font-style:normal}
.var{font-family:var(--mono);font-size:0.66rem;text-transform:uppercase;
  letter-spacing:0.06em}
.v-clean{color:var(--clean)} .v-skew{color:var(--skew)} .v-turn{color:var(--turn)}
.v-shred{color:var(--shred)}
.ev{font-family:var(--mono);font-size:0.66rem;color:var(--ink-3)}
.ev .up{color:var(--rescued)} .ev .flat{color:var(--ink-3)}

.count{font-family:var(--mono);font-size:0.72rem;color:var(--ink-3);
  text-align:center;margin:1.25rem 0 0}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
</style>
<div class="wrap">
<header>
  <p class="eyebrow">MIB intake &middot; stage S2 &middot; OCR input</p>
  <h1>The images that reach OCR</h1>
  <p class="lede">Every scan-only page in the census sweep, shown as the exact
  pixels the pipeline fed to tesseract &mdash; the winning variant after the
  restoration ladder picked among upright, deskewed, turned, and deshredded
  reads. Filter by what it took to make each page legible.</p>
  <p class="meta">{{meta}}</p>
</header>
</div>
<div class="bar">{{chips}}</div>
<div class="wrap">
<div class="grid">{{cells}}</div>
<p class="count"></p>
</div>
<script>
const chips=[...document.querySelectorAll('.chip')];
const cells=[...document.querySelectorAll('.cell')];
const count=document.querySelector('.count');
function apply(f){
  let n=0;
  for(const c of cells){
    const tags=c.dataset.tags.split(' ');
    const show=f==='all'||tags.includes(f);
    c.classList.toggle('hide',!show);
    if(show)n++;
  }
  count.textContent=n+' page'+(n===1?'':'s')+' shown';
}
for(const chip of chips){
  chip.addEventListener('click',()=>{
    chips.forEach(c=>c.classList.toggle('on',c===chip));
    apply(chip.dataset.filter);
  });
}
apply('all');
</script>
"""


def main(out_path, census_path):
    rows = [json.loads(l) for l in Path(census_path).read_text().splitlines()
            if l.strip()]
    rows = [r for r in rows if "error" not in r]
    if not rows:
        raise SystemExit(f"no usable rows in {census_path} — run "
                         "scripts/visualize_restore.py --census first")
    cells = []
    for i, row in enumerate(rows):
        cells.append(build_cell(row))
        if (i + 1) % 25 == 0:
            print(f"  {i + 1}/{len(rows)}", file=sys.stderr)
    meta = (f"{len(cells)} scan pages &middot; census sweep "
            f"(every {vr.CENSUS_STRIDE}th train packet) &middot; winning OCR "
            f"variant per page &middot; no OCR re-run")
    text = report(cells, meta)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(text)
    print(f"{out_path}  {len(text) / 1024:.0f} KB, {len(cells)} cells",
          file=sys.stderr)


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "output/viz/ocr_inputs.html")
    census = sys.argv[2] if len(sys.argv) > 2 else str(vr.CENSUS_PATH)
    main(out, census)
