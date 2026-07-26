"""Interpretation: document typing, key/value parsing, and field vocabularies."""
import difflib
import re
from datetime import date

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


# All 979 train truth names are 2-3 alphabetic tokens, every token
# capitalized. The capitalization requirement is deliberate: separator-less
# corroboration sees prose fragments (`Applicant is expected on ...`) whose
# lowercase continuations would otherwise pass a bare token-shape test.
_NAME_SHAPE_RE = re.compile(r"[A-Z][A-Za-z'-]+( [A-Z][A-Za-z'-]+){1,2}")


def _plausible_value(field, value):
    """Could `value` really be this field? The corroboration bar for a label
    claim without a separator: closed fields must validate or repair into
    their vocabulary (deny-safe snaps included), open name fields must fit the
    census shape, free-text purpose must land in the closed 10-purpose set.
    """
    from . import vocab
    if field in ("applicant_name", "registry_name"):
        return bool(_NAME_SHAPE_RE.fullmatch(value.strip()))
    if valid_value(field, value):
        return True
    if field == "declared_purpose":
        return vocab.repairable_purpose(value)
    snapped = vocab.snap(field, value)
    return snapped not in (None, "unknown")


def _sepless_kv(line):
    """`Label Value` with the separator lost entirely — the [:.;] glyph is the
    smallest mark on the line and dies first (`Fee Status waved`, `Sponsor ID
    SP14-3584`, `Apphcant Onvoss Mesh`). A label head may claim the rest of
    the line only when the whole line still has label-line SHAPE and the value
    corroborates the claimed field (the principle that guards the loose key
    match). Shape means: the head fuzzy-matches a label of the SAME token
    count (a lost separator does not also delete label words), and the value
    is at most 4 tokens (a prose sentence — `Sponsor SPN-5086 attests that
    ...` — is not a label line, and claiming it poisoned the text-layer
    sponsor field with an unusable 11-token value, handing the vote to a
    decoy-page bait id). Longest head first; one recognized head decides.
    """
    toks = line.split()
    for k in range(min(3, len(toks) - 1), 0, -1):
        if len(toks) - k > 4:
            continue
        head = " ".join(toks[:k])
        key = key_for(head)
        if not key or len(_label_for(key, head).split()) != k:
            continue
        value = " ".join(toks[k:])
        return (key, value) if _plausible_value(key, value) else (None, None)
    return None, None


def _label_for(key, head):
    """The canonical label text `head` matched for `key` (for shape checks)."""
    t = head.strip().lower()
    if t in KEY_MAP:
        return t
    close = difflib.get_close_matches(t, [k for k, v in KEY_MAP.items() if v == key],
                                      n=1, cutoff=0.8)
    return close[0] if close else t


def parse_kv(lines):
    """Extract pairs from 'Key: Value' lines, 'Key' / 'Value' line pairs, and
    corroborated separator-less 'Key Value' lines (`_sepless_kv`).

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
        else:
            key, value = _sepless_kv(line)
            if key:
                kv.setdefault(key, value)
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


# --- damaged-registry recovery ----------------------------------------------
# The registry extract prints two-line label/value pairs (`Registry Name` /
# `Ixodane Luzarn` / `Home World` / ...). On faint scans the label's leading
# word erodes, fusing its tail onto the value line ('Home World' + 'Eris Relay'
# reads as 'World Ens Relay'), or the label vanishes entirely and only the bare
# value survives. Recovery anchors on the surviving canonical label word first,
# then on closed-vocabulary membership (snap cutoff 0.7 keeps debris out), and
# a bare TitleCase pair matching neither is the registry name — the only
# free-text field on the form. MIB-000293 p0 is the motivating read.
_REGISTRY_TAILS = [
    (re.compile(r"^(?:Registry\s+)?Name\b[:.;]?\s+(\S.*)$", re.I), "registry_name"),
    (re.compile(r"^(?:Home\s+)?World\b[:.;]?\s+(\S.*)$", re.I), "home_world"),
    (re.compile(r"^(?:Species\s+)?Code\b[:.;]?\s+(\S.*)$", re.I), "species_code"),
    (re.compile(r"^(?:Arrival\s+)?Date\b[:.;]?\s+(\S.*)$", re.I), "arrival_date"),
    (re.compile(r"^(?:Registry\s+)?Status\b[:.;]?\s+(\S.*)$", re.I), "registry_status"),
]
_BARE_NAME_RE = re.compile(r"^[A-Z][a-z]{2,}\s+[A-Z][a-z]{2,}$")
_BARE_NAME_STOP = {"registry", "extract", "packet", "synthetic", "hiring",
                   "challenge", "document", "image", "eyes", "only", "sample",
                   "denial", "manual", "note", "form", "receipt", "sponsor",
                   "attestation", "letter", "planetary"}


def registry_fallback_kv(lines):
    """Values a damaged Planetary Registry Extract still shows after its labels
    eroded. Fills only what parse_kv could not (the caller setdefaults), and
    every value passes through the same snap/validation as a labelled read."""
    from . import vocab
    kv = {}
    for raw in lines:
        line = raw.strip().rstrip(".")
        if not line:
            continue
        for rx, fname in _REGISTRY_TAILS:
            m = rx.match(line)
            if m:
                kv.setdefault(fname, m.group(1).strip())
                break
        else:
            if len(line.split()) <= 3:
                world = vocab.snap("home_world", line)
                if world:
                    kv.setdefault("home_world", world)
                    continue
            if _BARE_NAME_RE.match(line) and \
                    not set(line.lower().split()) & _BARE_NAME_STOP:
                kv.setdefault("registry_name", line)
    return kv


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
        if not DATE_RE.fullmatch(value):
            return False
        try:                        # reject well-shaped but impossible dates (2026-03-41)
            date.fromisoformat(value)
        except ValueError:
            return False
        return True
    if field == "fee_status":
        return value.lower() in FEE_STATUSES
    if field == "species_code":
        return bool(re.fullmatch(r"[A-Z][A-Z_]+", value))
    return True


def norm_name(name):
    return re.sub(r"[^a-z]", "", (name or "").lower())
