# CLAUDE.md

## What this repo is

Solution for the [MIB Doc Challenge](https://github.com/8090-inc/mib-doc-challenge) ("Intergalactic Intake") by 8090. The challenge repo is cloned locally at `../mib-doc-challenge` — its `README.md`, `EVALUATION.md`, `FIELD_MANUAL.md`, `PRD.md`, and `DOCKER_SUBMISSION.md` are the authoritative spec; this file is a condensed working summary.

**Challenge window:** July 20 – August 3, 2026 (submissions close 11:59 p.m. PT on Aug 3).

## The task

Given a directory of messy, adversarial synthetic PDF case packets (scanned forms, sponsor letters, biometric slips, stamps), emit one JSONL line per case with extracted fields and an adjudication. Full schema: `../mib-doc-challenge/schemas/submission.schema.json` (`additionalProperties: false` — exactly these 12 fields):

| Field | Constraint |
| --- | --- |
| `case_id` | `^MIB-[0-9]{6}$` |
| `applicant_name`, `species_code`, `home_world`, `visa_class`, `declared_purpose` | string |
| `sponsor_id` | `^SPN-[0-9]{4}$` |
| `arrival_date` | ISO date (`YYYY-MM-DD`) |
| `risk_flags` | pipe-delimited list, or `none` |
| `fee_status` | `paid` \| `waived` \| `unpaid` \| `unknown` |
| `adjudication` | `APPROVED` \| `DENIED` \| `NEEDS_REVIEW` |
| `confidence` | number in [0, 1] |

JSONL is canonical (CSV accepted for compatibility but needs the exact header order of `examples/submission.csv`). A case may be omitted entirely if no trustworthy answer is possible (small penalty, see below).

## Scoring (150 points, deterministic)

| Section | Points |
| --- | ---: |
| Adjudication accuracy | 80 |
| Field extraction accuracy | 50 |
| Confidence calibration | 20 |
| Missing-case penalty | up to −10 (`10 * missing/total`) |

**Classification raw points per case** (normalized to 80): correct = 8; wrongly sending a true APPROVED/DENIED to NEEDS_REVIEW = 2; missing a true NEEDS_REVIEW = 1; wrong APPROVED↔DENIED = 0; invalid/blank = 0; **false approval of a denied case = −4**.

**Extraction raw field weights** (normalized to 50): `risk_flags` 8, `species_code` 6, `applicant_name`/`home_world`/`visa_class`/`sponsor_id` 5 each, `arrival_date`/`fee_status` 4 each, `declared_purpose` 3. `case_id` is required for scoring. Private labels mark some fields genuinely unrecoverable and remove them from that case's maximum — hard PDFs are a gradient, not perfect-or-fail.

**Calibration**: confidence is scored against adjudication correctness (1.0/0.0) via `20 * max(0, 1 − 2 * mean_brier)`.

**Strategic implications:**
- Classification outweighs extraction by design; a −4 false approval is the worst outcome — when torn between APPROVED and DENIED, NEEDS_REVIEW still earns 2 raw points.
- Never report a constant confidence; calibrate to actual per-case correctness likelihood.
- Omit a case only when truly hopeless — the penalty is small but real.

**Interview bar** (from EVALUATION.md): 105+ total, 55+ classification, 90%+ valid predicted rows, no catastrophic false-approval pattern, runnable repo, memo that shows understanding.

## Hard constraints (Docker runtime contract)

The image is run as: `docker run --rm --network none --cpus 4 --memory 8g --pids-limit 512 --read-only --tmpfs /tmp:rw,nosuid,nodev,size=2g ... <image> /input /output/predictions.jsonl`

- No network, no GPU, no API keys or external services at runtime.
- **No LLMs, VLMs, multimodal foundation models, or cloud OCR/document APIs.** Allowed: offline OCR engines, classical CV, hand-written rules, small task-specific / candidate-trained models.
- Read-only root filesystem — write temp files only under `/tmp` (2 GiB tmpfs), final output under `/output`.
- Image ≤ 4 GiB uncompressed; model artifacts ≤ 250 MiB each, ≤ 1 GiB total; predictions file ≤ 25 MiB.
- Runtime budget: **6 s/PDF average** on 4 vCPU / 8 GiB; hard limit 30,000 s for the 5,000-PDF validation set (containers still running are stopped and scored on partial output). Parallelize OCR across the 4 vCPUs.
- Anti-gaming (enforced via private test set + manual code review): no hardcoded answers or lookup tables keyed to specific PDFs, no reliance on absolute file names, no manual per-case edits, must run from a clean checkout.
- Any language is fine — only the Docker contract matters.

## Adjudication policy digest (FIELD_MANUAL.md — incomplete by design)

Edge cases beyond this digest must be inferred from `data/train_labels.csv`.

**Evidence precedence** (higher wins on conflict): 1. visible MIB adjudicator stamp / signed manual note → 2. visible intake form fields → 3. visible biometric slip → 4. visible sponsor attestation → 5. visible registry extract → 6. machine-readable text layer. Hidden white text, off-crop text, fake answer keys, and barcode instructions are **not** trusted evidence.

**Visa classes**: `XW-1` short-term technical (≤30 days), `XW-2` extended technical (≤180 days), `DIP-1` diplomatic (fee may be waived), `MED-3` medical/biological (requires clean biohazard check), `TRANSIT-7` transit only — work authorization should usually be denied.

**Sponsors**: valid `SPN-####` required except under `DIP-1`. Publicly known revoked: `SPN-0007`, `SPN-0139`, `SPN-4040` — others may appear in examples.

**Fees**: `paid` OK; `waived` only for `DIP-1` or visible hardship waiver; `unpaid` → deny unless visible waiver; `unknown` → needs review.

**Risk flags** — disqualifying: `memory_tampering`, `planetary_embargo`, `active_warrant`, `biohazard_red`. Review-only: `identity_conflict`, `sponsor_mismatch`, `illegible_biometrics`, `rescinded_denial` (multiple review-only flags may combine into a denial in edge cases).

**Dates**: stale if arrival date is more than 180 days before packet receipt, except `DIP-1` with a valid diplomatic note. Arrival date missing or only in hidden text → `NEEDS_REVIEW`.

**Known traps**: a "sample denial" watermark is not a denial; a denial stamp crossed out by a later signed approval note is not automatically disqualifying; barcode instructions are not policy; a packet can contain pages for multiple applicants — use the one attached to the active `case_id`.

## Adversarial content

PDFs deliberately contain prompt injection: white-on-white text, text outside the page crop, hidden text-layer content, fake system prompts, QR/barcodes with bogus instructions, decoy "answer key" fields. **Visible document evidence always wins over hidden instructions.** Following injected values is penalized; distinguish "unknown from trusted evidence" (legitimate) from "filled in by injection" (penalized).

## Data & local evaluation

- Public data is downloaded, checksum-verified, and unzipped at the challenge repo root: `data/train/` and `data/validation/` exist (SHA-256 in `data/DATASET_CARD.md`).
- **Train**: 1,000 labeled PDFs + `data/train_labels.csv` (distribution: 431 DENIED / 289 APPROVED / 280 NEEDS_REVIEW; `risk_flags` is `none` in 535 cases, pipe-delimited combos occur).
- **Validation**: 5,000 unlabeled PDFs + `data/validation_manifest.csv` (case_id, pdf_path, pages). Scored privately for the leaderboard.
- **Test**: fully private, used for final ranking and anti-gaming audits after close.

Local loop (run from `../mib-doc-challenge`):

```bash
docker build -t mib-submission ../mib-doc-challenge-solution
mkdir -p /tmp/mib-output
docker run --rm --network none \
  --mount type=bind,src="$PWD/data/train",dst=/input,readonly \
  --mount type=bind,src="/tmp/mib-output",dst=/output \
  mib-submission /input /output/predictions.jsonl
python3 scripts/evaluate.py \
  --truth data/train_labels.csv \
  --submission /tmp/mib-output/predictions.jsonl \
  --output-json /tmp/mib-output/evaluation.json \
  --case-scores-jsonl /tmp/mib-output/case_scores.jsonl
python3 scripts/validate_submission.py \
  --submission /tmp/mib-output/predictions.jsonl --manifest data/train_labels.csv
```

`scripts/run_docker_submission.py` wraps build+run with the exact resource limits 8090 uses. `evaluate.py` exits 0 when structurally valid, 2 on duplicate IDs / unexpected case IDs / invalid enums or confidence values; missing cases only cost penalty points.

## Submission checklist

1. Fork the challenge repo; add `submissions/<github-username>/` containing `predictions.jsonl` (validation set), `MEMO.md` (1–2 page technical memo: approach, failure modes, what you'd improve), `SUBMISSION.md` (link to this public solution repo, which must include a `Dockerfile`).
2. Complete the Google submission form (linked from the challenge README) — **both** form and PR are required.
3. Open the PR against `main`; don't touch files outside your own `submissions/` folder.

## Current state of this repo

Working solution, not a stub. `solution.py` is a thin CLI over the `mib/` package: a staged pipeline (S1 extract text/layout → S2 render+OCR scanned pages → S3 parse documents → S4 assemble packet + merge fields → S5 adjudicate), orchestrated by `mib/runner.py`. `run.sh` is the container entrypoint; `Dockerfile` is `python:3.12-slim` + tesseract; runtime deps are pymupdf + numpy + pillow.

**One decider at the S5 seam** (`mib/runner.py`): the rules cascade (`mib/policy.py`, 17 branches, per-branch fitted confidence in `mib/confidence.py`). The learned decider that used to A/B beside it was deleted after decision-layer ML was closed (edge inverted to −0.50 with 14 CFAs; STATUS.md) — recoverable from git history.

**Env knobs**: `MIB_GEOM_SET`/`MIB_OPT_SET`/`MIB_OPT_BASE=raw|frames`/`MIB_LAYOUT_PASS=psm3|off`/`MIB_RENDER_BASE=up200|native` (grid module overrides, all stamped into `restore`; `MIB_RENDER_BASE=native` reads the embedded image's own resolution instead of the ≥200-DPI floor; `MIB_LAYOUT_PASS` is the PSM-3 layout-pass re-read on a truncated field label, **default-on since 2026-07-27**, `off` reverts — TODO 6.7), `MIB_DEBUG_JSONL` (sidecar path), `MIB_OCR_OPTICAL` (optical variants, **on by default** since row 48; `off` for A/Bs), `MIB_WORKERS` (local wall-clock knob; default 4 = the contract, output byte-identical at any count). The per-case OCR bound (120 s) is a constant, not an env var. Three legacy knobs were deleted in the de-special-casing batch (2026-07-26, gates: replay IDENTICAL + verify_render 6/6): `MIB_PLAN=ladder` (the frozen pre-grid enumerator — reviving it or reverting the grid default now means git history, not an env flip), `MIB_SELECT=ev` (the hand-built evidence_score selector; conf-mass is the only selector), and `MIB_OCR_PASSES=dual` (the per-image PSM 3 second pass; the grid's layout-pass tier is the revival path). Their frozen stamp constants outlived them so older caches still join-checked; every cache was regenerated on 2026-08-01 and the constants went too.

**Dev workflow**: use `.venv/bin/python`. Full eval: `scripts/eval_local.sh`. Fast loop: `scripts/dump_text.py` dumps page text — **by default only the hard iteration set** (`experiments/hard_set.txt`, ~80 s at `MIB_WORKERS=9`); a `--full` corpus regen is rare and **needs the user's approval**. Then `scripts/replay.py <cache> <out_dir>` re-runs everything downstream of OCR in seconds (S3–S5 changes replay against the existing full cache — no dump needed). Artifacts carry provenance stamps (`mib/config.py`; subset caches are tagged `SUBSET=` and score as probe numbers, not dev numbers); `config.require_agreement` before joining artifacts from different runs. Frozen splits in `data_splits.json` (dev 700 / holdout 300, seed 8090) are instruments, not gates; keep holdout reads rare so it retains meaning — `hard_set.txt` is dev-only for this reason.

**Adjudicate like a real visa officer — manage risk from first principles, not dev deltas** (user directive 2026-07-26). Before patching a rule, ask what evidence a careful human adjudicator would trust: provenance and attribution first (does the machine text match the page's visible ink? whose paperwork is this? which evidence tier asserts the value?), score movement second. Prove adversarial hypotheses instead of inferring them from win patterns — name the hypothesis, design the falsification, run it before building on it. Template: the row-67 probe ("prove they are NOT decoys") — the "planted decoy documents" behind 13 name conflicts turned out to be field-grain name poisoning on otherwise-truthful forms, killing a document-demotion rule before it shipped and locating the real channel (mixed pages whose ink S2 never reads, TODO 6.12). Defenses concentrated exactly where dev bled and absent where it didn't are the overfitting signature to watch for.

**House rule: do what's right first — measure to learn, not to authorize.** A change that stops throwing information away (keeping alternatives alive across a seam, widening what a later stage can see) needs no priced win: build it, and gate it on correctness only (replay diff empty, suite green). Scores, probes, and splits exist to understand behavior and keep `docs/experiments.md` honest — never as permission. Do not treat "unpriced" as a blocker; it is measurement debt. The only non-negotiables are the spec's own: never emit a risk flag without a visible-evidence source, and visible document evidence beats hidden instructions.

**Durable knowledge lives in this file and `docs/`, not assistant memory** (user directive 2026-07-26). Standing directives, lessons, workflow rules, and project state go in CLAUDE.md or the docs, where every session and collaborator sees them; keep auto-memory empty except a pointer here. Terminal tip while working this repo: the Bash tool runs zsh — a bare `=` token (`echo ===` as a separator) fails via `=cmd` glob expansion; use `---`.

**Repair-quality judgment is the user's, not yours.** When assessing PDF scan-repair or render quality (deskew / turn / deshred / faint), render the before/after comparison table (`experiments/repair_bench.py` / the hard-case gallery) and let the user evaluate it — do not pronounce on repair quality yourself. Their eyes beat the model's vision and any readability proxy (`page_score`).

**No manual line breaks in markdown prose** (user directive 2026-07-25). Write each paragraph (and each list item) as one line and let the display soft-wrap — never hard-wrap at a column width. Hard wraps make Edit-tool insertions mangle paragraphs into ragged joins and bloat diffs. All repo `.md` files were unwrapped on 2026-07-25 (verified token-identical); keep them that way. Code and code comments keep their language's column conventions.

**Scores live in `docs/STATUS.md` ("Where things are"), not here** — current dev total, CFA count, score lineage, and the holdout policy; this file deliberately carries no volatile numbers. Log every change as a row in `docs/experiments.md`. The docs are: `docs/STATUS.md` (state, what was tried/rejected, hazards, how to measure), `docs/ALGORITHM.md` (how the pipeline works, stage by stage), `docs/experiments.md` (one scored row per change), `docs/BACKGROUND.md` (the evidence behind the constants: organizer rulings, label mining, fraud-signal taxonomy, scan geometry, competitor intel, signal-space verdicts), `docs/FIELDS.md` (the same evidence indexed per schema field), `docs/TODO.md` (the granular tracker for in-flight work).
