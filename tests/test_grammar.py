"""Characterization pins for the id/date OCR-coercion grammar.

These lock the *documented* repair behavior of the case_id / sponsor_id /
arrival_date coercions (the anchors cited in mib/vocab.py's block comments)
before that logic moves from vocab.snap into mib.grammar, and guard it after.
The public entry (vocab.snap) and the extracted grammar.coerce_* are asserted to
agree, so the delegation stays faithful.
"""
import re

import pytest

from mib import grammar, vocab

CASE_ID = [
    ("MIB-000123", "MIB-000123"),          # clean read passes through
    ("MlB-OOOl23", "MIB-000123"),          # prefix + O/l cell glyphs -> digits
    ("MIB-T00000", "MIB-700000"),          # shared digit-cell map: T->7
    ("MIB-G00000", "MIB-600000"),          # G->6, from the sponsor-measured set
    ("MIB-0Q0000", "MIB-000000"),          # Q->0
    ("MIB-00A000", None),                  # a glyph the map can't repair -> None, never malformed
    ("nothing here", None),                # no MIB anchor -> nothing to coerce
]
SPONSOR_ID = [
    ("SPN-4040", "SPN-4040"),              # clean read
    ("SPH4530", "SPN-4530"),               # MIB-000190: N->H prefix confusion
    ("garbage", None),
]
ARRIVAL_DATE = [
    ("2026-05-01", "2026-05-01"),          # clean read
    ("2028-11-30", "2026-11-30"),          # 6->8 year misread, one glyph off 2026
    ("2099-01-01", "2099-01-01"),          # future but NOT a 1-glyph misread -> kept
    ("2024-01-01", "2024-01-01"),          # plausible past date -> never un-staled
    ("no date", None),
]


@pytest.mark.parametrize("field,cases,coerce", [
    ("case_id", CASE_ID, getattr(grammar, "coerce_case_id", None)),
    ("sponsor_id", SPONSOR_ID, getattr(grammar, "coerce_sponsor_id", None)),
    ("arrival_date", ARRIVAL_DATE, getattr(grammar, "coerce_arrival_date", None)),
])
def test_id_date_coercion(field, cases, coerce):
    for raw, expected in cases:
        assert vocab.snap(field, raw) == expected, (field, raw)
        if coerce is not None:                    # present once step 2 lands
            assert coerce(raw) == expected, (field, raw)


WELL_FORMED_CASE = re.compile(r"^MIB-\d{6}$")


@pytest.mark.parametrize("raw", [
    "MIB-00i456", "MIB-0b0000", "MIB-0L0000", "mib-oolIiB",  # IGNORECASE admits these glyphs
    "MIB-00A000", "MIB-ZZZZZZ", "MIB-", "garbage", "", None,
])
def test_coerce_case_id_never_returns_a_malformed_value(raw):
    """The contract: coerce_case_id returns None or an exactly-well-formed id.

    The case regex is re.IGNORECASE, so its cell class admits case-folded glyphs
    (i/b/L) the translate table doesn't repair; the re-validation guarantees such
    a cell yields None rather than a partially-repaired `MIB-00i456`-style value."""
    out = grammar.coerce_case_id(raw)
    assert out is None or WELL_FORMED_CASE.match(out), (raw, out)
