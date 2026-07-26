# Status: what we tried, where things are

_Last updated: 2026-07-25. Rewrite this file in place; do not append._

The front page. [experiments.md](experiments.md) has one scored row per change,
[ALGORITHM.md](ALGORITHM.md) describes how the pipeline works, [BACKGROUND.md](BACKGROUND.md) holds
the evidence behind the constants (§6 is the signal-space verdict table), [FIELDS.md](FIELDS.md)
indexes the same evidence per schema field, and [CLAUDE.md](../CLAUDE.md) briefs a newcomer on the
problem. This file answers the two questions those don't: **what has been tried**, and **where are
we right now**.

---

## Where things are

**Working tree: dev 122.44, CFA 0, 0 missing rows** (row 34, uncommitted: red-strikethrough value
cells are voided — a document crossing out its own printed value, e.g. a fee receipt's struck
`unpaid`, is not sourceable evidence; +0.20, fixing two struck-fee false denials). HEAD (`54c38ec`)
is at **122.24** (rows 32–33).

The load-bearing facts of the current state:

- **Row 30 (`4afeb58`) is the keystone: the OCR ensemble crosses the seam intact.** `read_case`
  returns every reading per page, the cache stores them all, non-text fields are settled by a
  plurality vote across all readings, and risk flags union across losing variants. Attribution is
  clean — the same fresh ensemble dump replayed with frozen pre-keystone behavior scores exactly
  the row-28 baseline 121.36, so the merge is worth **+0.74 on identical substrate**.
- **Shipped config** is the full restoration ladder (fixed in code — the `off`/`skew`/`turn` rungs
  are no longer selectable), exhaustive OCR (early stop removed, row 16), single PSM 11 pass, rules
  decider. `confidence_table.json` is fitted on the shipping keystone+struck substrate (rows 31 and
  34; `confidence_table.meta.json` stamps it), which discharges the row-28 refit hazard.
- **The rules cascade is the only decider.** The learned decider is deleted (row 27): re-measured
  on the 119.10 substrate its edge **inverted** (was +1.16 class pts at 115.20, then −0.50 with 14
  CFAs vs rules' 0), and decision-layer ML is closed, not shelved — the remaining classification
  loss (`fee_unknown`, `b13_census`) is information-limited, not model-limited (question 5). The
  code lives only in git history.

Score lineage, all dev-700, CFA 0 at every step (one row per change in experiments.md): 115.20
(ship `skew`, row 15) → 115.43 (per-field preference, row 15b) → 118.63 (exhaustive OCR, row 16) →
119.10 (P3 parse + flag extraction, row 18) → 121.36 (full ladder priced, row 28) → 122.10
(keystone, row 30) → 122.24 (registry recovery, row 32) → 122.44 (strike void, row 34). Holdout
113.46 at tag `v1`, one read, taken when dev was 115.43.

**Overfitting picture.** `scripts/audit_constants.py` refits every label-fitted constant 5-fold
within dev and scores out-of-fold: honest dev was 118.86 vs the then-reported 119.10 — a bias of
−0.23 that is **entirely the confidence table** (row 21). `STALE_CUTOFF`, the embargo world lists,
and the three mined revoked-sponsor ids re-mine *identically* from every fold and cost nothing.
That is a good result but a narrow one — it does not explain the v1 dev→holdout gap of −1.97, so
the cascade's *structure* (branch order, which branches exist, ~10 hand-tuned thresholds) is the
prime suspect and is unaudited (question 8).

**Cross-case inference is allowed and used.** An earlier reading of the anti-gaming rules argued
against computing corpus-level statistics at runtime. That was backwards: the submission already
ships a *hardcoded list of sponsor ids derived from train labels*, and a runtime statistic uses no
labels and no per-PDF keys, so it is strictly the less exposed of the two. `mib/corpus.py` recovers
the mined ids from recurrence structure alone (row 23) — a provable no-op on train and on the
5,000-case validation set (row 24), kept purely as private-set insurance.

---

## What we tried and kept

Mechanism, not just the delta — the deltas and full evidence are in [experiments.md](experiments.md).

- **Ship `skew` as the default** (+0.70, row 15). The default was `off`, so a container built from
  the last commit scored 114.50 while the log's headline read 116.88. Only the default moved.
- **Split the pipeline at the cache boundary** (0.00, byte-identical; `b5542cb`, instruments in
  row 15). `runner.read_case` (S1+S2, expensive, impure) and `runner.predict_from_evidence`
  (S3→emit, pure) — which is what makes the seconds-long replay loop possible at all.
- **Per-field candidate preference** (+0.23, row 15b). `packet.docs` sorted by `(doc_type, source)`
  ranks whole *documents*, so an OCR'd high-trust document won every field at once over a clean
  text-layer copy elsewhere. Prefer the clean read; settle ties by field-manual trust order.
- **Runner robustness** (0.00 on train, by design, row 17). Streaming ordered writes (a container
  killed at the 30,000 s limit is scored on real partial output, not an empty file), a NEEDS_REVIEW
  fallback row instead of a dropped case, `case_id` coercion, the 120 s per-case OCR bound, pinned
  `spawn`.
- **Exhaustive OCR** (+0.21, row 16). The early stop settled for the first good-enough variant and
  spent the most OCR on the hardest pages; removed outright (restoring it means a checkout).
- **P3 parse work** (committed `b926403`; the biggest single jump, 115.43 → 119.10 with rows 16/18).
  Prose extraction for the sponsor attestation (its facts are in wrapped *sentences*, so matching
  joins lines first), the `Purpose:` alias, unreadable risk lines no longer repaired into `"none"`
  (removed a CFA), damage markers (`[NAME CUT OUT]`) rejected as values, and calendar-checked dates
  — loose key matching was **kept** once `parse.valid_value` rejected non-existent dates
  (`2026-03-41`), fixing the root cause instead of dropping the feature.
- **OCR-robust flag extraction** (+0.46, row 18). Value-first fuzzy match with a confusion-weighted
  edit distance + runner-up margin guard; 46 cases gain a flag, 0 phantoms.
- **`embargo_world` un-shadowed + confidence-table staleness fixed** (row 22). A duplicate
  `planetary_embargo` inference in signals had turned the policy branch into dead code; the table
  had silently gone ~5 dev points stale (standing hazard below).
- **Digit-tolerant decoy filter and future-impossible year snap** (rows 25–26). An OCR page one
  glyph off the active case id is the applicant's own page misread, not a decoy (text-layer pages
  get no tolerance); OCR years ≥2028 one glyph off 2026 snap back, past years never do.
- **The keystone ensemble merge** (+0.74, row 30) — see "Where things are".
- **Eroded-label registry recovery** (+0.14, row 32). Faint-scan registry pages fuse label tails
  onto values (`World Ens Relay`); canonical-label-tail regexes + guarded snap recover them
  (MIB-000293 went from extraction 0/45 to fully read, landing an `embargo_world` DENIED — truth).
- **OCR-misread tolerance in `identity_conflict`** (row 33). An OCR-sourced registry name at
  similarity ≥0.75 to the applicant is agreement, not conflict; true conflicts mine at ≤0.5.
- **Red-strikethrough value void** (+0.20, row 34). A struck value is deterministically not the
  truth (452 receipts, 0 counterexamples); voiding it fixes struck-fee false denials, and the fee
  receipt is the one single-source field where the strike slips through (sponsor/visa strikes are
  100% paired with a rank-0 manual correction — kept as private-set insurance).

---

## What we tried and rejected

The more useful half. Each of these looked reasonable and was killed by a measurement.

- **Splitting `b13_census` — the last apparent ML prize, and it was a generator artifact.** The cell
  holds 6.25 points of loss and models find real signal in it: a random forest on the 66 features gets
  +2.01 class points (nested CV +2.11), which would have been the largest lever in the repo. It is not
  real. The signal is `n_scan_pages`, a *render* property rather than document evidence, and it leaks
  the label corpus-wide — **48.6% APPROVED for packets with no scan pages vs 25.1% for packets with
  them** — because the generator applies scan damage preferentially to DENIED cases. Every
  legitimate-looking alternative turned out to be the same split wearing a different hat: `clean` is a
  strict *subset* of `registry=CLEAR` (intersection = 68 = |clean|) and of "no sponsor doc", and
  conditioned on render state `registry=CLEAR` carries nothing (37.5% vs 30.8% APPROVED among scanned
  packets, n=8). Strip render + registry features and the gain collapses 2.01 → **0.86 with *more*
  CFAs (10 vs 9)** — fewer points for more false approvals is a model guessing, not finding structure.
  And it is unbuyable regardless: inside the 68 clean packets the truth is still 48/12/**8 DENIED**,
  and nothing in the feature space separates those 8 (a full-feature model turns 7 of them into false
  approvals). The CFA-0 gate was doing its job by refusing this. `fee_unknown` is deader still — all
  four model families lose to the rules baseline *and* every one adds CFAs. **Decision-layer ML is
  closed**, not shelved: seven model families now, ranking by regularization strength rather than
  capacity, which is an information limit and not a hypothesis-class one.
- **Promoting the learned decider on the stronger substrate** (−0.50 class, +14 CFA). The calibrated
  logistic beat rules by +1.16 class pts when rules scored 61.27 (the 115.20 substrate). Rules then
  climbed to 62.41 (flag extraction + P3 parse), and a re-measurement (dev 5-fold OOF) found the edge
  **inverted**: 61.91 vs 62.41, Brier .1229 vs .1169, 14 CFA vs 0. The learned decider is a
  residual-corrector on rules, so a stronger cascade leaves it nothing to correct. It bleeds most on
  `fee_unknown` (Δ−0.73), a data-availability wall, not an ML-winnable cell. No CFA veto both matches
  rules' CFA=0 and beats its class points — it is strictly dominated. The best naive dev read
  (MLP(16), 63.14) is the overfit trap: it buys the raw points with 20 CFAs. Holdout was deliberately
  **not** read on a model being rejected. The code was deleted outright in row 27.
- **Vocabulary passthrough for unseen values** (−0.08, then −0.04 after the preference fix).
  `mib/vocab.py`'s docstring promised unseen values pass through so private-set values survive.
  It measured as a *loss* twice: deleting a value snapping could not vouch for was quietly acting as
  a quality filter, letting a cleaner copy on another document supply the field. Its premise was
  also false — 1,000 train cases yield exactly 13 home worlds, 12 species and 10 purposes, and the
  vocabulary lists *are* those enumerations. A fourteenth world would be expected ~77 times in a
  sample that size, so the universe is closed and there was nothing to rescue. The xfail is now an
  ordinary test asserting the deletion is deliberate.
- **Dual-pass Tesseract PSM 3+11** (+0.87 dev, CFA 0 — *not shipped*). The top competitor's recipe,
  and it does score (row 20). It is off because the cost never got a clean number. PSM 3 runs full
  layout analysis where PSM 11 just hunts text blobs, so on geometrically destroyed scans it pays
  superlinearly in the noise-inflated component count — slowest exactly where pages are worst, then
  multiplied ~4× by the variant fan-out. Median case 1.7×, but the **worst 5% run 10–13.5× and carry
  a third of the added cost**; the 120 s per-case budget fired on a real case, and the
  contract-limits timing run died at 486/1000. Kept reachable behind `MIB_OCR_PASSES=dual`. Reviving
  it means *gating* the PSM 3 pass, and the gate direction is unknown until we record which pass
  actually won the pages that gained.
- **`b13_census` → APPROVED wholesale** (+0.80–0.91 by attribution). Manufactures false approvals
  on the 12 truly-denied cases in that branch. **Split the branch, never flip it.**
- **300-DPI grayscale/autocontrast OCR retry** (+0.21 for 43× runtime, row 8). Those pages were not
  low-resolution, they were turned, skewed and shredded; ~5 ms of numpy on the geometry axis bought
  +2.38. *Diagnose the transform before scaling the compute.*
- **The hidden-text substring invariant** — the *test* was wrong, not the pipeline. It asserted no
  emitted value may appear only in hidden text. But the injected answer keys hold the **true**
  values, and correct OCR repair converges on them: page text reading `Home World: Woll-1081c` is
  repaired to `Wolf-1061c`, which is absent from the visible text and present in the injected key.
  A substring test calls that a leak; it is the opposite. Replaced with two differential tests —
  blank the hidden lines, and inject a hostile answer key; the record must not move either way.
  Both pass on all 71 fixture cases, which is a far stronger claim than the original could make.
- **A 45 s per-case OCR budget.** It truncated a real case purely because the host was busy. It is
  a wall-clock guard, so any value low enough to bite makes output depend on machine load. Now
  120 s — above the worst case ever measured (107 s) — so it bounds a runaway without firing
  normally.
- **A whole layer of signal-space candidates**, measured dead in one investigation: agreement-
  conditioned calibration, cross-doc-disagreement demotion, injection-presence as a decision input,
  and conf-filter/whitelist/binarization OCR retries. See [BACKGROUND.md §6](BACKGROUND.md) for the
  verdict table and the evidence-trust vs damage-quantity discipline behind it.

---

## How to measure anything

Everything in this section is an instrument, not a toll booth: the gates prove a refactor is
correct, the instruments build understanding, and the record (experiments.md) keeps us honest.
None of it is a precondition for doing what's architecturally right — see the house rule in
[BACKGROUND.md](BACKGROUND.md).

**The fast loop** — seconds per variant, instead of a 40-minute pipeline run:

```bash
scripts/dump_text.py <pdf_dir> output/cache/train_bands.jsonl   # once, ~20 min idle
scripts/replay.py output/cache/train_bands.jsonl output/replay_foo   # seconds
scripts/score_split.py output/replay_foo dev
```

**The two gates.** Use the right one — they cover opposite halves of the pipeline:

| change touches | gate | passes when |
| --- | --- | --- |
| S3–S5, emit (downstream of OCR) | `scripts/replay.py <cache> --reference <predictions.jsonl>` | prints `IDENTICAL` |
| S1–S2 (extract, render) | `scripts/verify_render.py` | re-reads real PDFs, reproduces cached page text |

`replay.py` starts *from* the cache, so it proves nothing about the code that produces it. That is
why `verify_render.py` exists, and it has already caught a real regression.

**The three instruments:**

- `scripts/attribute.py <eval_dir> <split>` — splits classification loss per `(branch, prediction)`
  into *relabel* (the entire ML ceiling) vs *finer partition*. It reproduces the official scorer's
  classification number exactly, which is what makes the two comparable.
- `scripts/recoverable.py <cache> <eval_dir> <split> [branch]` — is the truth value even present in
  the text? Splits misses into `absent` (a rendering problem) and `parsefail` (a parsing problem),
  in points.
- `pytest` — 71-case characterization fixture, invariants, regression. Seconds.

**Provenance.** Every artifact is stamped with its render config and git rev (`mib/config.py`), and
consumers call `config.require_agreement` before joining. This exists because a metric run silently
joined `skew`-derived text against `bands`-derived predictions and produced confident wrong numbers.

**Container vs host** (row 19): the shipped container reproduces the host score (119.17 vs 119.10
at the time — within nondeterminism), but host and container OCR are **not byte-identical** — the
Debian `tesseract` is a different build, moving 7/1000 adjudications and ~4% of `applicant_name`.
The differences roughly cancel and CFA stays 0, so host replay is a faithful proxy; the number that
ultimately ships still comes from container output.

---

## Open questions

| # | question | what would settle it |
| --- | --- | --- |
| 1 | ~~Is ~117.98 real?~~ **ANSWERED** | dev **119.10** confirmed by full-pipeline eval (row 18); the partial-cache 117.98 superseded |
| 2 | ~~Does the pipeline fit the runtime budget?~~ **ANSWERED** | full contract-limits run at `skew`: 0.54 s/PDF, ~11× headroom (row 19, `scripts/run_docker_submission.py`) |
| 3 | ~~Can `turn`/`bands` (+1.68) be made affordable?~~ **ANSWERED on score** (row 28: +2.09, and it already ships). Remaining half: **re-run the Docker gate at the ladder's cost** — laptop cost is ~2.4×/case vs skew, against ~11× measured headroom, so the expectation is "fits"; the gate decides a revert, not an adoption. (The other row-28 follow-up, the confidence-table refit, is done — rows 31/34) |
| 4 | ~~Is CFA 0 a hard gate or a priced cost?~~ **MOOT** | learned decider deleted; rules run at CFA 0 with no veto. Revives only with a future decider (then the honest count is the OOF CFAs — 12 on the 115.20 substrate, 14 on 119.10 — not the in-sample 5) |
| 5 | ~~Where do the remaining classification points go?~~ **ANSWERED — and they are not reachable** | `fee_unknown` (7.11) has no signal: all model families lose to rules and add CFAs; the fee is genuinely absent from the document (visible 0.0%, 3.6% with OCR, hidden-only in 41 cases). `b13_census` (6.25) has signal, but it is the `n_scan_pages` render artifact. See the rejected list |
| 6 | Can the `illegible_biometrics` structural gap be read or argued? (~+0.8 dev ceiling) | **Mostly settled by two-mode label mining — full evidence in [BACKGROUND.md §3](BACKGROUND.md).** Mode A (printed token): P=1.00, we emit 91/94, and the misses are matcher/gate strictness, not absence — 3 doc-type-gate misses plus ≥5 user-verified pages that print the token OCR-mangled past `match_flag_token`. That is the fix family: printed-but-untyped-page doc gate + label-anchored value recovery on `Observed flags:` lines. Mode B (condition-derived, no token printed): every observable proxy tops out at P≤0.38 — emission is expected-negative on a weight-8 exact-set field; dead |
| 7 | Does Tesseract word-level confidence (TSV `conf`) resolve the valid-vs-valid vote ties? | Capture TSV in `_tesseract` (same recognition pass), store per-line conf in `Read`, A/B a conf-weighted vote offline on the ensemble cache. Evidence so far: the variant-merge probe found ties where generation order picks wrong in one direction each (025 date / 037 name) and only word conf wins both; and the MIB-000990/252 ladder autopsy showed every conf metric ranking the repaired variants above the tilted raw render that `evidence_score` chose — the tilted winner's margin came from a well-formed-but-**wrong** value (`MIB-000000`, misread of 000990, +1 via CASE_ID_RE). Shape-validity credits confident garbage; the engine's own conf does not. Per-line conf is the right unit for the flag scan; page-level conf mass just rewards word count |
| 8 | Does the fitted-constant audit generalize to the cascade's *structure*? | **Open, and it is the real question.** Row 21 cleared the four fitted values at −0.23, which does **not** explain the v1 dev→holdout gap of −1.97. Branch order, which branches exist, and ~10 hand-tuned thresholds were also picked on dev and are unaudited |
| 9 | ~~Do the fitted constants survive contact with unseen data?~~ **ANSWERED, label-free** | row 24 (`output/val_shift`, 5,000 validation packets): sponsor recurrence transfers **exactly** (same six ids, gap 14.6×); `STALE_CUTOFF` is correct but its margin fell 37 d → 2 d (logged risk, not a change); render damage **halved**, independently confirming the `b13_census` artifact would not have transferred |
| 10 | Does the dev→holdout gap still hold? | holdout untouched since 113.46 at `v1`; read only at a milestone |
| 11 | Is hidden-line *content* worth mining as a signal? | untried — see note below |

**On question 11 (untried lever).** Today hidden spans are split off in `stages/extract` and barred
from *evidence*: no field value can source from them (`textmatch.sourceable_text` = visible + OCR
only) and no policy branch reads them — `test_hidden_text_cannot_change_the_output` guards this.
`textmatch.hidden_text()` reconstructs the hidden string but has no callers at all (verified by
grep) — kept only for this lever. The lever: mine that content as a **flag only** — e.g. a hidden
`SPN-####`/date/flag that conflicts with the visible value could raise
`sponsor_mismatch`/`identity_conflict` or an injection tell. Hard constraint: it may only ever
*flag*, never source a value or flip a branch toward the hidden value, or we reward the injection
the guard exists to defeat. Unmeasured; no row yet.

---

## Known hazards

Each of these has cost time once already.

**Render-derived features carry the generator's damage-vs-label coupling.** Thirteen of the 66
features in the deleted `mib/features.py` described how the PDF was *produced* rather than what it
says (`n_pages`, `n_scan_pages`, `hidden_present`, the `has_*` doc-presence flags, …). The
generator damages DENIED packets more often (48.6% vs 25.1% APPROVED, clean vs scanned), so every
one is a partial label proxy with no reason to survive to a private test set. This is what made the
`b13_census` prize look real. The features are gone (row 27), but the lesson stands: **nothing new
may condition on render-derived properties**, and any future model that scores well should be
re-checked with them removed before it is believed.

**A miner that reads extracted values measures its own extraction noise.** The first pass of
`audit_constants.py` mined `home_world` from pipeline output (87.9% accurate) and reported a −1.23
fitting bias with a CFA. Both were the audit's own errors: Wolf-1061c is 32/32 denied non-DIP by the
labels but 22/26 by extraction, so it fell out of the partial-embargo list. Constants are derived
from labelled data; anything re-deriving them must read the label columns.

**An in-place monkeypatch needs copies, not the live objects.** `audit_constants.patched` clears its
targets before writing them, so passing `policy.FULL_EMBARGO_WORLDS` itself as the "keep this one
unchanged" value emptied it. Three attribution runs executed with empty embargo sets and an empty
confidence table and looked plausible. The no-refit control (must reproduce the baseline exactly) is
what caught it and is why that control is not optional.

**S3's vocabulary silently drives S2's OCR.** `stages/render.evidence_score` scores a page by how
many labels `parse.key_for` recognizes. Adding one `KEY_MAP` entry (`purpose`) changed which OCR
variant won — **in both directions** — and invalidated the page-text cache. `verify_render.py`
caught it at 4/6 identical. So S2 is *not* a pure function of (image, render config), and **a parser
change can silently invalidate the cache**. The fix is to give `render` its own frozen label set for
page-quality scoring; that is a behaviour change and needs its own measured row.

**Better extraction can remove a safety net.** Filling `visa_class` and `sponsor_id` on MIB-000672
stopped the missing-field guards from firing, and the case fell through to `clean_approve` — a
catastrophic false approval. The underlying defect was worse: its B-13 risk line had OCR'd to
debris, and `vocab.snap` **repaired that debris into `"none"`**, fabricating a positive assertion
that no risk flag was observed. Fixed in two places — unreadable flags now return `None`, and the
risk-concealment census asks whether the risk line was *read*, not whether a slip was detected.
Generally: **when extraction improves, re-check the branches whose job was to catch missing data.**

**Nothing refits `confidence_table.json` automatically, and nothing warns when it goes stale.** It
silently encodes the accuracy of whatever pipeline last ran `scripts/fit_confidence.py` — it once
sat ~5 dev points stale (row 22). `confidence_table.meta.json` stamps what it was fitted on: check
it before trusting a calibration number, and refit after any change that moves branch membership.

**Parallel sessions interfere, and the interference looks like a result.** The journal once
recorded a phantom "extraction changed" that was actually an in-flight `packet.py` edit landing
between two replays. If two sessions are editing, pin the tree (`git stash`, or replay from a fixed
commit) before an A/B, and diff the non-decision fields to catch it.

**Laptop timings are not evidence.** Wall figures on rows 11–14 were wrong by 10–50× and a
conclusion drawn from them had to be retracted from two documents. Only
`scripts/run_docker_submission.py` under the real limits settles a runtime question.

---

## Immediate next steps

Highest leverage first. Steps 3–4 are broken into anchored sub-items in [TODO.md](TODO.md)
(the granular tracker for the OCR-quality work, hard-examples-first):

1. **Audit the cascade's structure, not just its constants** (question 8). Branch order and the ~10
   hand-tuned thresholds are the remaining dev→holdout-gap candidates and nothing has measured them.
   Do this before spending a holdout read — a holdout read cannot tell you *which* choice is overfit.
2. **Re-run the Docker gate at the shipped ladder's cost** (question 3). The ladder already ships,
   so this decides a revert, not an adoption; expectation is "fits" (~2.4× laptop cost against ~11×
   measured headroom).
3. **TSV word confidence** (question 7): capture in `_tesseract`, A/B the conf-weighted vote and the
   per-line-conf flag scan offline against the ensemble cache.
4. **The `illegible_biometrics` fix family** (question 6): printed-but-untyped-page doc-gate fix +
   label-anchored `Observed flags:` value recovery (~+0.8 dev ceiling, mode-A misses only).
5. Deferred: the `MIB_OCR_OPTICAL` faint-scan track (gated, unmeasured); reviving dual-PSM needs the
   winning-variant instrument first (row 20). Holdout read only at the next milestone.
