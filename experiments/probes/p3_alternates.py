#!/usr/bin/env python3
"""P3 — rank-2 alternates (offline). On the digits OCR gets wrong, is the truth
in tesseract's 2nd/3rd choice? (lstm_choice_mode=2 hOCR, per-character choices)."""
import re, subprocess, tempfile
import numpy as np
from PIL import Image
import _probe_util as U

def char_choices(ink, psm=7):
    """-> list of per-symbol ranked [(char, conf), ...] for the recognized line."""
    im = Image.fromarray((~ink * 255).astype(np.uint8))
    with tempfile.NamedTemporaryFile(suffix=".png") as f:
        im.save(f.name)
        hocr = subprocess.run(["tesseract", f.name, "stdout", "--psm", str(psm),
                               "-c", "lstm_choice_mode=2", "hocr"],
                              capture_output=True, text=True).stdout
    positions = []
    for chunk in hocr.split("id='lstm_choices")[1:]:
        ch = re.findall(r"title='x_confs ([\d.]+)'>(.?)</span>", chunk)
        ranked = [(c, float(v)) for v, c in ch if c and c != " "]
        if ranked:
            positions.append(ranked)
    return positions

BINS = {"raw": U.raw127, "otsu": U.otsu, "sauvola": lambda g: U.sauvola(g, 41, 0.2)}
cases = [(cid, tr) for kind, cid, tr in U.load_cases() if kind == "sponsor"]
print("P3 rank-2 alternates on confused sponsor digits (pooled over binarizations)\n")
wrong = 0
in_top2 = in_top3 = 0
for cid, tr in cases:
    truth = tr["sponsor_id"].replace("SPN-", "")
    g = U.sponsor_crop(cid)
    if g is None:
        continue
    for bname, fn in BINS.items():
        pos = char_choices(fn(g))
        cell = [p for p in pos if p and p[0][0].isdigit()][-4:]
        if len(cell) < 4:
            continue
        top1 = "".join(p[0][0] for p in cell)
        for i, ranked in enumerate(cell):
            t = truth[i]
            if top1[i] == t:                  # this binarization got it right
                continue
            wrong += 1
            chars = [c for c, _ in ranked]
            rank = chars.index(t) + 1 if t in chars else None
            in_top2 += (rank is not None and rank <= 2)
            in_top3 += (rank is not None and rank <= 3)
            alts = " ".join(f"{c}:{v:.0f}" for c, v in ranked[:4])
            print(f"  {cid} {bname:8} pos{i} truth {t} got {top1[i]}  "
                  f"truth-rank={rank if rank else '-'}  alts=[{alts}]")

print(f"\nconfused digit-positions (pooled): {wrong}")
if wrong:
    print(f"  truth in top-2: {in_top2}/{wrong} ({in_top2/wrong:.0%})")
    print(f"  truth in top-3: {in_top3}/{wrong} ({in_top3/wrong:.0%})")
    go = in_top3 / wrong >= 0.40
    print(f"GO/NO-GO (P3): truth in top-3 on >=40% -> {'GO' if go else 'NO-GO'} ({in_top3/wrong:.0%})")
