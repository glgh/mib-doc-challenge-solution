"""Derived fraud signals (taxonomy and validation status: docs/BACKGROUND.md §3).

Every function here turns raw packet evidence into a named signal consumed by
policy. Signals must be validated on train before policy may act on them.
"""
import difflib
import re
from collections import Counter

from . import parse, vocab
from .packet import SRC_OCR
from .parse import SPONSOR_RE, norm_name

_TOKEN_SPLIT = re.compile(r"[\s|,;:/()\[\]]+")
_LEGEND_RE = re.compile(
    r"\b(option|one of|any of|possible|valid value|choose|list of|e\.g\.)\b", re.I)
_NEGATION = {"no", "not", "without", "none", "negative", "clear", "cleared", "absent"}
_STRIP = " .,;:|/()[]'\"-"

# Whole-value rescue bars, from the mined safety table over the full train
# ensemble (experiments/flag_probe.py --values): 563 labelled observed-flags
# values the token matcher missed; every argmax-FALSE row sits at score<=0.40 /
# margin<=0.08 (damage markers cluster at 0.36/0.04), every TRUE at >=0.44 /
# >=0.10 — the band between is empty. Single-read acceptance stays well above
# the boundary; the thin 0.44-0.55 band additionally requires two independent
# readings of the page to argmax the same flag.
VALUE_SINGLE_SCORE, VALUE_SINGLE_MARGIN = 0.55, 0.15
VALUE_QUORUM_SCORE, VALUE_QUORUM_MARGIN, VALUE_QUORUM_N = 0.44, 0.10, 2

# List separators inside a multi-flag `Observed flags:` value. Comma/semicolon/
# pipe only — not '/', which is an OCR glyph artifact *inside* a word far more
# often than a list separator (splitting on it fragments single flags).
_FLAG_LIST_SPLIT = re.compile(r"[,;|]")

_KV_LINE_RE = re.compile(r"^([A-Za-z0-9][A-Za-z _0-9]{1,28}?)\s*[:.;]\s*(.+)$")


def _labelled_flag_value(line):
    """The value part of an `Observed flags:`-labelled line, or None.

    Label matching is loose (parse._loose_key_for): the label survives OCR
    better than the value ('Obearved fags', 'Geserved flags'), and the value
    must then corroborate through the scored bars — the same loose-key +
    well-formed-value pattern parse.parse_kv uses (LOOSE_KEY_CUTOFF)."""
    m = _KV_LINE_RE.match(line.strip())
    if not m or _LEGEND_RE.search(line):
        return None
    label = m.group(1)
    if parse.key_for(label) == "observed_flags" or \
            parse._loose_key_for(label) == "observed_flags":
        return m.group(2).strip()
    return None


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


def _resolve_flag_value(value):
    """Flags a single `Observed flags:` value asserts -> {flag: 'single'|'quorum'}.

    `vocab.match_flag_value` scores a value WHOLE, so a two-flag list value
    (`biohazard_red, illegible_biometrics`, OCR-shattered to `bichaxarc_yed,
    Regie. biometics`) dilutes the second flag below its bar and the token path
    can't reach it either (a whole missing `illegible` prefix is not an OCR
    confusion). We score the whole value AND each delimiter-separated part at the
    same mined bars, taking the union: a stray OCR comma inside a single flag is
    harmless because the whole-value match still fires and the fragments just
    miss the bars. 'single' (strong) always wins over 'quorum' for a given flag.
    """
    out = {}
    for part in (value, *_FLAG_LIST_SPLIT.split(value)):
        part = part.strip()
        if len(part) < 4:
            continue
        flag, score, margin = vocab.match_flag_value(part)
        if not flag:
            continue
        if score >= VALUE_SINGLE_SCORE and margin >= VALUE_SINGLE_MARGIN:
            out[flag] = "single"
        elif score >= VALUE_QUORUM_SCORE and margin >= VALUE_QUORUM_MARGIN:
            out.setdefault(flag, "quorum")
    return out


def observed_flags(packet):
    """Risk flags stated on the flag-bearing documents, read value-first. (§5)

    Matched fuzzily and confusion-weighted (mib.vocab.match_flag_token) so the
    flag survives OCR damage to either the label ('Observed flags'->'Chserved
    flags') or the value ('biohazard_red'->'bichaxarc_yed') — the same
    'values outlast labels' property S2's page_score relies on. `_raw` holds
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

    OCR readings additionally get the whole-value rescue: a labelled
    `Observed flags:` value the token matcher couldn't resolve (shattered past
    any single token — `Bagitie bematics`, space-split `illegible biometrics`)
    is scored whole against the flag vocabulary (vocab.match_flag_value) and
    accepted at the mined bars above, alone or by cross-variant quorum. Only
    OCR text: a text layer doesn't misread, so an unmatchable value there is
    genuinely not a flag (same principle as the case-id decoy tolerance and
    identity_conflict's OCR clause).
    """
    flags = set()
    quorum = Counter()   # (page_no, flag) -> independent readings agreeing
    readings = ([(src, kv) for _d, src, kv in packet.docs] +
                [(SRC_OCR, kv) for _d, kv in packet.variant_docs])
    for src, kv in readings:
        rescued = set()
        for line in kv.get("_raw", []):
            flags |= _flags_in_line(line)
            if src != SRC_OCR:
                continue
            # Run the value path even when the token path already fired on this
            # line: on a multi-flag value the token path resolves the legible
            # flag and the whole-value rescue used to be skipped (`if hits:
            # continue`), stranding the shattered second flag (MIB-000414's
            # `bichaxarc_yed, Regie. biometics` -> biohazard_red only). Splitting
            # per part recovers it; the union with the token hits is idempotent.
            value = _labelled_flag_value(line)
            if value is None:
                continue
            resolved = _resolve_flag_value(value)
            if len(resolved) > 3:   # a value resolving to >3 flags reads as a legend
                continue
            for flag, tier in resolved.items():
                if tier == "single":
                    flags.add(flag)
                else:
                    rescued.add(flag)
        for flag in rescued:
            quorum[(kv.get("_page_no"), flag)] += 1
    flags |= {f for (_pg, f), n in quorum.items() if n >= VALUE_QUORUM_N}
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
        ov = bio.get("observed_flags")
        # A parsed value (incl. 'none') is a read risk line — UNLESS it is a
        # damage marker ('[RISK PANEL MISSING]'), which says the panel is missing,
        # not read. Counting it as evidence contradicts this function's contract
        # and lets `b13_census` clean_approve a concealed-risk packet (the CFA
        # direction the rule exists to block).
        if ov is not None and not parse._damage_markerish(str(ov)):
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


_NAME_AGREE_RATIO = 0.75  # row-33 max-margin cutoff; see identity_conflict's docstring


def _name_agrees(a, b):
    """OCR-tolerant name agreement: normalized similarity at/above the row-33 cutoff."""
    return difflib.SequenceMatcher(
        None, norm_name(a), norm_name(b)).ratio() >= _NAME_AGREE_RATIO


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
    if source == SRC_OCR and _name_agrees(reg_name, name):
        return False
    # A losing variant that read the registry name within tolerance is evidence
    # of AGREEMENT — the same principle as has_flag_evidence's losing-variant
    # clause. MIB-000523: the conf-selected primary read `Inout Solkx` (a worse
    # mangle of `Ixoul Solix`) and the conflict re-fired against the very case
    # row 33 fixed; the sibling variants still read the name within tolerance.
    # Variant readings are OCR by construction, so this never loosens
    # text-layer exact-match semantics.
    for dtype, kv in packet.variant_docs:
        if dtype == parse.DOC_REGISTRY:
            alt = kv.get("registry_name")
            if alt and _name_agrees(alt, name):
                return False
    return True


# A scanned note's Finding line survives shred/deskew but not verbatim: OCR reads
# the `NEEDS_REVIEW` underscore as a space and sometimes drops the label colon
# (MIB-000900 p2: `Manual Adjudicator Note` reunited by the deshred fix, but its
# finding OCR'd `Finding NEEDS REVIEW`). Tolerate both — the three decision tokens
# stay distinct, so a loosened separator can never turn a DENIED into an APPROVED
# — and normalize the space back to the underscore the enum expects.
_FINDING_RE = re.compile(r"Finding[:.]?\s*(APPROVED|DENIED|NEEDS[ _]REVIEW)")


def _finding_in(lines):
    m = _FINDING_RE.search(" ".join(lines))
    return m.group(1).replace(" ", "_") if m else None


def adjudicator_finding(packet):
    """Explicit decision on a Manual Adjudicator Note — highest-trust evidence. (validated)

    The primary read of the note wins outright. When it lost the Finding line
    (a weaker variant won the page — observed under the optical rung on
    MIB-000065, where the finding fell to `fee_unknown`'s 0.494 confidence),
    the losing OCR variants of the same note are the same visible evidence read
    differently, so they answer — but only unanimously: two variants reading
    different findings is a misread by construction (one page, one stamp), and
    fabricating a winner from that is worse than letting the cascade decide.

    The finding line is not gated on the page typing as an adjudicator note. A
    skewed+shredded note has its TITLE destroyed while the `Finding:` line reads
    clean (964 p1: +5° skew doubles `Manual Adjudicator Note` into `MARHeaeF
    Note` — typed OTHER — yet every variant reads `Finding: NEEDS_REVIEW`, the
    truth). The finding line is its own fingerprint: over all 1,000 train cases
    every `Finding:` line is unanimous across reads AND matches the label
    (333/333, experiments/finding_census.py), and 9 cases carry a correct finding
    the title gate was discarding (105/141/142/390/608/615/900/964/986 — 141 a
    live false NEEDS_REVIEW on a truth-DENIED). Trust rests on three guards: the
    tokens are distinct so a loosened read can't cross DENIED↔APPROVED; `_raw` is
    visible+OCR only, so hidden/injected findings never reach here and assemble
    has already dropped cross-applicant decoy pages; and unanimity means a lone
    misread inventing a second value declines to a null, never a fabricated one.
    """
    adjudicator = packet.adjudicator
    if adjudicator:
        found = _finding_in(adjudicator.get("_raw", []))
        if found:
            return found
    seen = set()
    for _dtype, _src, kv in packet.docs:
        found = _finding_in(kv.get("_raw", []))
        if found:
            seen.add(found)
    for _dtype, kv in packet.variant_docs:
        found = _finding_in(kv.get("_raw", []))
        if found:
            seen.add(found)
    if len(seen) == 1:
        return seen.pop()
    return None


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
    (sponsor_mismatch, identity_conflict) still drives the decision but is
    never written to risk_flags. (planetary_embargo is no longer inferred here
    — see the note below; policy.embargo_world now owns that rule.)
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
        # An injection-shaped hidden line claiming APPROVED caps an unforced
        # approval at NEEDS_REVIEW (policy). Presence only — never a value source.
        "injected_approval": packet.injected_approval,
        "waiver_code": waiver_code(packet),
        # Presence counts LOSING variants too (fourth application of the
        # losing-variants principle, after the flag union, the finding
        # fallback, and identity agreement): MIB-000886's B-13 page is typed
        # by several variants while the grid's primary lost the header to a
        # composed-optical read — a slip plainly read by any variant exists.
        # Presence is a weaker claim than field sourcing; values still come
        # only from the trust-ordered docs and the vote.
        "has_biometric": (packet.has_doc(parse.DOC_BIOMETRIC)
                          or any(d == parse.DOC_BIOMETRIC
                                 for d, _kv in packet.variant_docs)),
        # A B-13 we could not read the flag line from is evidence of nothing.
        # `has_biometric` only says a slip was detected; the risk-concealment
        # census is about whether its risk line was actually read, so policy
        # needs to tell "flags: none" from "flags: <unreadable>".
        "has_flag_evidence": has_flag_evidence(packet, observed),
        "scan_only_pages": packet.scan_only_pages,
    }
