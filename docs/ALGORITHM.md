# Algorithm: how the pipeline works right now

_Last updated: 2026-07-25. Rewrite this file in place when the algorithm changes; do not append._

The reference for **what the system actually does**, stage by stage, with the real constants. Sister docs answer the other questions: [STATUS.md](STATUS.md) — where we are and what we tried; [experiments.md](experiments.md) — one scored row per change; [BACKGROUND.md](BACKGROUND.md) — the evidence behind the constants (organizer rulings, label mining, fraud-signal taxonomy, scan geometry, competitor intel, signal-space verdicts); [FIELDS.md](FIELDS.md) — the same evidence indexed per schema field; the challenge's own [EVALUATION.md](../../mib-doc-challenge/EVALUATION.md) — what the evaluator rewards. [CLAUDE.md](../CLAUDE.md) briefs a newcomer on the problem.

Keep this honest against the code. When a constant or branch here disagrees with `mib/`, the code wins and this file is stale — fix it.

---

## Shape of the system

One JSONL row per PDF: 12 schema fields + an adjudication + a calibrated confidence. The work is a five-stage pipeline with a deliberate **cache boundary** after OCR, so everything downstream of pixels replays from a page-text cache in seconds.

```
S1 extract   PDF        -> Page[]        pure, cheap
S2 render    Page       -> Read[]        IMPURE, EXPENSIVE   <- cache boundary
S3 parse     Read[]     -> kv / Candidate[]   pure, cheap
S4 assemble  Candidate[]-> Packet+values pure, cheap
S5 decide    values+sig -> Decision      pure, cheap         <- rules <-> learned swap
   emit      Decision   -> schema row    pure, cheap
```

The seam records live in `mib/records.py`; each is **plural on purpose** (many reads per page, every candidate per field, every rule predicate per case) so no improvement is foreclosed by a value being thrown away early. Orchestration is `mib/runner.py` (`read_case` = S1+S2, `predict_from_evidence` = S3→emit); the CLI is `solution.py`.

**Shipped configuration today:** the rules cascade (the only decider — the learned one was deleted after decision-layer ML was closed, see S5), the full restoration ladder (fixed in code), exhaustive OCR — every geometric variant is read and the best kept, with no early stop to switch off.

---

## S1 — extract (`mib/stages/extract.py`)

PyMuPDF `get_text("dict")`, classifying every **span** as visible or hidden. This is the injection trust boundary: the corpus contains no legitimate hidden text, so a span is **hidden** (quarantined — never reaches extraction or policy) if *any* of:

- colour whiteish (`r,g,b > 240`), or
- font `size < 2.0`, or
- its bbox does not intersect the page crop.

A page `is_scan_only` when `image_count > 0 and len(visible_lines) <= 3` — its content is pixels, not text, so it needs S2. Hidden lines are retained separately for the injection differential tests only.

S1 also records **struck** value cells: text a red strikethrough crosses out in the vector layer (`get_drawings()` — a thin, horizontal, reddish line over the span). This is the document voiding its own printed value (a fee receipt's `unpaid`, an intake's decoy sponsor/visa), which the text layer still reads — so, like hidden text, a struck value is not sourceable evidence and S4 drops it. Only text-layer strikes are visible here; scanned red-*pixel* strikes are not (OCR is grayscale).

## S2 — render / OCR (`mib/stages/render.py` + `mib/imaging.py`)

Runs only on scan-only pages — 47% of train pages; 85% of packets carry at least one (only 149 of 1,000 train packets are fully text-layer). Pixel sources, cheapest first: the embedded raster if `width >= 1000`, then a re-render at `max(200 DPI, native embedded DPI)` capped at ~300 (row 37's floor — a private-set 300-DPI scan is never downsampled). Restoration variants feed tesseract as PNM straight from memory (row 40, S2 overhead −42%; the embedded original's encoded bytes are still written untouched, and the render base declares `--dpi 96` because the old PNGs' metadata chunk was load-bearing for segmentation). Engine is **Tesseract PSM 11** (sparse text), `OMP_THREAD_LIMIT=1`, 20 s per-call timeout — one invocation runs BOTH the `txt` and `tsv` renderers (row 41): text stays byte-identical to the old stdout pass, and the tsv rides along at ~1× cost yielding per-line `conf` tuples `(mean word conf, n_words, y_frac, line text)` (schema 4, row 44).

Each reading carries two scores. `evidence_score` (labels + well-formed values) survives as tooling's hard-page cut and the optical gate; **selection is by `conf`** (row 42, default since row 43): `records.conf_excess_mass` = Σ max(0, conf−40)·words over non-footer lines (y_frac < 0.90 — page furniture OCRs at conf 90+ and would bias any mass metric). The ev pathology this replaced: a well-formed misread (`MIB-000000`) outscored honest partial reads; engine conf rates it as junk. `MIB_SELECT=ev` remains for A/Bs until Phase E deletes it.

**Optical rung** (default ON since row 48): pages whose geometric ensemble reads below `GOOD_ENOUGH` also get local-adaptive-threshold and autocontrast variants per source. Its ev-era killer (binarized well-formed garbage displacing correct reads, 11/10 unguarded) does not transfer to conf selection (hard-set A/B 1 better / 0 worse). Caveat learned from the 2026-07-26 gallery: `GOOD_ENOUGH` counts labels, so sparse-but-clean documents (a pristine 3-line fee receipt) also trip the gate — harmless for optical (extra variants on a readable page) but wrong as a "dead page" definition (TODO 1.6).

**Geometric restoration cascade** (fixed in code, not selectable — every rung runs on every weak page). Damaged scans are *transformed*, not low-resolution — undoing the geometry is what recovers text (a 300-DPI retry bought +0.21 for 43× runtime and was reverted):

| level | what it does |
| --- | --- |
| `skew` | argmax projection-profile sharpness over ±8° in 0.25° steps, via **shear + bincount** on the downsampled ink mask (~ms); rotate only if `|angle| >= 0.5°` |
| `turn` | try 90° / 270° when nothing has read yet (180° never wins) |
| `bands` | "shredder" realign — read per-row shift off the printed page border, roll each band back to the common left margin |

Every variant is OCR'd and **every reading crosses the seam** (rows 30, `4afeb58`): `read_case` returns the whole ensemble per page, the cache stores it (with each reading's `evidence_score` and `conf`, so replay-time selection is pure), and the winner is chosen downstream — `records.best_read` picks the page's *primary* reading (defines the document; by conf metric when the read has conf, by `quality` for pre-conf caches), while losing readings survive as `Packet.variant_docs` for the per-field vote, the flag scan, and the adjudicator-finding fallback. An earlier design stopped OCR once `evidence_score >= GOOD_ENOUGH (6)`; it measured **−0.21 dev** — it settled for the first good-enough variant and spent the most OCR on the hardest pages (docs/experiments.md row 16) — and has been removed, so there is no longer a switch for it. Cost is bounded by `runner.CASE_OCR_BUDGET_S`, not by skipping variants. `skew_sweep` is exposed so `scripts/visualize_restore.py` plots the exact curve.

> **Hazard:** S2 page-quality scoring calls `parse.key_for`, so a `KEY_MAP` edit can change which OCR variant wins and silently invalidate the page-text cache. S2 is therefore *not* a pure function of (image, render config). Guard S3-touching changes with `scripts/verify_render.py`.

## S3 — parse (`mib/parse.py`)

- **Doc typing** (`detect_doc_type`): exact header markers first (`FORM I-8090`, `FORM B-13`, `Sponsor Attestation Letter`, `Planetary Registry Extract`, `Manual Adjudicator Note`, `MIB Fee Receipt`), then fuzzy tokens for OCR-mangled headers. Trust ranks (low = more trusted): adjudicator 1 < intake 2 < biometric 3 < sponsor 4 < registry 5 < fee 6 < other 9.
- **`parse_kv`**: `Key[:.;] Value` (tolerant separator — OCR reads `:` as `.`, which once caused a false approval). Label resolution: exact `KEY_MAP`, then fuzzy `cutoff=0.8`; a **loose** match (`cutoff=0.55`) is accepted only when the value corroborates via `valid_value`. Also handles the `Key` line / `Value` line pair form, and (row 50) the **separator lost entirely** (`Fee Status waved`, `Apphcant Onvoss Mesh`): a label head claims the rest of the line only under `_plausible_value` corroboration (closed fields validate or snap deny-safely; names must fit the census shape — 979/979 truth names are 2–3 capitalized alpha tokens; purposes must land in the closed 10-value set) AND label-line shape (head fuzzy-matches a label of the SAME token count, value ≤4 tokens — without the shape guard, `Sponsor SPN-#### attests…` prose was claimed as a sponsor id and 30+ cases fell to decoy bait ids).
- **`parse_prose`**: four regexes anchored on sponsor-attestation sentence wording (`Sponsor SPN-#### attests…`, `…is expected on Earth for <purpose>`, `class XW-2 compliance`), matched on *joined* text so a wrapped sentence survives. Fills only fields the labelled lines didn't (273 of 312 dev parse failures were unparsed prose).
- **`valid_value`**: per-field shape gates; rejects `unknown`/`n/a` and in-place damage markers (`[NAME CUT OUT]`, `[REGISTRY LOST]`, …). `arrival_date` is shape- **and** calendar-checked (`date.fromisoformat` rejects well-shaped impossibles like `2026-03-41`; hardened in `b926403`).

## S4 — assemble + merge (`mib/packet.py`, `mib/signals.py`)

**`assemble`**:
- **case_id** by majority vote across all pages (visible + OCR lines); pages naming a *different* case_id are dropped as decoys for another applicant — with one tolerance (row 25): an *OCR* page whose only id is one glyph off the active case is kept (the applicant's own page misread, not a decoy); text-layer pages get exact match only.
- On OCR'd pages, `_repair_ocr_kv` snaps values toward closed vocabularies (`vocab.snap`) and **deletes** hopeless id/enum reads — including `observed_flags` — so absent beats wrong.
- `_void_struck` drops any field whose value the page **struck through** (S1's `struck` set): the document crossed out its own value, so the true value must come from another document, else the field degrades to unknown. Match is normalized equality (never substring: a struck `unpaid` must not void a `paid`). `_raw` is untouched, so flag scanning and manual corrections still read (row 34, +0.20 dev, CFA 0; the deterministic fee-decoy negation — struck ⟺ printed value ≠ truth).
- Documents are stored `(doc_type, source)`-sorted, where `source` is text (0) < OCR (1).

**`merge_fields`**: `candidates()` materializes *every* value seen for every field, sorted by `_preference = (source, doc_type)` — a clean text-layer read beats an OCR'd one, ties settled by field-manual trust order — and the first valid candidate wins (this per-field preference is worth +0.23 dev; it stops a correct `Miravoss` losing to a garbled `Mirayoss`). Then **manual corrections** (`Manual correction: <field> is <value>`, the signed-note tier) override everything at rank 0. Finally the **variant vote** (row 30, +0.74 dev with the flag union; overhauled rows 45/47/49): any field whose winner was OCR-sourced (or missing) is settled by plurality across the whole OCR ensemble — losing variants included. Vote keys **merge instead of tying**: edge punctuation is stripped per token (`Zazam_`/`Zazam.`/`Zazam` pool their votes), and for applicant_name the stroke-collapse pairs `rn`≡`m` (any position) / `ri`≡`n` (token-final) merge with the expanded form as representative — ablation-proven load-bearing (row 49: with the merge off, per-line conf alone regresses 7 names / recovers 0, because tesseract is MORE confident on the merged glyph). Values with an inner colon are parse errors, not votes (schema census: 0 of 12,000 truth values contain one). Genuine ties break by per-line engine conf (`_line_conf`, matched into the schema-4 tsv line text), then first-seen. Clean text-layer and manual-note values are never outvoted; vote-settled fields carry provenance doc_type `VOTE_DOC` (99). Known open weakness: cross-page family balance (a decoy page with more restoration variants gets more votes) — truncation pooling and doc-type preference were both measured against it and rejected (row 47).

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

16 ordered branches, first match wins. Named so confidence is calibrated per-branch and eval residuals attribute to the rule that fired.

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
| 16 | `clean_approve` | APPROVED | everything above passed |

Deny rules require **positive** evidence: an *unknown* visa never arms a non-DIP-only denial (`known_non_dip = visa is not None and visa != "DIP-1"`), which stopped 3 over-denials of true DIP-1 packets whose visa failed to extract. `b13_census` distinguishing "flags: none" from "flags: unreadable" is the guard that removed the MIB-000672 false approval.

Order is severity-monotone: every positive-evidence deny branch precedes every ignorance-review branch (row 39 fixed the one historical inversion — `stale_arrival` now outranks `fee_unknown`, because the truth-side cell "fee genuinely unknown ∧ stale ∧ non-DIP" is empty across all 1,000 train labels (thin evidence alone — ~1.6 expected under independence — but all 8 pipeline cases landing there carry a polluted fee read, 7 truth DENIED, and the positively-evidenced stale date should win; NR→DENIED is also the CFA-safe direction). The co-fire audit (`experiments/cofire_probe.py`) showed order is decision-relevant *only* there: all other deny-vs-review overlaps already resolve deny-first by construction, and same-decision overlaps affect calibration/attribution only.

Key constants (train-validated, see [BACKGROUND.md](BACKGROUND.md) §2):
- `STALE_CUTOFF = 2026-01-02` — midpoint of the empty 48-day band between the latest stale-denied arrival (2025-12-09) and the earliest fresh **non-DIP** arrival (2026-01-26); max-margin, since no visible receipt date exists in the corpus.
- Full-embargo `{TRAPPIST-1e, Eris Relay}` (denial via the `embargo_world` branch — no flag is inferred); partial-embargo `{Wolf-1061c}` (non-DIP only).
- Revoked sponsors `{SPN-0007, SPN-0139, SPN-4040}` (manual) + `{SPN-2718, SPN-7331, SPN-9090}` (inferred: 11–14 non-DIP occurrences each, zero approvals).

### Historical: the learned decider (deleted)

A calibrated-logistic decider (66 features, EV-argmax over the real scoring matrix) ran beside the rules cascade as a sidecar A/B. Its edge inverted on the stronger substrate (−0.50 class, 14 CFAs vs rules' 0), decision-layer ML was closed, and the code (`mib/{decision,features}.py`, `decision_model.npz`, `scripts/{train,export}_decision.py`, the `MIB_DECIDER`/`MIB_CFA_VETO` knobs) was deleted — git history only. Full story: STATUS rejected list, experiments rows 18/27.

## Confidence + emit (`mib/confidence.py`, `mib/emit.py`)

- Per-branch table fitted from dev empirical accuracy (Laplace k=10, clamped to [0.05, 0.95], `mib/confidence_table.json`, via `scripts/fit_confidence.py`); hand-set fallback if the table is absent. Never a constant — the calibration section scores `20·max(0, 1 − 2·mean_brier)`. `confidence_table.meta.json` stamps the substrate it was fitted on (currently the shipping keystone+struck cache, rows 31/34); refit after any change that moves branch membership.
- **`emit.validate`** is the schema safety net (the evaluator hard-fails on bad enums / confidence / duplicate or malformed ids): clamps enums and confidence, repairs `case_id` (last resort `MIB-000000`, a valid shape that matches no case — strictly better than a fatal malformed id), shape-checks `arrival_date` (coerces to `1900-01-01` on shape failure). A failed case emits a `NEEDS_REVIEW` fallback row rather than nothing (worth up to 8 raw pts, dodges the missing penalty).

## Orchestration (`solution.py`)

`multiprocessing.Pool(4)` (spawn start method pinned for dev/container parity) over `sorted(*.pdf)`. `imap` streams rows **in input order, flushing each line**, so a container stopped at the 30,000 s limit is scored on real partial output rather than an empty file. Duplicate case ids are dropped with a stderr warning. `MIB_DEBUG_JSONL` writes the per-case diagnostic sidecar (branch, provenance, census) beside a config-stamped `meta.json`; predictions stay schema-clean.

### S6 — corpus revision (`mib/corpus.py`)

The one stage that is not per-case, and it runs last for that reason: a sponsor id can only be recognized as a *policy entity* rather than case data by how often it recurs across the input directory, which is unknown until every case has been read.

`recurring_sponsors` counts extracted sponsor ids, splits the occurrence spectrum at its largest **ratio** gap, and returns the ids above it — but only if the split is defensible: the gap must clear 3×, the flagged set must stay under 5% of distinct ids, and the corpus must hold at least 50 cases. Otherwise it abstains and the published + validated `vocab.REVOKED_SPONSORS` list stands alone. On train it recovers exactly the six known revoked ids, so it changes nothing; its value is coverage of ids that exist only in the private set (see `experiments.md` row 23 for the ablation that measures it).

`revise` then applies the *same* rule policy applies to a known revoked sponsor — non-DIP visas only, higher-precedence branches (`adjudicator_finding`, `disqualifying_flag`, `embargo_world`, `embargo_world_partial`) left untouched — so it can only ever tighten a decision toward DENIED.

Deliberately a **rewrite of already-written rows**, via a sibling temp file and `os.replace`, never a barrier before the first write: the streaming above exists precisely so a killed container is scored on partial output, and holding everything back until the corpus was complete would trade that away. Killed early, the file is exactly what the per-case pipeline produced. A failure inside the revision is caught and logged, keeping the provisional rows.

## Environment knobs

| var | default | effect |
| --- | --- | --- |
| `MIB_OCR_PASSES` | `psm11` | `dual` adds a PSM 3 pass per image (experiments.md row 20 — +0.87 dev, unshipped for cost) |
| `MIB_OCR_OPTICAL` | **on** (row 48) | `off` disables the faint-scan variants (local-adaptive threshold + autocontrast, `mib/imaging.py`), gated on weak geometric reads; critical join key |
| `MIB_SELECT` | `conf` (row 43) | `ev` ranks primaries by `evidence_score` instead of guarded excess conf mass, for A/Bs until Phase E deletes ev |
| `MIB_WORKERS` | 4 | local wall-clock knob only (`config.workers()` — the contract's vCPU count is the default). Output is byte-identical at any worker count, so it is deliberately not stamped |
| `MIB_DEBUG_JSONL` | unset | path for the per-case diagnostic sidecar |

The per-case OCR wall-clock bound (120 s) is a constant (`runner.CASE_OCR_BUDGET_S`), not a knob; experiments override it per-call via `read_case(pdf, budget_s=...)`.
