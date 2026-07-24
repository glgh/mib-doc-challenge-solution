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

| # | Date | Commit | Change | Total | Class /80 | Extr /50 | Calib /20 | CFA | Wall | Decision |
| - | ---- | ------ | ------ | ----: | --------: | -------: | --------: | --: | ---: | -------- |
| 16 | 2026-07-23 | (b926403) | **Default OCR to exhaustive** — early-stop off (`mib/config.early_stop`, `MIB_EARLY_STOP=1` restores): try every geometric variant and keep best-of-all instead of stopping at `GOOD_ENOUGH` | **118.63** | 62.33 | 40.99 | 15.31 | 0 | 2.05s/case | keep |

Clean same-machine A/B, both caches rebuilt fresh from current HEAD (dev-700 via `score_split.py`): early-stop **ON 118.42** (extr 40.84, class 62.24, risk_flags 503) → **OFF 118.63** (+0.21; extr +0.15, class +0.09, CFA 0), for **1.51 → 2.05 s/case**. Early-stop was a false economy: it halted at the first `evidence_score ≥ 6` reading and settled for a worse variant, spending the *most* OCR on the hardest pages (which never clear the bar). The +0.21 is a **floor** — it keeps the per-*page* `best()`; per-*field* selection across all variants (defer-selection, the next step) sits on top and also removes S2's parser-vocab dependency. Two caveats that must travel with this: (1) the baseline here is the progressive-restoration branch, well above row 15's 115.20 from intervening unlogged work; (2) **2.05 s/case is this 10-core laptop / 4 workers, not the 4 vCPU contract** — comfortably under the 6 s budget here, but the ship gate is still `run_docker_submission.py` under real limits, never run. The A/B is now provenance-guarded: `early_stop` is a critical stamp key, so an exhaustive cache can't be silently joined with an early-stop one.

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

---

| # | Date | Commit | Change | Total | Class /80 | Extr /50 | Calib /20 | CFA | Wall | Decision |
| - | ---- | ------ | ------ | ----: | --------: | -------: | --------: | --: | ---: | -------- |
| 18 | 2026-07-23 | (b926403) | OCR-robust risk-flag extraction: value-first fuzzy match with a pure-python confusion-weighted edit distance (`mib/vocab.match_flag_token`), scanning flag-bearing docs in `signals.observed_flags`/`has_flag_evidence` | 119.10 | 62.41 | 41.36 | 15.32 | 0 | 515s | keep |

**Substrate = replay on `output/cache/train_skew.jsonl` (early_stop off), dev split; delta vs the same-cache P3 baseline (118.63): +0.46** (extraction +0.37, classification +0.08, calibration +0.01). Isolated by replaying the identical frozen page text with vs. without the change, so OCR variance cannot contaminate the delta.

The defect: `signals.observed_flags` exact-matched `token in ALL_FLAGS` against one parsed key on one document type, so any OCR perturbation dropped a legible flag. Two shapes seen on the 18 recoverable train cases — trailing punctuation (`planetary_embargo.` on the adjudicator note, difflib 0.97) and genuine corruption (`bichaxarc_yed` for `biohazard_red`, 0.69). Plain difflib cannot separate the corrupted tail from benign flag-substring words (`biometrics`→`illegible_biometrics` 0.67 > corrupted `mnt_tampenng`→`memory_tampering` 0.64); a length-normalized **confusion-weighted** distance (o↔c, z↔x, …) plus a runner-up **margin guard** does. Rejected en route: tesseract dictionary bias (unreliable with the LSTM engine) and the `weighted-levenshtein` package (source-only, no 3.12 wheel, would put a compiler in the image).

Against the corrected P3 baseline on this cache: **46 cases gain a flag, 0 phantom** (every added flag is in truth), **0 true flags removed**, and **2 adjudication flips, both NEEDS_REVIEW→DENIED with truth DENIED** (safe direction, CFA stays 0). Guards proven by unit tests (`tests/test_regression.py`): explicit `none`/`clear`, option legends, and negated sentences (`cleared of biohazard_red`) all yield nothing; the injection differential tests still pass because `_raw` never holds hidden text.

**Confirmed by a full-pipeline eval (2026-07-23, HEAD `17f82ae`, `output/eval_head`):** the committed
tree runs the real pipeline end-to-end on the 1,000-PDF train set at **dev 119.10** (class 62.41, extr
41.36, calib 15.32, CFA 0, 0 missing) in 515s — matching the row-18 cache replay to within 0.01, so the
provisional caveat is discharged. `read_case` non-determinism is still real (it moves ~0.01) but does
not move the sign or safety. The non-determinism investigation remains open.

**Learned decider re-measured on the 119.10 substrate — edge inverted, promotion shelved (2026-07-23,
`output/eval_head`, dev 5-fold OOF, `scripts/train_decision.py`):**

| decider | class /80eq | Brier | CFA |
| --- | ---: | ---: | ---: |
| **rules baseline** | **62.41** | **0.1169** | **0** |
| calibrated logistic + conf-correctness *(the shipped `export_decision.py` recipe)* | 61.91 | 0.1229 | 14 |
| logistic + branch (uncalibrated) | 62.37 | 0.1326 | 23 |
| MLP(16, α=1) + cal | 63.14 | 0.1378 | 20 |
| MLP(32) + branch | 60.16 | 0.2013 | 28 |

The learned decider's advantage over rules has **inverted**: it was +1.16 class pts on the 115.20
substrate (bake-off above), and is **−0.50** here, with **14 CFAs vs rules' 0** and a worse Brier. The
mechanism is staleness, not a bug: the learned decider is a *residual-corrector on the rules cascade*,
and the flag-extraction (row 18) + P3 parse work strengthened rules to the point that there is no
residual left to correct — it now adds noise and false approvals. Divergence-by-branch confirms it
bleeds where it diverges: `fee_unknown` (n=50) **−0.73** and `fee_unpaid` (n=2) −0.17 swamp its gains
(`missing_arrival` +0.26, `missing_sponsor` +0.13, `b13_census` +0.09), and `fee_unknown` is a
data-availability wall (fee genuinely absent from the document — STATUS Q5), not an ML-winnable cell.
The CFA veto cannot rescue it: reaching CFA-parity with rules needs t≤0.05 → 58.50 (−3.9). It is
**strictly dominated** — no veto setting both matches CFA=0 and beats rules' class points.

The overfitting guard held: the best *naive* dev read, **MLP(16) at 63.14**, wins only by carrying
**20 CFAs** and a worse Brier — a catastrophic-false-approval pattern that fails the interview bar
regardless of raw score (MLP already showed 31 CFA on the prior substrate). Holdout was **not** read:
the dev-OOF measurement already says "don't promote," so there is no frozen model worth a holdout read.
**Decision: keep `MIB_DECIDER=rules` (already the default); the shipped `decision_model.npz` is
`d6427f8`-trained and now silently scores drifted features, so it is frozen and marked superseded — not
a live promotion candidate — until there is an edge the rules cascade cannot capture.**

---

| # | Date | Commit | Change | Total | Class /80 | Extr /50 | Calib /20 | CFA | Wall | Decision |
| - | ---- | ------ | ------ | ----: | --------: | -------: | --------: | --: | ---: | -------- |
| 19 | 2026-07-23 | (dirty) | **Runtime gate: first-ever contract-limits Docker run** (`scripts/run_docker_submission.py`) of the shipped `skew` config, scored on the container's own output | **119.17** | 62.51 | 41.30 | 15.36 | 0 | **0.54 s/PDF** | keep — baseline |

Row 19 is a **runtime measurement**, not a scoring change: the shipped `skew`/psm11/rules config, but
run for the first time under the *exact* Docker contract (`--network none --cpus 4 --memory 8g
--pids-limit 512 --read-only --tmpfs /tmp:…size=2g`, image **0.13 GiB**) rather than on a contended
laptop. Two results, both new:

- **Runtime fits with ~11× headroom, and the heavy tail was a contention artifact.** 1,000 PDFs in
  **542 s wall → 0.54 s/PDF** (budget 6), projecting to **0.75 h for 5,000** (budget 8.3 h). Per-case
  `cost_ms`: mean 2.16 s, p50 2.01, p90 4.14, p99 6.43, **max 8.33 s**. The laptop figures this repo
  feared (p99 57 s, max 107 s) were contention, not the pipeline — on 4 dedicated vCPU the tail is
  tame. Wall = compute (2160 s) / 4 workers, self-consistent. **This dissolves the runtime objection
  that gated `turn`/`bands` and would gate dual-PSM: even a 2× OCR cost lands far under budget.**
  (STATUS open-question 2 answered; question 3's premise — that `turn`/`bands` are unaffordable — is
  now in doubt and should be re-measured directly.)
- **The shipped container reproduces the host score, and the host↔container OCR skew is score-neutral
  and CFA-safe.** Container dev **119.17** (class 62.51 / extr 41.30 / calib 15.36 / CFA 0 / Brier
  0.1159) vs host `eval_head` **119.10** — within nondeterminism. Parity vs `eval_head` at the row
  level: 0 case-id diffs, but **7/1000 adjudications (0.70%)** and ~4 % of `applicant_name` differ,
  because the container's Debian `tesseract` is a *different build* than the host's — not run-to-run
  jitter. The differences roughly cancel in aggregate (class +0.10, extr −0.06) and **CFA stays 0**,
  so the host replay loop is a faithful proxy for the shipped artifact. Recorded as a standing caveat:
  the ship number ultimately comes from container output, not host replay.

| # | Date | Commit | Change | Total | Class /80 | Extr /50 | Calib /20 | CFA | Wall | Decision |
| - | ---- | ------ | ------ | ----: | --------: | -------: | --------: | --: | ---: | -------- |
| 20 | 2026-07-23 | (dirty) | **Dual-pass Tesseract PSM 3+11** (`MIB_OCR_PASSES=dual`): a PSM 3 read per image alongside PSM 11, `best()` keeps the stronger | **119.96** | 62.76 | 41.84 | 15.36 | 0 | unresolved | **flag-gated, NOT shipped** |

**+0.87 dev over its own baseline, at CFA 0 — and still not shipped, because the cost never got a
clean measurement.** Both caches were rebuilt from the current tree so the only difference is the
flag (`train_skew_psm11.jsonl` vs `train_skew_dual.jsonl`); the psm11 side replays to **119.09**
against the committed `eval_head` 119.10, which validates the substrate before reading the delta.

| cache | Extr | Class | Calib | Total | CFA | Brier |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| psm11 (baseline) | 41.36 | 62.41 | 15.32 | 119.09 | 0 | 0.1169 |
| **dual** | **41.84** | **62.76** | **15.36** | **119.96** | **0** | **0.1160** |

Gains on both axes (4 more `correct`, 4 fewer `conservative_review`), Brier slightly better, CFA 0.

**Why it is off anyway — the cost is a tail, not a factor.** "Dual" doubles the *number* of OCR calls,
but PSM 3 is a different algorithm from PSM 11: PSM 11 skips layout analysis and hunts text blobs,
while PSM 3 runs the full pipeline (connected components → blob classification → column detection →
line/baseline finding → reading order). On the geometrically destroyed scans this corpus is built from,
PSM 3 tries to infer a page structure that does not exist, and noise inflates the component count that
its grouping stage is superlinear in. So it is slowest exactly where the pages are worst — and our
exhaustive variant fan-out (`{embedded, render} × {none, deskew}`) multiplies it by ~4 per scan page.

Measured on the host (1,000 cases): psm11 511 s vs dual 2396 s, per-case p50 1948→4145 ms (2.1×),
p99 5977→63110 ms (10.6×), **max 7879→106718 ms (13.5×)**. The distribution is the point:

| population | n | median slowdown |
| --- | ---: | ---: |
| 0 scan pages (text-layer only) | 149 | 1.0× |
| 1–2 scan pages | 498 | 1.7× |
| 3+ scan pages | 353 | 1.7× |
| **worst 5% of cases** | 50 | **10–13.5×** |

The worst 5% carry **33% of all the added cost**, and the top 8 are all near-fully-scanned packets —
so it tracks *damage severity*, not page count. The median case behaves like the naive "2×" intuition.

**The contract-limits run did not survive.** `run_docker_submission.py --ocr-passes dual` died at
486/1000 after 1094 s (`unexpected EOF`, exit 125) and took the Docker daemon with it (`/version` and
`/info` returned 500 afterwards). The host was contended by an unrelated GUI process at the time, so
the crash is not attributable to dual alone — but two things did happen on the record: the **120 s
per-case OCR budget fired** (`MIB-000243: OCR budget spent, text layer only for page 3+`), which is a
silent quality regression, and the run produced no usable timing. **Decision: keep `MIB_OCR_PASSES=psm11`
(the default, and now pinned in the `Dockerfile`), exactly as `turn`/`bands` are kept reachable but
unbanked.** Reviving dual means gating the PSM 3 pass rather than running it on every image.

Incidental: 486 valid rows survived the crash on disk — the row-17 streaming-write robustness working
under a real container death, where the old `pool.map` design would have left an empty file.

**Open, and it decides the gate direction:** we do not yet know *which* pages produced the +0.87,
because the cache stores only the chosen line list, not the winning variant. The plan assumed PSM 3
should run on pages *below* `GOOD_ENOUGH`; the cost data hints the opposite (PSM 3 earns its keep on
dense intact forms and burns time on wreckage it cannot segment). Recording the winning variant is a
one-line instrument and is the prerequisite for any gated retry.
