"""Orchestration: sequences the stages and owns the failure envelope.

Stages are independent of each other (see mib/stages/__init__.py); this is where
they are composed. Two entry points, split at the cache boundary:

  `read_case`              S1 + S2 — the expensive, impure half. Opens the PDF.
  `predict_from_evidence`  S3..emit — pure, cheap, and replayable from a cache.

That split is the whole point: `scripts/replay.py` re-runs the second half over
a stored page-text cache in seconds, so a parse or policy change is measurable
without paying for OCR again.
"""
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
# whatever was written. Retune only against the Docker parity run; experiments
# override per-call via `read_case(pdf, budget_s=...)`, not the environment.
CASE_OCR_BUDGET_S = 120.0


def read_case(pdf_path, budget_s=None):
    """S1 + S2 for one PDF -> (pages, reads_by_page).

    The document is opened once and held across both stages: rendering a scanned
    page needs the same handle S1 read from. Keeping that lifetime here is what
    lets `extract` avoid reaching forward into `render`, which is the backwards
    edge the previous `pdfio.read_pages` had.

    `reads_by_page` carries EVERY reading S2 produced (page_no -> list[Read]) —
    the seam no longer collapses the ensemble to one winner. Selection happens
    per field at the S4 merge (`packet`), which is the point: a losing variant
    can still hold the best copy of one field or the only legible risk flag.
    Each Read stores the evidence score S2 computed, so downstream selection is
    a pure function of the stored ensemble and replays faithfully from a cache.
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
    return pages, reads


def predict(pdf_path):
    pages, reads_by_page = read_case(pdf_path)
    return predict_from_evidence(pages, reads_by_page, pdf_path.stem)


def predict_from_evidence(pages, reads_by_page, stem):
    """Everything downstream of page text: pure, cheap, independently testable.

    Split from `read_case` so the characterization tests and the replay gate can
    drive the real pipeline from frozen page text without re-running OCR — and
    without re-implementing this sequence, which would let them pass while the
    pipeline drifted underneath. `reads_by_page` is the stored OCR ensemble
    (page_no -> list[Read]); everything from selection onward happens in here,
    so a cache replay exercises the very same code path as a live run.
    """
    pkt = packet.assemble(pages, reads_by_page, fallback_case_id=stem)
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
    }
    # The learned decider that used to swap in here (MIB_DECIDER=mlp) was
    # deleted after decision-layer ML was closed: its edge over rules inverted
    # to −0.50 with 14 CFAs once rules strengthened (experiments.md row 18
    # follow-up; STATUS.md). Recoverable from git history if ever revived.
    return record, debug
