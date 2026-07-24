# Algorithm: how the pipeline works right now

_Last updated: 2026-07-23. Rewrite this file in place when the algorithm changes; do not append._

The reference for **what the system actually does**, stage by stage, with the real
constants. Sister docs answer the other questions: [STATUS.md](STATUS.md) — where we are and
what we tried; [experiments.md](experiments.md) — one scored row per change; [BACKGROUND.md](BACKGROUND.md) —
the evidence behind the constants (organizer rulings, label mining, fraud-signal taxonomy, scan
geometry, competitor intel); the challenge's own [EVALUATION.md](../../mib-doc-challenge/EVALUATION.md) —
what the evaluator rewards. [CLAUDE.md](../CLAUDE.md) briefs a newcomer on the problem.

Keep this honest against the code. When a constant or branch here disagrees with `mib/`, the
code wins and this file is stale — fix it.

---

## Shape of the system

One JSONL row per PDF: 12 schema fields + an adjudication + a calibrated confidence. The work
is a five-stage pipeline with a deliberate **cache boundary** after OCR, so everything downstream
of pixels replays from a page-text cache in seconds.

```
S1 extract   PDF        -> Page[]        pure, cheap
S2 render    Page       -> Read[]        IMPURE, EXPENSIVE   <- cache boundary
S3 parse     Read[]     -> kv / Candidate[]   pure, cheap
S4 assemble  Candidate[]-> Packet+values pure, cheap
S5 decide    values+sig -> Decision      pure, cheap         <- rules <-> learned swap
   emit      Decision   -> schema row    pure, cheap
```

The seam records live in `mib/records.py`; each is **plural on purpose** (many reads per page,
every candidate per field, every rule predicate per case) so no improvement is foreclosed by a
value being thrown away early. Orchestration is `mib/runner.py` (`read_case` = S1+S2,
`predict_from_evidence` = S3→emit); the CLI is `solution.py`.

**Shipped configuration today:** rules decider (`MIB_DECIDER=rules`), skew restoration
(`MIB_RESTORE=skew`), exhaustive OCR (early-stop off, `MIB_EARLY_STOP=0`). The learned decider
and the `turn`/`bands` restorations are built and measured but off by default.

---

## S1 — extract (`mib/stages/extract.py`)

PyMuPDF `get_text("dict")`, classifying every **span** as visible or hidden. This is the
injection trust boundary: the corpus contains no legitimate hidden text, so a span is **hidden**
(quarantined — never reaches extraction or policy) if *any* of:

- colour whiteish (`r,g,b > 240`), or
- font `size < 2.0`, or
- its bbox does not intersect the page crop.

A page `is_scan_only` when `image_count > 0 and len(visible_lines) <= 3` — its content is pixels,
not text, so it needs S2. Hidden lines are retained separately for the injection differential
tests only.

## S2 — render / OCR (`mib/stages/render.py` + `mib/imaging.py`)

Runs only on scan-only pages (~25% of packets). Pixel sources, cheapest first: the embedded
raster if `width >= 1000`, then a 200-DPI re-render (`zoom 2.8`). Engine is **Tesseract PSM 11**
(sparse text), `OMP_THREAD_LIMIT=1`, 20 s per-call timeout.

Each reading is scored by `evidence_score` = recognizable field labels (`parse.key_for`) + count
of well-formed values (case-id / sponsor / date regex, plus visa / species / world / flag vocab
words). Values matter because the shredder clips the left margin: a rescued line reads
`mnsor ID: SPN-5809` — label gone, value intact and worth the points.

**Geometric restoration cascade** (`MIB_RESTORE`, cumulative levels `off < skew < turn < bands`,
default `skew`). Damaged scans are *transformed*, not low-resolution — undoing the geometry is
what recovers text (a 300-DPI retry bought +0.21 for 43× runtime and was reverted):

| level | what it does |
| --- | --- |
| `skew` | argmax projection-profile sharpness over ±8° in 0.25° steps, via **shear + bincount** on the downsampled ink mask (~ms); rotate only if `|angle| >= 0.5°` |
| `turn` | try 90° / 270° when nothing has read yet (180° never wins) |
| `bands` | "shredder" realign — read per-row shift off the printed page border, roll each band back to the common left margin |

By default every variant is OCR'd and `best()` keeps the highest `evidence_score` (earliest read
breaks ties). `MIB_EARLY_STOP=1` restores the old behaviour of stopping once `evidence_score >=
GOOD_ENOUGH (6)`, which measured **−0.21 dev** — it settled for the first good-enough variant and
spent the most OCR on the hardest pages (docs/experiments.md row 16). `reads_for` keeps **every**
reading. `skew_sweep` is exposed so `scripts/visualize_restore.py` plots the exact curve.

> **Hazard:** S2 page-quality scoring calls `parse.key_for`, so a `KEY_MAP` edit can change which
> OCR variant wins and silently invalidate the page-text cache. S2 is therefore *not* a pure
> function of (image, render config). Guard S3-touching changes with `scripts/verify_render.py`.

## S3 — parse (`mib/parse.py`)

- **Doc typing** (`detect_doc_type`): exact header markers first (`FORM I-8090`, `FORM B-13`,
  `Sponsor Attestation Letter`, `Planetary Registry Extract`, `Manual Adjudicator Note`,
  `MIB Fee Receipt`), then fuzzy tokens for OCR-mangled headers. Trust ranks (low = more trusted):
  adjudicator 1 < intake 2 < biometric 3 < sponsor 4 < registry 5 < fee 6 < other 9.
- **`parse_kv`**: `Key[:.;] Value` (tolerant separator — OCR reads `:` as `.`, which once caused a
  false approval). Label resolution: exact `KEY_MAP`, then fuzzy `cutoff=0.8`; a **loose** match
  (`cutoff=0.55`) is accepted only when the value corroborates via `valid_value`. Also handles the
  `Key` line / `Value` line pair form.
- **`parse_prose`**: four regexes anchored on sponsor-attestation sentence wording
  (`Sponsor SPN-#### attests…`, `…is expected on Earth for <purpose>`, `class XW-2 compliance`),
  matched on *joined* text so a wrapped sentence survives. Fills only fields the labelled lines
  didn't (273 of 312 dev parse failures were unparsed prose).
- **`valid_value`**: per-field shape gates; rejects `unknown`/`n/a` and in-place damage markers
  (`[NAME CUT OUT]`, `[REGISTRY LOST]`, …). **`arrival_date` is shape-only** (`\d{4}-\d{2}-\d{2}`),
  never calendar-checked — see Known gaps.

## S4 — assemble + merge (`mib/packet.py`, `mib/signals.py`)

**`assemble`**:
- **case_id** by majority vote across all pages (visible + OCR lines); pages naming a *different*
  case_id are dropped as decoys for another applicant.
- On OCR'd pages, `_repair_ocr_kv` snaps values toward closed vocabularies (`vocab.snap`) and
  **deletes** hopeless id/enum reads — including `observed_flags` — so absent beats wrong.
- Documents are stored `(doc_type, source)`-sorted, where `source` is text (0) < OCR (1).

**`merge_fields`**: `candidates()` materializes *every* value seen for every field, sorted by
`_preference = (source, doc_type)` — a clean text-layer read beats an OCR'd one, ties settled by
field-manual trust order — and the first valid candidate wins (this per-field preference is worth
+0.23 dev; it stops a correct `Miravoss` losing to a garbled `Mirayoss`). Then **manual
corrections** (`Manual correction: <field> is <value>`, the signed-note tier) override everything
at rank 0.

**`signals.derive`** turns evidence into named signals for policy:

| signal | source |
| --- | --- |
| `flags` | `observed_flags` (B-13 + registry status line), plus derived `sponsor_mismatch`, `identity_conflict`, and `planetary_embargo` inferred for full-embargo worlds |
| `finding` | `Finding: APPROVED\|DENIED\|NEEDS_REVIEW` on a Manual Adjudicator Note (highest trust) |
| `waiver_code` | first non-empty waiver code on any doc |
| `has_biometric` | a B-13 was detected |
| `has_flag_evidence` | the B-13's flag line was actually *read* (`observed_flags` present), not merely that a slip exists |

## S5 — the two deciders (`mib/runner.py` seam)

Both always run; the learned one is logged to the debug sidecar on every eval (a permanent A/B).
Only the one named by `MIB_DECIDER` is emitted.

### Rules cascade (`mib/policy.py`) — default

16 ordered branches, first match wins. Named so confidence is calibrated per-branch and eval
residuals attribute to the rule that fired.

| # | branch | decision | fires when |
| --- | --- | --- | --- |
| 1 | `adjudicator_finding` | (the finding) | a signed adjudicator finding is present |
| 2 | `disqualifying_flag` | DENIED | any of `memory_tampering\|planetary_embargo\|active_warrant\|biohazard_red` |
| 3 | `embargo_world` | DENIED | home world in `{TRAPPIST-1e, Eris Relay}` (pierces DIP immunity) |
| 4 | `embargo_world_partial` | DENIED | home world `Wolf-1061c` **and** known non-DIP |
| 5 | `revoked_sponsor` | DENIED | sponsor in revoked set **and** known non-DIP |
| 6 | `transit_visa` | DENIED | visa `TRANSIT-7` |
| 7 | `fee_unpaid` | DENIED | fee `unpaid` |
| 8 | `fee_unknown` | NEEDS_REVIEW | fee `unknown` |
| 9 | `stale_arrival` | DENIED | arrival < `2026-01-02` **and** known non-DIP |
| 10 | `waived_non_dip` | NEEDS_REVIEW | fee `waived` on a non-DIP/unknown-visa packet |
| 11 | `missing_arrival` | NEEDS_REVIEW | no arrival date |
| 12 | `review_flag` | NEEDS_REVIEW | any review-only flag |
| 13 | `missing_sponsor` | NEEDS_REVIEW | no sponsor on a non-DIP/unknown-visa packet |
| 14 | `missing_visa` | NEEDS_REVIEW | no visa class |
| 15 | `b13_census` | NEEDS_REVIEW | no B-13 **or** its flag line was unreadable |
| 16 | `clean_approve` | APPROVED | everything above passed |

Deny rules require **positive** evidence: an *unknown* visa never arms a non-DIP-only denial
(`known_non_dip = visa is not None and visa != "DIP-1"`), which stopped 3 over-denials of true
DIP-1 packets whose visa failed to extract. `b13_census` distinguishing "flags: none" from "flags:
unreadable" is the guard that removed the MIB-000672 false approval.

Key constants (train-validated, see [BACKGROUND.md](BACKGROUND.md) §2):
- `STALE_CUTOFF = 2026-01-02` — midpoint of the empty 48-day band between the latest stale-denied
  and earliest fresh arrival (max-margin; no visible receipt date exists in the corpus).
- Full-embargo `{TRAPPIST-1e, Eris Relay}` (also *implies* the `planetary_embargo` flag);
  partial-embargo `{Wolf-1061c}` (non-DIP only).
- Revoked sponsors `{SPN-0007, SPN-0139, SPN-4040}` (manual) + `{SPN-2718, SPN-7331, SPN-9090}`
  (inferred: 11–14 non-DIP occurrences each, zero approvals).

### Learned decider (`mib/decision.py` + `mib/features.py`) — `MIB_DECIDER=mlp`

A **66-dim** feature vector (47 without the branch/rules one-hots) over the emitted (record, debug)
pair — the trainer and runtime run literally the same `featurize`. Model is a calibrated logistic
(`StandardScaler + LogisticRegression`, `CalibratedClassifierCV cv=3 sigmoid`) trained offline;
runtime is a **numpy-only forward pass** from `mib/decision_model.npz`, averaged over the 3 CV
members. No sklearn ships. `load()` refuses a model whose feature order disagrees with
`features.names()`, so a feature edit without a retrain fails loudly.

Decision is **argmax expected points** using the real scoring matrix:

```
EV[APPROVED]     = 8·pA + 1·pN − 4·pD
EV[DENIED]       = 8·pD + 1·pN
EV[NEEDS_REVIEW] = 8·pN + 2·pA + 2·pD
```

with an optional `MIB_CFA_VETO` (default `1.0` = pure EV) demoting a learned APPROVED to
NEEDS_REVIEW once `P(DENIED)` exceeds the threshold. Honest OOF gain vs. rules: **+1.16
classification pts** (dev 5-fold). Honest OOF CFA count is **12** (dev 5-fold, `experiments.md`);
the in-sample train-fit dev read shows only 5, so quoting 5 beside the OOF gain understates the
risk ~2.4×. Whether CFA 0 is a hard gate or a priced cost is an unreconciled decision (see
STATUS.md hazards).

## Confidence + emit (`mib/confidence.py`, `mib/emit.py`)

- **Rules path:** per-branch table fitted from dev empirical accuracy (Laplace-shrunk, clamped,
  `mib/confidence_table.json`); hand-set fallback if the table is absent. Never a constant — the
  calibration section scores `20·max(0, 1 − 2·mean_brier)`.
- **Learned path:** its own 1-D sigmoid calibrator on the chosen-class probability, clamped to
  `[0.05, 0.95]`.
- **`emit.validate`** is the schema safety net (the evaluator hard-fails on bad enums / confidence /
  duplicate or malformed ids): clamps enums and confidence, repairs `case_id` (last resort
  `MIB-000000`, a valid shape that matches no case — strictly better than a fatal malformed id),
  shape-checks `arrival_date` (coerces to `1900-01-01` on shape failure). A failed case emits a
  `NEEDS_REVIEW` fallback row rather than nothing (worth up to 8 raw pts, dodges the missing
  penalty).

## Orchestration (`solution.py`)

`multiprocessing.Pool(4)` (spawn start method pinned for dev/container parity) over
`sorted(*.pdf)`. `imap` streams rows **in input order, flushing each line**, so a container stopped
at the 30,000 s limit is scored on real partial output rather than an empty file. Duplicate case
ids are dropped with a stderr warning. `MIB_DEBUG_JSONL` writes the per-case diagnostic sidecar
(branch, provenance, census) beside a config-stamped `meta.json`; predictions stay schema-clean.

## Environment knobs

| var | default | effect |
| --- | --- | --- |
| `MIB_DECIDER` | `rules` | which decider ships (`rules` \| `mlp`) |
| `MIB_RESTORE` | `skew` | scan restoration level (`off` \| `skew` \| `turn` \| `bands`) |
| `MIB_EARLY_STOP` | `0` | `1` stops OCR at the first good-enough variant; default reads every variant and keeps best-of-all (+0.21 dev) |
| `MIB_CFA_VETO` | `1.0` | P(DENIED) that demotes a learned APPROVED to NEEDS_REVIEW (1.0 = pure EV) |
| `MIB_CASE_BUDGET_S` | `120` | per-case OCR wall-clock bound; overrun drops remaining pages to text layer |
| `MIB_WORKERS` | `4` | pool size (the contract gives 4 vCPU) |
| `MIB_DEBUG_JSONL` | unset | path for the per-case diagnostic sidecar |

## Known gaps

- **`arrival_date` is never calendar-validated.** `parse.valid_value`, `vocab.snap`, and
  `emit.validate` all regex the *shape* `\d{4}-\d{2}-\d{2}`; none call `date.fromisoformat`. An
  impossible-but-well-shaped date (`2026-03-41`, seen on MIB-000805 via loose-key matching) flows
  to output. The official `evaluate.py` only counts it as a field miss, but `validate_submission.py`
  rejects the row — it counts against the "90%+ valid rows" bar. A one-line calendar check in
  `valid_value` closes it.
- **S2 is not pure w.r.t. the parser** — a `KEY_MAP` edit can move which OCR variant wins (see the
  S2 hazard note).
