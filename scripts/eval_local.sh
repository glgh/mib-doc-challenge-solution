#!/usr/bin/env bash
# Local eval loop: run the pipeline on the public train set and score it with the
# challenge's official scorer. Usage: scripts/eval_local.sh [output_dir]
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CH="$ROOT/../mib-doc-challenge"
OUT="${1:-$ROOT/output/eval}"
mkdir -p "$OUT"

START=$(date +%s)
"$ROOT/.venv/bin/python" "$ROOT/solution.py" "$CH/data/train" "$OUT/predictions.jsonl"
END=$(date +%s)
echo "pipeline wall-clock: $((END - START))s for $(wc -l < "$OUT/predictions.jsonl" | tr -d ' ') predictions"
echo

python3 "$CH/scripts/evaluate.py" \
  --truth "$CH/data/train_labels.csv" \
  --submission "$OUT/predictions.jsonl" \
  --output-json "$OUT/evaluation.json" \
  --case-scores-jsonl "$OUT/case_scores.jsonl"
EVAL_EXIT=$?
echo
"$ROOT/.venv/bin/python" "$ROOT/scripts/field_report.py" "$OUT"
exit $EVAL_EXIT
