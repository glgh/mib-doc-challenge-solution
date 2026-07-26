# TODO: the try-list (2026-07-25 OCR autopsies + cascade co-fire audit)

_The single todo tracker for this work. Update statuses in place; log scored results as experiments.md rows and link them here. Background evidence: BACKGROUND §3 (flag label mining, incl. the mangled-value geometry table), STATUS Q6/Q7. Fact-checked 2026-07-25 against docs, caches, and code; anchor strings verified verbatim in the ensemble caches._

**Working style (user directive): hard examples first.** Every item below names its anchor cases and the expected per-case outcome. An item is tried against its anchors before any full-corpus measurement is spent; the full dump regenerates only when something has already proven itself on the anchors. No "regenerate everything" as step one.

**The anchor set** (dev split except where marked; ladder galleries and page renders from 2026-07-25; similarity ratios throughout are on lowercase-alnum-normalized text — raw `SequenceMatcher` gives different numbers):

| case/page | what it shows | truth |
| --- | --- | --- |
| MIB-000990 p1 | tilted render beat deskewed sibling via well-formed-but-WRONG `MIB-000000` (+1 CASE_ID_RE); TSV conf ranks repaired variants higher; flag value shattered across 14 hard-ensemble reads: `Beghie_ ju. ics` / `Begibie_|.._ics` / `Bagte——I*` / `Hagia;` | `illegible_biometrics` printed — **recovered** (row 36, quorum) |
| MIB-000252 p2 | gray stock, blotchy toner; geometry fine, every rung optical-fails; mangle `Bagitie bematics` (difflib 0.53 — but weighted-sim 0.64, see BACKGROUND §3) | printed — **recovered** (row 36, single-read) |
| MIB-000595 p3 | shred bands stay offset after deshred (border itself shredded); `Mogible_ biometrics` → 0.83 either metric | printed — **recovered** (row 36, single-read) |
| MIB-000656 / 979 | token legible verbatim, but page's OCR header defeats `detect_doc_type` → flag scan used to skip the page | printed — **recovered** (row 35, gate deleted) |
| MIB-000771 | same doc-gate miss; token one glyph off (`Begible_biometrics`) — still resolved by `match_flag_token` once the page is scanned | printed — **recovered** (row 35) |
| MIB-000747 p5, MIB-000506 | `Observed flags: [RISK PANEL MISSING]` / `[RISK PANEL = NG]` read perfectly — damage marker, no token | **controls: must NOT emit** |
| MIB-000096 p2 | dead page: 8 variants all debris, no doc type; field block is a faint smudge | condition-derived flag — **control only** (Track 4: condition emission is dead) |
| MIB-000135 | no B-13 anywhere, all-text packet | condition-derived — **control only** |
| MIB-000065 p3 | text rotated 90° and cropped mid-field at page edge | info genuinely absent — **control only** |
| 025 date / 037 name | valid-vs-valid variant-vote ties where generation order picks wrong (STATUS Q7 / merge-probe `681bbee`) | **HOLDOUT — do not tune on these.** 1.4 must re-mine dev-split ties first |

Instruments already built: `output/cache/reads_hard.jsonl` (92-case hard ensemble, optical rungs on), `output/cache/train_bands.jsonl` (full-train ensemble, schema 2 with `struck` backfilled — supersedes `train_bands_reads.jsonl` as the current substrate), `experiments/mine_hard.py`, `scripts/replay.py` (frozen-behavior reference gate), `experiments/ladder_gallery.py` (promoted from scratchpad 2026-07-25).

---

## Track 1 — OCR confidence (Q7)

### 1.1 ☑ TSV conf probe — GRADUATED (user-judged galleries, 2026-07-25)
`experiments/conf_probe.py`, 13 pages (3 anchors + 10 stratified hard picks). Findings:
- **The `ev` pathology does not transfer**: 990's hallucinated `MIB-000000` line got conf 36 (page junk level); every repaired variant out-confs the tilted render `ev` crowned.
- **Metric shape matters**: plain mean is gamed by rotated few-confident-words reads; raw mass by debris volume; render-source boilerplate (footer `Packet MIB-… / page N`, watermark — conf 90+) biases any mass metric toward `render`. Winner: **guarded excess mass** = Σ max(0, conf−40) per word over non-page-furniture lines. User verdict on the 8 differ-pages: conf-pick no worse.
- **TSV line text ≠ stdout text on 93/111 reads** — "same pass, free conf" is false; 1.2 must run TSV additively (2nd pass) or re-baseline.
- Incidental: the `Packet MIB-XXXXXX / page N` footer premise of 3.3 is real on probe pages.

### 1.2 ☑ Conf captured in the pipeline — SHIPPED (rows 41 + 44)
Single invocation `txt tsv` (text byte-identical); `Read.conf` per-line tuples; schema 3 (row 41), then schema 4 added the tsv line TEXT as a 4th element (row 44, `52c12eb`) making per-line conf queryable. Subset workflow live: `output/cache/hard_conf4.jsonl` (82 hard-set cases) + `hard_conf4_opt.jsonl` (optical) are the iteration substrate; `verify_render` compares at the cache's conf arity and honors the cache's `select` stamp.

### 1.3 ☑ Conf selection SHIPPED as default (rows 41–43, dev 123.02 → 123.17, CFA 0)
Guarded excess mass (`records.conf_excess_mass`, baseline 40, positional footer guard) is `DEFAULT_SELECT=conf`; `MIB_SELECT=ev` remains for A/Bs until ev is deleted (Phase E). A/B: the 990 anchor flipped off the tilted render; 4 better / 4 worse fields, all four regressions dispatched — 523 by the identity_conflict variant-agreement clause (row 43), 708/1000 wait on per-line conf ties, 293 on per-field conf. Sensitivity: baseline 40–50 plateau, 30 loses. 1.5 died unbuilt. Confidence-table refit deferred to the next substrate change.

### 1.4 ☑ Vote overhaul: merge-don't-tie + per-line conf tie-break — SHIPPED (rows 45 + 47)
Row 45 (`612c615`): vote keys strip edge punctuation per token and (names only) merge stroke collapses `rn`≡`m` any-position / `ri`≡`n` token-final, rep = expanded form (census-bound; inner `ri` merge conflated `Miradane`/`Miradarie` and was position-restricted out). Genuine ties break by `_line_conf` (alnum-substring into the schema-4 tsv text), then first-seen. 6 better / 0 worse full-train. Row 47 (`f4c48ba`): inner-colon junk filter. REJECTED in the same batch: truncation pooling (consolidation strengthens whichever family fragments more — 250's decoy bloc outvoted truth; needs a per-page two-level vote) and intake-over-sponsor name preference (census says the opposite: sponsor-text right 28:5, sponsor-OCR right 12:4, but 14/16 conflicts already resolve). **Open residue**: ~9 vote-reachable name anchors (truncation ties 258/340/506/990, wrong-plurality 262/299/389/536) wait on per-line conf over the FULL corpus (schema-4 regen) — the 3-3 family ties these reduce to have no arbiter in a schema-3 cache. Prior page-level weighting + revoked-tie guard reverts stand (row 43).

### 1.5 ☐ `evidence_score` pathology fix (only if 1.3 leaves the score in the loop)
Value-pattern credit requires the value's line conf above a bar — a well-formed misread (`MIB-000000`) stops outscoring an honest partial read. Expected to die via 1.3.

### 1.6 ☐ GOOD_ENOUGH audit (rides along with 1.3)
Hand-picked 6 in `8d56832` as an early-stop bar; early-stop is dead (row 16). Today it gates only the optical rung (`render.py`) and tooling's "hard page" cut (`mine_hard.py`). Dev evidence-score distribution valley sits at ev=5, not 6 (session measurement, STATUS Q7). If the selection metric changes, re-derive this gate from the conf distribution. Expected to die via 1.3.

## Track 2 — flag-line recovery ("line present, matcher/gate too strict")

### 2.1 ☑ Delete the flag-scan doc-type gate — SHIPPED (row 35, dev 122.44 → 122.79, CFA 0)
Doc-type filter dropped from `observed_flags`; `has_flag_evidence`'s positive clause shares the widened scan; the `'flag' + none/clear` negative clause stays biometric-restricted (CFA-risk direction, unmeasured). Anchors passed (656/771/979 emit, 747/506 silent) — but the "exactly three cases change" expectation **failed in the good direction**: 18 cases changed, every added flag true (the gate was also blocking planetary_embargo/biohazard_red/identity_conflict/ memory_tampering on header-mangled pages; the P=1.00 mining had only measured illegible_biometrics). 3 correct NEEDS_REVIEW→DENIED flips, so classification moved after all. Regression tests from the mined lines + controls; confidence table refit (score-neutral).

### 2.2 ☑ Unified weighted flag-value resolver — SHIPPED (row 36, dev 122.79 → 122.86, CFA 0)
Absorbed old 2.2 *and* 2.3 as one mechanism. `vocab.match_flag_value` (confusion-weighted, alnum-normalized, whole-value); `snap("observed_flags")` refactored onto `match_flag_token` (the parallel per-token difflib@0.8 path is gone — one metric, one threshold vocabulary). Bars mined from the full safety table (`experiments/flag_probe.py --values`, 563 values): every false at ≤0.40/≤0.08, every true at ≥0.44/≥0.10 → single-read 0.55/0.15, quorum-2 at 0.44/0.10, OCR readings only. Anchors exact: 252/595 emit single-read, 990 by quorum, 747/506 / `biometrics ok` / `none` silent; replay diff was exactly the three anchors + holdout 438 (planetary_embargo by quorum-6, truth-confirmed). Regression tests shipped. The mining also surfaced the space-split class (`illegible biometrics` scores 1.00 whole, invisible to the token path). Broken-toner note: repair can't read 252/990 (optical rungs tried live in session — more mangles, no matchable value; *unrecorded* beyond commit `b1199a5`'s dev-wide 11-recovered/10-corrupted A/B), so this resolver was the only live path to that class.

## Track 3 — visual defects

### 3.1 ☐ Color-aware masking (`imaging`, pre-`to_gray`)
Saturation mask → paper white: stamps (252 red, 990 purple — session observation, unrecorded), SAMPLE DENIAL watermark (96/595), yellow highlights (65/252) are colored; evidence ink is black. **Anchors are controls here:** on all four autopsy pages the overlays miss the field lines, so expect no change — the value, if any, is elsewhere in the corpus. Try on the hard set first; before/after gallery to the user (house rule: their eyes judge repair quality).

### 3.2 ☐ Content-anchored deshred (`imaging.realign_bands`)
Fall back to text-line continuity when the printed border is destroyed (`_band_offsets` needs a full-width border and ≥20 readable rows; 595's border is itself shredded). **Anchor:** 595 p3 bands visibly align (gallery to user); its flag value should then read closer to matchable.

### 3.3 ☐ Footer registration anchor (exploratory, only if 1–2 stall)
**Premise unverified:** no doc records a `Packet MIB-XXXXXX / page N` footer at a known position — BACKGROUND §4 names the page *border* as the registration signal. First render a page sample and check the footer exists, survives crop/shred, and sits where claimed; only then design orientation + skew + case-id cross-check around it. Mostly a runtime win if real.

## Track 4 — dispositions (no action unless reopened)

- **Optical rung** (`MIB_OCR_OPTICAL`): REOPENED and flipped ON by default (row 48). Its ev-era killer (well-formed binarized garbage outscoring correct reads, 11 recovered / 10 corrupted unguarded) does not transfer to conf selection: hard-set A/B under conf measured 1 better / 0 worse / 2 neutral, and the one hazard it exposed (optical primary dropping the adjudicator Finding line on 065) was fixed structurally by the row-46 variant fallback. `ocr_optical` promoted to CRITICAL_KEYS (None-tolerant join guard).
- **Condition-based flag emission**: dead. P(truth | any observable condition) ≤0.38; the generator's B-13-included-then-destroyed vs never-included distinction is invisible in the PDF (BACKGROUND §3). Printed evidence only — which is also organizer ruling §1.
- **Docker gate for the full ladder** (STATUS Q3 second half): standing, unrelated to this track.

## Track 5 — cascade structure (2026-07-25 co-fire audit; instrument: `experiments/cofire_probe.py`)

_The audit evaluated all 16 branch predicates independently per dev case (STATUS question 8's order component). Probe stays in sync with `policy.adjudicate` order — it asserts first-match equivalence and reports mismatches._

### 5.1 ☑ Order audit + stale-over-fee reorder — SHIPPED (row 39, dev 122.86 → 123.02, CFA 0)
Order was decision-relevant on exactly one co-fire cell: `fee_unknown` preempting `stale_arrival`. Truth-side cell empty on all 1,000 labels (thin alone — ~1.6 expected under independence; the decision stands on the 7-of-8 truth-DENIED pipeline mix + CFA-safe direction); 8 corpus flips NR→DENIED. The priced cost is MIB-000096 — the dead-page anchor control, truth NR: its arrival read is *correct* (genuinely stale); the OCR ensemble vote fabricated visa `MED-3` against a truth of DIP-1, so `known_non_dip` armed on wrong-but-positive evidence and the case lost its staleness exemption. Pre-reorder it was right by accident via `fee_unknown`. Confidence refit + regression test (`test_stale_arrival_outranks_an_unknown_fee`). Open follow-up: the §6 drift check for this reorder is 5.6; the 096 failure class is 5.8.

### 5.2 ☐ Mode-B dead-page census assessment (the largest recoverable mass)
The 57 mode-B cases with **no detected B-13 on dead scan pages** are unassessed page-by-page (BACKGROUND §3's only open residue). The migration matrix shows `b13_census` holds **61 truth-clean packets** (~4.8 class pts ceiling, `attribute.py` "finer partition"). Render the pages, gallery to the user (their eyes judge — house rule): how many hold a human-visible B-13 that better restoration could read vs genuinely absent? Any recovery is upstream (S2/Track 1–3), never a census weakening — the census also guards ~14 truth-DENIED cases (the CFA pool).

### 5.3 ☐ Threshold + branch-existence audit (question 8 residue)
Branch order is settled (5.1); the ~10 hand-tuned thresholds and which-branches-exist remain the dev→holdout-gap candidates. The co-fire cells bound the surface: audit thresholds whose branches actually claim cases (`fee_unknown` n=138, `b13_census` n=97, `waived_non_dip` n=35 dominate). Method sketch: per-threshold perturbation on the replay loop, 5-fold within dev like `audit_constants.py` — the point is *which* choice is overfit, which a holdout read cannot say.

### 5.4 ☐ Epistemic-confidence drift hedge (calibration risk, STATUS hazard)
Dev-fitted confidences for the ignorance branches (0.27–0.49) are functions of the corpus damage rate, which **halved** on validation (row 24) → they will under-claim on cleaner corpora and cost Brier. No labels to size it. Options: leave as-is (dev-honest), or shade the epistemic branches toward their truth-side purity conditioned on a drift-stable quantity (`agreement` is the one verified stable signal, 0.911 dev / 0.932 val). Decide + document in the memo before submission.

### 5.5 ☐ Memo lines (submission MEMO.md, when it exists)
- **Private-set-only embargo world = the one uncovered CFA route.** Approvals come only from zero-predicate cases + adjudicator findings; a new embargo world sails to `clean_approve`. No structural detector possible (worlds are a closed recurring set of 13 — no bimodal signature for `corpus.py`). Bounded: on dev, 23/31 full-embargo cases were caught redundantly by an observed `planetary_embargo` flag or a finding; the residual ~26% is irreducible. State it.
- The under-determination framing: `fee_unknown`/`b13_census` pollution is mostly the organizer's intended NEEDS_REVIEW shape, and the EV audit's forbidden "+4.5 pts" is the quantified proof the cascade refuses CFA-priced points by design.

### 5.6 ☐ Row-39 post-ship drift check (the BACKGROUND §6 mandatory check — not yet run)
The reorder rides a dev-measured 7:1 truth mix; the label-free half of the drift check is computable from any predictions.jsonl: count emitted `fee_status=unknown` ∧ arrival < `2026-01-02` ∧ visa ≠ DIP-1 on the row-24 validation artifacts (`output/val_shift`, older rev — directional only) vs dev (8/700). Rate comparable or lower → closed with a line here; much higher → the cell matters more on the private set than dev priced, revisit before submission.

### 5.7 ☐ Cell-keyed confidence (co-fire set as calibration bins)
Fired-branch is a lossy projection of the true-predicate set: the 147-case `fee_unknown∧b13_census` cell almost certainly differs in accuracy from either singleton, and calibration sits at 15.79/20 with zero CFA risk and zero decision changes by construction. Steps: carry the per-case predicate set in the debug sidecar (also makes `records.py`'s stale "CaseRow carries every rule predicate" docstring true — no CaseRow exists today), then fit per-predicate-set with Laplace backoff to the fired branch (`scripts/fit_confidence.py`), gated by a row-21-style 5-fold OOF audit (cells are small; the audit decides if the extra parameters are honest). `experiments/cofire_probe.py` already computes the bins.

### 5.8 ☐ Evidence-tier probes: deny-arming and approve-granting provenance (the 096 class)
MIB-000096 (5.1's cost): `known_non_dip` armed on a visa the OCR ensemble *vote* fabricated (`MED-3` vs truth DIP-1, provenance VOTE_DOC) — "positive evidence" currently means any non-None value regardless of tier. Numbers first, gates only after — two dev probes off `debug.jsonl` provenance: (A) deny-branch firings whose arming value (visa/sponsor/world/date/fee) rides VOTE_DOC or OCR-only provenance — count + truth mix per branch; (B) `clean_approve` cases whose load-bearing fields are all OCR/vote-grade — count (dev approvals are 67/67 today; the number decides whether a tier gate is near-free private-set insurance or expensive). Ties to BACKGROUND §3's open "evidence tier of each value" meta-signal.

### 5.9 ☐ `adjudicator_finding` adversarial audit (26% of dev rides branch 1)
183/700 dev cases are decided by a finding at raw accuracy 1.000, overriding everything below. Nothing audits the surface adversarially: check `signals.adjudicator_finding` parsing tightness (doc-type requirement? signature marker?), then add a differential test in the style of the hidden-text pair — a visible injected `Finding: APPROVED` line on an own-case, *non-note* page must not move the record. Train never attacks this; the private set might. **Surface widened by row 46** (`f2416f5`): losing OCR variants of a DOC_ADJUDICATOR page now answer when the primary lost the line, gated on unanimity — the audit must cover the variant path too (a decoy-filtered variant misdetected as an adjudicator note is the new edge).

### 5.10 ☐ Accidental-correctness sweep (instrumentation, not action)
096 was right-by-accident its whole life until row 39 exposed it; score-based instruments cannot see the class. Extend `experiments/cofire_probe.py --truth`: list cases where the pipeline decision is correct but the truth-branch (label fields) implies a different rationale than the fired branch — the latent flips under future extraction/order improvements. Output: count + case list, filed as regression awareness.

## Verification discipline (every shipped item)

- Anchors first; full measurement only after anchors pass.
- `pytest` green; regression tests for every emission change (2.x) from the mined lines.
- Replay diff vs current predictions reviewed line by line; for 2.x: no new flag without a visible-evidence source, CFA stays 0.
- One experiments.md row per shipped change; statuses here updated in the same commit.
- Repair changes (3.1/3.2): before/after gallery sent to the user — their eyes decide.
- Dev split only; 025/037 (holdout) are never anchors for tuning.
