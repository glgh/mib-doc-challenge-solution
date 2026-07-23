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
    """Cached page text -> (pages, reads), as `runner.read_case` would produce.

    The stored form keeps one OCR reading per page — the one S2 chose — so each
    replays as a single `Read` tagged `cached`. That is a deliberate limit of the
    *format*, not of the pipeline: S2 already produces every variant, and this
    file grows a list when there is an ensemble to store (P3).

    `is_scan_only` is not restored even though it is written for readability: it
    is derived, so recomputing keeps a cache from pinning a stale definition of
    "this page is a scan".
    """
    from .records import Page, Read
    pages, reads = [], {}
    for i, p in enumerate(page_dicts):
        page_no = p.get("page_no", i)
        pages.append(Page(
            page_no=page_no,
            visible_lines=list(p["visible_lines"]),
            hidden_lines=list(p["hidden_lines"]),
            image_count=p["image_count"],
        ))
        if p.get("ocr_lines"):
            reads[page_no] = [Read(page_no=page_no, lines=list(p["ocr_lines"]),
                                   variant="cached")]
    return pages, reads


def from_case(pages, reads):
    """(pages, reads) -> the serializable page dicts this module reads back."""
    from .stages import render
    return [{
        "page_no": p.page_no,
        "visible_lines": p.visible_lines,
        "hidden_lines": p.hidden_lines,
        "ocr_lines": render.best_lines(reads.get(p.page_no, [])),
        "image_count": p.image_count,
        "is_scan_only": p.is_scan_only,
    } for p in pages]


def read_meta(path):
    """The header alone, without paying to parse the body."""
    path = Path(path)
    if not path.exists():
        return None
    with open(path) as f:
        line = f.readline()
    if not line.strip():
        return None
    rec = json.loads(line)
    return rec[META_KEY] if isinstance(rec, dict) and META_KEY in rec else None
