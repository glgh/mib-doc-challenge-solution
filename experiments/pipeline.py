"""Candidate progressive-restoration stages — the code under active iteration.

This is the sandbox the repair bench renders. Edit these functions, rerun
`experiments/repair_bench.py`, look at the before/after. Nothing here is imported
by the runtime pipeline; a stage graduates to `mib/imaging.py` only once it wins
on the hard cases.

A "stage" is a function gray -> (label, image_or_None). None means the stage
declined to act (no damage of its kind detected), and the bench shows the input
carried through. Stages compose left to right: orientation -> deskew -> deshred.
"""
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mib import imaging  # noqa: E402

INK = imaging.INK


# --- orientation: ink run-length anisotropy + top-mass tiebreak -------------

def _mean_run(mask, axis):
    """Mean length of a contiguous ink run along `axis`.

    Total ink / number of run-starts. For horizontal text the vertical runs are
    the longer ones — glyph stems span the x-height uninterrupted, while
    horizontal runs are chopped at every inter-character gap (docs/damage-geometry).
    """
    total = mask.sum()
    if total == 0:
        return 0.0
    if axis == 0:                                   # vertical runs, down columns
        starts = mask[0].sum() + (mask[1:] & ~mask[:-1]).sum()
    else:                                           # horizontal runs, across rows
        starts = mask[:, 0].sum() + (mask[:, 1:] & ~mask[:, :-1]).sum()
    return total / max(1, starts)


def _top_mass(gray):
    """Fraction of ink in the top third — the intake form's dense block sits high."""
    ink = (gray < INK)
    if ink.sum() == 0:
        return 0.0
    return ink[: ink.shape[0] // 3].sum() / ink.sum()


def orientation(gray, turn_margin=0.20):
    """('upright'|'turn1'|'turn3', confidence).

    Anisotropy: for upright text the vertical ink runs are longer (stems span the
    x-height), so ratio = mean_vertical_run / mean_horizontal_run > 1. A turned
    page inverts it.

    The threshold is deliberately **asymmetric**. A wrong turn rotates a good page
    into garbage; a missed turn merely leaves it upright, which is where it
    started. And a form's horizontal table-rules inflate the horizontal run-length,
    dragging an *upright* page's ratio slightly below 1 (MIB-000109/193/221 sit at
    0.89–0.94 upright). So we turn only on *strong* evidence — ratio ≤ 1 −
    turn_margin — and default to upright through the whole ambiguous band. True
    turns are far more anisotropic (ratio ≤ 0.65) so nothing real is lost.
    """
    ink = (gray < INK)
    v, h = _mean_run(ink, 0), _mean_run(ink, 1)
    ratio = v / max(1e-6, h)
    conf = abs(ratio - 1.0)
    if ratio <= 1.0 - turn_margin:                   # confidently turned
        t1, t3 = imaging.turn(gray, 1), imaging.turn(gray, 3)
        pick = "turn1" if _top_mass(t1) >= _top_mass(t3) else "turn3"
        return pick, conf
    return "upright", conf


def orient(gray):
    """Stage form: apply the detected orientation."""
    o, conf = orientation(gray)
    if o == "upright":
        return f"orient upright (conf {conf:.2f})", None
    q = 1 if o == "turn1" else 3
    return f"orient {o} (conf {conf:.2f})", imaging.turn(gray, q)


# --- deskew (reuses the shipped detector) -----------------------------------

def deskew(gray):
    angle = imaging.skew_angle(gray)
    if abs(angle) < imaging.MIN_SKEW:
        return f"deskew {angle:+.2f}° (skip)", None
    return f"deskew {angle:+.2f}°", imaging.rotate(gray, angle)


# --- deshred: border-based (current shipped behaviour, for comparison) -------

def deshred_border(gray):
    out = imaging.realign_bands(gray)
    return ("deshred/border (skip)", None) if out is None else ("deshred/border", out)


# --- deshred: content cross-correlation (the candidate) ---------------------

def _offset_profile(gray):
    off = imaging._band_offsets(gray)
    if off is None:
        return None
    filled = off.copy()
    last = np.nanmedian(off)
    for y in range(len(filled)):
        if not np.isnan(filled[y]):
            last = filled[y]
        filled[y] = last
    return filled


def _seams(off, jump=15):
    return [int(s) + 1 for s in np.flatnonzero(np.abs(np.diff(off)) > jump)]


def _bands(off, jump=15, min_rows=25):
    bounds = [0, *_seams(off, jump), len(off)]
    return [(a, b) for a, b in zip(bounds, bounds[1:]) if b - a >= min_rows]


def _best_shift(profile, ref, span=160):
    ref = ref - ref.mean()
    best_s, best_c = 0, -1.0
    for s in range(-span, span + 1, 2):
        p = np.roll(profile, s).astype(float)
        p -= p.mean()
        denom = (np.linalg.norm(p) * np.linalg.norm(ref)) or 1.0
        c = float(p @ ref) / denom
        if c > best_c:
            best_c, best_s = c, s
    return best_s


def _shift_block(block, s):
    """Slide a row-block horizontally by s px, padding paper-white (no wrap)."""
    if not s:
        return block
    out = np.full_like(block, 255)
    w = block.shape[1]
    if abs(s) >= w:
        return out
    if s > 0:
        out[:, s:] = block[:, :w - s]
    else:
        out[:, :w + s] = block[:, -s:]
    return out


def deshred_content(gray, min_move=8, tol=28):
    """Border proposes, content confirms — applied per row.

    Fine alignment comes from the border's *per-row* shift (what makes the
    shipped realign_bands align 045 well). Safety comes from the *content*
    cross-correlation: a band is corrected only when its text shift agrees with
    its border shift (|Δ| ≤ tol). 037's form-rule bands disagree (content ~0,
    border large) → left untouched, so the clean line no longer garbles. Within a
    confirmed band the shift is taken per row, so it tracks a border that isn't
    perfectly flat. Returns None if no band is both moved and confirmed."""
    off = _offset_profile(gray)
    if off is None:
        return "deshred/content (no border)", None
    bands = _bands(off)
    if len(bands) < 2:
        return "deshred/content (no bands)", None
    ink = (gray < INK)
    ref_a, ref_b = max(bands, key=lambda ab: ab[1] - ab[0])
    ref_profile = ink[ref_a:ref_b].sum(axis=0)
    ref_off = np.nanmedian(off[ref_a:ref_b])
    out = gray.copy()
    applied, moved = [], False
    for a, b in bands:
        border_shift = int(round(ref_off - np.nanmedian(off[a:b])))
        content_shift = _best_shift(ink[a:b].sum(axis=0), ref_profile)
        if abs(border_shift) < min_move or abs(content_shift - border_shift) > tol:
            continue                                   # not moved, or content disagrees
        for y in range(a, b):                          # per-row fine alignment
            s = int(round(ref_off - off[y]))
            if s:
                out[y:y + 1] = _shift_block(gray[y:y + 1], s)
        applied.append(border_shift)
        moved = True
    if not moved:
        return "deshred/content (none confirmed)", None
    return f"deshred/content applied={applied}", out


# The stage sequence the bench renders, in order.
STAGES = [deskew, deshred_border, deshred_content]
