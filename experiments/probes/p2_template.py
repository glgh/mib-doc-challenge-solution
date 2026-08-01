#!/usr/bin/env python3
"""P2 — template matching built properly, on the sponsor digit cells.

Render clean Helvetica 0-9, segment the Otsu-binarized cell with the fixed-pitch
(tabular-digit) prior, match each position by normalized cross-correlation.
Question: does template matching beat tesseract on the confused digits?"""
import re
import numpy as np
import scipy.ndimage as ndi
from PIL import Image, ImageDraw, ImageFont
import _probe_util as U

HELV = "/System/Library/Fonts/Helvetica.ttc"
EMITTED = {"MIB-000784": "2263 2283", "MIB-000395": "6146 6148", "MIB-000594": "4867 4887",
           "MIB-000554": "1368 1388", "MIB-000870": "7561 7581", "MIB-000008": "2913 2813"}

def digit_templates(h=60):
    f = ImageFont.truetype(HELV, h)
    T = {}
    for d in "0123456789":
        im = Image.new("L", (h, int(h * 1.4)), 255)
        ImageDraw.Draw(im).text((3, 0), d, font=f, fill=0)
        a = np.asarray(im) < 128
        ys, xs = np.where(a)
        T[d] = a[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    return T

def canon(mask, size=(48, 72)):
    im = Image.fromarray((mask * 255).astype(np.uint8)).resize(size, Image.LANCZOS)
    return np.asarray(im).astype(np.float64) / 255.0

def ncc(a, b):
    a = a - a.mean(); b = b - b.mean()
    d = np.sqrt((a * a).sum() * (b * b).sum())
    return float((a * b).sum() / d) if d else 0.0

def match(mask, T):
    c = canon(mask)
    return sorted(((ncc(c, canon(t)), d) for d, t in T.items()), reverse=True)

def digit_region(cid):
    """The 4-digit block = the rightmost ink token on the Otsu line (after the
    '-' gap that separates it from 'SPN')."""
    g = U.sponsor_crop(cid)
    if g is None:
        return None
    ink = U.otsu(g)
    col = ink.sum(0)
    on = col > col.max() * 0.04
    # split into tokens on runs of empty columns >= gap
    tokens, i, W = [], 0, len(col)
    gap = max(3, W // 40)
    while i < W:
        if on[i]:
            j = i
            while j < W and (on[j] or (j + gap < W and on[j:j + gap].any())):
                j += 1
            tokens.append((i, j)); i = j
        else:
            i += 1
    if not tokens:
        return None
    c0, c1 = tokens[-1]                      # rightmost token = the digits
    return ink[:, c0:c1]

def four_digits(region, T):
    col = region.sum(0)
    on = np.where(col > col.max() * 0.06)[0]
    if len(on) < 4:
        return None, None
    c0, c1 = on[0], on[-1]
    edges = np.linspace(c0, c1 + 1, 5).astype(int)
    read, ranks = "", []
    for i in range(4):
        sl = region[:, edges[i]:edges[i + 1]]
        ys = np.where(sl.sum(1) > 0)[0]
        if len(ys) == 0:
            read += "?"; ranks.append(None); continue
        sl = sl[ys.min():ys.max() + 1]
        scored = match(sl, T)
        read += scored[0][1]
        ranks.append(scored)
    return read, ranks

T = digit_templates()
cases = [(cid, tr) for kind, cid, tr in U.load_cases() if kind == "sponsor"]
print(f"P2 template-match on {len(cases)} sponsor cells\n")
tm_recover = ocr_beat = located = 0
for cid, tr in cases:
    truth = tr["sponsor_id"].replace("SPN-", "")
    region = digit_region(cid)
    if region is None:
        print(f"{cid} truth {truth}: (cell not located)"); continue
    located += 1
    read, ranks = four_digits(region, T)
    if read is None:
        print(f"{cid} truth {truth}: (segmentation failed)"); continue
    hit = read == truth
    tm_recover += hit
    emit = EMITTED.get(cid, "? ?").split()[1]
    # per-position truth rank (how far down the NCC list is the true digit)
    posrank = []
    for i, sc in enumerate(ranks):
        if sc:
            order = [d for _, d in sc]
            posrank.append(str(order.index(truth[i]) + 1) if truth[i] in order else "-")
    print(f"{cid} truth {truth} tesseract {emit} template {read}"
          f"  {'TEMPLATE-HIT' if hit else ''}")
    print(f"      per-digit truth-rank in NCC list: {posrank}  "
          f"(top-3 margin {ranks[0][0][0]-ranks[0][2][0]:.2f} on d0)")

print(f"\ncells located {located}/{len(cases)}, template recovered {tm_recover}")
print(f"GO/NO-GO (P2): need >=3 rank-1 recoveries -> {'GO' if tm_recover>=3 else 'NO-GO'} ({tm_recover})")
