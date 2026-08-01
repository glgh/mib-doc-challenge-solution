# TODO: the in-flight try-list

_Only OPEN (☐) and PARTIAL (◐) items live here in full. Shipped and dead items are collapsed to one-line pointers — their detail is in experiments.md (one row per scored change) and STATUS ("What we tried and kept" / "…and rejected"). Update in place; log new results as experiments.md rows._

**State: see [STATUS.md](STATUS.md) — "Where things are".** That file owns the current dev number, the CFA count, the holdout policy and the score lineage; restating them here is how the two drifted apart before. What belongs to this file: the extraction/selection frontier is CLOSED with evidence (the read-creation ceiling is airtight across every OCR-config axis — PSM 3/4/6 + native hi-DPI + field-crop recovered ~0 of 35 dev misses, `experiments/ceiling_delta*.py`, see the 1.8 caveat below; selection headroom is mostly already-worked; the merge tie-break "improvement" was falsified on diagnosis, 5.11). **Remaining real work is submission-hardening**: the current-rev validation drift re-check (5.6) and the memo (5.5). The open items below are mostly low-yield given the proven ceiling — pursue the repair items (Track 3) only for rank-1 evidence (findings / flags / stamps that flip a whole adjudication), not general field recovery.

**Working style (user directive): hard examples first.** Try an item against its named anchors before any full-corpus spend; a full dump regenerates only after the anchors prove it. Dev split only — 025/037 are HOLDOUT and never anchors for tuning.

**Anchors still live for open items:** 595 p3 (destroyed-border deshred, 3.2), 243 p2 (stamp fragmentation, 3.5). Controls that must NOT change: 096 p2 / 135 / 065 p3 (info genuinely absent), 747 p5 / 506 (damage markers, no token).

---

## Track 1 — OCR confidence & fusion — CLOSED

**1.1–1.6, 1.8, 1.10 ☑ shipped/closed** (rows 41–66, 84): TSV conf captured per line (schema 3/4); guarded-excess-mass (`records.conf_excess_mass`) as the primary-read selector; vote keys merge-don't-tie with `_line_conf` tie-break then first-seen; `evidence_score`/`GOOD_ENOUGH` deleted with the ladder; cross-read fusion + closed-vocab exceedance fill shipped; the risk_flags oracle bound closed (Track 2 saturated at ≤3 instances). The surviving hand-picked bar is `render.WEAK_BAR=6` (re-derivation debt → 6.11).

**1.7 ☒ A1 arbitration-metric library — CLOSED, no home.** The `m_guards`/`m_dom`/`domain_hits` library (`experiments/probe_arbitration.py`) was built for three candidate grains and all three are now dead: page-level selection is vote-absorbed (row 53), the `_line_conf` tie-break was falsified (5.11 — the domain metric doesn't separate truth from error there), and 1.8 fusion is closed. Do not re-aim it without a new grain that something consumes un-voted.

**1.8 caveat — "extraction saturated" is CONFIG-RELATIVE.** The full argument and its numbers live in the STATUS hazard list; in short, the saturation verdict was stress-tested (2026-07-28) by expanding the read set and held, but it is measured against the current OCR config (PSM-11, 202 DPI, whole-page gating). Re-run the oracles if the engine or the config changes.

### 1.9 ◐ Fee residue — (a) explicit-unknown tier SHIPPED (row 61); (b)/(c)/(d) OPEN
Shipped: `packet.fee_fallback` display-only ladder (row 54) + `_fee_unknown_stated` (row 61); fuzzy fee-KEY repair CLOSED (row-61 census — every legible non-unknown receipt value in the fee_unknown branch is a STRUCK value the strike-void correctly deleted; raw-line fuzzy recovery would resurrect them, so "receipt values never feed policy nor get fuzz-recovered" stands). Still open: **(b) USER DECISION** — feed the base-rate paid tier to policy for +0.22 dev (16 approvals / 9 review losses / 1 CFA — MIB-000332, silent-unpaid, irreducible from visible evidence); **(c)** audit other autocontrast consumers for the OCR-visible white-text injection channel `_INJECTION_RE` guards in the fee path (114/058 anchors); **(d) verbatim-outranks-reconstructed vote rank** (from 688, row 69): a snap-RECONSTRUCTED value should rank below a VERBATIM read of a competing value in `_variant_vote`'s group rank — 688's 4 reconstructed `unpaid` (`naid`-class decapitations) outvote 2 verbatim `paid`. Needs a corpus A/B counting current reconstructed-over-verbatim vote winners, truth-marked, before shipping.

---

## Track 2 — flag-line recovery — CLOSED

2.1/2.2/2.3 shipped (doc-gate deleted, unified weighted flag-value resolver, multi-flag value split; rows 35/36/84). Track is SATURATED at ≤3 instances (~0.3 raw pts, 1.10) — do not invest in matchers/bars.

---

## Track 3 — visual defects (repair)

**Deprioritized (2026-07-28):** the read-creation ceiling result (1.8 caveat) shows repair on damaged pages recovers ~0 new fields at the OCR ceiling. Pursue these only for the rank-1-evidence subset (adjudicator findings / flags / stamps that flip a whole adjudication), not general field recovery.

_NO-GO, do not reopen: **3.4** seam-window conf acceptance — the ensemble keeps every repair and crosses the seam, so there is no accept/reject grain; `_line_conf` is already finer, and page-level acceptance is vote-absorbed (row 85). **3.6** raster-strike detection — the eye-audit found zero scan-strike instances corpus-wide; row 34's vector detection covers every real strike (514/614/855), and the candidates were label errata / note-superseded (row 34)._

### 3.1 ☐ Color-aware masking (`imaging`, pre-`to_gray`)
Saturation mask → paper white: stamps (252 red, 990 purple — session observation, unrecorded), SAMPLE DENIAL watermark (96/595), yellow highlights (65/252) are colored; evidence ink is black. **Anchors are controls here:** on all four autopsy pages the overlays miss the field lines, so expect no change — the value, if any, is elsewhere in the corpus. Try on the hard set first; before/after gallery to the user (house rule: their eyes judge repair quality).

### 3.2 ◐ Content-anchored deshred — PARTIALLY SHIPPED as the `local` rung (row 51); the destroyed-border class remains
Row 51 shipped `imaging.realign_local`: at seams that cut through text, the cut glyph halves' cross-correlation overrides the border's implied shift (including "the text never moved" — 165's rescue). What it does NOT cover: pages whose border is itself shredded to dashes (the confetti class — 237/243/595: thin one-line bands, ~no text on cuts, 8-17% left-line presence; findings.md 2026-07-26). For those the fix is not repair but standing down (no-op class: zero cuts-text seams + healthy base read, both census-computable). **Anchor:** 595 p3 (gallery to user).

### 3.3 ☐ Footer registration anchor (exploratory, only if 1–2 stall)
**Premise unverified:** no doc records a `Packet MIB-XXXXXX / page N` footer at a known position — BACKGROUND §4 names the page *border* as the registration signal. First render a page sample and check the footer exists, survives crop/shred, and sits where claimed; only then design orientation + skew + case-id cross-check around it. Mostly a runtime win if real.

### 3.5 ☐ Stamp-fragment tolerance (S3, from the confetti finding)
243's `COPY` stamp and its `MIB` adjudication box are torn across many thin bands — multi-band line structures no row-realign reassembles, and stamps are evidence precedence rank 1. Check S3 stamp/adjudicator-finding detection for fragmentation tolerance; anchor 243 p2 (crops in the 2026-07-26 session gallery).

---

## Track 4 — dispositions

- **Optical rung** (`MIB_OCR_OPTICAL`): ON by default (row 48). **Condition-based flag emission**: dead (P ≤ 0.38; the generator's B-13-included-then-destroyed vs never-included distinction is invisible in the PDF — printed evidence only, organizer ruling §1).
- **Docker gate at grid cost: DONE (row 97, 2026-07-29)** — see STATUS Q3 for the numbers. No revert needed; the ladder enumerator stays deleted. **Live residue:** the gate must run once more on the real 5,000-case validation set before submission.

---

## Track 5 — cascade structure

**5.1 / 5.2a / 5.7 / 5.8 ☑ shipped/closed** (stale-over-fee reorder row 39; fee-pool diagnosis closed by negative censuses row 50; cell-keyed confidence rows 86–87; evidence-tier probes row 57 — the blanket vote-armed-deny→NR gate priced out, its 5-case worklist shrinking through read quality). Open:

### 5.2 ☐ Mode-B dead-page census assessment — REFRAMED by the 2026-07-26 gallery session
The gallery ran (19 of 95 dev b13_census cases have any page with all reads under GOOD_ENOUGH; user + model read sheet 1 identically) and overturned the premise twice: (a) **the "dead page" cut is a GOOD_ENOUGH artifact** — a pristine fee receipt has only 3 label lines and scores 3-4, so sparse-but-clean pages masquerade as destroyed (1.6's audit debt, now demonstrated); (b) **the B-13 absence is real, not an OCR failure** — 76/95 pool cases have every page reading fine and no slip anywhere. Visa-class census of the pool: DIP-1 25 A / 3 D / 10 NR, MED-3 13/6/3, XW-1 8/3/3, XW-2 15/1/5 — every class carries a DENIED minority, so **no no-B13 slice is safe to auto-approve** (blanket DIP-1 ≈ +62 raw but 3 CFAs — the forbidden-EV shape, 5.5's memo point). The convertible path: peel truth-DENIED cases off via missed-evidence extraction (flags/findings), which purifies the residual census; policy-side subdivision belongs to the policy.py stream. Remaining gallery-worthy set: the genuine geometric failures (112-class: shredded-border pages `realign_bands` cannot anchor — Track 3.2).

### 5.3 ☐ Threshold + branch-existence audit (question 8 residue)
Branch order is settled (5.1); the ~10 hand-tuned thresholds and which-branches-exist remain the dev→holdout-gap candidates. The co-fire cells bound the surface: audit thresholds whose branches actually claim cases (`fee_unknown` n=138, `b13_census` n=97, `waived_non_dip` n=35 dominate). Method sketch: per-threshold perturbation on the replay loop, 5-fold within dev like `audit_constants.py` — the point is *which* choice is overfit, which a holdout read cannot say. (The 2026-07-28 holdout read at 128.48 / gap +1.03 is a reassuring end-to-end generalization signal, but does not localize an overfit threshold.)

### 5.4 ☐ Epistemic-confidence drift hedge (calibration risk, STATUS hazard)
Dev-fitted confidences for the ignorance branches (0.27–0.49) are functions of the corpus damage rate, which **halved** on validation (row 24) → they will under-claim on cleaner corpora and cost Brier. No labels to size it. Options: leave as-is (dev-honest), or shade the epistemic branches toward their truth-side purity conditioned on a drift-stable quantity (`agreement` is the one verified stable signal, 0.911 dev / 0.932 val). Decide + document in the memo before submission.

### 5.5 ☐ Memo lines (submission MEMO.md, when it exists)
- **Private-set-only embargo world = the one uncovered CFA route.** Approvals come only from zero-predicate cases + adjudicator findings; a new embargo world sails to `clean_approve`. No structural detector possible (worlds are a closed recurring set of 13 — no bimodal signature for `corpus.py`). Bounded: on dev, 23/31 full-embargo cases were caught redundantly by an observed `planetary_embargo` flag or a finding; the residual ~26% is irreducible. State it.
- The under-determination framing: `fee_unknown`/`b13_census` pollution is mostly the organizer's intended NEEDS_REVIEW shape, and the EV audit's forbidden "+4.5 pts" is the quantified proof the cascade refuses CFA-priced points by design.
- The four-way ceiling evidence (2026-07-28): extraction reachability, selection headroom, tie-break diagnosis, and the clean holdout all agree the score is near a real, generalizing ceiling — the honest "why we cannot squeeze more without new evidence" argument.

### 5.6 ◐ Row-39 post-ship drift check — RUN on old-rev val, FLAG RAISED; re-check on current rev MANDATORY before submission
The check ran against `output/val_shift` (rev `4767919`, directional): the cell (`fee_status=unknown` ∧ arrival < `2026-01-02` ∧ visa ≠ DIP-1) is **376/5000 = 7.52% vs dev 8/700 = 1.14% — 6.6× denser**. Two mitigating unknowns keep it a flag rather than a rollback: the old rev predates the fee/date extraction gains (rows 40–52 shrink the cell), and the reorder's dev truth mix in the cell was 7:1 DENIED with positive EV. Required before submission: recompute on a current-rev validation run; if the rate stays ≥several× dev, re-examine the row-39 reorder's blast radius (it sends the whole cell to DENIED) against a hand-checked sample of cell members' rendered pages. Also re-check `STALE_CUTOFF`'s margin (37 d dev → 2 d val, STATUS Q9) on the current rev.

### 5.9 ☐ `adjudicator_finding` adversarial audit (26% of dev rides branch 1)
183/700 dev cases are decided by a finding at raw accuracy 1.000, overriding everything below. Nothing audits the surface adversarially: check `signals.adjudicator_finding` parsing tightness (doc-type requirement? signature marker?), then add a differential test in the style of the hidden-text pair — a visible injected `Finding: APPROVED` line on an own-case, *non-note* page must not move the record. Train never attacks this; the private set might. **Surface widened by row 46** (`f2416f5`): losing OCR variants of a DOC_ADJUDICATOR page now answer when the primary lost the line, gated on unanimity — the audit must cover the variant path too (a decoy-filtered variant misdetected as an adjudicator note is the new edge). Cheap insurance; a good pre-submission item.

### 5.10 ☐ Accidental-correctness sweep (instrumentation, not action)
096 was right-by-accident its whole life until row 39 exposed it; score-based instruments cannot see the class. Extend `experiments/cofire_probe.py --truth`: list cases where the pipeline decision is correct but the truth-branch (label fields) implies a different rationale than the fired branch — the latent flips under future extraction/order improvements. Output: count + case list, filed as regression awareness.

### 5.11 ◐ Merge-rule structure audit — DONE (row 66); tie-break improvement FALSIFIED (2026-07-28)
The audit (`experiments/audit_merge.py`) ablated every merge/validity rule: three mechanisms carry ~83% (fee ladder, text shield, variant vote), every decision-changing micro-rule is net-positive, none is CFA-load-bearing. The one proposed IMPROVE — swapping `_line_conf` for the A1 domain metric at the vote tie-break — was FALSIFIED on diagnosis (`experiments/diagnose_tiebreak.py`): 085 is a genuine conf tie where domain worth points the WRONG way, 609's truth is in 0 reads (a reachability/ceiling case), only 395 has a razor-thin edge — ineffective at best, case-fitted overfitting at worst, NOT shipped and not a live lever. **Open residue (user call):** keep-vs-delete on three zero-effect guards, all dev-safe to delete but each with a documented adversarial/hygiene story arguing keep — edge-punct vote-key pooling (row 45, debris fragmentation), page-balance rank key (row 58, decoy fan-out), fuzzy damage-marker rejection (row 58a, manual-review optics). Everything else in the harmed-case autopsy (`output/viz/audit_merge.jsonl`) is an information-limited class (name-poisoning 6.12, digit saturation, planted fee receipts).

---

## Track 6 — S2 grid

**6.1–6.5, 6.7 ☑ shipped** (the grid + `page_score` + schema 5, rows 58–60; turn-gate discipline row-recorded; honest 202 DPI; vote page-balance + short-junk name-shape guard row 64; damage-marker fuzzy rejection row 65; layout-pass PSM-3 tier row 67, truncation-triggered, +0.01 dev). Open:

_NO-GO, do not reopen: **6.10** seam-window acceptance (= 3.4, same mechanism). **6.12** ink-consent — refuted (row 68): the generator plants BOTH directions (poisoned digital forms with nowhere-in-ink fake names AND genuine foreign scanned docs with real ink), so a 1v1 name conflict is structurally ambiguous; only ≥2-doc corroboration separates them (shipped row 63); the 5-case residue (064/243/507/564/885) is information-limited._

### 6.6 ◐ Revoked-id churn — INSTRUMENT SHIPPED (`experiments/revoked_churn.py`), veto pending growth evidence
Live substrate: 106 revoked emissions, 103 text/raw-grounded, 3 churn-class (244/288/350) — all currently harmless (wrong sponsor, right adjudication). Re-run on every new substrate; build the veto the day a truth-APPROVED case enters the churn class. The cross-document conflict class is named (row 81 key-oracle work): MIB-000190/237/955 print a genuinely-real revoked id (`SPN-4040`/`SPN-0007`) on the applicant's OWN intake while the truth sponsor sits on another doc; all three are truth-DENIED (harmless today), but they are the concrete instances a page-attribution rule would need to resolve. The row-81 sponsor cell widening left these unchanged by construction (0 movement, verified) — value recovery only, attribution deferred here.

### 6.8 ☐ Orientation-hint consumers (the hints are calibrated, nothing gates on them yet)
`imaging.orientation_profile` sharpness is rule-stripped glyph projection — 12/14 on the eyeball-labeled registry (raw sweep scored 6/14: form rules INVERT the signal; the residue is the 90-vs-270 tie projections cannot see). Hints were removed from base-tier ordering (shuffling read order feeds first-seen tie-breaks — an uncontrolled degree of freedom); their consumers are future gated tiers: expansion priority, psm3 frame choice.

### 6.9 ☐ Color-mask module (Track 3.1 folded in) — deprioritized per Track 3
A source-transform slot in the grid (saturation mask → paper white for stamps/watermarks/highlights). RGB is not yet plumbed past `_sources`' gray conversion — that plumbing is the first step; galleries decide the module's value (anchors are controls per 3.1). Given the ceiling result, only worth it for the rank-1 stamp/finding subset.

### 6.11 ☐ WEAK_BAR derivation + the coverage metrics
`WEAK_BAR=6` is a continuity value; re-derive from the dev `page_score` distribution. Coverage instruments to track per substrate: DOC_OTHER pages (baseline ~30.7% of scanned pages DOC_OTHER; refine to DOC_OTHER ∧ no-fields-extracted, since 030 p2 extracts fine while staying untyped — of 600 dev OTHER scan pages ~175 carry a recoverable label-signature but only ~57 biometric/adjudicator are decision-relevant), arb-oracle reachable count, fee-silent pool, dead-page pool (5.2).

---

## Verification discipline (every shipped item)

- Anchors first; full measurement only after anchors pass.
- `pytest` green; regression tests for every emission change (2.x) from the mined lines.
- Replay diff vs current predictions reviewed line by line; for 2.x: no new flag without a visible-evidence source, CFA stays 0.
- One experiments.md row per shipped change; statuses here updated in the same commit.
- Repair changes (3.1/3.2): before/after gallery sent to the user — their eyes decide.
- Dev split only; 025/037 (holdout) are never anchors for tuning.
