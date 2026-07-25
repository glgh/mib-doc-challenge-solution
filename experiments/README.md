# experiments/ — detector R&D before touching the pipeline

Progressive scan restoration (workstream memory + `experiments/findings.md`) replaces the
generate-and-test variant ensemble with **detect-then-repair**: measure the
damage, correct it, OCR once. That only works if the detectors are trustworthy —
a false positive corrupts the single image we OCR (MIB-000037 proved deshred
fires on form-rule structure and garbles a clean line).

So detectors are prototyped **here**, against a fixed labeled set, and only the
winner is promoted into `mib/imaging.py`. Nothing in this folder is imported by
the runtime pipeline.

## The hard-case set — `hard_cases.jsonl`

One JSON object per line. Fields:

- `case`, `page` — the PDF and page index.
- `track` — `geometry` (orientation/skew/shred — in scope for these detectors),
  `ocr` (upright & legible but tesseract fails — a *different* recovery track),
  or `triage` (root cause unconfirmed, needs eyeballing).
- `damage` — one-line description of what was done to the page.
- `expect` — the labels a correct detector must produce, e.g.
  `{"orientation": "turn1", "shred": false}`. `orientation` is `upright` |
  `turn1` (90° CW stored) | `turn3` (270°); `shred` is a boolean.
- `ev` — `base` (OCR evidence upright) and `best` (best variant) scores, from the
  census sweep, for context on how much is recoverable.
- `note` — why this case is here and what it tests.

The set is **append-only and reviewed**: every case was eyeballed, not scraped.
When a new failure is found, add a line with a note — don't delete unless it was
mislabeled. Seeded from the 2026-07 session that found the deshred false positive.

Key members to preserve:
- `MIB-000037 p0` — shred **negative** after turn (the false positive to defeat).
- `MIB-000221 p0`, `MIB-000189 p2` — orientation/shred negatives (must no-op).
- `MIB-000045 p2`, `MIB-000013 p2` — shred positives (`MIB-000085 p2` was
  relabeled shred-negative — see findings.md 2026-07-23 and `hard_cases.jsonl`).
- `MIB-000089 p1` (270), `MIB-000037 p0` (90), `MIB-000025 p4` (90) — orientation positives.
- `MIB-000165 p1` — deskew-vs-deshred order sensitivity.
- `MIB-000021 p2` — the `ocr`-track proof that "silent" ≠ "geometric".

## Probes

- `probe_shred.py` — dumps the per-row border-offset structure for every
  geometry-track case and scores candidate shred detectors against the `shred`
  labels. Run: `.venv/bin/python experiments/probe_shred.py`.

(Orientation needed no separate probe — the anisotropy detector was tuned via
`pipeline.py` + `repair_bench.py` to 13/13; see findings.md 2026-07-23.)

## Workflow

1. Add/confirm labeled cases in `hard_cases.jsonl`.
2. Prototype detector variants as functions in a `probe_*.py`; score against labels.
3. When one cleanly separates positives from negatives on the set, promote it to
   `mib/imaging.py` and wire it into `mib/stages/render.py`.
4. Re-run the probe after any `mib/imaging.py` change as a regression check.
