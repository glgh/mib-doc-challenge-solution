"""Provenance-stamped JSONL: one `_meta` header line, then one record per line.

The header is what makes a cache file self-describing, so a consumer can refuse
a join instead of quietly measuring the wrong thing (see mib/config.py). Files
written before stamping existed still read fine, with `meta=None`.
"""
import json
from pathlib import Path

META_KEY = "_meta"


def write_header(f, meta):
    f.write(json.dumps({META_KEY: meta}, sort_keys=True) + "\n")
    f.flush()


def open_write(path, meta):
    """Open a cache for writing and stamp it. The caller streams records in."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    f = open(path, "w")
    write_header(f, meta)
    return f


def append(f, record):
    """One record, flushed: a partially written cache stays usable."""
    f.write(json.dumps(record) + "\n")
    f.flush()


def read(path):
    """-> (meta, records). meta is None for an unstamped file."""
    meta, records = None, []
    with open(path) as f:
        for i, line in enumerate(f):
            if not line.strip():
                continue
            rec = json.loads(line)
            if i == 0 and isinstance(rec, dict) and META_KEY in rec:
                meta = rec[META_KEY]
                continue
            records.append(rec)
    return meta, records


def to_case(page_dicts):
    """Cached page text -> (pages, reads_by_page), as `runner.read_case` produces.

    The cache stores the whole OCR ensemble per page (`reads`), so replay-time
    selection is a pure function of the stored readings and matches the live run
    exactly. The `ocr_lines`-only branch below is the **fixture format**, not
    legacy-cache support
    (tests/fixtures/characterization.json stores one read per page to stay
    small and stable; it rehydrates as a one-read ensemble, over which every
    selection strategy is the identity). Old on-disk caches are regenerated,
    not accommodated (user call, 2026-07-26).

    `is_scan_only` is not restored even though it is written for readability: it
    is derived, so recomputing keeps a cache from pinning a stale definition of
    "this page is a scan".
    """
    from .records import Page, Read
    pages, reads_by_page = [], {}
    for i, p in enumerate(page_dicts):
        page_no = p.get("page_no", i)
        pages.append(Page(
            page_no=page_no,
            visible_lines=list(p["visible_lines"]),
            hidden_lines=list(p["hidden_lines"]),
            struck=list(p.get("struck", [])),   # the fixture format carries no strikes
            image_count=p["image_count"],
        ))
        if p.get("reads") is not None:
            reads = [Read(page_no=page_no, lines=list(r["lines"]),
                          variant=r["variant"], conf=r["conf"],
                          cost_ms=r["cost_ms"])
                     for r in p["reads"]]
        elif p.get("ocr_lines"):
            reads = [Read(page_no=page_no, lines=list(p["ocr_lines"]),
                          variant="cache")]
        else:
            reads = []
        if reads:
            reads_by_page[page_no] = reads
    return pages, reads_by_page


def from_case(pages, reads_by_page):
    """(pages, reads_by_page) -> the serializable page dicts this module reads back.

    Every reading survives serialization — the collapse this format used to bake
    in is exactly what the merge now needs undone. `ocr_lines` (the primary
    reading's lines) is still written: tools that only want "the page's text"
    (textmatch, recoverable) read it, and old-format consumers stay compatible.
    """
    from .records import best_read
    out = []
    for p in pages:
        reads = reads_by_page.get(p.page_no) or []
        primary = best_read(reads)
        out.append({
            "page_no": p.page_no,
            "visible_lines": p.visible_lines,
            "hidden_lines": p.hidden_lines,
            "struck": p.struck,
            "ocr_lines": primary.lines if primary else [],
            # cost_ms is wall clock: real for offline cost analysis, poison for
            # any identity comparison (verify_render excludes it by key).
            "reads": [{"variant": r.variant,
                       "conf": r.conf, "lines": r.lines,
                       "cost_ms": r.cost_ms} for r in reads],
            "image_count": p.image_count,
            "is_scan_only": p.is_scan_only,
        })
    return out
