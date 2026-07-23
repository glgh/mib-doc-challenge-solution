#!/usr/bin/env python3
"""Materialize stage-1/2 output (page text) for a PDF directory.

This is the expensive part of the pipeline (~95% of runtime) and it is
deterministic given the PDF and the render config, so it is worth writing down
once. Everything downstream — parse, merge, policy, confidence — can then be
re-run against this file in seconds instead of re-OCRing.

Per-case wall time is recorded so config choices can be made cost-aware; the
restoration ladder's tail is currently unmeasured.

The file carries a provenance header (mib/config.py) naming the render config
and code revision that produced it, because the whole value of the cache is that
other tools join against it — and a join across configs is wrong, not noisy.

Usage: scripts/dump_text.py [input_dir] [out.jsonl] [restore_level]
"""
import os
import sys
import time
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CH = ROOT.parent / "mib-doc-challenge"


def dump_one(pdf_path):
    from mib import cache, runner
    t0 = time.time()
    try:
        pages, reads = runner.read_case(pdf_path)
        err = None
    except Exception as exc:                      # keep going; record the failure
        pages, reads, err = [], {}, f"{type(exc).__name__}: {exc}"
    return {
        "stem": pdf_path.stem,
        "cost_ms": round((time.time() - t0) * 1000),
        "error": err,
        "pages": cache.from_case(pages, reads),
    }


def main(input_dir, out_path, restore):
    os.environ["MIB_RESTORE"] = restore        # inherited by the pool workers
    from mib import cache, config

    pdfs = sorted(Path(input_dir).glob("*.pdf"))
    out = Path(out_path)
    meta = config.stamp(artifact="page_text", input_dir=str(input_dir), n_pdfs=len(pdfs))
    t0 = time.time()
    done = 0
    with cache.open_write(out, meta) as f, Pool(4) as pool:
        for rec in pool.imap(dump_one, pdfs):     # ordered: deterministic output
            cache.append(f, rec)
            done += 1
            if done % 100 == 0:
                print(f"  {done}/{len(pdfs)}  {time.time() - t0:.0f}s", flush=True)

    _meta, records = cache.read(out)
    costs = sorted(r["cost_ms"] for r in records)
    n = len(costs)
    print(f"wrote {n} cases to {out} in {time.time() - t0:.0f}s "
          f"({config.describe(meta)})")
    print(f"per-case ms: p50={costs[n // 2]} p90={costs[int(n * .9)]} "
          f"p99={costs[int(n * .99)]} max={costs[-1]}")


if __name__ == "__main__":
    from mib.config import DEFAULT_RESTORE

    restore = sys.argv[3] if len(sys.argv) > 3 else DEFAULT_RESTORE
    main(sys.argv[1] if len(sys.argv) > 1 else CH / "data/train",
         sys.argv[2] if len(sys.argv) > 2 else ROOT / f"output/cache/train_{restore}.jsonl",
         restore)
