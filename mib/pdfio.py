"""Evidence intake: PDF → per-page visible text, with hidden content quarantined.

This is the injection trust boundary. Survey finding (docs/fraud-signals.md §1):
this corpus contains no legitimate hidden text, so any white/tiny/off-crop span
is adversarial by construction. Hidden lines are retained separately for
diagnostics only and must never feed field extraction or adjudication.
"""
from dataclasses import dataclass, field

import fitz  # PyMuPDF


@dataclass
class PageText:
    visible_lines: list = field(default_factory=list)
    hidden_lines: list = field(default_factory=list)
    ocr_lines: list = field(default_factory=list)
    image_count: int = 0

    @property
    def is_scan_only(self):
        return self.image_count > 0 and len(self.visible_lines) <= 3


def _is_whiteish(color_int):
    r, g, b = (color_int >> 16) & 255, (color_int >> 8) & 255, color_int & 255
    return r > 240 and g > 240 and b > 240


def read_pages(pdf_path):
    """Return a PageText per page, splitting spans into visible vs hidden."""
    pages = []
    with fitz.open(pdf_path) as doc:
        for page in doc:
            pt = PageText(image_count=len(page.get_images()))
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
            if pt.is_scan_only:
                from . import ocr
                pt.ocr_lines = ocr.ocr_page(doc, page)
            pages.append(pt)
    return pages
