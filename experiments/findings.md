# experiments/ findings

Dated notes from detector R&D. Newest first.

## 2026-07-23 — orientation false-positives from form rules; asymmetric threshold

The first `orientation` cut (anisotropy, turn when ratio < 0.95) wrongly turned
upright pages the user spotted in the bench: MIB-000109 p1, MIB-000193 p3,
MIB-000221 p2 — all upright form pages, rotated into garbage. Cause: their
horizontal **table rules** inflate the horizontal run-length, so the ratio dips
just below 1 (0.89–0.94) even though the text is upright.

The signal still separates cleanly — it was the threshold that was wrong:

| | ratio | correct |
| --- | ---: | --- |
| upright form pages (109/193p3/221p2) | 0.89–0.94 | upright |
| genuine turns (193p1, 221p1, 089, 037…) | 0.44–0.65 | turn |

A wide gap (0.65 → 0.89). Fix: **asymmetric, high-confidence bar to turn** — fire
only when ratio ≤ 1 − 0.20 = 0.80, default to upright across the whole ambiguous
band. Rationale: a wrong turn wrecks a good page; a missed turn just leaves it
upright (safe), and true turns are far more anisotropic so nothing real is lost.
Result: labeled orientation **12/13 → 13/13** (also fixed the 021 borderline), all
three false positives upright again, both real turns kept. Matches the survey's
"93% at margin ≥ 0.20."

## 2026-07-23 — border offsets alone cannot detect shred (`probe_shred.py`)

Ran three candidate shred detectors over the 9 geometry-track cases with a
`shred` label. Result: **none beat the current border-present baseline (6/9)**,
and the reason is in the diagnostics, not the rules.

The per-row border-offset profile is nearly identical for the false positive and
the true positives:

| case | expect shred | offset spread | seams (>15px) |
| --- | --- | ---: | ---: |
| MIB-000037 p0 | **False** | 109 | 22 |
| MIB-000045 p2 | True | 87 | 22 |
| MIB-000165 p1 | True | 107 | 22 |
| MIB-000013 p2 | True | 118 | 24 |
| MIB-000237 p1 | True | 65 | 19 |
| MIB-000125 p4 | True | 77 | 14 |
| MIB-000221 p0 | False | 2 | 0 |
| MIB-000189 p2 | False | 1 | 0 |
| MIB-000085 p2 | False* | 1 | 0 |

Two clean groups fall out — but not the ones we need. Spread≈0 (221, 189, 085)
is trivially not-shred and every detector gets it. The hard discrimination is
**037 (false) vs 045/165/013 (true)**, and there the border signal is
indistinguishable: same spread, same seam count. `_band_offsets` reads the outer
page border and the internal form-table rules the same way, and on 037 the
"displacement" is just the form rules sitting at different x — the **text never
moved**.

**Conclusion:** the border left-edge is the wrong signal. What separates real
shred from 037 is whether the *content* shifted with the border, not the border
alone. The recommended next probe is a **content-shift** discriminator: for each
band, measure the horizontal shift of its **text ink** (leftmost non-border ink,
or cross-correlation of column ink-profiles against a reference band) and fire
only when the content shift agrees with the border-derived shift. In 037 the text
left-margin is ~constant while the border varies → disagreement → no shred.

This also suggests a better *deshred algorithm*, not just a detector: measure each
band's shift from **content cross-correlation** rather than the border, which
removes the dependence on a full-width border being present (the current
all-or-nothing guard) at the same time.

*085 relabeled to shred-negative here — see hard_cases.jsonl.

### Update, same day — content-shift discriminator prototyped, 7/8

Added `d_content_shift` to `probe_shred.py`: segment rows into bands at the offset
seams, and for each band measure the shift its **text** underwent by
cross-correlating its column ink-profile against the largest band's. Fire only
when that content shift agrees with the border-implied shift (median |Δ| ≤ 18px)
and something actually moved (≥20px).

| detector | score |
| --- | ---: |
| border_present (current) | 5/8 |
| step_structured | 4/8 |
| few_modes | 4/8 |
| **content_shift** | **7/8** |

content_shift fires on all five true-shred cases and correctly rejects the flat
negatives (221, 189) that border-present false-fires on. **The single miss is
037** — and that is probably *correct detection of a real problem we mis-handle*:
037's Case ID line genuinely is sheared, so there is real shift on the page; the
damage is that `realign_bands` applies the wrong per-band shift and garbles the
applicant. So 037 splits into two questions:

1. **Detector**: is content-shift right to fire on 037? Arguably yes.
2. **Algorithm**: `realign_bands` mis-corrects it. The fix is to drive the shift
   from **content cross-correlation** (what content_shift already measures) rather
   than the border left-edge, so the applied shift is the one the text actually
   needs. This is the same signal doing detection *and* correction.

**Direction for the plan:** promote a content-cross-correlation deshred — one
routine that (a) decides whether to fire and (b) computes each band's shift from
content — replacing both the border-based detector and the border-based
`realign_bands`. Tune its tolerance against this set, watching 037 and the flat
negatives. Then re-confirm end-to-end that 037's applicant no longer garbles.
