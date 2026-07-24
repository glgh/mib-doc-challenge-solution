"""Orchestration: sequences the stages and owns the failure envelope.

Stages are independent of each other (see mib/stages/__init__.py); this is where
they are composed. Two entry points, split at the cache boundary:

  `read_case`              S1 + S2 — the expensive, impure half. Opens the PDF.
  `predict_from_evidence`  S3..emit — pure, cheap, and replayable from a cache.

That split is the whole point: `scripts/replay.py` re-runs the second half over
a stored page-text cache in seconds, so a parse or policy change is measurable
without paying for OCR again.
"""
import os
import sys
import time

from . import confidence, emit, packet, policy, signals
from .stages import extract, render

# Per-case OCR budget: a bound on the pathological case, not a tuning knob.
# Exceeding it drops to the text layer for the remaining pages rather than
# abandoning the case, because a partial read still scores.
#
# Deliberately set above the worst case ever measured (p50 1.6s / p90 9.8s /
# p99 57.5s / max 107s over 1,000 train packets) so that in normal operation it
# never fires and output stays reproducible. It is a wall-clock guard, so a
# value low enough to bite would make results depend on machine load — a 45s
# trial truncated a real case (MIB-000008) purely because the host was busy.
# What it actually bounds is the unbounded shape underneath: tesseract is capped
# at 20s per call, but a many-page scan tried at several variants each has no
# ceiling, and the contract stops the container at a fixed wall time and scores
# whatever was written. Retune only against the Docker parity run.
CASE_OCR_BUDGET_S = float(os.environ.get("MIB_CASE_BUDGET_S", "120"))


def read_case(pdf_path, budget_s=None):
    """S1 + S2 for one PDF -> (pages, ocr_lines_by_page).

    The document is opened once and held across both stages: rendering a scanned
    page needs the same handle S1 read from. Keeping that lifetime here is what
    lets `extract` avoid reaching forward into `render`, which is the backwards
    edge the previous `pdfio.read_pages` had.

    Variant selection (`render.best`) is S2's job and runs here, so the value
    that crosses into the pure downstream half — and into the cache — is the
    chosen line list per page, not the ensemble of readings. `best` is impure
    w.r.t. the parser's readability vocab, so keeping it above the cache boundary
    is what lets the replay path consume the decision instead of re-deriving it.
    """
    budget = CASE_OCR_BUDGET_S if budget_s is None else budget_s
    reads = {}
    deadline = time.monotonic() + budget if budget else None
    with extract.open_document(pdf_path) as doc:
        pages = extract.pages(doc)
        for page in pages:
            if not page.is_scan_only:
                continue
            if deadline and time.monotonic() > deadline:
                print(f"{pdf_path.name}: OCR budget spent, text layer only for "
                      f"page {page.page_no}+", file=sys.stderr)
                break
            reads[page.page_no] = render.reads_for(doc, doc[page.page_no],
                                                   page.page_no)
    ocr_lines = {no: render.best_lines(rs) for no, rs in reads.items()}
    return pages, ocr_lines


def predict(pdf_path):
    pages, ocr_lines = read_case(pdf_path)
    return predict_from_evidence(pages, ocr_lines, pdf_path.stem)


def predict_from_evidence(pages, ocr_lines, stem):
    """Everything downstream of page text: pure, cheap, independently testable.

    Split from `read_case` so the characterization tests and the replay gate can
    drive the real pipeline from frozen page text without re-running OCR — and
    without re-implementing this sequence, which would let them pass while the
    pipeline drifted underneath. `ocr_lines` is the already-chosen line list per
    page; selection happened in `read_case`, so nothing here re-derives it.
    """
    pkt = packet.assemble(pages, ocr_lines, fallback_case_id=stem)
    provenance = {}
    values = packet.merge_fields(pkt, provenance)
    sig = signals.derive(pkt, values)
    decision, branch = policy.adjudicate(values, sig)
    conf = confidence.for_branch(branch)
    record = emit.build_record(pkt.case_id, values, sig["emit_flags"], decision, conf)
    debug = {
        "case_id": pkt.case_id,
        "branch": branch,
        "provenance": {k: list(v) for k, v in provenance.items()},
        "doc_types": sorted({d for d, _, _ in pkt.docs}),
        "scan_only_pages": pkt.scan_only_pages,
        "has_biometric": sig["has_biometric"],
        "flags": sorted(sig["flags"]),
        "emit_flags": sorted(sig["emit_flags"]),
        "finding": sig["finding"],
        "waiver_code": sig["waiver_code"],
        "registry_status": (pkt.registry.get("registry_status") or "").strip().upper(),
        "n_pages": len(pages),
        "hidden_lines": sum(len(p.hidden_lines) for p in pages),
        "n_fields_missing": sum(1 for f in packet.parse.FIELDS if not values.get(f)),
        "n_corrections": len(packet.manual_corrections(pkt)),
        "rules_decision": decision,
    }
    # S5 decider swap: the learned decider always runs for the sidecar (permanent
    # A/B on every eval); MIB_DECIDER=mlp promotes its outputs into the record.
    # Failure falls back to the rules record — a missing/stale model file must
    # degrade the score, never crash a case.
    decider = os.environ.get("MIB_DECIDER", "rules").lower()
    debug["decider"] = decider
    try:
        from . import decision as learned
        mlp_dec, mlp_conf, mlp_probs = learned.decide(record, debug)
        debug["mlp_decision"], debug["mlp_confidence"], debug["mlp_probs"] = \
            mlp_dec, mlp_conf, mlp_probs
        if decider == "mlp":
            record = emit.build_record(pkt.case_id, values, sig["emit_flags"],
                                       mlp_dec, mlp_conf)
    except Exception as exc:  # noqa: BLE001
        debug["mlp_error"] = str(exc)
    return record, debug
