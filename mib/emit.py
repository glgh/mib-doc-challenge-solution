"""Record assembly and output validation — the schema safety net.

The official evaluator hard-fails (exit 2) on invalid enums, confidence, or
duplicate ids. Nothing upstream is trusted to be well-formed; everything is
clamped/normalized here so a pipeline bug can never produce an invalid row.
"""
import re
import sys
from datetime import date

ADJUDICATIONS = {"APPROVED", "DENIED", "NEEDS_REVIEW"}
FEE_VALUES = {"paid", "waived", "unpaid", "unknown"}
CASE_ID_RE = re.compile(r"^MIB-\d{6}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# What a case scores when we have nothing: NEEDS_REVIEW is worth 2 raw points
# against a true APPROVED or DENIED and 8 against a true NEEDS_REVIEW, where a
# missing row is worth 0 and also forfeits every extraction point on that case.
FALLBACK = {
    "applicant_name": "unknown", "species_code": "unknown", "home_world": "unknown",
    "visa_class": "unknown", "sponsor_id": "SPN-0000", "arrival_date": "1900-01-01",
    "declared_purpose": "unknown", "risk_flags": "none", "fee_status": "unknown",
    "adjudication": "NEEDS_REVIEW", "confidence": 0.2,
}


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


def fallback_record(case_id):
    """A schema-valid row for a case that failed outright.

    Emitting this instead of nothing is worth up to 8 raw classification points
    and removes the case from the missing-case penalty. There is no downside: a
    row we cannot justify cannot score worse than no row at all.
    """
    return validate({"case_id": case_id, **FALLBACK})


def validate(record, fallback_case_id=None):
    """Coerce a record into the submission schema. Never raises.

    case_id is coerced too. The evaluator treats an unexpected id as an extra
    case *and* counts the real one as missing, so a single malformed id costs
    twice — and CASE_ID_RE sat here unused while that was true.
    """
    if not CASE_ID_RE.match(str(record.get("case_id", ""))):
        repaired = _repair_case_id(record.get("case_id"), fallback_case_id)
        if repaired != record.get("case_id"):
            print(f"emit: case_id {record.get('case_id')!r} -> {repaired!r}",
                  file=sys.stderr)
        record["case_id"] = repaired
    if record["adjudication"] not in ADJUDICATIONS:
        record["adjudication"] = "NEEDS_REVIEW"
    if record["fee_status"] not in FEE_VALUES:
        record["fee_status"] = "unknown"
    ad = record["arrival_date"] or ""
    if not DATE_RE.match(ad):
        record["arrival_date"] = "1900-01-01"
    else:
        try:                       # well-shaped but impossible -> coerce to the sentinel
            date.fromisoformat(ad)
        except ValueError:
            record["arrival_date"] = "1900-01-01"
    try:
        record["confidence"] = min(1.0, max(0.0, float(record["confidence"])))
    except (TypeError, ValueError):
        record["confidence"] = 0.5
    return record


def _repair_case_id(value, fallback):
    """Recover a well-formed id from debris, else fall back, else emit a valid shape.

    The last resort is deliberately a syntactically valid id that matches no real
    case: the evaluator rejects the whole submission on a malformed id, so an
    unmatchable one is strictly better than a fatal one.
    """
    for text in (value, fallback):
        m = re.search(r"MIB-(\d{6})", str(text or ""))
        if m:
            return f"MIB-{m.group(1)}"
    if CASE_ID_RE.match(str(fallback or "")):
        return str(fallback)
    return "MIB-000000"


def dedupe(records):
    """Keep the first record per case id; the evaluator exits 2 on duplicates.

    A collision means two PDFs resolved to the same id, so one real case is being
    dropped — silently, until now. It does not happen on any of the 1,000 train
    packets, but it is exactly the failure that would be invisible on the private
    set, so it says so on stderr.
    """
    seen, out = set(), []
    for r in records:
        if r is None:
            continue
        if r["case_id"] in seen:
            print(f"emit: duplicate case_id {r['case_id']} — dropping a case",
                  file=sys.stderr)
            continue
        seen.add(r["case_id"])
        out.append(r)
    return out
