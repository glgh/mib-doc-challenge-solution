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
from pathlib import Path

from mib import config, emit, runner

WORKERS = int(os.environ.get("MIB_WORKERS", "4"))       # the contract gives 4 vCPU


def _safe_predict(pdf):
    """Never return nothing. A dropped case forfeits its extraction points and
    takes the missing-case penalty, where an unjustified NEEDS_REVIEW row still
    scores 2 raw points against a true APPROVED or DENIED and 8 against a true
    NEEDS_REVIEW."""
    try:
        return runner.predict(pdf)
    except Exception as exc:
        print(f"{pdf.name}: {type(exc).__name__}: {exc} — emitting fallback row",
              file=sys.stderr)
        return emit.fallback_record(pdf.stem), {"case_id": pdf.stem,
                                                "branch": "error",
                                                "error": f"{type(exc).__name__}: {exc}"}


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
                if debug_file and dbg is not None:
                    debug_file.write(json.dumps(dbg, sort_keys=True) + "\n")
                    debug_file.flush()
    finally:
        if debug_file:
            debug_file.close()

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
