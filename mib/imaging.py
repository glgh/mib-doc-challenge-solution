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
from PIL import Image, ImageOps

INK = 128            # glyphs are near-black; the faint form rules sit well above
MAX_SKEW = 8.0       # degrees; beyond this the page is turned, not skewed
SKEW_STEP = 0.25
MIN_SKEW = 0.5       # below this, rotating costs an OCR pass and buys nothing

# Adaptive ink mask for faint/gray scans, where the glyphs sit well above INK so
# the fixed `gray < INK` mask is nearly empty and every geometry detector goes
# blind (see ink_mask). Tuned on the train scan census: a normal page has genuine
# dark ink, a faint page's ink is gray — so the threshold only adapts when the base
# mask is starved AND the ink density jumps as the threshold is raised.
PAPER_CUT = 245      # exclude the paper spike before thresholding the remainder
INK_CEIL = 230       # hard cap: never call anything lighter than this "ink"
STARVED = 0.01       # base-mask ink fraction below this may be a faint page
FAINT_HI = 210       # where faint gray ink lives
FAINT_RATIO = 5.0    # ink >=5x denser at FAINT_HI than at the base => gray, not black
MIN_ADAPT_PX = 200   # too few sub-paper pixels => blank page, don't fabricate ink


def to_gray(image_bytes):
    return np.asarray(Image.open(io.BytesIO(image_bytes)).convert("L"))


def _otsu(values):
    """Otsu's threshold over a 1-D intensity array: the histogram valley that best
    splits it into two classes (dark ink / lighter background), by maximising the
    between-class variance in one cumulative pass. `values` are ints in [0, 255]."""
    hist = np.bincount(values, minlength=256).astype(np.float64)
    total = hist.sum()
    if total == 0:
        return None
    weight = np.cumsum(hist)
    mean = np.cumsum(hist * np.arange(256))
    mean_total = mean[-1]
    w0, w1 = weight, total - weight
    m0 = mean / np.where(w0 == 0, 1, w0)
    m1 = (mean_total - mean) / np.where(w1 == 0, 1, w1)
    between = w0 * w1 * (m0 - m1) ** 2
    return int(np.argmax(between))


def ink_mask(gray, thresh=INK):
    """Boolean ink mask for the geometry detectors, adaptive on faint/gray scans.

    Normally just `gray < thresh`, so black-ink pages are byte-identical to the
    fixed-threshold behaviour. Only when that mask is *starved* (fraction < STARVED)
    AND the ink is clearly gray rather than sparse-black (>=FAINT_RATIO denser by
    FAINT_HI than at `thresh`) is the threshold re-derived by Otsu over the
    below-paper range, capped at INK_CEIL. A near-blank page (too little sub-paper
    ink) keeps the base mask rather than have a threshold invented from noise.
    """
    base = gray < thresh
    frac = base.mean()
    if frac >= STARVED:
        return base
    if (gray < FAINT_HI).mean() < FAINT_RATIO * max(frac, 1e-6):
        return base                     # ink already dark (sparse-black), leave it
    sub = gray[gray < PAPER_CUT]
    if sub.size < MIN_ADAPT_PX:
        return base                     # blank: don't fabricate ink from noise
    t = _otsu(sub.astype(np.int64))
    if t is None:
        return base
    return gray < min(t, INK_CEIL)


def skew_sweep(gray):
    """(angles, sharpness) over the candidate skew range — the curve `skew_angle`
    takes the argmax of.

    Shearing rather than rotating the ink mask keeps this to one bincount per
    candidate angle, so the whole sweep costs a few milliseconds.

    Exposed separately so `scripts/visualize_restore.py` can plot the same
    numbers the pipeline decides on, instead of a re-implementation that could
    drift from it. Returns `(angles, None)` when there is too little ink to
    measure, which is the flat-zero case the caller reports as 0 degrees.
    """
    angles = np.arange(-MAX_SKEW, MAX_SKEW + 1e-9, SKEW_STEP)
    ink = np.asarray(Image.fromarray(ink_mask(gray).astype(np.uint8) * 255).resize(
        (max(1, gray.shape[1] // 3), max(1, gray.shape[0] // 3)), Image.BILINEAR)) > 40
    ys, xs = np.nonzero(ink)
    if len(ys) < 50:
        return angles, None
    height = ink.shape[0]
    scores = np.empty(len(angles))
    for i, angle in enumerate(angles):
        rows = np.round(ys - xs * np.tan(np.deg2rad(angle))).astype(int) + height
        profile = np.bincount(np.clip(rows, 0, 3 * height), minlength=3 * height + 1)
        scores[i] = float((profile.astype(np.float64) ** 2).sum())
    return angles, scores


def skew_angle(gray):
    """Angle (degrees) that lays text lines flat, by projection-profile sharpness."""
    angles, scores = skew_sweep(gray)
    if scores is None:
        return 0.0
    return float(angles[int(np.argmax(scores))])


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
    dark = ink_mask(gray, thresh=150)
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


# --- optical restoration (behind config.ocr_optical) ------------------------
# Faint / unevenly-lit scans read as blank to OCR and are erased by a *global*
# binarization (the global-threshold sweep measured +0 over the ensemble,
# experiments A1). A local-adaptive threshold and an autocontrast stretch are a
# different operation: they recover ink the global cut washes out. Kept
# numpy/PIL-only (no scipy) so they can enter the runtime image if the A/B earns
# it. The scan-page miner (experiments) shows label-proven headroom on ~7 cases.

def _box_mean(a, w):
    """Mean over a wxw window centred on each pixel, via a summed-area table so
    the whole field costs O(H*W) regardless of window size. Windows shrink at the
    borders (clipped, not reflected), which is fine for a threshold estimate."""
    H, W = a.shape
    ii = np.zeros((H + 1, W + 1), dtype=np.float64)
    ii[1:, 1:] = np.cumsum(np.cumsum(a, axis=0), axis=1)
    r = max(1, w // 2)
    y0, y1 = np.clip(np.arange(H) - r, 0, H), np.clip(np.arange(H) + r + 1, 0, H)
    x0, x1 = np.clip(np.arange(W) - r, 0, W), np.clip(np.arange(W) + r + 1, 0, W)
    Y0, Y1, X0, X1 = y0[:, None], y1[:, None], x0[None, :], x1[None, :]
    total = ii[Y1, X1] - ii[Y0, X1] - ii[Y1, X0] + ii[Y0, X0]
    return total / np.maximum((Y1 - Y0) * (X1 - X0), 1)


def local_threshold(gray, w=25, k=0.34, R=128.0):
    """Sauvola local-adaptive binarization: threshold each pixel against its
    neighbourhood mean and standard deviation, so faint or unevenly-lit ink
    survives where one global cut erases it. Returns a uint8 0/255 image."""
    g = gray.astype(np.float64)
    m = _box_mean(g, w)
    std = np.sqrt(np.maximum(_box_mean(g * g, w) - m * m, 0.0))
    t = m * (1 + k * (std / R - 1))
    return np.where(gray > t, np.uint8(255), np.uint8(0))


def autocontrast(gray):
    """Stretch intensities so the faintest ink reaches full black (1% cutoff)."""
    return np.asarray(ImageOps.autocontrast(Image.fromarray(gray).convert("L"), cutoff=1))


def to_png_bytes(gray):
    buf = io.BytesIO()
    Image.fromarray(gray).save(buf, format="PNG")
    return buf.getvalue()
