# Label mining: how far do FIELD_MANUAL rules go on ground-truth fields?

Date: 2026-07-21. Source: `../mib-doc-challenge/data/train_labels.csv` (1,000 rows) — analysis done before any PDFs were available. Question: **if field extraction were perfect, how well would policy rules alone predict the adjudication?**

Class balance: 431 DENIED / 289 APPROVED / 280 NEEDS_REVIEW.

## Manual rules that hold 100% on train

| Rule | n | Outcome |
| --- | ---: | --- |
| Any disqualifying flag (memory_tampering, planetary_embargo, active_warrant, biohazard_red) | 186 | DENIED 186/186 |
| visa_class = TRANSIT-7 | 53 | DENIED 53/53 |
| fee_status = unpaid | 50 | DENIED 50/50 |
| fee_status = unknown | 44 | NEEDS_REVIEW 44/44 |
| Stale arrival (>180 days before receipt ≈ 2026-07-07), non-DIP-1 | 36 | DENIED 36/36 |

Receipt-date note: no receipt date appears in the labels; 2026-07-07 (the data version date) makes the staleness rule fit perfectly. The real pipeline should prefer a visible packet receipt date from the document when present, with the constant as fallback — do not blindly hardcode.

Stale + DIP-1: 13 APPROVED / 3 NEEDS_REVIEW — matches the "DIP-1 with a valid diplomatic note" exception; note visibility lives in the PDF.

## Inferred policy (the manual invites this)

**Additional revoked sponsors** — non-DIP outcomes per sponsor, zero approvals each:

| Sponsor | Non-DIP outcomes |
| --- | --- |
| SPN-2718 | 13 DENIED |
| SPN-7331 | 14 DENIED |
| SPN-9090 | 11 DENIED |

Same signature as the published revoked list (SPN-0007: 13 D, SPN-0139: 13 D, SPN-4040: 15 D). Treat all six as revoked. Inference method: sponsors appearing ≥2× in otherwise-rule-clean DENIED cases, then checked for zero non-DIP approvals. This is policy inference from labeled examples (explicitly invited by FIELD_MANUAL: "Other revoked sponsors may appear in examples"), not per-case memorization.

**Revoked sponsor under DIP-1 is fine:** 11/11 such cases APPROVED — consistent with "sponsor not required for DIP-1".

## Ambiguity that fields alone cannot resolve

- Single review-only flag: 194 NEEDS_REVIEW / 56 DENIED. The DENIED cases mostly carry a co-occurring condition (revoked sponsor 12, unpaid 10, TRANSIT-7 9, stale 8, waived-non-DIP 7) — i.e. cascade ordering handles many, not all.
- MED-3 + illegible_biometrics: 33 DENIED / 28 NEEDS_REVIEW — the "requires clean biohazard check" interaction; the split is not predictable from label fields.
- 2+ review-only flags: 24 NEEDS_REVIEW / 5 DENIED ("may combine into a denial in edge cases" — combos not identifiable from labels alone).
- fee waived on non-DIP-1: 89 DENIED / 46 NEEDS_REVIEW / 37 APPROVED — hinges on whether a hardship waiver is *visible* in the document.

## Rule-cascade ceiling experiment

A first-draft cascade (disqualifying flags → TRANSIT-7 → unpaid → unknown fee → revoked sponsor (non-DIP) → stale (non-DIP) → waived-non-DIP → MED-3+illegible → any flag → NEEDS_REVIEW → else APPROVED), run on **ground-truth** fields:

- Accuracy: **84.6%**
- 9 false approvals (truth DENIED, cascade says APPROVED) — all look "clean" in the labels (paid, no flags, valid-looking sponsor, fresh date): the denial reason is only visible in the PDF (stamp, signed note, waiver problem, …).
- 65 truth-NEEDS_REVIEW predicted DENIED — cases where the label records the true field value but the *evidence* in the document was presumably illegible/conflicting/untrusted.
- 37 truth-APPROVED predicted DENIED — over-aggressive rules (waived-non-DIP and single-flag handling need refinement or a learned residual model).

## Conclusions

1. ~85% is the ceiling for *any* pipeline that only extracts the 10 schema fields, even perfectly. Document-level signals (adjudicator stamps, signed notes, waiver/diplomatic-note visibility, legibility, evidence conflicts) are required to go higher — and specifically to eliminate false approvals.
2. The deterministic rules cover ~half the caseload with certainty; they should short-circuit before any learned model.
3. The residual ambiguity (single-flag cases, MED-3 biometrics, waived-non-DIP) is exactly where a small trained classifier over extracted-field + document-signal features should operate.
