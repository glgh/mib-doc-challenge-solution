"""One test per historically-observed failure.

Each of these cost real points once. They are cheap to keep and they are the
part of the suite that compounds: every future CFA or mis-repair lands here.
"""
import pytest

from mib import parse, vocab


def test_ocr_period_separator_is_parsed_as_a_key_value_line():
    """MIB-000161 (experiments row 5): Tesseract read the colon in 'Observed
    flags:' as a period. The strict-colon parser dropped the line, the
    active_warrant flag went unseen, and the case became a catastrophic false
    approval."""
    kv = parse.parse_kv(["FORM B-13", "Observed flags. active_warrant"])
    assert kv.get("observed_flags") == "active_warrant"


def test_ocr_key_typos_still_resolve():
    """Fuzzy key matching (cutoff 0.8) covers common OCR debris."""
    assert parse.key_for("Observed fIags") == "observed_flags"
    assert parse.key_for("Case 1D") == "case_id"
    assert parse.key_for("a much longer line that is clearly not a field label") is None


def test_digit_repair_never_fabricates_a_revoked_sponsor():
    """A revoked sponsor id triggers a hard denial, so digit translation must not
    invent one: 'SPN-Ol39' -> SPN-0139 would manufacture a denial from OCR noise.
    Exact digits are required before a revoked match counts."""
    from mib.policy import REVOKED_SPONSORS
    assert "SPN-0139" in REVOKED_SPONSORS
    assert vocab.snap("sponsor_id", "SPN-Ol39") is None
    # Repairs that do not land on a revoked id stay useful for extraction points.
    assert vocab.snap("sponsor_id", "SPN-58O9") == "SPN-5809"


@pytest.mark.xfail(strict=True, reason=(
    "mib/vocab.py:29 maps S->5 in _DIGIT_FIXES, but the capture class at "
    "mib/vocab.py:58 is [0-9OolIB]{4} and excludes S, so the mapping is dead and "
    "a '5' misread as 'S' is unrepairable."))
def test_digit_repair_handles_s_for_five():
    assert vocab.snap("sponsor_id", "SPN-S809") == "SPN-5809"


def test_unpaid_is_never_reconstructed_by_edit_distance():
    """'unpaid' forces a denial and sits one edit from 'paid', so it must be read
    verbatim; anything close-but-not-exact degrades to 'unknown'."""
    assert vocab.snap("fee_status", "unpajd") == "unknown"
    assert vocab.snap("fee_status", "unpaid") == "unpaid"
    assert vocab.snap("fee_status", "paíd") == "paid"


def test_snapping_does_not_corrupt_in_vocabulary_values():
    assert vocab.snap("home_world", "Kepler-186f") == "Kepler-186f"
    assert vocab.snap("home_world", "Keplerl86f") == "Kepler-186f"
    assert vocab.snap("declared_purpose", "an unseen purpose") == "an unseen purpose"


def test_unrepairable_values_are_dropped_not_passed_through():
    """Deleting a value snapping cannot vouch for is deliberate, not an oversight.

    It reads like a bug: the module docstring promises unseen values pass through,
    and only declared_purpose actually does. It was measured as a fix and was not
    one — passthrough cost 0.08 dev points, because deletion was acting as a
    quality filter that let a cleaner copy on another document supply the value
    (0.04 even after per-field source preference landed).

    The intended upside was unseen private-set values surviving, and that upside
    does not exist: 1,000 train cases yield exactly 13 home worlds and 12 species,
    and mib/vocab.py's lists are those enumerations. A fourteenth world would be
    expected ~77 times in a sample that size, so the universe is closed.
    """
    assert vocab.snap("home_world", "Antares Reach IV") is None
    assert vocab.snap("species_code", "ZZ_UNSEEN_SPECIES") is None
    # declared_purpose keeps its passthrough: it is free text, not an enumeration.
    assert vocab.snap("declared_purpose", "an unseen purpose") == "an unseen purpose"
