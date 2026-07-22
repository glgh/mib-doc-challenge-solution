# Scoring mechanics and what they imply for the design

Source: `../mib-doc-challenge/scripts/evaluate.py` (public, deterministic — the exact scorer 8090 runs) and `EVALUATION.md`. 150 points total: 80 classification, 50 extraction, 20 calibration, up to −10 missing-case penalty.

## Classification (80 pts)

Per-case raw points, normalized to 80 over all cases:

| Outcome | Raw |
| --- | ---: |
| Correct label | 8 |
| Truth A/D, predicted NEEDS_REVIEW | 2 |
| Truth NEEDS_REVIEW, predicted A/D | 1 |
| Wrong APPROVED↔DENIED | 0 |
| Invalid/blank adjudication | 0 |
| **Truth DENIED, predicted APPROVED** | **−4** |

Catastrophic false approvals are also counted separately in `evaluation.json`, and the interview bar requires "no catastrophic false-approval pattern" — so the −4s matter beyond the arithmetic.

**Implication — decide by expected points, not argmax.** With calibrated probabilities P(A), P(D), P(N):

- E[APPROVED] = 8·P(A) − 4·P(D) + 1·P(N)
- E[DENIED] = 8·P(D) + 0·P(A) + 1·P(N)
- E[NEEDS_REVIEW] = 8·P(N) + 2·(P(A) + P(D))

Pick the argmax of expected points. Consequence of the asymmetry: with P(N)≈0, approving beats hedging only at P(A) ≳ 0.5, while denying beats hedging already at P(D) ≳ 0.25. Denial is cheap (worst case 0), approval is expensive (worst case −4).

## Extraction (50 pts)

Raw field weights, normalized to 50: risk_flags 8, species_code 6, applicant_name / home_world / visa_class / sponsor_id 5 each, arrival_date / fee_status 4 each, declared_purpose 3. `case_id` is required for a row to score at all.

Matching (`normalize()` in evaluate.py): casefold + collapse whitespace + **exact** string equality. `risk_flags` is compared as a sorted set of pipe-delimited tokens; empty/`unknown`/`null` normalize to `none`.

Implications:
- **Formatting is scoring.** Dates must be ISO `YYYY-MM-DD`; visa/species/fee/flags must be snapped to exact vocabulary strings (an OCR en-dash in `XW–1` scores zero).
- **Always guess.** A wrong value scores the same as a blank (0), so emit the best guess for every field on every case.
- Fields the private labels mark unrecoverable are removed from that case's denominator — hard PDFs are a gradient, not perfect-or-fail.

## Calibration (20 pts)

`20 · max(0, 1 − 2·mean_brier)`, where each case's Brier target is 1.0 if our adjudication was correct else 0.0. Invalid confidence (non-numeric or outside [0,1]) counts as Brier 1.0. Mean Brier ≥ 0.5 zeroes the section.

Implication: `confidence` should literally be our calibrated probability that the chosen adjudication is correct — which falls out of the classifier + isotonic calibration for free. Roughly: a well-calibrated system at ~85% accuracy earns ~15/20; constants do much worse.

## Missing cases (−10 max)

Penalty = `10 · missing/total` ≈ 0.002 pts per omitted case on the 5,000-case validation set. A submitted NEEDS_REVIEW earns ≥1 raw classification point even when wrong, and an honestly low confidence limits the calibration damage. **Essentially never omit a case**; omit only when no valid `case_id` can be produced.

## Validity gates

`evaluate.py` exits 2 on: duplicate case IDs, unexpected case IDs, invalid adjudication enum, invalid confidence, invalid `fee_status`. The pipeline therefore needs a final output-validation stage (dedupe, enum check, confidence clamp, date format) so no upstream bug can produce invalid rows. Interview bar: 90%+ valid predicted rows.

## Local loop

Because the scorer is public, the train-set eval loop (see CLAUDE.md) gives the *exact* objective. All tuning (rule thresholds, decision rule, calibration) should be validated against `evaluate.py` total score under cross-validation — not proxy accuracy.
