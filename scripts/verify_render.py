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
    meta, records = cache.read(cache_path)
    # The restoration level is no longer selectable, so a cache built at another
    # level cannot be reproduced and any diff against it would be the config
    # talking, not the code. Refuse rather than report a meaningless mismatch.
    stamped = (meta or {}).get("restore")
    if stamped and stamped != config.RESTORE:
        raise SystemExit(
            f"cache was built at restore={stamped!r} but this code only produces "
            f"{config.RESTORE!r}; rebuild it with scripts/dump_text.py before comparing.")
    # The stored `ocr_lines` is the primary the cache's OWN selection metric
    # picked, so compare under that metric — otherwise a select-default flip
    # reads as an S2 diff (it did: 1/6 vs 6/6 on identical reads). Caches
    # stamped before `select` existed were all written under ev.
    import os
    os.environ["MIB_SELECT"] = (meta or {}).get("select") or "ev"
    from mib import runner

    print(f"cache:   {config.describe(meta)}")
    print(f"reading: restore={config.RESTORE}\n")

    by_ocr = sorted((r for r in records if not r.get("error")),
                    key=lambda r: -sum(len(p["ocr_lines"]) for p in r["pages"]))
    if not by_ocr:
        raise SystemExit("cache has no usable records")
    mid = len(by_ocr) // 2
    sample = by_ocr[:n_scans] + by_ocr[mid:mid + n_random]

    # Current cache format only (reads + struck + schema-4 conf). The
    # progressive tolerate-missing-keys scaffolding for pre-ensemble /
    # pre-strike / pre-conf caches is gone (user call, 2026-07-26: old caches
    # are not worth carrying — regenerate instead of accommodating).
    if any("reads" not in p or "struck" not in p
           for rec in sample for p in rec["pages"]):
        raise SystemExit("cache predates the ensemble/struck format; "
                         "regenerate it with scripts/dump_text.py.")

    def norm_reads(reads):
        # Fixed identity keys only. `cost_ms` (schema 5) is wall clock —
        # nondeterministic by construction, comparing it would fail every run;
        # a schema-4 cache simply lacks it. Conf tuples JSON-round-trip as
        # lists; live reads carry tuples.
        return [{"variant": r.get("variant", ""), "quality": r.get("quality", 0.0),
                 "lines": r["lines"],
                 "conf": None if r.get("conf") is None else
                 [tuple(t) for t in r["conf"]]} for r in reads]

    def page_key(p):
        return (p["visible_lines"], p["hidden_lines"], p["ocr_lines"],
                p["image_count"], norm_reads(p["reads"]), p["struck"])

    bad = 0
    for rec in sample:
        pages, reads_by_page = runner.read_case(CH / f"data/train/{rec['stem']}.pdf")
        got = [page_key(p) for p in cache.from_case(pages, reads_by_page)]
        want = [page_key(p) for p in rec["pages"]]
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
    level = config.RESTORE
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1
                  else ROOT / f"output/cache/train_{level}.jsonl",
                  int(sys.argv[2]) if len(sys.argv) > 2 else 4,
                  int(sys.argv[3]) if len(sys.argv) > 3 else 2))
