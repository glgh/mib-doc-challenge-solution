#!/usr/bin/env python3
"""MIB doc challenge pipeline: read a directory of case-packet PDFs, write predictions.jsonl.

Usage: solution.py <input_pdf_dir> <output_path>

Stages:
  1. Visible-text extraction per page (hidden/injected spans quarantined).
  2. Doc-type detection + key-value field extraction with source precedence.
  3. Rules-engine adjudication per the MIB field manual.
"""
import json
import re
import sys
from multiprocessing import Pool
from pathlib import Path

import fitz  # PyMuPDF

# ---------------------------------------------------------------- constants

CASE_ID_RE = re.compile(r"\bMIB-\d{6}\b")
SPONSOR_RE = re.compile(r"\bSPN-\d{4}\b")
DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")

VISA_CLASSES = {"XW-1", "XW-2", "DIP-1", "MED-3", "TRANSIT-7"}
FEE_STATUSES = {"paid", "waived", "unpaid", "unknown"}
DISQUALIFYING_FLAGS = {"memory_tampering", "planetary_embargo", "active_warrant", "biohazard_red"}
REVIEW_FLAGS = {"identity_conflict", "sponsor_mismatch", "illegible_biometrics", "rescinded_denial"}
ALL_FLAGS = DISQUALIFYING_FLAGS | REVIEW_FLAGS
REVOKED_SPONSORS = {"SPN-0007", "SPN-0139", "SPN-4040"}

# Doc types in field-manual trust order (low number = more trusted).
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

# key label -> canonical field name
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

# ------------------------------------------------------- visible text layer


def _is_whiteish(color_int):
    r, g, b = (color_int >> 16) & 255, (color_int >> 8) & 255, color_int & 255
    return r > 240 and g > 240 and b > 240


def visible_page_lines(page):
    """Return visible text lines, quarantining hidden spans (white, off-crop, tiny)."""
    crop = page.rect
    lines = []
    d = page.get_text("dict")
    for block in d.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            parts = []
            for span in line.get("spans", []):
                text = span.get("text", "")
                if not text.strip():
                    continue
                if _is_whiteish(span.get("color", 0)):
                    continue
                if span.get("size", 12.0) < 2.0:
                    continue
                if not crop.intersects(fitz.Rect(span["bbox"])):
                    continue
                parts.append(text)
            if parts:
                lines.append(" ".join(parts).strip())
    return lines

# ------------------------------------------------------------- page parsing


def detect_doc_type(lines):
    head = " ".join(lines[:4])
    for marker, dtype in DOC_HEADERS:
        if marker in head:
            return dtype
    return DOC_OTHER


def parse_kv(lines):
    """Extract key-value pairs from 'Key: Value' lines and 'Key' / 'Value' line pairs."""
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

# ----------------------------------------------------------- field validity


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

# ------------------------------------------------------------- per-PDF flow


def predict(pdf_path):
    with fitz.open(pdf_path) as doc:
        pages = [visible_page_lines(p) for p in doc]

    # Active case id: majority vote over visible ids across pages.
    from collections import Counter
    id_votes = Counter()
    for lines in pages:
        id_votes.update(page_case_ids(lines))
    case_id = id_votes.most_common(1)[0][0] if id_votes else pdf_path.stem

    # Parse pages of matching case id, keep best value per field by doc precedence.
    docs = []  # (doc_type, kv)
    for lines in pages:
        ids = set(page_case_ids(lines))
        if ids and case_id not in ids:
            continue  # page belongs to a different applicant
        dtype = detect_doc_type(lines)
        kv = parse_kv(lines)
        kv["_raw"] = lines
        docs.append((dtype, kv))
    docs.sort(key=lambda t: t[0])

    values = {}
    for field in FIELDS:
        for dtype, kv in docs:
            candidates = [kv.get(field)]
            if field == "applicant_name":
                candidates.append(kv.get("registry_name") if dtype == DOC_REGISTRY else None)
            for cand in candidates:
                if cand and valid_value(field, cand):
                    values[field] = cand.strip()
                    break
            if field in values:
                break

    # ---- risk flags
    flags = set()
    intake = next((kv for d, kv in docs if d == DOC_INTAKE), {})
    registry = next((kv for d, kv in docs if d == DOC_REGISTRY), {})
    biometric = next((kv for d, kv in docs if d == DOC_BIOMETRIC), {})
    sponsor_doc = next((kv for d, kv in docs if d == DOC_SPONSOR), {})
    adjudicator = next((kv for d, kv in docs if d == DOC_ADJUDICATOR), None)

    observed = (biometric.get("observed_flags") or "").strip().lower()
    for token in re.split(r"[|,;\s]+", observed):
        if token in ALL_FLAGS:
            flags.add(token)

    reg_status = (registry.get("registry_status") or "").strip().lower()
    if reg_status and reg_status != "clear":
        for token in re.split(r"[|,;\s]+", reg_status):
            if token in ALL_FLAGS:
                flags.add(token)

    # derived: sponsor attestation vs intake mismatch
    if sponsor_doc:
        att_text = " ".join(sponsor_doc.get("_raw", []))
        att_spn = SPONSOR_RE.search(att_text)
        if att_spn and values.get("sponsor_id") and att_spn.group(0) != values["sponsor_id"]:
            flags.add("sponsor_mismatch")
        m = re.search(r"attests that ([A-Z][A-Za-z]+(?: [A-Z][A-Za-z]+)+)", att_text)
        if m and values.get("applicant_name") and \
                norm_name(m.group(1)) != norm_name(values["applicant_name"]):
            flags.add("sponsor_mismatch")

    # derived: registry vs intake identity conflict
    reg_name = registry.get("registry_name")
    if reg_name and intake.get("applicant_name") and \
            norm_name(reg_name) != norm_name(intake["applicant_name"]):
        flags.add("identity_conflict")

    # ---- adjudication rules (field manual)
    fee = (values.get("fee_status") or "unknown").lower()
    visa = values.get("visa_class")
    sponsor = values.get("sponsor_id")
    waiver = (next((kv.get("waiver_code") for d, kv in docs if kv.get("waiver_code")), "") or "")
    has_waiver = waiver.strip().lower() not in ("", "n/a", "none")

    decision, conf = None, None
    finding = None
    if adjudicator:
        text = " ".join(adjudicator.get("_raw", []))
        m = re.search(r"Finding:\s*(APPROVED|DENIED|NEEDS_REVIEW)", text)
        if m:
            finding = m.group(1)

    if finding:
        decision, conf = finding, 0.9
    elif flags & DISQUALIFYING_FLAGS:
        decision, conf = "DENIED", 0.85
    elif sponsor in REVOKED_SPONSORS:
        decision, conf = "DENIED", 0.85
    elif visa == "TRANSIT-7":
        decision, conf = "DENIED", 0.75
    elif fee == "unpaid" and not has_waiver:
        decision, conf = "DENIED", 0.8
    elif fee == "unknown":
        decision, conf = "NEEDS_REVIEW", 0.65
    elif fee == "waived" and visa != "DIP-1" and not has_waiver:
        decision, conf = "NEEDS_REVIEW", 0.6
    elif not values.get("arrival_date"):
        decision, conf = "NEEDS_REVIEW", 0.65
    elif flags & REVIEW_FLAGS:
        decision, conf = "NEEDS_REVIEW", 0.65
    elif not sponsor and visa != "DIP-1":
        decision, conf = "NEEDS_REVIEW", 0.6
    elif not visa:
        decision, conf = "NEEDS_REVIEW", 0.55
    else:
        decision, conf = "APPROVED", 0.8

    risk_flags = "|".join(sorted(flags)) if flags else "none"
    return {
        "case_id": case_id,
        "applicant_name": values.get("applicant_name", "unknown"),
        "species_code": values.get("species_code", "unknown"),
        "home_world": values.get("home_world", "unknown"),
        "visa_class": values.get("visa_class", "unknown"),
        "sponsor_id": values.get("sponsor_id", "SPN-0000"),
        "arrival_date": values.get("arrival_date", "1900-01-01"),
        "declared_purpose": values.get("declared_purpose", "unknown"),
        "risk_flags": risk_flags,
        "fee_status": values.get("fee_status", "unknown"),
        "adjudication": decision,
        "confidence": conf,
    }


def _safe_predict(pdf):
    try:
        return predict(pdf)
    except Exception as exc:
        print(f"skipping {pdf.name}: {exc}", file=sys.stderr)
        return None


def main(input_dir, output_path):
    pdfs = sorted(Path(input_dir).glob("*.pdf"))
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with Pool(4) as pool:
        records = pool.map(_safe_predict, pdfs)
    with open(output, "w") as f:
        for record in records:
            if record is not None:
                f.write(json.dumps(record, sort_keys=True) + "\n")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: solution.py <input_pdf_dir> <output_path>")
    main(sys.argv[1], sys.argv[2])
