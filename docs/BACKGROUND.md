# Background: the evidence behind the constants

_Last updated: 2026-07-23. Rewrite in place when a finding changes; do not append._

Why the policy constants, signals, and restoration cascade are what they are. The code is in
`mib/`; [ALGORITHM.md](ALGORITHM.md) says what it does; this file says *why those numbers*. Sources
are labelled train-mining (`../mib-doc-challenge/data/train_labels.csv`, 1,000 rows:
431 DENIED / 289 APPROVED / 280 NEEDS_REVIEW), organizer rulings (authoritative), or competitor
intel (hypotheses, verified before adoption). Scoring mechanics live in the challenge's own
[EVALUATION.md](../../mib-doc-challenge/EVALUATION.md) and CLAUDE.md.

House rule: **no signal ships until validated on train.** Every constant below was measured
before it entered `mib/`.

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
perfectly, but the shipped cutoff is the **max-margin midpoint 2026-01-02** (middle of the empty
48-day band between latest stale-denied and earliest fresh arrival). Stale + DIP-1 splits
13 A / 3 NR — the "DIP-1 with a valid diplomatic note" exception, whose visibility lives in the PDF.

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

### Embargo worlds

| World | Rule | Evidence |
| --- | --- | --- |
| TRAPPIST-1e | DENIED incl. DIP-1 | 32/32 embargo, 32/32 DENIED (8/8 DIP-1) — **pierces diplomatic immunity** |
| Eris Relay | DENIED incl. DIP-1 | 18/18 embargo, 18/18 DENIED (5/5 DIP-1) |
| Wolf-1061c | DENIED non-DIP only | non-DIP 51/51 DENIED (mostly *without* the flag — partial embargo); DIP-1 ~normal |

Full-embargo worlds also *imply* the `planetary_embargo` flag (earns `risk_flags`, weight 8); do
**not** infer the flag for Wolf-1061c (labels usually lack it).

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
  comparing); multi-applicant decoy pages ✅ (filter by active-case-id majority); B-13 species/
  biometric-confidence as identity signals ⬜.
- **Sponsor fraud**: revoked sponsor ✅ (§2); attestation SPN/name ≠ intake → `sponsor_mismatch`
  🔶 (require text-layer evidence one side + edit distance ≥2, never OCR-vs-OCR — OCR debris
  fabricates it); missing sponsor non-DIP ✅ → treat as *our* extraction-quality signal (review,
  not deny).
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

**Results** (dev 700, cumulative `MIB_RESTORE`, CFA 0 at every level):

| Level | Total | Extraction | Classification | Calibration |
| --- | ---: | ---: | ---: | ---: |
| `off` | 114.50 | 38.53 | 60.94 | 15.03 |
| `skew` (shipped) | 115.20 | 38.76 | 61.27 | 15.16 |
| `turn` | 116.59 | 39.58 | 61.79 | 15.22 |
| `bands` | **116.88** | 39.62 | 61.97 | 15.30 |

`bands` is +2.38 over `off`, CFA 0, Brier 0.1243 → 0.1176. Per-case: MIB-000030 extraction
8/45 → 36/45, MIB-000131 4/45 → 27/45.

**Runtime is unresolved.** The only clean figure is `skew` at 1101 s over 1,000 PDFs on a contended
10-core laptop (mean 4.40 s/case, p99 57.5 s, max 107 s → ~1.53 h projected for 5,000 vs the 8.3 h
budget). `turn`/`bands` are unmeasured on any sample worth quoting; a prior conclusion that
"wall-clock falls as restoration deepens" was **retracted** (laptop seconds under load, not
comparable). Deeper levels add OCR passes, so assume cost *rises* until measured. Only
`scripts/run_docker_submission.py` under the real limits settles it — never yet run. That is why
`skew` ships and `turn`/`bands` stay flag-gated.

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
