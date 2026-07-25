"""MIB doc challenge pipeline.

Stages run in order and do not call each other; `runner` sequences them and
`records` defines what crosses each seam:

    stages/extract  — S1 evidence intake: PDF → Page[] (injection quarantine)
    stages/render   — S2 OCR of scan-only pages → Read[]  (impure, expensive)
    parse           — S3 doc-type detection, key/value parsing, vocabularies
    packet          — S4 case assembly: active case id, census, field merge
    signals         — derived fraud signals (taxonomy: docs/BACKGROUND.md §3)
    policy          — S5 adjudication rule engine (named branches, manual order)
    confidence      — branch → calibrated confidence
    emit            — record assembly + output validation (schema safety net)

Supporting:

    records    — the dataclasses that cross the seams
    runner     — orchestration: read_case (S1+S2) and predict_from_evidence (rest)
    config     — what config this run is at, and provenance stamping
    cache      — provenance-stamped JSONL for materialized page text
    textmatch  — normalized matching, mirroring the scorer's comparison
    imaging    — geometric restoration primitives (skew, turn, band realign)
    vocab      — closed vocabularies and OCR repair

The seam between `render` and `parse` is the cache boundary: everything after it
is pure and cheap, so `scripts/replay.py` re-runs it from stored page text in
seconds instead of re-paying for OCR (~95% of runtime).
"""
