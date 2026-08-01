#!/usr/bin/env python3
"""Zero-OCR census of the full-layout re-read trigger (TODO 6.7, slice B).

Applies `render.extraction_gaps` to every scanned page of an existing grid cache
— no new OCR — to authorize (or veto) arming the PSM-3 pass, before spending a
full-corpus regen. The gap assessment is a pure function of the stored reads, so
this measures exactly what the live trigger would see post-optical.

Reports:
  * per-gap fire counts + exclusives; union = projected added PSM-3 calls
    (one per firing page, since the pass is one call per triggered page)
  * OPTICAL-FLIP: pages where the injection-immune `weak` differs from the raw
    page_score weak() the optical rung uses today (on BASE reads) — the scope of
    the moved optical gate, i.e. whether slice C's regen also moves optical
  * per-label tell histogram (does any excluded short-value label leak in?)
  * per-CASE projected calls (the 120 s OCR-budget tail; time itself is
    unmeasured here — that is slice C's regen)
  * anchors: 101 p2 (must be truncated & not weak), 114 p2 (weak under the
    filter, not under raw weak()), 096 (control)

Usage: experiments/layout_pass_census.py [--cache PATH]
"""
import argparse
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mib import cache, config                       # noqa: E402
from mib.stages import render                       # noqa: E402

ANCHORS = {"MIB-000101", "MIB-000114", "MIB-000096"}
_OPT = set(render._OPTICAL_MODULES)


def is_optical(read):
    return any(part in _OPT for part in read.variant.split("+"))


def raw_weak(reads):
    """The current gate: raw page_score (no injection filter) under WEAK_BAR."""
    if not reads:
        return True
    return max(render.page_score(r.lines) for r in reads) < render.WEAK_BAR


def label_hits(reads):
    """[(label, tail, sig)] for display — the tell's view of one page."""
    hits = []
    for r in reads:
        for line in render._injection_free(r.lines):
            if len(line.split()) < 2:
                continue
            head, tail = render._label_tail(line)
            if tail is None:
                continue
            for label in render._TELL_LABELS:
                if render._weighted_sim(head, label) >= render.TELL_LABEL_SIM:
                    hits.append((label, tail, len(tail.replace(" ", ""))))
                    break
    return hits


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cache",
                    default=str(ROOT / f"output/cache/train_{config.RESTORE}.jsonl"))
    a = ap.parse_args()

    meta, recs = cache.read(a.cache)
    print(f"cache: {a.cache}")
    print(f"meta:  {config.describe(meta) if meta else '(unstamped)'}\n")

    n_pages = 0
    fire = weak_n = trunc_n = 0
    excl = Counter()          # which single arm fired, when exactly one did
    optical_flip = 0
    per_case_calls = Counter()
    tell_labels = Counter()
    anchors = {stem: [] for stem in ANCHORS}

    for rec in recs:
        if rec.get("error"):
            continue
        stem = rec["stem"]
        _pages, reads_by_page = cache.to_case(rec["pages"])
        for page_no, reads in reads_by_page.items():
            n_pages += 1
            base = [r for r in reads if not is_optical(r)]
            g = render.extraction_gaps(reads)
            g_base = render.extraction_gaps(base)

            if raw_weak(base) != g_base.weak:
                optical_flip += 1

            if g.weak or g.truncated:
                fire += 1
                per_case_calls[stem] += 1
                arms = []
                if g.weak:
                    weak_n += 1
                    arms.append("weak")
                if g.truncated:
                    trunc_n += 1
                    arms.append("trunc")
                    tell_labels.update(g.truncated)
                if len(arms) == 1:
                    excl[arms[0]] += 1

            if stem in ANCHORS:
                anchors[stem].append((page_no, len(reads), raw_weak(reads), g))

    print(f"scanned pages: {n_pages}   firing pages (added PSM-3 calls): {fire}"
          f"  ({100 * fire / max(1, n_pages):.1f}%)")
    print(f"  weak {weak_n}   truncated {trunc_n}")
    print(f"  exclusive: weak-only {excl['weak']}  trunc-only {excl['trunc']}")
    print(f"\noptical-gate flip (base-read weak moves under the filter): "
          f"{optical_flip} pages  -> {'byte-identical optical cache' if optical_flip == 0 else 'optical gate moves; slice C regen covers it'}")

    print("\nper-case added calls (the 120 s budget tail):")
    dist = Counter(per_case_calls.values())
    for k in sorted(dist):
        print(f"  {k} call(s)/case: {dist[k]} cases")
    heavy = sorted(((n, s) for s, n in per_case_calls.items() if n >= 3), reverse=True)
    if heavy:
        print(f"  >=3 calls: {[f'{s}:{n}' for n, s in heavy[:15]]}")

    print("\nper-label tell fires (which labels drive `truncated`):")
    for label, n in tell_labels.most_common():
        print(f"  {n:5d}  {label}")

    print("\nanchors:")
    for stem in sorted(ANCHORS):
        rows = anchors.get(stem) or []
        if not rows:
            print(f"  {stem}: not in cache")
            continue
        for page_no, nreads, rw, g in rows:
            print(f"  {stem} p{page_no}: reads={nreads} raw_weak={rw} "
                  f"| gaps weak={g.weak} trunc={sorted(g.truncated)}")
        _p, _n, _rw, _g = rows[0]
        # show the tell's view on the page(s) that fired trunc
        for page_no, nreads, rw, g in rows:
            if g.truncated:
                _pages, rbp = None, None
                # re-fetch this page's reads for a label dump
                for rec in recs:
                    if rec.get("stem") == stem and not rec.get("error"):
                        _pages, rbp = cache.to_case(rec["pages"])
                        break
                if rbp and page_no in rbp:
                    hits = label_hits(rbp[page_no])
                    seen = {}
                    for lbl, tail, sig in hits:
                        if lbl not in seen or sig > seen[lbl][1]:
                            seen[lbl] = (tail, sig)
                    print(f"      p{page_no} label view: "
                          + "; ".join(f"{lbl}->'{t}'({s})" for lbl, (t, s) in seen.items()))


if __name__ == "__main__":
    main()
