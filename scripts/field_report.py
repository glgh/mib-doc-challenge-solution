#!/usr/bin/env python3
"""Post-eval report: per-field match rates and CFA case list from case_scores.jsonl."""
import json
import sys
from collections import Counter
from pathlib import Path


def main(out_dir, split=None):
    out = Path(out_dir)
    name = f"case_scores_{split}.jsonl" if split else "case_scores.jsonl"
    cases = [json.loads(line) for line in (out / name).read_text().splitlines()]

    field_hits, field_totals = Counter(), Counter()
    cfas, wrong_by_reason = [], Counter()
    for c in cases:
        for field, res in c["field_results"].items():
            if res["status"] == "not_scorable_unrecoverable":
                continue
            field_totals[field] += 1
            field_hits[field] += res["status"] == "matched"
        wrong_by_reason[c["classification_reason"]] += 1
        if c["catastrophic_false_approval"]:
            cfas.append(c["case_id"])

    print("Per-field match rates:")
    for field in sorted(field_totals, key=lambda f: field_hits[f] / field_totals[f]):
        print(f"  {field:18s} {field_hits[field]:4d}/{field_totals[field]} "
              f"({field_hits[field] / field_totals[field]:.1%})")
    print("Classification reasons:", dict(wrong_by_reason.most_common()))
    print(f"CFAs ({len(cfas)}):", " ".join(cfas[:25]), "..." if len(cfas) > 25 else "")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "output/eval",
         sys.argv[2] if len(sys.argv) > 2 else None)
