"""Interpretation: document typing, key/value parsing, and field vocabularies."""
import re

CASE_ID_RE = re.compile(r"\bMIB-\d{6}\b")
SPONSOR_RE = re.compile(r"\bSPN-\d{4}\b")
DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")

VISA_CLASSES = {"XW-1", "XW-2", "DIP-1", "MED-3", "TRANSIT-7"}
FEE_STATUSES = {"paid", "waived", "unpaid", "unknown"}
DISQUALIFYING_FLAGS = {"memory_tampering", "planetary_embargo", "active_warrant", "biohazard_red"}
REVIEW_FLAGS = {"identity_conflict", "sponsor_mismatch", "illegible_biometrics", "rescinded_denial"}
ALL_FLAGS = DISQUALIFYING_FLAGS | REVIEW_FLAGS

# Doc types in field-manual trust order (low = more trusted).
DOC_ADJUDICATOR = 1
DOC_INTAKE = 2
DOC_BIOMETRIC = 3
DOC_SPONSOR = 4
DOC_REGISTRY = 5
DOC_FEE = 6
DOC_OTHER = 9

DOC_HEADERS = [
    ("Manual Adjudicator Note", DOC_ADJUDICATOR),
    ("FORM I-8090", DOC_INTAKE),
    ("FORM B-13", DOC_BIOMETRIC),
    ("Sponsor Attestation Letter", DOC_SPONSOR),
    ("Planetary Registry Extract", DOC_REGISTRY),
    ("MIB Fee Receipt", DOC_FEE),
]

KEY_MAP = {
    "case id": "case_id",
    "applicant": "applicant_name",
    "registry name": "registry_name",
    "species code": "species_code",
    "species match": "species_code",
    "home world": "home_world",
    "visa class": "visa_class",
    "sponsor id": "sponsor_id",
    "arrival date": "arrival_date",
    "declared purpose": "declared_purpose",
    "fee status": "fee_status",
    "waiver code": "waiver_code",
    "registry status": "registry_status",
    "observed flags": "observed_flags",
    "biometric confidence": "biometric_confidence",
    "finding": "finding",
}

FIELDS = ["applicant_name", "species_code", "home_world", "visa_class", "sponsor_id",
          "arrival_date", "declared_purpose", "fee_status"]


def detect_doc_type(lines):
    head = " ".join(lines[:4])
    for marker, dtype in DOC_HEADERS:
        if marker in head:
            return dtype
    return DOC_OTHER


def parse_kv(lines):
    """Extract pairs from 'Key: Value' lines and 'Key' / 'Value' line pairs."""
    kv = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r"^([A-Za-z ][A-Za-z _]+?)\s*:\s*(.+)$", line)
        if m and m.group(1).strip().lower() in KEY_MAP:
            kv.setdefault(KEY_MAP[m.group(1).strip().lower()], m.group(2).strip())
            i += 1
            continue
        key = line.strip().lower()
        if key in KEY_MAP and i + 1 < len(lines):
            nxt = lines[i + 1].strip()
            if nxt.lower() not in KEY_MAP:
                kv.setdefault(KEY_MAP[key], nxt)
                i += 2
                continue
        i += 1
    return kv


def page_case_ids(lines):
    ids = []
    for line in lines:
        ids.extend(CASE_ID_RE.findall(line))
    return ids


def valid_value(field, value):
    if not value or value.lower() in ("n/a", "unknown", ""):
        return False
    if field == "visa_class":
        return value in VISA_CLASSES
    if field == "sponsor_id":
        return bool(SPONSOR_RE.fullmatch(value))
    if field == "arrival_date":
        return bool(DATE_RE.fullmatch(value))
    if field == "fee_status":
        return value.lower() in FEE_STATUSES
    if field == "species_code":
        return bool(re.fullmatch(r"[A-Z][A-Z_]+", value))
    return True


def norm_name(name):
    return re.sub(r"[^a-z]", "", (name or "").lower())
