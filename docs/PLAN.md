# Working plan

> **Amendment (2026-07-22).** Two guardrails below are superseded by the
> decision-layer rework (see JOURNAL entries for commits adff813 / a21fc36 and
> `docs/rethink-2026-07-22.md`):
>
> - **"CFA = 0 is a hard gate" no longer holds as an absolute.** Catastrophic
>   false approvals are now *priced, not banned*: the learned decider chooses by
>   expected-points argmax (a CFA costs −4 raw vs +8 for a correct approve, so
>   EV already charges for it), `MIB_CFA_VETO` provides a tunable
>   P(DENIED) threshold that demotes marginal approvals to NEEDS_REVIEW, and
>   every eval reports its CFA count. The rules cascade still runs at CFA 0;
>   the shipped operating point (decider + veto) is chosen on dev OOF at
>   packaging time.
> - **Architecture is now one signal layer, two deciders** — the rules cascade
>   (`mib/policy.py`) and a calibrated-logistic learned decider
>   (`mib/decision.py`), selected via `MIB_DECIDER` at the S5 seam, replacing
>   the M4 "GBM residual model with rules short-circuit" sketch. The debug
>   sidecar logs both on every run, so each eval is a standing A/B.
>
> The milestone list below is otherwise historical context; current sequencing
> lives in JOURNAL's latest entry.

Goal: a real solution clearing the interview bar (105+ total, 55+ classification, 0 catastrophic false approvals, 90%+ valid rows) with headroom, built as an evaluation-driven loop rather than a big-bang pipeline. Deadline: Aug 3, 2026.

Background docs: [scoring.md](scoring.md) (what the metric rewards), [label-mining.md](label-mining.md) (rule ceiling ~85% on perfect fields), [organizer-guidance.md](organizer-guidance.md) (authoritative rulings — part of the spec), [repo-intel.md](repo-intel.md) (secondary reference), [JOURNAL.md](JOURNAL.md) (running log).

## The loop (process before features)

Every change goes through the same cycle:

1. **Work** — one focused change (new signal, rule refinement, OCR tweak, model update).
2. **Evaluate** — `scripts/eval_local.sh`: run the pipeline on `data/train`, score with the challenge's own `evaluate.py`, print total / extraction / classification / calibration / CFA count / per-field match rates.
3. **Record** — append one row to `docs/experiments.md`: date, git commit, change description, scores, CFAs, decision (keep/revert). Notable findings also get a JOURNAL entry.
4. **Improve** — pick the next change from the current largest gap (the eval output tells us: which fields miss most, which confusion cells are heaviest, where calibration bleeds).

Guardrails, checked every run:
- **CFA = 0 is a hard gate** — any change that introduces catastrophic false approvals is reverted or fixed before anything else proceeds.
- Output validity 100% (validate_submission.py) — a final in-pipeline validation stage guarantees enums/dates/dedupe regardless of upstream bugs.
- Never tune toward under-determined labels (see organizer-guidance.md): those cases are *supposed* to land on NEEDS_REVIEW at 2/8 points.
- Fast path vs. contract path: day-to-day evaluation runs the pipeline directly (venv, multiprocess) for speed; the full Docker contract (`--network none`, read-only, 4 cpu / 8 GiB) is re-verified at every milestone (M2, M4, M6) via `scripts/run_docker_submission.py`.
- Anything *trained or tuned* uses frozen stratified splits (e.g. 700 dev / 150 calibration / 150 holdout); the holdout is touched only at milestones.

## Milestones

### M0 — Data + harness (first)
- Verify zip sha256 (`data/downloads.sha256`), unzip at challenge-repo root → `data/train/`, `data/validation/`.
- Write `scripts/eval_local.sh` + `docs/experiments.md` (empty table). Commit the current stub as the repo's first commit, then score the stub as the baseline row (expect ≈ classification-only ~14, everything else ~0).
- **Exit criteria:** loop runs end-to-end in one command; baseline row recorded.

### M1 — See the data
- Survey ~100 train PDFs: text-layer vs scan-only ratio, page/doc-type inventory (intake form, B-13 biometric slip, sponsor letter, registry extract, fee receipt, adjudicator notes), injection patterns actually present (span colors, off-crop, invisible render modes, decoys), layout variants, multi-applicant packets. Findings → `docs/pdf-survey.md`.
- Confirm/deny the OCR priors (sparse-text mode, embedded rasters, receipt-date visibility) and pick the OCR engine from measured per-page timings against the 6 s/PDF budget.
- **Exit criteria:** pdf-survey.md answers: what fraction needs OCR at all; what does trusted vs. untrusted text look like concretely; chosen OCR engine + settings.

### M2 — Iteration 1: trusted text layer + rules (no OCR)
- Restructure `solution.py` into modules (`pdfio`, `extract`, `adjudicate`, `emit`) keeping the `<input> <output>` contract.
- Trusted-span filter (drop white/tiny/off-crop/invisible spans, decoy patterns) → label-anchored field extraction with enum snapping (visa/species/fee/flags vocabularies, ISO dates, `SPN-####`/`MIB-######`) → deterministic rule cascade: disqualifying flags, TRANSIT-7, unpaid, unknown-fee, revoked sponsors (6 known), staleness (visible receipt date, fallback constant), embargo worlds (TRAPPIST-1e/Eris Relay always; Wolf-1061c non-DIP), DIP-1 carve-outs, review-only flags → NR.
- Document census: missing B-13 when risk evidence would be decisive → NEEDS_REVIEW (organizer-sanctioned).
- Confidence: per-decision-bucket empirical accuracy from train (coarse but honest).
- **Exit criteria (train, text-layer-only):** valid rows 100%, CFA 0; expect classification to already be strong on text-layer-rich cases; extraction limited by scan-only PDFs. Record per-field misses to size M3.

### M3 — Iteration 2: OCR + visible-evidence reconstruction
- Selective OCR (pages with sparse trusted text), 4 workers, per-page cache under `/tmp`; embedded-raster fast path; row-reordering before label/value pairing.
- Cross-check text layer vs OCR-visible content → untrusted-span flagging; extraction consumes trusted evidence only, with the manual's precedence order (stamp/note > intake > biometric > attestation > registry > text layer). Approvals require intake-grade evidence.
- Fuzzy repair pass for OCR'd enums/IDs (edit-distance snap; digit-confusion handling for `SPN-####`).
- **Exit criteria:** extraction match rate materially up on scan-only cases (target from M1 sizing); CFA still 0; average runtime ≤ ~4 s/PDF local (headroom vs 6 s budget).

### M4 — Iteration 3: residual model + calibration + decision rule
- Feature builder over extractor outputs + doc signals (census, legibility, conflicts, staleness days, sponsor validity, completeness). GBM → per-class probabilities; rules short-circuit the deterministic cases.
- Isotonic calibration on the calibration split; adjudication = expected-points argmax (E[A]=8P(A)−4P(D)+P(N), etc. — see scoring.md); confidence = calibrated P(chosen label correct).
- Ablate: rules-only vs rules+model; keep the model only where it demonstrably beats rules on the holdout.
- **Exit criteria (holdout):** total ≥ 105 trajectory visible, calibration ≥ 15/20, CFA 0, no rule regression.

### M5 — Hardening
- Failure-path coverage: corrupt/encrypted/empty PDFs, per-PDF timeout with degraded fallback (text-layer-only → low-confidence NR); case_id from document content first, filename stem as fallback only.
- Full 5,000-PDF validation run under exact Docker limits; wall-clock and memory profile; fix any budget breach.
- **Exit criteria:** clean run on all 6,000 public PDFs under contract limits; 100% valid rows on validation manifest.

### M6 — Submission
- Final frozen run → `predictions.jsonl` for validation; sanity-check prediction distribution vs train priors.
- `MEMO.md` largely assembled from JOURNAL + experiments.md (approach, injection defense, under-determined handling, ablations, failure modes, what-next). `SUBMISSION.md`, fork PR, Google form (both required).
- **Exit criteria:** PR open before Aug 3 cutoff with everything checked.

## Prioritization principle

When choosing the next change inside any milestone: classification points > calibration points > extraction points (mirrors 80/20/50 weighting vs effort), and any CFA fix preempts everything. If the eval says a planned feature (e.g. stamp CV) isn't moving the metric, drop it and journal why — the experiments log is the arbiter, not the plan.
