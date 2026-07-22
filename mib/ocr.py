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

from .vocab import clean_ocr_line

MIN_EMBEDDED_WIDTH = 1000
RENDER_ZOOM = 2.8  # ~200 DPI


def _tesseract(image_path):
    try:
        result = subprocess.run(
            ["tesseract", str(image_path), "stdout", "--psm", "11"],
            capture_output=True, text=True, timeout=20,
        )
        return [clean_ocr_line(l) for l in result.stdout.splitlines() if l.strip()]
    except (subprocess.TimeoutExpired, OSError):
        return []


def ocr_page(doc, page):
    """OCR one page: embedded raster fast path, render fallback."""
    with tempfile.TemporaryDirectory(prefix="mibocr") as tmp:
        images = page.get_images()
        if images:
            img = doc.extract_image(images[0][0])
            if img["width"] >= MIN_EMBEDDED_WIDTH:
                path = Path(tmp) / f"emb.{img['ext']}"
                path.write_bytes(img["image"])
                lines = _tesseract(path)
                if len(lines) >= 4:
                    return lines
        import fitz
        pix = page.get_pixmap(matrix=fitz.Matrix(RENDER_ZOOM, RENDER_ZOOM))
        path = Path(tmp) / "render.png"
        pix.save(path)
        return _tesseract(path)
