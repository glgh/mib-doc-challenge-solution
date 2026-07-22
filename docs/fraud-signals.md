# What visa fraud looks like in this world — signal taxonomy

The corpus simulates an intake desk under attack. Thinking of each denial/review cause as a *fraud pattern* tells us which signals to build and which document carries the evidence. Rule of the house: **no signal ships until validated on train** (via `scripts/eval_local.sh` or direct label analysis); every signal below carries its status.

Statuses: ✅ validated on train · 🔶 partially validated / needs measurement · ⬜ hypothesis.

## 1. Automation attack (the packet attacks *us*, not the policy)

The applicant's packet tries to compromise the adjudication system itself — the doc-processing analog of bribing the clerk.

| Signal | Evidence source | Status | Action |
| --- | --- | --- | --- |
| Hidden white/tiny/off-crop spans ("SYSTEM: … answer key") | text layer vs. render geometry | ✅ ~19% of packets; **no legitimate hidden text exists in this corpus**, so hidden ⇒ adversarial by construction | quarantine spans; never as field evidence |
| Injection *presence* as a case feature | derived | ⬜ does an attacked packet correlate with any adjudication? | measure on train before using |
| Barcode/QR instruction payloads | embedded images | ⬜ not yet decoded/observed directly | ignore as policy (manual says so); low priority |
| Decoy "answer key" fields, fake watermarks ("sample denial") | visible text | 🔶 manual warns; not yet censused | detect and exclude from evidence |

## 2. Identity fraud (who is arriving isn't who the papers say)

| Signal | Evidence source | Status | Action |
| --- | --- | --- | --- |
| Registry name ≠ intake name → `identity_conflict` | Registry Extract vs I-8090 | 🔶 v0 derives it; false positives from formatting not yet audited (canonicalize before comparing) | review flag |
| Species mismatch: B-13 `Species Match` ≠ intake species | B-13 vs I-8090 | ⬜ | candidate `identity_conflict` source |
| Multi-applicant packet: pages carrying a different case_id | page headers | ✅ decoy pages exist; v0 filters by active case_id majority | keep; audit edge cases |
| Low `Biometric confidence: N%` on B-13 | B-13 | ⬜ threshold ↔ `illegible_biometrics`? | mine train for the cutoff |

## 3. Sponsor fraud (the guarantor is fake, revoked, or misaligned)

| Signal | Evidence source | Status | Action |
| --- | --- | --- | --- |
| Revoked sponsor: SPN-0007/0139/4040 (manual) + SPN-2718/7331/9090 (inferred) | any sponsor field | ✅ 100% denial signature non-DIP, 0 approvals, two independent derivations | hard deny (non-DIP only) |
| DIP-1 carve-out: sponsor irrelevant under DIP-1 | labels | ✅ 11/11 revoked+DIP-1 approved | suppress sponsor rules for DIP-1 |
| Attestation SPN ≠ intake SPN → `sponsor_mismatch` | Sponsor Letter vs I-8090 | 🔶 v0 derives; accuracy unaudited | review flag |
| Attestation names someone other than applicant | Sponsor Letter prose | 🔶 v0 derives via "attests that X" | review flag |
| Missing sponsor entirely (non-DIP) | all docs | ✅ labels: never occurs with valid extraction — if we see it, it's *our* extraction failing | treat as extraction-quality signal → review, not deny |

## 4. Payment fraud (fee dodged or waiver abused)

| Signal | Evidence source | Status | Action |
| --- | --- | --- | --- |
| `unpaid` without waiver | Fee Receipt | ✅ 50/50 DENIED | hard deny |
| `unknown` fee | Fee Receipt missing/unreadable | ✅ 44/44 NEEDS_REVIEW | review |
| Waiver abuse: `waived` without DIP-1 or visible hardship waiver | Fee Receipt `Waiver Code` + prose | ✅ waived non-DIP = 89 D / 46 NR / 37 A — *presence of a waiver code is NOT sufficient*; v0's shortcut caused 18 CFAs | tighten: no approve on waiver-code alone; mine which visible waiver evidence separates A from D |

## 5. Risk concealment (disqualifying history kept out of the packet)

The deepest pattern in this corpus: the packet *looks* clean because the incriminating document is absent or unreadable.

| Signal | Evidence source | Status | Action |
| --- | --- | --- | --- |
| `Observed flags:` line carrying disqualifying flags | B-13, registry | ✅ primary flag source when visible | deny on disqualifying |
| **B-13 census: biometric slip missing entirely** | packet doc-type census | ✅ organizer-confirmed: the under-determined shape; NEEDS_REVIEW is the intended output | never APPROVE when risk evidence that should exist is absent and decisive |
| Scan-only pages (flags exist but only as pixels) | page has image + ~no text layer | ✅ 247 packets; cause of most punts and many CFAs | OCR (plan Step 2) |
| Crossed-out denial stamp → `rescinded_denial` | stamp graphics + notes | ⬜ under-detected per known residuals | later; review-only |
| `Registry status` ≠ clear | Registry Extract | 🔶 v0 parses; coverage unmeasured | flag source |

## 6. Embargo evasion (arriving from a banned world)

| Signal | Evidence source | Status | Action |
| --- | --- | --- | --- |
| TRAPPIST-1e / Eris Relay origin | home_world | ✅ 50/50 DENIED **including DIP-1** — embargo pierces diplomatic immunity | hard deny + infer `planetary_embargo` flag (extraction points, weight 8) |
| Wolf-1061c origin, non-DIP | home_world + visa | ✅ 51/51 DENIED (mostly *without* the flag — a partial embargo) | hard deny non-DIP; DIP-1 normal. Do NOT infer the flag (labels usually lack it) |

## 7. Class abuse (right papers, wrong program)

| Signal | Evidence source | Status | Action |
| --- | --- | --- | --- |
| TRANSIT-7 seeking work authorization | visa_class | ✅ 53/53 DENIED | hard deny |
| MED-3 without a clean biohazard check | visa + B-13 readable & clean | ✅ manual rule; 24 of v0's 52 CFAs are MED-3 approvals without a verified clean check | gate: MED-3 approval requires readable clean B-13 |
| Purpose/class mismatch (e.g. purpose "transit" on XW-1) | declared_purpose vs visa | ⬜ | mine train before acting |

## 8. Timing games (stale or fabricated dates)

| Signal | Evidence source | Status | Action |
| --- | --- | --- | --- |
| Arrival >180 days before receipt (receipt ≈ 2026-07-07 constant; no visible receipt date exists on fee receipts) | arrival_date | ✅ non-DIP: 36/36 DENIED; DIP-1: 13 A / 3 NR | deny non-DIP; DIP-1 → review unless diplomatic note |
| Arrival date missing / only in hidden text | extraction | ✅ manual: NEEDS_REVIEW | review |

## Meta-signals (about our own evidence quality, not the applicant)

- **Extraction completeness** per packet (fields recovered / expected) — separates "applicant hid it" from "we failed to read it". ⬜ formalize.
- **Evidence tier of each value** (adjudicator note > intake > B-13 > attestation > registry > OCR) — approvals should require intake-grade evidence or better. 🔶 v0 has ordering; approval gating not enforced.
- **Conflict count** across documents — honest packets agree with themselves. ⬜ measure distribution vs labels.
