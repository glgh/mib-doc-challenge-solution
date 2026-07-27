"""Shared adversarial-content patterns.

Leaf module (depends only on `re`) so any stage can import it without a cycle —
`packet` (S4) uses it to guard the fee tiers and shape-gate the hidden-echo
filter (row 79), and `render` (S2) needs it to keep injected text from counting
as evidence in the weak-page assessment, but cannot import `packet`. One source
of truth for both.

`INJECTION_RE` matches the corpus's prompt-injection shapes: fake `SYSTEM:`
prompts, `answer key` fields, `ignore visible/previous`-style instructions, and
the decision-with-confidence payload (`APPROVED, 0.99` / `DENIED 0.98`) planted
to be followed as an adjudication. These arrive as hidden text-layer spans or as
white ink that only becomes OCR-visible under autocontrast (MIB-000114 p2). They
are never trusted evidence — visible document evidence always wins over hidden
instructions — so this pattern marks a line as non-evidence wherever it is read.
"""
import re

INJECTION_RE = re.compile(r"system:|answer key|ignore (?:visible|previous)|"
                          r"\b(?:APPROVED|DENIED)\W{0,3}0\.\d\d", re.I)
