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
    """Cached page text -> (pages, ocr_lines), as `runner.read_case` produces.

    The cache stores the line list S2 chose per page, so replay receives that
    selection directly and never re-runs `render.best`. That is what keeps the
    downstream half a pure function of the stored text: the old form rebuilt a
    single `Read` and let replay re-select over it, which matched the live run
    only while there was one variant and would silently diverge (all-zero
    `quality` tie-break) the moment the format grew an ensemble.

    `is_scan_only` is not restored even though it is written for readability: it
    is derived, so recomputing keeps a cache from pinning a stale definition of
    "this page is a scan".
    """
    from .records import Page
    pages, ocr_lines = [], {}
    for i, p in enumerate(page_dicts):
        page_no = p.get("page_no", i)
        pages.append(Page(
            page_no=page_no,
            visible_lines=list(p["visible_lines"]),
            hidden_lines=list(p["hidden_lines"]),
            image_count=p["image_count"],
        ))
        if p.get("ocr_lines"):
            ocr_lines[page_no] = list(p["ocr_lines"])
    return pages, ocr_lines


def from_case(pages, ocr_lines):
    """(pages, ocr_lines) -> the serializable page dicts this module reads back.

    `ocr_lines` is already the chosen line list per page (variant selection is
    S2's job, done in `runner.read_case`), so the cache stores S2's decision
    verbatim rather than re-deriving it — which is why this module no longer
    imports `render`.
    """
    return [{
        "page_no": p.page_no,
        "visible_lines": p.visible_lines,
        "hidden_lines": p.hidden_lines,
        "ocr_lines": ocr_lines.get(p.page_no, []),
        "image_count": p.image_count,
        "is_scan_only": p.is_scan_only,
    } for p in pages]
