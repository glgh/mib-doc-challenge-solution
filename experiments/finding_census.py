#!/usr/bin/env python3
"""Is the adjudicator `Finding:` line trustworthy off ANY page, not just a page
that types as an adjudicator note?

`signals.adjudicator_finding` reads the note's `Finding: <DECISION>` line as
Tier-0 evidence (it passes straight through as the decision). It used to be gated
on the page typing DOC_ADJUDICATOR — but a skewed+shredded note (964 p1: +5°
skew) has its TITLE destroyed while the finding line still reads clean, so the
gate silently discarded correct rank-1 evidence. This census checks the premise
behind removing that gate: scan every page and every OCR variant for a `Finding:`
line (with the OCR-tolerant regex signals now ships), then ask whether the value
is unanimous within a case and whether it matches the label.

Result on the shipped grid cache (output/cache/train_grid.jsonl):
  333/333 cases with a Finding line are unanimous AND == truth (zero mismatches),
  and 9 carry a finding no adjudicator-TYPED read holds — the title-gate misses:
  105/141/390/608/615 (DENIED), 142/900/964/986 (NEEDS_REVIEW). 141 was a live
  false NEEDS_REVIEW on a truth-DENIED case.

Run: .venv/bin/python experiments/finding_census.py [cache.jsonl]
"""
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CH = ROOT.parent / "mib-doc-challenge"

from mib import parse                      # noqa: E402
from mib.signals import _finding_in        # noqa: E402  (the shipped extractor)


def main(cache=ROOT / "output/cache/train_grid.jsonl"):
    truth = {r["case_id"]: r["adjudication"]
             for r in csv.DictReader(open(CH / "data/train_labels.csv"))}
    recs = (json.loads(l) for l in open(cache))

    with_finding = unanimous = matches = 0
    title_gated = []                        # finding present, no adjudicator-typed read
    mismatches = []
    for r in recs:
        stem = r.get("stem")
        if not stem:
            continue                        # meta line
        findings, typed_adjud = set(), False
        for p in r["pages"]:
            reads = [p.get("visible_lines") or []] + \
                    [rd["lines"] for rd in (p.get("reads") or [])]
            for lines in reads:
                found = _finding_in(lines)
                if found:
                    findings.add(found)
                    if parse.detect_doc_type(lines) == parse.DOC_ADJUDICATOR:
                        typed_adjud = True
        if not findings:
            continue
        with_finding += 1
        if len(findings) == 1:
            unanimous += 1
            value = next(iter(findings))
            if value == truth.get(stem):
                matches += 1
            else:
                mismatches.append((stem, value, truth.get(stem)))
            if not typed_adjud:
                title_gated.append((stem, value, truth.get(stem)))
        else:
            mismatches.append((stem, sorted(findings), truth.get(stem)))

    print(f"cases with a Finding line:   {with_finding}")
    print(f"  unanimous across reads:    {unanimous}/{with_finding}")
    print(f"  unanimous AND == truth:    {matches}/{unanimous}")
    print(f"  mismatches:                {mismatches}")
    print(f"\ntitle-gated (finding present, NO adjudicator-typed read): "
          f"{len(title_gated)}")
    for stem, value, t in sorted(title_gated):
        print(f"  {stem}: finding={value:12s} truth={t}")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else main.__defaults__[0])
