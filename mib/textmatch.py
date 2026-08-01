"""Normalized text matching, mirroring the scorer's comparison semantics.

`normalize` is the same whitespace-collapse + casefold the official evaluator
applies before comparing a field (evaluate.py:normalize), so "is this value
present in this text?" is asked on the same terms the score is settled on.
"""
import re

# Values that carry no information and must never count as "found".
EMPTY = {"", "none", "unknown", "null", "n/a", "spn-0000", "1900-01-01"}


def normalize(value):
    return " ".join(str(value or "").strip().split()).casefold()


def plausible_misread(a, b):
    """Same-length ids differing in at most one character: b could be an OCR
    misread of a (single-glyph substitution, the scanner's dominant error)."""
    return len(a) == len(b) and sum(x != y for x, y in zip(a, b)) <= 1


def present(value, text):
    """Is `value` in `text`, on token boundaries? None if there is nothing to find.

    Boundaries are not optional here: "paid" is a substring of "unpaid", so a
    plain containment test reports every unpaid fee as recoverable.
    """
    v = normalize(value)
    if v in EMPTY:
        return None
    return re.search(rf"(?<![a-z0-9]){re.escape(v)}(?![a-z0-9])", text) is not None


def trusted_text(page_dicts):
    """The text we are allowed to source an answer from: visible + OCR, never hidden."""
    return normalize(" ".join(
        line for p in page_dicts for line in p["visible_lines"] + p["ocr_lines"]))


def hidden_text(page_dicts):
    """The hidden text-layer content, reconstructed for mining only — never evidence.

    No current callers: hidden spans are barred from sourcing any value (see
    `trusted_text`) and guarded by `test_hidden_text_cannot_make_the_output_more_assertive`.
    Retained for the untried STATUS.md question-11 lever — mining hidden content
    as a FLAG-only signal (e.g. a hidden SPN/date that conflicts with the visible
    value), never as a value source or a branch flip toward the hidden value.
    """
    return normalize(" ".join(line for p in page_dicts for line in p["hidden_lines"]))


def unsourced_flags(risk_flags, text):
    """Emitted flags that name nothing in the trusted text — i.e. inferred, not read.

    Shared by the invariant test and by the fixture builder, so the fixture can
    guarantee it actually contains cases the invariant is capable of catching.
    """
    return sorted(f for f in set(str(risk_flags).split("|")) - {"none"}
                  if not present(f, text))
