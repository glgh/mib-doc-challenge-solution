#!/usr/bin/env python3
"""The byte-identical gate for S1/S2 changes, which replay.py cannot cover.

`scripts/replay.py` starts from cached page text, so it proves nothing about the
code that *produces* that text. Any change to extraction or rendering needs the
opposite check: re-read real PDFs with the current code and compare against what
the cache recorded. Identical text means the change was behaviour-preserving.

Sampling is deliberately biased toward the pages that exercise the restoration
ladder — an S2 refactor that only handles clean text layers correctly would pass
a uniform sample and fail in production.

Usage: scripts/verify_render.py [cache.jsonl] [n_scans] [n_random]
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CH = ROOT.parent / "mib-doc-challenge"

from mib import cache, config  # noqa: E402


def main(cache_path, n_scans=4, n_random=2):
    import os

    meta, records = cache.read(cache_path)
    if meta and meta.get("restore"):
        # Compare like with like: the cache's config, not the current default.
        os.environ["MIB_RESTORE"] = meta["restore"]
    from mib import runner

    print(f"cache:   {config.describe(meta)}")
    print(f"reading: restore={config.restore_level()}\n")

    by_ocr = sorted((r for r in records if not r.get("error")),
                    key=lambda r: -sum(len(p["ocr_lines"]) for p in r["pages"]))
    if not by_ocr:
        raise SystemExit("cache has no usable records")
    mid = len(by_ocr) // 2
    sample = by_ocr[:n_scans] + by_ocr[mid:mid + n_random]

    bad = 0
    for rec in sample:
        pages, reads = runner.read_case(CH / f"data/train/{rec['stem']}.pdf")
        got = [(p["visible_lines"], p["hidden_lines"], p["ocr_lines"], p["image_count"])
               for p in cache.from_case(pages, reads)]
        want = [(p["visible_lines"], p["hidden_lines"], p["ocr_lines"], p["image_count"])
                for p in rec["pages"]]
        ok = got == want
        bad += not ok
        n_ocr = sum(len(p["ocr_lines"]) for p in rec["pages"])
        print(f"  {rec['stem']}  pages={len(pages):2d}  ocr_lines={n_ocr:3d}  "
              f"{'identical' if ok else 'DIFFERS'}")
        if not ok:
            for i, (g, w) in enumerate(zip(got, want)):
                if g != w:
                    print(f"      page {i} cached : {w[2][:3]}")
                    print(f"      page {i} current: {g[2][:3]}")

    print(f"\n{len(sample) - bad}/{len(sample)} identical")
    return 1 if bad else 0


if __name__ == "__main__":
    level = config.DEFAULT_RESTORE
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1
                  else ROOT / f"output/cache/train_{level}.jsonl",
                  int(sys.argv[2]) if len(sys.argv) > 2 else 4,
                  int(sys.argv[3]) if len(sys.argv) > 3 else 2))
