"""Geometric restoration of damaged scans, before OCR ever sees them.

Survey finding (docs/experiments.md row 11): a large minority of scan pages are
not degraded, they are *transformed* — whole pages turned 90/270 degrees, pages
skewed several degrees, and pages cut into horizontal bands that are re-pasted
at different horizontal offsets ("shredder" damage). Tesseract reads none of
these, at any resolution; row 8's failed 300-DPI retry was spending compute on
the wrong axis. Undoing the geometry is what recovers the text.
"""
import io

import numpy as np
from PIL import Image

INK = 128            # glyphs are near-black; the faint form rules sit well above
MAX_SKEW = 8.0       # degrees; beyond this the page is turned, not skewed
SKEW_STEP = 0.25
MIN_SKEW = 0.5       # below this, rotating costs an OCR pass and buys nothing


def to_gray(image_bytes):
    return np.asarray(Image.open(io.BytesIO(image_bytes)).convert("L"))


def skew_angle(gray):
    """Angle (degrees) that lays text lines flat, by projection-profile sharpness.

    Shearing rather than rotating the ink mask keeps this to one bincount per
    candidate angle, so the whole sweep costs a few milliseconds.
    """
    ink = np.asarray(Image.fromarray((gray < INK).astype(np.uint8) * 255).resize(
        (max(1, gray.shape[1] // 3), max(1, gray.shape[0] // 3)), Image.BILINEAR)) > 40
    ys, xs = np.nonzero(ink)
    if len(ys) < 50:
        return 0.0
    height = ink.shape[0]
    best, best_score = 0.0, -1.0
    for angle in np.arange(-MAX_SKEW, MAX_SKEW + 1e-9, SKEW_STEP):
        rows = np.round(ys - xs * np.tan(np.deg2rad(angle))).astype(int) + height
        profile = np.bincount(np.clip(rows, 0, 3 * height), minlength=3 * height + 1)
        score = float((profile.astype(np.float64) ** 2).sum())
        if score > best_score:
            best_score, best = score, float(angle)
    return best


def rotate(gray, degrees):
    """Rotate counter-clockwise, padding with paper white."""
    if not degrees:
        return gray
    return np.asarray(Image.fromarray(gray).rotate(
        degrees, expand=True, fillcolor=255, resample=Image.BICUBIC))


def turn(gray, quarter_turns_cw):
    return np.asarray(Image.fromarray(gray).rotate(
        -90 * quarter_turns_cw, expand=True))


def _band_offsets(gray):
    """Per-row horizontal shift, read off the printed page-border rectangle.

    The border has a fixed width, so wherever a row shows a dark pair that wide,
    the left member's x is that band's displacement. Rows without a readable
    border inherit the last known shift, which is what makes a band coherent.
    """
    dark = gray < 150
    spans = []
    for row in dark:
        xs = np.flatnonzero(row)
        if len(xs) >= 2:
            spans.append((xs[0], xs[-1] - xs[0]))
        else:
            spans.append((-1, -1))
    widths = [w for _, w in spans if w > 0]
    if len(widths) < 20:
        return None
    border_width = int(np.median(widths))
    if border_width < gray.shape[1] // 2:
        return None            # no full-width border to key off
    offsets = np.full(len(spans), np.nan)
    for y, (left, width) in enumerate(spans):
        if width > 0 and abs(width - border_width) <= 6:
            offsets[y] = left
    return offsets if np.count_nonzero(~np.isnan(offsets)) >= 20 else None


def realign_bands(gray):
    """Slide each shredded band back onto the page's common left margin."""
    offsets = _band_offsets(gray)
    if offsets is None:
        return None
    reference = np.nanmedian(offsets)
    if np.isnan(reference):
        return None
    out = gray.copy()
    shift = 0
    moved = False
    for y in range(gray.shape[0]):
        if not np.isnan(offsets[y]):
            shift = int(round(reference - offsets[y]))
        if shift:
            out[y] = np.roll(gray[y], shift)
            moved = True
    return out if moved else None


def to_png_bytes(gray):
    buf = io.BytesIO()
    Image.fromarray(gray).save(buf, format="PNG")
    return buf.getvalue()
