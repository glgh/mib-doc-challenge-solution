#!/usr/bin/env python3
"""Track 1.1 — TSV word-confidence probe on the anchor + hard-set pages.

No pipeline change. For each probe page, regenerate the geometric ladder
variants live (same code path as S2), OCR each variant twice — the pipeline's
plain `stdout` pass and a `tsv` pass — and compare:

  1. selection: which variant `evidence_score` picks vs which mean word conf
     picks (graduation bar: conf-preferred as-good-or-better on ~10/12 by eye —
     side-by-side PNGs are written for every page where the winners differ);
  2. the lexicon-bias trap: per-line conf of wrong-but-well-formed case-id
     lines (the MIB-000000 shape) — if hallucinated ids carry high conf, conf
     inherits ev's pathology instead of fixing it;
  3. the 1.2 migration risk: does line text reconstructed from tsv rows match
     the stdout text the pipeline parses today?

Usage: experiments/conf_probe.py [--out-dir output/viz/conf_probe]
"""
import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CH = ROOT.parent / "mib-doc-challenge"

from mib.parse import CASE_ID_RE  # noqa: E402
from mib.stages import extract, render  # noqa: E402
from mib.vocab import clean_ocr_line  # noqa: E402

# 1-indexed anchors from docs/TODO.md, then stratified picks from hard_pages.txt.
ANCHORS = [("MIB-000990", 0), ("MIB-000252", 1), ("MIB-000595", 2)]
PER_CLASS = 2


def hard_picks():
    """First PER_CLASS non-HAZARD pages per damage class from hard_pages.txt."""
    picked, seen_class = [], {}
    for line in (ROOT / "experiments/hard_pages.txt").read_text().splitlines():
        if line.startswith("#") or not line.strip() or "HAZARD" in line:
            continue
        m = re.match(r"(MIB-\d{6}) (\d+)\s+# (\S+)", line)
        if not m:
            continue
        cls = m.group(3)
        if seen_class.get(cls, 0) < PER_CLASS:
            seen_class[cls] = seen_class.get(cls, 0) + 1
            picked.append((m.group(1), int(m.group(2))))
    return picked


def tesseract(path, mode):
    """mode 'text' -> cleaned lines (the pipeline's pass); 'tsv' -> (lines, line_confs)."""
    args = ["tesseract", str(path), "stdout", "--psm", str(render.PRIMARY_PSM)]
    if mode == "tsv":
        args.append("tsv")
    out = subprocess.run(args, capture_output=True, text=True, timeout=30,
                         env={**os.environ, "OMP_THREAD_LIMIT": "1"}).stdout
    if mode == "text":
        return [clean_ocr_line(l) for l in out.splitlines() if l.strip()]
    lines, confs, nwords = [], [], []
    cur_key, cur_words, cur_confs = None, [], []
    for row in out.splitlines()[1:]:
        f = row.split("\t")
        if len(f) < 12 or f[0] != "5":
            continue
        key = (f[1], f[2], f[3], f[4])
        conf, word = float(f[10]), f[11]
        if not word.strip() or conf < 0:
            continue
        if key != cur_key and cur_words:
            lines.append(clean_ocr_line(" ".join(cur_words)))
            confs.append(sum(cur_confs) / len(cur_confs))
            nwords.append(len(cur_confs))
            cur_words, cur_confs = [], []
        cur_key = key
        cur_words.append(word)
        cur_confs.append(conf)
    if cur_words:
        lines.append(clean_ocr_line(" ".join(cur_words)))
        confs.append(sum(cur_confs) / len(cur_confs))
        nwords.append(len(cur_confs))
    keep = [(l, c, n) for l, c, n in zip(lines, confs, nwords) if l]
    return ([l for l, _, _ in keep], [c for _, c, _ in keep],
            [n for _, _, n in keep])


def variants_for(doc, page):
    with tempfile.TemporaryDirectory(prefix="confprobe") as tmp:
        for name, _encoded, gray in render._sources(doc, page, tmp):
            yield name, gray
            for vname, img in render._restorations(gray):
                yield f"{name}+{vname}", img


def side_by_side(entries, ev_win, conf_win, case, pno, out_dir):
    """One PNG: ev's pick (left) vs conf's pick (right), full page + captions."""
    font = ImageFont.load_default(20)
    cells = []
    for title, name in (("evidence_score pick", ev_win), ("conf-mass pick", conf_win)):
        e = next(e for e in entries if e["variant"] == name)
        img = Image.fromarray(e["gray"]).convert("L")
        h = 640
        thumb = img.resize((max(1, round(img.width * h / img.height)), h), Image.LANCZOS)
        cells.append((f"{title}: {name}  ev={e['ev']} mass={e['mass']:.0f}", thumb))
    w = sum(c.width for _, c in cells) + 36
    total = Image.new("RGB", (w, 700), "white")
    d = ImageDraw.Draw(total)
    x = 12
    for cap, thumb in cells:
        d.text((x, 8), cap, fill="black", font=font)
        total.paste(thumb, (x, 40))
        x += thumb.width + 12
    path = out_dir / f"confprobe_{case}_p{pno + 1}.png"
    total.save(path)
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=str(ROOT / "output/viz/conf_probe"))
    args = ap.parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    pages = ANCHORS + hard_picks()
    agree = differ = 0
    text_same = text_total = 0
    wrong_id_lines = []
    galleries = []
    for case, pno in pages:
        with extract.open_document(CH / f"data/train/{case}.pdf") as doc:
            entries = []
            for vname, gray in variants_for(doc, doc[pno]):
                path = Path(tempfile.mktemp(suffix=".png"))
                Image.fromarray(gray).convert("L").save(path)
                try:
                    plain = tesseract(path, "text")
                    tsv_lines, confs, nwords = tesseract(path, "tsv")
                finally:
                    path.unlink(missing_ok=True)
                ev = render.evidence_score(plain)
                conf = sum(confs) / len(confs) if confs else 0.0
                # conf mass = volume x quality: robust to the rotated-page trap
                # where a few confident garbage words dominate a plain mean.
                mass = sum(c * n for c, n in zip(confs, nwords))
                entries.append(dict(variant=vname, gray=gray, ev=ev, conf=conf,
                                    mass=mass, plain=plain, tsv=tsv_lines,
                                    confs=confs))
                text_total += 1
                text_same += (plain == tsv_lines)
                for line, lc in zip(tsv_lines, confs):
                    for cid in CASE_ID_RE.findall(line):
                        if cid != case:
                            wrong_id_lines.append((case, pno, vname, cid, lc, conf))
        ev_win = max(entries, key=lambda e: e["ev"])["variant"]
        conf_win = max(entries, key=lambda e: e["mass"])["variant"]
        same = ev_win == conf_win
        agree += same
        differ += not same
        print(f"\n== {case} p{pno + 1}  ev-pick: {ev_win}  conf-pick: {conf_win}"
              f"  {'SAME' if same else '<-- DIFFER'}")
        for e in sorted(entries, key=lambda e: -e["mass"]):
            risk = next((l for l in e["tsv"] if "flag" in l.lower()), "")
            print(f"   {e['variant']:22s} ev={e['ev']:2d} mass={e['mass']:7.0f} "
                  f"mean={e['conf']:5.1f}  {risk[:46]}")
        if not same:
            galleries.append(side_by_side(entries, ev_win, conf_win, case, pno, out_dir))

    print(f"\npages: {len(pages)}  winners agree: {agree}  differ: {differ}")
    print(f"tsv-reconstructed text == stdout text: {text_same}/{text_total} variant reads")
    print("\nwrong-but-well-formed case-id lines (the MIB-000000 shape):")
    for case, pno, vname, cid, lc, pageconf in wrong_id_lines:
        print(f"  {case} p{pno + 1} {vname:22s} read {cid}: line conf {lc:5.1f} "
              f"(page mean {pageconf:5.1f})")
    for g in galleries:
        print("gallery:", g)


if __name__ == "__main__":
    main()
