"""S2 — OCR for scan-only pages. Mandatory, not a fallback: ~47% of train pages
(85% of packets have at least one) carry their visible content only as pixels.

Recipe validated on train scans (docs/experiments.md): Tesseract PSM 11
(sparse text) recovers structured Key: Value lines where PSM 4/6 fail; embedded
raster is preferred (already the source image, no re-render cost); pages whose
embedded image is small or yields nothing are re-rendered at ~200 DPI. An
optional PSM 3 (full-layout) second pass per image is available behind
config.ocr_passes=dual, kept only when it out-reads PSM 11 (see `best`).

Pages that still read badly are usually damaged *geometrically* rather than
optically (turned, skewed, or shredded into offset bands), so weak pages are
retried through mib.imaging restorations rather than at higher resolution. The
transforms compose in the order they undo real damage: the `bands` rung deskews
first and then deshreds, because band detection reads the page border and a
skewed border is a moving reference (see `_restorations`).

The ladder is not selectable: every weak page gets deskew, both quarter-turns,
and shred-band realignment, because that full set is what recovers the pages a
cheaper subset leaves unreadable. The `local` rung on top is the text-consent
corrector (imaging.realign_local, graduated 2026-07-26): same walk, but at
seams that cut through text the cut glyph halves override the border's implied
shift; it is emitted only when its pixels differ from the plain deshred. The `off`/`skew`/`turn` rungs that used to be
switchable existed to A/B the ladder (experiments.md rows 11-14) and are gone;
the record stays in the docs and in git.

OCR is exhaustive: every variant is produced and read, and `best()` keeps the
strongest. An earlier design stopped as soon as a reading looked good enough;
that measured −0.21 dev (experiments.md row 16) because it settled for a worse
variant while spending the most OCR on the hardest pages, which never cleared
the bar anyway. The per-case wall-clock bound (`runner.CASE_OCR_BUDGET_S`)
is what keeps this affordable, not skipping work.

`reads_for` returns **every** reading it produced, not just the winner — the
discarded readings survive the seam, which is what an ensemble over variants
needs. Choosing among them is `best()`, deliberately separate.
"""
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

import fitz
import numpy as np
from PIL import Image

from .. import imaging
from ..config import ocr_optical as _ocr_optical
from ..config import ocr_passes as _ocr_passes
from ..parse import ALL_FLAGS, CASE_ID_RE, DATE_RE, SPONSOR_RE, VISA_CLASSES, key_for
from ..records import Read, best_read
from ..vocab import HOME_WORLDS, SPECIES, clean_ocr_line

MIN_EMBEDDED_WIDTH = 1000
RENDER_ZOOM = 2.8       # ~200 DPI floor
# Never render BELOW the page's native scan resolution. On train this is a
# provable no-op (every embedded scan is <=144 DPI: 1224px full pages and 512px
# photo boxes, measured corpus-wide 2026-07-25), but a private-set packet
# scanned at 300 DPI would silently lose resolution to a fixed zoom. Capped at
# ~300 DPI: past that, tesseract gains nothing and render cost grows quadratically.
MAX_RENDER_ZOOM = 4.2   # ~300 DPI cap

# The evidence score at which a page reads like an intact form. No longer a
# pipeline gate — S2 reads every variant regardless — but it remains the corpus's
# definition of "already good enough", which analysis tooling selects on
# (experiments/mine_hard.py picks hard pages as those scoring below it).
GOOD_ENOUGH = 6


# Page-segmentation modes. PSM 11 (sparse) is the primary the corpus was tuned on;
# PSM 3 (full auto layout) is the dual-pass secondary — see config.ocr_passes.
PRIMARY_PSM = 11
SECONDARY_PSM = 3


def _recognize(image_path, psm=PRIMARY_PSM, dpi=None):
    """One tesseract invocation, two renderers -> (lines, conf).

    The `txt` renderer is byte-identical to the old stdout pass (verified live),
    so text behavior cannot drift; the `tsv` renderer rides along in the same
    recognition pass at ~1x cost and yields `conf`: one (mean word conf,
    n_words, y_fraction) triple per tsv text line. The two renderers group
    lines differently (93/111 diverge), so `conf` is a parallel measurement of
    the page, NOT aligned 1:1 with `lines` — page-level metrics need no
    alignment, per-line uses fuzzy-match on demand.
    """
    base = Path(str(image_path) + "_o")
    args = ["tesseract", str(image_path), str(base), "--psm", str(psm)]
    if dpi:
        args += ["--dpi", str(dpi)]
    args += ["txt", "tsv"]
    try:
        subprocess.run(args, capture_output=True, text=True, timeout=20,
                       env={**os.environ, "OMP_THREAD_LIMIT": "1"})
        txt = Path(str(base) + ".txt")
        tsv = Path(str(base) + ".tsv")
        lines = [clean_ocr_line(l)
                 for l in txt.read_text(errors="replace").splitlines() if l.strip()]
        return lines, _parse_tsv(tsv)
    except (subprocess.TimeoutExpired, OSError):
        return [], None


def _parse_tsv(tsv_path):
    """Tesseract tsv -> [(mean word conf, n_words, y_frac, text)] per line, or None.

    Lines whose cleaned joined text is empty are dropped (pure-punctuation
    debris), matching how the text pass drops blank lines. y_frac is the line's
    top edge as a fraction of image height — the positional page-furniture
    guard keys off it (the printed footer band), so it must survive the seam.
    `text` (schema 4) is the line's cleaned words: the tsv renderer's own
    grouping, which is what makes per-line confidence queryable — "how
    confident was the engine about *this* line" needs the line written next to
    its number.
    """
    try:
        rows = tsv_path.read_text(errors="replace").splitlines()
    except OSError:
        return None
    height = 0
    out = []
    cur_key, words, confs, y_top = None, [], [], 0
    def flush():
        text = clean_ocr_line(" ".join(words)) if words else ""
        if text:
            out.append((round(sum(confs) / len(confs), 1), len(confs),
                        round(y_top / height, 4) if height else 0.0, text))
    for row in rows[1:]:
        f = row.split("\t")
        if len(f) < 12:
            continue
        if f[0] == "1":
            height = int(f[9])
        if f[0] != "5" or not f[11].strip() or float(f[10]) < 0:
            continue
        key = (f[1], f[2], f[3], f[4])
        if key != cur_key:
            flush()
            cur_key, words, confs, y_top = key, [], [], int(f[7])
        words.append(f[11])
        confs.append(float(f[10]))
    flush()
    return out


def _tesseract(image_path, psm=PRIMARY_PSM, dpi=None):
    """Text-only compatibility wrapper (tools call this); pipeline uses _recognize."""
    return _recognize(image_path, psm, dpi)[0]


def recognized_keys(lines):
    """How many lines carry a recognizable field label."""
    count = 0
    for line in lines:
        head = line.split(":")[0].split(".")[0].split(";")[0]
        if key_for(head):
            count += 1
    return count


_VALUE_PATTERNS = (CASE_ID_RE, SPONSOR_RE, DATE_RE)
_VALUE_WORDS = tuple(VISA_CLASSES | set(SPECIES) | set(HOME_WORLDS) | ALL_FLAGS)


def evidence_score(lines):
    """Recognizable field labels *plus* well-formed values.

    Labels alone underrate a restored page: the shredder tends to clip the left
    margin, so a rescued line often reads `mnsor ID: SPN-5809` — the label is
    gone but the value that carries the points is intact and exact.
    """
    text = "\n".join(lines)
    values = sum(len(p.findall(text)) for p in _VALUE_PATTERNS)
    values += sum(1 for w in _VALUE_WORDS if re.search(r"\b" + re.escape(w) + r"\b", text))
    return recognized_keys(lines) + values


def _restorations(gray):
    """Geometric variants worth OCR'ing, cheapest and most likely first."""
    angle = imaging.skew_angle(gray)
    upright = imaging.rotate(gray, angle) if abs(angle) >= imaging.MIN_SKEW else None
    if upright is not None:
        yield "skew", upright
    for quarter in (1, 3):                         # 90 and 270 clockwise; 180 never wins
        turned = imaging.turn(gray, quarter)
        yield f"turn{quarter}", imaging.rotate(turned, imaging.skew_angle(turned))
    # Deskew first, then deshred. `realign_bands` reads the printed border's
    # left edge per row; on a skewed page that border is diagonal, so the
    # per-row offset drifts continuously and the bands are measured against a
    # moving reference. Deskewing first makes the border vertical, so the
    # per-row left edge is a clean read of each band's true shift — and the
    # deskewed base needs no further rotation. Reuses `upright` from the skew
    # rung above; when the page wasn't meaningfully tilted (`upright is
    # None`), deshredding `gray` directly is correct.
    base = upright if upright is not None else gray
    deshredded = imaging.realign_bands(base)
    if deshredded is not None:
        yield "deshred", deshredded
    # The text-consent corrector (imaging.realign_local): border offsets from
    # the merged reader, overridden at text-cutting seams by what the cut glyph
    # halves themselves want. Emitted only when it produces a genuinely new
    # image — equal to the plain deshred (or a no-op) it would just double the
    # OCR bill for the same pixels.
    corrected = imaging.realign_local(base)
    if corrected is not None and (deshredded is None
                                  or not np.array_equal(corrected, deshredded)):
        yield "local", corrected


def _optical_restorations(gray):
    """Optical variants (behind config.ocr_optical): local-adaptive threshold +
    autocontrast, which recover faint/unevenly-lit ink that reads as blank and
    that a global binarization erases. Emitted by `reads_for` ONLY when the
    geometric ensemble read the page below GOOD_ENOUGH — the unguarded A/B showed
    a well-formed-but-wrong binarized read can outscore and displace a correct
    reading on a page that already reads well (dev: 11 fields recovered, 10
    corrupted). Gating on weak geometric evidence keeps the rescues on dead pages
    and cannot touch healthy ones."""
    yield "adapt", imaging.local_threshold(gray)
    yield "autocon", imaging.autocontrast(gray)


def _sources(doc, page, tmp):
    """Page pixels to read, as (name, encoded_bytes, grayscale array): embedded
    raster first, then a full-page render at >=200 DPI — raised to the native
    resolution of the page's largest embedded image (MAX_RENDER_ZOOM cap) so a
    high-DPI scan is never downsampled by the fixed floor. The encoded bytes are
    kept so the unrestored pass reads exactly the original image, not a
    re-encode of it."""
    images = page.get_images()
    if images:
        img = doc.extract_image(images[0][0])
        if img["width"] >= MIN_EMBEDDED_WIDTH:
            yield "embedded", img["image"], imaging.to_gray(img["image"])
    # get_images tuples carry (xref, smask, width, ...) — no decode needed.
    native_px = max((im[2] for im in images), default=0)
    native_zoom = native_px / max(1.0, page.rect.width)
    zoom = max(RENDER_ZOOM, min(native_zoom, MAX_RENDER_ZOOM))
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
    # Straight from the pixmap samples — the old path went pixmap -> PNG ->
    # PIL -> array, paying a deflate encode + decode for identical pixels
    # (PNG is lossless). Tesseract gets the same RGB samples as PNM (P6).
    rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    gray = np.asarray(Image.fromarray(rgb).convert("L"))
    yield "render", imaging.to_pnm_bytes(rgb), gray


def reads_for(doc, page, page_no):
    """Every OCR reading of one page, in generation order (cheapest first).

    A 300-DPI grayscale/autocontrast retry was tried here and reverted: +0.21
    dev pts for 43x runtime (experiments.md row 8). The pages it targeted were
    turned or shredded, not low-resolution — hence the geometric path instead.
    """
    # Which PSM passes to OCR each image with. `dual` adds PSM 3 per image and
    # keeps the stronger via best(); an intact page still reads at PSM 11, so the
    # second pass is pure upside on the dense forms PSM 11 fragments.
    psms = (PRIMARY_PSM, SECONDARY_PSM) if _ocr_passes() == "dual" else (PRIMARY_PSM,)
    reads = []
    with tempfile.TemporaryDirectory(prefix="mibocr") as tmp:
        written = 0

        def read(encoded, variant, dpi=None):
            nonlocal written
            written += 1
            path = Path(tmp) / f"p{written}.png"
            path.write_bytes(encoded)
            for psm in psms:
                suffix = "" if psm == PRIMARY_PSM else f"+psm{psm}"
                t0 = time.time()
                lines, conf = _recognize(path, psm, dpi)
                reads.append(Read(page_no=page_no, lines=lines, variant=variant + suffix,
                                  quality=evidence_score(lines), conf=conf,
                                  cost_ms=round((time.time() - t0) * 1000)))

        sources = list(_sources(doc, page, tmp))
        for name, encoded, gray in sources:
            # The old render PNGs carried pymupdf's default 96-DPI pHYs chunk
            # and tesseract's segmentation was tuned with that (wrong but
            # load-bearing) value; PNM has no metadata, so the render base
            # declares it explicitly. Restorations and embedded originals never
            # had DPI metadata — no flag, tesseract estimates as before.
            read(encoded, name, dpi=96 if name == "render" else None)
            for variant, image in _restorations(gray):
                read(imaging.to_pnm_bytes(image), f"{name}+{variant}")
        # Optical rung, gated: only when the geometric ensemble read this page
        # weakly (below the intact-form bar) does a binarized/contrast pass earn a
        # place, so it cannot outscore a page that already reads well. See
        # `_optical_restorations` for the A/B evidence behind the guard.
        if _ocr_optical() and (not reads or max(r.quality for r in reads) < GOOD_ENOUGH):
            for name, encoded, gray in sources:
                for variant, image in _optical_restorations(gray):
                    read(imaging.to_pnm_bytes(image), f"{name}+{variant}")
    return reads


def best(reads):
    """The strongest reading (records.best_read); kept as S2's public name."""
    return best_read(reads)


def best_lines(reads):
    chosen = best(reads)
    return chosen.lines if chosen else []
