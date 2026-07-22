#!/usr/bin/env python3
"""Score an existing predictions.jsonl against one split (dev/holdout/all).

Filters both truth CSV and predictions to the split's case ids (evaluate.py
exits 2 on extra cases), runs the official scorer, then the field report.
Usage: score_split.py <eval_dir> [dev|holdout|all]
"""
import csv
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CH = ROOT.parent / "mib-doc-challenge"


def main(eval_dir, split="dev"):
    eval_dir = Path(eval_dir)
    if split == "all":
        ids = None
    else:
        ids = set(json.loads((ROOT / "data_splits.json").read_text())[split])

    truth_rows = list(csv.DictReader(open(CH / "data/train_labels.csv")))
    preds = [json.loads(l) for l in (eval_dir / "predictions.jsonl").read_text().splitlines()]
    if ids is not None:
        truth_rows = [r for r in truth_rows if r["case_id"] in ids]
        preds = [p for p in preds if p["case_id"] in ids]

    truth_path = eval_dir / f"truth_{split}.csv"
    with open(truth_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=truth_rows[0].keys())
        w.writeheader()
        w.writerows(truth_rows)
    pred_path = eval_dir / f"predictions_{split}.jsonl"
    pred_path.write_text("".join(json.dumps(p, sort_keys=True) + "\n" for p in preds))

    print(f"== split: {split} ({len(truth_rows)} cases) ==")
    rc = subprocess.run([
        sys.executable, str(CH / "scripts/evaluate.py"),
        "--truth", str(truth_path), "--submission", str(pred_path),
        "--output-json", str(eval_dir / f"evaluation_{split}.json"),
        "--case-scores-jsonl", str(eval_dir / f"case_scores_{split}.jsonl"),
    ]).returncode
    subprocess.run([sys.executable, str(ROOT / "scripts/field_report.py"),
                    str(eval_dir), split])
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "dev"))
