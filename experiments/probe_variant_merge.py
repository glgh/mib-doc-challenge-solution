#!/usr/bin/env python3
"""Size the per-field variant-merge lever, and the residual tie-break risk.

The guard (`render.best`) picks ONE whole OCR reading per page. But base
(pre-deshred) and desh (border-deshred) each read some fields better than the
other (037: name from base, purpose from desh; 025: species from base, date from
desh). A per-field merge would keep the best-read copy of every field — the same
thing `packet.merge_fields` already does across *documents*, applied across OCR
*variants* of one page.

This probe quantifies, over a set of pages, without touching the pipeline:
  * how often base and desh disagree on a field, and how the disagreement splits
    (one side absent vs both present-and-different);
  * on disagreements where we know the truth, whether a validity-merge lands on
    the truth more often than the whole-reading guard does;
  * the residual hard case — both readings valid but different (025's 2028 vs
    2026) — and whether base or desh is the correct one, which is the evidence for
    picking a tie-break rule.

Orientation + deskew are the pipeline's own detection (production-realistic).
Only pages where `realign_bands` fires can disagree via deshred; the rest are
skipped (no second OCR).

Run: .venv/bin/python experiments/probe_variant_merge.py [bag|hard|both]
"""
import csv
import json
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "experiments"))
CH = ROOT.parent / "mib-doc-challenge"

from mib import imaging, parse, packet          # noqa: E402
from mib.stages import extract, render          # noqa: E402
from mib.textmatch import normalize, EMPTY      # noqa: E402
import pipeline as P                            # noqa: E402

HARD = ROOT / "experiments/hard_cases.jsonl"
BAG = ROOT / "experiments/test_pages.txt"
LABELS = CH / "data/train_labels.csv"
FIELDS = parse.FIELDS


def load_labels():
    out = {}
    with open(LABELS) as f:
        for row in csv.DictReader(f):
            out[row["case_id"]] = row
    return out


def source_gray(case, page):
    with extract.open_document(CH / f"data/train/{case}.pdf") as doc:
        with tempfile.TemporaryDirectory(prefix="pvm") as tmp:
            return next(render._sources(doc, doc[page], tmp))[2]


def read(gray):
    """OCR once -> (evidence_score, snapped field dict)."""
    with tempfile.TemporaryDirectory(prefix="pvm") as tmp:
        p = Path(tmp) / "x.png"
        p.write_bytes(imaging.to_png_bytes(gray))
        lines = render._tesseract(p)
    ev = render.evidence_score(lines)
    kv = parse.parse_kv(lines)
    for fn, v in parse.parse_prose(lines).items():
        kv.setdefault(fn, v)
    kv = packet._repair_ocr_kv(kv)              # snap to vocab; drop hopeless ids
    return ev, {k: v for k, v in kv.items() if not k.startswith("_")}


def variants(case, page):
    """(base_ev, base_fields, desh_ev, desh_fields) or None if deshred didn't fire."""
    src = source_gray(case, page)
    _, ori = P.orient(src)
    oriented = ori if ori is not None else src
    _, dsk = P.deskew(oriented)
    base = dsk if dsk is not None else oriented
    desh = imaging.realign_bands(base)
    if desh is None:
        return None
    be, bf = read(base)
    de, df = read(desh)
    return be, bf, de, df


def pages(which):
    if which in ("bag", "both"):
        for line in BAG.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                case, page = line.split()
                yield case, int(page)
    if which in ("hard", "both"):
        for line in HARD.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                if r["track"] == "geometry":
                    yield r["case"], r["page"]


def truthy(v):
    return v is not None and normalize(v) not in EMPTY


def main(which="bag"):
    labels = load_labels()
    plist = list(dict.fromkeys(pages(which)))    # dedupe, keep order
    print(f"set={which}: {len(plist)} pages\n")

    fired = 0
    dis = Counter()                 # field -> # disagreements
    split = Counter()               # (field, kind) -> n   kind in base_only/desh_only/both_differ
    # decisive tally over disagreements where truth is known:
    verdict = Counter()             # merge_better / guard_better / both_right / both_wrong / truth_unknown
    ties = []                       # (case,page,field,base,desh,truth,who_right)

    for i, (case, page) in enumerate(plist):
        v = variants(case, page)
        if (i + 1) % 10 == 0:
            print(f"  {i + 1}/{len(plist)}", file=sys.stderr)
        if v is None:
            continue
        fired += 1
        be, bf, de, df = v
        guard = df if de > be else bf            # whole-reading guard pick (tie -> base)
        truth = labels.get(case, {})
        for fn in FIELDS:
            b, d = bf.get(fn), df.get(fn)
            if normalize(b or "") == normalize(d or ""):
                continue                         # agree (incl. both absent)
            dis[fn] += 1
            if b and not d:
                kind = "base_only"
            elif d and not b:
                kind = "desh_only"
            else:
                kind = "both_differ"
            split[(fn, kind)] += 1

            t = truth.get(fn)
            if not truthy(t):
                verdict["truth_unknown"] += 1
                continue
            tn = normalize(t)
            # validity-merge: prefer the present value; if both present, tie-break -> base
            merge = b if b else d
            if b and d:
                merge = b                        # default tie-break (conservative)
            g_ok = normalize(guard.get(fn) or "") == tn
            m_ok = normalize(merge or "") == tn
            if m_ok and not g_ok:
                verdict["merge_better"] += 1
            elif g_ok and not m_ok:
                verdict["guard_better"] += 1
            elif g_ok and m_ok:
                verdict["both_right"] += 1
            else:
                verdict["both_wrong"] += 1
            if kind == "both_differ":
                who = "base" if normalize(b) == tn else ("desh" if normalize(d) == tn else "neither")
                ties.append((case, page, fn, b, d, t, who))

    print(f"deshred fired (disagreement possible) on {fired}/{len(plist)} pages\n")

    print("per-field disagreements (base vs desh):")
    print(f"  {'field':16s} {'total':>5s} {'base_only':>10s} {'desh_only':>10s} {'both_differ':>12s}")
    for fn in FIELDS:
        if dis[fn]:
            print(f"  {fn:16s} {dis[fn]:5d} {split[(fn,'base_only')]:10d} "
                  f"{split[(fn,'desh_only')]:10d} {split[(fn,'both_differ')]:12d}")
    print(f"  {'TOTAL':16s} {sum(dis.values()):5d}")

    print("\ndecisive: on disagreements with known truth, validity-merge vs whole-reading guard:")
    for k in ("merge_better", "guard_better", "both_right", "both_wrong", "truth_unknown"):
        print(f"  {k:16s} {verdict[k]}")

    print("\nresidual hard cases — both readings present & different (the tie-break question):")
    if not ties:
        print("  (none)")
    for case, page, fn, b, d, t, who in ties:
        print(f"  {case} p{page} {fn}: base={b!r} desh={d!r} truth={t!r} -> correct: {who}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "bag")
