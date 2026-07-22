"""Record assembly and output validation — the schema safety net.

The official evaluator hard-fails (exit 2) on invalid enums, confidence, or
duplicate ids. Nothing upstream is trusted to be well-formed; everything is
clamped/normalized here so a pipeline bug can never produce an invalid row.
"""
import re

ADJUDICATIONS = {"APPROVED", "DENIED", "NEEDS_REVIEW"}
FEE_VALUES = {"paid", "waived", "unpaid", "unknown"}
CASE_ID_RE = re.compile(r"^MIB-\d{6}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def build_record(case_id, values, flags, decision, conf):
    risk_flags = "|".join(sorted(flags)) if flags else "none"
    fee = (values.get("fee_status") or "unknown").lower()
    return validate({
        "case_id": case_id,
        "applicant_name": values.get("applicant_name", "unknown"),
        "species_code": values.get("species_code", "unknown"),
        "home_world": values.get("home_world", "unknown"),
        "visa_class": values.get("visa_class", "unknown"),
        "sponsor_id": values.get("sponsor_id", "SPN-0000"),
        "arrival_date": values.get("arrival_date", "1900-01-01"),
        "declared_purpose": values.get("declared_purpose", "unknown"),
        "risk_flags": risk_flags,
        "fee_status": fee,
        "adjudication": decision,
        "confidence": conf,
    })


def validate(record):
    if record["adjudication"] not in ADJUDICATIONS:
        record["adjudication"] = "NEEDS_REVIEW"
    if record["fee_status"] not in FEE_VALUES:
        record["fee_status"] = "unknown"
    if not DATE_RE.match(record["arrival_date"] or ""):
        record["arrival_date"] = "1900-01-01"
    try:
        record["confidence"] = min(1.0, max(0.0, float(record["confidence"])))
    except (TypeError, ValueError):
        record["confidence"] = 0.5
    return record


def dedupe(records):
    """Keep first record per case id; evaluator exits 2 on duplicates."""
    seen, out = set(), []
    for r in records:
        if r is None or r["case_id"] in seen:
            continue
        seen.add(r["case_id"])
        out.append(r)
    return out
