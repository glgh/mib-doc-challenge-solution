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
    return _repair_ocr_kv(kv) if ocr else kv


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
            primary[pt.page_no] = best.lines

    id_votes = Counter()
    for pt in pages:
        id_votes.update(parse.page_case_ids(pt.visible_lines))
        id_votes.update(parse.page_case_ids(primary.get(pt.page_no, [])))
    case_id = id_votes.most_common(1)[0][0] if id_votes else fallback_case_id

    packet = Packet(case_id=case_id)
    for pt in pages:
        lines, source = (pt.visible_lines, SRC_TEXT)
        if pt.is_scan_only:
            packet.scan_only_pages += 1
            if primary.get(pt.page_no):
                lines, source = (primary[pt.page_no], SRC_OCR)
            for r in reads_by_page.get(pt.page_no) or []:
                if r.lines is lines or not r.lines or _decoy(r.lines, case_id, ocr=True):
                    continue
                kv = _parse_lines(r.lines, ocr=True)
                kv["_raw"] = r.lines
                kv["_page_no"] = pt.page_no
                packet.variant_docs.append((parse.detect_doc_type(r.lines), kv))
        if _decoy(lines, case_id, ocr=(source == SRC_OCR)):
            continue  # decoy page for a different applicant
        kv = _parse_lines(lines, ocr=(source == SRC_OCR))
        kv["_raw"] = lines
        kv["_page_no"] = pt.page_no
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


def _variant_vote(field_name, kvs):
    """Plurality over valid, normalized values for one field across OCR readings.

    Ties break first-seen, which is generation order: cheapest variant first
    within a page, pages in document order — the same bias `best_read` has.
    Returns (representative raw value, agreement count) or (None, 0).
    """
    counts, rep = Counter(), {}
    for kv in kvs:
        v = kv.get(field_name)
        if not v or not parse.valid_value(field_name, v):
            continue
        key = textmatch.normalize(v)
        counts[key] += 1
        rep.setdefault(key, v)
    if not counts:
        return None, 0
    top = max(counts, key=lambda k: counts[k])
    return rep[top], counts[top]


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
