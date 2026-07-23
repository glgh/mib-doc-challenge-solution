# Intel from the challenge repo's issues and PRs

Date: 2026-07-21. Source: github.com/8090-inc/mib-doc-challenge issues #4/#5 (organizer answers = authoritative) and submission PRs #3/#6/#7 (competitor memos = hypotheses; verify before adopting).

## Organizer clarifications (authoritative)

Moved to their own standing document: **[organizer-guidance.md](organizer-guidance.md)** — treat that file as part of the spec. Summary: under-determined cases are intentional; never guess invisible flags; NEEDS_REVIEW is the intended output when missing risk evidence is outcome-determinative; the detectable signal is a missing B-13 biometric slip (document census).

## Competitor memos — verified claims (checked against train labels ourselves)

- **Embargo worlds** (claimed in PR #3, verified 2026-07-21):
  - TRAPPIST-1e: 32/32 cases `planetary_embargo`, 32/32 DENIED — **including 8/8 DIP-1**.
  - Eris Relay: 18/18 embargo, 18/18 DENIED — including 5/5 DIP-1.
  - Wolf-1061c: non-DIP → 51/51 DENIED (mostly *without* the embargo flag); DIP-1 → 11 A / 10 NR / 5 D (roughly normal). Rule: non-DIP Wolf-1061c → DENIED; DIP-1 Wolf-1061c → treat normally.
  - Side benefit: home_world → embargo flag inference can also earn `risk_flags` extraction points (weight 8) for TRAPPIST-1e / Eris Relay.
- **Revoked sponsors SPN-9090 / SPN-7331 / SPN-2718** (PR #3) — independently matches our own label mining (see label-mining.md). Two independent derivations; high confidence.

## Competitor lessons taken as strong priors (not re-verified yet)

From **afifi-yusuf** (PR #7, CV/DL background — tried the fancy stuff and measured it):
- **Stamp detection is a trap.** Stamp CNNs and color/shape heuristics "never produced a safe signal"; stamp colors "failed to correlate with fee or adjudication in a full-train audit". Silent stamps are the under-determined cases → NEEDS_REVIEW, not a vision problem. (Downgrades the priority of our planned stamp detector substantially.)
- Approvals sourced from sponsor-attestation prose caused a false approval over a "silent warrant page" → approvals should require trusted **intake-form** fields, not letter prose.
- OCR debris fabricates `identity_conflict` (near-duplicate name spellings like Ixokesh/Ikokesh) → name canonicalization + edit-distance dedup before declaring conflicts.
- Waiver-code shortcuts to APPROVED raised catastrophic false approvals; reverted.

From **strobl** (PR #6, local 100.11/150, zero CFAs):
- **Render-first**: text layer used only as diagnostic, never as prediction evidence — the cleanest injection defense.
- Tesseract **sparse-text mode** (PSM 11/12) materially beats block mode on these forms; re-order OCR regions into visual rows then left-to-right before label/value pairing.
- Disciplined data hygiene: frozen stratified tuning/calibration/holdout splits (700/150/150); contaminated cases forced into tuning; final candidate evaluated once on fresh holdout.
- "Learned exceptions" only allowed when they make policy **stricter**, use visible features, and carry no case identity — a clean anti-gaming framing worth reusing in our memo.

From **Abhishek21g** (PR #3, local 113.7/150 = ext 37.1 + class 63.6 + calib 13.0):
- Perf: prefer **embedded page rasters** over re-rasterizing; OCR only pages with almost no trusted spans.
- OCR recipes: header crops recover fee status hidden under stamps; a mid-band + *unsharpened* pass recovers `Observed flags:` lines that sharpening destroys.
- Their receipt-date constant is 2026-07-01 (ours fit at 2026-07-07; both fit train — pick after checking documents for a visible receipt date).
- Their residual failures: invisible risk stamps (~2% of train → now known under-determined), crossed-out DENIED stamps under-detected, fee unreadable under COPY/ARCHIVE overlays.

## Benchmarks (local train scores self-reported in PRs)

| Who | Total | Extraction | Classification | Calibration | CFAs |
| --- | ---: | ---: | ---: | ---: | --- |
| Abhishek21g | 113.7 | 37.1/50 | 63.6/80 | 13.0/20 | ~2% of train (invisible stamps) |
| strobl | 100.11 | — | — | — | 0 |

Reading: 113.7 clears the 105 interview bar with extraction at only 74% and calibration at 65% — both leave headroom. Beating ~115 needs: near-max deterministic classification + the under-determined cases correctly routed to NR + isotonic-grade calibration + stronger extraction (fuzzy enum repair).

## Second wave (surveyed 2026-07-22 — five new PRs #7–#12)

| Who | Total | Extraction | Classification | Calibration | Notes |
| --- | ---: | ---: | ---: | ---: | --- |
| thegoleffect (#9) | **132.44** | 45.80/50 | 69.79/80 | 16.84/20 | Tesseract dual-pass PSM 3+11 at 150 DPI; vocab repair for species/worlds/visas/names + constrained char fixes for dates/IDs; 300-DPI grayscale retry *only* for low-confidence packets with no recovered risk flag |
| dw820 (#10) | 129.21 | 43.42/50 | 68.42/80 | 17.37/20 | RapidOCR (PP-OCR via ONNX) + pypdfium2; treats the embedded text layer as `hidden_text`, trusts only rendered pixels; gated orientation testing (~3x OCR cost saved); flag rules 0.825 precision / 0.638 recall; **HistGradientBoosting+ExtraTrees cost-sensitive adjudicator; ships 5 CFAs** |
| mikeg-cerebras (#12) | 126.7 | 43.6/50 | 66.9/80 | 16.2/20 | Pure rules, no ML in decision path; "escalation ladder" of OCR enhancement/segmentation passes + CNN secondary validation; text counts as evidence only if it demonstrably paints visible pixels inside the crop; 1.4 s/doc |
| arvindcr4 (#8) | 120.6 | 40.5/50 | 64.5/80 | 15.7/20 | OCR variant ensemble (dark-ink threshold, projection-profile deskew, Otsu, 4-orientation fallback, keep best-parsing); provenance-tracked extraction, hidden text quarantined as fraud signal; EV-matrix decision layer pricing packet hygiene; honest 5-fold CV numbers (63.1/15.5) |

Reading: the 126–132 tier shares (a) multi-pass OCR on every weak page — PSM/orientation/threshold variants or a better engine, (b) aggressive vocab repair, (c) selective expensive retries gated on "no flags recovered yet", (d) a cost-sensitive decision layer that tolerates a few CFAs instead of retreating. Independent corroboration of our geometry finding (orientation fallbacks everywhere; nobody needed higher resolution). dw820's render-only trust model (never read the text layer as visible evidence) is the cleanest injection defense seen so far.

## Design updates adopted

1. Add **document census** signal (which doc types present; B-13 missing ⇒ review-leaning) — new, from issue #5.
2. Add **embargo-world rules** (TRAPPIST-1e / Eris Relay deny even DIP-1; Wolf-1061c deny non-DIP) + world→flag extraction inference — verified.
3. **Demote stamp detector** from Phase 4 to "only if CFA analysis demands it, and then review-only (may demote approve→review, never approve/deny alone)" — per afifi-yusuf's negative result and their proposed safe variant.
4. Approvals require intake-form-grade evidence; attestation prose never sufficient alone.
5. OCR plan: Tesseract sparse-text mode first candidate; row-reorder regions; selective OCR; embedded-raster fast path.
6. Adopt strobl-style frozen split hygiene for anything tuned/trained.
