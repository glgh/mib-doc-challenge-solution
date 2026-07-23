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

**Split note:** rows 11–14 are a single cumulative A/B of `MIB_RESTORE` (geometric scan restoration, see [BACKGROUND.md](BACKGROUND.md) §4). Row 11 re-baselines: `ocr_page` now selects candidates by `evidence_score` (labels *plus* well-formed values) instead of `recognized_keys`, and stops at `GOOD_ENOUGH` rather than on the first page with any label — worth +0.07 on its own, so rows 12–14 are attributable to geometry.

| # | Date | Commit | Change | Total | Class /80 | Extr /50 | Calib /20 | CFA | Wall | Decision |
| - | ---- | ------ | ------ | ----: | --------: | -------: | --------: | --: | ---: | -------- |
| 11 | 2026-07-22 | (step6) | `MIB_RESTORE=off` — value-aware candidate selection, no geometry; re-baseline | 114.50 | 60.94 | 38.53 | 15.03 | 0 | ~~295s~~ | baseline(dev) |
| 12 | 2026-07-22 | (step6) | `=skew` — projection-profile deskew (±8°, 0.25° steps) on weak pages | 115.20 | 61.27 | 38.76 | 15.16 | 0 | 1101s † | keep |
| 13 | 2026-07-22 | (step6) | `=turn` — + 90°/270° quarter turns on pages reading zero evidence (180° never wins) | 116.59 | 61.79 | 39.58 | 15.22 | 0 | ~~927s~~ | keep |
| 14 | 2026-07-22 | (step6) | `=bands` — + shredder band realignment keyed off the constant-width page border | **116.88** | 61.97 | 39.62 | **15.30** | 0 | ~~608s~~ | keep — **flag-gated, experimental until P3** |

Row 14 is +2.38 over row 11 (+2.45 over row 10's 114.43) with CFA still 0, and Brier 0.1243 → 0.1176.

Lesson superseding row 8: the pages that failed OCR were not low-resolution, they were turned, skewed, and shredded. Row 8 spent 43x runtime on the resolution axis and bought +0.21; ~5 ms of numpy on the geometry axis buys +2.38. **Diagnose the transform before scaling the compute.**

**Correction (2026-07-22, row 15 work): the struck-through wall figures above are wrong, and one conclusion drawn from them is withdrawn.** They were laptop seconds recorded with other jobs running, and they are not mutually comparable.

† is the best figure currently available, and it is still not a clean one: `scripts/dump_text.py` at `skew` over all 1,000 train PDFs on 4 workers — 1101s wall, mean 4.40s/case, p50 1.6s / p90 9.8s / p99 57.5s / max 107s, projecting to **1.53 h** for 5,000 PDFs against the 30,000 s (8.3 h) budget. Caveats that must travel with that number:

- **The machine was not idle** (a game was using ~47% of CPU throughout), so it is an upper bound on a quiet host, not a clean measurement.
- Wall-derived and per-case-derived projections agreeing at 1.53 h is *not* an independent cross-check — `cost_ms` is per-case wall time, so both are the same contended measurement seen two ways.
- It is a 10-core macOS laptop running 4 workers, not the contract's 4 vCPU Linux container. **The only figure that decides whether a config ships is `scripts/run_docker_submission.py` under the real limits, which has never been run.** That is the gap to close before trusting any of this.

Withdrawn: *"wall-clock falls as restoration deepens."* That read 1280 → 927 → 608 down the struck-through column, and those three numbers do not support it. Per-case cost at `turn` and `bands` is still unmeasured on any sample worth quoting (the `bands` figure quoted elsewhere came from a 12-case dump). Deeper restoration adds OCR passes per page, so the prior should be that cost *rises* with depth until measured otherwise — which is the assumption P3 is planned against.

---

| # | Date | Commit | Change | Total | Class /80 | Extr /50 | Calib /20 | CFA | Wall | Decision |
| - | ---- | ------ | ------ | ----: | --------: | -------: | --------: | --: | ---: | -------- |
| 15 | 2026-07-22 | (P0.5) | **Ship `skew` as the default** (`mib/config.py`, pinned in `Dockerfile`) + staged-pipeline instruments: provenance stamping, page-text cache, replay gate, characterization suite | **115.20** | 61.27 | 38.76 | 15.16 | 0 | contended | keep |

Row 15 is +0.70 over row 11 and banks a result rows 11–14 had already measured: the shipped artifact was still `off`, so a container built before this row scored 114.50 while the log's headline was 116.88. Only the default moved. Every other change in this row is verified **byte-identical** to row 12: `scripts/replay.py` reproduces all 1,000 cached cases exactly, a full re-run reproduces `output/ab_skew/predictions.jsonl` exactly, and the `mib/config.py` extraction of the `MIB_RESTORE` lookup out of `mib/ocr.py` reproduces page text exactly on the most OCR-heavy scans. `turn`/`bands` remain reachable but unbanked: they cost 8–10× the runtime on an untrustworthy measurement, which is what P3 exists to fix.

Wall is left as "contended" deliberately — the run took ~50 min against the cache build's 18 min for the same OCR work, on a host that was also running a game. Recording that as a number would repeat the mistake corrected above.

What the instruments bought, all on dev at `skew` and all newly measurable:

- **parse, not OCR, is the cheap lever.** 312 field-instances have their truth value in the text and are not emitted (2.21 extraction pts); 273 of those are `parse_miss` (1.90 pts) with three named causes — unparsed prose in the sponsor attestation, a missing `purpose` alias, and left-margin clipping defeating fuzzy key matching. None needs OCR. For comparison the entire `skew`→`bands` restoration ladder is worth +1.68.
- **`fee_unknown` (7.89 class pts) is not an OCR problem**: its `fee_status` is visible in 0.0% of cases, 3.6% with OCR, and hidden-only in 41 — the fee is not in the document.
- **`b13_census` (5.00 class pts) is not an evidence problem**: 89 of 95 cases have no B-13 anywhere, only 6 are detection misses, and the other fields read at 98.0%. It needs a split, not better reading.
- **The ML ceiling over today's partition is +1.56**, against 17.17 behind a finer partition. Decision work stays last.

| # | Date | Commit | Change | Total | Class /80 | Extr /50 | Calib /20 | CFA | Wall | Decision |
| - | ---- | ------ | ------ | ----: | --------: | -------: | --------: | --: | ---: | -------- |
| 16 | 2026-07-22 | (P1b) | Per-field candidate preference: prefer the clean text-layer read, settle ties by field-manual trust order | **115.43** | 61.27 | **39.00** | 15.16 | 0 | — | keep |
| — | 2026-07-22 | (P1b) | *Also tried:* vocab passthrough for unseen `home_world` / `species_code` | 115.39 | 61.27 | 38.95 | 15.16 | 0 | — | **reject** |

Row 16 is the first change measured entirely through `scripts/replay.py` — seconds per variant against a cached page-text dump, rather than a 40-minute pipeline run. The full 2×2 matrix was scored in about a minute:

| passthrough | preference | total | extr |
| --- | --- | ---: | ---: |
| off | trust-then-source (old) | 115.20 | 38.76 |
| off | **source-then-trust** | **115.43** | **39.00** |
| on | trust-then-source | 115.12 | 38.68 |
| on | source-then-trust | 115.39 | 38.95 |

`packet.docs` sorts by `(doc_type, source)`, which ranks whole *documents*, so an OCR'd high-trust document wins every field at once over a clean text-layer copy elsewhere. Both cases the characterization suite flagged were corrections: MIB-000241 `Tekzam Mirarix` → `Tekzarn Mirarix` and MIB-000303 `Qorvoss Sollesh` → `Qorul Arikesh`, both matching truth, and the first also stopped emitting a spurious `sponsor_mismatch` — better names mean fewer false name-mismatch flags on an exact-set-match field worth 8.

| # | Date | Commit | Change | Total | Class /80 | Extr /50 | Calib /20 | CFA | Wall | Decision |
| - | ---- | ------ | ------ | ----: | --------: | -------: | --------: | --: | ---: | -------- |
| 17 | 2026-07-22 | (P2) | Runner robustness: streaming ordered writes, fallback row per failed case, case_id coercion, per-case OCR bound, pinned start method | 115.43 | 61.27 | 39.00 | 15.16 | 0 | — | keep |

Row 17 is deliberately score-neutral on train — none of these failures occur on the public set. That is the point: each one is invisible here and expensive on the private set.

- **Partial output.** `pool.map` blocks until all 5,000 PDFs finish and only then writes, so a container stopped at the 30,000s limit was scored on an **empty file**. Verified directly: killing a run after 25s now leaves 24 valid rows where it previously left none.
- **No case is ever dropped.** `_safe_predict` returned `None` on any exception, forfeiting that case's extraction points *and* taking the missing-case penalty. It now emits a NEEDS_REVIEW fallback, which cannot score worse than nothing (2 raw points against a true APPROVED/DENIED, 8 against a true NEEDS_REVIEW).
- **`case_id` coercion.** `emit.CASE_ID_RE` was defined and never used, so a malformed id reached the evaluator — which rejects the whole submission. A bad id also costs twice, as an extra case *and* a missing one. Now recovered from debris, then the filename stem, then a syntactically valid id matching no real case.
- **Per-case OCR bound**, set at 120s — above the worst case ever measured (max 107s) so it never fires in normal operation. A 45s trial truncated a real case purely because the host was busy: it is a wall-clock guard, so a value low enough to bite makes output depend on machine load. What it bounds is the shape underneath — tesseract is capped at 20s per call, but a many-page scan tried at several variants has no ceiling.
- **Pinned `spawn`**, since 3.14 defaults to spawn on macOS and forkserver on Linux, and this pipeline reads config from the environment.

Measured while doing it: `case_id` voting agrees with the filename on **all 1,000** train packets, with zero duplicate collisions. So the dedupe and id-repair paths are insurance against a failure that does not occur on public data, not fixes for an active bug.

**The rejected row is the more useful one.** Passing unseen values through instead of deleting them looks like an obvious bug fix — `mib/vocab.py`'s docstring promises it, and only `declared_purpose` delivers it — and a strict-xfail test was standing by to confirm it. It measured as a *loss* twice, because deleting a value snapping could not vouch for was quietly acting as a quality filter that let a cleaner copy supply the field. Its intended upside also does not exist: all 1,000 train cases yield exactly 13 home worlds, 12 species and 10 purposes, and the vocabulary lists are those enumerations. In a sample that size a fourteenth world would be expected ~77 times, so the value universe is closed and there are no unseen private-set values to rescue. U7 is closed as a non-issue and the xfail became an ordinary test asserting the deletion is deliberate.

**Step-0 decision-layer bake-off (2026-07-22, commit adff813, substrate = skew cache replay at 115.20 dev):** 5-fold OOF within dev, identical 66-dim features. Rules 61.27/80eq Brier .1210 CFA 0 → **calibrated logistic + inner-CV correctness confidence 62.43/80eq Brier .1293 CFA 12** (+1.16 class pts, −0.33 calib pts, net ≈ +0.8). MLP(32) overfits (59.44, Brier .21, 31 CFA) — Phase 1 ships logistic. CFA veto sweep: t=0.15 → 61.73/6 CFA; t=0.10 → 60.76/2. Full table in JOURNAL.
