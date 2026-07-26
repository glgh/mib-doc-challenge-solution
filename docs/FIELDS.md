# FIELDS.md — the per-field wiki

A field-indexed reference: for each schema field, *where it lives, its shape, its decoys, how we
handle it, and what's still open.* The other docs are organized on different axes —
[BACKGROUND.md](BACKGROUND.md) by fraud *theme* (and evidence *modality*, §6),
[experiments.md](experiments.md) by *change*, [ALGORITHM.md](ALGORITHM.md) by *stage*. This
is the "what do we know about `fee_status`?" lookup that stitches those together.

**It is a jump-table, not a third copy.** Deep evidence stays in BACKGROUND; here each field
points there. Keep it current: when a field's pattern gets mined, add a line and link the row.

**Evidence precedence** (higher wins, FIELD_MANUAL §): signed adjudicator note / stamp → intake form
fields → biometric slip → sponsor attestation → registry extract → machine text layer. Hidden text,
off-crop text, fake answer keys, barcodes are **never** trusted. `Manual correction: <field> is
<value>` is the signed-note tier (rank 0) and overrides everything.

## Quick reference

| Field | Pts | Shape | Primary source(s) | Signature trap |
| --- | --: | --- | --- | --- |
| `risk_flags` | 8 | pipe-list or `none` | B-13 slip, registry, adjudicator | unreadable ≠ `none`; legend/negation; emit observed-only |
| `species_code` | 6 | closed enum (12) | intake, B-13, registry | closed universe — drop unrepairable, don't pass through |
| `applicant_name` | 5 | free (Title Case) | intake, registry, attestation prose | struck (100% paired w/ correction); `[NAME CUT OUT]`; misreads |
| `home_world` | 5 | closed enum (13) | intake, registry | embargo worlds; OCR (`Woll-1081c`→`Wolf-1061c`) |
| `visa_class` | 5 | enum (5) | intake, attestation prose, correction | struck (100% paired); unknown must not arm non-DIP denials |
| `sponsor_id` | 5 | `^SPN-[0-9]{4}$` | intake, attestation prose, correction, registry | struck (100% paired); revoked-neighbor ring; never snap toward revoked |
| `arrival_date` | 4 | ISO `YYYY-MM-DD` | intake, registry | `6→8` year OCR; legible value under a garbled key; `1900-01-01` sentinel |
| `fee_status` | 4 | enum (4) | fee receipt, correction, adjudicator | **struck receipts (only 18% paired) → `unknown`**; waiver-code-alone ≠ approve |
| `declared_purpose` | 3 | free (~10) | intake, attestation prose, `Purpose:` | prose wraps mid-phrase; passthrough KEPT (free text) |
| `case_id` | req | `^MIB-[0-9]{6}$` | every page header | decoy pages for other applicants |
| `adjudication` | 80 | 3-way | the rules cascade | −4 false approval; NR still earns 2 |
| `confidence` | 20 | `[0,1]` | per-branch fitted | never constant; Brier-scored |

---

## `risk_flags` — 8 pts (highest extraction weight)

- **Shape:** pipe-delimited, sorted, or `none`. Disqualifying: `memory_tampering`, `planetary_embargo`,
  `active_warrant`, `biohazard_red`. Review-only: `identity_conflict`, `sponsor_mismatch`,
  `illegible_biometrics`, `rescinded_denial`.
- **Sources ↓precedence:** adjudicator note, B-13 slip `Observed flags:`, registry status
  (`FLAG_DOC_TYPES` = adjudicator/biometric/registry — scanning others invites decoy/legend FPs).
- **Decoys & traps:** unreadable risk line must **not** repair to `none` (MIB-000672 CFA — debris → a
  false "no risk observed"); legend lists (`Possible flags: a | b | c …`) and negations (`cleared of
  biohazard_red`) must yield nothing; OCR corruption (`bichaxarc_yed`→`biohazard_red`).
- **Handling:** `signals.observed_flags` — value-first fuzzy match, confusion-weighted edit distance
  + runner-up margin guard (`vocab.match_flag_token`); union across losing OCR variants. **Emit the
  observed subset only** (`emit_flags`): inferred flags (`sponsor_mismatch`, `identity_conflict`, the
  deleted `planetary_embargo`) drive the decision but are never written (organizer §1).
- **Mining:** `illegible_biometrics` has two truth modes — *printed* (94/94, P=1.00, emittable) vs
  *condition-derived* (68, not emittable from the PDF); `biometric_confidence` (65–77%) doesn't
  discriminate. See BACKGROUND §3.
- **Open:** `rescinded_denial` (crossed-out denial note) ⬜ unmapped; `Registry status ≠ clear` 🔶;
  3 printed-token misses on headers that defeat `detect_doc_type` (label-anchored value recovery).
- **See:** BACKGROUND §3 · experiments rows 5, 18, 30 · `mib/signals.py`, `mib/vocab.py`

## `species_code` — 6 pts

- **Shape:** closed enum, **12** species; `[A-Z][A-Z_]+` (e.g. `JOVIAN_GASFORM`, `ALPHA_DRACONIAN`,
  `AQUARIAN_MANTIS`, `ANDROMEDAN`).
- **Sources ↓precedence:** intake, B-13 (`Species Match`), registry.
- **Decoys & traps:** closed universe — a 13th species would be expected ~77× in 1,000 cases, so
  unrepairable reads are **dropped, not passed through** (passthrough measured −0.08/−0.04).
- **Handling:** `vocab.snap` to the enum; `_repair_ocr_kv` drops hopeless reads (absent beats wrong).
- **See:** experiments row 15b/rejected · `mib/vocab.py` · test `test_unrepairable_values_are_dropped`

## `applicant_name` — 5 pts

- **Shape:** free string, Title-Case, usually two tokens. Not a closed vocab.
- **Sources ↓precedence:** intake, registry (`registry_name`), B-13, sponsor attestation prose
  (`attests that <Name> is expected on Earth`).
- **Decoys & traps:** **struck names are 100% paired** (27/27) with a `Manual correction` that gives
  the truth (row 34); damage markers `[NAME CUT OUT]`/`[MAME CUT OUT]` are not values; registry≠intake
  → `identity_conflict`; single-glyph misreads (`Miravoss`/`Mirayoss`).
- **Handling:** per-field preference (clean text-layer beats OCR — row 15b); `norm_name` for all
  comparisons; `identity_conflict` tolerates an OCR-sourced registry name at similarity ≥0.75 (row 33).
- **See:** BACKGROUND §3 (identity) · experiments rows 15b, 33, 34 · `mib/packet.py`, `mib/signals.py`

## `home_world` — 5 pts

- **Shape:** closed enum, **13** worlds (e.g. `Luyten-b`, `Wolf-1061c`, `TRAPPIST-1e`, `Eris Relay`,
  `Proxima-b`, `Titan Freeport`, `Sirius Outpost`, `Barnard-c`, `Kepler-186f`).
- **Sources ↓precedence:** intake, registry (reads cleanly even when the intake scan garbles it —
  MIB-000514).
- **Decoys & traps:** embargo worlds — `TRAPPIST-1e`/`Eris Relay` deny **including DIP-1** (embargo
  pierces immunity); `Wolf-1061c` denies non-DIP only. OCR (`Woll-1081c`→`Wolf-1061c`).
- **Handling:** `vocab.snap` to the enum; policy `embargo_world` / `embargo_world_partial`.
- **See:** BACKGROUND §2 (embargo worlds) · `mib/policy.py`

## `visa_class` — 5 pts

- **Shape:** enum {`XW-1`, `XW-2`, `DIP-1`, `MED-3`, `TRANSIT-7`}.
- **Sources ↓precedence:** intake, sponsor attestation prose (`class XW-2 compliance`), `Manual
  correction`.
- **Decoys & traps:** **struck visa 100% paired** (29/29) with a correction (row 34); an *unknown*
  visa must not arm the non-DIP-only denials (positive-precondition rule — a failed extract of a true
  DIP-1 caused 3 over-denials). Policy carve-outs: `DIP-1` (fee may be waived, sponsor not required),
  `TRANSIT-7` (deny work auth), `MED-3` (requires clean biohazard).
- **Handling:** `valid_value` enum gate; `known_non_dip` guards the deny branches.
- **See:** BACKGROUND §3 (class abuse) · experiments row 34 · `mib/policy.py`

## `sponsor_id` — 5 pts

- **Shape:** `^SPN-[0-9]{4}$`.
- **Sources ↓precedence:** intake, sponsor attestation prose (`Sponsor SPN-#### attests`), `Manual
  correction`, registry.
- **Decoys & traps:** **struck sponsor 100% paired** (47/47) with a correction (row 34); **revoked**
  ids `SPN-0007/0139/4040` (+ 3 mined) deny non-DIP; **revoked-neighbor ring** — the corpus plants
  innocent ids one digit from a revoked (`SPN-4044/4007/3040/6040` around `4040`), incl. a true
  APPROVED — so repair must **never snap an id toward the revoked list**; missing sponsor non-DIP →
  *review*, not deny.
- **Handling:** `SPONSOR_RE`; guarded digit repair (`vocab.snap`, exact digits before a revoked match
  counts); `mib/corpus.py` re-derives the revoked ids from recurrence (label-free), gated on ablation.
- **Mining:** the 6 revoked ids recur 9–22× against a 1–2× background (bimodal) and transfer to the
  5,000-case validation set exactly (same 6, gap 14.6×).
- **See:** BACKGROUND §2 (revoked), §3 (sponsor fraud) · experiments rows 23, 24, 34 · `mib/corpus.py`

## `arrival_date` — 4 pts

- **Shape:** ISO `YYYY-MM-DD`; `valid_value` rejects well-shaped-but-impossible dates (`2026-03-41`).
- **Sources ↓precedence:** intake, registry.
- **Decoys & traps:** systematic `6→8` year OCR error (`2028`→`2026`, 23/25); a **legible date value
  under a garbled key** (MIB-000614 registry `nal iste: 2028-04-29` — key-anchored parse drops it,
  **value-anchored recovery is an open lever**); missing/hidden-only arrival → NEEDS_REVIEW. The
  `1900-01-01` sentinel is emit-only (policy decides on the raw values first, so `missing_arrival`
  still fires on absence).
- **Handling:** `DATE_RE`; `vocab.snap` snaps future-impossible years (≥2028 one glyph off 2026) but
  never past ones (un-staling a real 2024 trades a 0-cost wrong denial for a −4 false approval).
  Staleness: `< STALE_CUTOFF (2026-01-02)` & non-DIP → deny.
- **Mining:** `STALE_CUTOFF` margin collapsed 37 d (train) → 2 d (validation) — correct but a logged
  no-margin risk (6 validation cases within ±7 d).
- **Open:** value-anchored date recovery when the key OCR's to debris (the 614 shape).
- **See:** BACKGROUND §2 (staleness), §3 (digit confusions) · experiments rows 24, 26 · `mib/parse.py`

## `fee_status` — 4 pts

- **Shape:** enum {`paid`, `waived`, `unpaid`, `unknown`}.
- **Sources ↓precedence:** `Manual correction` (rank 0), adjudicator note, then the **fee receipt**
  (low trust — and the most-decoyed source in the corpus).
- **Decoys & traps:** **red-strikethrough receipts** — a struck value ⟺ printed ≠ truth (0
  counterexamples/452), and fee is the field where the strike is **only 18%-paired** with a
  correction (5/28), so a struck value is genuinely unrecoverable → void to `unknown` → NEEDS_REVIEW
  (row 34, fixed MIB-000514/000614 false denials). **Waiver-code presence alone is not approval**
  (v0's `DIP-WAIVER` shortcut caused 18 CFAs). `unpaid` is one edit from `paid` — read verbatim,
  never snap. Visible in ~0% of cases (3.6% with OCR) — mostly unrecoverable.
- **Handling:** `parse_kv` → `_void_struck` → policy `fee_unpaid` (deny) / `fee_unknown` (review) /
  `waived_non_dip` (review unless visible hardship/DIP waiver).
- **Mining:** `fee_unknown` holds 7.11 class pts with **no signal** — all model families lose to the
  rules baseline and add CFAs; the fee is genuinely not in the document.
- **Open:** scanned-receipt **red-pixel** strikes (OCR is grayscale) are unmapped; `waived_non_dip`
  may be too coarse where a visible `DIP-WAIVER`+`$0.00`+correction justifies approval (MIB-000328).
- **See:** BACKGROUND §2.3, §3 (payment, strikethrough) · experiments row 34 · `mib/packet.py`, `mib/policy.py`

## `declared_purpose` — 3 pts (lowest extraction weight)

- **Shape:** free string, ~**10** purposes (semi-closed but treated as free text). E.g. `medical
  consult`, `field repair`, `diplomatic`, `reactor maintenance`.
- **Sources ↓precedence:** intake, `Purpose:` alias, sponsor attestation prose (`expected on Earth
  for <purpose>`).
- **Decoys & traps:** the attestation states it in a **sentence that wraps mid-phrase** (`… for
  reactor` / `maintenance.`) — matching must join lines first; an explicit `Purpose:` outranks prose.
- **Handling:** `parse_prose` (anchored on attestation wording so a decoy can't match); **vocab
  passthrough is KEPT** here (free text, unlike the closed world/species enums).
- **Mining:** 273 of 312 dev "present-but-unextracted" field-instances were unparsed attestation prose.
- **See:** the P3 parse work (`b926403`; STATUS "tried and kept") · `mib/parse.py` (`_PROSE_PATTERNS`)

## `case_id` — required (scoring key)

- **Shape:** `^MIB-[0-9]{6}$`.
- **Sources:** every page header; also embedded in each document.
- **Decoys & traps:** **decoy pages for other applicants** (sequential ids make adjacent-case decoys
  cheap to plant); an *OCR* page one glyph off the active id is the applicant's own page misread
  (tolerated), a *text-layer* near-miss is a genuine decoy (no tolerance).
- **Handling:** majority vote across all pages; `emit._repair_case_id` recovers from debris → filename
  stem → a valid-but-unmatchable id (a malformed id fails the whole submission). Agrees with the
  filename on all 1,000 train packets, 0 collisions.
- **See:** experiments rows 17, 25 · `mib/packet.py`, `mib/emit.py`

## `adjudication` — 80 pts (the decision, not an extracted field)

- **Shape:** `APPROVED` / `DENIED` / `NEEDS_REVIEW`. Scoring: correct 8; wrong-to-NR 2; missed-NR 1;
  APPROVED↔DENIED 0; **false approval of a denied case −4** (the CFA — hard gate at 0).
- **Handling:** the ordered rules cascade (`mib/policy.py`, 16 branches). Precedence: `finding` →
  `disqualifying_flag` → `embargo_world[_partial]` → `revoked_sponsor` → `transit_visa` → `fee_unpaid`
  → `fee_unknown` → `stale_arrival` → `waived_non_dip` → `missing_arrival` → `review_flag` →
  `missing_sponsor` → `missing_visa` → `b13_census` → `clean_approve`. Deny rules require *positive*
  evidence of their precondition.
- **Mining:** the two irreducible loss cells are `fee_unknown` (7.11, no signal) and `b13_census`
  (6.25, a generation artifact — do not model on `n_scan_pages`). ML ceiling over the current
  partition is closed (learned decider deleted, edge inverted to −0.50 with 14 CFAs).
- **See:** BACKGROUND §1–3 · ALGORITHM §S5 · STATUS · experiments rows 27, 34 · `mib/policy.py`

## `confidence` — 20 pts (calibration)

- **Shape:** number in `[0,1]`, scored by Brier against adjudication correctness
  (`20 · max(0, 1 − 2·mean_brier)`). **Never emit a constant.**
- **Handling:** per-policy-branch fitted value (`mib/confidence_table.json`, `scripts/fit_confidence.py`
  — dev-empirical P(correct|branch), Laplace k=10, clamp [0.05, 0.95]). Refit after any change that
  moves branch membership (standing hazard); the audit found the table is the one fitted constant with
  OOF bias (−0.23).
- **Open lever:** it is **not** derived from OCR word-confidence today — capturing `tesseract … tsv`
  in `_tesseract` would supply a variant-vote tie-break and a per-line conf for the flag scan
  (STATUS question 7; calibration-side use measured dead, BACKGROUND §6).
- **See:** experiments rows 10, 21, 22, 31, 34 · `mib/confidence.py`
