#!/usr/bin/env python3
"""Render high-zoom crops of the READ-CREATION RESIDUE — fields where no OCR read
holds the truth in any form — so a human can judge ink-present vs ink-absent.

Locus is heuristic: reads carry no bbox, so we find the field's label anchor in a
page's line sequence and map its ordinal to a vertical band. Misses fall back to a
whole-page crop flagged `label_unread`. Dev split only.

Writes PNG crops under output/viz/crops/residue/ and a manifest residue_manifest.json.
"""
import base64
import io
import json
import re
import unicodedata
from pathlib import Path

import fitz
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent.parent
CH = ROOT.parent / "mib-doc-challenge"
CACHE = ROOT / "output/cache/train_grid.jsonl"
OUT = ROOT / "output/viz/crops/residue"
OUT.mkdir(parents=True, exist_ok=True)

# curated residue sample: (case, field, pool-tag)
SAMPLE = [
    ("MIB-000016", "home_world", "closed-vocab DEAD"),
    ("MIB-000067", "home_world", "closed-vocab DEAD"),
    ("MIB-000436", "home_world", "closed-vocab FRAG_ONLY"),
    ("MIB-000138", "home_world", "closed-vocab SIGNAL_LOSES"),
    ("MIB-000018", "species_code", "closed-vocab SIGNAL_LOSES"),
    ("MIB-000114", "species_code", "closed-vocab SIGNAL_LOSES"),
    ("MIB-000134", "species_code", "closed-vocab SIGNAL_LOSES"),
    ("MIB-000146", "declared_purpose", "closed-vocab SIGNAL_LOSES"),
    ("MIB-000033", "risk_flags", "flag unreachable"),
    ("MIB-000068", "risk_flags", "flag unreachable"),
    ("MIB-000001", "risk_flags", "flag unreachable"),
    ("MIB-000065", "risk_flags", "flag unreachable"),
    ("MIB-000003", "applicant_name", "scalar unreachable"),
    ("MIB-000023", "applicant_name", "scalar unreachable"),
    ("MIB-000008", "sponsor_id", "scalar unreachable"),
    ("MIB-000010", "arrival_date", "scalar unreachable"),
]

# printed label text to locate exactly via the PDF text layer (page.search_for)
SEARCH = {
    "home_world": ["Home World"],
    "species_code": ["Species Code", "Species"],
    "declared_purpose": ["Declared Purpose", "Purpose"],
    "visa_class": ["Visa Class"],
    "sponsor_id": ["Sponsor ID", "Sponsor"],
    "applicant_name": ["Applicant Name", "Applicant", "Name"],
    "arrival_date": ["Arrival Date", "Arrival"],
    "fee_status": ["Fee Status", "Fee"],
    "risk_flags": ["Observed flags", "Observed Flags", "Risk Flags", "B-13", "Biometric"],
}
# soft anchors for line-text annotation (reads carry no bbox)
ANCHORS = {
    "home_world": ["world"], "species_code": ["species"],
    "declared_purpose": ["purpose"], "visa_class": ["visa"],
    "sponsor_id": ["sponsor"], "applicant_name": ["applicant", "name"],
    "arrival_date": ["arrival"], "fee_status": ["fee"],
    "risk_flags": ["observed flags", "flags", "risk", "b-13", "biohazard"],
}


def norm(s):
    s = unicodedata.normalize("NFKD", s)
    return re.sub(r"[^a-z0-9]", "", s.lower())


def load_oracles():
    arb = {(r["stem"], r["field"]): r
           for r in (json.loads(l) for l in open(ROOT / "output/viz/arb_oracle.jsonl"))}
    eb = {(r["stem"], r["field"]): r
          for r in (json.loads(l) for l in open(ROOT / "output/viz/exceed_bound.jsonl"))}
    flags = {}
    for l in open(ROOT / "output/viz/flags_oracle.jsonl"):
        if '"case"' not in l:
            continue
        r = json.loads(l)
        flags[r["case"]] = r
    return arb, eb, flags


def load_cache():
    recs = {}
    with open(CACHE) as f:
        f.readline()
        for line in f:
            r = json.loads(line)
            recs[r["stem"]] = r
    return recs


def page_sequences(pg):
    """Return list of (label, lines) sequences for a page: text layer + each read."""
    seqs = []
    if pg.get("visible_lines"):
        seqs.append(("text", pg["visible_lines"]))
    for rd in pg.get("reads", []):
        if rd.get("lines"):
            seqs.append((rd.get("variant", "ocr"), rd["lines"]))
    return seqs


def locate(doc, rec, field):
    """Exact locate via the PDF text layer. Returns (page_no, band, mode):
    mode 'text' = label found in a text-layer form (crop its row);
    mode 'scan' = no text layer holds the label (crop whole page, contrast-revealed)."""
    for term in SEARCH[field]:
        for page in doc:
            rects = page.search_for(term)
            if rects:
                r = page.rect
                # tallest/left-most hit, expand to a row band around it
                y0 = min(rc.y0 for rc in rects)
                y1 = max(rc.y1 for rc in rects)
                pad = 0.9 * (y1 - y0)
                top = max(0.0, (y0 - r.y0 - pad) / r.height)
                bot = min(1.0, (y1 - r.y0 + 2.4 * pad) / r.height)
                return page.number, (top, bot), "text"
    # no text-layer label anywhere -> scan page. Prefer the page whose OCR reads
    # mention the field's anchor (that's where the label survived); else densest.
    anchors = ANCHORS[field]
    scored = []
    for pg in rec["pages"]:
        anchor_hits = sum(1 for _, lines in page_sequences(pg)
                          for ln in lines if any(a in ln.lower() for a in anchors))
        dens = sum(len(l) for _, l in page_sequences(pg)) or 0
        scored.append((anchor_hits, dens, pg["page_no"]))
    scored.sort(reverse=True)
    return scored[0][2], (0.0, 1.0), "scan"


def reads_near(rec, page_no, field, truth):
    """Lines on the page that contain the anchor or resemble truth — the evidence."""
    anchors = ANCHORS[field]
    tnorm = norm(truth)
    out = []
    pg = next(p for p in rec["pages"] if p["page_no"] == page_no)
    for src, lines in page_sequences(pg):
        for ln in lines:
            low = ln.lower()
            hit_anchor = any(a in low for a in anchors)
            ln_norm = norm(ln)
            resembles = tnorm[:4] and tnorm[:4] in ln_norm
            if hit_anchor or resembles:
                out.append(f"[{src}] {ln.strip()}")
    # dedupe preserve order, cap
    seen, dedup = set(), []
    for x in out:
        if x not in seen:
            seen.add(x); dedup.append(x)
    return dedup[:6]


def main():
    arb, eb, flags = load_oracles()
    cache = load_cache()
    manifest = []
    for case, field, pool in SAMPLE:
        rec = cache[case]
        doc = fitz.open(CH / f"data/train/{case}.pdf")
        pno, (top, bot), mode = locate(doc, rec, field)
        page = doc[pno]
        r = page.rect
        zoom = 5 if mode == "text" else 3
        clip = fitz.Rect(r.x0, r.y0 + top * r.height, r.x1, r.y0 + bot * r.height)
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip)
        img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("L")
        # autocontrast reveals whether faint ink survives at all
        stretched = ImageOps.autocontrast(img, cutoff=0.5)
        fn = OUT / f"{case}_{field}.png"
        fn_s = OUT / f"{case}_{field}_stretch.png"
        img.save(fn)
        stretched.save(fn_s)
        doc.close()
        found = mode == "text"
        # annotations
        if field == "risk_flags":
            fr = flags.get(case, {})
            truth = "|".join(fr.get("truth", []))
            pred = "|".join(fr.get("emitted", [])) or "none"
        else:
            o = arb.get((case, field)) or {}
            truth = o.get("truth", "?")
            pred = o.get("predicted", "?")
        reads = reads_near(rec, pno, field, truth)
        manifest.append({
            "case": case, "field": field, "pool": pool,
            "page": pno, "band": [round(top, 3), round(bot, 3)], "mode": mode,
            "anchor_found": found, "truth": truth, "predicted": pred,
            "reads": reads, "png": fn.name,
            "b64": base64.b64encode(fn.read_bytes()).decode(),
            "b64_stretch": base64.b64encode(fn_s.read_bytes()).decode(),
        })
        tag = "text-layer form" if found else "SCAN (whole page, contrast)"
        print(f"{case} {field:16s} p{pno} [{tag}] "
              f"truth={truth!r} pred={pred!r}")
    (ROOT / "output/viz/residue_manifest.json").write_text(json.dumps(manifest))
    print(f"\n{len(manifest)} crops -> {OUT}")


if __name__ == "__main__":
    main()
