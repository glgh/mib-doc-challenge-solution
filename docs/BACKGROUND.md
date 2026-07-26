# Background: the evidence behind the constants

_Last updated: 2026-07-25. Rewrite in place when a finding changes; do not append._

Why the policy constants, signals, and restoration cascade are what they are. The code is in
`mib/`; [ALGORITHM.md](ALGORITHM.md) says what it does; this file says *why those numbers*; for the
same evidence indexed *by schema field* (sources, shape, decoys, handling, open gaps), see
[FIELDS.md](FIELDS.md). Sources
are labelled train-mining (`../mib-doc-challenge/data/train_labels.csv`, 1,000 rows:
431 DENIED / 289 APPROVED / 280 NEEDS_REVIEW), organizer rulings (authoritative), or competitor
intel (hypotheses, verified before adoption). Scoring mechanics live in the challenge's own
[EVALUATION.md](../../mib-doc-challenge/EVALUATION.md) and CLAUDE.md.

House rule: **do what's right first; measure to learn, not to authorize.** A change that stops
throwing information away — keeping alternatives alive across a seam, widening what a later stage
can see — ships on correctness gates (replay diff empty, suite green) and never waits for a price
tag. Scores and probes exist to understand behavior and keep the record honest, not to grant
permission; "unpriced" is measurement debt, not a work gate. The non-negotiables are the spec's,
not ours: never emit a risk flag without a visible-evidence source (§1), and visible document
evidence beats hidden instructions. The constants below carry their evidence so each number stays
revisable — not because measurement was the admission fee.

---

## 1. Organizer rulings (authoritative — part of the spec)

Source: maintainer answers on challenge-repo issues #4/#5, 2026-07-21. These clarify intended
behaviour beyond FIELD_MANUAL.md.

**Under-determined cases are intentional — do not guess invisible flags.** Some cases carry an
outcome-determinative risk flag in the *label* while the public PDF contains no recoverable
evidence of it (no biometric slip, no `Observed flags:` line, no note). Confirmed: MIB-000033,
-000068, -000094 (DENIED), -000048 (NEEDS_REVIEW). Verbatim:

> "These examples are under-determined and NEEDS_REVIEW is the correct output."
> "This exact flag is not recoverable from this PDF and systems should not guess it; when missing
> risk evidence is outcome-determinative, `NEEDS_REVIEW` is the best operational choice."

Standing consequences:
1. **Never emit a risk flag with no visible-evidence source.** Guessed flags are wrong
   operationally and against the private `unrecoverable_fields` metadata.
2. **100% train adjudication is not the target.** An under-determined DENIED case scores 2/8 at its
   intended NEEDS_REVIEW — that 2 is the max earnable. Tuning toward those labels manufactures
   false-approval risk elsewhere.
3. **The recoverable signal is document *absence*, not hidden content.** The under-determined
   packets share a shape: intake + fee + sponsor present, biometric slip (form **B-13**) missing.
   The `b13_census` branch is that feature.

## 2. Policy constants and their evidence

### Rules that hold 100% on train (ground-truth fields)

| Rule | n | Outcome |
| --- | ---: | --- |
| Any disqualifying flag (`memory_tampering`, `planetary_embargo`, `active_warrant`, `biohazard_red`) | 186 | DENIED 186/186 |
| `visa_class = TRANSIT-7` | 53 | DENIED 53/53 |
| `fee_status = unpaid` | 50 | DENIED 50/50 |
| `fee_status = unknown` | 44 | NEEDS_REVIEW 44/44 |
| Stale arrival (>180 d before receipt), non-DIP | 36 | DENIED 36/36 |

No receipt date appears in the labels; the data-version date (~2026-07-07) makes staleness fit
perfectly, but the shipped cutoff is the **max-margin midpoint 2026-01-02** — middle of the empty
48-day band between the latest stale-denied arrival (2025-12-09) and the earliest fresh **non-DIP**
arrival (2026-01-26). The lone arrival inside that band, 2026-01-15, is a staleness-exempt DIP-1
APPROVED — which is why the *label-free* margin check (experiments row 24, all arrivals) reports the
narrower 37-day gap for train. Stale + DIP-1 splits 13 A / 3 NR — the "DIP-1 with a valid
diplomatic note" exception, whose visibility lives in the PDF.

### Revoked sponsors (published + inferred)

Non-DIP outcomes per sponsor, **zero approvals each**, same signature as the published list:

| Sponsor | Non-DIP | Source |
| --- | --- | --- |
| SPN-0007 / SPN-0139 / SPN-4040 | 13 D / 13 D / 15 D | FIELD_MANUAL |
| SPN-2718 / SPN-7331 / SPN-9090 | 13 D / 14 D / 11 D | inferred, and independently corroborated by competitor PR #3 |

Inference method: sponsors appearing ≥2× in otherwise-rule-clean DENIED cases, then checked for
zero non-DIP approvals — policy inference the manual explicitly invites, not per-case memorization.
**Revoked-under-DIP-1 is fine** (11/11 such cases APPROVED — sponsor not required for DIP-1). Snap
must never *fabricate* a revoked id by digit-translation (`mib/vocab.py` guards this).

**The structural evidence is stronger than the outcome table, and it needs no labels.** A sponsor id
is per-case data — each packet carries its own — so the occurrence spectrum over the 1,000-case train
corpus should be flat at 1. It is not; it is bimodal, with nothing in between:

| appears | 1× | 2× | … | 9× | 16× | 18× | 22× |
| --- | ---: | ---: | --- | ---: | ---: | ---: | ---: |
| distinct ids | 734 | 23 | — | 1 | 1 | 3 | 1 |

The six ids above the gap are **exactly** the six revoked sponsors, published and inferred alike. So
recurrence alone recovers the list without consulting a single label, which is why the inference is
sound rather than lucky: the generator gives every ordinary case a fresh sponsor and reuses only the
policy-level ones. Two independent confirmations followed. `scripts/audit_constants.py` re-mines the
same three ids from **every** 4/5 fold of dev (row 21, zero fitting bias), and `mib/corpus.py` detects
all three label-free after they are ablated, restoring 62.41/80 and CFA 0 from an ablated 60.61/1
(row 23).

The residual exposure is **coverage, not correctness**: the list cannot see a revoked sponsor that
exists only in the private set, and a missed one falls through to `clean_approve` — the −4 outcome.
That is what `mib/corpus.py` exists to cover.

### Embargo worlds

| World | Rule | Evidence |
| --- | --- | --- |
| TRAPPIST-1e | DENIED incl. DIP-1 | 32/32 embargo, 32/32 DENIED (8/8 DIP-1) — **pierces diplomatic immunity** |
| Eris Relay | DENIED incl. DIP-1 | 18/18 embargo, 18/18 DENIED (5/5 DIP-1) |
| Wolf-1061c | DENIED non-DIP only | non-DIP 51/51 DENIED (mostly *without* the flag — partial embargo); DIP-1 ~normal |

In the *labels*, full-embargo worlds usually co-occur with the `planetary_embargo` flag — but the
pipeline deliberately does **not** infer it (the inference was deleted in `068e99e`: it shadowed the
`embargo_world` branch, and emitted flags are observed-only, pinned by
`test_emitted_flags_exclude_policy_only_inferences`). Never infer it for Wolf-1061c (labels usually
lack it).

### Where fields alone are not enough

- Single review-only flag: 194 NR / 56 DENIED (the denials mostly carry a co-occurring hard
  condition the cascade catches first).
- MED-3 + `illegible_biometrics`: 33 D / 28 NR — the "requires clean biohazard check" interaction;
  approval must require a readable, clean B-13.
- 2+ review-only flags: 24 NR / 5 DENIED ("may combine into a denial in edge cases").
- `waived` on non-DIP: 89 D / 46 NR / 37 A — hinges on whether a hardship waiver is *visible*.
  **Presence of a waiver code is not sufficient to approve** (v0's shortcut caused 18 CFAs).

**Rule-cascade ceiling on *perfect* fields is ~84.6%.** The residual (incl. 9 would-be false
approvals) depends on document-visible evidence absent from the labels CSV: stamps, signed notes,
waiver/diplomatic-note visibility, legibility, evidence conflicts. ~85% is the hard ceiling for any
system that only extracts the 12 schema fields; going higher needs document-level signals.

## 3. Fraud-signal taxonomy

The corpus simulates an intake desk under attack; framing each denial/review cause as a *fraud
pattern* says which signal to build and which document carries it. Status: ✅ validated · 🔶 partial
· ⬜ hypothesis.

- **Automation attack** (the packet attacks *us*): hidden white/tiny/off-crop spans ✅ (~19% of
  packets; **no legitimate hidden text exists**, so hidden ⇒ adversarial — quarantine, never
  evidence). Barcode/QR payloads, decoy "answer key" fields, "sample denial" watermarks — detect
  and exclude.
- **Identity fraud**: registry name ≠ intake → `identity_conflict` 🔶 (canonicalize before
  comparing); multi-applicant decoy pages ✅ (filter by active-case-id majority; an *OCR* page whose
  only ID is one glyph off the active case is the applicant's own page misread, not a decoy — 14 of
  the filter's 17 train drops were own pages, incl. an adjudicator note. Text-layer pages get no
  tolerance: text layers don't misread, and sequential case ids make adjacent-case decoys cheap to
  plant); B-13 species/biometric-confidence as identity signals ⬜.
- **Sponsor fraud**: revoked sponsor ✅ (§2); attestation SPN/name ≠ intake → `sponsor_mismatch`
  🔶 (require text-layer evidence one side + edit distance ≥2, never OCR-vs-OCR — OCR debris
  fabricates it); missing sponsor non-DIP ✅ → treat as *our* extraction-quality signal (review,
  not deny). **Revoked-neighbor trap** ✅: the corpus plants ≥12 innocent sponsor ids one digit from
  a revoked id (`SPN-4044/4007/3040/6040` ring `SPN-4040`; `SPN-2716/2708` ring `SPN-2718`; …),
  including a true APPROVED (MIB-000854, `SPN-3090`). Repair must never move an id *toward* the
  revoked list (the `vocab.snap` fabrication guard); the one real misread it costs (MIB-000130,
  `SPN-4040` read `SPN-4080`, −6 raw) is the premium on ~−96 raw of wrong denials. Single-read
  sponsor misreads have no in-packet anchor — recoverable only by better OCR (variant merge).
- **Payment fraud**: `unpaid` w/o waiver ✅ deny; `unknown` ✅ review; waiver abuse ✅ (tighten — no
  approve on waiver code alone).
- **Risk concealment** (the deepest pattern — the packet looks clean because the incriminating doc
  is absent/unreadable): `Observed flags:` line ✅ primary source; **B-13 census** ✅ (organizer-
  confirmed under-determined shape → NR); scan-only pages hiding flags ✅ → OCR; `Registry status`
  ≠ clear 🔶; crossed-out denial → `rescinded_denial` ⬜.
- **Embargo evasion** ✅ (§2). **Class abuse**: TRANSIT-7 ✅; MED-3 without clean biohazard ✅
  (24 of v0's 52 CFAs were MED-3 approvals without a verified clean check). **Timing games** ✅ (§2).

Meta-signals (about our own evidence quality, not the applicant): extraction completeness per
packet, evidence tier of each value (approvals should require intake-grade evidence or better),
cross-document conflict count. All partially used; formalizing them is open work.

**`illegible_biometrics` has two truth modes** (label mining 2026-07-25, dev split, 162 truth
cases). Mode A — *printed* (94/162): the B-13 itself states `Observed flags: illegible_biometrics`
as a field value (the in-universe scanner failed, the slip is fine). Whenever that token is legible
in **any** read — any variant, any page, regardless of detected doc type — truth carries the flag
94/94: **P=1.00, zero false positives even with the doc-type gate removed**. Current emission
catches 91 (0 FPs); the 3 misses (MIB-000656/771/979) print the token on pages whose OCR-mangled
header defeats `detect_doc_type`, so the FLAG_DOC_TYPES gate skips them. Mode B — *condition-
derived* (68/162): nothing prints the token; the truth flag marks that the packet's B-13 was
generated illegible or omitted. Looking the other way kills condition-based emission: P(truth
flags | B-13 seen but risk line unreadable) = 0.38 (n=29); P(| no B-13 found + ≥1 dead scan page)
= 0.30 (n=113); P(| no B-13, scans exist, none dead) = 0.25 (n=118); P(| all-text packet, no
B-13) = 0.05 (n=111). The generator knows whether the spec included a B-13 it then destroyed vs
never included one while destroying some other page — from the PDF those are the same picture, so
the flag is emittable only from printed evidence (which is also what organizer ruling §1 demands).
`biometric_confidence` does not discriminate (65–77%, flagged and unflagged interleaved).
**Mode B subdivides (page renders, user-verified 2026-07-25).** Of the 11 mode-B cases where a
B-13 *was* detected but its risk line read as debris: ≥5 (252, 565, 577, 595, 990) visibly print
`Observed flags: illegible_biometrics` — human-legible on the page, OCR-mangled past
`match_flag_token`'s reach (`Bagitie bematics`, `Mogible_ biometrics`, `Bagte——I*`) — these are
mode A with worse OCR, not condition-derived at all; 2 (506, 747) print a damage marker instead
(`[RISK PANEL MISSING]`, `[RISK PANEL = NG]`) — OCR read them perfectly, there is simply no flag
token on the paper; the rest are truncated/debris. So the fix family is "flag line present,
matcher/gate too strict": the 3 doc-type-gate misses plus label-anchored value recovery (the
`Observed flags:` *label* survives; the value needs a looser, guarded snap). The 57 mode-B cases
with no detected B-13 (dead pages) remain unassessed page-by-page.

**OCR digit confusions are systematic, not uniform**: the scanner's dominant year error is 6→8
(23 of 25 out-of-range arrival years read `2028` for a true `2026`; the same confusion appears in
months, `06`→`08`). Repair is safe only where a prior makes one reading impossible — years ≥2028
are future-impossible under ≤180-day visas, so they snap to 2026; past years (2020, 2024) are
always plausible stale dates and must be taken as read, because un-staling a genuine old date
trades a 0-cost wrong denial for a −4 false approval.

**Red strikethrough voids a printed value** (label mining 2026-07-25, `experiments.md` row 34). The
corpus crosses out a field value with a thin red line in the PDF *vector* layer; the text layer still
reads the value, so a text-only pipeline emits it. It is a deterministic negation mark: across 452
text-layer fee receipts a struck value ⟺ printed value ≠ truth (0 counterexamples); visa 0/29,
sponsor 0/47 struck values equal truth. 138 train cases carry one (129 intake, 30 fee). **The pairing
is the tell.** A struck `visa_class`/`sponsor_id`/`applicant_name` is **100% paired** (29/29, 47/47,
27/27) with a `Manual correction: <field> is <value>` that already supplies the truth at rank-0 — the
strike is a visual echo of the correction, so voiding those is redundant-but-safe insurance. A struck
**`fee_status` is only 18% paired** (5/28): the fee is crossed out but *not* corrected, so the true
value is unrecoverable → the honest answer is `unknown` → NEEDS_REVIEW. That asymmetry is why the
void (S1 `struck` → `packet._void_struck`) moves only fee on dev (MIB-000514/000614, false denials
on a struck `unpaid`). Strike-*presence* is a weak adjudication signal alone (struck 48% DENIED vs
43% base); a struck-but-*uncorrected* value is 12 D / 10 NR / **1 A** — almost never a clean approve
— but that is already consumed by the void→`fee_unknown` path, and wiring strike-presence in directly
would risk the `b13_census` generation-artifact trap for marginal gain. Integrity check for the
private set: on train a struck identity field *always* has its correction, so a struck one *without*
would be anomalous (an OCR-missed correction). Scope: text-layer vector strikes only; scanned
red-*pixel* strikes (OCR is grayscale) are unmapped.

## 4. Scan damage is geometric, not optical

Survey (2026-07-22, every 8th train case: 125 cases, 110 with scans, 259 scan pages). Two earlier
conclusions — "OCR is bimodal / synthetically destroyed" (experiments row 8) and "PSM-mode problem"
— both aimed at the wrong axis. The unreadable pages are **geometrically transformed**; undoing the
transform recovers the text at the 200-DPI PSM-11 pass already run. Resolution never could help.

Three co-occurring transforms:
1. **Quarter turns** — whole pages at 90°/270°. Of 259 pages, best rotation was 0° on 233, 90° on
   17, 270° on 9. **180° never occurs** — not in the generator's repertoire.
2. **Skew** — several degrees of tilt, enough to defeat Tesseract on human-legible text.
3. **Band displacement ("shredder")** — page cut into horizontal bands, each slid sideways; bands
   shifted far enough push content off the left page edge, where it is genuinely lost.

What it costs: 36% of scan pages read zero labels upright; 10% are rescued by rotation alone;
**15.5% of cases carry a turned page contributing nothing**, and those extract at 52% vs 75%
corpus-wide (the intake form — 6 of 9 scored fields — is as likely to be turned as anything).

**Orientation detection.** Tesseract OSD is unusable (too sparse — "Too few characters"). Ink
run-length **anisotropy** gets 86% (91% at margin ≥0.05): for horizontal text the *vertical* runs
are longer (stems run the full x-height; horizontal runs chop at every gap — easy to get the sign
backwards). Used only to *order* candidates, never to decide (6× budget headroom: a wrong ordering
costs one pass, a wrong decision discards a page's text). Best untapped signal is the printed
**page border** — constant width, so its edges give skew precisely and its per-row left edge *is*
the band offset that `imaging.realign_bands()` keys off; present even on near-textless pages.

**Results** (dev 700, cumulative restoration levels as they were then selectable, CFA 0 at every level):

| Level | Total | Extraction | Classification | Calibration |
| --- | ---: | ---: | ---: | ---: |
| `off` | 114.50 | 38.53 | 60.94 | 15.03 |
| `skew` (shipped) | 115.20 | 38.76 | 61.27 | 15.16 |
| `turn` | 116.59 | 39.58 | 61.79 | 15.22 |
| `bands` | **116.88** | 39.62 | 61.97 | 15.30 |

`bands` is +2.38 over `off`, CFA 0, Brier 0.1243 → 0.1176. Per-case: MIB-000030 extraction
8/45 → 36/45, MIB-000131 4/45 → 27/45.

**Runtime is resolved for `skew`, still open for the deeper rungs.** The contract-limits gate
(`scripts/run_docker_submission.py`, experiments.md row 19, 2026-07-23) measured `skew` at
**0.54 s/PDF with ~11× headroom** — the feared heavy tail (p99 57.5 s, max 107 s from a contended
10-core laptop) was contention, not the pipeline (container max 8.33 s). A prior conclusion that
"wall-clock falls as restoration deepens" was **retracted** (laptop seconds under load, not
comparable). `turn`/`bands` and dual-PSM remain untimed under the gate; deeper levels add OCR
passes, so assume cost *rises* until measured there.

**Known inefficiency.** The flow is repair-after-failure (OCR → fail → repair → re-OCR); each
repaired page burns a doomed pass. Skew and axis are both measurable in ~5 ms of numpy, so the
flow should be detect-then-repair: measure geometry, fix once, OCR once. Not yet implemented. The
200-DPI re-render is now largely wasted (the embedded raster is already 1224×1584) and should be
gated on "restoration didn't help either."

## 5. Competitor benchmarks (context, not evidence)

Self-reported train scores from public submission PRs (verify claims before adopting):

| Who | Total | Extr | Class | Calib | Approach signature |
| --- | ---: | ---: | ---: | ---: | --- |
| thegoleffect | **132.44** | 45.80 | 69.79 | 16.84 | Tesseract dual-pass PSM 3+11 @150 DPI; vocab repair; 300-DPI retry only for low-conf packets with no recovered flag |
| dw820 | 129.21 | 43.42 | 68.42 | 17.37 | RapidOCR (PP-OCR/ONNX); trusts only rendered pixels; **HistGradientBoosting cost-sensitive adjudicator, ships 5 CFAs** |
| mikeg-cerebras | 126.70 | 43.60 | 66.90 | 16.20 | Pure rules; OCR "escalation ladder" + CNN validation; 1.4 s/doc |
| arvindcr4 | 120.60 | 40.50 | 64.50 | 15.70 | OCR variant ensemble (deskew, Otsu, 4-orientation, keep best-parsing); EV-matrix decision layer; honest 5-fold CV |
| Abhishek21g | 113.70 | 37.10 | 63.60 | 13.00 | embedded rasters; header crops for fee; unsharpened pass for `Observed flags:` |
| strobl | 100.11 | — | — | — | render-first (text layer diagnostic only — cleanest injection defense); frozen split hygiene |

The 126–132 tier shares: (a) multi-pass OCR on every weak page, (b) aggressive vocab repair,
(c) expensive retries gated on "no flags recovered yet", (d) a cost-sensitive decision layer that
tolerates a few CFAs instead of retreating. Independent corroboration of our geometry finding
(orientation fallbacks everywhere; nobody needed higher resolution). dw820's render-only trust
model is the cleanest injection defense seen. Two competitor negatives worth heeding:
**stamp detection is a trap** (colors don't correlate with fee/adjudication — silent stamps are the
under-determined cases, not a vision problem), and **attestation prose alone must not approve**
(it caused a false approval over a silent warrant page).

## 6. Signal-space verdicts: which evidence modalities pay, which leak

Condensed from the 2026-07-24 signal-space investigation (the full doc, with the probe tables, is
`docs/SIGNAL-SPACE.md` in git history; its probes ran on small stratified dev samples — directional
evidence, not tunable point estimates). Its one ship recommendation — the defer-selection ensemble
merge — later landed as the row-30 keystone (+0.74 dev), which **supersedes** the doc's original
"keep the seam closed" bottom line; some probe scripts it cites (`signal_probe.py`,
`crossdoc_probe.py`) no longer exist in the checkout.

**The discipline, which outlives the investigation.** Every candidate signal is one of two kinds:

- **Evidence-trust** — *how much to trust a read value*: cross-variant agreement, OCR recognition
  confidence, cross-document consistency, injection presence. Plausibly transfers to the private
  set — but must be proven to.
- **Damage-quantity** — *how mangled the page is*: skew angle, shred-band count, `n_scan_pages`,
  readable-variant count. Correlates with the label only because the generator damages DENIED
  packets more (48.6% vs 25.1% APPROVED, clean vs scanned), a generator property that will not
  transfer. **Off-limits as a decision input** — it is the measured cause of death of the
  `b13_census` prize (experiments rows 24, 27; STATUS hazards).

**The drift check (mandatory, mechanical).** For any signal we'd trust: compare its distribution on
dev vs a sample of the unlabeled validation PDFs. If its correctness correlation rides on a quantity
that shifts between the two, reject it — no matter how good its dev OOF looked. Confirmed both ways:
`agreement` is stable (0.911 dev / 0.932 val) while `readable_variants` drifts +27% and the
clean-packet rate shifts 0.159 → 0.056.

| signal → use | measured | verdict |
| --- | --- | --- |
| Ensemble agreement → per-field merge | +1…+5/field at ensemble level, 0 wrong-value regressions (incl. all three −4-sensitive fields); drift-stable | **shipped** as the row-30 keystone |
| Per-word Tesseract confidence (TSV) | on-truth tokens sit +15.9 conf above the page mean; no case-level calibration lift | live lever for *value selection*, not calibration — STATUS question 7 |
| Ensemble agreement → calibration | case-level separation +0.008; even in-sample, Brier 0.187 vs the per-branch table's 0.117 | dead — the branch table already wins |
| Cross-doc disagreement → demote to NR | lift +0.05 (noise); blanket demotion masks 8 correct denials, rescues 0 | dead as a decision input; residue: candidate-level *name* disagreement recalls 3/3 truth `identity_conflict` vs the shipped check's 1/3 at ~15% precision — a possible recall tweak, still open |
| Injection presence → adjudication | P(wrong \| injection) +0.02 over base | dead — the quarantine holds; injected cases are misadjudicated at the base rate, not followed |
| Conf-filter / char-whitelist / binarization OCR retries | +0 over the production variant union (gains vs a single raw pass were already banked by turn/skew/deshred) | dead — the OCR recovery floor is solid; residual misses are genuinely unrecoverable or injection-only |
| `readable_variants`, `margin`, `snap_ratio` | weak or degenerate; `readable_variants` drifts +27% dev→val | dead — damage-coupled or no discrimination |

Residue worth keeping: the flag legend guard (`signals.py`, `len(found) <= 3`) is the one
early-exit with adjudication stakes and no measurement behind it; a timed-out Tesseract call enters
the ensemble as an empty reading, indistinguishable from a blank page (an observability gap for any
agreement-family signal); and with ~11× runtime headroom (row 19), no early-exit in the pipeline is
justified by compute scarcity anymore — the ones that stay, stay for correctness or determinism.
