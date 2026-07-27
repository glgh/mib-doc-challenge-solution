"""Grammar of the three regex-shaped schema fields — case_id, sponsor_id, arrival_date.

One home for the *shape* of each field token, so the digit counts and the
`MIB-`/`SPN-` prefixes are spelled exactly once. Callers pick a tier by intent:

  FIND_*      \\b-anchored — is a well-formed token present inside messy text?
              (document parsing in `parse`, OCR-variant scoring in `stages.render`)
  VALIDATE_*  ^…$-anchored — is *this* value exactly well-formed? (the emit-stage
              schema safety net, which the official evaluator hard-fails without)
  EXTRACT_*   unanchored, capturing — pull the payload out of surrounding debris.
  coerce_*    OCR-tolerant — rebuild a value from a mangled read (accept the
              scanner's lookalike glyphs, repair, re-check the shape), or None.

The `\\b` find and `^…$` validate forms answer different questions (present in
messy text vs. exactly well-formed), so they stay distinct — but both derive from
the one shared fragment below, so the shape can't drift between them.

Tier 2 (`coerce_*`) is the OCR-tolerant path. `mib.vocab.snap` delegates its
case_id / sponsor_id / arrival_date arms here; the fuzzy *vocabulary* snapping
(species, home world, purpose, flags) stays in `vocab`, next to those closed
vocabularies. Nothing outside this module should re-spell `MIB-\\d{6}`.
"""
import re

from .textmatch import plausible_misread

# Numeric payloads — the counts live once so a widened id can't half-migrate.
_CASE_NUM = r"\d{6}"
_SPN_NUM = r"\d{4}"

CASE_ID = rf"MIB-{_CASE_NUM}"
SPONSOR = rf"SPN-{_SPN_NUM}"
DATE = r"\d{4}-\d{2}-\d{2}"

# Tier 1a — is a well-formed token present inside messy text?
FIND_CASE_ID = re.compile(rf"\b{CASE_ID}\b")
FIND_SPONSOR = re.compile(rf"\b{SPONSOR}\b")
FIND_DATE = re.compile(rf"\b{DATE}\b")

# Tier 1b — is *this* value exactly well-formed? (emit schema safety net)
VALIDATE_CASE_ID = re.compile(rf"^{CASE_ID}$")
VALIDATE_SPONSOR = re.compile(rf"^{SPONSOR}$")
VALIDATE_DATE = re.compile(rf"^{DATE}$")

# Tier 1c — unanchored capture of the numeric payload, for pulling an id out of
# debris (emit._repair_case_id): "…MIB-000123…" -> "000123".
EXTRACT_CASE_ID = re.compile(rf"MIB-({_CASE_NUM})")


# ── Tier 2 — OCR-tolerant coercion ──────────────────────────────────────────
# Rebuild a well-formed value from a mangled read, or None. Each entity accepts
# the lookalike glyphs the scanner actually produces in its prefix/cell, then
# normalizes through a translate table and re-checks the shape. Callers pass an
# already-trimmed read; these strip defensively so they also stand alone.

# Common OCR confusions applied before matching a case-id cell.
_CASE_DIGIT_FIXES = str.maketrans({"O": "0", "o": "0", "l": "1", "I": "1", "S": "5", "B": "8"})
_CASE_TOLERANT = re.compile(r"M[iI1l]B[-–—:\s]*([0-9OolIB]{6})", re.IGNORECASE)

# Inside an SPN-#### cell every character is a digit, so an alpha there is a
# digit misread — the distress maps 5->S, 7->T, 2->Z, 6->G, 1->I/l/i/|, 0->O/Q,
# 8->B (train ensemble, 2026-07-27 key-oracle worklist). Translating the four
# cell chars glyph-locally lets a mangled read survive `snap` deletion and reach
# the cross-variant plurality vote, which recovers the id no single read holds
# (MIB-000140: ST73/STT3/SIT3/S773 -> 5773; MIB-000190: SPH4530 -> 4530). This is
# position-local and never steers toward the revoked list: a garble becomes a
# revoked id only when a page genuinely prints one. Only confusions carried by a
# measured recovery (train replay 2026-07-27) plus standard-safe case variants:
# S->5 (743), T->7 (543 `OOOT`), B->8 (383 `SO5B`), O/Q->0, l/I/i/|->1, G->6.
# Z->2 was tried and dropped: it earns no fix and manufactures wrong votes from
# noise cells (MIB-000784's `Z283`/`Z253` are misreads of a `2263` truth). The
# one accepted casualty is MIB-000427 (`T687`->`7687`), the T->7 tradeoff that
# also earns 543; regression-guarded.
_SPN_CELL_FIXES = str.maketrans({
    "O": "0", "o": "0", "Q": "0",
    "l": "1", "I": "1", "i": "1", "|": "1",
    "S": "5", "T": "7", "B": "8", "G": "6",
})
# Prefix tolerates the N->H/M and P->F confusions (`SPH4530`, `sPN`); the cell
# class is deliberately loose (letters that look like digits) because the SPN
# prefix already anchors the match.
_SPONSOR_TOLERANT = re.compile(r"[Ss5][PpFf][NnHhMm][-–—:.\s]*([0-9OoQlIi|STBG]{4})")
_FOUR_DIGITS = re.compile(_SPN_NUM)

_DATE_TOLERANT = re.compile(r"(\d{4})[-–—/.](\d{2})[-–—/.](\d{2})")


def coerce_case_id(value):
    """A well-formed case id rebuilt from an OCR-mangled read, or None. Accepts
    the MIB prefix's lookalike glyphs, then digit-translates the 6-char cell."""
    m = _CASE_TOLERANT.search((value or "").strip())
    return f"MIB-{m.group(1).translate(_CASE_DIGIT_FIXES)}" if m else None


def coerce_sponsor_id(value):
    """A well-formed sponsor id rebuilt from a mangled read, or None.

    Every survivor of the loose match is required to translate to exactly four
    digits. (The row-69 fabrication guard that used to reject repairs landing on
    revoked ids was removed on user call 2026-07-26.)"""
    m = _SPONSOR_TOLERANT.search((value or "").strip())
    if not m:
        return None
    cell = m.group(1).translate(_SPN_CELL_FIXES)
    if not _FOUR_DIGITS.fullmatch(cell):
        return None
    return f"SPN-{cell}"


def coerce_arrival_date(value):
    """A YYYY-MM-DD date pulled from a mangled read, or None.

    Visas run <=180 days from a 2026-era receipt, so a year >= 2028 is
    future-impossible: one glyph off 2026 means the scanner misread the year (the
    6->8 confusion is systematic in the corpus). Past years get no such repair —
    2020 or 2024 is always a *plausible* stale date, and rewriting a genuine one
    would un-stale a legitimate denial."""
    m = _DATE_TOLERANT.search((value or "").strip())
    if not m:
        return None
    year = m.group(1)
    if int(year) >= 2028 and plausible_misread(year, "2026"):
        year = "2026"
    return f"{year}-{m.group(2)}-{m.group(3)}"
