#!/usr/bin/env python3
"""Prototype and score shred detectors against the labeled hard-case set.

Deshred must fire on genuine band displacement and stay silent on form-rule
structure (MIB-000037). This dumps the per-row border-offset profile for every
geometry-track case, then scores candidate detectors against the `shred` labels
so a rule can be chosen from what actually separates the two — not guessed.

Detection runs on the image AFTER orientation+deskew, because that is where the
pipeline would decide (a skewed border is a moving reference). Orientation is
taken from each case's label so the shred detector is tested in isolation.

Run: .venv/bin/python experiments/probe_shred.py
"""
import json
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CH = ROOT.parent / "mib-doc-challenge"

from mib import imaging                       # noqa: E402
from mib.stages import extract, render        # noqa: E402

CASES = ROOT / "experiments/hard_cases.jsonl"


def prepared_gray(case, page, orientation):
    """Source pixels after orientation + deskew — where shred is decided."""
    with extract.open_document(CH / f"data/train/{case}.pdf") as doc:
        with tempfile.TemporaryDirectory(prefix="probe") as tmp:
            gray = next(render._sources(doc, doc[page], tmp))[2]
    if orientation == "turn1":
        gray = imaging.turn(gray, 1)
    elif orientation == "turn3":
        gray = imaging.turn(gray, 3)
    angle = imaging.skew_angle(gray)
    if abs(angle) >= imaging.MIN_SKEW:
        gray = imaging.rotate(gray, angle)
    return gray


def offset_profile(gray):
    """Per-row border offset, NaN carried forward — the raw signal deshred uses."""
    offsets = imaging._band_offsets(gray)
    if offsets is None:
        return None
    filled = offsets.copy()
    last = np.nanmedian(offsets)
    for y in range(len(filled)):
        if not np.isnan(filled[y]):
            last = filled[y]
        filled[y] = last
    return filled


# --- candidate detectors: (name, fn(offsets_or_None) -> bool) ----------------

def d_border_present(off, gray=None):
    """Baseline = current behaviour: fire whenever a border was found."""
    return off is not None


def _steps(off, jump=15):
    """Indices where the offset jumps by more than `jump` px (candidate seams)."""
    d = np.abs(np.diff(off))
    return np.flatnonzero(d > jump)


def d_step_structured(off, gray=None, jump=15, max_seams=15, max_within_std=6.0):
    """Genuine shred = a few sharp seams separating flat bands.

    Form-rule structure (037) drifts smoothly or scatters: many small steps, no
    flat bands. Test: at least one real seam, not too many, and the segments
    between seams are near-constant.
    """
    if off is None:
        return False
    seams = _steps(off, jump)
    if not (1 <= len(seams) <= max_seams):
        return False
    bounds = [0, *(int(s) + 1 for s in seams), len(off)]
    within = [np.std(off[a:b]) for a, b in zip(bounds, bounds[1:]) if b - a >= 10]
    return bool(within) and float(np.median(within)) <= max_within_std


def d_few_modes(off, gray=None, bin_px=8, min_frac=0.9, max_modes=6):
    """Genuine shred = offsets concentrate in a few discrete levels (one/band).

    Form-rule variation spreads across many bins.
    """
    if off is None:
        return False
    binned = np.round((off - np.nanmin(off)) / bin_px).astype(int)
    counts = np.bincount(binned)
    order = np.sort(counts)[::-1]
    modes = order[:max_modes].sum()
    return modes / len(off) >= min_frac and np.count_nonzero(counts) > 1


def _bands(off, jump=15, min_rows=25):
    """Row segments between seams: (start, stop) for each coherent band."""
    seams = [int(s) + 1 for s in _steps(off, jump)]
    bounds = [0, *seams, len(off)]
    return [(a, b) for a, b in zip(bounds, bounds[1:]) if b - a >= min_rows]


def _best_shift(profile, ref, span=160):
    """Horizontal shift s maximizing correlation of profile shifted onto ref."""
    best_s, best_c = 0, -1.0
    ref = ref - ref.mean()
    for s in range(-span, span + 1, 2):
        p = np.roll(profile, s).astype(float)
        p -= p.mean()
        denom = (np.linalg.norm(p) * np.linalg.norm(ref)) or 1.0
        c = float(p @ ref) / denom
        if c > best_c:
            best_c, best_s = c, s
    return best_s


def d_content_shift(off, gray=None, jump=15, tol=18, min_move=20):
    """Fire only when the TEXT actually shifted with the border.

    For each band, the border implies a shift (reference - band offset). Measure
    the shift the *content* really underwent by cross-correlating that band's
    column ink-profile against the largest band's. Real shred: the two agree.
    037's false positive: the border varies but the text did not move, so the
    content shift stays ~0 while the border shift is large -> disagreement.
    """
    if off is None or gray is None:
        return False
    bands = _bands(off, jump)
    if len(bands) < 2:
        return False
    ink = (gray < imaging.INK)
    ref_band = max(bands, key=lambda ab: ab[1] - ab[0])
    ref_profile = ink[ref_band[0]:ref_band[1]].sum(axis=0)
    ref_off = np.nanmedian(off[ref_band[0]:ref_band[1]])
    agree, moved = [], False
    for a, b in bands:
        border_shift = float(ref_off - np.nanmedian(off[a:b]))
        if abs(border_shift) >= min_move:
            moved = True
        content_shift = _best_shift(ink[a:b].sum(axis=0), ref_profile)
        agree.append(abs(content_shift - border_shift))
    return moved and float(np.median(agree)) <= tol


DETECTORS = [
    ("border_present(current)", d_border_present),
    ("step_structured", d_step_structured),
    ("few_modes", d_few_modes),
    ("content_shift", d_content_shift),
]


def main():
    rows = [json.loads(l) for l in CASES.read_text().splitlines() if l.strip()]
    geo = [r for r in rows if r["track"] == "geometry" and "shred" in r.get("expect", {})]
    print(f"{len(geo)} geometry cases with a shred label\n")

    header = f"{'case':16s} {'exp':4s} " + " ".join(f"{n[:16]:>16s}" for n, _ in DETECTORS)
    print(header)
    print("-" * len(header))
    tally = {n: [0, 0] for n, _ in DETECTORS}   # [correct, total]
    diag = []
    for r in geo:
        gray = prepared_gray(r["case"], r["page"], r["expect"].get("orientation", "upright"))
        off = offset_profile(gray)
        exp = r["expect"]["shred"]
        cells = []
        for name, fn in DETECTORS:
            pred = fn(off, gray=gray)
            ok = pred == exp
            tally[name][0] += ok
            tally[name][1] += 1
            cells.append(f"{('T' if pred else 'F')}{'  ' if ok else ' X'}"[:16].rjust(16))
        print(f"{r['case']+' p'+str(r['page']):16s} {('T' if exp else 'F'):4s} " + " ".join(cells))
        if off is not None:
            seams = _steps(off)
            diag.append((r["case"], r["page"], exp, float(np.ptp(off)), len(seams)))

    print("\naccuracy:")
    for name, _ in DETECTORS:
        c, t = tally[name]
        print(f"  {name:24s} {c}/{t}")

    print("\noffset diagnostics (spread px, #seams>15px):")
    for case, page, exp, spread, nseams in diag:
        print(f"  {case} p{page}  exp_shred={exp!s:5s} spread={spread:6.0f} seams={nseams}")


if __name__ == "__main__":
    main()
