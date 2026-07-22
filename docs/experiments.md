# Experiments log

One row per evaluated change. Score = deterministic total from the official `evaluate.py` on the public train set (1,000 cases). CFA = catastrophic false approvals (hard gate: must be 0 from Step 1 of the plan onward). Decision: keep / revert.

| # | Date | Commit | Change | Total | Class /80 | Extr /50 | Calib /20 | CFA | Wall | Decision |
| - | ---- | ------ | ------ | ----: | --------: | -------: | --------: | --: | ---: | -------- |
| 1 | 2026-07-21 | 5fc6e04 | v0 baseline: text-layer only, manual-digest rules, hand-set confidences | 91.91 | 50.51 | 30.84 | 10.55 | 52 | 14s | baseline |
| 2 | 2026-07-21 | 5f0af45 | Refactor into mib/ package (trust-boundary modules); behavior-identical | 91.91 | 50.51 | 30.84 | 10.55 | 52 | 14s | keep |
| 3 | 2026-07-21 | d1bf327 | Step-1 rules, all pre-validated via mine_signals.py: +3 inferred revoked sponsors, DIP-1 carve-out, embargo worlds (TRAPPIST-1e/Eris Relay always + flag inference; Wolf-1061c non-DIP), staleness vs 2026-07-07, waived-non-DIP → NR (killed DIP-WAIVER shortcut), B-13 census → NR per organizer ruling | 96.69 | 53.52 | 30.95 | 12.22 | **0** | 14s | keep |
| 4 | 2026-07-21 | 9f28981 | Step-2 OCR: Tesseract PSM 11 (validated vs PSM 4/6) on scan-only pages, embedded-raster fast path + 200 DPI render fallback, vocab snapping (13 worlds / 12 species / 10 purposes from labels, guarded cutoffs), OCR docs rank below text-layer peers | 110.62 | 59.96 | 36.70 | 13.96 | 1 | 145s | fix CFA first |
| 5 | 2026-07-21 | 9f28981 | OCR-tolerant KV parsing: fuzzy key match (cutoff 0.8) + separator [:.;] — MIB-000161 CFA was `Observed flags. active_warrant` (colon read as period) slipping past the strict parser | **112.12** | 60.72 | 37.32 | 14.08 | **0** | 148s | keep |

Row 5 clears the full interview bar: ≥105 total ✓, ≥55 classification ✓, 0 CFA ✓, 100% valid rows ✓. Watch item: risk_flags dipped 68.3→66.8 with fuzzy parsing (over-extraction somewhere) — residual-mining candidate. fee_status (55%) still worst field.

Worst fields at baseline: fee_status 44.3%, sponsor_id 49.4%, visa_class 51.4% (scan-only packets dominate misses). Confusion hotspots: 184 DENIED→NR, 142 APPROVED→NR (scan-only punts), 52 DENIED→APPROVED (CFAs).
