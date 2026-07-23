"""Interpretation: document typing, key/value parsing, and field vocabularies."""
import difflib
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
    "purpose": "declared_purpose",      # sponsor attestation labels it plainly
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


# Distinctive header tokens for OCR-mangled pages, tried after exact markers.
_FUZZY_HEADERS = [
    ("adjudicator", DOC_ADJUDICATOR),
    ("i-8090", DOC_INTAKE),
    ("b-13", DOC_BIOMETRIC),
    ("biometric", DOC_BIOMETRIC),
    ("attestation", DOC_SPONSOR),
    ("registry extract", DOC_REGISTRY),
    ("fee receipt", DOC_FEE),
]


def detect_doc_type(lines):
    head = " ".join(lines[:4])
    for marker, dtype in DOC_HEADERS:
        if marker in head:
            return dtype
    low = head.lower()
    for token, dtype in _FUZZY_HEADERS:
        if token in low:
            return dtype
    return DOC_OTHER


def key_for(text):
    """Map a candidate label to its canonical field, tolerating OCR debris.

    Exact match first; else fuzzy (handles 'Case 1D', 'Observed fIags'). Fuzzy
    matching is safe because labels are short and the key set is small/distinct.

    Public because S2 scores a page by how many labels it can recognise; it used
    to reach for the private name.
    """
    t = text.strip().lower()
    if t in KEY_MAP:
        return KEY_MAP[t]
    if len(t) > 30:
        return None
    close = difflib.get_close_matches(t, KEY_MAP.keys(), n=1, cutoff=0.8)
    return KEY_MAP[close[0]] if close else None


# The shredder clips the left margin, so a rescued line often reads
# `insor ID: SPN-5809` — the label is destroyed but the value that carries the
# points is intact and exact. A looser key match alone would be reckless (an
# adjudicator note reading `Revoked sponser: SPN-2718` must never become the
# applicant's sponsor), so the value has to corroborate it: the loose match is
# accepted only when the value is well-formed for the field it claims. In
# practice `Revoked sponser` fails the key test at this cutoff anyway, and the
# corroboration is what makes that not merely lucky.
LOOSE_KEY_CUTOFF = 0.55


def _loose_key_for(text):
    t = text.strip().lower()
    if len(t) > 30:
        return None
    close = difflib.get_close_matches(t, KEY_MAP.keys(), n=1, cutoff=LOOSE_KEY_CUTOFF)
    return KEY_MAP[close[0]] if close else None


def parse_kv(lines):
    """Extract pairs from 'Key: Value' lines and 'Key' / 'Value' line pairs.

    Separator is tolerant ([:.;]) — OCR reads colons as periods ('Observed
    flags. active_warrant'), which caused a catastrophic false approval
    (MIB-000161) under the strict-colon parser.
    """
    kv = {}
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        m = re.match(r"^([A-Za-z0-9][A-Za-z _0-9]{1,28}?)\s*[:.;]\s*(.+)$", line)
        if m:
            key = key_for(m.group(1))
            if key:
                kv.setdefault(key, m.group(2).strip())
                i += 1
                continue
            loose, value = _loose_key_for(m.group(1)), m.group(2).strip()
            if loose and valid_value(loose, value):
                kv.setdefault(loose, value)
                i += 1
                continue
        key = key_for(line)
        if key and i + 1 < len(lines):
            nxt = lines[i + 1].strip()
            if key_for(nxt) is None:
                kv.setdefault(key, nxt)
                i += 2
                continue
        i += 1
    return kv


# The sponsor attestation states its facts in sentences, not Key: Value lines,
# so parse_kv sees nothing on it at all — 273 of the 312 dev field-instances that
# are present in the text but never extracted come from unparsed lines like this.
# Matching happens against the *joined* text because the sentence wraps: the
# purpose in "expected on Earth for reactor / maintenance." straddles a newline,
# and half of it is not a purpose.
_PROSE_PATTERNS = [
    ("sponsor_id", re.compile(r"\bSponsor\s+(SPN-\d{4})\s+attests\b", re.IGNORECASE)),
    ("applicant_name",
     re.compile(r"\battests\s+that\s+(.+?)\s+is\s+expected\s+on\s+Earth\b", re.IGNORECASE)),
    ("declared_purpose",
     re.compile(r"\bis\s+expected\s+on\s+Earth\s+for\s+(.+?)\s*[.;]", re.IGNORECASE)),
    ("visa_class",
     re.compile(r"\bresponsibility\s+for\s+class\s+([A-Za-z]+-\d)\s+compliance",
                re.IGNORECASE)),
]


def parse_prose(lines):
    """Fields stated in sentences rather than Key: Value lines.

    Deliberately narrow: each pattern is anchored on wording specific to the
    attestation letter, so it cannot fire on an intake form or a decoy. Values
    are returned raw — snapping and validation happen downstream exactly as they
    do for parsed keys.
    """
    text = " ".join(l.strip() for l in lines if l.strip())
    out = {}
    for field, pattern in _PROSE_PATTERNS:
        m = pattern.search(text)
        if m and m.group(1).strip():
            out[field] = m.group(1).strip()
    return out


def page_case_ids(lines):
    ids = []
    for line in lines:
        ids.extend(CASE_ID_RE.findall(line))
    return ids


# The corpus marks damaged fields in place: [NAME CUT OUT], [REGISTRY LOST],
# [PURPOSE ILLEGIBLE], and OCR-mangled variants like [MAME CUT OUT]. These are
# the document saying the value is unrecoverable, which is exactly `unknown` —
# not a value. 51 of them were being emitted verbatim as answers.
_DAMAGE_MARKER_RE = re.compile(r"^\[[^\]]{3,40}\]$")


def valid_value(field, value):
    if not value or value.lower() in ("n/a", "unknown", ""):
        return False
    if _DAMAGE_MARKER_RE.match(value.strip()):
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
