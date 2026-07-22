# Experiments log

One row per evaluated change. Score = deterministic total from the official `evaluate.py` on the public train set (1,000 cases). CFA = catastrophic false approvals (hard gate: must be 0 from Step 1 of the plan onward). Decision: keep / revert.

| # | Date | Commit | Change | Total | Class /80 | Extr /50 | Calib /20 | CFA | Wall | Decision |
| - | ---- | ------ | ------ | ----: | --------: | -------: | --------: | --: | ---: | -------- |
| 1 | 2026-07-21 | 5fc6e04 | v0 baseline: text-layer only, manual-digest rules, hand-set confidences | 91.91 | 50.51 | 30.84 | 10.55 | 52 | 14s | baseline |
| 2 | 2026-07-21 | 5f0af45 | Refactor into mib/ package (trust-boundary modules); behavior-identical | 91.91 | 50.51 | 30.84 | 10.55 | 52 | 14s | keep |
| 3 | 2026-07-21 | (next) | Step-1 rules, all pre-validated via mine_signals.py: +3 inferred revoked sponsors, DIP-1 carve-out, embargo worlds (TRAPPIST-1e/Eris Relay always + flag inference; Wolf-1061c non-DIP), staleness vs 2026-07-07, waived-non-DIP → NR (killed DIP-WAIVER shortcut), B-13 census → NR per organizer ruling | 96.69 | 53.52 | 30.95 | 12.22 | **0** | 14s | keep |

Worst fields at baseline: fee_status 44.3%, sponsor_id 49.4%, visa_class 51.4% (scan-only packets dominate misses). Confusion hotspots: 184 DENIED→NR, 142 APPROVED→NR (scan-only punts), 52 DENIED→APPROVED (CFAs).
