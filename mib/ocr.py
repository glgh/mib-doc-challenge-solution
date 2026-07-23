"""OCR for scan-only pages — mandatory, not a fallback: ~25% of packets carry
their visible content only as pixels.

Recipe validated on train scans (docs/experiments.md): Tesseract PSM 11
(sparse text) recovers structured Key: Value lines where PSM 4/6 fail; embedded
raster is preferred (already the source image, no re-render cost); pages whose
embedded image is small or yields nothing are re-rendered at ~200 DPI.
"""
import subprocess
import tempfile
from pathlib import Path

from . import parse
from .vocab import clean_ocr_line

MIN_EMBEDDED_WIDTH = 1000
RENDER_ZOOM = 2.8       # ~200 DPI


def _tesseract(image_path):
    try:
        result = subprocess.run(
            ["tesseract", str(image_path), "stdout", "--psm", "11"],
            capture_output=True, text=True, timeout=20,
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


def ocr_page(doc, page):
    """OCR one page: embedded raster fast path, render fallback, and an
    enhanced retry (300 DPI + grayscale/autocontrast) when the first pass
    yields no recognizable field labels — stamp overlays (COPY/ARCHIVE) and
    degraded scans defeat the plain pass on exactly the pages that matter
    (fee receipts, B-13 flag lines, adjudicator notes)."""
    with tempfile.TemporaryDirectory(prefix="mibocr") as tmp:
        candidates = []
        images = page.get_images()
        if images:
            img = doc.extract_image(images[0][0])
            if img["width"] >= MIN_EMBEDDED_WIDTH:
                path = Path(tmp) / f"emb.{img['ext']}"
                path.write_bytes(img["image"])
                embedded = _tesseract(path)
                if recognized_keys(embedded) > 0:
                    return embedded
                candidates.append(embedded)
        import fitz
        pix = page.get_pixmap(matrix=fitz.Matrix(RENDER_ZOOM, RENDER_ZOOM))
        path = Path(tmp) / "render.png"
        pix.save(path)
        rendered = _tesseract(path)
        candidates.append(rendered)
        # A 300-DPI grayscale/autocontrast retry pass was tried here and
        # reverted: +0.21 dev pts for 43x runtime (experiments.md row 8) —
        # destroyed scans stay destroyed.
        return max(candidates, key=recognized_keys)


