# Probe findings

Offline probes over the shared hard-case set (`cases.txt`). Baselines are the repo's measured priors (experiments rows 76–78, the +0 binarization sweep, sponsor <0.22 pts). "Recovery" = a technique reads the truth on a cell the shipped pipeline got wrong; "score" = official evaluator delta (usually ~0 on saturated fields).

## P1 — multi-binarization — GO (on recovery), reconciles with +0 (on score)

`p1_binariz.py` over 8 sponsor cells (6 located, 2 whose SPN line wasn't found).

- **Global Otsu recovers 3/6** located cells the pipeline missed: `MIB-000395 6146`, `MIB-000594 4867`, `MIB-000870 7561`. `otsu+open` matches; illumination-normalized Otsu adds `395`.
- **Otsu is not currently an OCR variant** — the pipeline feeds Sauvola (`imaging.local_threshold`) + autocontrast to tesseract and uses Otsu only for the ink-mask. Shipped Sauvola recovers **none** of the three; on these small bold cells local-adaptive over-segments while global Otsu nails the bimodal split.
- **Reconciliation with the repo's "+0 sweep":** both hold. Otsu genuinely recovers cases at the *cell* level, but 3 sponsor cells ≈ 0.02 pts, so it is still +0 at the *score* level. The prior was measured on score (correct); it did not mean "no cell is recoverable."
- **Verdict:** the "nothing more to get from binarization" wall is real *for sponsor score* but false *for recovery*. Otsu-as-an-OCR-variant is a cheap, genuinely-missing lever. Whether it earns its place depends on a higher-weight field (flags/vocab, P2) and a corpus-wide replay (graduation) — the per-cell probe cannot measure corpus score.
- **If graduated:** add Otsu to `imaging._OPTICAL_MODULES` (`render.py:157`) + `config.GRID_PRESETS["opt"]`, gate on full `train_grid` replay (expect ~+0 score, possibly a few field recoveries).

## P2 — template matching — NO-GO (independently confirms rows 77/78)

`p2_template.py` (sponsor per-digit NCC + fixed-pitch segmentation) and `p2b_flags.py` (whole-token flag match).

- **Sponsor digits:** even with segmentation fixed (rightmost-token off the Otsu line), the true digit ranks near the *bottom* of the NCC list (per-position ranks like 10/10/5/5); every read collapses to `4`s/`1`s. 0/6 recovered. Reproduces row 77's "at chance."
- **Flags (the specific intel):** on `070`, the only ink that survives OCR is `obsen —`; whole-token NCC ranks the two true flags **5th/6th of 8**, and argmaxes on `sponsor_mismatch` (0.105) — a *wrong* flag, at a 0.011 noise-floor margin. `957`'s flag line doesn't localize. This is worse than useless: it would emit a wrong flag. Confirms row 78 (2-case pool, ~0.16 pts, unvalidatable) and the geometry table (damage-marker argmax 0.30–0.36).
- **Why:** template matching and OCR need the *same* surviving glyph structure — on legible cells OCR already wins (Otsu, P1), on blotted cells only the envelope survives and it can't separate like-length candidates. Verdict stands after a genuine build, not just citation.

## P3 — rank-2 alternates — NO-GO (weak signal)

`p3_alternates.py` (tesseract 5.5.2 `lstm_choice_mode=2` hOCR, pooled over raw/otsu/sauvola).

- On 6 confused digit-positions, the truth is in tesseract's top-3 alternates **2/6 (33%)** — below the 40% bar.
- The 4 misses are telling: the alternates are dominated by the *confusion family* (`554` pos2 true `6`: alts `8,5,0,3`, truth at rank 6), not the truth. When tesseract makes a systematic error it does so confidently across its whole hypothesis list — the true digit isn't in it.
- The 2 hits (`784` pos3, `395` pos3) are rank-2 — exactly where a confusion-weighted decode *could* flip them. So alternates aren't dead, but they're a P4 input, not a lever on their own.

## P4 — noisy-channel decode — subsumed (not separately built)

Its extraction gain is bounded by P1+P3: across binarizations the confused reads agree on the same *wrong* closed-loop digit (e.g. `554` → `8,8,8`), so `argmax P(t)·∏P(obs|t)` still picks `8` (identity likelihood dominates). Recovers ≈ the couple of rank-2 cases P3 found → ~0 score on saturated extraction. Its only real value is as a *complexity* substitute for the hand-built `group_rank` tiebreak ladder — a code-health call, not a points call. Deferred to a code-health pass, not chased for score.

## P5 — unify the confusion tables — small win only (code-health, ~0 score)

`p5_confusion_tables.py`.

- `_OCR_SUB_COST` (29 pairs) is **21 letter↔letter** (o/c, o/e, t/f, d/o, e/c, v/y…) + 8 letter↔digit; `_DIGIT_CELL_FIXES` is glyph→digit for id cells. They share only the letter↔digit lookalikes.
- Where they overlap on digits they mostly **agree** (o/0, i/1, l/1, z/2, s/5, b/8). Two apparent conflicts — `_OCR_SUB_COST` `g↔9` vs `_DIGIT_CELL_FIXES` `G→6`, and `b↔6` vs `B→8` — are **domain/case-appropriate, not errors**: lowercase `g` resembles `9` in text fields, uppercase `G` resembles `6` in id cells. Merging would *lose* that specialization.
- **Verdict:** not duplicate tables to merge. A single *grounded* matrix would need mining the closed-vocab fields' letter confusions (a separate job the id-cell matrix doesn't supply). The unify win is small: dedup the shared lookalikes + optionally reconcile the two digit conflicts with measured data. Code-health only.

## P6 — probabilistic calibration — GO (+0.74 cal pts, holdout-validated) ★

`p6_calibration.py`, frozen holdout (300, seed 8090).

| model | Brier | cal/20 | Δ |
|---|--:|--:|--:|
| shipped confidence (per-branch fitted) | 0.0941 | 16.24 | — |
| isotonic recal of shipped | 0.0922 | 16.31 | +0.07 |
| logistic on debug features | 0.0763 | 16.95 | +0.71 |
| logistic + shipped conf | 0.0755 | **16.98** | **+0.74** |

- The shipped confidence is well-calibrated *as a curve* (isotonic adds only +0.07). The **+0.74** comes from **features the per-branch table ignores** — `n_fields_missing`, `has_biometric`, `has_flag_evidence`, correction/hit counts — i.e. genuine within-branch variance in P(correct).
- The **one probe with real positive score headroom**, and it's the user's "go probabilistic" idea paying off exactly where recognition couldn't.
- **Caveats before graduating:** single-split estimate (variance); replacing the interpretable per-branch table with a feature model adds a model artifact and a private-set generalization risk (calibration is scored on the private set). Graduation = a probabilistic `P(correct)` layer beside `confidence.py`, validated holdout-honest + checked it doesn't degrade private-set robustness, gated on the standard replay + suite.

## Bottom line
Recognition frontier confirmed **thin/dead** with real effort: template matching NO-GO (rows 77/78 reproduced, harmful on flags); rank-2 alternates weak (33%); multi-binarization recovers a few sponsor *cells* (Otsu, genuinely missing as an OCR variant) but ~0 *score*. The confusion-table unify is a minor code-health item. **The single actionable win is P6: a feature-based probabilistic confidence worth ~+0.74 calibration points on holdout.**

## P7 (bonus) — can the signal DRIVE decisions? — NO (re-derives the -0.50/14-CFA wall)

`p7_decision.py`: model P(truth=class | features), pick the action maximizing expected raw points under the asymmetric matrix (−4 false-approval priced in). Frozen holdout.

| policy | /80 | CFAs |
|---|--:|--:|
| current (shipped) | 65.47 | 0 |
| EV-optimal (−4 priced in) | 60.87 | **16** |
| EV-optimal, APPROVE forbidden | 56.73 | 0 |

- Driving decisions **loses 4.6 pts and adds 16 CFAs** — independently reproducing the deleted learned decider (STATUS.md: −0.50, 14 CFAs).
- The shift that does it: **71 NEEDS_REVIEW → APPROVED.** The EV wants to decide the over-referred cases, but those buckets are ~60–70% approvable with a *denied minority* — so committing catches the true-APPROVEDs **and** approves the hidden true-DENIEDs → CFAs by construction. The −4 is priced into the EV, yet the bucket ambiguity still makes APPROVE the EV-max action, so it fires and blows up.
- **The lesson — same model, two uses:** calibration (P6, +0.74) is safe because it *describes* uncertainty without changing a decision, so it can never create a CFA. Decision-driving is dangerous because the only way to win the over-referral points is NR→APPROVE, which is exactly where the −4 lives. This is the mathematical restatement of the repo's "needs a split, not a decider": P(truth|features) is essentially per-branch, and the branches are ambiguous by construction — the features don't separate approvable-from-deniable *within* a bucket.
- **Verdict:** the probabilistic layer belongs on the confidence/calibration output (describe), not the adjudication (act). Confirmed twice now, from a fresh angle.

## P8 — hunt the NEEDS_REVIEW split — NO legitimate split exists

`p8_split.py`, fee_unknown + b13_census buckets (dev 218 / holdout 85).

- **APP-vs-DEN within-bucket, holdout AUC = 0.607** on visible evidence — barely above chance. The deciding evidence genuinely isn't in the trusted channel (the fee / B-13 that would decide is *absent* by construction).
- **CFA-safe NR→DENY wedge: 0 committable cases, +0.0 net.** True-DENIEDs are the minority (12/85); no P(DEN) threshold is pure enough that committing beats the −2 (true-APP) / −7 (true-NR) costs. No safe wedge in either direction.
- **The weak signal is a generation artifact:** the tree keys on `n_pages`/doc-composition — the packet-structure/damage-quantity the repo marked **off-limits** ("measured cause of death of the b13_census prize", `BACKGROUND.md:169`); won't generalize, spec-questionable.
- **Only strong separator = the parked hidden-key inverse-verdict** (188/188, row 79), spec-tension (deciding on hidden adversarial text).
- **Verdict:** the 15-pt classification gap is genuinely locked — no legitimate, generalizable, CFA-safe split from visible evidence. Confirms "needs a split" really means "needs evidence that isn't in the trusted channel." Independently re-derives the repo's ceiling from the split angle.

## Suite conclusion
Every layer is at its evidence-limited ceiling: recognition (P1–P4) thin/dead, decision-driving (P7) dangerous (−4.6/16 CFAs), the split (P8) locked behind generation-artifacts or the spec-fraught inverse-verdict. **The one safe, real, holdout-validated win is P6: a probabilistic P(correct) confidence layer, +0.74 calibration pts — describe, don't act.** Recommended graduation: P6.
