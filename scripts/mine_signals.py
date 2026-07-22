#!/usr/bin/env python3
"""Dump per-case pipeline signals joined with train labels for rule validation.

Usage: .venv/bin/python scripts/mine_signals.py  →  output/signals.jsonl
Each row: case_id, extracted values, derived signals, truth label fields.
"""
import csv
import json
import sys
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mib import packet, pdfio, policy, signals  # noqa: E402

CH = Path(__file__).resolve().parent.parent.parent / "mib-doc-challenge"


def row(pdf_path):
    try:
        pages = pdfio.read_pages(pdf_path)
        pkt = packet.assemble(pages, fallback_case_id=pdf_path.stem)
        values = packet.merge_fields(pkt)
        sig = signals.derive(pkt, values)
        decision, branch = policy.adjudicate(values, sig)
        return {
            "case_id": pkt.case_id,
            **{f"x_{k}": v for k, v in values.items()},
            "x_flags": "|".join(sorted(sig["flags"])) or "none",
            "waiver_code": sig["waiver_code"],
            "has_biometric": sig["has_biometric"],
            "scan_only_pages": sig["scan_only_pages"],
            "n_pages": len(pages),
            "n_docs": len(pkt.docs),
            "finding": sig["finding"] or "",
            "pred": decision,
            "branch": branch,
        }
    except Exception as exc:
        return {"case_id": pdf_path.stem, "error": str(exc)}


def main():
    truth = {r["case_id"]: r for r in csv.DictReader(open(CH / "data/train_labels.csv"))}
    pdfs = sorted((CH / "data/train").glob("*.pdf"))
    with Pool(4) as pool:
        rows = pool.map(row, pdfs)
    out = Path(__file__).resolve().parent.parent / "output/signals.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        for r in rows:
            t = truth.get(r["case_id"], {})
            r.update({f"t_{k}": v for k, v in t.items() if k != "case_id"})
            f.write(json.dumps(r) + "\n")
    print(f"wrote {len(rows)} rows to {out}")


if __name__ == "__main__":
    main()
