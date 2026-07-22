"""Case assembly: active case id, document census, precedence-ordered field merge."""
from collections import Counter
from dataclasses import dataclass, field

from . import parse
from .parse import DOC_ADJUDICATOR, DOC_BIOMETRIC, DOC_INTAKE, DOC_REGISTRY, DOC_SPONSOR


@dataclass
class Packet:
    case_id: str = ""
    docs: list = field(default_factory=list)  # [(doc_type, kv)] sorted by trust order
    scan_only_pages: int = 0                  # pages with an image but ~no visible text

    def doc(self, dtype):
        return next((kv for d, kv in self.docs if d == dtype), {})

    def has_doc(self, dtype):
        return any(d == dtype for d, _ in self.docs)

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


def assemble(pages, fallback_case_id):
    """Build a Packet from PageText objects; pages of other applicants are dropped."""
    id_votes = Counter()
    for pt in pages:
        id_votes.update(parse.page_case_ids(pt.visible_lines))
    case_id = id_votes.most_common(1)[0][0] if id_votes else fallback_case_id

    packet = Packet(case_id=case_id)
    for pt in pages:
        ids = set(parse.page_case_ids(pt.visible_lines))
        if ids and case_id not in ids:
            continue  # decoy page for a different applicant
        if pt.image_count and len(pt.visible_lines) <= 3:
            packet.scan_only_pages += 1
        kv = parse.parse_kv(pt.visible_lines)
        kv["_raw"] = pt.visible_lines
        packet.docs.append((parse.detect_doc_type(pt.visible_lines), kv))
    packet.docs.sort(key=lambda t: t[0])
    return packet


def merge_fields(packet):
    """Best value per schema field, walking documents in trust order."""
    values = {}
    for fname in parse.FIELDS:
        for dtype, kv in packet.docs:
            candidates = [kv.get(fname)]
            if fname == "applicant_name" and dtype == DOC_REGISTRY:
                candidates.append(kv.get("registry_name"))
            for cand in candidates:
                if cand and parse.valid_value(fname, cand):
                    values[fname] = cand.strip()
                    break
            if fname in values:
                break
    return values
