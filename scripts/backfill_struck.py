#!/usr/bin/env python3
"""Add `struck` (red-strikethrough value cells) to an existing page-text cache.

Strike detection is a pure function of the PDF vector layer — independent of OCR
and the restoration level — so an existing cache can be augmented in seconds
rather than rebuilt, which would re-run OCR for nothing. It uses the same
`stages.extract` code the live pipeline uses, so the augmented `struck` field is
exactly what a full `scripts/dump_text.py` rebuild would have written. The OCR
output (`ocr_lines`, `reads`) is copied through untouched, so `restore`/
`ocr_passes` are preserved and the result stays joinable with the source cache.

Usage: scripts/backfill_struck.py [in_cache.jsonl] [out_cache.jsonl] [pdf_dir]
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CH = ROOT.parent / "mib-doc-challenge"


def main(in_path, out_path, pdf_dir):
    from mib import cache
    from mib.stages import extract

    meta, records = cache.read(in_path)
    meta = dict(meta or {})
    meta["schema"] = 2
    meta["backfilled"] = "struck"

    n_pages = 0
    with cache.open_write(out_path, meta) as f:
        for rec in records:
            pdf = Path(pdf_dir) / f"{rec['stem']}.pdf"
            struck_by_page = {}
            if not rec.get("error") and pdf.exists():
                with extract.open_document(pdf) as doc:
                    for pg in extract.pages(doc):      # S1 only — no OCR
                        if pg.struck:
                            struck_by_page[pg.page_no] = pg.struck
            for p in rec.get("pages", []):
                p["struck"] = struck_by_page.get(p.get("page_no"), [])
                n_pages += bool(p["struck"])
            cache.append(f, rec)

    print(f"wrote {out_path}: {len(records)} cases, {n_pages} pages carry a strike")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "output/cache/train_bands.jsonl"),
         sys.argv[2] if len(sys.argv) > 2 else str(ROOT / "output/cache/train_bands_struck.jsonl"),
         sys.argv[3] if len(sys.argv) > 3 else str(CH / "data/train"))
