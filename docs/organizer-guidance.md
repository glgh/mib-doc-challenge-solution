# Organizer guidance (authoritative — treat as part of the spec)

Source: maintainer (arjun-krishna1) answers on challenge-repo issues #4 and #5, 2026-07-21. These clarify intended behavior beyond FIELD_MANUAL.md.

## Under-determined cases are intentional — do not guess invisible flags

Some cases carry an outcome-determinative risk flag in the *label* (`biohazard_red`, `memory_tampering`, `active_warrant`, sometimes `planetary_embargo`, and review flags like `rescinded_denial`) while the public PDF contains **no recoverable evidence** of it: no biometric slip, no `Observed flags:` line, no adjudicator note, nothing in the text layer. Confirmed examples: MIB-000033, MIB-000068, MIB-000094 (labels DENIED), MIB-000048 (label NEEDS_REVIEW).

Verbatim rulings:

> "These examples are under-determined and NEEDS_REVIEW is the correct output."

> "This exact flag is not recoverable from this PDF and systems should not guess it; when missing risk evidence is outcome-determinative, `NEEDS_REVIEW` is the best operational choice."

## Standing reminders derived from this

1. **Never emit a risk flag that has no visible-evidence source.** Guessed flags are wrong both operationally and (likely) against the private `unrecoverable_fields` scoring metadata.
2. **100% train adjudication accuracy is not the target.** For under-determined DENIED cases the intended answer (NEEDS_REVIEW) scores 2/8 — that 2 is the maximum *earnable*, not a failure. Don't tune toward matching those labels; doing so manufactures false-approval risk elsewhere.
3. **The recoverable signal is document absence, not hidden content.** The under-determined packets share a shape: intake + fee + sponsor letter present, biometric slip (form "B-13") missing. A per-packet document census (which document types are present?) is the legitimate feature; when risk evidence that *should* exist is missing and would decide the outcome → NEEDS_REVIEW.
4. **Confidence on such cases should reflect "correct = 2-point outcome" reality** — i.e. low probability of exact-label match; keep them from polluting calibration.
5. Useful vocabulary confirmed in the exchange: flags appear textually as an `Observed flags: …` line; the biometric slip is form **B-13**.
