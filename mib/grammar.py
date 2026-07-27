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
# Rebuild a well-formed value from a mangled read, or None. The scanner misreads
# a digit as a lookalike LETTER, and does so the same way whichever field the
# cell sits in — so the letter->digit confusion is modelled ONCE, and both the
# case-id and sponsor-id cells repair through the one shared table. Each coerce
# matches its field-specific prefix, translates the captured cell, then
# RE-VALIDATES to the exact digit count (\d{6} / \d{4}): a glyph the map does not
# repair yields None, never a malformed value. Callers pass an already-trimmed
# read; these strip defensively so they also stand alone.
#
# The map is MAXIMAL — every observed letter->digit confusion, including the weak
# Z->2 (user call 2026-07-27, reversing the row-81 rejection). Pooling misread
# evidence across both fields recovers more ids; the accepted price is that a
# noise cell can vote a wrong digit (Z->2 re-breaks MIB-000784's `2263` read, as
# experiments row 81 records). Re-validation still blocks any malformed emit, and
# the upstream cross-variant vote / filename anchor absorb single wrong reads.
# Translation is position-local and never steers toward the revoked list: a
# garble becomes a revoked id only when a page genuinely prints one (the
# fabrication guard was removed on user call 2026-07-26).
_DIGIT_CELL_FIXES = {
    "O": "0", "o": "0", "Q": "0",
    "l": "1", "I": "1", "i": "1", "|": "1",
    "S": "5", "T": "7", "Z": "2", "B": "8", "G": "6",
}
_DIGIT_CELL_TABLE = str.maketrans(_DIGIT_CELL_FIXES)
# One alphabet, spelled once: the capture classes are DERIVED from the map keys,
# so a cell class can never drift from the glyphs the table actually repairs.
_CELL = "0-9" + re.escape("".join(_DIGIT_CELL_FIXES))

# Only the numeric cell is shared. The prefixes anchor their fields differently:
# the MIB prefix tolerates its I/1/l middle glyph (and case, via re.IGNORECASE);
# the SPN prefix tolerates N->H/M and P->F (`SPH4530`, `sPN`).
_CASE_TOLERANT = re.compile(rf"M[iI1l]B[-–—:\s]*([{_CELL}]{{6}})", re.IGNORECASE)
_SPONSOR_TOLERANT = re.compile(rf"[Ss5][PpFf][NnHhMm][-–—:.\s]*([{_CELL}]{{4}})")
_SIX_DIGITS = re.compile(_CASE_NUM)
_FOUR_DIGITS = re.compile(_SPN_NUM)

_DATE_TOLERANT = re.compile(r"(\d{4})[-–—/.](\d{2})[-–—/.](\d{2})")


def coerce_case_id(value):
    """A well-formed case id rebuilt from an OCR-mangled read, or None.

    Accepts the MIB prefix's lookalike glyphs, translates the 6-char cell through
    the shared digit-cell table, then re-checks the shape — the return matches
    ``^MIB-\\d{6}$`` or is None, never a partially-repaired value."""
    m = _CASE_TOLERANT.search((value or "").strip())
    if not m:
        return None
    cell = m.group(1).translate(_DIGIT_CELL_TABLE)
    if not _SIX_DIGITS.fullmatch(cell):
        return None
    return f"MIB-{cell}"


def coerce_sponsor_id(value):
    """A well-formed sponsor id rebuilt from a mangled read, or None.

    Every survivor of the loose match must translate to exactly four digits. (The
    row-69 fabrication guard that used to reject repairs landing on revoked ids
    was removed on user call 2026-07-26.)"""
    m = _SPONSOR_TOLERANT.search((value or "").strip())
    if not m:
        return None
    cell = m.group(1).translate(_DIGIT_CELL_TABLE)
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
