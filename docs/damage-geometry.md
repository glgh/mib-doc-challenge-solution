# Scan damage is geometric, not optical

Survey date: 2026-07-22. Sample: every 8th train case (125 cases, 110 with scan
pages, 259 scan pages), each page OCR'd at 0/90/180/270 and scored with the
pipeline's own `recognized_keys()`.

## The correction

[experiments.md](experiments.md) row 8 concluded "OCR quality on this corpus is
bimodal — pages either read fine at 200 DPI or are synthetically destroyed."
[rethink-2026-07-22.md](rethink-2026-07-22.md) already walked half of that back
(the pages are human-legible) and proposed a PSM-mode ensemble as R1.

Both were aiming at the wrong axis. The unreadable pages are not degraded and
they are not a page-segmentation-mode problem: they are **geometrically
transformed**. Undoing the transform recovers the text at 200 DPI with the PSM
11 pass we already run. Resolution cannot help, because nothing was lost.

## Damage taxonomy

Three transforms, which co-occur:

1. **Quarter turns.** Whole pages stored at 90° or 270°. Of 259 surveyed scan
   pages, best rotation was 0° on 233, 90° on 17, 270° on 9. **180° never won
   on any page** — it is not part of the generator's repertoire.
2. **Skew.** Several degrees of tilt: −4.25° on MIB-000030 p0, +2.25° on p1.
   Enough to defeat Tesseract on text a human reads without effort.
3. **Band displacement ("shredder").** The page is cut into horizontal bands
   and each is slid sideways. Bands that cut mid-glyph destroy that line; bands
   shifted far enough push content off the left page edge, where it is
   genuinely lost.

## What it costs

- **36%** of scan pages (92/259) yield zero recognizable field labels upright.
- **10%** of scan pages (26/259) are rescued by rotation alone.
- **15.5%** of cases (17/110) carry at least one turned page that currently
  contributes *nothing*.
- Those cases extract at **52%** against a corpus-wide **75%**.

The turned pages are disproportionately expensive because the generator turns
whole documents, and the intake form — the highest-precedence source, carrying
6 of the 9 scored fields — is as likely to be turned as anything else.

## Orientation detection: what does and doesn't work

| Method | Result |
| --- | --- |
| Tesseract OSD (`--psm 0`) | Unusable. Bailed with "Too few characters" on 2 of 3 test pages; 0.44 confidence on the third. These forms are too sparse. |
| Projection-profile spikiness | 32% (62/193). The faint form rules dominate both projections. |
| Ink run-length anisotropy | **86%**; 91% at margin ≥0.05, 93% at ≥0.20. |

The run-length rule is counterintuitive and easy to get backwards: for
*horizontal* text the **vertical** runs are longer, because character stems run
the full x-height uninterrupted while horizontal runs are chopped at every
inter-character gap. The first implementation had the sign inverted and scored
14% — a detector that good at being wrong is a detector with a flipped sign.

Because the budget has ~6x headroom, detection is currently used only to
*order* candidates, never to decide: a wrong decision silently discards a
page's text, whereas a wrong ordering just costs one OCR pass.

## The best untapped signal: the printed page border

Every form carries a printed border rectangle of **constant width** (1085 px on
MIB-000030 p0). That makes it the best-conditioned feature in the corpus:

- its edges give skew far more precisely than a text projection;
- its left-edge x **per row is literally the shredder's band offset**, which is
  what `imaging.realign_bands()` keys off;
- it is present on pages with almost no text — exactly where every text-based
  detector fails.

## Results

Dev split (700 cases), cumulative `MIB_RESTORE` levels, CFA 0 at every level:

| Level | Total | Extraction | Classification | Calibration | Wall |
| --- | ---: | ---: | ---: | ---: | ---: |
| `off` | 114.50 | 38.53 | 60.94 | 15.03 | 295s |
| `skew` | 115.20 | 38.76 | 61.27 | 15.16 | 1280s |
| `turn` | 116.59 | 39.58 | 61.79 | 15.22 | 927s |
| `bands` | **116.88** | 39.62 | 61.97 | 15.30 | 608s |

Brier improved 0.1243 → 0.1176 as a side effect: better extraction lands cases
in better-calibrated branches.

Per-case, on the two packets that prompted the survey:

| Case | Extraction `off` | Extraction `bands` |
| --- | ---: | ---: |
| MIB-000030 | 8/45 | 36/45 |
| MIB-000131 | 4/45 | 27/45 |

Wall-clock *falls* as restoration deepens (`bands` is the fastest of the three)
because each level rescues more pages before the expensive 200-DPI re-render,
and rescued pages trip the `GOOD_ENOUGH` early exit. These are laptop seconds
with other work running alongside; the Docker-limits run is what counts.

## Known inefficiency

The current flow is **repair-after-failure**: OCR → looks bad → repair → OCR
again. Every repaired page burns at least one pass that was always going to
fail. Since skew and axis are both measurable in ~5 ms of numpy, the flow should
be **detect-then-repair**: measure geometry, fix the image, OCR once. Not yet
implemented.

Related: the 200-DPI re-render is now largely wasted work — the embedded raster
is already 1224×1584 and row 8 established resolution is not the failure mode.
It should be gated on "restoration didn't help either" rather than run as a peer
candidate.

## Residuals this does not fix

- **Names need exact matches.** `Mirayoss` vs truth `Miravoss` scores 0. Both
  p0 and p1 read the name correctly in isolation; the precedence merge picked a
  worse variant. Cross-page voting is the obvious fix (probe written, not run).
- **Self-contradicting packets.** MIB-000131 reads `SPN-1106` where truth is
  `SPN-2378`, and that case's truth `risk_flags` is `identity_conflict` — the
  cross-page disagreement *is* the signal, and we currently discard it by taking
  the most-trusted reading and stopping.
- **`fee_status`** remains the blocker on MIB-000030 (truth `waived`, a DIP-1).
  Now a policy/evidence question rather than an OCR one.
