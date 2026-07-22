#!/usr/bin/env python3
"""MIB doc challenge entrypoint: <input_pdf_dir> <output_predictions_path>.

Thin orchestrator; stages live in mib/ (see mib/__init__.py for the layout).
"""
import json
import sys
from multiprocessing import Pool
from pathlib import Path

from mib import confidence, emit, packet, pdfio, policy, signals


def predict(pdf_path):
    pages = pdfio.read_pages(pdf_path)
    pkt = packet.assemble(pages, fallback_case_id=pdf_path.stem)
    values = packet.merge_fields(pkt)
    sig = signals.derive(pkt, values)
    decision, branch = policy.adjudicate(values, sig)
    conf = confidence.for_branch(branch)
    return emit.build_record(pkt.case_id, values, sig["flags"], decision, conf)


def _safe_predict(pdf):
    try:
        return predict(pdf)
    except Exception as exc:
        print(f"skipping {pdf.name}: {exc}", file=sys.stderr)
        return None


def main(input_dir, output_path):
    pdfs = sorted(Path(input_dir).glob("*.pdf"))
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with Pool(4) as pool:
        records = pool.map(_safe_predict, pdfs)
    with open(output, "w") as f:
        for record in emit.dedupe(records):
            f.write(json.dumps(record, sort_keys=True) + "\n")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: solution.py <input_pdf_dir> <output_path>")
    main(sys.argv[1], sys.argv[2])
