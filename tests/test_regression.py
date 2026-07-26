"""One test per historically-observed failure.

Each of these cost real points once. They are cheap to keep and they are the
part of the suite that compounds: every future CFA or mis-repair lands here.
"""
from pathlib import Path

import pytest

from mib import parse, vocab

_TRAIN = Path(__file__).resolve().parent.parent.parent / "mib-doc-challenge/data/train"


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


def test_stale_arrival_outranks_an_unknown_fee():
    """The cascade's one positive-evidence-vs-ignorance inversion (row 39):
    fee_unknown used to fire before stale_arrival, so a packet with a real
    stale arrival whose fee read as unknown went to NEEDS_REVIEW. The cell
    "fee genuinely unknown AND stale AND non-DIP" is empty across all 1,000
    train labels — every case that lands there carries a polluted fee read,
    not a real unknown — and 7 of the 8 such train cases are truth DENIED
    (MIB-000031 among them). Denial requires positive evidence; a stale date
    IS positive evidence, and unreadable fees must not launder it away."""
    from mib import policy

    values = {"arrival_date": "2025-11-01", "visa_class": "XW-2",
              "fee_status": "unknown"}
    sig = {"flags": set(), "finding": None, "has_biometric": True,
           "has_flag_evidence": True, "waiver_code": "", "emit_flags": set()}
    decision, branch = policy.adjudicate(values, sig)
    assert (decision, branch) == ("DENIED", "stale_arrival")
    # An unknown visa must still disarm the denial (positive-evidence guard).
    values["visa_class"] = None
    decision, branch = policy.adjudicate(values, sig)
    assert decision == "NEEDS_REVIEW"


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


def test_flag_scan_reads_pages_whose_header_defeated_doc_typing():
    """The flag scan has no doc-type gate: MIB-000656/771/979 print
    `Observed flags: illegible_biometrics` on B-13 pages whose OCR-mangled
    headers type as DOC_OTHER, and the old FLAG_DOC_TYPES gate skipped them
    (BACKGROUND §3: with the gate removed, P=1.00, 94/94, zero false positives).
    Lines below are the mined reads verbatim (771's token is one glyph off and
    resolves through the confusion-weighted matcher)."""
    from mib import signals
    from mib.packet import SRC_OCR, Packet

    mined = [
        ["bee bbe Af ot", "Ae? aes", "Observed flags: illegible_biometrics"],   # 656 p3
        ["Case ID: MIB-O00771", "ce", "Observed flags: Begible_biometrics"],    # 771 p3
        ["Case ID: MIB-000979", "SCAN IMAGE",
         "Observed flags: illegible_biometrics"],                               # 979 p5
    ]
    for lines in mined:
        pkt = Packet()
        pkt.docs = [(parse.DOC_OTHER, SRC_OCR, {"_raw": lines})]
        assert signals.observed_flags(pkt) == {"illegible_biometrics"}, lines
        # The census must agree with the emitted flag, whatever the doc type.
        assert signals.has_flag_evidence(pkt)


def test_damage_markers_on_untyped_pages_do_not_become_flags():
    """The controls for the gate deletion: MIB-000747/506 print a damage marker
    in the flags cell — OCR reads it perfectly, but there is no flag token on
    the paper, so nothing may be emitted from any page type."""
    from mib import signals
    from mib.packet import SRC_OCR, Packet

    for line in ("Pbserved flags: [RISK PANEL MISSING]",     # 747 p5
                 "Observed fags: [RISK PANEL = NG]",         # 506
                 "Observed flags: [RISK PANEL NG)"):
        for dtype in (parse.DOC_BIOMETRIC, parse.DOC_OTHER):
            pkt = Packet()
            pkt.docs = [(dtype, SRC_OCR, {"_raw": [line]})]
            assert signals.observed_flags(pkt) == set(), (dtype, line)


def test_whole_value_flag_rescue_single_read_bars():
    """A labelled `Observed flags:` value shattered past any single token still
    resolves whole (MIB-000595/252, the mode-A-with-worse-OCR class — BACKGROUND
    §3 geometry table). Bars are mined, not guessed: every argmax-false value in
    the train ensemble sits at score<=0.40/margin<=0.08, so single-read
    acceptance at 0.55/0.15 has real clearance."""
    from mib import signals
    from mib.packet import SRC_OCR, SRC_TEXT, Packet

    for value in ("Mogible_ biometrics",     # 595 p3: 0.83, margin 0.48
                  "Bagitie bematics",        # 252 p2: 0.64, margin 0.18
                  "illegible biometrics"):   # space-split: token path is blind to it
        pkt = Packet()
        pkt.docs = [(parse.DOC_OTHER, SRC_OCR,
                     {"_raw": [f"Observed flags: {value}"], "_page_no": 1})]
        assert signals.observed_flags(pkt) == {"illegible_biometrics"}, value

    # A text layer doesn't misread: the same unmatchable value there is
    # genuinely not a flag, so the rescue is OCR-only.
    pkt = Packet()
    pkt.docs = [(parse.DOC_BIOMETRIC, SRC_TEXT,
                 {"_raw": ["Observed flags: Bagitie bematics"], "_page_no": 1})]
    assert signals.observed_flags(pkt) == set()

    # Innocent phrases and explicit none stay silent at any tier.
    for value in ("biometrics ok", "none"):
        pkt = Packet()
        pkt.docs = [(parse.DOC_BIOMETRIC, SRC_OCR,
                     {"_raw": [f"Observed flags: {value}"], "_page_no": 1})]
        assert signals.observed_flags(pkt) == set(), value


def test_whole_value_flag_rescue_needs_quorum_below_single_bar():
    """MIB-000990 p1: no single mangle clears the single-read bar (0.46/0.45),
    but two independent readings argmax to the same flag with margin >=0.10 —
    that agreement is the evidence. One such reading alone must NOT emit."""
    from mib import signals
    from mib.packet import SRC_OCR, Packet

    one = {"_raw": ["Observed flags: Beghie_ ju. ics"], "_page_no": 0}
    two = {"_raw": ["Observed flags: Begibie_|.._ics"], "_page_no": 0}

    pkt = Packet()
    pkt.docs = [(parse.DOC_OTHER, SRC_OCR, one)]
    assert signals.observed_flags(pkt) == set()

    pkt = Packet()
    pkt.docs = [(parse.DOC_OTHER, SRC_OCR, one)]
    pkt.variant_docs = [(parse.DOC_OTHER, two)]
    assert signals.observed_flags(pkt) == {"illegible_biometrics"}


def test_identity_conflict_cleared_by_agreeing_variant_read():
    """MIB-000523 (again): under conf selection the primary registry read
    degraded to `Inout Solkx` and identity_conflict re-fired against the very
    case row 33 fixed. A losing variant that read the name within OCR tolerance
    is evidence of agreement — same principle as has_flag_evidence's
    losing-variant clause. A genuinely different name in every read must still
    conflict."""
    from mib import signals
    from mib.packet import SRC_OCR, Packet

    pkt = Packet(case_id="MIB-000523")
    pkt.docs = [(parse.DOC_REGISTRY, SRC_OCR,
                 {"registry_name": "Inout Solkx", "_raw": []})]
    pkt.variant_docs = [(parse.DOC_REGISTRY,
                         {"registry_name": "Ixoul Solx", "_raw": []})]
    assert not signals.identity_conflict(pkt, {"applicant_name": "Ixoul Solix"})

    pkt.variant_docs = [(parse.DOC_REGISTRY,
                         {"registry_name": "Barnaby Wilkes", "_raw": []})]
    assert signals.identity_conflict(pkt, {"applicant_name": "Ixoul Solix"})


def test_registry_eroded_labels_recover_values():
    """MIB-000293 p0 (row 32): a faint registry scan erodes the two-line labels,
    leaving label tails fused to values and a bare name. The fallback must
    recover all four values; snapping ('Ens Relay' -> 'Eris Relay') and
    validation happen downstream exactly as for labelled reads."""
    from mib import parse

    kv = parse.registry_fallback_kv([
        "Registry Extract", "Nexquell Veemora", "World Ens Relay",
        "Code ANDROMEDAN", "Date: 2026-04-14", "Pacl",
        "Synthetic hiring challenge document",
    ])
    assert kv["registry_name"] == "Nexquell Veemora"
    assert kv["home_world"] == "Ens Relay"
    assert kv["species_code"] == "ANDROMEDAN"
    assert kv["arrival_date"] == "2026-04-14"


def test_struck_value_is_voided_but_a_lookalike_is_not():
    """MIB-000514/000614: the fee receipt reads `unpaid`, but the value is crossed
    out with a red strikethrough — the document voiding its own value. `_void_struck`
    drops the struck field. The match is equality, never substring, so a struck
    `unpaid` must not also void a `paid` (one edit away, opposite meaning) elsewhere
    on the page; and `_raw` is preserved so flags/manual-corrections still read."""
    from mib.packet import _void_struck

    kv = {"fee_status": "unpaid", "visa_class": "DIP-1", "_raw": ["MIB Fee Receipt"]}
    _void_struck(kv, ["unpaid"])
    assert "fee_status" not in kv          # the crossed-out value did not source
    assert kv["visa_class"] == "DIP-1"     # unrelated field untouched
    assert kv["_raw"] == ["MIB Fee Receipt"]

    keep = {"fee_status": "paid"}
    _void_struck(keep, ["unpaid"])         # equality, not substring
    assert keep["fee_status"] == "paid"

    inline = {"fee_status": "unpaid"}
    _void_struck(inline, ["Fee Status: unpaid"])   # inline "Key: Value" strike
    assert "fee_status" not in inline


def test_red_strikethrough_on_the_fee_receipt_is_detected():
    """S1 reads the strike from the vector layer (the text layer still says
    `unpaid`). MIB-000514 page 2 is a fee receipt whose `unpaid` is struck."""
    pdf = _TRAIN / "MIB-000514.pdf"
    if not pdf.exists():
        pytest.skip("train corpus not present")
    from mib.stages import extract

    with extract.open_document(pdf) as doc:
        pages = extract.pages(doc)
    assert "unpaid" in {s for p in pages for s in p.struck}


def test_struck_fee_does_not_source_fee_status():
    """End to end through the merge: a struck fee value never becomes the emitted
    `fee_status`, so it can no longer drive a false `fee_unpaid` denial. Without the
    strike the same page supplies `unpaid`, which is the pre-change behavior."""
    from mib.records import Page
    from mib import packet

    lines = ["MIB Fee Receipt", "Case ID: MIB-000999", "Fee Status", "unpaid",
             "Amount", "$809.00", "Waiver Code", "N/A"]
    struck = packet.assemble([Page(page_no=0, visible_lines=lines, struck=["unpaid"])],
                             {}, "MIB-000999")
    assert packet.merge_fields(struck).get("fee_status") is None

    plain = packet.assemble([Page(page_no=0, visible_lines=lines)], {}, "MIB-000999")
    assert packet.merge_fields(plain).get("fee_status") == "unpaid"


def test_registry_fallback_ignores_boilerplate_and_debris():
    """The bare-name capture must not read headers, footers, or OCR debris as a
    registry name, and short garbage must not snap into a home world."""
    from mib import parse

    kv = parse.registry_fallback_kv([
        "Registry Extract", "Planetary Registry", "Sample Denial",
        "Synthetic hiring challenge document", "Gab EP Se", "bee I", "e",
    ])
    assert "registry_name" not in kv
    assert "home_world" not in kv


def test_identity_conflict_tolerates_ocr_misread_of_registry_name():
    """MIB-000523 (row 33): an OCR-read registry name one glyph off the emitted
    applicant name is agreement, not conflict. A text-layer near-miss keeps
    exact-match semantics, and genuinely different names still conflict even
    from OCR (every true conflict pair mined at ratio <= 0.5)."""
    from mib import signals
    from mib.packet import SRC_OCR, SRC_TEXT, Packet

    def conflict(reg_name, applicant, source):
        p = Packet()
        p.docs = [(parse.DOC_REGISTRY, source, {"registry_name": reg_name})]
        return signals.identity_conflict(p, {"applicant_name": applicant})

    assert not conflict("Ixoul Solx", "Ixoul Solix", SRC_OCR)      # misread
    assert conflict("Ixoul Solx", "Ixoul Solix", SRC_TEXT)         # text is exact
    assert conflict("Oritari Ixovara", "Zarix Ixotari", SRC_OCR)   # real conflict


def test_sepless_label_lines_parse_with_corroboration():
    """Row 50: the [:.;] separator is the smallest glyph on a label line and
    dies first. A label head claims the rest of the line only when the value
    corroborates the claimed field (mined lines: 073 'Fee Status waved', 125's
    receipt, 866 p4's eroded Applicant label)."""
    kv = parse.parse_kv(["Fee Status waved"])
    assert kv.get("fee_status") == "waved"          # snaps to 'waived' downstream
    kv = parse.parse_kv(["Apphcant Onvoss Mesh"])
    assert kv.get("applicant_name") == "Onvoss Mesh"
    kv = parse.parse_kv(["Case ID MIB-000243"])
    assert kv.get("case_id") == "MIB-000243"


def test_sepless_claim_rejects_prose_and_bait_shapes():
    """Row 50's first cut claimed 'Sponsor SPN-5086 attests that ...' as a
    sponsor_id, poisoning the text-layer field with an 11-token value and
    handing the vote to decoy-page bait ids (SPN-0000/SPN-4040, 30+ cases).
    Prose is not a label line: values cap at 4 tokens, and a head must match a
    label of the SAME token count (a lost separator does not delete label
    words)."""
    kv = parse.parse_kv(["Sponsor SPN-5086 attests that Miradane Ludane is expected on Earth"])
    assert "sponsor_id" not in kv
    kv = parse.parse_kv(["Applicant is expected on Earth for xenobotany"])
    assert "applicant_name" not in kv
    # damage markers never corroborate
    kv = parse.parse_kv(["Fee Status [FEE STATUS OBSCURED]"])
    assert "fee_status" not in kv
