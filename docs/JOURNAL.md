# Journal

Running log of exploration, findings, and decisions. Short entries: what we did / what we learned / what we decided. Topic deep-dives live in their own files under `docs/`.

## 2026-07-21 — Rules of the game, label mining (no PDFs yet)

**Data status:** public data zip still downloading (`../mib-doc-challenge/mib-doc-challenge-public-data-v2026-07-07.zip`, size observed growing past 1.4 GB; checksum can't verify until complete). Labels CSV and validation manifest are already available in the challenge repo checkout.

**Read the scorer** (`../mib-doc-challenge/scripts/evaluate.py`) — it is public and deterministic, i.e. the exact leaderboard metric. Mechanics and strategic consequences written up in [scoring.md](scoring.md). Headlines:
- Field matching is casefolded whitespace-collapsed exact string match → canonical output formatting (ISO dates, enum snapping) is worth extraction points.
- Classification payoff is asymmetric (false approval = −4; retreat to NEEDS_REVIEW = 2) → decide via expected-points argmax over calibrated class probabilities, not raw argmax.
- Confidence is Brier-scored against adjudication correctness → per-case calibrated confidence, never a constant.
- Missing-case penalty is tiny (~0.002 pts/case) → essentially never omit a case.

**Mined `data/train_labels.csv`** (1,000 rows: 431 DENIED / 289 APPROVED / 280 NEEDS_REVIEW) against the FIELD_MANUAL policy. Full numbers in [label-mining.md](label-mining.md). Headlines:
- Every testable manual rule holds 100% on train (disqualifying flags, TRANSIT-7, unpaid fee, unknown fee, stale non-DIP dates).
- Three additional revoked sponsors are learnable from labels: SPN-2718, SPN-7331, SPN-9090 (manual explicitly invites inferring these).
- Revoked sponsor is irrelevant under DIP-1 (sponsor not required there).
- A rule cascade on **perfect** (ground-truth) fields tops out at **84.6%** adjudication accuracy — the residual (incl. 9 would-be false approvals) depends on document-visible evidence not present in the labels CSV: stamps, signed notes, waiver/diplomatic-note visibility, legibility, evidence conflicts.

**Decisions:**
- Architecture direction: layered pipeline — trusted-text reconstruction (text layer + OCR cross-check) → field extraction with enum snapping → document-level signals (stamps, notes, legibility) → deterministic rules for the 100% cases + small trained classifier (e.g. GBM over extracted features) for the residual → calibrated probabilities → expected-points decision rule → validated JSONL. Start basic (text layer + rules), add trained parts as data justifies.
- No LLMs/VLMs of any kind at runtime (explicitly banned in EVALUATION.md, including local ones like llama.cpp); offline OCR + classical CV + small task-specific/candidate-trained models are allowed.
- Keep this journal updated at each milestone; record local eval scores for every pipeline change (ablation-style).

**Next:** once the zip finishes — verify sha256, unzip at the challenge repo root, then survey PDFs (text-layer vs scanned ratio, actual injection patterns, stamp appearance, layout variants) before committing to an OCR engine.

## 2026-07-21 (later) — Intel from challenge repo issues & submission PRs

Surveyed github.com/8090-inc/mib-doc-challenge issues and PRs. Full writeup in [repo-intel.md](repo-intel.md). Headlines:

- **Organizer confirmed (issues #4/#5): some cases are deliberately under-determined** — the label carries a disqualifying flag but the public PDF contains no recoverable evidence for it; NEEDS_REVIEW is the *intended* output even though the label says DENIED. Don't guess invisible flags; detect the shape instead (packet missing its biometric slip "B-13").
- **Verified new policy rules from labels** (hypothesis via competitor memo, confirmed ourselves): TRAPPIST-1e and Eris Relay are embargo worlds → DENIED 100%, even DIP-1; Wolf-1061c → DENIED 51/51 for non-DIP. Also independent corroboration of revoked sponsors SPN-9090/7331/2718.
- **Three competitor submissions exist with public memos**; best self-reported local train score 113.7/150 (ext 37.1, class 63.6, calib 13.0) — clears the 105 bar, and shows where headroom is (extraction, calibration, under-determined routing).
- **Stamp detection reported as a dead end** by the CV-background competitor (no safe signal; colors uncorrelated) → demoted our stamp detector to a conditional, review-only component.
- Practical OCR recipes adopted as priors: Tesseract sparse-text mode, row-reordering, selective OCR, embedded-raster fast path, header crops for fee, unsharpened pass for `Observed flags:` lines.

**Decisions:** design updates 1–6 listed at the end of repo-intel.md (document census, embargo worlds, stamp-detector demotion, intake-grade evidence for approvals, OCR plan, frozen split hygiene).

## 2026-07-21 (later) — Organizer guidance promoted; working plan written

- Organizer rulings extracted into [organizer-guidance.md](organizer-guidance.md) as a standing spec-level reminder (under-determined cases → NEEDS_REVIEW; never guess invisible flags; B-13 document census is the legitimate signal). Competitor material stays in repo-intel.md as secondary reference only.
- Working plan written: [PLAN.md](PLAN.md). Core of it is the loop — every change runs `eval_local.sh` against the challenge's own scorer and lands one row in `docs/experiments.md` (date, commit, change, scores, CFAs, keep/revert), with CFA=0 as a hard gate and Docker-contract re-verification at milestones. Milestones M0 (harness) → M1 (PDF survey) → M2 (text-layer + rules) → M3 (OCR) → M4 (model + calibration) → M5 (hardening) → M6 (submission).
- **Next action:** M0 — verify/unzip the data (download was still in progress), write `scripts/eval_local.sh`, commit the stub, record the baseline row.

## 2026-07-21 (later) — Steps 0–2 executed: 91.91 → 112.12, CFA 52 → 0

Full detail in [experiments.md](experiments.md) (rows 1–5). Summary:

- **Harness first:** `scripts/eval_local.sh` (official scorer + per-field report via `scripts/field_report.py`), `scripts/mine_signals.py` (per-case signals joined with truth — every rule validated there *before* shipping), experiments log, baseline committed.
- **Architecture:** monolith split into `mib/` along trust boundaries — `pdfio` (injection quarantine), `parse`, `packet` (census + precedence merge), `signals` (fraud taxonomy, see [fraud-signals.md](fraud-signals.md)), `policy` (named branches), `confidence`, `emit` (schema safety net). Refactor verified score-identical before any behavior change.
- **Step 1 rules (validated kills/collateral):** inferred revoked sponsors, DIP-1 carve-out, embargo worlds, staleness, waived-non-DIP tightening, B-13 census. Key finding: the ~25 label-DENIED cases with no readable risk evidence are *EV-positive to approve on train labels* but organizer-ruled NEEDS_REVIEW — we follow the ruling (cost ~2.6 pts, CFA → 0, upside on private labels).
- **Step 2 OCR:** Tesseract PSM 11 (PSM 4/6 validated useless here), embedded-raster fast path, vocab snapping. One new CFA appeared and was diagnosed to the exact character: OCR reads colons as periods, so `Observed flags. active_warrant` slipped the strict parser — fixed with fuzzy key matching + tolerant separators.
- **State:** 112.12/150 (class 60.72, extr 37.32, calib 14.08), 0 CFA, 0.15 s/PDF — clears the full interview bar on train.

**Next:** Step 3 calibration (branch-empirical confidences on frozen splits — calib is 14.08/20 with hand-set constants); residual mining (fee_status 55% worst field; risk_flags fuzzy-parse dip 68.3→66.8; 196 APPROVED→NR retreats to shrink); then Docker contract run.

## 2026-07-22 — Lean round executed: dev 112.56 → 114.43, holdout 113.46, still 0 CFA

Experiments rows 6–10. Highlights:
- **Frozen 700/300 split** now governs iteration (holdout read only at milestones; read #1 passed: dev-holdout gap < 1 pt).
- **Manual corrections discovered**: `Manual correction: <field> is <value>.` lines on 136 packets, 100% truth-consistent where checked — parsed as rank-0 evidence (the manual's "signed manual note" tier). Found while diagnosing an over-denial whose intake carried "Manual correction: fee status is waived." above an `unpaid` receipt.
- **Safe-deny evidence standards**: deny rules now require positive preconditions (unknown visa no longer arms non-DIP denials; digit repair can't fabricate revoked sponsor ids; `unpaid` must be verbatim). Also replaced the fitted receipt-date constant with the max-margin staleness cutoff (2026-01-02, middle of train's 48-day empty band).
- **Negative result worth keeping**: 300-DPI preprocessing retry for destroyed scans = +0.21 pts at 43x runtime → reverted. OCR quality here is bimodal; pages read at 200 DPI or are synthetically destroyed.
- **Fitted per-branch confidence** (Laplace-shrunk dev accuracy): calibration 14.22 → 15.13 dev / 15.05 holdout; census retreat honestly sits at 0.283.

**State vs interview bar (holdout, unbiased): 113.46 total, 60.63 classification, 0 CFA — all cleared.**
**Next segment:** stop-or-model decision (per-branch entropy check for the conditional GBM), Docker contract parity run (tesseract + pillow in image), then packaging (validation predictions, MEMO.md, SUBMISSION.md, PR + form).

## 2026-07-22 (later) — Rethink at 113.46: reports of ~135 prompted a zoom-out

Full analysis in [rethink-2026-07-22.md](rethink-2026-07-22.md). Headlines:
- Competitor PRs now self-report 120.6 / 126.7 / 129.2 / **132.4** on train — the gap is real.
- **"Destroyed scans" was wrong**: the pages are human-legible; our single-pass PSM 11 OCR is
  the failure. PSM 6/11 (+binarization) are complementary on real pages. Multi-pass ensemble
  with KV-level voting is roadmap item R1; PP-OCR (ONNX) to be benchmarked as an alternative.
- ~17/19 lost classification points are our own NR retreats; `b13_census` is 58% truly-approved
  (benign packets legitimately lack a B-13; registry-CLEAR discriminates); `fee_unknown` is
  mostly an unread-flags problem, and many packets have no receipt at all.
- Hidden injected answer keys on train contain the TRUE field values (fake APPROVED tail) —
  confirmed trap, still quarantined.
- Recommendation pending user sign-off: relax hard CFA=0 to expected-points argmax with vetoes
  and a documented cap (competitor at 129.2 carries 5 CFAs).
- Roadmap v2: R1 OCR ensemble → R2 parse/flag hygiene → R3 policy re-mine → R4 cost-sensitive
  residual adjudicator (trigger now clearly met) → R5 calibration/holdout/Docker/package.

## 2026-07-22 (later still) — Scan damage is geometric: dev 114.43 → 116.88, CFA still 0

Full survey in [damage-geometry.md](damage-geometry.md); experiments rows 11–14. Prompted by reading two packets by hand (MIB-000030, MIB-000131) and noticing pages that were turned sideways or sliced into offset horizontal strips.

- **Root cause found.** Row 8's "synthetically destroyed" and rethink's "PSM-mode problem" were both aiming at the wrong axis. The unreadable pages are *geometrically transformed*: quarter turns (90/270 — **180° never occurs**), several degrees of skew, and horizontal bands slid sideways. Undo the transform and the existing 200-DPI PSM 11 pass reads them fine. Resolution could never have helped; nothing was lost.
- **Sizing:** 36% of scan pages read zero field labels upright; 10% are rescued by rotation alone; **15.5% of cases carry a turned page contributing nothing**, and those cases extract at 52% vs 75% corpus-wide. Turned pages hurt disproportionately because the intake form — 6 of 9 scored fields — is as likely to be turned as anything else.
- **Result:** dev 116.88 (+2.38 over the re-baselined 114.50), extraction 38.53 → 39.62, 0 CFA, Brier 0.1243 → 0.1176. MIB-000030 extraction 8/45 → 36/45; MIB-000131 4/45 → 27/45.
- **Selector flaw fixed en route:** `recognized_keys` underrates restored pages, because the shredder clips the left margin and a rescued line often reads `mnsor ID: SPN-5809` — label gone, scoring value intact. `evidence_score` now counts well-formed values too.
- **Detection notes:** Tesseract OSD is unusable here (too sparse — "Too few characters" on 2 of 3 test pages). Ink run-length anisotropy gets 86% (91% at margin ≥0.05), but is used only to *order* candidates, never to decide — with 6x budget headroom, a wrong decision discards a page's text while a wrong ordering costs one pass. Best untapped signal is the printed page border: constant width, so its edges give skew precisely and its per-row left edge *is* the band offset.
- **Build bugs found and fixed** (unrelated, latent): the Dockerfile installed no tesseract **and** never copied `mib/` — the container would have died on import. Added both, plus `TMPDIR=/tmp`, `OMP_THREAD_LIMIT=1` (tesseract's OpenMP threads fight `Pool(4)`), and a `.dockerignore`.

**Shipped flag-gated, default `off`** — the gain is real but the flow is still repair-after-failure (OCR → fail → repair → re-OCR), which is why `skew` costs 4x baseline wall-clock.

**Next:** detect-then-repair rework (measure geometry in ~5 ms, OCR once) and gate the now-redundant 200-DPI re-render; then promote the default and read holdout. After that: cross-page voting for exact-match names, and treating cross-page field disagreement as `identity_conflict` evidence rather than discarding it.

## 2026-07-22 (later still) — Plan v2 approved (rules ∥ MLP deciders); prep landed

- **Approved plan** (plan file, summarized): one signal-extraction layer feeding two
  interchangeable deciders — the existing rules cascade (kept as baseline/fallback/feature
  source) and a simple MLP → expected-points argmax, with the cascade's branch one-hot as
  stacked features; `MIB_DECIDER=rules|mlp` selector. CFA=0 hard gate replaced by the −4
  term inside expected points + per-case CFA reporting.
- **Prep commit 72364b4**: enriched debug sidecar (finding, registry_status, waiver_code,
  n_pages, hidden_lines, n_fields_missing, n_corrections, rules_decision — additive only),
  tesseract subprocess pinned to `OMP_THREAD_LIMIT=1` locally (parity with the Docker ENV),
  `scripts/ocr_bench.py` (offline per-variant OCR recovery table), `scripts/train_decision.py`
  (Step-0 bake-off: 5-fold CV within dev, rules vs MLP vs logistic, branch-feature ablation,
  divergence-by-branch table, CFA listing). Baseline tag `v1-rules-holdout-113.46` at f0e7f62;
  sklearn in the dev venv only (never the image).
- **Second competitor wave** documented in repo-intel.md: 120.6 / 126.7 / 129.2 / **132.4**
  train. Common tier traits: multi-pass OCR on weak pages, vocab repair, selective retries
  gated on "no flags yet", cost-sensitive decision layers tolerating a few CFAs.
- **Step-0 bake-off not yet run**: it needs `output/eval/{debug,predictions}.jsonl` from a
  `MIB_RESTORE=bands` eval; the attempt was interrupted (worker-pool BrokenPipeError), so
  `output/eval/` currently holds an `off`-mode run. First action next session:
  `MIB_RESTORE=bands scripts/eval_local.sh dev`, then `scripts/train_decision.py`.

## 2026-07-22 (night) — Step-0 bake-off: decision layer PASSES the gate (calibrated logistic, not the MLP)

Substrate: `output/eval_skew/` — replay of the 1,000-case skew page-text cache
(`scripts/replay.py output/cache/train_skew.jsonl output/eval_skew`), scored 115.20 dev —
byte-consistent with experiments row 12, so the cache/replay seam is faithful. The planned
bands eval was dropped: bands is demoted to experimental-until-P3 and its cost is unmeasured,
while skew is the shipped default with a full cache on disk.

Bake-off (`scripts/train_decision.py output/eval_skew`, 5-fold OOF within dev, all deciders
on identical features — 66 dims incl. 16-branch one-hot + rules-decision one-hot):

| Decider | raw | /80eq | acc | Brier | CFA |
| --- | ---: | ---: | ---: | ---: | --: |
| Rules baseline (fitted conf) | 4289 | 61.27 | .690 | .1210 | 0 |
| MLP(32)+branch | 4161 | 59.44 | .730 | .2131 | 31 |
| MLP(32) no branch | 4106 | 58.66 | .717 | .2027 | 30 |
| Logistic+branch (raw conf) | 4366 | 62.37 | .756 | .1346 | 21 |
| Logistic+branch, sigmoid-cal | 4370 | 62.43 | .737 | .1370 | 12 |
| **Logistic+cal + inner-CV correctness conf** | **4370** | **62.43** | .737 | **.1293** | 12 |

- **Verdict: PASS, with the simpler model.** +1.16 dev pts classification over rules; Brier
  within 0.008 (≈ −0.33 calib pts), net ≈ +0.8. The MLP overfits 700 cases badly and loses
  to its own linear ablation — Phase 1 ships a **logistic** forward pass (even easier in
  numpy), MLP revisited only if Phase-2 features change the picture.
- Confidence lesson: raw P(chosen) is NOT P(decision correct) under EV-argmax; an inner-CV
  correctness calibrator (mirroring the rules' fitted per-branch confidence) recovered most
  of the Brier gap honestly.
- Divergence profile of the winner vs rules: +0.41 missing_sponsor, +0.33 b13_census,
  +0.29 missing_arrival, +0.17 missing_visa; fee_unknown neutral (+0.06); only review_flag
  slightly negative (−0.10). Exactly the "cascade can't price ambiguity" branches.
- CFA veto sweep (approve blocked when P(D) ≥ t): t=1.0 → 62.43/12 CFA; 0.20 → 62.00/9;
  0.15 → 61.73/6; 0.10 → 60.76/2; 0.05 → 58.60/0. Smooth insurance knob; decision on the
  shipped t deferred to Phase 3 with the memo's CFA-pattern argument in view.
- Provenance: substrate stamped restore=skew rev=d6427f8+dirty; `train_decision.py` now
  takes an eval-dir argv and runs `config.require_agreement` before joining artifacts.

**Next:** Docker contract parity run (in progress), validation fallback artifact, then
Phase 1 (`mib/features.py` + `mib/decision.py`, logistic weights in npz, `MIB_DECIDER`).

## 2026-07-22 (night, cont.) — Phase 1 landed: learned decider wired behind MIB_DECIDER

- `mib/features.py`: featurize moved out of the bake-off script — single source of truth
  consuming the exact (record, debug) pair the pipeline emits, so trainer and runtime cannot
  drift. `mib/decision.py`: numpy-only forward pass replicating
  CalibratedClassifierCV(scaler+logistic, cv=3, sigmoid) + EV-argmax + correctness-calibrated
  confidence + `MIB_CFA_VETO` knob (default 1.0 = pure EV). `scripts/export_decision.py`
  trains on dev(700) and refuses to export unless the numpy pass matches sklearn
  (achieved 3e-16); model is 22 KiB, feature-order check refuses stale models at load.
- Wiring at the S5 seam in `mib/runner.py` (the refactor's intended swap point): the learned
  decider always runs into the sidecar (mlp_decision/confidence/probs on every eval — permanent
  A/B); `MIB_DECIDER=mlp` promotes it into the record; any failure falls back to rules.
- Same-code A/B on the skew cache replay, dev: rules 61.27 class / .1210 Brier / 0 CFA vs
  mlp 65.16 / .1066 / 5 CFA, extraction byte-identical (0 non-decision field diffs).
  **The mlp figures are train-fit biased** (model trained on these 700); the honest OOF
  estimate stays 62.43 class (+1.16). Holdout untouched.
- Debugging note: a phantom "extraction changed under mlp" turned out to be the parallel
  session's in-flight packet.py edit landing between my two replays — caught by diffing
  non-decision fields, currently −0.08 extraction on their WIP (junk values no longer
  normalized to 'unknown': 'unknown' → '[REGISTRY LOST]' etc. on 39 fields).

## 2026-07-23 — Session close: Docker parity post-mortem, docs synced

- **Docker parity is still unresolved, and both attempts this session are invalid.**
  Post-mortem: the first contract run "failed" with exit 125 ("unexpected EOF") because the
  Docker daemon crashed — but its container survived the daemon restart and kept running.
  The retry then started a *second* contract container, and two concurrent `--memory 8g`
  containers (plus 2g tmpfs each) on the 8.2 GB Docker Desktop VM starved the daemon into
  the same EOF crash. Neither run's timing or output is usable. Both zombies killed, stale
  image removed. **Lesson: `docker ps` before starting or timing any contract run**, and
  treat the VM's memory as barely sufficient for exactly one contract container.
- Docs synced to reality: CLAUDE.md "Current state" rewritten (staged pipeline, two
  deciders, env knobs, dev workflow, scores); PLAN.md got a dated amendment retiring the
  CFA=0 hard gate in favor of EV-priced decisions + `MIB_CFA_VETO` + per-run CFA reporting.
- State at close: dev 115.43 (rules, CFA 0, parallel session at P2 runner robustness);
  learned decider honest OOF +1.16 class pts; holdout 113.46 (v1, one read).

**Next-session queue, in order:**
1. Clean Docker parity from current HEAD (verify `docker ps` empty first): byte-match vs a
   same-code local run at skew + s/PDF timing vs the 6 s budget.
2. Validation fallback artifact: best pipeline over `data/validation/` (5,000 PDFs) →
   `output/validation_predictions_v1.jsonl` + `validate_submission.py`. Insurance from day one.
3. Phase 2 signal extraction: OCR multi-pass ensemble at the Read seam; adjudicator
   `Reason:` → risk_flags; guarded loose flag snap; evidence-gated mismatch flags;
   coordinate with the parallel session's geometry rework.
4. Phase 3 integrate & ship: retrain/export on Phase-2 signals (`--split all` only at
   packaging), pick decider + `MIB_CFA_VETO` on dev OOF, holdout read #2 (gap < 2 pts
   gate), Docker parity re-run, package MEMO/SUBMISSION/PR + form before 2026-08-03.
