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
