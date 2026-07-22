#!/usr/bin/env bash
# Local eval loop: run the pipeline on the public train set, score one split
# with the challenge's official scorer. Usage: scripts/eval_local.sh [dev|holdout|all]
# Holdout discipline: score holdout only at milestones (see docs/PLAN.md).
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CH="$ROOT/../mib-doc-challenge"
SPLIT="${1:-dev}"
OUT="$ROOT/output/eval"
mkdir -p "$OUT"

START=$(date +%s)
"$ROOT/.venv/bin/python" "$ROOT/solution.py" "$CH/data/train" "$OUT/predictions.jsonl"
END=$(date +%s)
echo "pipeline wall-clock: $((END - START))s for $(wc -l < "$OUT/predictions.jsonl" | tr -d ' ') predictions"
echo

"$ROOT/.venv/bin/python" "$ROOT/scripts/score_split.py" "$OUT" "$SPLIT"
