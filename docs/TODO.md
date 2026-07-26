# TODO: the OCR-quality try-list (from the 2026-07-25 autopsies)

_The single todo tracker for this work. Update statuses in place; log scored results as
experiments.md rows and link them here. Background evidence: BACKGROUND §3 (flag label mining,
incl. the mangled-value geometry table), STATUS Q6/Q7. Fact-checked 2026-07-25 against docs,
caches, and code; anchor strings verified verbatim in the ensemble caches._

**Working style (user directive): hard examples first.** Every item below names its anchor cases
and the expected per-case outcome. An item is tried against its anchors before any full-corpus
measurement is spent; the full dump regenerates only when something has already proven itself on
the anchors. No "regenerate everything" as step one.

**The anchor set** (dev split except where marked; ladder galleries and page renders from
2026-07-25; similarity ratios throughout are on lowercase-alnum-normalized text — raw
`SequenceMatcher` gives different numbers):

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

Instruments already built: `output/cache/reads_hard.jsonl` (92-case hard ensemble, optical rungs
on), `output/cache/train_bands.jsonl` (full-train ensemble, schema 2 with `struck` backfilled —
supersedes `train_bands_reads.jsonl` as the current substrate), `experiments/mine_hard.py`,
`scripts/replay.py` (frozen-behavior reference gate), `experiments/ladder_gallery.py` (promoted
from scratchpad 2026-07-25).

---

## Track 1 — OCR confidence (Q7)

### 1.1 ☑ TSV conf probe — GRADUATED (user-judged galleries, 2026-07-25)
`experiments/conf_probe.py`, 13 pages (3 anchors + 10 stratified hard picks). Findings:
- **The `ev` pathology does not transfer**: 990's hallucinated `MIB-000000` line got conf 36 (page
  junk level); every repaired variant out-confs the tilted render `ev` crowned.
- **Metric shape matters**: plain mean is gamed by rotated few-confident-words reads; raw mass by
  debris volume; render-source boilerplate (footer `Packet MIB-… / page N`, watermark — conf 90+)
  biases any mass metric toward `render`. Winner: **guarded excess mass** = Σ max(0, conf−40) per
  word over non-page-furniture lines. User verdict on the 8 differ-pages: conf-pick no worse.
- **TSV line text ≠ stdout text on 93/111 reads** — "same pass, free conf" is false; 1.2 must run
  TSV additively (2nd pass) or re-baseline.
- Incidental: the `Packet MIB-XXXXXX / page N` footer premise of 3.3 is real on probe pages.

### 1.2 ☐ Capture conf in the pipeline (`records.Read.conf`, `mib/stages/render.py`, `mib/cache.py`)
**Single invocation, two renderers**: `tesseract img base txt tsv` emits BOTH the text file
(verified byte-identical to today's stdout output) and the TSV in one recognition pass at ~1×
cost — the 1.1 divergence was an artifact of reconstructing text *from* TSV rows; asking for both
renderers avoids it entirely. Text stays canonical from the txt renderer; conf (+ word count and
y-position per line, for the positional furniture guard) parses from the TSV. Old caches
rehydrate `conf=None` (keystone rehydration pattern, same as row 34's `struck=[]`).
Correctness-gated (house rule): replay diff empty with conf unused, suite green. Dump regen:
hard set first (subset-first directive), full regen with user approval, only after this lands.

### 1.3 ☐ Conf-aware selection A/B (`records.best_read`)
Offline on the regenerated cache: conf metrics vs `evidence_score` for the page-primary pick.
**Anchors:** 990 p1 must flip to the deskewed variant; no healthy-page regressions in the replay
diff. **Default plan is that conf replaces `evidence_score` as the selection metric** — then 1.5
and 1.6 die unbuilt; they exist below only for the case where conf can merely blend.

### 1.4 ☐ Conf-weighted variant-vote tie-break (`packet._variant_vote`)
**Blocked on anchor re-mining: the known tie cases (025 date / 037 name) are HOLDOUT**, so they
can motivate but never tune. First re-run the merge-probe tie mining restricted to dev, pick ≥2
dev ties as anchors, then A/B the conf-weighted tie-break against those. 025/037 may be checked
once, at the end, as part of a milestone holdout read — logged like every holdout read.

### 1.5 ☐ `evidence_score` pathology fix (only if 1.3 leaves the score in the loop)
Value-pattern credit requires the value's line conf above a bar — a well-formed misread
(`MIB-000000`) stops outscoring an honest partial read. Expected to die via 1.3.

### 1.6 ☐ GOOD_ENOUGH audit (rides along with 1.3)
Hand-picked 6 in `8d56832` as an early-stop bar; early-stop is dead (row 16). Today it gates only
the optical rung (`render.py`) and tooling's "hard page" cut (`mine_hard.py`). Dev evidence-score
distribution valley sits at ev=5, not 6 (session measurement, STATUS Q7). If the selection metric
changes, re-derive this gate from the conf distribution. Expected to die via 1.3.

## Track 2 — flag-line recovery ("line present, matcher/gate too strict")

### 2.1 ☑ Delete the flag-scan doc-type gate — SHIPPED (row 35, dev 122.44 → 122.79, CFA 0)
Doc-type filter dropped from `observed_flags`; `has_flag_evidence`'s positive clause shares the
widened scan; the `'flag' + none/clear` negative clause stays biometric-restricted (CFA-risk
direction, unmeasured). Anchors passed (656/771/979 emit, 747/506 silent) — but the "exactly
three cases change" expectation **failed in the good direction**: 18 cases changed, every added
flag true (the gate was also blocking planetary_embargo/biohazard_red/identity_conflict/
memory_tampering on header-mangled pages; the P=1.00 mining had only measured
illegible_biometrics). 3 correct NEEDS_REVIEW→DENIED flips, so classification moved after all.
Regression tests from the mined lines + controls; confidence table refit (score-neutral).

### 2.2 ☑ Unified weighted flag-value resolver — SHIPPED (row 36, dev 122.79 → 122.86, CFA 0)
Absorbed old 2.2 *and* 2.3 as one mechanism. `vocab.match_flag_value` (confusion-weighted,
alnum-normalized, whole-value); `snap("observed_flags")` refactored onto `match_flag_token`
(the parallel per-token difflib@0.8 path is gone — one metric, one threshold vocabulary).
Bars mined from the full safety table (`experiments/flag_probe.py --values`, 563 values): every
false at ≤0.40/≤0.08, every true at ≥0.44/≥0.10 → single-read 0.55/0.15, quorum-2 at 0.44/0.10,
OCR readings only. Anchors exact: 252/595 emit single-read, 990 by quorum, 747/506 /
`biometrics ok` / `none` silent; replay diff was exactly the three anchors + holdout 438
(planetary_embargo by quorum-6, truth-confirmed). Regression tests shipped. The mining also
surfaced the space-split class (`illegible biometrics` scores 1.00 whole, invisible to the token
path). Broken-toner note: repair can't read 252/990 (optical rungs tried live in session — more
mangles, no matchable value; *unrecorded* beyond commit `b1199a5`'s dev-wide
11-recovered/10-corrupted A/B), so this resolver was the only live path to that class.

## Track 3 — visual defects

### 3.1 ☐ Color-aware masking (`imaging`, pre-`to_gray`)
Saturation mask → paper white: stamps (252 red, 990 purple — session observation, unrecorded),
SAMPLE DENIAL watermark (96/595), yellow highlights (65/252) are colored; evidence ink is black.
**Anchors are controls here:** on all four autopsy pages the overlays miss the field lines, so
expect no change — the value, if any, is elsewhere in the corpus. Try on the hard set first;
before/after gallery to the user (house rule: their eyes judge repair quality).

### 3.2 ☐ Content-anchored deshred (`imaging.realign_bands`)
Fall back to text-line continuity when the printed border is destroyed (`_band_offsets` needs a
full-width border and ≥20 readable rows; 595's border is itself shredded). **Anchor:** 595 p3
bands visibly align (gallery to user); its flag value should then read closer to matchable.

### 3.3 ☐ Footer registration anchor (exploratory, only if 1–2 stall)
**Premise unverified:** no doc records a `Packet MIB-XXXXXX / page N` footer at a known position —
BACKGROUND §4 names the page *border* as the registration signal. First render a page sample and
check the footer exists, survives crop/shred, and sits where claimed; only then design
orientation + skew + case-id cross-check around it. Mostly a runtime win if real.

## Track 4 — dispositions (no action unless reopened)

- **Optical rung** (`MIB_OCR_OPTICAL`): stays off. Recorded evidence: unguarded dev-wide A/B — 11
  fields recovered, 10 corrupted (commit `b1199a5`); gated form shipped but off. Session-recalled,
  *unrecorded*: live tries on 252/990 produced more mangles and no matchable value; glyph-close
  hurt; skew angle brute-force-checked optimal. Treat the unrecorded parts as leads, not results.
  Revisit only with a corpus-wide bench after 3.1.
- **Condition-based flag emission**: dead. P(truth | any observable condition) ≤0.38; the
  generator's B-13-included-then-destroyed vs never-included distinction is invisible in the PDF
  (BACKGROUND §3). Printed evidence only — which is also organizer ruling §1.
- **Docker gate for the full ladder** (STATUS Q3 second half): standing, unrelated to this track.

## Verification discipline (every shipped item)

- Anchors first; full measurement only after anchors pass.
- `pytest` green; regression tests for every emission change (2.x) from the mined lines.
- Replay diff vs current predictions reviewed line by line; for 2.x: no new flag without a
  visible-evidence source, CFA stays 0.
- One experiments.md row per shipped change; statuses here updated in the same commit.
- Repair changes (3.1/3.2): before/after gallery sent to the user — their eyes decide.
- Dev split only; 025/037 (holdout) are never anchors for tuning.
