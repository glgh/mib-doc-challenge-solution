# Algorithm: how the pipeline works right now

_Last updated: 2026-07-26. Rewrite this file in place when the algorithm changes; do not append._

The reference for **what the system actually does**, stage by stage, with the real constants. Sister docs answer the other questions: [STATUS.md](STATUS.md) — where we are and what we tried; [experiments.md](experiments.md) — one scored row per change; [BACKGROUND.md](BACKGROUND.md) — the evidence behind the constants (organizer rulings, label mining, fraud-signal taxonomy, scan geometry, competitor intel, signal-space verdicts); [FIELDS.md](FIELDS.md) — the same evidence indexed per schema field; the challenge's own [EVALUATION.md](../../mib-doc-challenge/EVALUATION.md) — what the evaluator rewards. [CLAUDE.md](../CLAUDE.md) briefs a newcomer on the problem.

Keep this honest against the code. When a constant or branch here disagrees with `mib/`, the code wins and this file is stale — fix it.

---

## Shape of the system

One JSONL row per PDF: the 12 schema fields (adjudication and calibrated confidence among them). The work is a five-stage pipeline with a deliberate **cache boundary** after OCR, so everything downstream of pixels replays from a page-text cache in seconds.

```
S1 extract   PDF        -> Page[]        pure, cheap
S2 render    Page       -> Read[]        IMPURE, EXPENSIVE   <- cache boundary
S3 parse     Read[]     -> kv / Candidate[]   pure, cheap
S4 assemble  Candidate[]-> Packet+values pure, cheap
S5 decide    values+sig -> Decision      pure, cheap         <- rules <-> learned swap
   emit      Decision   -> schema row    pure, cheap
```

The seam records live in `mib/records.py`; each is **plural on purpose** (many reads per page, every candidate per field) so no improvement is foreclosed by a value being thrown away early; S5's input is the named-signal dict from `signals.derive`, not a record type. Orchestration is `mib/runner.py` (`read_case` = S1+S2, `predict_from_evidence` = S3→emit); the CLI is `solution.py`.

**Shipped configuration today:** the rules cascade (the only decider — the learned one was deleted after decision-layer ML was closed, see S5), the S2 composition grid (row 60; the pre-grid ladder enumerator was deleted outright in the de-special-casing batch, 2026-07-26 — git history holds it), exhaustive OCR — every geometric variant is read and the best kept, with no early stop to switch off.

---

## S1 — extract (`mib/stages/extract.py`)

PyMuPDF `get_text("dict")`, classifying every **span** as visible or hidden. This is the injection trust boundary: the corpus contains no legitimate hidden text, so a span is **hidden** (quarantined — never reaches extraction or policy) if *any* of:

- colour whiteish (`r,g,b > 240`), or
- font `size < 2.0`, or
- its bbox does not intersect the page crop.

A page `is_scan_only` when `image_count > 0 and len(visible_lines) <= 3` — its content is pixels, not text, so it needs S2. Hidden lines are retained separately for the injection differential tests only.

S1 also records **struck** value cells: text a red strikethrough crosses out in the vector layer (`get_drawings()` — a thin, horizontal, reddish line over the span). This is the document voiding its own printed value (a fee receipt's `unpaid`, an intake's decoy sponsor/visa), which the text layer still reads — so, like hidden text, a struck value is not sourceable evidence and S4 drops it. Only text-layer strikes are visible here; scanned red-*pixel* strikes are not (OCR is grayscale).

## S2 — render / OCR (`mib/stages/render.py` + `mib/imaging.py`)

Runs only on scan-only pages — 47% of train pages; 85% of packets carry at least one (only 149 of 1,000 train packets are fully text-layer). Pixel sources, cheapest first: the embedded raster if `width >= 1000`, then a re-render at `max(200 DPI, native embedded DPI)` capped at ~300 (row 37's floor — a private-set 300-DPI scan is never downsampled). Restoration variants feed tesseract as PNM straight from memory (row 40, S2 overhead −42%; the embedded original's encoded bytes are still written untouched; the render base declares its real DPI — the 96-DPI pHYs accident died with the ladder). Engine is **Tesseract PSM 11** (sparse text), `OMP_THREAD_LIMIT=1`, 20 s per-call timeout — one invocation runs BOTH the `txt` and `tsv` renderers (row 41): text stays byte-identical to the old stdout pass, and the tsv rides along at ~1× cost yielding per-line `conf` tuples `(mean word conf, n_words, y_frac, line text)` (schema 4, row 44).

**Selection is by `conf`** (row 42, default since row 43, the only selector since the de-special-casing batch): `records.conf_excess_mass` = Σ max(0, conf−40)·words over non-footer lines (y_frac < 0.90 — page furniture OCRs at conf 90+ and would bias any mass metric). The ev pathology this replaced: a well-formed misread (`MIB-000000`) outscored honest partial reads; engine conf rates it as junk. The hand-built `evidence_score` (labels + well-formed values) is deleted with the ladder; old caches carry it in `quality`, which conf-less reads still fall back to. Tooling's hard-page cut is `page_score` now.

**The composition grid** (rows 58–60, the only enumerator — the frozen `ladder` legacy proved the refactor byte-identical against pre-grid caches and was then deleted). Variants are chains in one canonical order, `source → orientation → deskew → deshred/local → optical`, with the correction detectors re-run INSIDE each orientation frame (`skew_angle` → 0.0 and `realign_bands` → None self-gate there), so a turned+shredded page gets the band fix in the right frame — the flat ladder ran deshred only at 0°. Names are honest chains (`render+turn3+skew+deshred+adapt`); pixel-hash dedupe keeps no-op compositions from paying an OCR pass; the grid's render base declares its REAL DPI (the 96 accident lost the row-40 deferred experiment 19–37 and lives only in the ladder path).

- **Base tier, unconditional:** raw sources + every orientation's in-frame correction chain (0°, turn1, turn3 — 180° never occurs). Turn-gating on page-level weakness was designed and offline-killed the same day: MIB-000509-class pages clear the bar on their raw read while the TURN read carries the field block. The floor is absolute in the shipped config — no default-on knob prunes the base; only the A/B-only, restore-stamped `MIB_GEOM_SET` override can (and the early stop that truncated coverage measured −0.21, row 16).
- **Expansion tier, weak pages only** (`page_score < WEAK_BAR` — a FROZEN render-owned lexicon with min-2-words + watermark guards, so no parser edit and no boilerplate mass can move the gate): the optical modules (`adapt` local-threshold, `autocon`, behind the default-on `MIB_OCR_OPTICAL`) composed over the corrected frames, not just raw gray — this is what reads MIB-000061's faint+tilted fee line (`skew+deshred+adapt` → `Fee Status waved` → sepless + snap → `waived` = truth). Optical's ev-era killer (11/10 unguarded) does not transfer to conf selection (row 48).
- **Layout-pass tier, default-on** (`MIB_LAYOUT_PASS=psm3`): one PSM-3 (full-layout) pass on the best frame of a page whose field label is present but its value truncated/absent across all reads (`extraction_gaps.truncated`, e.g. `Home World: Tit`) — row 67's image-box class, where PSM 11's sparse-text grouping mis-segments around large graphic boxes. The weak/faint class is deliberately NOT a trigger (PSM-3 is null on it, row 76); the census (TODO 6.7) priced the weak/furniture arms as over-fire. Shipped 2026-07-27: +0.01 dev, CFA 0, ~+7% p50 OCR cost.

The underlying transforms are unchanged from the ladder era: `skew` = argmax projection-profile sharpness over ±8° in 0.25° steps via shear + bincount (~ms), applied at `|angle| >= 0.5°`; `deshred` = per-row shift off the printed border, bands rolled back to the common left margin; `local` = the text-consent corrector (row 51), emitted only when its pixels differ from deshred. `imaging.orientation_profile` supplies per-frame deskew angles plus a rule-stripped glyph-projection sharpness hint (12/14 on the labeled registry; the raw sweep scored 6/14 because form rules INVERT the signal) — hints exist for future gated consumers and never decide membership.

Every variant is OCR'd and **every reading crosses the seam** (row 30, `4afeb58`): `read_case` returns the whole ensemble per page, the cache stores it (with each reading's `conf`, so replay-time selection is pure), and the winner is chosen downstream — `records.best_read` picks the page's *primary* reading (defines the document; by conf metric when the read has conf, by the stored `quality` for pre-conf caches), while losing readings survive as `Packet.variant_docs` for the per-field vote, the flag scan, and the adjudicator-finding fallback. An earlier design stopped OCR once the page looked good enough; it measured **−0.21 dev** — it settled for the first passable variant and spent the most OCR on the hardest pages (docs/experiments.md row 16) — and has been removed, so there is no longer a switch for it. Cost is bounded by `runner.CASE_OCR_BUDGET_S`, not by skipping variants. `skew_sweep` is exposed so `scripts/visualize_restore.py` plots the exact curve.

> **Hazard (fully discharged):** the deleted `evidence_score` called `parse.key_for`, so a `KEY_MAP` edit could change which variant won and silently invalidate the cache. The gate is the frozen `page_score` and selection is conf — S2 is a pure function of (image, config) again, and the last code path carrying the coupling died with the ladder.

## S3 — parse (`mib/parse.py`)

- **Doc typing** (`detect_doc_type`): three tiers over the first `HEADER_WINDOW`=6 non-empty lines (skipping deskew debris that pushes a title past `lines[:4]`) — exact header markers first (`FORM I-8090`, `FORM B-13`, `Sponsor Attestation Letter`, `Planetary Registry Extract`, `Manual Adjudicator Note`, `MIB Fee Receipt`), then short fuzzy tokens (`_FUZZY_HEADERS`), then a windowed difflib match against the full canonical titles (`_FUZZY_TITLES`, cutoff `HEADER_FUZZY_CUTOFF`=0.66) — the digit code garbles first but the descriptive tail survives, so the full title is both more OCR-robust and more discriminative; under-detection only (`OTHER`→known, never one known type→another). Trust ranks (low = more trusted): adjudicator 1 < intake 2 < biometric 3 < sponsor 4 < registry 5 < fee 6 < other 9.
- **`parse_kv`**: `Key[:.;] Value` (tolerant separator — OCR reads `:` as `.`, which once caused a false approval). Label resolution: exact `KEY_MAP`, then fuzzy `cutoff=0.8`; a **loose** match (`cutoff=0.55`) is accepted only when the value corroborates via `valid_value`. Also handles the `Key` line / `Value` line pair form, and (row 50) the **separator lost entirely** (`Fee Status waved`, `Apphcant Onvoss Mesh`): a label head claims the rest of the line only under `_plausible_value` corroboration (closed fields validate or snap deny-safely; names must fit the census shape — 979/979 distinct truth names are 2–3 capitalized alpha tokens (rows 62/64's sharper census: exactly two tokens, each ≥4 chars); purposes must land in the closed 10-value set) AND label-line shape (head fuzzy-matches a label of the SAME token count, value ≤4 tokens — without the shape guard, `Sponsor SPN-#### attests…` prose was claimed as a sponsor id and 30+ cases fell to decoy bait ids).
- **`parse_prose`**: four regexes anchored on sponsor-attestation sentence wording (`Sponsor SPN-#### attests…`, `…is expected on Earth for <purpose>`, `class XW-2 compliance`), matched on *joined* text so a wrapped sentence survives. Fills only fields the labelled lines didn't (273 of 312 dev parse failures were unparsed prose).
- **`registry_fallback_kv`** (row 32, OCR registry pages only): the registry extract's two-line labels erode on faint scans, fusing label tails onto values (`World Ens Relay`) or leaving bare values; canonical-label-tail regexes + vocab snap (cutoff 0.7) + bare-TitleCase-pair name capture recover what `parse_kv` lost (hooked in `packet.assemble`).
- **`valid_value`**: per-field shape gates; rejects `unknown`/`n/a` and in-place damage markers (`[NAME CUT OUT]`, `[REGISTRY LOST]`, …). `arrival_date` is shape- **and** calendar-checked (`date.fromisoformat` rejects well-shaped impossibles like `2026-03-41`; hardened in `b926403`).

## S4 — assemble + merge (`mib/packet.py`, `mib/signals.py`)

**`assemble`**:
- **case_id** by majority vote across all pages (visible + OCR lines); pages naming a *different* case_id are dropped as decoys for another applicant — with one tolerance (row 25): an *OCR* page whose only id is one glyph off the active case is kept (the applicant's own page misread, not a decoy); text-layer pages get exact match only.
- On OCR'd pages, `_repair_ocr_kv` snaps values toward closed vocabularies (`vocab.snap`) and **deletes** hopeless id/enum reads — including `observed_flags` — so absent beats wrong.
- `_void_struck` drops any field whose value the page **struck through** (S1's `struck` set): the document crossed out its own value, so the true value must come from another document, else the field degrades to unknown. Match is normalized equality (never substring: a struck `unpaid` must not void a `paid`). `_raw` is untouched, so flag scanning and manual corrections still read (row 34, +0.20 dev, CFA 0; the deterministic fee-decoy negation — struck ⟺ printed value ≠ truth).
- Documents are stored `(doc_type, source)`-sorted, where `source` is text (0) < OCR (1).

**`merge_fields`**: `candidates()` materializes *every* value seen for every field, sorted by `_preference = (source, doc_type)` — a clean text-layer read beats an OCR'd one, ties settled by field-manual trust order — and the first valid candidate wins (this per-field preference carries 24 dev fields vs 1 loss on the grid substrate — the row-66 structure audit; the historical anchor was a correct `Miravoss` losing to a garbled `Mirayoss`, row 15b's 2-case evidence). Then the **variant vote** (row 30, +0.74 dev with the flag union; overhauled rows 45/47/49): any field whose winner was OCR-sourced (or missing) is settled by plurality across the whole OCR ensemble — losing variants included. Vote keys **merge instead of tying**: edge punctuation is stripped per token (`Zazam_`/`Zazam.`/`Zazam` pool their votes), and for applicant_name the stroke-collapse pairs `rn`≡`m` (any position) / `ri`≡`n` (token-final) merge with the expanded form as representative — ablation-proven load-bearing (row 49: with the merge off, per-line conf alone regresses 7 names / recovers 0, because tesseract is MORE confident on the merged glyph). Values with an inner colon are parse errors, not votes (schema census: 0 of 12,000 truth values contain one). Groups are ranked snappable-first on closed-vocab fields (row 60: the value universe is closed, so an unsnappable group is never the truth — MIB-000134's cross-page truncation `fiel` vs `field repair`), then by distinct contributing pages before raw read count (row 58, the decoy fan-out guard); both grid-era keys measured zero-effect on the current substrate (row 66) and are retained as adversarial insurance. Genuine ties break by per-line engine conf (`_line_conf`, matched into the schema-4 tsv line text), then first-seen. Clean text-layer and manual-note values are never outvoted; vote-settled fields carry provenance doc_type `VOTE_DOC` (99). Known open weakness: cross-page family balance (a decoy page with more restoration variants gets more votes) — truncation pooling and doc-type preference were both measured against it and rejected (row 47). After the vote, applicant_name faces the **corroboration challenge** (`_name_corroboration`, row 63): adversarial packets poison the NAME CELL of an otherwise-truthful text-layer form — row 67 falsified the earlier multi-applicant-decoy reading (0/13 fake names own any other case; the poisoned form's other fields match the active truth 6/6, so the poisoning is field-grain, not document-grain, and document-level demotion would break ~6 correct fields per case) — so a name asserted by **≥2 distinct documents and strictly more than the incumbent's** deposes the single-document winner (counterfactual-first: FIXED 6/BROKE 0; every weaker clause — OCR breadth, one doc + dominant vote — measured net-negative; name-only replacement is the proven-correct grain). The poisoned page is a pristine digital form (portrait + seal images, no ink text — ink-consent refuted, row 68), and the generator also plants the REVERSE (genuine foreign scanned documents, 930/402), so 1v1 name conflicts are structurally ambiguous and the ≥2-doc bar is the decidability frontier. **Manual corrections** (`Manual correction: <field> is <value>`, the signed-note tier) apply LAST and override doc merge, vote, and challenge alike. Validity itself is census-tightened (rows 62/64): arrival_date years outside 2020–2030 are OCR garble, not dates (`2928` = `2026` under 9/0 confusion), and applicant_name must fit the total truth shape — exactly two alphabetic tokens, each ≥4 chars, watermark furniture tokens (`COPY`, `SPECIMEN`, …) rejected.

**Display-only fee fallback** (`fallbacks.fee_fallback`, rows 54/61): for packets whose fee never parsed, the runner — AFTER `policy.adjudicate` has seen the merged value, so no fallback tier can ever approve a case — imputes unpaid-phrase (7/7) > DIP-WAIVER value (7/7) > **explicit-unknown statement** > `paid` (the 69% silence base rate). A statement that the fee state is unknown (inline `Fee Status: unknown`, split-line receipt, prose reason/manual note, `[FEE STATUS OBSCURED]` marker incl. truncations — all fuzzy-matched, injection-guarded) is evidence, not silence; fuzz toward paid/waived/unpaid is forbidden — a legible receipt value that contradicts truth is a struck, superseded printing (MIB-000514's `unpaid` on a truth-APPROVED paid case; row 66 corrected the earlier "planted trap" framing to the row-34 lifecycle class), and raw-line fuzzy recovery would resurrect exactly what the strike-void deleted. The fallback tiers themselves never feed policy; adjudication sees only the merged evidence value (a cleanly parsed, unstruck receipt value merges like any candidate).

**Display-only closed-vocab fallback** (`fallbacks.closed_vocab_fallback`, row 65): the same contract one field-family over — after adjudication, species/world/purpose fields the merge left empty (or non-vocabulary junk purposes) are filled by a value-first scan of every injection-guarded OCR line the merge consults: token 1–4-grams scored space-stripped under the confusion-weighted similarity, evidence pooled per vocab entry, label-corroborated pools first, floor best ≥ 0.60. Runner-hooked after `policy.adjudicate` like the fee fallback, so a filled world can never arm `planetary_embargo` and a filled field can never disarm a missing-field guard.

**`signals.derive`** turns evidence into named signals for policy:

| signal | source |
| --- | --- |
| `flags` | `observed_flags`: **every** doc and every losing OCR variant is scanned for flag lines as a union (the doc-type gate was deleted in row 35 — the per-line legend/negation/≤3-flags guards are the safety mechanism, and every flag the widened scan added was true). Token tier: `vocab.match_flag_token` per token. Value tier (row 36, OCR readings only): a loosely-labelled `Observed flags:` value the token matcher can't resolve is scored whole by `vocab.match_flag_value` (confusion-weighted), accepted single-read at score ≥0.55/margin ≥0.15, or in the 0.44–0.55 band only when ≥2 independent readings of the page argmax the same flag (bars mined from the 563-value safety table, BACKGROUND §3). Plus derived `sponsor_mismatch` and `identity_conflict` (the `planetary_embargo` inference was deleted in `068e99e` — it shadowed policy's `embargo_world` branch, and emitted flags are observed-only) |
| `finding` | `Finding: APPROVED\|DENIED\|NEEDS_REVIEW` on a Manual Adjudicator Note (highest trust). The primary read wins outright; when it lost the line, losing OCR variants of the note answer — but only unanimously (row 46: one page, one stamp; disagreeing variants = a misread, the cascade decides). Recovered 2 true APPROVED and 5 correct-NR confidences |
| `waiver_code` | first non-empty waiver code on any doc |
| `has_biometric` | a B-13 was detected |
| `has_flag_evidence` | the risk line was actually *read* — a flag (positive clause shares `observed_flags`'s widened scan, so the census can never contradict an emitted flag) or an explicit `flags: none/clear` (negative clause stays restricted to biometric-typed readings — widening it toward `clean_approve` is the CFA-risk direction and unmeasured) — not merely that a slip exists |

## S5 — adjudication (`mib/runner.py` seam)

One decider: the rules cascade. (A learned decider used to run beside it as a permanent sidecar A/B; it was deleted after decision-layer ML was closed — see the historical note at the end of this section.)

### Rules cascade (`mib/policy.py`)

17 named branches in three structural severity tiers (row 73): a signed finding passes through (tier 0), else ANY rule in `DENY_RULES` → DENIED, else ANY rule in `REVIEW_RULES` → NEEDS_REVIEW, else the injected-approval cap (row 88), else APPROVED via `clean_approve`. Each tier is a tuple of `(name, predicate)` rules; list order is attribution priority only — it names the branch that gets credit (per-branch fitted confidence, residual attribution) and cannot change the decision, so a review rule preempting a deny rule is unrepresentable. The table's # column is that attribution order.

| # | branch | decision | fires when |
| --- | --- | --- | --- |
| 1 | `adjudicator_finding` | (the finding) | a signed adjudicator finding is present |
| 2 | `disqualifying_flag` | DENIED | any of `memory_tampering\|planetary_embargo\|active_warrant\|biohazard_red` |
| 3 | `embargo_world` | DENIED | home world in `{TRAPPIST-1e, Eris Relay}` (pierces DIP immunity) |
| 4 | `embargo_world_partial` | DENIED | home world `Wolf-1061c` **and** known non-DIP |
| 5 | `revoked_sponsor` | DENIED | sponsor in revoked set **and** known non-DIP |
| 6 | `transit_visa` | DENIED | visa `TRANSIT-7` |
| 7 | `fee_unpaid` | DENIED | fee `unpaid` |
| 8 | `stale_arrival` | DENIED | arrival < `2026-01-02` **and** known non-DIP |
| 9 | `fee_unknown` | NEEDS_REVIEW | fee `unknown` |
| 10 | `waived_non_dip` | NEEDS_REVIEW | fee `waived` on a non-DIP/unknown-visa packet |
| 11 | `missing_arrival` | NEEDS_REVIEW | no arrival date |
| 12 | `review_flag` | NEEDS_REVIEW | any review-only flag |
| 13 | `missing_sponsor` | NEEDS_REVIEW | no sponsor on a non-DIP/unknown-visa packet |
| 14 | `missing_visa` | NEEDS_REVIEW | no visa class |
| 15 | `b13_census` | NEEDS_REVIEW | no B-13 **or** its flag line was unreadable |
| 16 | `injected_approval_review` | NEEDS_REVIEW | a hidden injected `APPROVED` key rides an otherwise-unforced approval (row 88; presence only, content never trusted) |
| 17 | `clean_approve` | APPROVED | everything above passed |

Deny rules require **positive** evidence: an *unknown* visa never arms a non-DIP-only denial (`known_non_dip = visa is not None and visa != "DIP-1"`), which stopped 3 over-denials of true DIP-1 packets whose visa failed to extract. `b13_census` distinguishing "flags: none" from "flags: unreadable" is the guard that removed the MIB-000672 false approval.

Severity monotonicity is structural: every positive-evidence deny rule outranks every ignorance-review rule by tier membership, not list position. Historically this was a flat ordered cascade whose one inversion — `stale_arrival` below `fee_unknown` — row 39 fixed on the evidence that the truth-side cell "fee genuinely unknown ∧ stale ∧ non-DIP" is empty across all 1,000 train labels (thin alone — ~1.6 expected under independence — but all 8 pipeline cases landing there carry a polluted fee read, 7 truth DENIED, and NR→DENIED is the CFA-safe direction); the co-fire audit (`experiments/cofire_probe.py`) then showed order was decision-relevant *only* there, which is what licensed making the tiers structural (row 73: replay byte-identical on predictions and attribution, probe mismatches 0). The audit's hand-mirrored `first_match` still asserts equivalence against `policy.adjudicate`, and every fired predicate per case lands in the debug sidecar as `deny_hits`/`review_hits` (`policy.fired`) — the per-case co-fire matrix, and the structural hook for the field manual's "multiple review-only flags may combine" hint (not implemented).

Key constants (train-validated, see [BACKGROUND.md](BACKGROUND.md) §2):
- `STALE_CUTOFF = 2026-01-02` — midpoint of the empty 48-day band between the latest stale-denied arrival (2025-12-09) and the earliest fresh **non-DIP** arrival (2026-01-26); max-margin, since no visible receipt date exists in the corpus.
- Full-embargo `{TRAPPIST-1e, Eris Relay}` (denial via the `embargo_world` branch — no flag is inferred); partial-embargo `{Wolf-1061c}` (non-DIP only).
- Revoked sponsors `{SPN-0007, SPN-0139, SPN-4040}` (manual) + `{SPN-2718, SPN-7331, SPN-9090}` (inferred: 11–14 non-DIP occurrences each, zero approvals).

### Historical: the learned decider (deleted)

A calibrated-logistic decider (66 features, EV-argmax over the real scoring matrix) ran beside the rules cascade as a sidecar A/B. Its edge inverted on the stronger substrate (−0.50 class, 14 CFAs vs rules' 0), decision-layer ML was closed, and the code (`mib/{decision,features}.py`, `decision_model.npz`, `scripts/{train,export}_decision.py`, the `MIB_DECIDER`/`MIB_CFA_VETO` knobs) was deleted — git history only. Full story: STATUS rejected list, experiments rows 18/27.

## Confidence + emit (`mib/confidence.py`, `mib/emit.py`)

- Per-branch table fitted from dev empirical accuracy (Laplace k=10, clamped to [0.05, 0.95], `mib/confidence_table.json`, via `scripts/fit_confidence.py`); hand-set fallback if the table is absent. Never a constant — the calibration section scores `20·max(0, 1 − 2·mean_brier)`. `confidence_table.meta.json` stamps the substrate it was fitted on (currently the grid substrate — `output/replay_grid_final` off `train_grid.jsonl`, the row-60 refit); refit after any change that moves branch membership.
- **`emit.validate`** is the schema safety net (the evaluator hard-fails on bad enums / confidence / duplicate or malformed ids): clamps enums and confidence, repairs `case_id` (last resort `MIB-000000`, a valid shape that matches no case — strictly better than a fatal malformed id), shape-checks `arrival_date` (coerces to `1900-01-01` on shape failure). A failed case emits a `NEEDS_REVIEW` fallback row rather than nothing (worth up to 8 raw pts, dodges the missing penalty).

## Orchestration (`solution.py`)

`multiprocessing.Pool(config.workers())` (default 4 = the contract's vCPU count; spawn start method pinned for dev/container parity) over `sorted(*.pdf)`. `imap` streams rows **in input order, flushing each line**, so a container stopped at the 30,000 s limit is scored on real partial output rather than an empty file. Duplicate case ids are dropped with a stderr warning. `MIB_DEBUG_JSONL` writes the per-case diagnostic sidecar (branch, provenance, census) beside a config-stamped `meta.json`; predictions stay schema-clean.

### S6 — corpus revision (`mib/corpus.py`)

The one stage that is not per-case, and it runs last for that reason: a sponsor id can only be recognized as a *policy entity* rather than case data by how often it recurs across the input directory, which is unknown until every case has been read.

`recurring_sponsors` counts extracted sponsor ids, splits the occurrence spectrum at its largest **ratio** gap, and returns the ids above it — but only if the split is defensible: the gap must clear 3×, the flagged set must stay under 5% of distinct ids, and the corpus must hold at least 50 cases with a usable sponsor id. Otherwise it abstains and the published + validated `vocab.REVOKED_SPONSORS` list stands alone. On train it recovers exactly the six known revoked ids, so it changes nothing; its value is coverage of ids that exist only in the private set (see `experiments.md` row 23 for the ablation that measures it).

`revise` then applies the *same* rule policy applies to a known revoked sponsor — non-DIP visas only, higher-precedence branches (`adjudicator_finding`, `disqualifying_flag`, `embargo_world`, `embargo_world_partial`) left untouched — so it can only ever tighten a decision toward DENIED.

Deliberately a **rewrite of already-written rows**, via a sibling temp file and `os.replace`, never a barrier before the first write: the streaming above exists precisely so a killed container is scored on partial output, and holding everything back until the corpus was complete would trade that away. Killed early, the file is exactly what the per-case pipeline produced. A failure inside the revision is caught and logged, keeping the provisional rows.

## Environment knobs

| var | default | effect |
| --- | --- | --- |
| `MIB_GEOM_SET` | unset | A/B override: comma-list pruning the geometry chain set (`skew,turn1,turn3,deshred,local`); stamped via the plan |
| `MIB_OPT_SET` | unset | A/B override of the optical module set (`adapt,autocon`) |
| `MIB_OPT_BASE` | `frames` | compose optical over corrected `frames` (default) or `raw` gray only |
| `MIB_LAYOUT_PASS` | `psm3` | `off` disables the layout-pass PSM-3 re-read (default-on 2026-07-27, TODO 6.7); it fires on a truncated field label (`extraction_gaps.truncated`), one call on the best frame |
| `MIB_OCR_OPTICAL` | **on** (row 48) | `off` disables the faint-scan variants (local-adaptive threshold + autocontrast, `mib/imaging.py`), gated on weak geometric reads; critical join key |
| `MIB_WORKERS` | 4 | local wall-clock knob only (`config.workers()` — the contract's vCPU count is the default). Output is byte-identical at any worker count, so it is deliberately not stamped |
| `MIB_DEBUG_JSONL` | unset | path for the per-case diagnostic sidecar |

The per-case OCR wall-clock bound (120 s) is a constant (`runner.CASE_OCR_BUDGET_S`), not a knob; experiments override it per-call via `read_case(pdf, budget_s=...)`. Three knobs were deleted in the de-special-casing batch (2026-07-26): `MIB_PLAN` (the ladder legacy enumerator is gone — the grid is the only plan), `MIB_SELECT` (conf-mass is the only selector; `evidence_score` deleted), and `MIB_OCR_PASSES` (the per-image dual PSM pass; the layout-pass tier is its revival path). All three still stamp constants (`restore`/`select`/`ocr_passes`) so existing caches join-check unchanged.
