#!/usr/bin/env python3
"""MIB doc challenge entrypoint: <input_pdf_dir> <output_predictions_path>.

Thin CLI over mib/runner.py, which sequences the stages (see mib/stages/). Set
MIB_DEBUG_JSONL=<path> to also write per-case diagnostics (branch, field
provenance, census) — predictions themselves stay schema-clean.
"""
import json
import multiprocessing
import os
import sys
import time
from pathlib import Path

from mib import config, corpus, emit, runner

WORKERS = int(os.environ.get("MIB_WORKERS", "4"))       # the contract gives 4 vCPU


def _safe_predict(pdf):
    """Never return nothing. A dropped case forfeits its extraction points and
    takes the missing-case penalty, where an unjustified NEEDS_REVIEW row still
    scores 2 raw points against a true APPROVED or DENIED and 8 against a true
    NEEDS_REVIEW.

    Stamps `cost_ms` (true per-case wall time inside this worker, OCR included)
    onto the debug dict — the only per-case timing on the container path, needed
    to measure the heavy tail (p99/max) under the Docker runtime contract. It is
    sidecar-only, so predictions stay schema-clean and replay debug is unchanged.
    """
    t0 = time.perf_counter()
    try:
        record, dbg = runner.predict(pdf)
    except Exception as exc:
        print(f"{pdf.name}: {type(exc).__name__}: {exc} — emitting fallback row",
              file=sys.stderr)
        record = emit.fallback_record(pdf.stem)
        dbg = {"case_id": pdf.stem, "branch": "error",
               "error": f"{type(exc).__name__}: {exc}"}
    dbg["cost_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return record, dbg


def _rewrite(path, rows):
    """Replace `path` with `rows`, atomically.

    Via a sibling temp file and os.replace, so a crash mid-write cannot leave a
    truncated submission where a complete provisional one used to be. Sibling
    rather than /tmp because os.replace is only atomic within one filesystem,
    and under the contract /tmp is a separate tmpfs from /output.
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("".join(json.dumps(r, sort_keys=True) + "\n"
                           for r in rows if r is not None))
    os.replace(tmp, path)


def main(input_dir, output_path):
    pdfs = sorted(Path(input_dir).glob("*.pdf"))
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    debug_path = os.environ.get("MIB_DEBUG_JSONL")

    # Stream, ordered, flushing each row. The contract stops containers that
    # exceed 30,000s and scores whatever output exists — which under pool.map
    # was an empty file, because it blocks until all 5,000 PDFs finish and only
    # then writes. imap preserves input order, so output stays deterministic.
    seen, written = set(), 0
    records, debugs = [], []
    debug_file = open(debug_path, "w") if debug_path else None
    try:
        with open(output, "w") as f, multiprocessing.Pool(WORKERS) as pool:
            for pdf, (record, dbg) in zip(pdfs, pool.imap(_safe_predict, pdfs)):
                if record is None:
                    record = emit.fallback_record(pdf.stem)
                if record["case_id"] in seen:
                    print(f"emit: duplicate case_id {record['case_id']} from "
                          f"{pdf.name} — dropping a case", file=sys.stderr)
                    continue
                seen.add(record["case_id"])
                f.write(json.dumps(record, sort_keys=True) + "\n")
                f.flush()
                written += 1
                records.append(record)
                debugs.append(dbg)
                if debug_file and dbg is not None:
                    debug_file.write(json.dumps(dbg, sort_keys=True) + "\n")
                    debug_file.flush()
    finally:
        if debug_file:
            debug_file.close()

    # Corpus-level pass, necessarily after the stream: a sponsor id can only be
    # recognized as a policy entity by how often it recurs, and that is unknown
    # until every case has been read (mib/corpus.py).
    #
    # Deliberately a *revision* of already-written rows rather than a barrier
    # before the first write. The streaming above exists because the contract
    # stops the container at 30,000s and scores whatever is on disk; holding all
    # output back until the corpus is complete would trade that safety away for
    # this. Killed early, the file is exactly what today's pipeline produces.
    try:
        new_ids, revised = corpus.revise(records, debugs)
        if revised:
            print(f"corpus: {len(new_ids)} recurring sponsor id(s) not in the known "
                  f"revoked set {sorted(new_ids)} — revised {revised} case(s)",
                  file=sys.stderr)
            _rewrite(output, records)
            if debug_path:
                _rewrite(Path(debug_path), debugs)
    except Exception as exc:  # noqa: BLE001
        # The provisional rows are already valid and scoreable; a failure here
        # must never cost the whole run.
        print(f"corpus: revision pass failed ({type(exc).__name__}: {exc}) — "
              f"keeping provisional output", file=sys.stderr)

    if debug_file:
        # Stamp the eval directory so metric scripts can refuse to join it
        # against a page-text cache built at a different config. Written beside
        # the debug sidecar rather than beside the predictions, so the container
        # writes nothing to /output but the schema-clean submission file.
        meta = config.stamp(artifact="eval", input_dir=str(input_dir),
                            n_pdfs=len(pdfs), n_records=written)
        (Path(debug_path).parent / "meta.json").write_text(
            json.dumps(meta, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: solution.py <input_pdf_dir> <output_path>")
    # Pin the start method: 3.14 defaults to spawn on macOS and forkserver on
    # Linux, so dev and the container would otherwise differ in how workers
    # inherit state — and this pipeline reads configuration from the environment.
    multiprocessing.set_start_method("spawn", force=True)
    main(sys.argv[1], sys.argv[2])
