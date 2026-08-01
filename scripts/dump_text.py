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

By default this dumps only the hard iteration set (experiments/hard_set.txt,
~82 dev cases, ~a minute) — S2 changes are probed there first. A full-corpus
regen (--full, ~20 min at 4 workers; MIB_WORKERS=9 roughly halves it) is spent
rarely and only on user approval, once a change has proven itself on the subset.
Subset caches are stamped `subset=...` and their scores are NOT dev numbers
(score_split warns on a partial eval).

Usage: scripts/dump_text.py [input_dir] [out.jsonl] [--cases FILE | --full]
"""
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
        pages, reads_by_page = runner.read_case(pdf_path)
        err = None
    except Exception as exc:                      # keep going; record the failure
        pages, reads_by_page, err = [], {}, f"{type(exc).__name__}: {exc}"
    return {
        "stem": pdf_path.stem,
        "cost_ms": round((time.time() - t0) * 1000),
        "error": err,
        "pages": cache.from_case(pages, reads_by_page),
    }


def read_stems(cases_path):
    """Case stems from a list file — one per line, '#' comments and blanks skipped."""
    stems = set()
    for line in Path(cases_path).read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            stems.add(line)
    return stems


def main(input_dir, out_path, stems=None, subset_name=None):
    from mib import cache, config

    pdfs = sorted(Path(input_dir).glob("*.pdf"))
    if stems is not None:
        pdfs = [p for p in pdfs if p.stem in stems]
        missing = stems - {p.stem for p in pdfs}
        if missing:
            print(f"WARNING: {len(missing)} listed case(s) not in {input_dir}: "
                  f"{sorted(missing)[:5]}")
    out = Path(out_path)
    extra = {"subset": subset_name, "n_subset": len(pdfs)} if stems is not None else {}
    # Stamp the corpus by name, not by absolute path: the stamp is copied into
    # every artifact fitted from this cache (mib/confidence_table.meta.json ships
    # inside the image), and a developer's home directory has no business
    # travelling with it. The name still distinguishes train from validation.
    meta = config.stamp(artifact="page_text", input_dir=Path(input_dir).name,
                        n_pdfs=len(pdfs), **extra)
    t0 = time.time()
    done = 0
    with cache.open_write(out, meta) as f, Pool(config.workers()) as pool:
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
    import argparse

    from mib.config import RESTORE

    HARD_SET = ROOT / "experiments/hard_set.txt"

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("input_dir", nargs="?", default=CH / "data/train")
    ap.add_argument("out", nargs="?", default=None)
    ap.add_argument("--cases", help="stems list file (one per line, # comments)")
    ap.add_argument("--full", action="store_true",
                    help="dump the whole directory — needs user approval "
                         "(STATUS 'How to measure anything')")
    a = ap.parse_args()
    if a.full and a.cases:
        ap.error("--full and --cases are mutually exclusive")

    if a.full:
        stems, name = None, None
        out = a.out or ROOT / f"output/cache/train_{RESTORE}.jsonl"
        print("full-corpus regen — the rare, user-approved path")
    else:
        cases = Path(a.cases) if a.cases else HARD_SET
        stems, name = read_stems(cases), cases.stem
        out = a.out or ROOT / f"output/cache/{cases.stem}_{RESTORE}.jsonl"

    main(a.input_dir, out, stems=stems, subset_name=name)
