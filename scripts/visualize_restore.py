#!/usr/bin/env python3
"""Render what the scan-restoration ladder does to a page, as a standalone report.

`docs/BACKGROUND.md` §4 argues from aggregate numbers that the unreadable scan
pages are geometrically transformed rather than optically degraded. That argument
is hard to check and easy to half-believe without seeing a page. This produces
the picture: for each exemplar, the plate as OCR first sees it beside the plate
after restoration, the detector curve the angle was chosen from, and the two OCR
readings that came out.

It drives the real pipeline code — `mib.imaging` for the transforms,
`mib.stages.render` for the source pixels, the tesseract call and the evidence
score — so a number shown here is the number the pipeline acted on. Nothing here
is imported by the pipeline; this is a dev instrument, not a runtime path.

Usage:
    scripts/visualize_restore.py --census [n_cases]      # measure, ~2 min
    scripts/visualize_restore.py [out.html] [CASE:PAGE:CLASS ...]

CLASS is skew | turn1 | turn3 | bands. With no exemplars given, the defaults
below are used — one page per damage class, picked from the census sweep. The
report renders in seconds and folds in the census if it has been run.
"""
import base64
import html
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
from mib.parse import key_for                 # noqa: E402
from mib.stages import extract, render        # noqa: E402

# One page per damage class, chosen for being unambiguous rather than extreme:
# each is a page whose evidence score the corresponding transform moves on its
# own, so the plate pair shows one effect and not a mixture.
EXEMPLARS = [
    ("MIB-000089", 1, "turn3"),
    ("MIB-000037", 0, "turn1"),
    ("MIB-000221", 0, "skew"),   # pure tilt (+4.5°); after reads clean, not shredded
    ("MIB-000045", 2, "bands"),
]

PLATE_W = 460          # page thumbnails: geometry is the point, not legibility
DETAIL_W = 900         # the strip crop, where legibility is the point
DETAIL_H = 190         # source rows in that crop


# --- pixels ----------------------------------------------------------------


def _uri(gray, width, quality=62):
    img = Image.fromarray(gray)
    if img.width > width:
        img = img.resize((width, max(1, round(img.height * width / img.width))),
                         Image.LANCZOS)
    buf = io.BytesIO()
    img.convert("L").save(buf, format="JPEG", quality=quality, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def _densest_strip(gray, height=DETAIL_H):
    """The horizontal band carrying the most ink — where the text actually is."""
    ink = (gray < imaging.INK).sum(axis=1).astype(np.float64)
    if ink.sum() == 0:
        return gray[:height]
    window = np.convolve(ink, np.ones(min(height, len(ink))), mode="valid")
    top = int(np.argmax(window))
    return gray[top:top + height]


# --- charts ----------------------------------------------------------------


def _polyline(xs, ys, w, h, pad=6):
    xs, ys = np.asarray(xs, float), np.asarray(ys, float)
    xr = np.ptp(xs) or 1.0
    yr = np.ptp(ys) or 1.0
    px = pad + (xs - xs.min()) / xr * (w - 2 * pad)
    py = h - pad - (ys - ys.min()) / yr * (h - 2 * pad)
    return " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(px, py)), px, py


def sweep_chart(gray, chosen, w=520, h=120):
    """The projection-profile sharpness curve, with the argmax the pipeline took."""
    angles, scores = imaging.skew_sweep(gray)
    if scores is None:
        return '<p class="note">too little ink to measure skew</p>'
    points, px, _py = _polyline(angles, scores, w, h)
    i = int(np.argmax(scores))
    zero = px[int(np.argmin(np.abs(angles)))]
    return f"""<svg class="chart" viewBox="0 0 {w} {h}" role="img"
      aria-label="skew sharpness curve, peak at {chosen:+.2f} degrees">
  <line class="axis" x1="{zero:.1f}" y1="4" x2="{zero:.1f}" y2="{h - 4}"></line>
  <polyline class="trace" points="{points}"></polyline>
  <line class="peak" x1="{px[i]:.1f}" y1="2" x2="{px[i]:.1f}" y2="{h - 2}"></line>
</svg>
<p class="cap"><span>&minus;{imaging.MAX_SKEW:g}&deg;</span>
<span>sharpness of the row-ink profile, sheared &mdash; peak at
<b>{chosen:+.2f}&deg;</b></span><span>+{imaging.MAX_SKEW:g}&deg;</span></p>"""


def bands_chart(gray, w=520, h=120):
    """Per-row left-border x: flat means intact, steps mean the page was shredded."""
    offsets = imaging._band_offsets(gray)
    if offsets is None:
        return '<p class="note">no full-width border to key off</p>'
    known = ~np.isnan(offsets)
    filled, last = offsets.copy(), np.nanmedian(offsets)
    for y in range(len(filled)):
        if known[y]:
            last = filled[y]
        filled[y] = last
    points, _px, _py = _polyline(np.arange(len(filled)), filled, w, h)
    ref = np.nanmedian(offsets)
    spread = float(np.nanmax(offsets) - np.nanmin(offsets))
    return f"""<svg class="chart" viewBox="0 0 {w} {h}" role="img"
      aria-label="left border offset per row, spread {spread:.0f} pixels">
  <polyline class="trace" points="{points}"></polyline>
</svg>
<p class="cap"><span>row 0</span><span>left border x per row &mdash; median
<b>{ref:.0f}px</b>, spread <b>{spread:.0f}px</b> across
{int(known.sum())} keyed rows</span><span>row {len(filled)}</span></p>"""


# --- pipeline ---------------------------------------------------------------


def source_gray(stem, page_no):
    """The exact pixels S2 would read for this page."""
    with extract.open_document(CH / f"data/train/{stem}.pdf") as doc:
        with tempfile.TemporaryDirectory(prefix="viz") as tmp:
            name, _encoded, gray = next(render._sources(doc, doc[page_no]))
    return name, gray


def ocr(gray):
    with tempfile.TemporaryDirectory(prefix="viz") as tmp:
        path = Path(tmp) / "p.png"
        path.write_bytes(imaging.to_png_bytes(gray))
        lines = render._tesseract(path)
    return lines, render.page_score(lines)


def restore(gray, damage):
    """Apply one correction rung, matching the grid's in-frame chains
    (render._orientation_chains)."""
    if damage == "skew":
        return imaging.rotate(gray, imaging.skew_angle(gray))
    if damage.startswith("turn"):
        turned = imaging.turn(gray, int(damage[-1]))
        return imaging.rotate(turned, imaging.skew_angle(turned))
    if damage == "bands":
        # Deskew first, then deshred — matches render._orientation_chains.
        angle = imaging.skew_angle(gray)
        base = imaging.rotate(gray, angle) if abs(angle) >= imaging.MIN_SKEW else gray
        return imaging.realign_bands(base)
    raise SystemExit(f"unknown damage class {damage!r}")


# --- composition demonstration ---------------------------------------------
#
# The ladder applies rungs as *alternatives* — best single variant wins — and
# its one combined rung (`restore(., "bands")`) deshreds and only then deskews.
# That order is backwards: `realign_bands` reads the printed border's left edge
# per row, and a skewed border drifts that signal continuously, so the bands are
# measured against a moving reference. Deskewing first makes the border vertical,
# and only then is the per-row left edge a clean read of each band's shift.
#
# The case that needs both is a text line the shredder cuts through its own
# x-height: the glyph tops land in one band and the bottoms in the next, so a
# rotation cannot rejoin them — only pulling the two bands back together can.
# Real pages hide the ground truth, so this is shown as a controlled injury: a
# clean, fully-read page is skewed and shredded with a known recipe, then walked
# back one transform at a time. The point is the capability and the ordering,
# not the size of the effect on any one training page.

DEMO_STEM, DEMO_PAGE = "MIB-000189", 2
DEMO_SKEW = -3.0                       # degrees of synthetic tilt
DEMO_SEAMS = (111, 140, 177, 203, 236)  # row centers of five field lines
DEMO_SHIFTS = (0, 55, -48, 52, -46, 50)  # px each band is slid; one per band


def _shred(gray, seams, shifts):
    """Cut into horizontal bands at `seams` and slide each by `shifts` px."""
    out = gray.copy()
    bounds = [0] + sorted(seams) + [gray.shape[0]]
    for i in range(len(bounds) - 1):
        if shifts[i]:
            out[bounds[i]:bounds[i + 1]] = np.roll(
                gray[bounds[i]:bounds[i + 1]], shifts[i], axis=1)
    return out


DEMO_FIELDS = ("species_code", "home_world", "visa_class", "sponsor_id",
               "arrival_date")


def _demo_step(gray, band_rows, label, note):
    from mib import parse
    lines, score = ocr(gray)
    kv = parse.parse_kv(lines)
    got = {f: kv.get(f, "") for f in DEMO_FIELDS}
    crop = gray[max(0, band_rows[0]):band_rows[1], :]
    return {
        "label": label, "note": note, "score": score,
        "plate": _uri(gray, PLATE_W),
        "strip": _uri(crop, DETAIL_W, 74),
        "fields": got,
    }


def build_demo():
    _source, clean = source_gray(DEMO_STEM, DEMO_PAGE)
    band = (80, 270)                          # the field block, for the zoom strip
    shredded = _shred(clean, DEMO_SEAMS, DEMO_SHIFTS)
    damaged = imaging.rotate(shredded, DEMO_SKEW)
    detected = imaging.skew_angle(damaged)
    deskewed = imaging.rotate(damaged, detected)
    recovered = imaging.realign_bands(deskewed)
    steps = [
        _demo_step(clean, band, "Ground truth",
                   "A page the pipeline already reads cleanly."),
        _demo_step(damaged, band, "Skewed &amp; shredded",
                   f"Tilted {DEMO_SKEW:+g}&deg;, then cut into six bands and slid "
                   "sideways &mdash; a seam through each field line&rsquo;s "
                   "x-height."),
        _demo_step(deskewed, band, "Deskew only",
                   f"Detected {detected:+.2f}&deg; and rotated flat. The lines are "
                   "level again, but the bands are still offset &mdash; the sheared "
                   "values stay broken."),
        _demo_step(recovered if recovered is not None else deskewed, band,
                   "Deskew &rarr; deshred",
                   "With the border now vertical, its left edge per row reads each "
                   "band&rsquo;s shift exactly, and the values snap back together."),
    ]
    return {"detected": detected, "target": DEMO_SKEW, "steps": steps}


def build(stem, page_no, damage):
    source, gray = source_gray(stem, page_no)
    before_lines, before_score = ocr(gray)
    fixed = restore(gray, damage)
    if fixed is None:
        raise SystemExit(f"{stem} p{page_no}: {damage} not applicable")
    after_lines, after_score = ocr(fixed)
    angle = imaging.skew_angle(gray)
    return {
        "stem": stem, "page": page_no, "damage": damage, "source": source,
        "angle": angle, "shape": list(gray.shape),
        "before": {"score": before_score, "lines": before_lines,
                   "plate": _uri(gray, PLATE_W),
                   "detail": _uri(_densest_strip(gray), DETAIL_W, 72)},
        "after": {"score": after_score, "lines": after_lines,
                  "plate": _uri(fixed, PLATE_W),
                  "detail": _uri(_densest_strip(fixed), DETAIL_W, 72)},
        "sweep": sweep_chart(gray, angle),
        "bands": bands_chart(gray) if damage == "bands" else None,
    }


# --- census -----------------------------------------------------------------


CENSUS_PATH = ROOT / "output/viz/census.jsonl"
CENSUS_STRIDE = 4       # every 4th train packet


def census_page(stem):
    """Score one packet's scan pages at each applicable rung. One row per page.

    Gating mirrors the retired `render._restorations`, so the counts describe the ladder as
    it runs rather than an exhaustive sweep: quarter turns are only measured
    where the upright read is already nearly silent, and band realignment only
    where the border gives it something to key off.
    """
    rows = []
    try:
        with extract.open_document(CH / f"data/train/{stem}.pdf") as doc:
            for page in extract.pages(doc):
                if not page.is_scan_only:
                    continue
                with tempfile.TemporaryDirectory(prefix="census") as tmp:
                    src = next(render._sources(doc, doc[page.page_no]), None)
                if src is None:
                    continue
                gray = src[2]
                angle = imaging.skew_angle(gray)
                row = {"stem": stem, "page": page.page_no, "angle": round(angle, 2),
                       "base": ocr(gray)[1]}
                row["skew"] = (ocr(imaging.rotate(gray, angle))[1]
                               if abs(angle) >= imaging.MIN_SKEW else row["base"])
                if row["base"] <= 1:
                    for quarter in (1, 3):
                        row[f"turn{quarter}"] = ocr(restore(gray, f"turn{quarter}"))[1]
                bands = restore(gray, "bands")
                if bands is not None:
                    row["bands"] = ocr(bands)[1]
                rows.append(row)
    except Exception as exc:  # noqa: BLE001
        rows.append({"stem": stem, "error": f"{type(exc).__name__}: {exc}"})
    return rows


def run_census(n_cases, out_path=CENSUS_PATH):
    """~2 minutes of OCR, so it is a separate command: the report reads the file."""
    import multiprocessing

    stems = [p.stem for p in sorted((CH / "data/train").glob("*.pdf"))]
    stems = stems[:n_cases * CENSUS_STRIDE:CENSUS_STRIDE]
    with multiprocessing.Pool(8) as pool:
        rows = [r for batch in pool.imap_unordered(census_page, stems) for r in batch]
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    print(f"{out_path}: {len(rows)} scan pages from {len(stems)} packets",
          file=sys.stderr)


def census(path):
    """Aggregate `run_census`'s rows, if that command has been run."""
    if not Path(path).exists():
        return None
    rows = [json.loads(l) for l in Path(path).read_text().splitlines() if l.strip()]
    rows = [r for r in rows if "error" not in r]
    if not rows:
        return None
    def gained(r, k):
        return k in r and r[k] > r["base"]
    return {
        "pages": len(rows),
        "mute": sum(1 for r in rows if r["base"] == 0),
        "skewed": sum(1 for r in rows if abs(r["angle"]) >= imaging.MIN_SKEW),
        "skew_gain": sum(1 for r in rows if gained(r, "skew")),
        "turn_gain": sum(1 for r in rows
                         if gained(r, "turn1") or gained(r, "turn3")),
        "bands_tried": sum(1 for r in rows if "bands" in r),
        "bands_gain": sum(1 for r in rows if gained(r, "bands")),
        "rescued": sum(1 for r in rows if r["base"] == 0 and max(
            [r.get(k, 0) for k in ("skew", "turn1", "turn3", "bands")]) > 0),
    }


# --- report -----------------------------------------------------------------

CLASS_COPY = {
    "skew": ("Skew", "The page is tilted a few degrees. Tesseract needs baselines "
             "roughly horizontal; a human reads it without noticing."),
    "turn1": ("Quarter turn, 90&deg;", "The whole page is stored side-on. Every "
              "glyph is legible and every line is unreadable."),
    "turn3": ("Quarter turn, 270&deg;", "The whole page is stored side-on the "
              "other way. 180&deg; never wins on this corpus — the generator "
              "does not produce it."),
    "bands": ("Band displacement", "The page was cut into horizontal bands and "
              "each slid sideways. The printed border&rsquo;s left edge, per row, "
              "is literally the displacement."),
}


def _lines_html(lines, limit=9):
    if not lines:
        return '<li class="empty">no text recovered</li>'
    out = []
    for line in lines[:limit]:
        head = line.split(":")[0].split(".")[0].split(";")[0]
        keyed = " keyed" if key_for(head) else ""
        out.append(f'<li class="ocr{keyed}">{html.escape(line)}</li>')
    if len(lines) > limit:
        out.append(f'<li class="more">+{len(lines) - limit} more lines</li>')
    return "\n".join(out)


def _delta(before, after):
    if after > before:
        return f'<span class="delta up">+{after - before}</span>'
    if after < before:
        return f'<span class="delta down">{after - before}</span>'
    return '<span class="delta flat">no change</span>'


def plate_section(d, index):
    title, blurb = CLASS_COPY[d["damage"]]
    charts = d["sweep"] if d["damage"] != "bands" else d["bands"] + d["sweep"]
    return f"""
<section class="plate" id="plate-{index}">
  <header class="plate-head">
    <div>
      <p class="eyebrow">{title}</p>
      <h2>{d['stem']} &middot; page {d['page']}</h2>
      <p class="blurb">{blurb}</p>
    </div>
    <dl class="facts">
      <div><dt>source</dt><dd>{d['source']}</dd></div>
      <div><dt>plate</dt><dd>{d['shape'][1]}&times;{d['shape'][0]}</dd></div>
      <div><dt>measured skew</dt><dd>{d['angle']:+.2f}&deg;</dd></div>
      <div><dt>evidence</dt><dd>{d['before']['score']} &rarr;
        {d['after']['score']} {_delta(d['before']['score'], d['after']['score'])}</dd></div>
    </dl>
  </header>

  <div class="two-up">
    <figure class="pane before">
      <figcaption><span class="tag">as scanned</span>
        <span class="score">evidence {d['before']['score']}</span></figcaption>
      <img src="{d['before']['plate']}" alt="{d['stem']} page {d['page']} before restoration">
    </figure>
    <figure class="pane after">
      <figcaption><span class="tag">after {html.escape(d['damage'])}</span>
        <span class="score">evidence {d['after']['score']}</span></figcaption>
      <img src="{d['after']['plate']}" alt="{d['stem']} page {d['page']} after restoration">
    </figure>
  </div>

  <div class="instrument">{charts}</div>

  <div class="two-up detail">
    <figure class="pane before">
      <figcaption><span class="tag">densest ink band, as scanned</span></figcaption>
      <img src="{d['before']['detail']}" alt="detail crop before restoration">
      <ul class="read">{_lines_html(d['before']['lines'])}</ul>
    </figure>
    <figure class="pane after">
      <figcaption><span class="tag">densest ink band, restored</span></figcaption>
      <img src="{d['after']['detail']}" alt="detail crop after restoration">
      <ul class="read">{_lines_html(d['after']['lines'])}</ul>
    </figure>
  </div>
</section>"""


def census_section(c):
    if not c:
        return ""
    pct = lambda n: f"{100 * n / c['pages']:.0f}%"          # noqa: E731
    return f"""
<section class="census">
  <p class="eyebrow">Sample census</p>
  <h2>How often the page is the problem</h2>
  <p class="blurb">Every {CENSUS_STRIDE}th of the first
  {CENSUS_STRIDE * 64} train packets, {c['pages']} scan-only pages, each read
  upright and again through the rungs that apply to it. A page
  &ldquo;gains&rdquo; when a rung raises its evidence score.</p>
  <div class="stats">
    <div class="stat"><b>{c['mute']}</b><span>read as nothing upright</span>
      <em>{pct(c['mute'])} of scan pages</em></div>
    <div class="stat"><b>{c['rescued']}</b><span>of those recovered by geometry alone</span>
      <em>{100 * c['rescued'] / max(1, c['mute']):.0f}% of the silent pages</em></div>
    <div class="stat"><b>{c['skewed']}</b><span>tilted past the &frac12;&deg; floor</span>
      <em>{c['skew_gain']} gained from deskewing</em></div>
    <div class="stat"><b>{c['turn_gain']}</b><span>rescued by a quarter turn</span>
      <em>tried only where upright scored &le;1</em></div>
    <div class="stat"><b>{c['bands_gain']}</b><span>gained from band realignment</span>
      <em>of {c['bands_tried']} pages with a usable border</em></div>
  </div>
</section>"""


def _field_cell(field, value, truth):
    got = value.strip().rstrip("}").strip()
    ok = got == truth
    state = "ok" if ok else ("part" if got else "miss")
    shown = html.escape(got) if got else "&mdash;"
    return (f'<tr class="{state}"><th>{field.replace("_", " ")}</th>'
            f'<td>{shown}</td></tr>')


DEMO_TRUTH = {
    "species_code": "ARCTURIAN", "home_world": "Proxima-b", "visa_class": "MED-3",
    "sponsor_id": "SPN-5145", "arrival_date": "2026-05-15",
}


def _demo_step_html(step, index):
    rows = "".join(_field_cell(f, step["fields"][f], DEMO_TRUTH[f])
                   for f in DEMO_FIELDS)
    recovered = sum(1 for f in DEMO_FIELDS
                    if step["fields"][f].strip().rstrip("}").strip() == DEMO_TRUTH[f])
    return f"""
  <figure class="step step-{index}">
    <figcaption>
      <span class="tag">{step['label']}</span>
      <span class="score">evidence {step['score']}</span>
    </figcaption>
    <img class="plate-img" src="{step['plate']}" alt="{step['label']} full plate">
    <img class="strip" src="{step['strip']}" alt="{step['label']} field block">
    <p class="note">{step['note']}</p>
    <table class="fields"><tbody>{rows}</tbody>
      <tfoot><tr><th>recovered</th><td>{recovered}/{len(DEMO_FIELDS)} fields</td></tr></tfoot>
    </table>
  </figure>"""


def demo_section(demo):
    if not demo:
        return ""
    steps = "".join(_demo_step_html(s, i) for i, s in enumerate(demo["steps"]))
    return f"""
<section class="demo" id="compose">
  <p class="eyebrow">Composing the ladder</p>
  <h2>Deskew, then deshred</h2>
  <p class="blurb prose">The rungs run as alternatives &mdash; the best single
  variant of a page wins &mdash; so a page that needs <em>two</em> transforms is
  only half-repaired. The case that needs both is a line the shredder cuts
  through its own x-height: rotating levels the line but cannot pull its halves
  back together. Shown as a controlled injury, because a real page hides the
  ground truth &mdash; a clean page skewed {abs(demo['target']):g}&deg; and cut
  into six offset bands, walked back one transform at a time. Deskew detected the
  tilt to {demo['detected']:+.2f}&deg;; only deshred rejoins the values.</p>
  <div class="steps">{steps}</div>
  <p class="colophon prose"><b>Why the order.</b> Deshred keys off the printed
  border&rsquo;s left edge, read per row. A skewed border drifts that reading
  continuously, so bands measured before deskewing are pulled against a moving
  reference. The <code>bands</code> rung now deskews first and deshreds second,
  so the border is vertical and the shift-per-row read exact before any band is
  moved. The older deshred-first order is dropped: where it happened to read
  better, the page&rsquo;s plain deskew variant already covers it, and the
  evidence-max selector keeps that reading.</p>
</section>"""


def report(plates, c, demo=None):
    # Straight substitution rather than str.format: the template carries a
    # stylesheet, and every CSS block would have to be brace-escaped.
    slots = {
        "{{plates}}": "\n".join(plate_section(d, i) for i, d in enumerate(plates)),
        "{{census}}": census_section(c),
        "{{demo}}": demo_section(demo),
        "{{toc}}": "\n".join(
            [f'<li><a href="#plate-{i}"><span>{CLASS_COPY[d["damage"]][0]}</span>'
             f'<b>{d["before"]["score"]} &rarr; {d["after"]["score"]}</b></a></li>'
             for i, d in enumerate(plates)]
            + ([f'<li><a href="#compose"><span>Deskew &rarr; deshred</span>'
                f'<b>compose</b></a></li>'] if demo else [])),
    }
    text = TEMPLATE
    for slot, value in slots.items():
        text = text.replace(slot, value)
    return text


STYLE = """
<style>
:root {
  --ground: #e6eae9; --surface: #f9fbfa; --sunk: #dde3e2;
  --ink: #101819; --ink-2: #3d4c4e; --ink-3: #66787b;
  --rule: #c6d0cf; --accent: #10707b; --accent-soft: #d3e6e7;
  --damaged: #9a3946; --restored: #2b7256;
  --serif: "Iowan Old Style", "Palatino Linotype", Palatino, "Book Antiqua", Georgia, serif;
  --sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  --mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
}
@media (prefers-color-scheme: dark) {
  :root {
    --ground: #0b1113; --surface: #131b1d; --sunk: #0a0f11;
    --ink: #dee6e5; --ink-2: #a3b3b4; --ink-3: #7a8b8d;
    --rule: #263436; --accent: #56b3bd; --accent-soft: #16353a;
    --damaged: #d8828d; --restored: #62b691;
  }
}
:root[data-theme="light"] {
  --ground: #e6eae9; --surface: #f9fbfa; --sunk: #dde3e2;
  --ink: #101819; --ink-2: #3d4c4e; --ink-3: #66787b;
  --rule: #c6d0cf; --accent: #10707b; --accent-soft: #d3e6e7;
  --damaged: #9a3946; --restored: #2b7256;
}
:root[data-theme="dark"] {
  --ground: #0b1113; --surface: #131b1d; --sunk: #0a0f11;
  --ink: #dee6e5; --ink-2: #a3b3b4; --ink-3: #7a8b8d;
  --rule: #263436; --accent: #56b3bd; --accent-soft: #16353a;
  --damaged: #d8828d; --restored: #62b691;
}

body { background: var(--ground); color: var(--ink); font-family: var(--sans);
  line-height: 1.6; margin: 0; padding: 0 1.25rem 5rem; }
.wrap { max-width: 62rem; margin: 0 auto; }
.prose { max-width: 36rem; }
h1, h2, h3 { font-family: var(--serif); font-weight: 600; text-wrap: balance;
  line-height: 1.15; margin: 0; }
h1 { font-size: clamp(2rem, 5vw, 3.1rem); letter-spacing: -0.015em; }
h2 { font-size: 1.55rem; }
p { margin: 0.6rem 0; }
a { color: var(--accent); }
b, strong { font-weight: 600; }
.eyebrow { font-family: var(--mono); font-size: 0.7rem; letter-spacing: 0.14em;
  text-transform: uppercase; color: var(--accent); margin: 0 0 0.35rem; }
.blurb { color: var(--ink-2); max-width: 46ch; }
.note { font-family: var(--mono); font-size: 0.78rem; color: var(--ink-3); }

/* masthead */
.masthead { border-bottom: 1px solid var(--rule); padding: 4rem 0 2.5rem; }
.masthead .lede { font-family: var(--serif); font-size: 1.2rem; color: var(--ink-2);
  max-width: 44ch; margin-top: 1.1rem; }
.masthead .meta { font-family: var(--mono); font-size: 0.72rem; color: var(--ink-3);
  letter-spacing: 0.06em; margin-top: 1.8rem; }

/* ladder */
.ladder { display: grid; gap: 1px; background: var(--rule); border: 1px solid var(--rule);
  grid-template-columns: repeat(auto-fit, minmax(13rem, 1fr)); margin: 2.5rem 0 0; }
.rung { background: var(--surface); padding: 1rem 1.1rem 1.2rem; }
.rung h3 { font-family: var(--mono); font-size: 0.82rem; letter-spacing: 0.04em;
  text-transform: uppercase; }
.rung p { font-size: 0.86rem; color: var(--ink-2); margin: 0.4rem 0 0; }
.rung .gate { font-family: var(--mono); font-size: 0.72rem; color: var(--accent);
  display: block; margin-top: 0.6rem; }

/* contents */
.toc { list-style: none; padding: 0; margin: 2.5rem 0 0; display: grid; gap: 1px;
  background: var(--rule); border: 1px solid var(--rule);
  grid-template-columns: repeat(auto-fit, minmax(11rem, 1fr)); }
.toc a { display: flex; justify-content: space-between; align-items: baseline;
  gap: 0.75rem; background: var(--surface); padding: 0.8rem 1rem;
  text-decoration: none; color: var(--ink); }
.toc a:hover, .toc a:focus-visible { background: var(--accent-soft); }
.toc span { font-size: 0.85rem; }
.toc b { font-family: var(--mono); font-size: 0.85rem; color: var(--accent);
  font-variant-numeric: tabular-nums; }

/* plates */
.plate { border-top: 1px solid var(--rule); padding-top: 2.5rem; margin-top: 3.5rem;
  scroll-margin-top: 1rem; }
.plate-head { display: flex; flex-wrap: wrap; gap: 1.5rem 2.5rem;
  justify-content: space-between; align-items: flex-start; }
.facts { display: grid; grid-template-columns: repeat(2, minmax(7rem, auto));
  gap: 0.5rem 1.75rem; margin: 0; }
.facts dt { font-family: var(--mono); font-size: 0.66rem; letter-spacing: 0.1em;
  text-transform: uppercase; color: var(--ink-3); }
.facts dd { font-family: var(--mono); font-size: 0.9rem; margin: 0;
  font-variant-numeric: tabular-nums; }
.delta { font-size: 0.78rem; }
.delta.up { color: var(--restored); }
.delta.down { color: var(--damaged); }
.delta.flat { color: var(--ink-3); }

.two-up { display: grid; grid-template-columns: 1fr 1fr; gap: 1px;
  background: var(--rule); border: 1px solid var(--rule); margin-top: 1.75rem; }
.pane { background: var(--surface); margin: 0; padding: 0.85rem; display: flex;
  flex-direction: column; gap: 0.7rem; min-width: 0; }
.pane figcaption { display: flex; justify-content: space-between; align-items: baseline;
  gap: 0.5rem; flex-wrap: wrap; }
.tag { font-family: var(--mono); font-size: 0.68rem; letter-spacing: 0.09em;
  text-transform: uppercase; color: var(--ink-3); }
.score { font-family: var(--mono); font-size: 0.72rem; padding: 0.1rem 0.45rem;
  border: 1px solid currentColor; }
.before .score { color: var(--damaged); }
.after .score { color: var(--restored); }
.pane img { width: 100%; height: auto; display: block; background: #fff;
  border: 1px solid var(--rule); }
.detail .pane img { image-rendering: crisp-edges; }

.read { list-style: none; margin: 0; padding: 0.6rem 0 0; border-top: 1px dashed var(--rule);
  font-family: var(--mono); font-size: 0.71rem; line-height: 1.65;
  overflow-x: auto; white-space: nowrap; }
.read .ocr { color: var(--ink-3); }
.read .ocr.keyed { color: var(--ink); }
.read .ocr.keyed::before { content: "\\25B8 "; color: var(--accent); }
.read .empty { color: var(--damaged); font-style: italic; }
.read .more { color: var(--ink-3); font-style: italic; }

.instrument { background: var(--sunk); border: 1px solid var(--rule); border-top: 0;
  padding: 1.1rem 1rem 0.8rem; }
.chart { width: 100%; height: auto; display: block; overflow: visible; }
.chart .trace { fill: none; stroke: var(--accent); stroke-width: 1.6;
  stroke-linejoin: round; }
.chart .axis { stroke: var(--rule); stroke-width: 1; }
.chart .peak { stroke: var(--damaged); stroke-width: 1.2; stroke-dasharray: 3 3; }
.cap { display: flex; justify-content: space-between; gap: 1rem; margin: 0.35rem 0 0.8rem;
  font-family: var(--mono); font-size: 0.68rem; color: var(--ink-3); }
.cap span:nth-child(2) { text-align: center; flex: 1; }
.cap b { color: var(--ink); }

/* census */
.census { border-top: 1px solid var(--rule); padding-top: 2.5rem; margin-top: 3.5rem; }
.stats { display: grid; gap: 1px; background: var(--rule); border: 1px solid var(--rule);
  grid-template-columns: repeat(auto-fit, minmax(11rem, 1fr)); margin-top: 1.5rem; }
.stat { background: var(--surface); padding: 1rem; display: flex; flex-direction: column;
  gap: 0.15rem; }
.stat b { font-family: var(--mono); font-size: 1.7rem; line-height: 1;
  font-variant-numeric: tabular-nums; }
.stat span { font-size: 0.85rem; color: var(--ink-2); }
.stat em { font-family: var(--mono); font-size: 0.68rem; color: var(--ink-3);
  font-style: normal; }

.colophon { border-top: 1px solid var(--rule); margin-top: 3.5rem; padding-top: 1.5rem;
  font-size: 0.85rem; color: var(--ink-2); }
.colophon code { font-family: var(--mono); font-size: 0.8rem; background: var(--sunk);
  padding: 0.1rem 0.3rem; }
.demo .colophon { margin-top: 2rem; }

/* composition demo: four-step walkback */
.demo { border-top: 1px solid var(--rule); padding-top: 2.5rem; margin-top: 3.5rem;
  scroll-margin-top: 1rem; }
.steps { display: grid; gap: 1px; background: var(--rule); border: 1px solid var(--rule);
  grid-template-columns: repeat(4, 1fr); margin-top: 1.75rem; }
.step { background: var(--surface); margin: 0; padding: 0.85rem; display: flex;
  flex-direction: column; gap: 0.6rem; min-width: 0; }
.step figcaption { display: flex; flex-direction: column; gap: 0.25rem; }
.step .plate-img { width: 100%; height: auto; display: block; background: #fff;
  border: 1px solid var(--rule); }
.step .strip { width: 100%; height: auto; display: block; background: #fff;
  border: 1px solid var(--rule); image-rendering: crisp-edges; }
.step .note { font-size: 0.78rem; color: var(--ink-2); margin: 0; }
/* the walkback reads left(worst) -> right(best): tint the endpoints */
.step-1 { box-shadow: inset 3px 0 0 var(--damaged); }
.step-3 { box-shadow: inset 3px 0 0 var(--restored); }
.step-1 .score { color: var(--damaged); }
.step-3 .score { color: var(--restored); }

.fields { width: 100%; border-collapse: collapse; font-family: var(--mono);
  font-size: 0.68rem; margin-top: auto; }
.fields th { text-align: left; font-weight: 400; color: var(--ink-3);
  padding: 0.15rem 0.3rem; white-space: nowrap; }
.fields td { text-align: right; padding: 0.15rem 0.3rem;
  font-variant-numeric: tabular-nums; }
.fields tr.ok td { color: var(--restored); }
.fields tr.miss td { color: var(--damaged); }
.fields tr.part td { color: var(--ink-2); }
.fields tfoot th, .fields tfoot td { border-top: 1px solid var(--rule);
  color: var(--ink); padding-top: 0.3rem; }

@media (max-width: 60rem) { .steps { grid-template-columns: repeat(2, 1fr); } }
@media (max-width: 46rem) {
  .two-up { grid-template-columns: 1fr; }
  .facts { grid-template-columns: repeat(2, minmax(6rem, 1fr)); }
  .steps { grid-template-columns: 1fr; }
}
@media (prefers-reduced-motion: reduce) { * { transition: none !important; } }
</style>
"""

TEMPLATE = """<title>Repairing the plate before OCR</title>
""" + STYLE + """
<div class="wrap">

<header class="masthead">
  <p class="eyebrow">MIB intake &middot; stage S2</p>
  <h1>Repairing the plate before OCR</h1>
  <p class="lede">A quarter of the intake packets carry their evidence only as
  pixels, and a large minority of those pages were not degraded &mdash; they were
  <i>transformed</i>. Nothing was lost, so no amount of resolution helps. Undoing
  the geometry does.</p>
  <p class="meta">Rendered by scripts/visualize_restore.py &middot; transforms from
  mib/imaging.py &middot; evidence scores from mib/stages/render.py &middot;
  tesseract PSM 11 at each rung</p>
</header>

<div class="ladder">
  <div class="rung"><h3>Read it as-is</h3>
    <p>Prefer the embedded raster over a re-render: it is already the source
    image, at 1224&times;1584, and costs nothing to obtain.</p>
    <span class="gate">always</span></div>
  <div class="rung"><h3>Deskew</h3>
    <p>Shear the ink mask across &plusmn;8&deg; in quarter-degree steps and keep
    the angle whose row profile is sharpest. A few milliseconds of numpy.</p>
    <span class="gate">if evidence &lt; 6</span></div>
  <div class="rung"><h3>Quarter turn</h3>
    <p>Try 90&deg; and 270&deg;, deskewing each. 180&deg; is never tried &mdash;
    it did not win on a single surveyed page.</p>
    <span class="gate">if evidence = 0</span></div>
  <div class="rung"><h3>Deskew &rarr; deshred</h3>
    <p>On the deskewed page, read the printed border&rsquo;s left edge per row and
    slide each shredded band back onto the common margin &mdash; deskew first, so
    the border is vertical and the per-row shift reads true.</p>
    <span class="gate">if evidence &lt; 4</span></div>
</div>

<ul class="toc">{{toc}}</ul>

{{demo}}

{{plates}}

{{census}}

<footer class="colophon prose">
  <p><b>Reading the panels.</b> <i>Evidence</i> is the pipeline&rsquo;s own page
  score: recognizable field labels plus well-formed values (case ids, sponsor ids,
  dates, known species and worlds). It is what decides which reading of a page the
  pipeline acts on, and whether to spend another pass. Lines marked &#9656; in the
  OCR panels carry a label the parser recognizes.</p>
  <p><b>Known inefficiency.</b> The ladder is repair-<i>after</i>-failure: OCR,
  look at the score, repair, OCR again. Every repaired page burns a pass that was
  always going to fail. Skew and axis are both measurable in milliseconds without
  OCR, so the flow should be detect-then-repair. Not yet implemented.</p>
  <p><b>Shipped level.</b> The full ladder &mdash; deskew, then quarter-turn, then
  shred-band realignment &mdash; runs on every weak page. It is fixed in code, not
  selectable: the lower rungs existed to A/B the ladder, and that A/B is on the
  record in <code>docs/experiments.md</code>.</p>
</footer>

</div>
"""


def main(out_path, specs):
    print("  composition demo", file=sys.stderr)
    demo = build_demo()
    plates = []
    for spec in specs:
        stem, page_no, damage = spec.split(":")
        print(f"  {stem} p{page_no} {damage}", file=sys.stderr)
        plates.append(build(stem, int(page_no), damage))
    text = report(plates, census(CENSUS_PATH), demo)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(text)
    print(f"{out_path}  {len(text) / 1024:.0f} KB", file=sys.stderr)


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--census":
        run_census(int(args[1]) if len(args) > 1 else 64)
        raise SystemExit(0)
    out = args[0] if args else str(ROOT / "output/viz/restore.html")
    given = args[1:] or [f"{s}:{p}:{d}" for s, p, d in EXEMPLARS]
    main(out, given)
