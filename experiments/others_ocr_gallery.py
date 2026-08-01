#!/usr/bin/env python3
"""Eyeball the OCR headroom on OTHER-typed scan pages.

`detect_doc_type` returns DOC_OTHER for any scan page whose header the OCR could
not resolve to one of the six known forms. Some of those are genuinely non-forms
(decoys, watermarks); some are real forms whose title garbled — and the second
kind is exactly where OCR quality still leaves points on the floor. This tool
samples a handful of them and lays out, per page, everything needed to judge by
eye whether a better read was available:

  * the INPUT pixels (the raw page render OCR started from) beside the
    REPAIRED pixels (the winning restoration variant OCR actually read),
  * the text each stage produced — S1 text layer, every S2 OCR variant with its
    confidence, and the final merged fields the pipeline emitted,
  * the ground-truth labels for the case.

Images are reconstructed faithfully by reusing the render stage's own
`_sources` / `_orientation_chains` / optical modules, so the REPAIRED panel is
the same pixels tesseract saw. Text comes from the frozen page-text cache, so no
OCR runs here — it is cheap and re-runnable.

Run it again for a fresh sample (the seed defaults to random); the output is a
self-contained HTML fragment sized for the Artifact viewer.

Usage:
  experiments/others_ocr_gallery.py [--n 5] [--seed S] [--case MIB-000123 ...]
                                    [--cache PATH] [--out PATH] [--include-text]
"""
import argparse
import base64
import io
import os
import random
import sys
from pathlib import Path

import fitz
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CH = ROOT.parent / "mib-doc-challenge"

from mib import cache, config, imaging, parse, runner          # noqa: E402
from mib.records import best_read, conf_excess_mass            # noqa: E402
from mib.stages import render                                 # noqa: E402

DEFAULT_CACHE = ROOT / "output/cache/train_grid.jsonl"
TRAIN_PDFS = CH / "data/train"
LABELS_CSV = CH / "data/train_labels.csv"
GT_FIELDS = ["applicant_name", "species_code", "home_world", "visa_class",
             "sponsor_id", "arrival_date", "declared_purpose", "risk_flags",
             "fee_status", "adjudication"]
EMIT_FIELDS = GT_FIELDS + ["confidence"]
IMG_W = 460          # inline panel width; keeps the fragment under a few MB


# --- selection --------------------------------------------------------------

def page_lines(page, reads):
    """The lines detect_doc_type sees: the best OCR read for a scan, else the
    visible text layer. Mirrors how packet.assemble types a page."""
    if reads:
        chosen = best_read(reads)
        return chosen.lines if chosen else []
    return page.visible_lines


def candidate_pages(records, include_text):
    """(stem, page_no) for every page that types OTHER. Scan pages only unless
    --include-text, since OCR headroom is a question about scanned pixels."""
    out = []
    for rec in records:
        if rec.get("error"):
            continue
        pages, reads_by_page = cache.to_case(rec["pages"])
        for p in pages:
            reads = reads_by_page.get(p.page_no)
            if not reads and not include_text:
                continue
            if parse.detect_doc_type(page_lines(p, reads)) == parse.DOC_OTHER:
                out.append((rec["stem"], p.page_no))
    return out


# --- faithful image reconstruction ------------------------------------------

def frame_images(doc, page):
    """variant name -> grayscale array, reproducing render.reads_for's image
    generation (raw sources + every orientation chain + optical modules over
    the corrected frames). No OCR; we only ever look up the winning variant, so
    generating the full grid unconditionally (no weak-gate, no dedupe) is safe —
    a name maps to exactly one deterministic transform."""
    plan = config.grid_plan()
    geom, opt = plan["geom"], plan["opt"]
    frames, sources = {}, []
    # _sources takes a tmp dir only to satisfy its signature; nothing is written.
    for name, _enc, gray in render._sources(doc, page):
        sources.append((name, gray))
    oprofs = {name: imaging.orientation_profile(gray) for name, gray in sources}
    for name, gray in sources:
        frames[name] = gray
        finals = {0: ((), gray)}
        for q in (0, 1, 3):
            for chain, image in render._orientation_chains(
                    gray, q, oprofs[name][q]["skew_deg"], geom):
                finals[q] = (chain, image)
                frames["+".join((name,) + chain)] = image
        if opt:
            bases = [((), gray)]
            if plan["opt_base"] == "frames":
                bases += [finals[q] for q in (1, 3) if q in finals and finals[q][0]]
            for chain, image in bases:
                for mod in opt:
                    frames["+".join((name,) + chain + (mod,))] = \
                        render._OPTICAL_MODULES[mod](image)
    return frames


def strip_psm(variant):
    """'render+turn1+psm3' -> 'render+turn1'. The layout pass re-reads the same
    frame at a different PSM, so its image is the un-suffixed variant's."""
    return variant.split("+psm", 1)[0]


def _uri(gray, width=IMG_W, quality=62):
    img = Image.fromarray(np.asarray(gray)).convert("L")
    if img.width > width:
        img = img.resize((width, max(1, round(img.height * width / img.width))),
                         Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


# --- per-page assembly ------------------------------------------------------

def chain_label(variant):
    """Human phrasing of a variant's restoration chain."""
    parts = strip_psm(variant).split("+")
    seg = {"skew": "deskewed", "turn1": "turned 90&deg;", "turn3": "turned 270&deg;",
           "deshred": "deshredded", "local": "line-realigned",
           "adapt": "adaptive-threshold", "autocon": "autocontrast"}
    steps = [seg[p] for p in parts[1:] if p in seg]
    if variant != strip_psm(variant):
        steps.append("PSM-3 layout pass")
    return " &rarr; ".join(steps) if steps else "read as scanned"


def build_page(stem, page_no, rec_by_stem, labels):
    rec = rec_by_stem[stem]
    pages, reads_by_page = cache.to_case(rec["pages"])
    page = next(p for p in pages if p.page_no == page_no)
    reads = reads_by_page.get(page_no) or []
    winner = best_read(reads)
    win_variant = winner.variant if winner else ""
    win_mass = conf_excess_mass(winner) if winner else None

    pdf = TRAIN_PDFS / f"{stem}.pdf"
    input_uri = repaired_uri = None
    note = ""
    with fitz.open(pdf) as doc:
        frames = frame_images(doc, doc[page_no])
        base = strip_psm(win_variant).split("+", 1)[0] if win_variant else ""
        if base in frames:
            input_uri = _uri(frames[base])
        stripped = strip_psm(win_variant)
        if stripped in frames:
            repaired_uri = _uri(frames[stripped])
        else:
            note = f"could not reconstruct variant '{win_variant}'"
            repaired_uri = input_uri
    repaired_is_input = strip_psm(win_variant) == base

    # Final merged fields the pipeline emits for the whole case.
    record, _debug = runner.predict_from_evidence(pages, reads_by_page, stem)

    # S2 reads, strongest first by the pipeline's own selection metric
    # (guarded excess confidence mass), deduped on (variant, text) for reading.
    seen, read_rows = set(), []
    for r in sorted(reads, key=lambda r: -(conf_excess_mass(r) or 0.0)):
        text = "\n".join(r.lines).strip()
        key = (r.variant, text)
        if key in seen:
            continue
        seen.add(key)
        read_rows.append({
            "variant": r.variant, "mass": conf_excess_mass(r),
            "is_winner": r is winner, "text": text,
        })

    return {
        "stem": stem, "page_no": page_no,
        "input_uri": input_uri, "repaired_uri": repaired_uri,
        "repaired_is_input": repaired_is_input, "note": note,
        "win_variant": win_variant, "win_label": chain_label(win_variant),
        "win_mass": win_mass,
        "s1_lines": page.visible_lines, "hidden": page.hidden_lines,
        "reads": read_rows,
        "final": {f: record.get(f) for f in EMIT_FIELDS},
        "gt": labels.get(stem),
    }


# --- rendering --------------------------------------------------------------

def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            if s is not None else "")


def text_block(lines):
    if not lines:
        return '<p class="empty">&mdash; nothing read &mdash;</p>'
    return '<pre>' + esc("\n".join(lines)) + '</pre>'


def read_html(r):
    cls = "read winner" if r["is_winner"] else "read"
    mass = f'{r["mass"]:.0f}' if r["mass"] is not None else "&mdash;"
    tag = '<span class="pill">winner</span>' if r["is_winner"] else ""
    body = f'<pre>{esc(r["text"])}</pre>' if r["text"] else \
        '<p class="empty">&mdash; nothing read &mdash;</p>'
    return f"""      <details class="{cls}"{' open' if r['is_winner'] else ''}>
        <summary><code>{esc(r['variant'])}</code>{tag}
          <span class="conf">mass {mass}</span></summary>
        {body}
      </details>"""


def fields_table(final, gt):
    rows = []
    for f in EMIT_FIELDS:
        got = final.get(f)
        truth = gt.get(f) if (gt and f in gt) else ("&mdash;" if f == "confidence" else None)
        match = ""
        if gt and f in gt and f != "confidence":
            match = "hit" if str(got) == str(gt.get(f)) else "miss"
        rows.append(
            f'<tr class="{match}"><th>{f}</th>'
            f'<td>{esc(got)}</td><td>{esc(truth) if truth is not None else ""}</td></tr>')
    return ('<table class="fields"><thead><tr><th></th><th>pipeline</th>'
            '<th>ground truth</th></tr></thead><tbody>'
            + "".join(rows) + "</tbody></table>")


def card_html(pg):
    if pg["repaired_is_input"]:
        rep_head = 'REPAIRED <span class="var noop">no repair &mdash; read as scanned</span>'
    else:
        rep_head = f'REPAIRED <span class="var">{pg["win_label"]}</span>'
    note = f'<p class="note">{esc(pg["note"])}</p>' if pg["note"] else ""
    hidden = ""
    if pg["hidden"]:
        hidden = (f'<details class="hidden-layer"><summary>hidden / off-crop text '
                  f'({len(pg["hidden"])} line(s)) &mdash; quarantined, never trusted</summary>'
                  f'<pre>{esc(chr(10).join(pg["hidden"]))}</pre></details>')
    gt_adj = pg["gt"]["adjudication"] if pg["gt"] else "?"
    return f"""
  <article class="card">
    <header class="card-head">
      <span class="id">{pg['stem']}<i>&middot;p{pg['page_no']}</i></span>
      <span class="type">typed OTHER</span>
      <span class="adj adj-{gt_adj}">{gt_adj}</span>
    </header>

    <div class="views">
      <figure><figcaption>INPUT <span class="var">raw render</span></figcaption>
        <img loading="lazy" src="{pg['input_uri']}" alt="input pixels"></figure>
      <figure><figcaption>{rep_head}</figcaption>
        <img loading="lazy" src="{pg['repaired_uri']}" alt="repaired pixels"></figure>
    </div>
    {note}

    <div class="stages">
      <section class="stage">
        <h3>S1 &middot; text layer</h3>
        {text_block(pg['s1_lines'])}
        {hidden}
      </section>
      <section class="stage">
        <h3>S2 &middot; OCR variants <span class="count">{len(pg['reads'])}</span></h3>
        <div class="reads">
{chr(10).join(read_html(r) for r in pg['reads'])}
        </div>
      </section>
      <section class="stage">
        <h3>Final &middot; merged fields vs ground truth</h3>
        {fields_table(pg['final'], pg['gt'])}
      </section>
    </div>
  </article>"""


def report(cards, meta):
    return TEMPLATE.replace("{{cards}}", "\n".join(cards)).replace("{{meta}}", meta)


TEMPLATE = r"""<title>OTHER-typed pages: OCR headroom</title>
<style>
:root{
  --ground:#e7ebea;--surface:#f9fbfa;--sunk:#dde3e2;--raise:#fff;
  --ink:#101819;--ink-2:#3d4c4e;--ink-3:#66787b;
  --rule:#c6d0cf;--accent:#10707b;--accent-soft:#d3e6e7;
  --hit:#2b7256;--miss:#9a3946;--win:#8a6d1f;
  --serif:"Iowan Old Style","Palatino Linotype",Palatino,"Book Antiqua",Georgia,serif;
  --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  --mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace;
}
@media (prefers-color-scheme:dark){:root{
  --ground:#0b1113;--surface:#131b1d;--sunk:#0a0f11;--raise:#1a2426;
  --ink:#dee6e5;--ink-2:#a3b3b4;--ink-3:#7a8b8d;
  --rule:#263436;--accent:#56b3bd;--accent-soft:#16353a;
  --hit:#62b691;--miss:#d8828d;--win:#c6a34e;
}}
:root[data-theme="light"]{--ground:#e7ebea;--surface:#f9fbfa;--sunk:#dde3e2;--raise:#fff;--ink:#101819;--ink-2:#3d4c4e;--ink-3:#66787b;--rule:#c6d0cf;--accent:#10707b;--accent-soft:#d3e6e7;--hit:#2b7256;--miss:#9a3946;--win:#8a6d1f;}
:root[data-theme="dark"]{--ground:#0b1113;--surface:#131b1d;--sunk:#0a0f11;--raise:#1a2426;--ink:#dee6e5;--ink-2:#a3b3b4;--ink-3:#7a8b8d;--rule:#263436;--accent:#56b3bd;--accent-soft:#16353a;--hit:#62b691;--miss:#d8828d;--win:#c6a34e;}

*{box-sizing:border-box}
body{background:var(--ground);color:var(--ink);font-family:var(--sans);margin:0;
  padding:0 1.25rem 5rem;line-height:1.55}
.wrap{max-width:74rem;margin:0 auto}
header.top{padding:3rem 0 1.6rem;border-bottom:1px solid var(--rule)}
.eyebrow{font-family:var(--mono);font-size:0.7rem;letter-spacing:0.14em;
  text-transform:uppercase;color:var(--accent);margin:0 0 0.45rem}
h1{font-family:var(--serif);font-weight:600;font-size:clamp(1.8rem,4vw,2.6rem);
  letter-spacing:-0.015em;margin:0;text-wrap:balance}
.lede{font-family:var(--serif);font-size:1.1rem;color:var(--ink-2);max-width:60ch;margin:0.9rem 0 0}
.meta{font-family:var(--mono);font-size:0.68rem;color:var(--ink-3);margin:1.3rem 0 0}

.card{background:var(--surface);border:1px solid var(--rule);border-radius:10px;
  margin:1.6rem 0;overflow:hidden}
.card-head{display:flex;align-items:center;gap:0.7rem;flex-wrap:wrap;
  padding:0.8rem 1.1rem;border-bottom:1px solid var(--rule);background:var(--sunk)}
.id{font-family:var(--mono);font-size:0.95rem;font-weight:600}
.id i{color:var(--ink-3);font-style:normal;font-weight:400}
.type{font-family:var(--mono);font-size:0.66rem;text-transform:uppercase;
  letter-spacing:0.08em;color:var(--ink-3);border:1px solid var(--rule);
  border-radius:999px;padding:0.1rem 0.55rem}
.adj{margin-left:auto;font-family:var(--mono);font-size:0.72rem;font-weight:600;
  letter-spacing:0.04em;padding:0.18rem 0.6rem;border-radius:999px}
.adj-APPROVED{background:var(--accent-soft);color:var(--hit)}
.adj-DENIED{background:var(--accent-soft);color:var(--miss)}
.adj-NEEDS_REVIEW{background:var(--accent-soft);color:var(--win)}

.views{display:grid;grid-template-columns:1fr 1fr;gap:1px;background:var(--rule)}
.views figure{margin:0;background:var(--surface);padding:0.7rem}
.views figcaption{font-family:var(--mono);font-size:0.68rem;letter-spacing:0.06em;
  color:var(--ink-2);margin-bottom:0.5rem;display:flex;flex-wrap:wrap;gap:0.4rem;align-items:baseline}
.var{color:var(--accent);text-transform:none;letter-spacing:0}
.var.noop{color:var(--ink-3)}
.views img{width:100%;height:auto;display:block;background:#fff;border:1px solid var(--rule)}
.note{font-family:var(--mono);font-size:0.7rem;color:var(--miss);margin:0.4rem 1.1rem}

.stages{display:grid;grid-template-columns:1fr 1fr;gap:1px;background:var(--rule)}
.stage{background:var(--surface);padding:0.9rem 1.1rem}
.stage:last-child{grid-column:1 / -1}
.stage h3{font-family:var(--mono);font-size:0.72rem;text-transform:uppercase;
  letter-spacing:0.08em;color:var(--ink-2);margin:0 0 0.6rem;font-weight:600}
.stage h3 .count{color:var(--ink-3)}
pre{font-family:var(--mono);font-size:0.74rem;line-height:1.5;white-space:pre-wrap;
  word-break:break-word;margin:0;color:var(--ink);background:var(--sunk);
  border:1px solid var(--rule);border-radius:6px;padding:0.55rem 0.7rem;max-height:22rem;overflow:auto}
.empty{font-family:var(--mono);font-size:0.72rem;color:var(--ink-3);font-style:italic;margin:0.2rem 0}

.reads{display:flex;flex-direction:column;gap:0.4rem;max-height:30rem;overflow:auto}
.read{border:1px solid var(--rule);border-radius:6px;background:var(--raise)}
.read.winner{border-color:var(--win)}
.read summary{cursor:pointer;list-style:none;display:flex;align-items:center;gap:0.5rem;
  padding:0.4rem 0.6rem;font-family:var(--mono);font-size:0.72rem}
.read summary::-webkit-details-marker{display:none}
.read summary code{color:var(--accent);word-break:break-all}
.read .conf{margin-left:auto;color:var(--ink-3)}
.read pre{margin:0 0.5rem 0.5rem;max-height:16rem}
.pill{font-size:0.6rem;text-transform:uppercase;letter-spacing:0.08em;color:var(--win);
  border:1px solid var(--win);border-radius:999px;padding:0.02rem 0.4rem}
.hidden-layer{margin-top:0.6rem}
.hidden-layer summary{cursor:pointer;font-family:var(--mono);font-size:0.68rem;color:var(--ink-3)}
.hidden-layer pre{margin-top:0.4rem;color:var(--ink-3)}

table.fields{width:100%;border-collapse:collapse;font-size:0.8rem;
  font-variant-numeric:tabular-nums}
table.fields th,table.fields td{text-align:left;padding:0.3rem 0.6rem;
  border-bottom:1px solid var(--rule);vertical-align:top}
table.fields thead th{font-family:var(--mono);font-size:0.66rem;text-transform:uppercase;
  letter-spacing:0.06em;color:var(--ink-3);font-weight:600}
table.fields tbody th{font-family:var(--mono);font-size:0.72rem;font-weight:500;
  color:var(--ink-2);white-space:nowrap}
table.fields td{font-family:var(--mono);font-size:0.74rem;word-break:break-word}
tr.hit td:first-of-type{color:var(--hit)}
tr.miss td:first-of-type{color:var(--miss)}
tr.miss{background:color-mix(in srgb,var(--miss) 8%,transparent)}

@media (max-width:640px){.views,.stages{grid-template-columns:1fr}.stage:last-child{grid-column:auto}}
</style>
<div class="wrap">
<header class="top">
  <p class="eyebrow">OCR quality &middot; inspection</p>
  <h1>Pages the classifier couldn&rsquo;t place</h1>
  <p class="lede">Each card is one scan page that <code>detect_doc_type</code> resolved to
  OTHER &mdash; a header the OCR never recovered. Compare the raw pixels against the repaired
  variant the pipeline read, then read across the stages to judge whether a better read was
  on the table.</p>
  <p class="meta">{{meta}}</p>
</header>
{{cards}}
</div>"""


# --- main -------------------------------------------------------------------

def load_labels():
    import csv
    with open(LABELS_CSV) as f:
        return {row["case_id"]: row for row in csv.DictReader(f)}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--n", type=int, default=5, help="pages to sample (default 5)")
    ap.add_argument("--seed", type=int, default=None,
                    help="sampling seed (default: random each run)")
    ap.add_argument("--case", action="append", default=[],
                    help="pin a case id (repeatable); its OTHER pages are used")
    ap.add_argument("--cache", default=str(DEFAULT_CACHE))
    ap.add_argument("--include-text", action="store_true",
                    help="also allow text-layer (non-scan) OTHER pages")
    ap.add_argument("--out", default=str(
        ROOT / "experiments/probes/others_ocr_gallery.html"))
    a = ap.parse_args()

    seed = a.seed if a.seed is not None else int.from_bytes(os.urandom(4), "big")
    rng = random.Random(seed)

    meta, records = cache.read(a.cache)
    rec_by_stem = {r["stem"]: r for r in records}
    labels = load_labels()

    cands = candidate_pages(records, a.include_text)
    if a.case:
        pins = set(a.case)
        chosen = [c for c in cands if c[0] in pins]
        if not chosen:
            print(f"no OTHER pages found for {sorted(pins)}", file=sys.stderr)
            return 1
    else:
        rng.shuffle(cands)
        chosen = cands[:a.n]

    print(f"seed={seed}  candidates={len(cands)}  sampling {len(chosen)}:", file=sys.stderr)
    cards = []
    for stem, page_no in chosen:
        print(f"  {stem} p{page_no}", file=sys.stderr)
        pg = build_page(stem, page_no, rec_by_stem, labels)
        cards.append(card_html(pg))

    cache_rev = (meta or {}).get("git_rev", "?")
    meta_line = (f"seed {seed} &middot; {len(chosen)} of {len(cands)} OTHER pages &middot; "
                 f"cache {Path(a.cache).name} @ {cache_rev} &middot; "
                 f"grid {config.grid_plan()['name']}")
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report(cards, meta_line))
    print(f"wrote {out}", file=sys.stderr)
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
