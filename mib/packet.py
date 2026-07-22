"""Case assembly: active case id, document census, precedence-ordered field merge."""
from collections import Counter
from dataclasses import dataclass, field

from . import parse, vocab
from .parse import DOC_ADJUDICATOR, DOC_BIOMETRIC, DOC_INTAKE, DOC_REGISTRY, DOC_SPONSOR

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
                       "fee_status", "species_code", "home_world"):
            del kv[fname]  # garbage id/enum: absent beats wrong for policy safety
    return kv


def assemble(pages, fallback_case_id):
    """Build a Packet from PageText objects; pages of other applicants are dropped."""
    id_votes = Counter()
    for pt in pages:
        id_votes.update(parse.page_case_ids(pt.visible_lines))
        id_votes.update(parse.page_case_ids(pt.ocr_lines))
    case_id = id_votes.most_common(1)[0][0] if id_votes else fallback_case_id

    packet = Packet(case_id=case_id)
    for pt in pages:
        lines, source = (pt.visible_lines, SRC_TEXT)
        if pt.is_scan_only:
            packet.scan_only_pages += 1
            if pt.ocr_lines:
                lines, source = (pt.ocr_lines, SRC_OCR)
        ids = set(parse.page_case_ids(lines))
        if ids and case_id not in ids:
            continue  # decoy page for a different applicant
        kv = parse.parse_kv(lines)
        if source == SRC_OCR:
            kv = _repair_ocr_kv(kv)
        kv["_raw"] = lines
        packet.docs.append((parse.detect_doc_type(lines), source, kv))
    packet.docs.sort(key=lambda t: (t[0], t[1]))
    return packet


def merge_fields(packet, provenance=None):
    """Best value per schema field, walking documents in trust order.

    If `provenance` is a dict it is filled with fname -> (doc_type, source).
    """
    values = {}
    for fname in parse.FIELDS:
        for dtype, source, kv in packet.docs:
            candidates = [kv.get(fname)]
            if fname == "applicant_name" and dtype == DOC_REGISTRY:
                candidates.append(kv.get("registry_name"))
            for cand in candidates:
                if cand and parse.valid_value(fname, cand):
                    values[fname] = cand.strip()
                    if provenance is not None:
                        provenance[fname] = (dtype, source)
                    break
            if fname in values:
                break
    return values
