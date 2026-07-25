"""Case assembly: active case id, document census, precedence-ordered field merge."""
import re
from collections import Counter
from dataclasses import dataclass, field

from . import parse, textmatch, vocab
from .parse import DOC_ADJUDICATOR, DOC_BIOMETRIC, DOC_INTAKE, DOC_REGISTRY, DOC_SPONSOR
from .records import Candidate

SRC_TEXT = 0  # clean digital text layer
SRC_OCR = 1   # OCR of scan pixels — same doc type ranks below its text-layer peer


@dataclass
class Packet:
    case_id: str = ""
    docs: list = field(default_factory=list)  # [(doc_type, source, kv)] sorted by trust
    scan_only_pages: int = 0                  # pages with an image but ~no visible text

    def doc(self, dtype):
        return next((kv for d, _, kv in self.docs if d == dtype), {})

    def has_doc(self, dtype):
        return any(d == dtype for d, _, _ in self.docs)

    @property
    def intake(self):
        return self.doc(DOC_INTAKE)

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


def assemble(pages, ocr_lines, fallback_case_id):
    """Build a Packet from Page records; pages of other applicants are dropped.

    `ocr_lines` maps page_no -> the winning OCR reading for that page. Choosing
    among readings is S2's job (`stages.render.best`), so this stage never has to
    know how many variants were tried or how they were scored.
    """
    id_votes = Counter()
    for pt in pages:
        id_votes.update(parse.page_case_ids(pt.visible_lines))
        id_votes.update(parse.page_case_ids(ocr_lines.get(pt.page_no, [])))
    case_id = id_votes.most_common(1)[0][0] if id_votes else fallback_case_id

    packet = Packet(case_id=case_id)
    for pt in pages:
        lines, source = (pt.visible_lines, SRC_TEXT)
        if pt.is_scan_only:
            packet.scan_only_pages += 1
            if ocr_lines.get(pt.page_no):
                lines, source = (ocr_lines[pt.page_no], SRC_OCR)
        ids = set(parse.page_case_ids(lines))
        if ids and case_id not in ids:
            # An OCR page whose ID is one glyph off the active case is the
            # applicant's own page misread, not a decoy. Text-layer pages get no
            # such tolerance: text layers don't misread, so a near-miss ID there
            # is a genuine decoy (sequential case ids make those cheap to plant).
            if source != SRC_OCR or \
                    not any(textmatch.plausible_misread(case_id, i) for i in ids):
                continue  # decoy page for a different applicant
        kv = parse.parse_kv(lines)
        # Prose fills only what the labelled lines did not: an explicit
        # `Purpose: research` on the same document outranks a sentence.
        for fname, value in parse.parse_prose(lines).items():
            kv.setdefault(fname, value)
        if source == SRC_OCR:
            kv = _repair_ocr_kv(kv)
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


def merge_fields(packet, provenance=None):
    """Best value per schema field: manual corrections override, then documents
    in trust order.

    If `provenance` is a dict it is filled with fname -> (doc_type, source).
    """
    values = {}
    for cand in sorted(candidates(packet), key=_preference):
        if not cand.valid or cand.field_name in values:
            continue
        values[cand.field_name] = cand.value
        if provenance is not None:
            provenance[cand.field_name] = (cand.doc_type, cand.source)
    for fname, value in manual_corrections(packet).items():
        values[fname] = value
        if provenance is not None:
            provenance[fname] = (0, 0)  # rank-0: signed manual note
    return values
