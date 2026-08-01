#!/usr/bin/env python3
"""P2b — whole-token template matching for blotted risk_flags (the user's intel).

Locate the flag-value region on the B-13 slip, render the 8 flag tokens in
Helvetica, and match. Question: does the true flag token rank #1 with a margin?"""
import csv, io, re, subprocess, tempfile
import numpy as np
import scipy.ndimage as ndi
import fitz
from PIL import Image, ImageDraw, ImageFont
import _probe_util as U

HELV = "/System/Library/Fonts/Helvetica.ttc"
FLAGS = ["memory_tampering", "planetary_embargo", "active_warrant", "biohazard_red",
         "identity_conflict", "sponsor_mismatch", "illegible_biometrics", "rescinded_denial"]
truth = {r["case_id"]: r for r in csv.DictReader(open(U.LABELS))}

def token_template(text, h):
    f = ImageFont.truetype(HELV, h)
    disp = text.replace("_", " ")
    w = int(f.getlength(disp)) + 6
    im = Image.new("L", (w, int(h * 1.5)), 255)
    ImageDraw.Draw(im).text((3, 0), disp, font=f, fill=0)
    a = np.asarray(im) < 128
    ys, xs = np.where(a)
    return a[ys.min():ys.max() + 1, xs.min():xs.max() + 1] if len(xs) else a

def ncc_resized(region, templ):
    size = (200, 40)
    r = np.asarray(Image.fromarray((region * 255).astype(np.uint8)).resize(size, Image.LANCZOS)) / 255.
    t = np.asarray(Image.fromarray((templ * 255).astype(np.uint8)).resize(size, Image.LANCZOS)) / 255.
    a, b = r - r.mean(), t - t.mean()
    d = np.sqrt((a * a).sum() * (b * b).sum())
    return float((a * b).sum() / d) if d else 0.0

def flag_value_region(cid):
    """Find the blotted 'Observed flags:' value region on the B-13 slip."""
    doc = fitz.open(U.TRAIN.format(cid))
    try:
        for pg in doc:
            for xref, *_ in pg.get_images(full=True):
                pix = fitz.Pixmap(doc, xref)
                if pix.n > 4: pix = fitz.Pixmap(fitz.csRGB, pix)
                img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("L")
                lines = {}
                for r in U._tsv(img):
                    if len(r) >= 12:
                        lines.setdefault((r[2], r[3], r[4]), []).append(r)
                for ws in lines.values():
                    text = " ".join(w[11] for w in ws).lower()
                    if re.search(r"observ|obsen|flag|glee|shenteg|degen|risk", text):
                        xs = [int(w[6]) for w in ws]; ys = [int(w[7]) for w in ws]
                        xe = [int(w[6]) + int(w[8]) for w in ws]; ye = [int(w[7]) + int(w[9]) for w in ws]
                        # the VALUE sits on this line, right of the label — take full line + a bit below
                        return img.crop((min(xs), min(ys) - 2,
                                         img.width, min(ye[-1] + 30, img.height))), text
    finally:
        doc.close()
    return None, None

for cid in ("MIB-000070", "MIB-000957"):
    tflags = truth[cid]["risk_flags"].split("|")
    crop, found = flag_value_region(cid)
    print(f"=== {cid}  truth {tflags}  ===")
    if crop is None:
        print("  flag-value line not locatable (OCRs to nothing)\n"); continue
    print(f"  located line OCR: {found!r}")
    g = np.asarray(crop.resize((crop.width * 3, crop.height * 3), Image.LANCZOS))
    region = U.otsu(g)
    scored = sorted(((ncc_resized(region, token_template(fl, 40)), fl) for fl in FLAGS), reverse=True)
    for s, fl in scored:
        mark = "  <-- TRUTH" if fl in tflags else ""
        print(f"    {s:+.3f}  {fl}{mark}")
    top_truth_rank = min(i for i, (_, fl) in enumerate(scored) if fl in tflags) + 1
    margin = scored[0][0] - scored[1][0]
    print(f"  best truth-flag rank = {top_truth_rank}/8, top-2 margin = {margin:.3f}\n")
