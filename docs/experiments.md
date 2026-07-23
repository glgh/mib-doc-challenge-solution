# Experiments log

One row per evaluated change. Score = deterministic total from the official `evaluate.py` on the public train set (1,000 cases). CFA = catastrophic false approvals (hard gate: must be 0 from Step 1 of the plan onward). Decision: keep / revert.

| # | Date | Commit | Change | Total | Class /80 | Extr /50 | Calib /20 | CFA | Wall | Decision |
| - | ---- | ------ | ------ | ----: | --------: | -------: | --------: | --: | ---: | -------- |
| 1 | 2026-07-21 | 5fc6e04 | v0 baseline: text-layer only, manual-digest rules, hand-set confidences | 91.91 | 50.51 | 30.84 | 10.55 | 52 | 14s | baseline |
| 2 | 2026-07-21 | 5f0af45 | Refactor into mib/ package (trust-boundary modules); behavior-identical | 91.91 | 50.51 | 30.84 | 10.55 | 52 | 14s | keep |
| 3 | 2026-07-21 | d1bf327 | Step-1 rules, all pre-validated via mine_signals.py: +3 inferred revoked sponsors, DIP-1 carve-out, embargo worlds (TRAPPIST-1e/Eris Relay always + flag inference; Wolf-1061c non-DIP), staleness vs 2026-07-07, waived-non-DIP → NR (killed DIP-WAIVER shortcut), B-13 census → NR per organizer ruling | 96.69 | 53.52 | 30.95 | 12.22 | **0** | 14s | keep |
| 4 | 2026-07-21 | bf28ae8 | Step-2 OCR: Tesseract PSM 11 (validated vs PSM 4/6) on scan-only pages, embedded-raster fast path + 200 DPI render fallback, vocab snapping (13 worlds / 12 species / 10 purposes from labels, guarded cutoffs), OCR docs rank below text-layer peers | 110.62 | 59.96 | 36.70 | 13.96 | 1 | 145s | fix CFA first |
| 5 | 2026-07-21 | bf28ae8 | OCR-tolerant KV parsing: fuzzy key match (cutoff 0.8) + separator [:.;] — MIB-000161 CFA was `Observed flags. active_warrant` (colon read as period) slipping past the strict parser | **112.12** | 60.72 | 37.32 | 14.08 | **0** | 148s | keep |

Row 5 clears the full interview bar: ≥105 total ✓, ≥55 classification ✓, 0 CFA ✓, 100% valid rows ✓. Watch item: risk_flags dipped 68.3→66.8 with fuzzy parsing (over-extraction somewhere) — residual-mining candidate. fee_status (55%) still worst field.

**Split change:** from row 6 on, scores are the frozen dev split (700 cases, `data_splits.json`); holdout (300) read only at milestones.

| # | Date | Commit | Change | Total | Class /80 | Extr /50 | Calib /20 | CFA | Wall | Decision |
| - | ---- | ------ | ------ | ----: | --------: | -------: | --------: | --: | ---: | -------- |
| 6 | 2026-07-21 | (step1) | Frozen 700/300 split + debug sidecar; dev baseline | 112.56 | 60.81 | 37.54 | 14.21 | 0 | 144s | baseline(dev) |
| 7 | 2026-07-21 | (step2) | Safe-deny evidence standards (positive preconditions, no fabricated revoked ids, verbatim unpaid, max-margin staleness) + **manual-correction overrides** (136 packets, 59/59 truth-consistent) | 113.41 | 60.86 | 38.32 | 14.23 | 0 | 150s | keep |
| 8 | 2026-07-22 | — | 300-DPI grayscale/autocontrast OCR retry pass | 113.62 | 61.03 | 38.35 | 14.24 | 0 | **6326s** | **revert** — +0.21 pts at 43x runtime, over 6s/PDF budget |
| 9 | 2026-07-22 | (step3) | Trimmed variant: recognized-keys selection between embedded/rendered passes only | 113.51 | 60.94 | 38.35 | 14.22 | 0 | 192s | keep |
| 10 | 2026-07-22 | (step5) | Fitted per-branch confidence (dev-empirical, Laplace k=10, clamp [0.05,0.95]) | **114.43** | 60.94 | 38.35 | **15.13** | 0 | 190s | keep — **holdout read #1: 113.46 total, calib 15.05, Brier 0.1239, 0 CFA; dev-holdout gap <1 pt, gate passed** |

Lesson from row 8, for the memo: OCR quality on this corpus is bimodal — pages either read fine at 200 DPI or are synthetically destroyed; spending compute between those modes buys almost nothing.

Worst fields at baseline: fee_status 44.3%, sponsor_id 49.4%, visa_class 51.4% (scan-only packets dominate misses). Confusion hotspots: 184 DENIED→NR, 142 APPROVED→NR (scan-only punts), 52 DENIED→APPROVED (CFAs).
