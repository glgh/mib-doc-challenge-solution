"""OCR for scan-only pages — mandatory, not a fallback: ~25% of packets carry
their visible content only as pixels.

Recipe validated on train scans (docs/experiments.md): Tesseract PSM 11
(sparse text) recovers structured Key: Value lines where PSM 4/6 fail; embedded
raster is preferred (already the source image, no re-render cost); pages whose
embedded image is small or yields nothing are re-rendered at ~200 DPI.

Pages that still read badly are usually damaged *geometrically* rather than
optically (turned, skewed, or shredded into offset bands), so weak pages are
retried through mib.imaging restorations rather than at higher resolution.
Restoration level is set by MIB_RESTORE: off | skew | turn | bands (cumulative).
"""
import os
import re
import subprocess
import tempfile
from pathlib import Path

from . import imaging, parse
from .vocab import HOME_WORLDS, SPECIES, clean_ocr_line

MIN_EMBEDDED_WIDTH = 1000
RENDER_ZOOM = 2.8       # ~200 DPI

# Evidence thresholds that decide how hard to work on a page.
GOOD_ENOUGH = 6         # reads like an intact form; stop spending passes
WEAK = 4                # worth trying band reassembly

_LEVELS = ("off", "skew", "turn", "bands")


def _at_least(level):
    have = os.environ.get("MIB_RESTORE", "off").lower()
    if have not in _LEVELS:
        have = "off"
    return _LEVELS.index(have) >= _LEVELS.index(level)


def _tesseract(image_path):
    try:
        result = subprocess.run(
            ["tesseract", str(image_path), "stdout", "--psm", "11"],
            capture_output=True, text=True, timeout=20,
            env={**os.environ, "OMP_THREAD_LIMIT": "1"},
        )
        return [clean_ocr_line(l) for l in result.stdout.splitlines() if l.strip()]
    except (subprocess.TimeoutExpired, OSError):
        return []


def recognized_keys(lines):
    """How many lines carry a recognizable field label (via parse._key_for)."""
    count = 0
    for line in lines:
        head = line.split(":")[0].split(".")[0].split(";")[0]
        if parse._key_for(head):
            count += 1
    return count


_VALUE_PATTERNS = (parse.CASE_ID_RE, parse.SPONSOR_RE, parse.DATE_RE)
_VALUE_WORDS = tuple(parse.VISA_CLASSES | set(SPECIES) | set(HOME_WORLDS) | parse.ALL_FLAGS)


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


def _restorations(gray, best_score):
    """Geometric variants worth OCR'ing, cheapest and most likely first."""
    if not _at_least("skew"):
        return
    angle = imaging.skew_angle(gray)
    upright = imaging.rotate(gray, angle) if abs(angle) >= imaging.MIN_SKEW else None
    if upright is not None and best_score() < GOOD_ENOUGH:
        yield upright
    if _at_least("turn") and best_score() == 0:
        for quarter in (1, 3):                     # 90 and 270 clockwise; 180 never wins
            turned = imaging.turn(gray, quarter)
            yield imaging.rotate(turned, imaging.skew_angle(turned))
    if _at_least("bands") and best_score() < WEAK:
        bands = imaging.realign_bands(gray)
        if bands is not None:
            yield imaging.rotate(bands, imaging.skew_angle(bands))


def _sources(doc, page, tmp):
    """Page pixels to read, as (encoded_bytes, grayscale array) pairs: embedded
    raster first, then a 200-DPI render. The encoded bytes are kept so the
    unrestored pass reads exactly the original image, not a re-encode of it."""
    images = page.get_images()
    if images:
        img = doc.extract_image(images[0][0])
        if img["width"] >= MIN_EMBEDDED_WIDTH:
            yield img["image"], imaging.to_gray(img["image"])
    import fitz
    pix = page.get_pixmap(matrix=fitz.Matrix(RENDER_ZOOM, RENDER_ZOOM))
    path = Path(tmp) / "render.png"
    pix.save(path)
    raw = path.read_bytes()
    yield raw, imaging.to_gray(raw)


def ocr_page(doc, page):
    """OCR one page, retrying weak reads through geometric restoration.

    A 300-DPI grayscale/autocontrast retry was tried here and reverted: +0.21
    dev pts for 43x runtime (experiments.md row 8). The pages it targeted were
    turned or shredded, not low-resolution — hence the geometric path instead.
    """
    with tempfile.TemporaryDirectory(prefix="mibocr") as tmp:
        best, best_score, written = [], -1, 0

        def read(encoded):
            nonlocal best, best_score, written
            written += 1
            path = Path(tmp) / f"p{written}.png"
            path.write_bytes(encoded)
            lines = _tesseract(path)
            score = evidence_score(lines)
            if score > best_score:
                best, best_score = lines, score

        for encoded, gray in _sources(doc, page, tmp):
            read(encoded)
            for variant in _restorations(gray, lambda: best_score):
                read(imaging.to_png_bytes(variant))
                if best_score >= GOOD_ENOUGH:
                    break
            if best_score >= GOOD_ENOUGH:
                break
        return best
