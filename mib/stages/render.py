"""S2 — OCR for scan-only pages. Mandatory, not a fallback: ~25% of packets
carry their visible content only as pixels.

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
cheaper subset leaves unreadable. The `off`/`skew`/`turn` rungs that used to be
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

from .. import imaging
from ..config import ocr_passes as _ocr_passes
from ..parse import ALL_FLAGS, CASE_ID_RE, DATE_RE, SPONSOR_RE, VISA_CLASSES, key_for
from ..records import Read
from ..vocab import HOME_WORLDS, SPECIES, clean_ocr_line

MIN_EMBEDDED_WIDTH = 1000
RENDER_ZOOM = 2.8       # ~200 DPI

# The evidence score at which a page reads like an intact form. No longer a
# pipeline gate — S2 reads every variant regardless — but it remains the corpus's
# definition of "already good enough", which analysis tooling selects on
# (experiments/mine_hard.py picks hard pages as those scoring below it).
GOOD_ENOUGH = 6


# Page-segmentation modes. PSM 11 (sparse) is the primary the corpus was tuned on;
# PSM 3 (full auto layout) is the dual-pass secondary — see config.ocr_passes.
PRIMARY_PSM = 11
SECONDARY_PSM = 3


def _tesseract(image_path, psm=PRIMARY_PSM):
    try:
        result = subprocess.run(
            ["tesseract", str(image_path), "stdout", "--psm", str(psm)],
            capture_output=True, text=True, timeout=20,
            env={**os.environ, "OMP_THREAD_LIMIT": "1"},
        )
        return [clean_ocr_line(l) for l in result.stdout.splitlines() if l.strip()]
    except (subprocess.TimeoutExpired, OSError):
        return []


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


def _sources(doc, page, tmp):
    """Page pixels to read, as (name, encoded_bytes, grayscale array): embedded
    raster first, then a 200-DPI render. The encoded bytes are kept so the
    unrestored pass reads exactly the original image, not a re-encode of it."""
    images = page.get_images()
    if images:
        img = doc.extract_image(images[0][0])
        if img["width"] >= MIN_EMBEDDED_WIDTH:
            yield "embedded", img["image"], imaging.to_gray(img["image"])
    pix = page.get_pixmap(matrix=fitz.Matrix(RENDER_ZOOM, RENDER_ZOOM))
    path = Path(tmp) / "render.png"
    pix.save(path)
    raw = path.read_bytes()
    yield "render", raw, imaging.to_gray(raw)


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

        def read(encoded, variant):
            nonlocal written
            written += 1
            path = Path(tmp) / f"p{written}.png"
            path.write_bytes(encoded)
            for psm in psms:
                suffix = "" if psm == PRIMARY_PSM else f"+psm{psm}"
                t0 = time.time()
                lines = _tesseract(path, psm)
                reads.append(Read(page_no=page_no, lines=lines, variant=variant + suffix,
                                  quality=evidence_score(lines),
                                  cost_ms=round((time.time() - t0) * 1000)))

        for name, encoded, gray in _sources(doc, page, tmp):
            read(encoded, name)
            for variant, image in _restorations(gray):
                read(imaging.to_png_bytes(image), f"{name}+{variant}")
    return reads


def best(reads):
    """The reading the pipeline acts on: highest evidence score, earliest wins ties.

    Ties break toward the earlier read because `_sources` yields the embedded
    raster before the re-render and restorations run cheapest-first, so the
    earlier read is the one that cost less to obtain.
    """
    chosen = None
    for r in reads:
        if chosen is None or r.quality > chosen.quality:
            chosen = r
    return chosen


def best_lines(reads):
    chosen = best(reads)
    return chosen.lines if chosen else []
