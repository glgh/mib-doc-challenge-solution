"""Closed vocabularies (from train labels — policy-level entities, not per-case
data) and fuzzy repair for OCR debris. Snapping is guarded: values far from any
known term pass through unchanged, so unseen private-set values survive."""
import difflib
import re

HOME_WORLDS = [
    "Barnard-c", "Eris Relay", "Europa Station", "Gliese-581g", "Kepler-186f",
    "Luyten-b", "Mars Dome-7", "Proxima-b", "Sirius Outpost", "TRAPPIST-1e",
    "Titan Freeport", "Wolf-1061c", "Zeta Reticuli",
]
SPECIES = [
    "ALPHA_DRACONIAN", "ANDROMEDAN", "AQUARIAN_MANTIS", "ARCTURIAN",
    "CENTAURI_SYNTH", "JOVIAN_GASFORM", "KAIJU_MICRO", "LUNA_SECURID",
    "ORION_GRAYS", "SIRIUS_AVIAN", "TRIANGULAN", "VENUSIAN_MYCELIAL",
]
PURPOSES = [
    "archive audit", "cultural exchange", "diplomatic", "field repair",
    "medical consult", "reactor maintenance", "research", "transit",
    "translation", "xenobotany",
]
VISAS = ["XW-1", "XW-2", "DIP-1", "MED-3", "TRANSIT-7"]
FEES = ["paid", "waived", "unpaid", "unknown"]
FLAGS = ["memory_tampering", "planetary_embargo", "active_warrant", "biohazard_red",
         "identity_conflict", "sponsor_mismatch", "illegible_biometrics",
         "rescinded_denial", "none"]

# Common OCR confusions applied before matching id-like tokens.
_DIGIT_FIXES = str.maketrans({"O": "0", "o": "0", "l": "1", "I": "1", "S": "5", "B": "8"})


def _closest(value, options, cutoff):
    match = difflib.get_close_matches(value, options, n=1, cutoff=cutoff)
    return match[0] if match else None


def snap(field, value):
    """Repair an OCR-read value toward its closed vocabulary; None if hopeless."""
    v = (value or "").strip()
    if not v:
        return None
    if field == "visa_class":
        return _closest(v.upper().replace("=", "-"), VISAS, 0.6)
    if field == "fee_status":
        best = _closest(v.lower(), FEES, 0.7)
        # "unpaid" triggers a hard denial, so it must be read verbatim, never
        # reconstructed by distance (a garbled "paid" is one edit from "unpaid").
        if best == "unpaid" and re.sub(r"[^a-z]", "", v.lower()) != "unpaid":
            return "unknown"
        return best
    if field == "species_code":
        return _closest(re.sub(r"[^A-Z_]", "", v.upper().replace(" ", "_")), SPECIES, 0.7)
    if field == "home_world":
        return _closest(v, HOME_WORLDS, 0.7)
    if field == "declared_purpose":
        return _closest(v.lower(), PURPOSES, 0.6) or v
    if field == "sponsor_id":
        m = re.search(r"[S5]PN[-–—:\s]*([0-9OolIB]{4})", v)
        if not m:
            return None
        raw, fixed = m.group(1), m.group(1).translate(_DIGIT_FIXES)
        # A revoked sponsor id triggers a hard denial: digit-translation must
        # never *fabricate* one (it did: SPN-8421 → "revoked" SPN-0139). Exact
        # digits are required for revoked matches; translated repairs of
        # non-revoked ids are harmless to policy and keep extraction points.
        from .policy import REVOKED_SPONSORS
        if fixed != raw and f"SPN-{fixed}" in REVOKED_SPONSORS:
            return None
        return f"SPN-{fixed}"
    if field == "case_id":
        m = re.search(r"M[iI1l]B[-–—:\s]*([0-9OolIB]{6})", v, re.IGNORECASE)
        return f"MIB-{m.group(1).translate(_DIGIT_FIXES)}" if m else None
    if field == "arrival_date":
        m = re.search(r"(\d{4})[-–—/.](\d{2})[-–—/.](\d{2})", v)
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None
    if field == "observed_flags":
        tokens = re.split(r"[|,;\s]+", v.lower())
        fixed = [f for t in tokens if t for f in [_closest(t, FLAGS, 0.8)] if f]
        return "|".join(f for f in fixed if f != "none") or "none"
    return v


def clean_ocr_line(line):
    """Strip OCR junk: stray quotes/brackets at edges, unicode quotes, id-case fixes."""
    line = line.replace("‘", "").replace("’", "").replace("“", "").replace("”", "")
    line = re.sub(r"^[^A-Za-z0-9]+", "", line)
    line = re.sub(r"\bM[iI1l][bB]-", "MIB-", line)
    line = re.sub(r"\b[S5]PN[-–—]", "SPN-", line)
    return line.strip()
