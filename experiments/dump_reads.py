#!/usr/bin/env python3
"""Ensemble page-text cache — the whole OCR ensemble, not just best()'s winner.

`scripts/dump_text.py` persists one line-list per scan page (`best_lines`), which
is the exact winner-take-all collapse a merge is meant to undo — so every merge
experiment had to re-OCR from the PDFs (minutes). This dumps EVERY reading per
scan page ONCE, so `best` / `union` / field-vote strategies replay downstream in
seconds with zero tesseract.

Full-core by default (`config.workers`, overridable with MIB_WORKERS); honours and
STAMPS `MIB_OCR_OPTICAL` (the ensemble contents depend on it), so a dump can never
be silently mixed with one built at a different optical setting.

Usage:
  experiments/dump_reads.py                         # dump the hard-set cases
  experiments/dump_reads.py --cases MIB-000047,...  [out.jsonl]
  experiments/dump_reads.py --replay <cache.jsonl> [out]    # reconstruct + predict, timed
"""
import os
import sys
import time
from multiprocessing import Pool, cpu_count
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CH = ROOT.parent / "mib-doc-challenge"

from mib import cache, config, records, runner  # noqa: E402
from mib.stages import extract, render          # noqa: E402


def _hard_cases():
    """Unique case ids appearing in experiments/hard_pages.txt (where merge bites)."""
    seen = []
    for line in (ROOT / "experiments/hard_pages.txt").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            cid = line.split()[0]
            if cid not in seen:
                seen.append(cid)
    return seen


def dump_one(cid):
    """Every page's text + every OCR reading of every scan page, for one case."""
    t0 = time.time()
    pages_out, err = [], None
    try:
        with extract.open_document(CH / f"data/train/{cid}.pdf") as doc:
            for pt in extract.pages(doc):
                rec = {"page_no": pt.page_no, "visible_lines": pt.visible_lines,
                       "hidden_lines": pt.hidden_lines, "image_count": pt.image_count,
                       "reads": None}
                if pt.is_scan_only:
                    rec["reads"] = [{"variant": r.variant, "quality": r.quality,
                                     "lines": r.lines}
                                    for r in render.reads_for(doc, doc[pt.page_no], pt.page_no)]
                pages_out.append(rec)
    except Exception as exc:                       # keep going; record the failure
        err = f"{type(exc).__name__}: {exc}"
    return {"stem": cid, "cost_ms": round((time.time() - t0) * 1000),
            "error": err, "pages": pages_out}


# --- reconstruction: cache record -> (pages, reads_by_page) -----------------

def _reads(page_rec):
    return [records.Read(page_no=page_rec["page_no"], lines=r["lines"],
                         variant=r["variant"], quality=r["quality"])
            for r in (page_rec["reads"] or [])]


def reconstruct(case_rec):
    """Rebuild (pages, reads_by_page) so runner.predict_from_evidence can run.

    Since the keystone (ensemble across the seam), selection and per-field
    merging live in the pipeline itself, so a replay just hands over the stored
    ensemble; the strategy machinery this module used to carry moved into
    packet.merge_fields / signals."""
    pages, reads_by_page = [], {}
    for p in case_rec["pages"]:
        pages.append(records.Page(page_no=p["page_no"], visible_lines=list(p["visible_lines"]),
                                  hidden_lines=list(p["hidden_lines"]), image_count=p["image_count"]))
        rs = _reads(p) if p["reads"] is not None else []
        if rs:
            reads_by_page[p["page_no"]] = rs
    return pages, reads_by_page


def do_dump(cases, out):
    meta = config.stamp(artifact="reads_ensemble", n_cases=len(cases))
    n = config.workers(default=max(1, cpu_count() - 1))
    print(f"dumping {len(cases)} cases with {n} workers "
          f"(optical={'on' if config.ocr_optical() else 'off'}) -> {out}", flush=True)
    t0 = time.time()
    with cache.open_write(out, meta) as f, Pool(n) as pool:
        for i, rec in enumerate(pool.imap(dump_one, cases), 1):
            cache.append(f, rec)
            if i % 20 == 0:
                print(f"  {i}/{len(cases)}  {time.time() - t0:.0f}s", flush=True)
    print(f"wrote {len(cases)} cases in {time.time() - t0:.0f}s ({config.describe(meta)})")


def do_replay(cache_path, out_pred):
    meta, records_ = cache.read(cache_path)
    print(f"replaying {len(records_)} cases through the live pipeline "
          f"(no OCR) — cache {config.describe(meta)}", flush=True)
    t0 = time.time()
    import json
    with open(out_pred, "w") as f:
        for rec in records_:
            pages, reads_by_page = reconstruct(rec)
            record, _dbg = runner.predict_from_evidence(pages, reads_by_page, rec["stem"])
            f.write(json.dumps(record, sort_keys=True) + "\n")
    print(f"replayed in {time.time() - t0:.1f}s -> {out_pred}  "
          f"({len(records_)} cases, {1000 * (time.time() - t0) / max(1, len(records_)):.0f} ms/case)")


def main():
    args = sys.argv[1:]
    if args and args[0] == "--replay":
        cache_path = Path(args[1])
        out_pred = Path(args[2]) if len(args) > 2 else cache_path.with_suffix(".pred.jsonl")
        do_replay(cache_path, out_pred)
        return
    cases = None
    out = ROOT / "output/cache/reads_hard.jsonl"
    if "--cases" in args:
        i = args.index("--cases")
        cases = args[i + 1].split(",")
        args = args[:i] + args[i + 2:]
    if args:
        out = Path(args[0])
    out.parent.mkdir(parents=True, exist_ok=True)
    do_dump(cases or _hard_cases(), out)


if __name__ == "__main__":
    main()
