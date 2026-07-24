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

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mib import imaging  # noqa: E402

INK = imaging.INK


# --- orientation: ink run-length anisotropy + top-mass tiebreak -------------

def _mean_run(mask, axis):
    """Mean length of a contiguous ink run along `axis`.

    Total ink / number of run-starts. For horizontal text the vertical runs are
    the longer ones — glyph stems span the x-height uninterrupted, while
    horizontal runs are chopped at every inter-character gap (docs/BACKGROUND §4).
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
    ink = imaging.ink_mask(gray)
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
    ink = imaging.ink_mask(gray)
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


# Content cross-correlation was the other deshred candidate and is deleted, not
# shelved: the OCR A/B (findings.md, 2026-07-23) has it trailing the border
# method on every true positive. Cross-correlating column ink-profiles between
# bands that hold *different text* is not a shift estimator — on 045 it railed to
# the ±160 span limit — so the idea is wrong at the root, not undertuned.
