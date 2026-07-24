"""Derived fraud signals (taxonomy and validation status: docs/fraud-signals.md).

Every function here turns raw packet evidence into a named signal consumed by
policy. Signals must be validated on train before policy may act on them.
"""
import re

from . import parse, vocab
from .parse import SPONSOR_RE, norm_name

# Flags are asserted on these documents; scanning others invites false positives
# from decoys and form legends. (B-13 slip, registry extract, adjudicator note.)
FLAG_DOC_TYPES = (parse.DOC_ADJUDICATOR, parse.DOC_BIOMETRIC, parse.DOC_REGISTRY)
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
    """
    flags = set()
    for dtype, _src, kv in packet.docs:
        if dtype in FLAG_DOC_TYPES:
            for line in kv.get("_raw", []):
                flags |= _flags_in_line(line)
    return flags


def has_flag_evidence(packet):
    """Whether the B-13 slip's risk line was actually read — a flag or an explicit
    'none'/'clear' — versus unreadable debris. Lets the risk-concealment census in
    policy tell 'flags: none' from 'flags: <unreadable>'; unreadable is not clear.
    """
    bio = packet.biometric
    if bio.get("observed_flags") is not None:   # parsed key present (incl. 'none')
        return True
    for line in bio.get("_raw", []):
        if _flags_in_line(line):
            return True
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
    """
    reg_name = packet.registry.get("registry_name")
    name = values.get("applicant_name")
    return bool(reg_name and name and norm_name(reg_name) != norm_name(name))


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
        "has_flag_evidence": has_flag_evidence(packet),
        "scan_only_pages": packet.scan_only_pages,
    }
