# FIELDS.md — the per-field wiki

A field-indexed reference: for each schema field, *where it lives, its shape, its decoys, how we handle it, and what's still open.* The other docs are organized on different axes — [BACKGROUND.md](BACKGROUND.md) by fraud *theme* (and evidence *modality*, §6), [experiments.md](experiments.md) by *change*, [ALGORITHM.md](ALGORITHM.md) by *stage*. This is the "what do we know about `fee_status`?" lookup that stitches those together.

**It is a jump-table, not a third copy.** Deep evidence stays in BACKGROUND; here each field points there. Keep it current: when a field's pattern gets mined, add a line and link the row.

**Evidence precedence** (higher wins, FIELD_MANUAL §): signed adjudicator note / stamp → intake form fields → biometric slip → sponsor attestation → registry extract → machine text layer. Hidden text, off-crop text, fake answer keys, barcodes are **never** trusted. `Manual correction: <field> is
<value>` is the signed-note tier (rank 0) and overrides everything.

## Quick reference

| Field | Pts | Shape | Primary source(s) | Signature trap |
| --- | --: | --- | --- | --- |
| `risk_flags` | 8 | pipe-list or `none` | B-13 slip, registry, adjudicator | unreadable ≠ `none`; legend/negation; emit observed-only |
| `species_code` | 6 | closed enum (12) | intake, B-13, registry | closed universe — drop unrepairable, don't pass through |
| `applicant_name` | 5 | 12×12 part grid (144, closed) | intake, registry, attestation prose | struck (100% paired w/ correction); `[NAME CUT OUT]`; confident stroke-merges (`rn`→`m`) |
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

- **Shape:** pipe-delimited, sorted, or `none`. Disqualifying: `memory_tampering`, `planetary_embargo`, `active_warrant`, `biohazard_red`. Review-only: `identity_conflict`, `sponsor_mismatch`, `illegible_biometrics`, `rescinded_denial`.
- **Sources ↓precedence:** adjudicator note, B-13 slip `Observed flags:`, registry status — but the scan reads **every** doc and variant (the doc-type gate was deleted, row 35: header-mangled pages were hiding true flags; the per-line guards, not doc typing, are the FP defense).
- **Decoys & traps:** unreadable risk line must **not** repair to `none` (MIB-000672 CFA — debris → a false "no risk observed"); legend lists (`Possible flags: a | b | c …`) and negations (`cleared of biohazard_red`) must yield nothing; OCR corruption (`bichaxarc_yed`→`biohazard_red`); damage markers (`[RISK PANEL MISSING]`) are not flag tokens.
- **Handling:** `signals.observed_flags` — value-first fuzzy match, confusion-weighted edit distance
  + runner-up margin guard (`vocab.match_flag_token`); union across losing OCR variants; whole-value rescue (`vocab.match_flag_value`, row 36) for shattered values, single-read or 2-reading quorum at mined bars. **Emit the observed subset only** (`emit_flags`): inferred flags (`sponsor_mismatch`, `identity_conflict`, the deleted `planetary_embargo`) drive the decision but are never written (organizer §1).
- **Mining:** `illegible_biometrics` has two truth modes — *printed* (94/94, P=1.00, emittable; recovery family shipped as rows 35–36) vs *condition-derived* (68, not emittable from the PDF); `biometric_confidence` (65–77%) doesn't discriminate. See BACKGROUND §3.
- **Open:** `rescinded_denial` (crossed-out denial note) ⬜ unmapped; `Registry status ≠ clear` 🔶; mode-B dead pages (57 cases, no B-13 detected) unassessed page-by-page.
- **See:** BACKGROUND §3 · experiments rows 5, 18, 30, 35, 36 · `mib/signals.py`, `mib/vocab.py`

## `species_code` — 6 pts

- **Shape:** closed enum, **12** species; `[A-Z][A-Z_]+` (e.g. `JOVIAN_GASFORM`, `ALPHA_DRACONIAN`, `AQUARIAN_MANTIS`, `ANDROMEDAN`).
- **Sources ↓precedence:** intake, B-13 (`Species Match`), registry.
- **Decoys & traps:** closed universe — a 13th species would be expected ~77× in 1,000 cases, so unrepairable reads are **dropped, not passed through** (passthrough measured −0.08/−0.04).
- **Handling:** `vocab.snap` to the enum; `_repair_ocr_kv` drops hopeless reads (absent beats wrong).
- **See:** experiments row 15b/rejected · `mib/vocab.py` · test `test_unrepairable_values_are_dropped`

## `applicant_name` — 5 pts

- **Shape:** NOT free after all — the generator composes every name as prefix+suffix from a **12×12 grid** (144 parts, `vocab.NAME_PARTS`; grid == the pool mined from all 1,000 truth names, verified 2026-07-26). Sharpened census (row 64): **exactly 2** alphabetic tokens, each **≥4 chars**, every token Capitalized, across all 1,000 truths. The `valid_value` shape guard that enforced this was **dropped on user call in the de-special-casing arc (row 72)**: its full ablation priced at −0.02 dev — 0 true names blocked, 0 adjudication moves, 2 names lost (a watermark fusion and a single-token truncation) and 14 honest-unknowns now emit as debris — and the token-count clause was a pure generator bet doing no work of its own (every rejected junk string also failed a hygiene clause). The census itself stays true and still powers the sepless corroboration shape (`_NAME_SHAPE_RE`) and the pool snap.
- **Sources ↓precedence:** intake, registry (`registry_name`), B-13, sponsor attestation prose (`attests that <Name> is expected on Earth`).
- **Decoys & traps:** **struck names are 100% paired** (27/27) with a `Manual correction` that gives the truth (row 34); damage markers `[NAME CUT OUT]`/`[MAME CUT OUT]` are not values; registry≠intake → `identity_conflict`; stroke-merge misreads read MORE confidently than truth (`rn`→`m`, token-final `ri`→`n` — engine conf must not arbitrate alone, rows 45/49 ablation 7/0); when intake and sponsor disagree on the name, the **sponsor letter is right 28:5 (text) / 12:4 (OCR)** — the poisoned intake carries an AUTHORED wrong-for-this-case name on an otherwise-truthful form (row 67: 0/13 such names own any train case and other fields match the active truth 6/6 — whether the name is fabricated or a real out-of-train applicant's digital form is undecidable, row 68 wording caveat; the poisoned page itself is a pristine digital form, portrait + seal only, no ink text); the shipped resolution is the **corroboration challenge** (row 63): a name asserted by ≥2 distinct documents and strictly more than the incumbent's deposes the single-document winner (FIXED 6/BROKE 0 — the active case id is stamped on every page, so id-anchored attribution is impossible; single-doc-vs-single-doc conflicts stay with `_preference`, and every weaker clause measured net-negative).
- **Handling:** per-field preference (clean text-layer beats OCR — row 15b); vote keys merge debris + stroke collapses instead of tying, per-line conf breaks true ties (rows 45/49); **pool snap** (row 52): out-of-pool tokens are misreads with P(correct)=0/61 on dev, so they snap to their unique best pool part at 0.72/margin 0.08 (in-pool tokens NEVER substituted — `Luix`≠`Lurix`; 104 pool pairs sit within 0.75); capitalization normalized; `identity_conflict` tolerates an OCR-sourced registry name at similarity ≥0.75 (row 33) and a losing variant's registry agreement (row 43).
- **Label-free error oracle:** out-of-pool rate doubles as a corpus self-diagnostic — applied to old-rev validation output it estimated 82% name accuracy with no labels (row 52).
- **See:** BACKGROUND §3 (identity) · experiments rows 15b, 33, 34, 45, 47, 49, 50, 52 · `mib/packet.py`, `mib/signals.py`, `mib/vocab.py`

## `home_world` — 5 pts

- **Shape:** closed enum, **13** worlds (e.g. `Luyten-b`, `Wolf-1061c`, `TRAPPIST-1e`, `Eris Relay`, `Proxima-b`, `Titan Freeport`, `Sirius Outpost`, `Barnard-c`, `Kepler-186f`).
- **Sources ↓precedence:** intake, registry (reads cleanly even when the intake scan garbles it — MIB-000514).
- **Decoys & traps:** embargo worlds — `TRAPPIST-1e`/`Eris Relay` deny **including DIP-1** (embargo pierces immunity); `Wolf-1061c` denies non-DIP only. OCR (`Woll-1081c`→`Wolf-1061c`).
- **Handling:** `vocab.snap` to the enum; policy `embargo_world` / `embargo_world_partial`.
- **See:** BACKGROUND §2 (embargo worlds) · `mib/policy.py`

## `visa_class` — 5 pts

- **Shape:** enum {`XW-1`, `XW-2`, `DIP-1`, `MED-3`, `TRANSIT-7`}.
- **Sources ↓precedence:** intake, sponsor attestation prose (`class XW-2 compliance`), `Manual correction`.
- **Decoys & traps:** **struck visa 100% paired** (29/29) with a correction (row 34); an *unknown* visa must not arm the non-DIP-only denials (positive-precondition rule — a failed extract of a true DIP-1 caused 3 over-denials). Policy carve-outs: `DIP-1` (fee may be waived, sponsor not required), `TRANSIT-7` (deny work auth), `MED-3` (requires clean biohazard).
- **Handling:** `valid_value` enum gate; `known_non_dip` guards the deny branches.
- **See:** BACKGROUND §3 (class abuse) · experiments row 34 · `mib/policy.py`

## `sponsor_id` — 5 pts

- **Shape:** `^SPN-[0-9]{4}$`.
- **Sources ↓precedence:** intake, sponsor attestation prose (`Sponsor SPN-#### attests`), `Manual correction`, registry.
- **Decoys & traps:** **struck sponsor 100% paired** (47/47) with a correction (row 34); **revoked** ids `SPN-0007/0139/4040` (+ 3 mined) deny non-DIP; **revoked-neighbor ring** — the corpus plants innocent ids one digit from a revoked (`SPN-4044/4007/3040/6040` around `4040`), incl. a true APPROVED — so repair must **never snap an id toward the revoked list**; missing sponsor non-DIP → *review*, not deny.
- **Handling:** `SPONSOR_RE`; guarded digit repair (`vocab.snap`, exact digits before a revoked match counts); `mib/corpus.py` re-derives the revoked ids from recurrence (label-free), gated on ablation.
- **Mining:** the 6 revoked ids recur 9–22× against a 1–2× background (bimodal) and transfer to the 5,000-case validation set exactly (same 6, gap 14.6×).
- **See:** BACKGROUND §2 (revoked), §3 (sponsor fraud) · experiments rows 23, 24, 34 · `mib/corpus.py`

## `arrival_date` — 4 pts

- **Shape:** ISO `YYYY-MM-DD`; `valid_value` rejects well-shaped-but-impossible dates (`2026-03-41`) and years outside 2020–2030 (row 62: OCR garble like `2928` = `2026` under 9/0 confusion forms plausible ISO dates that outvote the true reading; truth spans 2025–2026, the wide window avoids riding the label distribution).
- **Sources ↓precedence:** intake, registry.
- **Decoys & traps:** systematic `6→8` year OCR error (`2028`→`2026`, 23/25); a **legible date value under a garbled key** (MIB-000614 registry `nal iste: 2028-04-29` — key-anchored parse drops it, **value-anchored recovery is an open lever**); missing/hidden-only arrival → NEEDS_REVIEW. The `1900-01-01` sentinel is emit-only (policy decides on the raw values first, so `missing_arrival` still fires on absence).
- **Handling:** `DATE_RE`; `vocab.snap` snaps future-impossible years (≥2028 one glyph off 2026) but never past ones (un-staling a real 2024 trades a 0-cost wrong denial for a −4 false approval). Staleness: `< STALE_CUTOFF (2026-01-02)` & non-DIP → deny.
- **Mining:** `STALE_CUTOFF` margin collapsed 37 d (train) → 2 d (validation) — correct but a logged no-margin risk (6 validation cases within ±7 d).
- **Open:** value-anchored date recovery when the key OCR's to debris (the 614 shape).
- **See:** BACKGROUND §2 (staleness), §3 (digit confusions) · experiments rows 24, 26 · `mib/parse.py`

## `fee_status` — 4 pts

- **Shape:** enum {`paid`, `waived`, `unpaid`, `unknown`}.
- **Sources ↓precedence:** `Manual correction` (rank 0), adjudicator note, then the **fee receipt** (low trust — and the most-decoyed source in the corpus).
- **Decoys & traps:** **red-strikethrough receipts** — a struck value ⟺ printed ≠ truth (0 counterexamples/452), and fee is the field where the strike is **only 18%-paired** with a correction (5/28), so a struck value is genuinely unrecoverable → void to `unknown` → NEEDS_REVIEW (row 34, fixed MIB-000514/000614 false denials). **Waiver-code presence alone is not approval** (v0's `DIP-WAIVER` shortcut caused 18 CFAs). `unpaid` snaps by plain distance since row 69 (user call; the verbatim-only guard's history and safety table live in experiments rows 68–69 — priced at one lost field, MIB-000688). **Label errata (user eye-audit of all pages, 2026-07-26): MIB-000191 AND MIB-000893 — both labels say `paid`, both documents print an unstruck `unpaid`; the labels are wrong.** 191's NEEDS_REVIEW rides `identity_conflict` (label and our flags agree) and 893's APPROVED rides its adjudicator finding, so neither decision involves the fee; our document-faithful `unpaid` emissions lose those two fields by construction — accepted, and any future fee-accuracy census should discount them. **Eight more of the same signature, user eye-confirmed 2026-07-28 ("these are all unpaid / waived") from the fee-errata gallery: MIB-000046, -000074, -000182, -000211, -000437, -000525, -000907, -000931** — label says `paid` (or `waived`), the receipt legibly prints a *different* unstruck value, and the label's value is absent everywhere in the packet (visible/OCR/hidden). We emit the visible value on all of them (15/16 across the pooled set), so all are unwinnable extraction misses to discount; adjudication is untouched except where the errata-consistent truth verdict diverges from our visible call (182 APPROVED vs our NR-on-`waived`, 931 NR — the latter now *correct* by an unrelated route, the row-94 reason fallback). Distinct sibling — **six "hidden-key" fee decoys (MIB-000058, -000345, -000362, -000435, -000723, -000853): the label's value lives ONLY in the hidden answer key**, so it is *not* errata but an adversarial decoy — and by the untrusted-hidden-text rule our visible emission is the spec-correct one anyway. The generalizable lesson: an "absent" fee whose truth token is nowhere-visible is far more often a decoy/errata than a recovery target (see the 2026-07-28 A/B/C absent-value census). **MIB-000688 is NOT errata** (eye-audit: receipt prints `paid`; label right): it is row 69's one real cost — the shred seam decapitates `paid` to `naid` systematically across 4 variants, `naid` reconstructs to `unpaid` (difflib 0.80 vs 0.75 — the stray `n` aligns with u-**n**-paid), and the reconstructed family outvotes the 2 verbatim `paid` reads the repair rungs recovered. Stale receipts are superseded through two VISIBLE channels, both handled: the red strike (text-layer detected + voided — 514/614/855, cache-verified struck spans) and the `Manual correction` note (284 waived / 355 paid — rank 0, wins the merge regardless of what the receipt prints). Visible in ~0% of cases (3.6% with OCR) — mostly unrecoverable.
- **Handling:** `parse_kv` (incl. separator-less `Fee Status waved` lines, row 50 — the `[:.;]` glyph dies first; 7 fee statuses recovered) → `_void_struck` → policy `fee_unpaid` (deny) / `fee_unknown` (review) / `waived_non_dip` (review unless visible hardship/DIP waiver). `snap` repairs toward the enum at 0.7 but `unpaid` must be read verbatim (one edit from `paid`, deny direction).
- **Mining:** `fee_unknown` holds the largest conservative-review mass and **no inference survives its census** (TODO 5.2a, 2026-07-26): receipt-presence⇒paid is 59% pure with truth-`unpaid` inside (CFA direction); waiver-code-seen is dominated by truth-unknown; printed `[FEE STATUS OBSCURED]` markers are honest unknowns. The residue is dead evidence pages — and the 2026-07-26 gallery showed many "dead" receipt pages are actually faint-but-legible with the status already extracted (the GOOD_ENOUGH label-count artifact); the truly silent cases have no fee anywhere in the ensemble. Grid recount (row 61): 96 of the 122 fee_unknown-branch dev cases are truly silent; 22 carry an **explicit unknown statement** (now a display ladder tier — unpaid > DIP-WAIVER > unknown > paid; waived-above-unknown measured on the two dual-evidence packets 219/554, both truth-waived); the statement class is itself partly a trap — 10 of the 34 dev triggers are planted `unknown` receipts on truth-`paid` labels, and the split/garbled **non**-unknown receipts are pure traps (514's `unpaid` on truth-APPROVED, 855's `paid` on truth-DENIED) — receipt values never feed policy, and fuzzy fee-KEY repair is CLOSED as ~0-residue for the same reason. All model families lose to the rules baseline and add CFAs.
- **Open:** scanned-receipt **red-pixel** strikes (OCR is grayscale) are unmapped; `waived_non_dip` may be too coarse where a visible `DIP-WAIVER`+`$0.00`+correction justifies approval (MIB-000328) — census recorded in TODO 5.2a for the policy seam. **5.6 drift flag (row 52 mining): the fee-unknown∧stale∧non-DIP cell is 6.6× denser on old-rev validation output (7.52% vs dev 1.14%) — re-check on a current-rev val run before submission.**
- **See:** BACKGROUND §2.3, §3 (payment, strikethrough) · experiments row 34 · `mib/packet.py`, `mib/policy.py`

## `declared_purpose` — 3 pts (lowest extraction weight)

- **Shape:** exactly **10** purposes across all 1,000 train labels (`vocab.PURPOSES` is the enumeration — saturated like worlds/species); snap still passes unmatched text through, but separator-less claims and corroboration use the strict closed-set form (`vocab.repairable_purpose`, row 50). A colon inside a candidate value means the parse mis-keyed the line — 0/12,000 truth values contain one (row 47/49 census).
- **Sources ↓precedence:** intake, `Purpose:` alias, sponsor attestation prose (`expected on Earth for <purpose>`).
- **Decoys & traps:** the attestation states it in a **sentence that wraps mid-phrase** (`… for reactor` / `maintenance.`) — matching must join lines first; an explicit `Purpose:` outranks prose.
- **Handling:** `parse_prose` (anchored on attestation wording so a decoy can't match); **vocab passthrough is KEPT** here (free text, unlike the closed world/species enums).
- **Mining:** 273 of 312 dev "present-but-unextracted" field-instances were unparsed attestation prose.
- **See:** the P3 parse work (`b926403`; STATUS "tried and kept") · `mib/parse.py` (`_PROSE_PATTERNS`)

## `case_id` — required (scoring key)

- **Shape:** `^MIB-[0-9]{6}$`.
- **Sources:** every page header; also embedded in each document.
- **Decoys & traps:** **decoy pages for other applicants** (sequential ids make adjacent-case decoys cheap to plant); an *OCR* page one glyph off the active id is the applicant's own page misread (tolerated), a *text-layer* near-miss is a genuine decoy (no tolerance).
- **Handling:** majority vote across all pages; `emit._repair_case_id` recovers from debris → filename stem → a valid-but-unmatchable id (a malformed id fails the whole submission). Agrees with the filename on all 1,000 train packets, 0 collisions.
- **See:** experiments rows 17, 25 · `mib/packet.py`, `mib/emit.py`

## `adjudication` — 80 pts (the decision, not an extracted field)

- **Shape:** `APPROVED` / `DENIED` / `NEEDS_REVIEW`. Scoring: correct 8; wrong-to-NR 2; missed-NR 1; APPROVED↔DENIED 0; **false approval of a denied case −4** (the CFA — hard gate at 0).
- **Handling:** the ordered rules cascade (`mib/policy.py`, 16 branches). Precedence: `finding` → `disqualifying_flag` → `embargo_world[_partial]` → `revoked_sponsor` → `transit_visa` → `fee_unpaid` → `stale_arrival` → `fee_unknown` → `waived_non_dip` → `missing_arrival` → `review_flag` → `missing_sponsor` → `missing_visa` → `b13_census` → `clean_approve`. Deny rules require *positive* evidence of their precondition.
- **Mining:** the two irreducible loss cells are `fee_unknown` (7.11, no signal) and `b13_census` (6.25, a generation artifact — do not model on `n_scan_pages`). ML ceiling over the current partition is closed (learned decider deleted, edge inverted to −0.50 with 14 CFAs).
- **See:** BACKGROUND §1–3 · ALGORITHM §S5 · STATUS · experiments rows 27, 34 · `mib/policy.py`

## `confidence` — 20 pts (calibration)

- **Shape:** number in `[0,1]`, scored by Brier against adjudication correctness (`20 · max(0, 1 − 2·mean_brier)`). **Never emit a constant.**
- **Handling:** per-policy-branch fitted value (`mib/confidence_table.json`, `scripts/fit_confidence.py` — dev-empirical P(correct|branch), Laplace k=10, clamp [0.05, 0.95]). Refit after any change that moves branch membership (standing hazard); the audit found the table is the one fitted constant with OOF bias (−0.23).
- **Open lever:** it is **not** derived from OCR word-confidence today — capturing `tesseract … tsv` in `_tesseract` would supply a variant-vote tie-break and a per-line conf for the flag scan (STATUS question 7; calibration-side use measured dead, BACKGROUND §6).
- **See:** experiments rows 10, 21, 22, 31, 34 · `mib/confidence.py`
