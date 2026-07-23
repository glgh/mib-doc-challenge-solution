"""Feature vector for the learned decider — the single source of truth.

Consumes the (record, debug) pair that `solution.predict_from_pages` emits, so
the trainer (reading eval jsonl sidecars) and the runtime (in-memory dicts) run
literally the same code. Any new signal must be added here, nowhere else, and
lands in both paths at once.

Feature order is the sorted key order, frozen by `names()`; the exported model
carries its own copy and `mib.decision` refuses to run on a mismatch.
"""
from datetime import date

CLASSES = ["APPROVED", "DENIED", "NEEDS_REVIEW"]
VISAS = ["XW-1", "XW-2", "DIP-1", "MED-3", "TRANSIT-7"]
FEES = ["paid", "waived", "unpaid", "unknown"]
FLAGS = ["memory_tampering", "planetary_embargo", "active_warrant", "biohazard_red",
         "identity_conflict", "sponsor_mismatch", "illegible_biometrics", "rescinded_denial"]
BRANCHES = ["adjudicator_finding", "disqualifying_flag", "embargo_world",
            "embargo_world_partial", "revoked_sponsor", "transit_visa", "fee_unpaid",
            "fee_unknown", "stale_arrival", "waived_non_dip", "missing_arrival",
            "review_flag", "missing_sponsor", "missing_visa", "b13_census", "clean_approve"]
REVOKED = {"SPN-0007", "SPN-0139", "SPN-4040", "SPN-2718", "SPN-7331", "SPN-9090"}
FULL_EMBARGO = {"TRAPPIST-1e", "Eris Relay"}
PARTIAL_EMBARGO = {"Wolf-1061c"}
STALE_CUTOFF = date(2026, 1, 2)

# Doc-type ranks as used in packet provenance (rank, is_ocr) pairs.
DOC_NAMES = [(1, "adjudicator"), (2, "intake"), (3, "biometric"),
             (4, "sponsor"), (5, "registry"), (6, "fee")]


def staleness_days(arrival):
    try:
        y, m, d = map(int, arrival.split("-"))
        return max(-365, min(365, (STALE_CUTOFF - date(y, m, d)).days)) / 365.0
    except (ValueError, AttributeError):
        return 0.0


def featurize(pred, dbg, with_branch=True):
    """(prediction record, debug sidecar) -> {name: value} feature dict."""
    f = {}
    visa = pred["visa_class"]
    for v in VISAS:
        f[f"visa={v}"] = visa == v
    f["visa=unknown"] = visa not in VISAS
    fee = pred["fee_status"]
    for v in FEES:
        f[f"fee={v}"] = fee == v
    sponsor = pred["sponsor_id"]
    f["sponsor_present"] = sponsor != "SPN-0000"
    f["sponsor_revoked"] = sponsor in REVOKED
    f["dip_no_sponsor"] = (visa == "DIP-1") and sponsor == "SPN-0000"
    arrival = pred["arrival_date"]
    f["arrival_present"] = arrival != "1900-01-01"
    f["staleness"] = staleness_days(arrival) if f["arrival_present"] else 0.0
    flags = set(dbg["flags"])
    for fl in FLAGS:
        f[f"flag={fl}"] = fl in flags
    f["n_flags"] = len(flags)
    reg = dbg.get("registry_status", "")
    f["registry=CLEAR"] = reg == "CLEAR"
    f["registry=FLAGGED"] = bool(reg) and reg != "CLEAR"
    f["registry=absent"] = not reg
    finding = dbg.get("finding")
    for c in CLASSES:
        f[f"finding={c}"] = finding == c
    f["finding=none"] = finding is None
    docs = set(dbg["doc_types"])
    for dt, name in DOC_NAMES:
        f[f"has_{name}"] = dt in docs
    f["n_pages"] = dbg.get("n_pages", 0)
    f["n_scan_pages"] = dbg.get("scan_only_pages", 0)
    f["hidden_present"] = dbg.get("hidden_lines", 0) > 0
    f["n_fields_missing"] = dbg.get("n_fields_missing", 0)
    f["n_corrections"] = dbg.get("n_corrections", 0)
    f["waiver_present"] = bool(dbg.get("waiver_code"))
    world = pred["home_world"]
    f["embargo_world"] = world in FULL_EMBARGO
    f["partial_embargo_world"] = world in PARTIAL_EMBARGO
    prov = dbg.get("provenance", {})
    srcs = [v[1] for v in prov.values()]
    f["n_ocr_fields"] = sum(srcs)
    f["worst_doc_rank"] = max((v[0] for v in prov.values()), default=9)
    if with_branch:
        for b in BRANCHES:
            f[f"branch={b}"] = dbg["branch"] == b
        for c in CLASSES:
            f[f"rules={c}"] = dbg.get("rules_decision", pred["adjudication"]) == c
    return f


def names(with_branch=True):
    """Frozen feature order: sorted keys of a featurized dummy case."""
    dummy_pred = {"visa_class": "", "fee_status": "", "sponsor_id": "SPN-0000",
                  "arrival_date": "1900-01-01", "home_world": "", "adjudication": ""}
    dummy_dbg = {"flags": [], "doc_types": [], "branch": "", "provenance": {}}
    return sorted(featurize(dummy_pred, dummy_dbg, with_branch))


def vector(pred, dbg, with_branch=True, order=None):
    f = featurize(pred, dbg, with_branch)
    return [float(f[k]) for k in (order or names(with_branch))]
