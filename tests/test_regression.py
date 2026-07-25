"""One test per historically-observed failure.

Each of these cost real points once. They are cheap to keep and they are the
part of the suite that compounds: every future CFA or mis-repair lands here.
"""
import pytest

from mib import parse, vocab


def test_policy_constants_have_a_single_source():
    """Policy facts (embargo sets, revoked sponsors, the stale cutoff) were once
    copied across the rules engine, the derived signals, and the deleted ML
    features — identical copies, so an edit to one silently missed the others.
    What remains to pin: signals used to be a copy-site too (it inferred
    planetary_embargo off FULL_EMBARGO_WORLDS, which duplicated policy's
    `embargo_world` branch and shadowed it into dead code). The rule lives in
    policy alone now, so the invariant is that signals does not reach for policy
    at all."""
    from mib import signals

    assert not hasattr(signals, "policy"), \
        "signals imports policy again — the embargo rule has a second home"


def test_bands_rung_deskews_before_deshredding(monkeypatch):
    """The `bands` restoration rung must deshred the *deskewed* page, not the raw
    one: `imaging.realign_bands` keys off the printed border's left edge per row,
    and a skewed border is a moving reference. Regression guard for the ordering
    — asserted on call order, so it needs no OCR or real geometry."""
    from mib import imaging
    from mib.stages import render

    deskewed, deshredded, turned = object(), object(), object()
    seen = {}
    monkeypatch.setattr(imaging, "skew_angle", lambda g: 3.0)   # past MIN_SKEW
    monkeypatch.setattr(imaging, "rotate", lambda g, deg: deskewed)
    # The turn rung runs unconditionally, so it must be stubbed too or it would
    # hand this fake page to PIL. Its output is irrelevant here; what matters is
    # that deshred is handed the *deskewed* image and not one of these.
    monkeypatch.setattr(imaging, "turn", lambda g, quarter: turned)

    def fake_realign(base):
        seen["realign_arg"] = base
        return deshredded
    monkeypatch.setattr(imaging, "realign_bands", fake_realign)

    variants = dict(render._restorations(object()))
    assert variants.get("deshred") is deshredded          # deshred is produced
    assert "bands" not in variants                        # under the old name
    assert seen["realign_arg"] is deskewed                # deskew came first


def test_unreadable_risk_line_is_not_repaired_into_no_risk():
    """MIB-000672 (experiments row 18): the B-13 scanned as `Observed fans: =-*`
    / `rant` — truly `active_warrant`. snap matched no flag token and fell
    through to "none", turning scan debris into a positive assertion that no risk
    flag was observed. The case was approved against a truth of DENIED: a
    catastrophic false approval manufactured entirely by a repair step.

    Unreadable and clear must not be the same value. A genuine "none" still reads
    as "none"; debris now reads as nothing at all, and the packet loses its claim
    to flag evidence."""
    assert vocab.snap("observed_flags", "=-*") is None
    assert vocab.snap("observed_flags", "rant") is None
    assert vocab.snap("observed_flags", "none") == "none"
    assert vocab.snap("observed_flags", "active_warrant") == "active_warrant"


def test_an_unreadable_b13_does_not_count_as_flag_evidence():
    """The risk-concealment census asks whether the risk line was *read*, not
    whether a slip was detected. A detected B-13 with an unreadable flag line is
    the concealment shape, so the field must be dropped rather than left as
    debris that still looks like evidence."""
    kv = parse.parse_kv(["FORM B-13", "Case ID: MIB-000672", "Observed fans: =-*"])
    assert kv.get("observed_flags") == "=-*"        # parsed, but meaningless
    from mib.packet import _repair_ocr_kv
    assert "observed_flags" not in _repair_ocr_kv(dict(kv))


def test_sponsor_attestation_prose_is_extracted():
    """The attestation states its facts in sentences, so parse_kv saw nothing on
    it — 273 of 312 dev parse failures came from unparsed lines like these.
    The sentence wraps mid-phrase, so matching must join lines first: 'reactor'
    and 'maintenance.' are on separate lines and 'reactor' is not a purpose."""
    fields = parse.parse_prose([
        "Sponsor Attestation Letter",
        "Sponsor SPN-4560 attests that Aridane Zavoss is expected on Earth for reactor",
        "maintenance.",
        "The sponsor acknowledges responsibility for class XW-2 compliance and immediate",
        "reporting duties.",
    ])
    assert fields == {
        "sponsor_id": "SPN-4560",
        "applicant_name": "Aridane Zavoss",
        "declared_purpose": "reactor maintenance",
        "visa_class": "XW-2",
    }


def test_prose_patterns_do_not_fire_on_unrelated_documents():
    """Each pattern is anchored on attestation wording; a decoy must not match."""
    assert parse.parse_prose([
        "FORM I-8090", "Applicant: Someone Else",
        "The sponsor is expected to comply with SPN-1234 reporting duties.",
    ]) == {}


def test_purpose_is_a_recognized_label():
    """The attestation labels it `Purpose:`; KEY_MAP only had `declared purpose`."""
    assert parse.key_for("Purpose") == "declared_purpose"
    assert parse.parse_kv(["Purpose: research"]).get("declared_purpose") == "research"


def test_damage_markers_are_not_values():
    """The corpus marks destroyed fields in place. These are the document saying
    the value is unrecoverable — which is `unknown`, not an answer. 51 were being
    emitted verbatim, including `[NAME CUT OUT]` as an applicant name."""
    for marker in ("[NAME CUT OUT]", "[REGISTRY LOST]", "[PURPOSE ILLEGIBLE]",
                   "[MAME CUT OUT]", "[PURPOSE NLEGIBLE]"):
        assert not parse.valid_value("applicant_name", marker), marker
        assert not parse.valid_value("declared_purpose", marker), marker
    # A real value that merely contains a bracket is untouched.
    assert parse.valid_value("applicant_name", "Zorx [the Elder]")


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


def test_confusion_weighted_matcher_recovers_ocr_corruption():
    """A flag word survives OCR damage that exact matching drops.

    MIB-000078's B-13 read `Observed flags: bichaxarc_yed` for biohazard_red;
    exact `token in ALL_FLAGS` missed it and the disqualifying flag was lost. The
    confusion-weighted matcher resolves the look-alike substitutions (o->c, z->x,
    r->y) that a plain edit distance would not credit."""
    assert vocab.match_flag_token("bichaxarc_yed") == "biohazard_red"
    assert vocab.match_flag_token("bicharerd_yed") == "biohazard_red"
    assert vocab.match_flag_token("actwe_warant") == "active_warrant"
    assert vocab.match_flag_token("planetary_embargo") == "planetary_embargo"


def test_matcher_rejects_benign_flag_substring_words():
    """A word that is a substring of a flag name is not the flag. The margin guard
    plus length-normalized distance keep 'biometrics'/'sponsor' from posing as
    illegible_biometrics/sponsor_mismatch — the false-positive that would deny a
    clean case."""
    for benign in ("biometrics", "sponsor", "biometric", "status", "reason",
                   "finding", "approved", "denied", "registry", "none"):
        assert vocab.match_flag_token(benign) is None, benign


def test_flag_extraction_survives_label_damage_and_trailing_punctuation():
    """observed_flags reads the flag value, not a specific parsed key, so a
    mangled label ('Chserved flags') or trailing punctuation ('planetary_embargo.'
    from adjudicator prose) no longer drops the flag."""
    from mib import signals
    from mib.packet import SRC_OCR, Packet

    pkt = Packet(case_id="MIB-000078")
    pkt.docs = [(parse.DOC_BIOMETRIC, SRC_OCR,
                 {"_raw": ["Chserved flags: bichaxarc_yed, illegible_biometrics"]})]
    assert signals.observed_flags(pkt) == {"biohazard_red", "illegible_biometrics"}

    note = Packet(case_id="MIB-000121")
    note.docs = [(parse.DOC_ADJUDICATOR, SRC_OCR,
                  {"_raw": ["Finding: DENIED. Reason: Disqualifying risk flag: planetary_embargo."]})]
    assert signals.observed_flags(note) == {"planetary_embargo"}


def test_flag_extraction_does_not_fabricate_from_legend_or_negation():
    """The guards that keep the fuzzy scan from manufacturing a flag: an explicit
    none/clear, a legend that lists the options, and a sentence that negates the
    flag must all yield nothing."""
    from mib import signals
    from mib.packet import SRC_OCR, Packet

    def flags(*lines):
        p = Packet()
        p.docs = [(parse.DOC_BIOMETRIC, SRC_OCR, {"_raw": list(lines)})]
        return signals.observed_flags(p)

    assert flags("Observed flags: none") == set()
    assert flags("Registry Status: clear") == set()
    assert flags("Finding: APPROVED. Reason: cleared of biohazard_red") == set()
    assert flags("Possible flags: biohazard_red | active_warrant | "
                 "memory_tampering | planetary_embargo") == set()
