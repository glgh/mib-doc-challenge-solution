#!/usr/bin/env python3
"""Re-price row 38(a) on the GRID substrate: drop turn rungs on strong-base pages.

Row 38 (2026-07-25) measured this on the **ladder**, which was replaced by the
composition grid the next day (rows 59/60). Its "predictions IDENTICAL, 16% of
OCR" therefore does not transfer, and this script re-measures it where the
pipeline actually lives now. The motive is runtime insurance, not score: the
5,000-case contract run has only 1.27x headroom (row 102), and the user's
constraint is explicit — a speedup ships only if it costs nothing.

Method: filter reads out of an existing cache and replay. That simulates never
having created them, which is sound here for two structural reasons:

  1. Read creation has no early exit (the `GOOD_ENOUGH` stop was removed at
     -0.21 dev, row 16), so which variants get OCR'd does not depend on what
     earlier variants said, except through the two gates below.
  2. The optical tier's gate is `extraction_gaps(reads).weak`, i.e.
     `max(page_score) < WEAK_BAR`. Removing reads can only LOWER that max, so it
     can only flip a page from strong to weak — and on a strong-BASE page the
     surviving base read already clears the bar by construction. So the optical
     tier fires identically on exactly the pages this ablation touches.

The one gate that removal can genuinely perturb is the PSM-3 layout pass, whose
trigger `extraction_gaps(reads).truncated` GROWS as reads disappear: a label
whose only value tail came from a turn frame becomes "truncated" once the turn
frames are gone, and the pipeline would answer with an extra OCR call that no
cache can supply. That is a real limit of an offline filter, so this script
counts those pages explicitly rather than hiding them — they are the cases where
the replay's answer is an approximation, and a non-zero count means the ablation
needs a live regen before anyone believes it.

Usage: .venv/bin/python experiments/turn_rung_ablation.py [cache.jsonl] [out.jsonl]
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mib import cache  # noqa: E402
from mib.records import Read, best_read  # noqa: E402
from mib.stages import render  # noqa: E402

SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "output/cache/train_grid.jsonl"
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "output/cache/train_grid_noturn.jsonl"
TURN_TOKENS = ("turn1", "turn3")


def is_base(variant):
    """A raw source read: the un-composed pixels, one per source."""
    return "+" not in variant


def is_turn(variant):
    return any(t in variant.split("+") for t in TURN_TOKENS)


def hydrate(page_no, read_dicts):
    return [Read(page_no=page_no, lines=list(r["lines"]), variant=r["variant"],
                 conf=r["conf"], cost_ms=r["cost_ms"]) for r in read_dicts]


def main():
    meta, records = cache.read(SRC)
    pages_total = pages_touched = 0
    reads_total = reads_dropped = 0
    ms_total = ms_dropped = 0.0
    newly_truncated = []          # the approximation's blast radius
    weak_flips = []               # must stay empty; see the docstring's point 2

    for rec in records:
        for page in rec.get("pages") or []:
            read_dicts = page.get("reads")
            if not read_dicts:
                continue
            pages_total += 1
            reads_total += len(read_dicts)
            ms_total += sum(r.get("cost_ms") or 0.0 for r in read_dicts)

            all_reads = hydrate(page.get("page_no", 0), read_dicts)
            base = [r for r in all_reads if is_base(r.variant)]
            if not base:
                continue
            strong = max(render.page_score(render._injection_free(r.lines))
                         for r in base) >= render.WEAK_BAR
            if not strong:
                continue

            kept_dicts = [r for r in read_dicts if not is_turn(r["variant"])]
            if len(kept_dicts) == len(read_dicts):
                continue
            pages_touched += 1
            reads_dropped += len(read_dicts) - len(kept_dicts)
            ms_dropped += (sum(r.get("cost_ms") or 0.0 for r in read_dicts)
                           - sum(r.get("cost_ms") or 0.0 for r in kept_dicts))

            kept = hydrate(page.get("page_no", 0), kept_dicts)
            before, after = render.extraction_gaps(all_reads), render.extraction_gaps(kept)
            if after.truncated - before.truncated:
                newly_truncated.append((rec.get("stem"), page.get("page_no"),
                                        sorted(after.truncated - before.truncated)))
            if after.weak != before.weak:
                weak_flips.append((rec.get("stem"), page.get("page_no")))

            page["reads"] = kept_dicts
            primary = best_read(kept)
            page["ocr_lines"] = primary.lines if primary else []

    with open(OUT, "w") as f:
        cache.write_header(f, meta)
        for rec in records:
            cache.append(f, rec)

    print(f"pages with reads      : {pages_total}")
    print(f"pages touched (strong): {pages_touched}  ({100*pages_touched/pages_total:.1f}%)")
    print(f"reads dropped         : {reads_dropped} / {reads_total} "
          f"({100*reads_dropped/reads_total:.1f}%)")
    print(f"OCR time dropped      : {ms_dropped/1000:.0f}s / {ms_total/1000:.0f}s "
          f"({100*ms_dropped/ms_total:.1f}%)")
    print(f"weak-gate flips       : {len(weak_flips)}   (structurally must be 0)")
    print(f"newly truncated pages : {len(newly_truncated)}   "
          f"(the offline filter cannot answer these; a live regen must)")
    for stem, page_no, labels in newly_truncated[:10]:
        print(f"    {stem} p{page_no}: {labels}")
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
