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


def faintness(gray, thresh=INK):
    """The faint-page decision as measurements: {'ink_frac', 'starved', 'faint',
    'otsu'}. `faint` is True exactly when `ink_mask` would adapt its threshold
    (mask starved AND ink gray-not-sparse-black AND enough sub-paper pixels for
    an honest Otsu); `otsu` is the adapted threshold when it would, else None.
    Exported so the S2 damage profile and the optical-rung policy read the SAME
    decision `ink_mask` acts on — one implementation of "this page is faint".
    """
    base = gray < thresh
    frac = float(base.mean())
    out = {"ink_frac": frac, "starved": frac < STARVED, "faint": False, "otsu": None}
    if not out["starved"]:
        return out
    if (gray < FAINT_HI).mean() < FAINT_RATIO * max(frac, 1e-6):
        return out                      # ink already dark (sparse-black)
    sub = gray[gray < PAPER_CUT]
    if sub.size < MIN_ADAPT_PX:
        return out                      # blank: don't fabricate ink from noise
    t = _otsu(sub.astype(np.int64))
    if t is None:
        return out
    out["faint"] = True
    out["otsu"] = int(min(t, INK_CEIL))
    return out


def ink_mask(gray, thresh=INK):
    """Boolean ink mask for the geometry detectors, adaptive on faint/gray scans.

    Normally just `gray < thresh`, so black-ink pages are byte-identical to the
    fixed-threshold behaviour. Only when that mask is *starved* (fraction < STARVED)
    AND the ink is clearly gray rather than sparse-black (>=FAINT_RATIO denser by
    FAINT_HI than at `thresh`) is the threshold re-derived by Otsu over the
    below-paper range, capped at INK_CEIL. A near-blank page (too little sub-paper
    ink) keeps the base mask rather than have a threshold invented from noise.
    The decision itself lives in `faintness`; this is its actor.
    """
    f = faintness(gray, thresh)
    if f["faint"]:
        return gray < f["otsu"]
    return gray < thresh


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


def orientation_profile(gray):
    """Readability hints per quarter-turn: {q: {"sharpness": s, "skew_deg": a}}.

    `skew_deg` is the in-frame deskew angle from the standard sweep (rules
    included — the border IS the deskew signal), computed here once so the
    caller never re-runs the sweep on a frame this function measured.

    `sharpness` is the row-projection peakedness of GLYPH ink only — long
    straight runs stripped in both axes first, because form rules dominate the
    raw projection and invert the signal: turning a form page makes its
    vertical rules horizontal, which projects sharper than the actual text
    (raw sweep scored 6/14 against the eyeball-labeled geometry registry;
    rule-stripped scores 12/14, the residue being the 90-vs-270 tie
    projections cannot see and one turned+6.5-degree page).

    Hints ORDER candidates for gated consumers (expansion priority, psm3 frame
    choice); they never decide membership — a wrong prune on a weak page would
    violate the coverage floor.
    """
    out = {}
    for q in (0, 1, 3):
        frame = gray if q == 0 else turn(gray, q)
        angles, scores = skew_sweep(frame)
        skew_deg = 0.0 if scores is None else float(angles[int(np.argmax(scores))])
        glyphs = _text_ink(frame) & _text_ink(np.ascontiguousarray(frame.T)).T
        ys = np.nonzero(glyphs)[0]
        if len(ys) < 50:
            out[q] = {"sharpness": 0.0, "skew_deg": skew_deg}
            continue
        prof = np.bincount(ys, minlength=frame.shape[0]).astype(np.float64)
        mean = prof.mean()
        out[q] = {
            "sharpness": round(float((prof ** 2).mean() / (mean ** 2)), 3) if mean else 0.0,
            "skew_deg": skew_deg,
        }
    return out


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


# --- local text-seam corrector (experiments/probe_seam_text.py, graduated) ---
# The border-driven realign above trusts the border everywhere; the corrector
# trusts it only where the TEXT agrees. Three findings behind the design
# (experiments/findings.md 2026-07-26): only seams that cut through text matter
# for OCR; the border's implied shift at such a seam is sometimes a phantom the
# text refutes (165: border says move, the cut glyph halves align at zero); and
# every signal must derive from ONE offset profile — mixing profiles corrupts
# the corrections. User-ratified on the win strips before promotion.

SUPPORT_H = 9        # +- rows in the line tracker's vertical-support window
MIN_SUPPORT = 6      # rows of that window that must agree for a run to count
MIN_GLOBAL = 12      # rows page-wide a line column must carry (kills text stems)
EDGE_FRAC = 0.35     # each border line must live in its own outer page-third
SNAP_TOL = 10        # tracker fill within this of the local pair level is jitter,
                     # not structure (real seams are >15px jumps)
SEAM_JUMP = 15       # offset jump that separates two bands
SEAM_WIN = 3         # rows each side of a seam that count as "at" the cut
RULE_ROW_FRAC = 0.4  # a row with glyph ink over this fraction of the width is a
                     # horizontal rule, not text (037's phantom source)
TEXT_MASS_MIN = 40   # px of glyph ink on a side to call the seam text-cutting
RULE_RUN = 25        # vertical dark runs at least this long are structure, not glyphs
LOCAL_MIN_CORR = 0.4  # glyph-half correlation below this cannot override the border
LOCAL_MAX_SHIFT = 150  # a "best" shift at the search edge is degenerate, not a reading


def _supported(mask):
    """Per-cell vertical support: rows within +-SUPPORT_H showing a candidate
    within +-2 columns. Boxcar via cumsum, no per-row Python loops."""
    H, W = mask.shape
    acc = np.zeros((H, W), np.float32)
    for dx in (-2, -1, 0, 1, 2):
        acc += np.roll(mask, dx, axis=1).astype(np.float32)
    acc = np.minimum(acc, 1.0)
    ii = np.vstack([np.zeros((1, W), np.float32), np.cumsum(acc, axis=0)])
    y0 = np.clip(np.arange(H) - SUPPORT_H, 0, H)
    y1 = np.clip(np.arange(H) + SUPPORT_H + 1, 0, H)
    return ii[y1] - ii[y0], acc


def line_offsets(gray):
    """Per-row offsets from independently tracked border lines, or None.

    The pair reader (`_band_offsets`) demands the full border pair per row and
    starves on degraded borders (faded right line, crop specks). Here each line
    is its own vertically-supported dark-run track — either line alone gives the
    row's offset (right implies left via the border width), and the pair test
    survives only as a cross-check when both are present."""
    dark = ink_mask(gray, thresh=150)
    H, W = dark.shape
    starts = dark & ~np.roll(dark, 1, axis=1)
    starts[:, 0] = dark[:, 0]
    ends = dark & ~np.roll(dark, -1, axis=1)
    ends[:, -1] = dark[:, -1]
    sup_l, acc_l = _supported(starts)
    sup_r, acc_r = _supported(ends)
    ok_l = starts & (sup_l >= MIN_SUPPORT) & (acc_l.sum(axis=0) >= MIN_GLOBAL)[None, :]
    ok_r = ends & (sup_r >= MIN_SUPPORT) & (acc_r.sum(axis=0) >= MIN_GLOBAL)[None, :]
    ok_l[:, int(W * EDGE_FRAC):] = False
    ok_r[:, :int(W * (1 - EDGE_FRAC))] = False
    left = np.where(ok_l.any(axis=1), ok_l.argmax(axis=1), np.nan)
    right_rev = ok_r[:, ::-1]
    right = np.where(right_rev.any(axis=1), W - 1 - right_rev.argmax(axis=1), np.nan)
    both = ~np.isnan(left) & ~np.isnan(right)
    if both.sum() < 20:
        return None
    bw = float(np.median((right - left)[both]))
    if bw < W / 2:
        return None
    offsets = np.full(H, np.nan)
    pair_ok = both & (np.abs((right - left) - bw) <= 6)
    offsets[pair_ok] = left[pair_ok]
    only_l = ~np.isnan(left) & np.isnan(right)
    offsets[only_l] = left[only_l]
    only_r = np.isnan(left) & ~np.isnan(right)
    offsets[only_r] = right[only_r] - bw
    return offsets if np.count_nonzero(~np.isnan(offsets)) >= 20 else None


def merged_offsets(gray):
    """Pair-reader offsets where the width test passes (precision), line-tracker
    offsets filling the rows it cannot measure (recall).

    A fill only contributes NEW STRUCTURE: a tracker value within SNAP_TOL of
    the nearest pair-measured level is that level — the one-line reading carries
    a few px of bias, and unsnapped fills corrupt band medians (045). Only a
    plateau genuinely apart from the local pair level keeps its own value."""
    pair = _band_offsets(gray)
    lines = line_offsets(gray)
    if pair is None or lines is None:
        return pair if lines is None else lines
    merged = pair.copy()
    meas = np.flatnonzero(~np.isnan(pair))
    prev_i = -1
    for y in np.flatnonzero(np.isnan(pair) & ~np.isnan(lines)):
        while prev_i + 1 < len(meas) and meas[prev_i + 1] < y:
            prev_i += 1
        cands = [meas[i] for i in (prev_i, prev_i + 1)
                 if 0 <= i < len(meas)]
        nearest = pair[min(cands, key=lambda i: abs(i - y))]
        merged[y] = nearest if abs(lines[y] - nearest) <= SNAP_TOL else lines[y]
    return merged


def _text_ink(gray):
    """Ink minus long vertical runs (form rules, border lines): glyphs only.
    Adaptive mask, not the fixed INK cut — faint pages have no sub-128 glyphs."""
    dark = ink_mask(gray)
    H, W = dark.shape
    fwd = np.zeros((H, W), np.int32)
    run = np.zeros(W, np.int32)
    for y in range(H):
        run = (run + 1) * dark[y]
        fwd[y] = run
    total = fwd.copy()
    for y in range(H - 2, -1, -1):
        cont = dark[y] & dark[y + 1]
        total[y] = np.where(cont, np.maximum(total[y], total[y + 1]), total[y])
    return dark & (total < RULE_RUN)


def _best_shift_corr(profile, ref, span=160):
    """Shift maximizing normalized correlation of profile onto ref, plus the
    peak value so callers can tell a confident measurement from noise."""
    best_s, best_c = 0, -1.0
    ref = ref - ref.mean()
    for s in range(-span, span + 1, 2):
        p = np.roll(profile, s).astype(float)
        p -= p.mean()
        denom = (np.linalg.norm(p) * np.linalg.norm(ref)) or 1.0
        c = float(p @ ref) / denom
        if c > best_c:
            best_c, best_s = c, s
    return best_s, best_c


def realign_local(gray):
    """Border walk with the text's own corrections at text-cutting seams.

    Seams (jumps > SEAM_JUMP in the forward-filled merged profile) that cut
    through text get their shift re-measured from the cut glyph halves: the
    column ink-profiles of half a line-pitch above and below the seam are
    cross-correlated, and a confident local estimate overrides the border's
    implied shift for the band below — including local zero, "this text never
    moved, leave it". Whitespace seams keep border behaviour (cosmetic).
    Returns None when there is nothing to move."""
    offsets = merged_offsets(gray)
    if offsets is None:
        return None
    profile = offsets.copy()
    last = np.nanmedian(offsets)
    for y in range(len(profile)):
        if not np.isnan(profile[y]):
            last = profile[y]
        profile[y] = last
    seams = [int(s) for s in np.flatnonzero(np.abs(np.diff(profile)) > SEAM_JUMP)]
    if not seams:
        return None
    ink = _text_ink(gray).astype(float)
    rule = ink.sum(axis=1) > RULE_ROW_FRAC * gray.shape[1]
    ink[rule] = 0.0                    # a horizontal rule is structure in both the
    prof = ink.sum(axis=1)             # seam classes and the correlation windows
    # median row distance between text-line block centers = the line pitch
    centers, start = [], None
    for y, on in enumerate(prof > 0):
        if on and start is None:
            start = y
        elif not on and start is not None:
            centers.append((start + y) / 2)
            start = None
    pitch = float(np.median(np.diff(centers))) if len(centers) >= 3 else 20.0
    w = max(int(round(pitch / 2)), 4)
    # One physical cut can smear across a few rows — the border offset ramps over
    # 2-3 steps (e.g. -2 -> 26 -> 68) instead of one clean jump, so `diff` reports
    # several seams within a line-pitch. The glyph-half correlation below reads a
    # half-pitch window each side, so it always sees the FULL ramp displacement;
    # scoring that against a single sub-step's jump double-counts and re-shears a
    # title the border already reassembled (900 p2 "Manual Adjudicator Note"). So
    # coalesce seams within `w` into one run and correct each run ONCE, against its
    # total drop. A lone seam is a run of one (a == b) — identical to before.
    runs = []
    for s in seams:
        if runs and s - runs[-1][1] <= w:
            runs[-1] = (runs[-1][0], s)
        else:
            runs.append((s, s))
    p2 = profile.copy()
    for i, (a, b) in enumerate(runs):
        above_mass = float(prof[max(0, a - SEAM_WIN + 1):a + 1].sum())
        below_mass = float(prof[b + 1:b + 1 + SEAM_WIN].sum())
        if above_mass < TEXT_MASS_MIN or below_mass < TEXT_MASS_MIN:
            continue                   # not a text-cutting seam
        above = ink[max(0, a - w + 1):a + 1].sum(axis=0)
        below = ink[b + 1:b + 1 + w].sum(axis=0)
        if above.sum() < TEXT_MASS_MIN or below.sum() < TEXT_MASS_MIN:
            continue
        local, corr = _best_shift_corr(below, above)
        if corr < LOCAL_MIN_CORR or abs(local) >= LOCAL_MAX_SHIFT:
            continue
        delta = float(profile[a] - profile[b + 1]) - local
        if not delta:
            continue
        nxt = runs[i + 1][0] if i + 1 < len(runs) else len(profile) - 1
        p2[b + 1:nxt + 1] += delta
    reference = np.nanmedian(p2)
    out = gray.copy()
    moved = False
    for y in range(gray.shape[0]):
        shift = int(round(reference - p2[y]))
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


def to_pnm_bytes(arr):
    """Raw PNM encode (P5 gray / P6 RGB) — same pixels as a lossless PNG at a
    fraction of the encode cost; tesseract's input, never an archival format.
    Only paths that already round-trip through lossless PNG may use this: the
    embedded-original-bytes path must keep its exact source encoding."""
    if arr.ndim == 2:
        header = b"P5 %d %d 255\n" % (arr.shape[1], arr.shape[0])
    else:
        header = b"P6 %d %d 255\n" % (arr.shape[1], arr.shape[0])
    return header + arr.tobytes()
