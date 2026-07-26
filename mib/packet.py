"""Case assembly: active case id, document census, precedence-ordered field merge."""
import re
from collections import Counter
from dataclasses import dataclass, field

from . import parse, textmatch, vocab
from .parse import DOC_ADJUDICATOR, DOC_BIOMETRIC, DOC_REGISTRY, DOC_SPONSOR
from .records import Candidate, best_read

SRC_TEXT = 0  # clean digital text layer
SRC_OCR = 1   # OCR of scan pixels — same doc type ranks below its text-layer peer


@dataclass
class Packet:
    case_id: str = ""
    docs: list = field(default_factory=list)  # [(doc_type, source, kv)] sorted by trust
    variant_docs: list = field(default_factory=list)  # [(doc_type, kv)] from losing OCR variants
    scan_only_pages: int = 0                  # pages with an image but ~no visible text

    def doc(self, dtype):
        return next((kv for d, _, kv in self.docs if d == dtype), {})

    def has_doc(self, dtype):
        return any(d == dtype for d, _, _ in self.docs)

    @property
    def biometric(self):
        return self.doc(DOC_BIOMETRIC)

    @property
    def sponsor_letter(self):
        return self.doc(DOC_SPONSOR)

    @property
    def registry(self):
        return self.doc(DOC_REGISTRY)

    @property
    def adjudicator(self):
        return self.doc(DOC_ADJUDICATOR) or None


def _repair_ocr_kv(kv):
    """Snap OCR-read values toward closed vocabularies; drop hopeless id fields."""
    for fname in list(kv):
        if fname.startswith("_"):
            continue
        snapped = vocab.snap(fname, kv[fname])
        if snapped:
            kv[fname] = snapped
        elif fname in ("sponsor_id", "case_id", "arrival_date", "visa_class",
                       "fee_status", "species_code", "home_world",
                       "observed_flags"):
            # Garbage id/enum: absent beats wrong for policy safety. observed_flags
            # belongs here for a sharper reason — left in place, an unreadable risk
            # line still counts as flag evidence, so a packet whose B-13 OCR'd to
            # debris looks like a slip that reported nothing.
            del kv[fname]
    return kv


def _parse_lines(lines, ocr):
    """One reading -> kv, the single parse path for docs and variants alike.

    Prose fills only what the labelled lines did not: an explicit
    `Purpose: research` on the same document outranks a sentence.
    """
    kv = parse.parse_kv(lines)
    for fname, value in parse.parse_prose(lines).items():
        kv.setdefault(fname, value)
    # OCR registry pages get the eroded-label recovery: label tails and bare
    # values that faint scans leave behind (text layers never erode, so the
    # fallback stays off them). Same snap/validation path as labelled reads.
    if ocr and parse.detect_doc_type(lines) == parse.DOC_REGISTRY:
        for fname, value in parse.registry_fallback_kv(lines).items():
            kv.setdefault(fname, value)
    return _repair_ocr_kv(kv) if ocr else kv


# "Key: Value" on one line, so an inline strike ("Fee Status: unpaid") matches by
# its value part as well as the whole line.
_INLINE_KV_RE = re.compile(r"^[A-Za-z0-9][A-Za-z _0-9]{1,28}?\s*[:.;]\s*(.+)$")


def _void_struck(kv, struck):
    """Drop any field whose value the document crossed out (red strikethrough).

    `struck` is the joined text of struck value cells for this page (from
    stages.extract). A struck value is the document voiding its own printed value
    — not sourceable evidence, like a hidden span or a damage marker — so it must
    not source a field; the true value comes from another document, or the field
    degrades to unknown. Match is normalized equality (never substring: a struck
    `unpaid` must not void a `paid` elsewhere on the page), plus the value part of
    an inline `Key: Value` strike. `_raw` is left intact, so flag scanning and
    manual-correction reading are unaffected.
    """
    if not struck:
        return kv
    voided = {textmatch.normalize(s) for s in struck}
    for s in struck:
        m = _INLINE_KV_RE.match(s)
        if m:
            voided.add(textmatch.normalize(m.group(1)))
    for fname in list(kv):
        if not fname.startswith("_") and textmatch.normalize(kv[fname]) in voided:
            del kv[fname]
    return kv


def _decoy(lines, case_id, ocr):
    """Does this reading belong to a different applicant?

    An OCR page whose ID is one glyph off the active case is the applicant's
    own page misread, not a decoy. Text-layer pages get no such tolerance:
    text layers don't misread, so a near-miss ID there is a genuine decoy
    (sequential case ids make those cheap to plant).
    """
    ids = set(parse.page_case_ids(lines))
    if not ids or case_id in ids:
        return False
    return not ocr or not any(textmatch.plausible_misread(case_id, i) for i in ids)


def assemble(pages, reads_by_page, fallback_case_id):
    """Build a Packet from Page records; pages of other applicants are dropped.

    `reads_by_page` maps page_no -> every OCR reading S2 produced. The primary
    reading (`records.best_read`) plays the role the single chosen reading used
    to: it defines the page's document. The losing readings survive as
    `variant_docs` — parsed, decoy-filtered kvs the per-field merge and the flag
    scan consult, because a losing variant can still hold the best copy of one
    field or the only legible risk flag.
    """
    primary = {}
    for pt in pages:
        best = best_read(reads_by_page.get(pt.page_no) or [])
        if best is not None:
            primary[pt.page_no] = best

    id_votes = Counter()
    for pt in pages:
        id_votes.update(parse.page_case_ids(pt.visible_lines))
        best = primary.get(pt.page_no)
        id_votes.update(parse.page_case_ids(best.lines if best else []))
    case_id = id_votes.most_common(1)[0][0] if id_votes else fallback_case_id

    packet = Packet(case_id=case_id)
    for pt in pages:
        lines, source, conf = (pt.visible_lines, SRC_TEXT, None)
        if pt.is_scan_only:
            packet.scan_only_pages += 1
            best = primary.get(pt.page_no)
            if best is not None and best.lines:
                lines, source, conf = (best.lines, SRC_OCR, best.conf)
            for r in reads_by_page.get(pt.page_no) or []:
                if r.lines is lines or not r.lines or _decoy(r.lines, case_id, ocr=True):
                    continue
                kv = _parse_lines(r.lines, ocr=True)
                kv["_raw"] = r.lines
                kv["_page_no"] = pt.page_no
                kv["_conf"] = r.conf
                packet.variant_docs.append((parse.detect_doc_type(r.lines), kv))
        if _decoy(lines, case_id, ocr=(source == SRC_OCR)):
            continue  # decoy page for a different applicant
        kv = _parse_lines(lines, ocr=(source == SRC_OCR))
        _void_struck(kv, pt.struck)   # a value the page crossed out is not evidence
        kv["_raw"] = lines
        kv["_page_no"] = pt.page_no
        kv["_conf"] = conf
        packet.docs.append((parse.detect_doc_type(lines), source, kv))
    packet.docs.sort(key=lambda t: (t[0], t[1]))
    return packet


# "Manual correction: <field> is <value>." — signed-manual-note evidence, the
# manual's highest precedence tier. Train census: 136 packets carry one, and
# every parseable correction matches the truth label (59/59 checked).
_CORRECTION_RE = re.compile(
    r"Manual correction[:.]?\s*"
    r"(fee status|visa class|sponsor(?: id)?|applicant(?: name)?|arrival date|"
    r"home world|species code|declared purpose)\s+is\s+(.+?)\.?\s*$",
    re.IGNORECASE)

_CORRECTION_FIELDS = {
    "fee status": "fee_status", "visa class": "visa_class",
    "sponsor": "sponsor_id", "sponsor id": "sponsor_id",
    "applicant": "applicant_name", "applicant name": "applicant_name",
    "arrival date": "arrival_date", "home world": "home_world",
    "species code": "species_code", "declared purpose": "declared_purpose",
}


def manual_corrections(packet):
    """field -> corrected value, from Manual correction lines on any document."""
    corrections = {}
    for _dtype, source, kv in packet.docs:
        for line in kv.get("_raw", []):
            m = _CORRECTION_RE.search(line)
            if not m:
                continue
            fname = _CORRECTION_FIELDS[m.group(1).lower()]
            value = m.group(2).strip()
            if source == SRC_OCR and not parse.valid_value(fname, value):
                value = vocab.snap(fname, value) or value
            if parse.valid_value(fname, value) or fname == "applicant_name":
                corrections.setdefault(fname, value)
    return corrections


def candidates(packet):
    """Every value seen for every schema field, in trust order.

    `packet.docs` is already sorted by (doc_type, source), so generation order is
    trust order and the winner is simply the first valid entry per field. The
    point of materializing the losers is that today they are unrecoverable: the
    sort ranks whole *documents*, so an OCR'd high-trust document outranks a
    clean text-layer lower-trust one for every field at once, and 37 dev
    field-instances are lost that way — a correct `Miravoss` on one page losing
    to a garbled `Mirayoss` on another. Preferring per field needs the
    alternatives to still exist at this seam.
    """
    out = []
    for dtype, source, kv in packet.docs:
        page_no = kv.get("_page_no", 0)
        for fname in parse.FIELDS:
            raws = [kv.get(fname)]
            # The registry extract names the applicant under its own key.
            if fname == "applicant_name" and dtype == DOC_REGISTRY:
                raws.append(kv.get("registry_name"))
            for raw in raws:
                if not raw:
                    continue
                out.append(Candidate(
                    field_name=fname, value=raw.strip(), raw_value=raw,
                    doc_type=dtype, source=source, page_no=page_no,
                    valid=parse.valid_value(fname, raw),
                    quality=1.0 if source == SRC_TEXT else 0.5))
    return out


def _preference(cand):
    """Sort key deciding which candidate for a field wins: read quality, then trust.

    `packet.docs` is ordered (doc_type, source), which ranks whole *documents* and
    therefore lets an OCR'd high-trust document win every field at once over a
    clean text-layer copy elsewhere — a correct `Miravoss` losing to a garbled
    `Mirayoss`. Preferring the clean read first and settling ties by field-manual
    trust order is worth +0.23 dev (38.76 -> 39.00 extraction, CFA unchanged).

    This does not contradict the manual's evidence precedence. That order ranks
    *kinds of evidence*, and both copies here are the same kind — one is simply a
    better reading of it. Trust order still decides between different documents,
    which is what the tie-break does.
    """
    return (cand.source, cand.doc_type)


VOTE_DOC = 99  # provenance doc_type marking a value settled by the variant vote

# Stroke merges the single-char confusion table cannot price: tesseract reads
# `rn` as `m` and `ri` as `n` (adjacent strokes fusing), and it does so MORE
# confidently than the true glyphs, so engine conf must not arbitrate between
# the two forms. This mechanism survives its own ablation at corpus scale
# (row 49): with the merge disabled on the schema-4 substrate, per-line conf
# alone regresses 7 names / recovers 0 — the merge is load-bearing, not a
# case patch. Positions are census-bound (train labels): `rn`>`m` anywhere;
# `ri`>`n` token-final only (inner `ri`/`n` are both common in real names —
# an inner merge conflated `Miradane` with the hallucinated `Miradarie`,
# row 45). No two distinct truth names collide under this map, so merging
# cannot conflate two real applicants; the expanded form is the
# representative (the merge direction never runs backward in this corpus).
_NAME_COLLAPSES = ((re.compile("rn"), "m"), (re.compile(r"ri\b"), "n"))


def _edge_strip(normalized):
    """Tokens with edge punctuation removed; inner structure (id hyphens, date
    dashes) untouched. `Zazam_` -> `zazam`, `spn-1234.` -> `spn-1234`."""
    toks = (re.sub(r"^[^a-z0-9]+|[^a-z0-9]+$", "", t) for t in normalized.split())
    return " ".join(t for t in toks if t)


def _vote_key(value):
    """Agreement key for the variant vote: edge punctuation is not disagreement
    (`Zazam_`, `Zazam.` and `Zazam` are one reading with debris)."""
    return _edge_strip(textmatch.normalize(value))


def _collapse(key):
    """Stroke-collapsed form of a name key: `Xanzarn`/`Xanzam` are one name
    read twice, not a 1-1 tie. Applied AFTER truncation pooling — a truncated
    `nextar` can only reach its expansion `nextari` pre-collapse (the collapse
    rewrites `nextari` to `nextan`, which no truncation is a prefix of)."""
    for expanded, merged in _NAME_COLLAPSES:
        key = expanded.sub(merged, key)
    return key


def _line_conf(kv, value):
    """The engine's confidence in the tsv line that carries `value`, or None.

    Matched by alnum substring against the conf entries' line text (schema 4);
    schema-3 caches have no text and yield None, so everything downstream falls
    back to first-seen order exactly as before.
    """
    want = re.sub(r"[^a-z0-9]", "", str(value).lower())
    if not want:
        return None
    best = None
    for entry in kv.get("_conf") or []:
        if len(entry) > 3 and want in re.sub(r"[^a-z0-9]", "", entry[3].lower()):
            best = entry[0] if best is None else max(best, entry[0])
    return best


# Truncation POOLING (merging `qormora nextar` into `qormora nextari` by
# token-wise prefix) was measured and REJECTED here (row 47): it recovered
# MIB-000665 but consolidation strengthens whichever name family FRAGMENTS
# more — on MIB-000250 the decoy applicant's six reads splintered into four
# keys, pooled into a bloc, and outvoted the truth's three consistent reads.
# Cross-family balance is a page-level question; revisit only with a per-page
# two-level vote.


def _variant_vote(field_name, kvs):
    """Plurality over valid values for one field across OCR readings, grouped
    by `_vote_key` so debris and stroke-merge variants pool their votes instead
    of splitting them (the 1-1 "ties" of row 42 were mostly one reading
    fragmented by punctuation).

    Genuine ties break by per-line engine confidence (`_line_conf`), then
    first-seen generation order. A PAGE-level conf weight was tried and
    rejected (row 43): whole-read mass is uncorrelated with one line's
    correctness — the line the value sits on is the right grain. A steer-ties-
    away-from-revoked-sponsors guard was tried in the same batch and also
    reverted: MIB-000130's `SPN-4040` is genuinely revoked (truth DENIED) and
    the guard handed the case to its misread neighbor `SPN-4080` — on a tie,
    the revoked reading is the deny-safe direction, not the hazard (the hazard
    is *repair* translating digits toward the list, guarded in vocab.snap).

    Returns (representative raw value, agreement count) or (None, 0).
    """
    groups = {}   # vote key -> [(value, clean, line_conf, seq)]
    for seq, kv in enumerate(kvs):
        v = kv.get(field_name)
        if not v or not parse.valid_value(field_name, v):
            continue
        # An INNER colon in a voted value means the parse mis-keyed a line —
        # the value swallowed another field's label (`Home World: Europa
        # Station` winning the *name* vote). Schema-grounded, not case-bound:
        # ZERO of the 12,000 train truth values contain a colon (census, row
        # 49). A trailing colon is mere edge debris (`Solul Qorzarn:`) — the
        # edge-strip key already absorbs it, and dropping those reads
        # reshuffled first-seen ties into regressions.
        if re.search(r":\s*\S", v):
            continue
        key = _vote_key(v)
        if not key:
            continue
        norm = textmatch.normalize(v)
        clean = norm == _edge_strip(norm)   # stripping removed nothing: no debris
        groups.setdefault(key, []).append((v, clean, _line_conf(kv, v), seq))
    if field_name == "applicant_name":
        collapsed = {}
        for key, entries in groups.items():
            collapsed.setdefault(_collapse(key), []).extend(entries)
        groups = collapsed
    if not groups:
        return None, 0

    def group_rank(entries):
        best_conf = max((c for _v, _cl, c, _s in entries if c is not None), default=-1.0)
        first_seen = min(s for _v, _cl, _c, s in entries)
        return (-len(entries), -best_conf, first_seen)

    entries = min(groups.values(), key=group_rank)

    def rep_rank(e):
        v, clean, conf, seq = e
        # Cleanest first (no edge debris), then the expanded stroke-merge form
        # (longer), then the engine's own preference, then generation order.
        return (not clean, -len(textmatch.normalize(v)),
                -(conf if conf is not None else -1.0), seq)

    return min(entries, key=rep_rank)[0], len(entries)


def merge_fields(packet, provenance=None):
    """Best value per schema field: manual corrections override, then documents
    in trust order, then a plurality vote across the whole OCR ensemble for any
    field the text layer (or a signed note) did not supply.

    The vote is why `variant_docs` exists: a reading `best_read` rejected for
    the page can still hold the best copy of one field, and agreement across
    independently-restored variants is strong evidence for an OCR value. Clean
    text-layer values are never outvoted — text layers don't misread.

    If `provenance` is a dict it is filled with fname -> (doc_type, source);
    vote-settled fields carry doc_type VOTE_DOC.
    """
    prov = {} if provenance is None else provenance
    values = {}
    for cand in sorted(candidates(packet), key=_preference):
        if not cand.valid or cand.field_name in values:
            continue
        values[cand.field_name] = cand.value
        prov[cand.field_name] = (cand.doc_type, cand.source)
    for fname, value in manual_corrections(packet).items():
        values[fname] = value
        prov[fname] = (0, 0)  # rank-0: signed manual note
    ocr_kvs = ([kv for _, kv in packet.variant_docs] +
               [kv for _, src, kv in packet.docs if src == SRC_OCR])
    if ocr_kvs:
        for fname in parse.FIELDS:
            if prov.get(fname, (0, SRC_OCR))[1] == SRC_TEXT:
                continue  # clean text-layer (or manual-note) value stays
            value, _agree = _variant_vote(fname, ocr_kvs)
            if value:
                values[fname] = value.strip()
                prov[fname] = (VOTE_DOC, SRC_OCR)
    return values
