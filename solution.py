#!/usr/bin/env python3
"""MIB doc challenge entrypoint: <input_pdf_dir> <output_predictions_path>.

Thin orchestrator; stages live in mib/ (see mib/__init__.py for the layout).
Set MIB_DEBUG_JSONL=<path> to also write per-case diagnostics (branch, field
provenance, census) — predictions themselves stay schema-clean.
"""
import json
import os
import sys
from multiprocessing import Pool
from pathlib import Path

from mib import confidence, emit, packet, pdfio, policy, signals


def predict(pdf_path):
    pages = pdfio.read_pages(pdf_path)
    pkt = packet.assemble(pages, fallback_case_id=pdf_path.stem)
    provenance = {}
    values = packet.merge_fields(pkt, provenance)
    sig = signals.derive(pkt, values)
    decision, branch = policy.adjudicate(values, sig)
    conf = confidence.for_branch(branch)
    record = emit.build_record(pkt.case_id, values, sig["flags"], decision, conf)
    debug = {
        "case_id": pkt.case_id,
        "branch": branch,
        "provenance": {k: list(v) for k, v in provenance.items()},
        "doc_types": sorted({d for d, _, _ in pkt.docs}),
        "scan_only_pages": pkt.scan_only_pages,
        "has_biometric": sig["has_biometric"],
        "flags": sorted(sig["flags"]),
        "finding": sig["finding"],
        "waiver_code": sig["waiver_code"],
        "registry_status": (pkt.registry.get("registry_status") or "").strip().upper(),
        "n_pages": len(pages),
        "hidden_lines": sum(len(p.hidden_lines) for p in pages),
        "n_fields_missing": sum(1 for f in packet.parse.FIELDS if not values.get(f)),
        "n_corrections": len(packet.manual_corrections(pkt)),
        "rules_decision": decision,
    }
    return record, debug


def _safe_predict(pdf):
    try:
        return predict(pdf)
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


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: solution.py <input_pdf_dir> <output_path>")
    main(sys.argv[1], sys.argv[2])
