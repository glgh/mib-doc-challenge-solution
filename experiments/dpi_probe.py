#!/usr/bin/env python3
"""Row 40's deferred experiment: the render read's DECLARED DPI.

The render pixels are ~200 DPI but tesseract is told `--dpi 96` — a preserved
pymupdf-pHYs accident the segmentation was tuned on. This probes the raw
render read of every hard-set scan page under three declarations (96 /
honest 202 / unset = tesseract estimates) and scores each with the frozen
`page_score`, so the accident can be kept or dropped on a measurement.

Usage: experiments/dpi_probe.py [cases.txt]
"""
import subprocess
import sys
import tempfile
from pathlib import Path

import fitz
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CH = ROOT.parent / "mib-doc-challenge"

from mib import imaging  # noqa: E402
from mib.stages import render  # noqa: E402


def main(cases_file):
    stems = [ln.strip() for ln in Path(cases_file).read_text().splitlines()
             if ln.strip() and not ln.startswith("#")]
    wins = {96: 0, 202: 0, None: 0}
    ties = 0
    rows = []
    with tempfile.TemporaryDirectory(prefix="dpiprobe") as tmp:
        for stem in stems:
            doc = fitz.open(CH / f"data/train/{stem}.pdf")
            for page_no in range(doc.page_count):
                page = doc[page_no]
                if not page.get_images():
                    continue
                srcs = list(render._sources(doc, page, tmp))
                enc = next((e for n, e, _g in srcs if n == "render"), None)
                if enc is None:
                    continue
                path = Path(tmp) / f"{stem}_p{page_no}.pnm"
                path.write_bytes(enc)
                scores = {}
                for dpi in (96, 202, None):
                    lines, _conf = render._recognize(path, dpi=dpi)
                    scores[dpi] = render.page_score(lines)
                best = max(scores.values())
                top = [d for d, s in scores.items() if s == best]
                if len(top) == 3:
                    ties += 1
                else:
                    for d in top:
                        wins[d] += 1
                rows.append((stem, page_no, scores))

    print(f"pages probed: {len(rows)}   all-tie: {ties}")
    print(f"wins (shared for joint leads): 96={wins[96]}  202={wins[202]}  unset={wins[None]}")
    tot = {d: sum(r[2][d] for r in rows) for d in (96, 202, None)}
    print(f"total page_score: 96={tot[96]}  202={tot[202]}  unset={tot[None]}")
    print("\npages where the declaration changes the score:")
    for stem, pno, s in rows:
        if len(set(s.values())) > 1:
            print(f"  {stem} p{pno}: 96={s[96]} 202={s[202]} unset={s[None]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1
                  else ROOT / "experiments/hard_set.txt"))
