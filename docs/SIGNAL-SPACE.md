# SIGNAL-SPACE.md — what evidence the packet carries, what we use, and what we throw away

**Status:** investigation (no runtime changes). Measured deltas are directional — taken on **small
stratified dev samples** on purpose, so a number here means "worth/not-worth a full measured
shipping pass," not a point estimate to tune against. Full-batch tuning on 700 dev is itself an
overfitting risk; this doc is a map, not a leaderboard.

**Baseline it is measured against:** dev 119.10 (Class 62.41/80, Extr 41.36/50, Calib 15.32/20 —
honest OOF 15.09 — CFA 0); holdout 113.46 at v1 (one read, not touched here).

---

## 1. Why this doc exists

We had chased the *specific* extraction levers to their floor — crop (+0–2 fields, 0 adjudication),
deshred (OCR-neutral), voting (`best()` already ~45/47), fee_status (the "gap" is injection-only,
so `unknown` is correct). This doc zooms out one level: to the **signal space** — the *kinds* of
evidence (modalities) a packet carries, orthogonal to any one lever. For each modality we ask three
things: **do we use it? can it move one of the three scored goals (adjudication 80 / extraction 50 /
calibration 20)? does it leak the label?**

## 2. The discipline: two kinds of signal, treated differently

The challenge's PDFs are generated. Anything that measures **how damaged the generator made a
packet** correlates with the label the generator assigned — and that correlation is a property of
the generator, not of intergalactic immigration law, so it **does not transfer** to a private test
set. This is not a hypothesis: it is the measured cause of death of the `b13_census` prize
(`n_scan_pages` leaked the label — 48.6% APPROVED with no scan pages vs 25.1% — and the clean-packet
rate **halved** 14.9%→7.3% between train and the 5,000-case validation set, `experiments.md` row 24).

So every candidate signal is one of:

- **Evidence-trust** — *how much to trust a read value*: OCR recognition confidence, cross-variant
  agreement, cross-document consistency, injection presence. Plausibly transferable — **but must be
  proven so** by the drift check below.
- **Damage-quantity** — *how mangled the page is*: skew angle, shred-band count, scan-ness,
  `n_scan_pages`, `worst_doc_rank`. **Measured-dead. Off-limits** as a decision input.

**The drift check (mandatory, mechanical).** For any signal we'd trust: compare its distribution on
dev(train) vs a sample of the unlabeled `data/validation` PDFs. If the signal's correctness
correlation rides on a quantity that shifts between the two, reject it — no matter how good its dev
OOF looked. This is "no undeserved priors," made into a gate.

## 3. Signal-modality taxonomy

| # | Modality | Measures | Used today? | Can move | Leak risk |
|---|---|---|---|---|---|
| 1 | Text content | OCR'd characters → field values | **Yes** (primary) | Extr, Adj | low |
| 2 | Recognition confidence | tesseract per-word conf (`image_to_data`) | **No** | Extr, Calib | low (trust) |
| 3 | Ensemble agreement | do variants (src×geom×psm) agree on a value | **No** (collapsed at `runner.py:64`) | Extr, Calib | low (trust) |
| 4 | Cross-doc consistency | do packet docs agree on a field | **Partial** (2 checks only) | **Adj** | low (trust) |
| 5 | Provenance / precedence | which evidence tier supplied a value | Partial (tier only) | Extr, Calib | low |
| 6 | Injection presence | hidden / off-crop / decoy answer-key present | **No** (dropped) | Adj, Calib | low (trust) |
| 7 | Spatial / layout | where text sits (top-left), form vs letter | Weak | Extr | medium |
| 8 | Geometric damage | skew, shred count, scan-ness | Yes (in features, unused by rules) | — | **HIGH — leaks** |

Modalities 2/3/4/6 are the evidence-trust axes we had never mined. Measured outcome (§8): of these,
only **#3 ensemble agreement** cleared both the correctness *and* drift gates — and only for a small,
safe extraction merge. Modality **#8 is the trap, confirmed again** by the drift check
(`readable_variants` +27% dev→val, clean-rate 0.159→0.056).

---

## 4. Layer A (bottom) — OCR / page-recovery floor

*Are we strong at recovering bad pages before we build anything on top?*

**Established (prior sessions):** crop variant +0–2 fields / 0 adjudication (three agreeing
measurements); deshred OCR-neutral; naive voting negative, per-field merge ceiling ~+2; fee "gap"
dominated by cases where the value is present **only** in the prompt-injection (so `unknown` is the
correct trusted answer). Bottleneck is tesseract PSM-11 whole-page segmentation, not geometry.

**MEASURED THIS ROUND (n=38 dev scan, `scripts/ocr_bench.py`):**

- **A1 — untried recovery modalities: all DEAD over the real production ensemble.** The key
  methodological catch: additive gains must be measured against `prod_union_p11` (production's
  *full* src+turns+skew+deshred union), not the single raw pass.
  - Per-word-confidence-filtered text (drop conf<40/60): **+0** — filtering only removes real tokens.
  - Char-whitelist passes (SPN/MIB/date, psm 7 & 11): **+0** — psm11 whitelist only re-reads values
    the raw pass already had; psm7 single-line yields nothing.
  - Binarization sweep (thresh 150–210): **+9 over the single raw pass but +0 over `prod_union`** —
    the fields bin210 "recovers" (MIB-000070 world, MIB-000019 date, MIB-000013 visa, MIB-000049
    purpose) are *already* banked by production's turn/skew/deshred variants. A textbook proxy
    artifact — the same trap the crop headline was.
  - **Conclusion: the OCR recovery floor is solid.** Production's existing ensemble already recovers
    what these untried modalities offer; residual misses are genuinely unrecoverable / injection-only
    (consistent with the fee finding). No new *recovery* lever here.
- **A2 — per-word confidence is a LIVE trust signal (modality #2).** On-truth tokens sit **+15.9**
  conf above the page mean (+17.4 vs off-truth tokens): arrival_date **+16.3**, case_id **+11.8**,
  sponsor_id **+5.7**. It cleanly discriminates correct from incorrect values. This is **not** a
  page-recovery lever — it's a **calibration / trust input** that feeds C2, and it's cheap
  (`tesseract … tsv`, one extra parse mode on OCR we already run).

## 5. Layer B (middle) — preserve-the-ensemble signals (measured offline, no seam change)

`render.reads_for` yields the full ensemble offline (the `reads_probe.py` trick), so we can value
signal-preservation **without** touching `runner.py:64`.

**MEASURED THIS ROUND (n=39 dev scan, 312 field-instances; `signal_probe.py` — a scratch probe
that was never committed, so the B1/B2 numbers below are not reproducible from this checkout):**

**B1 — which ensemble signals actually track correctness** (target = did `best()`'s winning scan
reading recover the field vs truth):

| signal | mean \| correct | mean \| wrong | verdict |
|---|---|---|---|
| **agreement** (fraction of readings on the modal valid value) | 0.955 | 0.755 | **strong +** |
| **survivor_only** (value present in *only* the winning reading) | 6.6% | 47.1% | **strong −** (lone survivor ⇒ likely wrong) |
| margin (best.quality − runner-up) | 0.421 | 0.277 | weak + |
| readable_variants (#readings quality>0) | 9.00 | 8.37 | very weak + |
| snap_ratio (vocab.snap difflib ratio) | 0.983 | 1.000 | degenerate |

Only `agreement` (positive) and `survivor_only` (negative) separate correctness; the rest are weak
or degenerate.

**B2 — drift check (the crux: dev vs a 40-case `data/validation` sample):**

| signal | dev | validation | verdict |
|---|---|---|---|
| clean-packet rate | 0.159 | 0.056 | **SHIFT** — re-confirms the damage-quantity leak |
| **agreement** | 0.911 (Q1/med/Q3 = 1/1/1) | 0.932 (1/1/1) | **STABLE → transfer-safe** |
| readable_variants | mean 8.62 | mean 10.95 (**+27%**) | **drifts up → damage-coupled, REJECT** (it's the `n_scan_pages` family) |
| margin | med 0, frac<2 = 0.92 | med 0, frac<2 = 0.98 | stable but near-degenerate |

**This is the honest on-repo test of the external plan's "quality signals won't drift" premise, and
it splits cleanly: `agreement` passes (tracks correctness *and* transfer-stable); the quantity-family
`readable_variants` fails.** `agreement` is the one signal that clears both gates. `survivor_only` is
a strong negative trust flag but was not drift-checked (same ensemble origin as agreement).

## 6. Layer C (top) — signals → the three goals

**C1 — Extraction (defer-selection merge). SAFE, but small realizable gain.** Merging per field on
highest `agreement` (tie → quality) vs `best()`'s single winning line-list, at the OCR-ensemble
level (n=39 dev scan):

| field | best() | agreement-merge | Δ | wrong-value regressions |
|---|---|---|---|---|
| fee_status | 4 | 9 | **+5** | 0 |
| species_code | 21 | 24 | +3 | 0 |
| home_world / declared_purpose / applicant_name | — | — | +2 each | 0 |
| visa_class / sponsor_id / arrival_date | — | — | +1 each | **0** |
| risk_flags (observed_flags proxy) | 1 | 3 | +2 | 0 |

Agreement-merge **strictly dominates** single-best — monotone +1…+5/field with **0 wrong-value
regressions, including all three −4-sensitive fields** (`risk_flags`, `sponsor_id`, `visa_class`).
The property that matters is *safety*. **But the shipped pipeline already exceeds the merge on every
field** (e.g. species 36 vs merge 24 vs best 21) because it merges the clean **text layer** — so the
merge's gains are an ensemble-internal ceiling mostly already banked. **Realizable end-to-end gain
≈ +2**, matching the prior crop/voting ceiling. Worth banking someday for the safety property, not
for points.

**C2 — Calibration (agreement-conditioned): NO-GO** (n=99 mixed dev). Aggregate per-case agreement
separates correct from incorrect *adjudications* by only **+0.008** (0.964 vs 0.956) — because
per-case agreement is ~0.96 for almost every case (clean text-layer fields → 1.0, no case-level
variance). Even an **in-sample (optimistic)** agreement calibrator scores **worse** than the
per-branch table: **Brier 0.187 vs 0.117** (≈ **−2.8 / 20** calib). The field-read trust that works
in B1 does **not** propagate to adjudication calibration. This is the direct on-data test of the
external plan's **"+0.17" premise — it does not materialize**; it corroborates the repo's prior
counter-evidence (per-case calibrator −0.33; per-branch table already ~optimal). Per-word confidence
(A2) is a real *token*-level trust signal but faces the same case-level variance wall. **Do not
build the calibration model.**

**C3 — Adjudication (cross-doc disagreement + injection presence).** Highest weight (80), most
genuinely-discarded modalities. **MEASURED (n=60 dev, stratified; `scripts/crossdoc_probe.py`):**

- *Cross-doc disagreement → adjudication: **DEAD** as a decision input.* Overall lift P(NR|disagree)
  0.33 vs base 0.28 = **+0.05 (noise)**. Blanket "demote any disagreement → NEEDS_REVIEW" would
  change 11 cases, **10 of which we currently get right** (8→2 pts each) and **8 are true DENIED we
  correctly deny** (masking correctly-deniable cases), rescuing **0** wrong cases. CFA created = 0
  (demotion can't approve a denied case — structurally safe) but strongly net-negative.
- *Two real sub-threads worth noting (not adjudication demotion):* (1) candidate-level **name**
  disagreement recalls all 3 truth `identity_conflict` cases (3/3) vs the existing
  `signals.identity_conflict` check's 1/3 — but at 15% precision (most name-conflicts are OCR
  garbage). A possible **recall tweak to that one existing check**, not a new decision input.
  (2) **arrival_date** conflicts (MIB-000146 `2026-03-26` vs bogus `2028-03-28`; MIB-000514) are
  cases we get *wrong* by picking the implausible 2028 date → this is an **extraction /
  value-selection** lever (prefer the plausible date), which belongs in C1, not here.
- *Injection presence → adjudication: **DEAD.*** Present in 15/60; hidden-line and answer-key are
  the identical set (one `"SYSTEM: ignore visible evidence…"` template). P(we-wrong | injection)
  0.27 vs base 0.25 = **+0.02** — the quarantine holds; injected cases are misadjudicated at the
  base rate, not followed. A mild DENIED tilt (+0.10, n=15) most plausibly reflects
  generator-coupling (damage-quantity per §2, won't transfer). **Presence-as-signal only was
  honored — no value sourced from hidden text** (pinned by `test_hidden_text_cannot_change_the_output`).

## 7. Layer D — early-exit inventory (preserve unless ROI strong)

Since the pipeline now runs at 0.54 s/PDF against a 6 s budget (~11× headroom), **no early-exit
here is still justified by compute scarcity.** The ones that survive this audit survive on *other*
grounds (a measured quality-filter effect, correctness, or determinism), not on cost. Audit only —
no changes this round.

| Early-exit | Site | Current behavior | Verdict | Measurement that would decide it |
|---|---|---|---|---|
| Ensemble collapse | `runner.py:64` `best_lines` | keeps winner's `lines`, drops all variants + `variant`/`quality`/`cost_ms` | **Keep until a signal is proven preservable** — widening is behavior-preserving (replay must print IDENTICAL), low-risk *when justified* by Layer B/C | any B1/C signal that clears dev-up + CFA-0 + drift ⇒ widen |
| `best()` tie-break | `render.py:186` | equal `evidence_score` → earliest read wins (embedded before re-render, cheapest-first) | **Keep** — low stakes | count ties that change the *parsed* value; matters only if frequent AND field-changing (B1 margin makes ties visible) |
| snap→None→field-delete | `vocab.py:47-110`, `packet._repair_ocr_kv` | value far from closed vocab → deleted | **Keep — measured-dead to change twice** (passthrough cost 0.08→0.04, never a gain; value universe is closed/saturated) | snap distance measured (B1) — **degenerate** (no discrimination), so it adds nothing as a trust signal either |
| decoy-page drop | `packet.py:87` | page whose case_ids exclude the active id → dropped (multi-applicant trap) | **Keep** — correct by design | count pages dropped/case; flag any dropped page that carried the *active* applicant's fields (false drop) |
| flag legend guard | `signals.py:39` | `len(found) > 3` on one line → empty (reads as a legend) | **MEASURE — the one unmeasured guard with adjudication stakes.** Wrongly keeping a legend's tokens can add a disqualifying flag (deny a should-approve); wrongly dropping a real >3-flag line can miss a denial | over truth `risk_flags`: how many real cases carry >3 genuine flags on one line vs how many legends carry ≤3 flag-like tokens |
| per-case OCR budget | `runner.py:33` 120 s | wall-clock guard; drops to text layer for remaining pages | **Keep as safety bound** (not a lever) — load-dependent, so firing ⇒ non-determinism | confirm it never fires under Docker parity (max ~8.33 s/PDF today); the shelved dual-PSM run *did* trip it once |
| tesseract timeout | `render.py:70` 20 s | silent `[]` on expiry — **indistinguishable from a blank page** | **Keep, but note the observability gap** — a timed-out read enters the ensemble as an empty "reading" and would corrupt an agreement/margin signal | count firings; if it fires on hard pages it silently degrades the very ensemble Layer B measures |

**Takeaway:** only the flag legend guard (`signals.py:39`) has both adjudication stakes *and* zero
prior measurement — it's the audit's one real to-do. The collapse at `runner.py:64` is the gate the
rest of this doc informs: keep it closed unless a probe earns the widening.

---

## 8. Verdicts

| Modality / lever | Measured delta (small dev sample) | Transfer verdict | Ship / shelve / more |
|---|---|---|---|
| **Ensemble agreement → defer-selection merge** (C1) | +1…+5/field at ensemble level, **0 regressions**; realizable end-to-end **~+2** (text layer already banks most) | **transfer-safe** (B2 stable) | **Shelve as the single future ship-candidate** — safe but small; only worth a runtime change if paired with the seam-widening |
| Per-word confidence (A2, modality #2) | token-level +15.9 conf separation; **no** case-level calibration lift | untested (token) / no case-variance | **Shelve** — real signal, wrong altitude for our goals |
| Ensemble agreement → calibration (C2) | +0.008 case separation; in-sample Brier 0.187 vs table 0.117 | n/a | **Reject** — external "+0.17" does not materialize |
| Cross-doc disagreement → adjudication (C3) | lift +0.05; blanket demote masks 8 correct denials, rescues 0 | generator-coupled tilt | **Reject** as decision input |
| Injection presence → adjudication (C3) | P(wrong\|inj) +0.02 | damage-coupled | **Reject** — quarantine already holds |
| Untried OCR recovery (A1: conf-filter / whitelist / binarize) | **+0** over production ensemble | n/a | **Reject** — floor is solid |
| `readable_variants`, `margin`, `snap_ratio` (B1/B2) | weak / degenerate; `readable_variants` **drifts +27%** | fails drift | **Reject** — damage-coupled or no discrimination |
| **Non-goal threads worth a future look** | — | — | arrival_date plausibility fix (MIB-000146/514, extraction); `identity_conflict` **recall** tweak (name-disagree 3/3 vs 1/3); measure the **flag legend guard** (§7) |

**Overall:** the signal space is largely mined out and the discipline held. The one signal clearing
both the correctness and drift gates is **ensemble agreement**, and its only net-positive use is a
**safe, ~+2, defer-selection extraction merge**. **Nothing here justifies widening the `runner.py:64`
seam** for calibration or adjudication. Keep the seam closed; carry the four small threads forward as
separately-greenlit items, each still subject to dev-up + CFA-0 + drift.

## 9. Method notes

- Samples are **small and stratified** (seed 8090) by design — directional, not tuned.
- Frozen dev split (`data_splits.json`); drift check reads a `data/validation` sample; **holdout
  untouched** (investigation, not a milestone).
- Probes are read-only measurement scripts; `mib/` runtime is unchanged this round.
