#!/usr/bin/env python3
"""MIB doc challenge entrypoint: <input_pdf_dir> <output_predictions_path>.

Thin CLI over mib/runner.py, which sequences the stages (see mib/stages/). Set
MIB_DEBUG_JSONL=<path> to also write per-case diagnostics (branch, field
provenance, census) — predictions themselves stay schema-clean.
"""
import json
import os
import sys
from multiprocessing import Pool
from pathlib import Path

from mib import config, emit, runner


def _safe_predict(pdf):
    try:
        return runner.predict(pdf)
    except Exception as exc:
        print(f"skipping {pdf.name}: {exc}", file=sys.stderr)
        return None, None


def main(input_dir, output_path):
    pdfs = sorted(Path(input_dir).glob("*.pdf"))
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with Pool(4) as pool:
        results = pool.map(_safe_predict, pdfs)
    records = emit.dedupe([r for r, _ in results])
    with open(output, "w") as f:
        for record in records:
            f.write(json.dumps(record, sort_keys=True) + "\n")
    debug_path = os.environ.get("MIB_DEBUG_JSONL")
    if debug_path:
        with open(debug_path, "w") as f:
            for _, dbg in results:
                if dbg is not None:
                    f.write(json.dumps(dbg, sort_keys=True) + "\n")
        # Stamp the eval directory so metric scripts can refuse to join it
        # against a page-text cache built at a different config. Written beside
        # the debug sidecar rather than beside the predictions, so the container
        # writes nothing to /output but the schema-clean submission file.
        meta = config.stamp(artifact="eval", input_dir=str(input_dir),
                            n_pdfs=len(pdfs), n_records=len(records))
        (Path(debug_path).parent / "meta.json").write_text(
            json.dumps(meta, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: solution.py <input_pdf_dir> <output_path>")
    main(sys.argv[1], sys.argv[2])
