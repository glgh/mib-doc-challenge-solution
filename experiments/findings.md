# experiments/ findings

Dated notes from detector R&D. Newest first.

## 2026-07-23 — faint-scan reading-failure class (021 p2, 129 p0); the pre-OCR normalization gap

A second failure mode, **optical not geometric**, worth coming back to. Two pages
read as ~nothing usable and it is not damage any restoration here addresses:

| case | mean | ink@<128 | what it is | fields parsed |
| --- | ---: | ---: | --- | ---: |
| MIB-000021 p2 | 243 | 3.5% | Registry Extract | 0 |
| MIB-000129 p0 | 248 | 2.1% | **Manual Adjudicator Note** (top trust tier) | 0 |

Mechanism:
- `INK = 128` is a *measurement* threshold (skew / `_band_offsets` / orientation),
  **never applied to the OCR input** — tesseract is fed the raw grayscale and does
  its own internal Otsu (`render.reads_for` writes original bytes or
  `to_png_bytes(gray)`; no thresholding, no contrast normalization anywhere).
- On a faint page the strokes sit at ~190–230, so `(gray < 128)` captures almost
  nothing → `skew_sweep` hits its `len(ys) < 50` guard and returns `None` ("too
  little ink"). So the faint page is **doubly** hurt: our geometry detectors go
  blind *and* tesseract gets low global contrast. `INK`'s comment ("glyphs are
  near-black") is simply false here.

Enhancement tried, all yielding **only garbage lines**: autocontrast(cutoff 1),
otsu (degenerates to threshold 255 on these bright pages), linear stretch 195→250,
+`MinFilter(3)` stroke-thicken, +2× upscale, across PSM 11/6/4/3. Best fragment
ever recovered is 129's "Manual Adjudicator Note" header, barely. So these two are
**probably genuinely degraded** (broken/dropped strokes), not merely faint —
whether a human can read them is unresolved and needs an eyeball.

**Deferred lever (do not lose): a guarded faint-page normalization** applied
*before* both measurement and OCR, gated on a faintness signal (e.g. page mean >
~230 **and** `(gray<128)` fraction < ~1–2%) so good pages stay untouched (global
autocontrast would amplify speckle/JPEG noise into fake ink and mislead the
INK-based detectors). Payoffs, ranked by certainty:
1. **Certain / a real bug fix:** un-blinds geometry on faint pages — today we
   cannot even deskew them because the ink mask is empty.
2. **Uncertain:** OCR lift on the *moderately*-faint tail (NOT 021/129, which
   enhancement does not rescue). Must be sized on the population via
   `recoverable.py` `+ocr`, held to CFA 0, and eyeballed (real text vs noise).

Prototype in `experiments/` before touching the pipeline. Tracked in
`hard_cases.jsonl` as the two `ocr`-track cases.

## 2026-07-23 — OCR A/B kills content-correlation deshred; the guard is the answer

Ran `repair_bench.py --set hard --ocr` — the first OCR-level A/B of
`deshred_content` (the border-proposes/content-confirms fusion) against
`deshred_border` (shipped `realign_bands`). Evidence score per case, on the image
after orient+deskew:

| case | shred | deskew | border | content |
| --- | --- | ---: | ---: | ---: |
| MIB-000037 p0 | **False** | 9 | 9 | 9 |
| MIB-000221 p0 | False | 6 | 6 | 6 |
| MIB-000189 p2 | False | 14 | 14 | 14 |
| MIB-000045 p2 | True | 3 | **6** | 3 |
| MIB-000013 p2 | True | 7 | **9** | 8 |
| MIB-000125 p4 | True | 0 | **2** | 1 |
| MIB-000165 p1 | True | **6** | 2 | 3 |
| MIB-000237 p1 | True | 12 | 12 | 12 |

**Content trails border on every true positive.** Instrumenting the per-band
shifts shows why, and it's fatal to the idea: cross-correlating *column
ink-profiles between bands that hold different text* is not a shift estimator. On
045's high-ink bands (ink 2045, 1887, 2490) `content_shift` rails to −124/−152/−160
(the ±160 span limit) — it aligns unrelated ink distributions and drifts to the
edge. So the content gate rejects the real bands and under-corrects. And on 037 it
does the opposite of the design: `content_shift ≈ border_shift` on nearly every
band (both −30, −46, −54…), so it would *fire*. The signal separates neither
direction. **Content-correlation deshred is abandoned.**

**The bigger finding — the 037 hazard was overstated.** OCR'd 037 before/after
`realign_bands` at the field level:

```
037 before:  Applicant: Arivara Zavoss   Declared Purpose: nrealta consunt (garbage)
037 after :  Appiicarit: arivard Zavoss  Declared Purpose: medical consult (RECOVERED)
045 before:  Le Snqnsauavaciation…  Purpose -tnpronraine   (no sponsor line)
045 after :  Sponsor Attestation…   Purpose: diplomatic   Sponsor: SPN-2847
```

Border-deshred on 037 mildly hurts the applicant (`Arivara`→`arivard`) but *fixes*
declared purpose — net roughly neutral, ev-flat at 9. Not the "corrupts the one
image we OCR" catastrophe the plan built around. Meanwhile 045 is a decisive win.

**Conclusion / direction.** Precise shred *detection* is not the lever. The border
corrector (`realign_bands`) is broadly beneficial; its only real regression here is
165 (6→2, order-sensitivity), and that is exactly what a **bounded OCR guard**
catches — OCR the pre-deshred and post-deshred images, `best()` keeps the max.
Answering the user's open question directly: **the guard is needed** (for 165), and
keeping it makes detection precision non-critical, so we don't need a razor-sharp
shred detector at all. The runtime win comes from the *orientation* detector
(done, 13/13), which removes the blind turn-90/turn-270 fan-out; deshred stays a
cheap 2-way guarded step (1 OCR typical, 2 on pages with a full-width border).

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

## 2026-07-25 — deferred variant selection priced offline; illegible_biometrics is a structural gap

Ensemble cache: `dump_reads.py` now dumps EVERY OCR reading per scan page (not
just `best()`'s winner) — 92 hard-set cases in `output/cache/reads_hard.jsonl`,
so merge strategies replay in seconds with zero tesseract.

**Scalar per-field vote** (`merge_probe.py`, text-layer precedence kept; vote
fills only non-text fields, plurality over valid normalized values across all
readings):

| strategy | fields | adj_ok | CFA |
| --- | ---: | ---: | ---: |
| best (shipped) | 581 | 78 | 0 |
| plurality | 600 | 80 | 0 |
| plurality_valid | 600 | 80 | 0 |

+19 field instances (applicant_name +7, fee_status +5, home_world/sponsor_id/
arrival_date +2, visa_class +1), +2 adjudications (fee_status feeding policy),
CFA 0. The year-plausibility filter adds nothing over `parse.valid_value`'s
calendar hardening (b926403).

**Flag union across variants** (`flag_probe.py`, per-read decoy filter + flag-
doc-type gate mirroring assemble; risk_flags scored as evaluate.py does, exact
set): union = +3 flags, all TRUE, **zero hallucinated**, rf_match 73→75, CFA 0;
quorum2 strictly less. The feared fuzzy-matcher hallucination on garbled losing
variants did not materialize.

**The remaining 17 rf misses are one structural gap, not a merge problem.**
14/17 miss `illegible_biometrics`. Train-wide: 223 truth cases carry it, only
83 print it (`Observed flags:` line in a text layer); current recall 54%
(120/223). The 103 FN are condition-derived labels — the B-13 is present but
unreadable, dominant shape (76 cases) so damaged the page isn't even detected
as a biometric doc. Predicate sweep through real policy (`train_bands.jsonl`
substrate): P1 (bio & !flag_ev) fixes 13 / breaks 11; dead-page census variants
fix 32–45 / break 91–102 — dead pages are geometrically identical to non-B-13
dead pages (all 612×792, image aspect 1.3, flat position distribution) and
their OCR debris carries no type tokens. **No cheap discriminator exists; ~+0.8
dev ceiling stays open.** Possible future angles: ensemble-wide fuzzy B-13
tokens, doc-census elimination (which expected doc is unaccounted for), or an
emission story compatible with the organizer ruling (an unreadable slip *is*
visible evidence of illegibility — argue it, don't guess it).

These numbers informed the keystone build (ensemble across the seam, vote +
flag-union at the merge) — they did not authorize it. See the house rule
rewrite (BACKGROUND.md, same day): measure to learn, not to authorize.
