"""S1 — evidence intake: PDF -> Page[], with hidden content quarantined.

This is the injection trust boundary. Survey finding (docs/BACKGROUND.md §3):
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
    # The "white" arm of the docstring's white/tiny/off-crop hidden-span taxonomy:
    # white-on-white injection text, all channels within ~15/255 of full white.
    r, g, b = (color_int >> 16) & 255, (color_int >> 8) & 255, color_int & 255
    return r > 240 and g > 240 and b > 240


def _red_strikes(page):
    """Red strikethrough lines on a page: thin, horizontal, reddish vector strokes.

    The corpus voids a printed value by drawing a red line through it (a fee
    receipt's `unpaid`, an intake's decoy sponsor/visa). It lives in the vector
    layer, so the text layer still reads the voided value — which is exactly why
    a text-only pipeline emits it. Returns the strokes' rects for span testing.
    """
    rects = []
    for dr in page.get_drawings():
        color = dr.get("color")
        # Reddish vector stroke in float RGB (R dominant, G/B suppressed) — the
        # red-strikethrough void mechanism catalogued in docs/BACKGROUND.md §3.
        if not color or not (color[0] > 0.5 and color[1] < 0.45 and color[2] < 0.45):
            continue
        if not any(item[0] == "l" for item in dr.get("items", [])):
            continue
        r = dr["rect"]
        if r.height < 4 and r.width > 3:           # a strikethrough, not a box/stamp
            rects.append(r)
    return rects


def _span_struck(strikes, span_bbox):
    """Does a red strike cross this span? (horizontal overlap, strike mid in band.)"""
    b = fitz.Rect(span_bbox)
    for s in strikes:
        smid = (s.y0 + s.y1) / 2
        # horizontal overlap, and the strike's midline within the span's y-band
        # (±2pt slack for stroke thickness / baseline jitter).
        if s.x0 <= b.x1 and s.x1 >= b.x0 and (b.y0 - 2) <= smid <= (b.y1 + 2):
            return True
    return False


def page_text(page, page_no):
    """Split one page's spans into visible, hidden, and struck-through."""
    pt = Page(page_no=page_no, image_count=len(page.get_images()))
    crop = page.rect
    strikes = _red_strikes(page)
    d = page.get_text("dict")
    for block in d.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            shown, hidden, is_struck = [], [], False
            for span in line.get("spans", []):
                text = span.get("text", "")
                if not text.strip():
                    continue
                is_hidden = (                              # white / tiny / off-crop
                    _is_whiteish(span.get("color", 0))
                    or span.get("size", 12.0) < 2.0        # sub-2pt: unreadable "tiny" tell
                    or not crop.intersects(fitz.Rect(span["bbox"]))
                )
                if is_hidden:
                    hidden.append(text)
                    continue
                shown.append(text)
                if strikes and _span_struck(strikes, span["bbox"]):
                    is_struck = True
            if shown:
                line_text = " ".join(shown).strip()
                pt.visible_lines.append(line_text)
                if is_struck:               # the same string parse_kv reads as a value
                    pt.struck.append(line_text)
            if hidden:
                pt.hidden_lines.append(" ".join(hidden).strip())
    return pt


def pages(doc):
    return [page_text(page, i) for i, page in enumerate(doc)]
