"""S1 — evidence intake: PDF -> Page[], with hidden content quarantined.

This is the injection trust boundary. Survey finding (docs/fraud-signals.md §1):
this corpus contains no legitimate hidden text, so any white/tiny/off-crop span
is adversarial by construction. Hidden lines are retained separately for
diagnostics and for the injection tests, and must never feed field extraction or
adjudication.

This module is the only one that opens a PDF. Rendering a scanned page needs the
same open document, so `open_document` is exposed for `mib/runner.py` to hold
open across S1 and S2 — rather than S1 reaching forward into S2 itself, which is
what the previous `pdfio.read_pages` did via an inline `from . import ocr`.
"""
import fitz  # PyMuPDF

from ..records import Page


def open_document(pdf_path):
    return fitz.open(pdf_path)


def _is_whiteish(color_int):
    r, g, b = (color_int >> 16) & 255, (color_int >> 8) & 255, color_int & 255
    return r > 240 and g > 240 and b > 240


def page_text(page, page_no):
    """Split one page's spans into visible and hidden."""
    pt = Page(page_no=page_no, image_count=len(page.get_images()))
    crop = page.rect
    d = page.get_text("dict")
    for block in d.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            shown, hidden = [], []
            for span in line.get("spans", []):
                text = span.get("text", "")
                if not text.strip():
                    continue
                is_hidden = (
                    _is_whiteish(span.get("color", 0))
                    or span.get("size", 12.0) < 2.0
                    or not crop.intersects(fitz.Rect(span["bbox"]))
                )
                (hidden if is_hidden else shown).append(text)
            if shown:
                pt.visible_lines.append(" ".join(shown).strip())
            if hidden:
                pt.hidden_lines.append(" ".join(hidden).strip())
    return pt


def pages(doc):
    return [page_text(page, i) for i, page in enumerate(doc)]
