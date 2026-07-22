"""Derived fraud signals (taxonomy and validation status: docs/fraud-signals.md).

Every function here turns raw packet evidence into a named signal consumed by
policy. Signals must be validated on train before policy may act on them.
"""
import re

from . import parse
from .parse import ALL_FLAGS, SPONSOR_RE, norm_name


def observed_flags(packet):
    """Risk flags stated on the B-13 slip and registry status line. (§5, validated)"""
    flags = set()
    observed = (packet.biometric.get("observed_flags") or "").strip().lower()
    for token in re.split(r"[|,;\s]+", observed):
        if token in ALL_FLAGS:
            flags.add(token)

    reg_status = (packet.registry.get("registry_status") or "").strip().lower()
    if reg_status and reg_status != "clear":
        for token in re.split(r"[|,;\s]+", reg_status):
            if token in ALL_FLAGS:
                flags.add(token)
    return flags


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


def identity_conflict(packet):
    """Registry name disagrees with intake name. (§2, partial)"""
    reg_name = packet.registry.get("registry_name")
    intake_name = packet.intake.get("applicant_name")
    return bool(reg_name and intake_name and norm_name(reg_name) != norm_name(intake_name))


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
    code = next((kv.get("waiver_code") for _, kv in packet.docs if kv.get("waiver_code")), "")
    code = (code or "").strip()
    return "" if code.lower() in ("", "n/a", "none") else code


def derive(packet, values):
    """All signals as a dict; single entry point for policy and diagnostics."""
    flags = set(observed_flags(packet))
    if sponsor_mismatch(packet, values):
        flags.add("sponsor_mismatch")
    if identity_conflict(packet):
        flags.add("identity_conflict")
    # Full-embargo origin implies the flag even when no document states it:
    # train shows 50/50 of these carry planetary_embargo. (Not inferred for
    # Wolf-1061c — its denials mostly lack the flag.)
    if values.get("home_world") in ("TRAPPIST-1e", "Eris Relay"):
        flags.add("planetary_embargo")
    return {
        "flags": flags,
        "finding": adjudicator_finding(packet),
        "waiver_code": waiver_code(packet),
        "has_biometric": packet.has_doc(parse.DOC_BIOMETRIC),
        "scan_only_pages": packet.scan_only_pages,
    }
