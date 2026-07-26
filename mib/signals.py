"""Derived fraud signals (taxonomy and validation status: docs/BACKGROUND.md §3).

Every function here turns raw packet evidence into a named signal consumed by
policy. Signals must be validated on train before policy may act on them.
"""
import difflib
import re

from . import parse, vocab
from .packet import SRC_OCR
from .parse import SPONSOR_RE, norm_name

_TOKEN_SPLIT = re.compile(r"[\s|,;:/()\[\]]+")
_LEGEND_RE = re.compile(
    r"\b(option|one of|any of|possible|valid value|choose|list of|e\.g\.)\b", re.I)
_NEGATION = {"no", "not", "without", "none", "negative", "clear", "cleared", "absent"}
_STRIP = " .,;:|/()[]'\"-"


def _flags_in_line(line):
    """Risk flags asserted on one line, matched fuzzily, or empty.

    The guards keep an OCR-mangled flag *value* from being manufactured out of a
    legend that merely lists the options, or a sentence that negates the flag
    ('cleared of biohazard_red'). Fuzzy matching itself is in mib.vocab.
    """
    low = line.strip().lower()
    if _LEGEND_RE.search(low):
        return set()
    tokens = [t.strip(_STRIP) for t in _TOKEN_SPLIT.split(low)]
    found = set()
    for i, tok in enumerate(tokens):
        if not tok or any(w in _NEGATION for w in tokens[max(0, i - 2):i]):
            continue
        flag = vocab.match_flag_token(tok)
        if flag:
            found.add(flag)
    return found if len(found) <= 3 else set()   # >3 on one line reads as a legend


def observed_flags(packet):
    """Risk flags stated on the flag-bearing documents, read value-first. (§5)

    Matched fuzzily and confusion-weighted (mib.vocab.match_flag_token) so the
    flag survives OCR damage to either the label ('Observed flags'->'Chserved
    flags') or the value ('biohazard_red'->'bichaxarc_yed') — the same
    'values outlast labels' property S2's evidence_score relies on. `_raw` holds
    only visible + OCR text (assemble never stores hidden_lines there), so this
    reads trusted evidence by construction; the injection differential tests hold.

    Losing OCR variants (packet.variant_docs) are scanned too, as a plain union:
    a flag legible only in the deskewed-but-not-chosen reading is still visible
    evidence. Measured on the hard-set ensemble (experiments/flag_probe.py):
    every flag recovered this way was true, none hallucinated — the per-line
    legend/negation guards hold on garbled variants as well.

    No doc-type gate. Flags used to be scanned only on adjudicator/biometric/
    registry docs, which skipped any B-13 whose OCR-mangled header defeated
    `detect_doc_type` (MIB-000656/771/979 print the token on pages typed OTHER).
    Label mining (BACKGROUND §3): whenever the token is legible in any read,
    truth carries the flag — P=1.00, zero false positives, with the gate removed
    entirely. The per-line guards, not the gate, are the safety mechanism.

    """
    flags = set()
    for _dtype, _src, kv in packet.docs:
        for line in kv.get("_raw", []):
            flags |= _flags_in_line(line)
    for _dtype, kv in packet.variant_docs:
        for line in kv.get("_raw", []):
            flags |= _flags_in_line(line)
    return flags


def has_flag_evidence(packet, observed=None):
    """Whether the risk line was actually read — a flag or an explicit
    'none'/'clear' — versus unreadable debris. Lets the risk-concealment census in
    policy tell 'flags: none' from 'flags: <unreadable>'; unreadable is not clear.

    A losing variant that read the risk line counts: the evidence was legible in
    some reading of the slip, even if `best_read` preferred another.

    The positive clause shares `observed_flags`'s widened scan (any doc, any
    variant), so the census can never contradict an emitted flag. The negative
    clause — an explicit 'flags: none/clear' — stays restricted to biometric-typed
    readings: widening it could flip `b13_census` into `clean_approve`, the
    CFA-risk direction, and is unmeasured.
    """
    if observed is None:
        observed = observed_flags(packet)
    if observed:
        return True
    bios = [packet.biometric] + [kv for d, kv in packet.variant_docs
                                 if d == parse.DOC_BIOMETRIC]
    for bio in bios:
        if bio.get("observed_flags") is not None:   # parsed key present (incl. 'none')
            return True
        for line in bio.get("_raw", []):
            low = line.lower()
            if "flag" in low and re.search(r"\b(none|clear)\b", low):
                return True
    return False


def sponsor_mismatch(packet, values):
    """Attestation letter disagrees with intake on sponsor id or applicant. (§3, partial)"""
    letter = packet.sponsor_letter
    if not letter:
        return False
    att_text = " ".join(letter.get("_raw", []))
    att_spn = SPONSOR_RE.search(att_text)
    if att_spn and values.get("sponsor_id") and att_spn.group(0) != values["sponsor_id"]:
        return True
    m = re.search(r"attests that ([A-Z][A-Za-z]+(?: [A-Z][A-Za-z]+)+)", att_text)
    if m and values.get("applicant_name") and \
            norm_name(m.group(1)) != norm_name(values["applicant_name"]):
        return True
    return False


def identity_conflict(packet, values):
    """Registry name disagrees with the applicant name we actually emit. (§2, partial)

    Compares against the merged value, not the raw intake kv, so a manual
    correction or a precedence choice that already fixed applicant_name also
    clears the conflict — otherwise this flag can fire against, and contradict,
    the very name the record reports.

    OCR tolerance ('text layers don't misread', same principle as the case-id
    decoy filter): a registry name read off pixels that is a near-miss of the
    emitted name is evidence of AGREEMENT, not conflict — MIB-000523's
    'Ixoul Solx' vs 'Ixoul Solix' sent a clean APPROVED to review. The 0.75
    cutoff is max-margin on train: every true identity_conflict pair sits at
    ratio <= 0.5 (genuinely different names), the lone OCR misread at 0.947,
    and the band between is empty (83 mismatching pairs mined, row 33).
    Text-layer registry names keep exact-match semantics.
    """
    reg_name = packet.registry.get("registry_name")
    name = values.get("applicant_name")
    if not reg_name or not name or norm_name(reg_name) == norm_name(name):
        return False
    source = next((src for dtype, src, _kv in packet.docs
                   if dtype == parse.DOC_REGISTRY), None)
    if source == SRC_OCR and difflib.SequenceMatcher(
            None, norm_name(reg_name), norm_name(name)).ratio() >= 0.75:
        return False
    return True


def adjudicator_finding(packet):
    """Explicit decision on a Manual Adjudicator Note — highest-trust evidence. (validated)"""
    adjudicator = packet.adjudicator
    if not adjudicator:
        return None
    text = " ".join(adjudicator.get("_raw", []))
    m = re.search(r"Finding:\s*(APPROVED|DENIED|NEEDS_REVIEW)", text)
    return m.group(1) if m else None


def waiver_code(packet):
    """First waiver code present on any document."""
    code = next((kv.get("waiver_code") for _, _, kv in packet.docs if kv.get("waiver_code")), "")
    code = (code or "").strip()
    return "" if code.lower() in ("", "n/a", "none") else code


def derive(packet, values):
    """All signals as a dict; single entry point for policy and diagnostics.

    Two flag sets, deliberately distinct. `flags` is what policy acts on:
    observed evidence plus policy-level inferences. `emit_flags` is only the
    observed subset — the flags with a visible-evidence source. organizer
    guidance §1 forbids emitting an inferred flag, so the inference
    (planetary_embargo from an embargo world, sponsor_mismatch, identity_conflict)
    still drives the decision but is never written to risk_flags.
    """
    observed = set(observed_flags(packet))
    flags = set(observed)
    if sponsor_mismatch(packet, values):
        flags.add("sponsor_mismatch")
    if identity_conflict(packet, values):
        flags.add("identity_conflict")
    # No planetary_embargo inference here. It used to be added for any
    # FULL_EMBARGO_WORLDS origin, which duplicated policy's `embargo_world`
    # branch and — being one position earlier in the cascade — shadowed it into
    # dead code: 0 of 700 dev cases ever reached `embargo_world`, and because the
    # fitter never saw a sample the branch was absent from confidence_table.json
    # and silently answered from the hand-set fallback.
    #
    # The 31 full-embargo dev cases split 15 / 8 / 8: fifteen carry an *observed*
    # planetary_embargo and still deny via `disqualifying_flag`, eight are settled
    # earlier by an adjudicator finding, and eight had no observed flag at all —
    # those are the ones the inference was carrying, and they now land on
    # `embargo_world`, the branch actually written for them. Same decision either
    # way (both deny), so this is attribution and calibration, not points.
    #
    # Deleting it leaves one home for the rule and drops a backwards
    # signals -> policy import.
    return {
        "flags": flags,
        "emit_flags": observed,
        "finding": adjudicator_finding(packet),
        "waiver_code": waiver_code(packet),
        "has_biometric": packet.has_doc(parse.DOC_BIOMETRIC),
        # A B-13 we could not read the flag line from is evidence of nothing.
        # `has_biometric` only says a slip was detected; the risk-concealment
        # census is about whether its risk line was actually read, so policy
        # needs to tell "flags: none" from "flags: <unreadable>".
        "has_flag_evidence": has_flag_evidence(packet, observed),
        "scan_only_pages": packet.scan_only_pages,
    }
