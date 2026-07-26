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

    # Pre-ensemble caches lack the per-page `reads` list; pre-strike caches lack
    # `struck`; pre-conf caches lack `conf` inside each read. Against those the
    # comparison drops the missing key rather than false-positiving on
    # None-vs-value (still a real S1/S2 check on the rest).
    with_reads = all("reads" in p for rec in sample for p in rec["pages"])
    with_struck = all("struck" in p for rec in sample for p in rec["pages"])
    with_conf = with_reads and all(
        "conf" in r for rec in sample for p in rec["pages"] for r in (p["reads"] or []))
    # Conf entries widened over time (schema 3: 3-tuples; schema 4: +line text).
    # Compare at the cache's arity so a schema-3 cache still verifies the numeric
    # part instead of false-positiving on the added text field.
    conf_arity = min((len(t) for rec in sample for p in rec["pages"]
                      for r in (p["reads"] or []) for t in (r.get("conf") or [])),
                     default=None) if with_conf else None

    def norm_reads(reads):
        if reads is None:
            return reads
        if not with_conf:
            return [{k: v for k, v in r.items() if k != "conf"} for r in reads]
        return [{**r, "conf": None if r.get("conf") is None else
                 [tuple(t[:conf_arity]) for t in r["conf"]]} for r in reads]

    def page_key(p):
        base = (p["visible_lines"], p["hidden_lines"], p["ocr_lines"], p["image_count"])
        base += ((norm_reads(p.get("reads")),) if with_reads else ())
        return base + ((p.get("struck"),) if with_struck else ())

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
