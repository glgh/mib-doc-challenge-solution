# Status: what we tried, where things are

_Last updated: 2026-07-23. Rewrite this file in place; do not append._

The front page. [experiments.md](experiments.md) has one scored row per change,
[ALGORITHM.md](ALGORITHM.md) describes how the pipeline works, [BACKGROUND.md](BACKGROUND.md) holds
the evidence behind the constants, [SIGNAL-SPACE.md](SIGNAL-SPACE.md) maps the evidence-modality
investigation (which discarded signals pay and which leak), and [CLAUDE.md](../CLAUDE.md) briefs a
newcomer on the problem.
This file answers the two questions those don't: **what has been tried**, and **where are we right
now**.

---

## Where things are

**Committed: dev 122.10, CFA 0, 0 missing rows** (row 30, `4afeb58` — the keystone: the OCR
ensemble crosses the seam intact, non-text fields are settled by a plurality vote across all
readings, and risk flags union across losing variants. Attribution is clean: the same fresh
ensemble dump replayed with frozen pre-keystone behavior scores exactly the row-28 baseline
121.36, so the merge is worth +0.74 on identical substrate. The 119.10–119.27 numbers quoted
before 2026-07-24 were skew-substrate replays; 119.10 was verified 2026-07-23 by a full-pipeline
eval, `output/eval_head`, 515s for 1,000 PDFs). Shipped config is the
full restoration ladder (fixed in code) with the **rules** decider. The stage-seam decoupling, exhaustive-OCR default
(row 16), P3 parse fixes, and OCR-robust flag extraction (row 18) that earlier drafts of this file
logged as "in flight / uncommitted" are all committed now (`ba59fcd`, `b926403`); `bands` has never
been timed under the contract gate, and the Docker parity result (row 19, ~11× headroom at `skew`)
weakens the old over-budget presumption — see question 3. The sections below still narrate that work
as a dirty tree — read them as history, not current state.

**The learned decider is deleted (2026-07-24 review sweep, row 27).** Re-measured on the 119.10
substrate, its edge over rules **inverted** (was +1.16 class pts at 115.20, then −0.50 with 14 CFAs
vs rules' 0 — see rejected list and `experiments.md`); after decision-layer ML was closed the code
was removed outright: `mib/{decision,features}.py`, `decision_model.npz`,
`scripts/{train,export}_decision.py`, and the `MIB_DECIDER`/`MIB_CFA_VETO` knobs all live only in
git history now.

**HEAD's real score is dev 121.36, CFA 0 (row 28)** — measured 2026-07-24 on a fresh full-ladder
cache, closing the gap the review found: `c905f00` fixed the restoration ladder ON and `4767919`
changed the ink mask with no scored row, so every number from 119.10 to 119.27 described the old
*skew* substrate, not the shipping config. The re-price (question 3) came back **+2.09** — the
prize grew on the stronger substrate rather than shrinking. Two follow-ups before trusting it
fully: re-run the Docker gate for the ladder's cost (it already ships, so the gate decides a
revert, not an adoption), and refit `confidence_table.json` on the bands substrate (branch
membership moved; the 121.36 carries the skew-fitted table).

**Decision-layer ML is now closed, not merely shelved (2026-07-24).** The last apparent prize — a
+2.0-point split of `b13_census` — traced to a data-generation artifact, and `fee_unknown` has no
signal at all. Both are in the rejected list; question 5 is answered and the remaining classification
loss is not reachable by any decider over these features.

**The rules cascade is far less overfit than assumed, and the audit says where to look next.**
`scripts/audit_constants.py` refits every label-fitted constant 5-fold within dev and scores
out-of-fold: **honest dev 118.86 vs reported 119.10, a bias of −0.23 that is entirely the confidence
table** (row 21). `STALE_CUTOFF`, the embargo world lists, and the three mined revoked-sponsor ids
re-mine *identically* from every fold and cost nothing. That is a good result but a narrow one — it
does not explain the v1 dev→holdout gap of −1.97, so the cascade's *structure* (branch order, which
branches exist, ~10 hand-tuned thresholds) is now the prime suspect and is unaudited (question 8).

**Correction on cross-case inference.** An earlier reading of the anti-gaming rules argued against
computing corpus-level statistics at runtime. That was backwards: the submission already shipped a
*hardcoded list of sponsor ids derived from train labels*, and a runtime statistic uses no labels and
no per-PDF keys, so it is strictly the less exposed of the two. `mib/corpus.py` now recovers the
mined ids from recurrence structure alone (row 23), gated on an ablation because on the shipped
config it is a provable no-op.

| step | commit | dev | note |
| --- | --- | ---: | --- |
| pre-P0.5 shipped default | `d6427f8` | 114.50 | container shipped `off` while the log's headline read 116.88 |
| P0.5 instruments + ship `skew` | `ff497f6` | **115.20** | +0.70 for one line; banks a result rows 11–14 had already measured |
| P1a staged seams + records | `b5542cb` | 115.20 | pure refactor, byte-identical |
| P1b per-field candidate preference | `0285f25` | **115.43** | extraction 38.76 → 39.00 |
| P2 runner robustness | `0aa7031` | 115.43 | score-neutral by design |
| P3 parse fixes | `b926403` | **119.10** | committed (with rows 16, 18); loose-key kept + dates hardened, suite green |

Two sessions have been working this repo in parallel today:

| workstream | owns | landed |
| --- | --- | --- |
| extraction pipeline | `mib/{records,runner,config,cache,packet,parse,vocab}.py`, `mib/stages/`, `tests/`, most of `scripts/` | `ff497f6`, `b5542cb`, `0285f25`, `0aa7031` |
| decision layer | `mib/{features,decision}.py`, `mib/decision_model.npz`, `scripts/{train,export}_decision.py`, the S5 block in `mib/runner.py` | `adff813`, `a21fc36` |

### The P3 parse work — RESOLVED (the rest of this section is history)

> **Resolved 2026-07-23.** Everything below is **committed** (`b926403`), tree clean on `mib/`, suite
> **green** (`28 passed, 1 xfailed`), dev **119.10** confirmed by full eval (`output/eval_head`). Two
> corrections to the narrative that follows: (1) **loose key matching was KEPT, not dropped** — the
> root cause was fixed instead: `parse.valid_value` now rejects non-existent dates (`2026-03-41`,
> `2026-02-30`, `2026-13-01` all fail), so the "should come out" verdict no longer applies; (2) the
> characterization **value snapshot** was retired (`57e4e60`), so the "4 tests fail" note is obsolete.

The working tree held the P3 parse work: `mib/{packet,parse,policy,signals,vocab}.py`,
`tests/test_regression.py`, and the regenerated fixture. It is **not one change** and was not
committed as one:

**Solid** — suite green, CFA 0, five new regression tests, measured at 117.98 (`output/replay_p3d`):
prose extraction for the sponsor attestation, the `Purpose:` alias, the fabricated-`"none"` fix,
the flag-evidence guard on the risk-concealment census, and damage-marker rejection.

**Should come out** — corroborated loose key matching. It scores +0.29 on top
(`output/replay_p3e`, 118.27, CFA 0) and it admits `arrival_date = 2026-03-41`, because
`parse.valid_value` checks the *shape* `\d{4}-\d{2}-\d{2}` and never asks whether the date exists.
It also produced `declared_purpose = 'Uipremenes ainlamatic'` and flipped a policy branch on
MIB-000805. A change that scores well by admitting garbage is the kind this repo has been careful
to reject.

With the loose-key change still present, **4 characterization tests fail** — that is the fixture
being stale against it, not a defect. Removing it and regenerating should return the suite to green.

### Why 117.98 is provisional

It was measured by replaying a page-text cache that is only **~92% current** (11 of the first 133
rebuilt cases had different page text). The rebuild was stopped at 300/1000 — the host was
saturated by unrelated load, and a contended timing run is worth nothing. `output/cache/train_skew_p3.jsonl`
holds the partial rebuild. **Finish it and re-score before quoting the number anywhere.**

---

## What we tried and kept

Mechanism, not just the delta — the delta is in [experiments.md](experiments.md).

- **Ship `skew` as the default** (+0.70). The default was `off`, so a container built from the last
  commit scored 114.50 while the log's headline read 116.88. Only the default moved.
- **Split the pipeline at the cache boundary** (0.00, byte-identical). `runner.read_case` (S1+S2,
  expensive, impure) and `runner.predict_from_evidence` (S3→emit, pure) — which is what makes the
  seconds-long replay loop possible at all. `Read` became plural per page; `ocr_page` used to score
  several OCR variants and return only the winner, so an ensemble could not be written down.
- **Per-field candidate preference** (+0.23). `packet.docs` sorted by `(doc_type, source)`, which
  ranks whole *documents* — so an OCR'd high-trust document won every field at once over a clean
  text-layer copy elsewhere. `Miravoss` was losing to `Mirayoss`. Preferring the clean read and
  settling ties by field-manual trust order fixed both flagged cases against truth.
- **Runner robustness** (0.00 on train, by design). `pool.map` blocked until all 5,000 PDFs
  finished and only then wrote, so a container stopped at the 30,000s limit was scored on an
  **empty file**; verified that killing a run after 25s now leaves 24 valid rows. Plus: a failed
  case emits a NEEDS_REVIEW row instead of nothing, `case_id` is coerced, `spawn` is pinned.
- **Prose extraction + the `Purpose` alias** (+2.20, in flight). The sponsor attestation states its
  facts in sentences, so `parse_kv` saw nothing on it. The sentence *wraps*, so matching has to join
  lines first — `expected on Earth for reactor / maintenance.` splits a purpose across a newline.
- **Unreadable risk lines stop being repaired into `"none"`** (+0.35, in flight, and it removed a
  CFA). See hazards below.
- **Damage markers are not values** (0.00, in flight). 51 emitted values were literal document
  damage text — `[NAME CUT OUT]` as an applicant name. Scores the same as `unknown` either way;
  emitting document damage as an answer is wrong on its own terms.

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
- **Promoting the learned decider on the current substrate** (−0.50 class, +14 CFA). The calibrated
  logistic beat rules by +1.16 class pts when rules scored 61.27 (the 115.20 substrate). Rules then
  climbed to 62.41 (flag extraction + P3 parse), and a re-measurement on `output/eval_head` (dev
  5-fold OOF) found the edge **inverted**: 61.91 vs 62.41, Brier .1229 vs .1169, 14 CFA vs 0. The
  learned decider is a residual-corrector on rules, so a stronger cascade leaves it nothing to correct.
  It bleeds most on `fee_unknown` (Δ−0.73), a data-availability wall, not an ML-winnable cell. No CFA
  veto both matches rules' CFA=0 and beats its class points — it is strictly dominated. The best naive
  dev read (MLP(16), 63.14) is the overfit trap: it buys the raw points with 20 CFAs. Kept the rules
  decider; froze the npz as superseded. Holdout was deliberately **not** read on a model being rejected.
- **Vocabulary passthrough for unseen values** (−0.08, then −0.04 after the preference fix).
  `mib/vocab.py`'s docstring promises unseen values pass through so private-set values survive;
  only `declared_purpose` delivers it, and a strict-xfail test was standing by to confirm the bug.
  It measured as a *loss* twice: deleting a value snapping could not vouch for was quietly acting as
  a quality filter, letting a cleaner copy on another document supply the field. Its premise was
  also false — 1,000 train cases yield exactly 13 home worlds, 12 species and 10 purposes, and the
  vocabulary lists *are* those enumerations. A fourteenth world would be expected ~77 times in a
  sample that size, so the universe is closed and there was nothing to rescue. The xfail is now an
  ordinary test asserting the deletion is deliberate.
- **Dual-pass Tesseract PSM 3+11** (+0.87 dev, CFA 0 — *not shipped*). The top competitor's recipe,
  and it does score: extraction +0.48, classification +0.35, Brier slightly better, CFA still 0
  (experiments.md row 20). It is off because the cost never got a clean number. PSM 3 runs full layout
  analysis (components → columns → lines → reading order) where PSM 11 just hunts text blobs, so on
  geometrically destroyed scans it tries to infer a structure that isn't there and pays superlinearly
  in the noise-inflated component count — slowest exactly where pages are worst, then multiplied ~4×
  by our variant fan-out. Median case 1.7× (the naive "2×" intuition holds), but the **worst 5% run
  10–13.5× and carry a third of the added cost**, the 120 s per-case budget fired on a real case, and
  the contract-limits timing run died at 486/1000 taking the Docker daemon with it. Kept reachable and
  flag-gated exactly like `turn`/`bands`. Reviving it means *gating* the PSM 3 pass, and the gate
  direction is unknown until we record which pass actually won the pages that gained.
- **Corroborated loose key matching** (+0.29, admits invalid dates). See the dirty-tree section.
- **`b13_census` → APPROVED wholesale** (+0.80–0.91 by attribution). Manufactures false approvals
  on the 12 truly-denied cases in that branch. **Split the branch, never flip it.**
- **300-DPI grayscale/autocontrast OCR retry** (+0.21 for 43× runtime, [row 8](experiments.md)).
  The lesson that superseded it: those pages were not low-resolution, they were turned, skewed and
  shredded. ~5 ms of numpy on the geometry axis bought +2.38. *Diagnose the transform before
  scaling the compute.*
- **The hidden-text substring invariant** — the *test* was wrong, not the pipeline. It asserted no
  emitted value may appear only in hidden text. But the injected answer keys hold the **true**
  values, and correct OCR repair converges on them: page text reading `Home World: Woll-1081c` is
  repaired to `Wolf-1061c`, which is absent from the visible text and present in the injected key.
  A substring test calls that a leak; it is the opposite. Replaced with two differential tests —
  blank the hidden lines, and inject a hostile answer key; the record must not move either way.
  Both pass on all 72 fixture cases, which is a far stronger claim than the original could make.
- **A 45s per-case OCR budget.** It truncated a real case (MIB-000008) purely because the host was
  busy. It is a wall-clock guard, so any value low enough to bite makes output depend on machine
  load. Now 120s — above the worst case ever measured (107s), so it bounds a runaway without ever
  firing normally.

---

## How to measure anything

Everything in this section is an instrument, not a toll booth: the gates prove a refactor is
correct, the instruments build understanding, and the record (experiments.md) keeps us honest.
None of it is a precondition for doing what's architecturally right — see the house rule in
[BACKGROUND.md](BACKGROUND.md).

**The fast loop** — seconds per variant, instead of a 40-minute pipeline run:

```bash
scripts/dump_text.py <pdf_dir> output/cache/train_skew.jsonl skew   # once, ~18 min idle
scripts/replay.py output/cache/train_skew.jsonl output/replay_foo   # seconds
scripts/score_split.py output/replay_foo dev
```

A full 2×2 matrix of merge-preference × vocab-passthrough was scored in about a minute this way.

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
  in points. The optional branch filter answers "is this branch blocked by missing evidence, or by
  our use of it?"
- `pytest` — 72-case characterization fixture, invariants, regression. ~3s.

**Provenance.** Every artifact is stamped with its render config and git rev (`mib/config.py`), and
consumers call `config.require_agreement` before joining. This exists because a metric run silently
joined `skew`-derived text against `bands`-derived predictions and produced confident wrong numbers.

---

## Open questions

| # | question | what would settle it |
| --- | --- | --- |
| 1 | ~~Is ~117.98 real?~~ **ANSWERED** | dev **119.10** confirmed by full-pipeline eval (HEAD `17f82ae`, `output/eval_head`, 1,000 PDFs, 515s); ~117.98 and its partial cache rebuild superseded |
| 2 | ~~Does the pipeline fit the runtime budget?~~ **ANSWERED** | full contract-limits run: 0.54 s/PDF, 0.75 h/5,000, ~11× headroom (`scripts/run_docker_submission.py`) |
| 3 | ~~Can `turn`/`bands` (+1.68) be made affordable?~~ **ANSWERED on score** (row 28): the ladder is worth **+2.09** on the current substrate (dev 121.36, CFA 0) and already ships (`c905f00`). Remaining half: laptop cost is ~2.4×/case vs skew — **re-run the Docker gate** to confirm the budget (skew had ~11× headroom, so a 2.4× tail should fit; the gate decides a revert, not an adoption) |
| 4 | ~~Is CFA 0 a hard gate or a priced cost?~~ **MOOT** | learned decider shelved; rules run at CFA 0 with no veto. Revives only with the learned decider (then the honest count is 12 OOF CFAs) |
| 5 | ~~Where do the remaining 16.66 classification points go?~~ **ANSWERED — and they are not reachable** | `fee_unknown` (7.11) has no signal: four model families all lose to the rules baseline and all add CFAs. `b13_census` (6.25) has signal, but it is the `n_scan_pages` render artifact — worth +0.86 with 10 CFAs once stripped, and the 8 DENIED inside its clean subset are inseparable. See the rejected list |
| 6 | Can the `illegible_biometrics` structural gap be read or argued? (~+0.8 dev ceiling) | 223 truth cases carry it; only 83 print it; current recall 54%, and the 103 misses are condition-derived — the B-13 is present but so damaged it isn't even detected as a document (76/103). Every cheap discriminator failed: dead pages are geometrically identical (612×792, aspect 1.3) and their debris carries no type tokens (findings.md 2026-07-25). Would settle it: a dead-page **doc-typer** (ensemble-wide fuzzy B-13 tokens, or census elimination — which expected doc is unaccounted for), or an organizer-ruling-compatible emission argument (an unreadable slip *is* visible evidence of illegibility) |
| 7 | Does Tesseract word-level confidence (TSV `conf` column) resolve the valid-vs-valid vote ties? | Capture TSV in `_tesseract` (same recognition pass, one dump regen), store per-line conf in `Read`, and A/B a conf-weighted vote offline on the ensemble cache. The earlier variant-merge probe found ties where generation order picks wrong in one direction each (025 date / 037 name) and only word conf wins both |
| 8 | Does the fitted-constant audit generalize to the cascade's *structure*? | **open, and it is the real question.** `audit_constants.py` says the four fitted values cost only −0.23 (row 21), which does **not** explain the v1 dev→holdout gap of −1.97. Branch order, which branches exist, and ~10 hand-tuned thresholds were also picked on dev and are unaudited |
| 9 | ~~Do the fitted constants survive contact with unseen data?~~ **ANSWERED, label-free** | row 24, `output/val_shift` (5,000 validation packets). Sponsor recurrence transfers **exactly** — same six ids, gap 14.6×. `STALE_CUTOFF` is correct but its margin fell 37 d → **2 d** (train's empty band was a small-sample artifact); 6 cases sit within ±7 d, so it is a logged risk, not a change. Render damage **halved** (14.9% → 7.3% clean packets), independently confirming the `b13_census` artifact would not have transferred |
| 6 | Does the dev→holdout gap still hold? | holdout untouched since 113.46 at `v1`; read only at a milestone |
| 7 | Is hidden-line *content* worth mining as a signal? | untried — see note below |

**Docker parity (question 2) is now answered.** `scripts/run_docker_submission.py` runs the container
under the exact contract flags (image **0.13 GiB** vs 4 GiB cap, `--network none`, read-only root,
tmpfs `/tmp`, 4 cpu / 8 GiB) over the **full 1,000-PDF** train set — not the earlier 12-PDF sample.
Result: **542 s wall → 0.54 s/PDF** (budget 6), projecting to **0.75 h for 5,000** (budget 8.3 h),
per-case `cost_ms` mean 2.16 / p90 4.14 / p99 6.43 / **max 8.33 s**. The heavy tail this repo feared
(p99 57 s, max 107 s) was **contention on the shared laptop, not the pipeline** — on 4 dedicated vCPU
it disappears. So `skew` fits at ~11× headroom, which is the lever that reopens `turn`/`bands` and
makes room for a second OCR pass (see experiments.md row 19).

Two facts fall out of the same run:
- **The shipped container reproduces the host score.** Scoring the container's own predictions:
  dev **119.17** (class 62.51 / extr 41.30 / calib 15.36 / CFA 0) vs host `eval_head` 119.10 — within
  nondeterminism. So the host replay loop is a faithful proxy for what actually ships.
- **But host and container OCR are not byte-identical**, because the container's Debian `tesseract` is a
  different build than the host's: 7/1000 adjudications (0.70 %) and ~4 % of `applicant_name` differ
  between the two full runs. The differences roughly cancel (class +0.10, extr −0.06) and **CFA stays
  0**, so this is a caveat, not a defect — but the number that ultimately ships comes from container
  output, and any final ship-decision score should be read there, not only from a host replay.

**On question 7 (untried lever).** Today hidden spans are split off in `stages/extract` and barred
from *evidence*: no field value can source from them (`textmatch.sourceable_text` = visible + OCR
only) and no policy branch reads them — `test_hidden_text_cannot_change_the_output` guards this.
But their *presence* is already a signal: `features.hidden_present` (`hidden_lines > 0`) feeds the
learned decider. What is **not** used is the hidden *content*. `textmatch.hidden_text()` reconstructs
the hidden string but has no callers at all (tests included; verified by grep) — kept only for this lever. The lever: mine that content
as a **flag only** — e.g. a hidden `SPN-####`/date/flag that conflicts with the visible value could
raise `sponsor_mismatch`/`identity_conflict` or an injection tell. Hard constraint: it may only ever
*flag*, never source a value or flip a branch toward the hidden value, or we reward the injection the
guard exists to defeat. Unmeasured; no row yet.

**On question 5**, the evidence is already in and it points away from OCR:

- `fee_unknown` (7.37 pts): its `fee_status` is visible in **0.0%** of cases, 3.6% with OCR, and
  hidden-only in 41 of 139. The fee genuinely is not in the document.
- `b13_census` (5.37 pts): **89 of 95** cases have no B-13 anywhere and only 6 are detection
  misses — but the other fields on those cases read at **98.0%**. It needs a split on evidence we
  already hold, not better reading.

---

## Known hazards

Each of these has cost time once already.

**Render-derived features carry the generator's damage-vs-label coupling.** Thirteen of the 66
features in `mib/features.py` describe how the PDF was *produced* rather than what it says:
`n_pages`, `n_scan_pages`, `n_ocr_fields`, `worst_doc_rank`, `n_fields_missing`, `n_corrections`,
`hidden_present`, and the six `has_*` doc-presence flags. The generator damages DENIED packets more
often (48.6% vs 25.1% APPROVED, clean vs scanned), so every one of these is a partial label proxy
whose relationship to the truth is a property of the data generator and has no reason to survive to a
private test set. This is what made the `b13_census` prize look real. The features are deleted along
with the learned decider (row 27), but the lesson stands: **nothing new may condition on
render-derived properties**, and any future model that scores well should be re-checked with them
removed before it is believed.

**A miner that reads extracted values measures its own extraction noise.** The first pass of
`audit_constants.py` mined `home_world` from pipeline output (87.9% accurate) and reported a −1.23
fitting bias with a CFA. Both were the audit's own errors: Wolf-1061c is 32/32 denied non-DIP by the
labels but 22/26 by extraction, so it fell out of the partial-embargo list. Constants are derived from
labelled data; anything re-deriving them must read the label columns.

**An in-place monkeypatch needs copies, not the live objects.** `audit_constants.patched` clears its
targets before writing them, so passing `policy.FULL_EMBARGO_WORLDS` itself as the "keep this one
unchanged" value emptied it. Three attribution runs executed with empty embargo sets and an empty
confidence table and looked plausible. The no-refit control (must reproduce the baseline exactly) is
what caught it and is why that control is not optional.

**S3's vocabulary silently drives S2's OCR.** `stages/render.evidence_score` scores a page by how
many labels `parse.key_for` recognizes. Adding one `KEY_MAP` entry (`purpose`) changed which OCR
variant won — **in both directions** — and invalidated the page-text cache. `verify_render.py`
caught it at 4/6 identical. This means S2 is *not* a pure function of (image, render config), so
the cache boundary the whole fast loop rests on is weaker than it looks: **a parser change can
silently invalidate your cache.** The fix is to give `render` its own frozen label set for
page-quality scoring; that is a behaviour change and needs its own measured row. Making `_key_for`
public during P1a legitimized this dependency instead of removing it — that was the wrong call.

**Better extraction can remove a safety net.** Filling `visa_class` and `sponsor_id` on MIB-000672
stopped the missing-field guards from firing, and the case fell through to `clean_approve` — a
catastrophic false approval. The underlying defect was worse: its B-13 risk line had OCR'd to
`Observed fans: =-*` / `rant` (truly `active_warrant`), and `vocab.snap` **repaired that debris
into `"none"`**, fabricating a positive assertion that no risk flag was observed. Fixed in two
places — unreadable flags now return `None`, and the risk-concealment census asks whether the risk
line was *read*, not whether a slip was *detected*. Generally: **when extraction improves, re-check
the branches whose job was to catch missing data.**

**The CFA-gate disagreement is moot for now — the learned decider is shelved.** The decision-layer
position was that catastrophic false approvals should be *priced, not banned* (EV-argmax charges −4,
with `MIB_CFA_VETO` as a demotion knob); the extraction side treated CFA 0 as a hard gate. The
question only bit while the learned decider was a promotion candidate — and the 2026-07-23
re-measurement retired it (strictly dominated by rules, which already run at CFA 0). The shipped
pipeline emits 0 CFAs on dev with no veto needed. If the learned decider is ever revived, this
decision comes back with it, and the honest number to argue from is the **12** out-of-fold CFAs (dev
5-fold), not the in-sample **5** that understated the risk ~2.4×.

**Parallel sessions interfere, and the interference looks like a result.** The journal records a
phantom "extraction changed under mlp" that was actually an in-flight `packet.py` edit landing
between two replays. If two people are editing, pin the tree (`git stash`, or replay from a fixed
commit) before an A/B, and diff the non-decision fields to catch it.

**Laptop timings are not evidence.** Wall figures on rows 11–14 were wrong by 10–50× and a
conclusion drawn from them ("wall-clock falls as restoration deepens") had to be retracted from two
documents. Only `scripts/run_docker_submission.py` under the real limits settles a runtime question.

---

## Immediate next steps

Done since this list was written: P3 parse committed (loose-key kept + dates hardened), suite green,
119.10 confirmed by full eval, learned decider shelved (which mooted the CFA-gate decision), and the
full-corpus Docker parity run (Q2) came back at 0.54 s/PDF with ~11× headroom. Then, 2026-07-24: ML
closed for good (question 5 answered), the constant audit landed at −0.23, `embargo_world` un-shadowed,
and `mib/corpus.py` added. What's left, highest leverage first:

0. **Audit the cascade's structure, not just its constants** (question 8). Row 21 cleared the four
   fitted values at −0.23, which leaves the −1.97 v1 dev→holdout gap unexplained. Branch order and the
   ~10 hand-tuned thresholds are the remaining candidates and nothing has measured them. Do this
   before spending a holdout read, since a holdout read cannot tell you *which* choice is overfit.

1. ~~Re-price the `turn`/`bands` prize~~ **DONE (row 28): +2.09, dev 121.36, CFA 0** — the prize
   grew on the stronger substrate (the inversion precedent did not repeat). Follow-ups now owned by
   the Q3 row: Docker-gate the ladder's cost, refit the confidence table on the bands substrate.
2. Decide the runtime-budget split between **dual-PSM** (`MIB_OCR_PASSES=dual`, in flight) and
   `turn`/`bands` — both now spend the same ~11× headroom, so they compete.
3. If step 1 says turn/bands still pays: wire orientation into `mib/imaging.py` + `render.py`, measure
   the **dev-population** A/B (not the 13-case hard set), guard on `render`'s own frozen label set
   (the vocab-coupling hazard), held to CFA 0. Otherwise the higher-leverage lever is the `b13_census`
   split (question 5) — `fee_unknown`/`b13_census` are where the points are, and neither is reading-limited.
