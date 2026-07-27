"""S2 — OCR for scan-only pages. Mandatory, not a fallback: ~47% of train pages
(85% of packets have at least one) carry their visible content only as pixels.

Recipe validated on train scans (docs/experiments.md): Tesseract PSM 11
(sparse text) recovers structured Key: Value lines where PSM 4/6 fail; embedded
raster is preferred (already the source image, no re-render cost); pages whose
embedded image is small or yields nothing are re-rendered at ~200 DPI. A PSM 3
(full-layout) pass exists only as the grid's last-resort tier (one call on the
best frame of a still-dead page); the per-image `dual` second pass it replaced
was deleted with the other legacy knobs (de-special-casing batch, 2026-07-26).

Pages that read badly are usually damaged *geometrically* rather than
optically (turned, skewed, or shredded into offset bands), so every page is
enumerated through mib.imaging corrections rather than at higher resolution.
The transforms compose in the order they undo real damage: deskew before
deshred, because band detection reads the page border and a skewed border is a
moving reference (see `_orientation_chains`). The `local` rung is the
text-consent corrector (imaging.realign_local, graduated 2026-07-26): same
walk, but at seams that cut through text the cut glyph halves override the
border's implied shift; it is emitted only when its pixels differ from the
plain deshred. The pre-grid `ladder` enumerator and its switchable
`off`/`skew`/`turn` predecessors are gone (experiments.md rows 11-14, 59-60);
the record stays in the docs and in git.

OCR is exhaustive: every variant is produced and read, and `best()` keeps the
strongest. An earlier design stopped as soon as a reading looked good enough;
that measured −0.21 dev (experiments.md row 16) because it settled for a worse
variant while spending the most OCR on the hardest pages, which never cleared
the bar anyway. The per-case wall-clock bound (`runner.CASE_OCR_BUDGET_S`)
is what keeps this affordable, not skipping work.

`reads_for` returns **every** reading it produced, not just the winner — the
discarded readings survive the seam, which is what an ensemble over variants
needs. Choosing among them is `best()`, deliberately separate.
"""
import difflib
import hashlib
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

import fitz
import numpy as np
from PIL import Image

from .. import grammar
from .. import imaging
from ..config import ocr_optical as _ocr_optical
from ..records import Read, best_read, conf_excess_mass
from ..vocab import FLAGS, HOME_WORLDS, SPECIES, VISAS, clean_ocr_line

MIN_EMBEDDED_WIDTH = 1000
RENDER_ZOOM = 2.8       # ~200 DPI floor
# Never render BELOW the page's native scan resolution. On train this is a
# provable no-op (every embedded scan is <=144 DPI: 1224px full pages and 512px
# photo boxes, measured corpus-wide 2026-07-25), but a private-set packet
# scanned at 300 DPI would silently lose resolution to a fixed zoom. Capped at
# ~300 DPI: past that, tesseract gains nothing and render cost grows quadratically.
MAX_RENDER_ZOOM = 4.2   # ~300 DPI cap

# Page-segmentation modes. PSM 11 (sparse) is the primary the corpus was tuned
# on; PSM 3 (full auto layout) runs only in the grid's last-resort tier.
PRIMARY_PSM = 11
SECONDARY_PSM = 3


def _recognize(image_path, psm=PRIMARY_PSM, dpi=None):
    """One tesseract invocation, two renderers -> (lines, conf).

    The `txt` renderer is byte-identical to the old stdout pass (verified live),
    so text behavior cannot drift; the `tsv` renderer rides along in the same
    recognition pass at ~1x cost and yields `conf`: one (mean word conf,
    n_words, y_fraction) triple per tsv text line. The two renderers group
    lines differently (93/111 diverge), so `conf` is a parallel measurement of
    the page, NOT aligned 1:1 with `lines` — page-level metrics need no
    alignment, per-line uses fuzzy-match on demand.
    """
    base = Path(str(image_path) + "_o")
    args = ["tesseract", str(image_path), str(base), "--psm", str(psm)]
    if dpi:
        args += ["--dpi", str(dpi)]
    args += ["txt", "tsv"]
    try:
        subprocess.run(args, capture_output=True, text=True, timeout=20,
                       env={**os.environ, "OMP_THREAD_LIMIT": "1"})
        txt = Path(str(base) + ".txt")
        tsv = Path(str(base) + ".tsv")
        lines = [clean_ocr_line(l)
                 for l in txt.read_text(errors="replace").splitlines() if l.strip()]
        return lines, _parse_tsv(tsv)
    except (subprocess.TimeoutExpired, OSError):
        return [], None


def _parse_tsv(tsv_path):
    """Tesseract tsv -> [(mean word conf, n_words, y_frac, text)] per line, or None.

    Lines whose cleaned joined text is empty are dropped (pure-punctuation
    debris), matching how the text pass drops blank lines. y_frac is the line's
    top edge as a fraction of image height — the positional page-furniture
    guard keys off it (the printed footer band), so it must survive the seam.
    `text` (schema 4) is the line's cleaned words: the tsv renderer's own
    grouping, which is what makes per-line confidence queryable — "how
    confident was the engine about *this* line" needs the line written next to
    its number.
    """
    try:
        rows = tsv_path.read_text(errors="replace").splitlines()
    except OSError:
        return None
    height = 0
    out = []
    cur_key, words, confs, y_top = None, [], [], 0
    def flush():
        text = clean_ocr_line(" ".join(words)) if words else ""
        if text:
            out.append((round(sum(confs) / len(confs), 1), len(confs),
                        round(y_top / height, 4) if height else 0.0, text))
    for row in rows[1:]:
        f = row.split("\t")
        if len(f) < 12:
            continue
        if f[0] == "1":
            height = int(f[9])
        if f[0] != "5" or not f[11].strip() or float(f[10]) < 0:
            continue
        key = (f[1], f[2], f[3], f[4])
        if key != cur_key:
            flush()
            cur_key, words, confs, y_top = key, [], [], int(f[7])
        words.append(f[11])
        confs.append(float(f[10]))
    flush()
    return out


def _tesseract(image_path, psm=PRIMARY_PSM, dpi=None):
    """Text-only compatibility wrapper (tools call this); pipeline uses _recognize."""
    return _recognize(image_path, psm, dpi)[0]


# Optical rendering modules: contrast-domain fixes applied AFTER geometry
# (binarizing before rotation re-blurs the strokes the threshold just sharpened
# — measured on MIB-000061: adapt-then-skew kept `Fee Stan waved`, skew-then-
# adapt read `Fee Status waved` verbatim). `adapt` recovers faint/unevenly-lit
# ink a global binarization erases; `autocon` stretches gray-stock levels.
# Gated to weak pages: the unguarded A/B showed well-formed-but-wrong binarized
# reads displacing correct ones on pages that already read well (11 recovered /
# 10 corrupted, dev).
_OPTICAL_MODULES = {
    "adapt": imaging.local_threshold,
    "autocon": imaging.autocontrast,
}


def _orientation_chains(gray, q, skew_deg, geom):
    """(chain, image) pairs for ONE orientation frame's correction ladder.

    Canonical order inside the frame: turn -> deskew -> band realign. The
    correction detectors self-gate in-frame (`realign_bands` returns None when
    no full-width border is found there), which is what lets a turned+shredded
    page finally get the band fix in the right frame — the flat ladder ran
    deshred only at 0 degrees. Turn frames are always resampled by their
    in-frame argmax angle (the historical behaviour); the `skew` chain segment
    is claimed only when the angle clears MIN_SKEW.
    """
    if q and f"turn{q}" not in geom:
        return
    chain = () if q == 0 else (f"turn{q}",)
    frame = gray if q == 0 else imaging.turn(gray, q)
    skewed = abs(skew_deg) >= imaging.MIN_SKEW
    if q:
        base = imaging.rotate(frame, skew_deg)
        base_chain = chain + (("skew",) if skewed else ())
        yield base_chain, base
    elif "skew" in geom and skewed:
        base = imaging.rotate(frame, skew_deg)
        base_chain = chain + ("skew",)
        yield base_chain, base
    else:
        base, base_chain = frame, chain
    deshredded = None
    if "deshred" in geom:
        deshredded = imaging.realign_bands(base)
        if deshredded is not None:
            yield base_chain + ("deshred",), deshredded
    if "local" in geom:
        corrected = imaging.realign_local(base)
        if corrected is not None and (deshredded is None
                                      or not np.array_equal(corrected, deshredded)):
            yield base_chain + ("local",), corrected


# ---------------------------------------------------------------------------
# page_score: the weak-page gate, decoupled from S3.
#
# Its predecessor `evidence_score` imported parse/vocab, so a KEY_MAP edit
# silently changed which pages the optical rung expanded on and invalidated the
# OCR cache (the documented "S3's vocabulary silently drives S2" hazard; the
# score itself died with the ladder in the de-special-casing batch). This score
# is a FROZEN snapshot (2026-07-27) — deliberately NOT imported — plus the two
# guards the arbitration lab proved necessary (experiments/probe_arbitration
# m_guards): label credit needs a >=2-token line, and watermark lines
# (SAMPLE DENIAL / SPECIMEN / COPY / VOID) earn nothing, so a page cannot
# saturate the bar on boilerplate while its faint field block goes unread.
_SCORE_LABELS = (
    "case id", "applicant", "purpose", "registry name", "species code",
    "species match", "home world", "visa class", "sponsor id", "arrival date",
    "declared purpose", "fee status", "waiver code", "registry status",
    "observed flags", "biometric confidence", "finding",
)
# \b-anchored field-token shapes (owned by `grammar`): a variant that surfaces
# more well-formed ids/dates read more of the page's structured content.
_SCORE_VALUE_PATTERNS = (grammar.FIND_CASE_ID, grammar.FIND_SPONSOR, grammar.FIND_DATE)
# The controlled vocabularies (owned by `vocab`): a variant that surfaces more
# known visa/species/world/flag terms has read more of the page's real content.
# "none" is excluded — it is not a term a scan would legibly print as evidence.
_SCORE_VALUE_WORDS = tuple(VISAS + SPECIES + HOME_WORLDS +
                           [f for f in FLAGS if f != "none"])
_WATERMARK_RE = re.compile(r"\b(sample|denial|specimen|copy|void)\b", re.I)

# Initial bar mirrors the retired GOOD_ENOUGH=6 for continuity; re-derive from
# the dev score distribution when it moves (the ev distribution's valley sat at
# 5, not the hand-picked 6 — hand-picked bars go stale).
WEAK_BAR = 6


def page_score(lines):
    """Frozen, guarded evidence shape of one reading (see block comment)."""
    score = 0
    kept = []
    for line in lines:
        if _WATERMARK_RE.search(line):
            continue
        kept.append(line)
        if len(line.split()) < 2:
            continue
        head = line.split(":")[0].split(".")[0].split(";")[0].strip().lower()
        if head in _SCORE_LABELS or difflib.get_close_matches(
                head, _SCORE_LABELS, n=1, cutoff=0.8):
            score += 1
    text = "\n".join(kept)
    score += sum(len(p.findall(text)) for p in _SCORE_VALUE_PATTERNS)
    score += sum(1 for w in _SCORE_VALUE_WORDS
                 if re.search(r"\b" + re.escape(w) + r"\b", text))
    return score


def _sources(doc, page, tmp):
    """Page pixels to read, as (name, encoded_bytes, grayscale array): embedded
    raster first, then a full-page render at >=200 DPI — raised to the native
    resolution of the page's largest embedded image (MAX_RENDER_ZOOM cap) so a
    high-DPI scan is never downsampled by the fixed floor. The encoded bytes are
    kept so the unrestored pass reads exactly the original image, not a
    re-encode of it."""
    images = page.get_images()
    if images:
        img = doc.extract_image(images[0][0])
        if img["width"] >= MIN_EMBEDDED_WIDTH:
            yield "embedded", img["image"], imaging.to_gray(img["image"])
    # get_images tuples carry (xref, smask, width, ...) — no decode needed.
    native_px = max((im[2] for im in images), default=0)
    native_zoom = native_px / max(1.0, page.rect.width)
    zoom = max(RENDER_ZOOM, min(native_zoom, MAX_RENDER_ZOOM))
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
    # Straight from the pixmap samples — the old path went pixmap -> PNG ->
    # PIL -> array, paying a deflate encode + decode for identical pixels
    # (PNG is lossless). Tesseract gets the same RGB samples as PNM (P6).
    rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    gray = np.asarray(Image.fromarray(rgb).convert("L"))
    yield "render", imaging.to_pnm_bytes(rgb), gray


def reads_for(doc, page, page_no):
    """Every OCR reading of one page, in generation order (cheapest first).

    The canonical composition grid (config.grid_plan): raw + EVERY
    orientation's correction chain unconditionally (each turn frame gets its
    OWN in-frame corrections, hint-ordered; gating turns on page-level weakness
    was offline-proven unsafe the day it was designed — see the base-tier
    comment); when the page still reads weak (frozen `page_score` < WEAK_BAR),
    expand with the optical modules composed over the corrected frames, not
    just raw gray; a still-dead page may get one last-resort PSM-3 pass on its
    best frame. Expansion only ever ADDS beyond the unconditional base — the
    early stop that truncated coverage measured −0.21 (row 16) and stays
    impossible by construction. Pixel-hash dedupe keeps no-op compositions
    from paying an OCR pass.

    A 300-DPI grayscale/autocontrast retry was tried here and reverted: +0.21
    dev pts for 43x runtime (experiments.md row 8). The pages it targeted were
    turned or shredded, not low-resolution — hence the geometric path instead.
    """
    from .. import config
    plan = config.grid_plan()
    reads = []
    with tempfile.TemporaryDirectory(prefix="mibocr") as tmp:
        written = 0
        hashed = set()

        def read(encoded, variant, dpi=None, only_psm=None):
            nonlocal written
            written += 1
            path = Path(tmp) / f"p{written}.png"
            path.write_bytes(encoded)
            psm = only_psm or PRIMARY_PSM
            suffix = "" if psm == PRIMARY_PSM else f"+psm{psm}"
            t0 = time.time()
            lines, conf = _recognize(path, psm, dpi)
            reads.append(Read(page_no=page_no, lines=lines, variant=variant + suffix,
                              conf=conf,
                              cost_ms=round((time.time() - t0) * 1000)))

        frame_images = {}

        def read_image(image, variant):
            """OCR a restored frame once per unique pixel content."""
            digest = hashlib.sha1(image.tobytes()).hexdigest()
            if digest in hashed:
                return
            hashed.add(digest)
            frame_images[variant] = image
            read(imaging.to_pnm_bytes(image), variant)

        sources = list(_sources(doc, page, tmp))

        geom, opt = plan["geom"], plan["opt"]
        oprofs = {name: imaging.orientation_profile(gray)
                  for name, _e, gray in sources}
        for _name, _e, gray in sources:
            hashed.add(hashlib.sha1(gray.tobytes()).hexdigest())
        # finals[(source, q)] = (chain, image): the most-corrected frame per
        # orientation actually produced — the bases optical composes over.
        finals = {}

        def emit_orientation(name, gray, q):
            for chain, image in _orientation_chains(
                    gray, q, oprofs[name][q]["skew_deg"], geom):
                finals[(name, q)] = (chain, image)
                read_image(image, "+".join((name,) + chain))

        # Base tier: raw + EVERY orientation's correction chain, unconditional.
        # Gating turns on page-level weakness was designed, offline-proven
        # unsafe, and reverted the same day (2026-07-27): a page can clear the
        # bar on its raw read while the TURN read carries the actual field
        # block (MIB-000509/501/235 lost species/world/sponsor in the drop
        # test) — page-grain weakness cannot license skipping an orientation.
        # The hints still ORDER the turns (sharpest projection first).
        for name, encoded, gray in sources:
            # Honest DPI: the retired ladder kept the 96-DPI pHYs accident its
            # tuning inherited (row 40); the hard-set probe (2026-07-27,
            # experiments/dpi_probe.py) scored honest 37 wins / 19 losses /
            # 296 ties against it, so the grid declares the render's REAL
            # resolution and the accident died with the flip.
            honest = round(72 * gray.shape[1] / max(1.0, page.rect.width))
            read(encoded, name, dpi=honest if name == "render" else None)
            frame_images[name] = gray
            finals[(name, 0)] = ((), gray)
            # Fixed orientation order. Hint-ordering the base tier was built
            # and removed the same day: both turns always run, so ordering
            # buys nothing there — it only shuffles read order, which feeds
            # the vote's first-seen tie-breaks (an uncontrolled degree of
            # freedom). The calibrated hint (imaging.orientation_profile,
            # 12/14 on the labeled registry with rule-stripped glyphs) is for
            # future GATED consumers: expansion priority, psm3 frame choice.
            for q in (0, 1, 3):
                emit_orientation(name, gray, q)

        def weak():
            if not reads:
                return True
            return max(page_score(r.lines) for r in reads) < WEAK_BAR

        # Expansion tier: optical composition over the corrected frames, only
        # while the page still reads weak.
        if weak():
            if _ocr_optical() and opt:
                for name, encoded, gray in sources:
                    if plan["opt_base"] == "frames":
                        bases = [((), gray)] + [
                            finals[(name, q)] for q in (0, 1, 3)
                            if finals.get((name, q)) and finals[(name, q)][0]]
                    else:
                        bases = [((), gray)]
                    for chain, image in bases:
                        for mod in opt:
                            read_image(_OPTICAL_MODULES[mod](image),
                                       "+".join((name,) + chain + (mod,)))
        # Last-resort tier: one full-layout pass on the best frame of a page
        # nothing else could read (row 20's +0.87 recipe, affordable only
        # because it is one call per still-dead page, never per image).
        if plan["last_resort"] == "psm3" and weak():
            candidates = [r for r in reads if r.variant in frame_images]
            if candidates:
                top = max(candidates,
                          key=lambda r: (conf_excess_mass(r) or 0.0, r.variant))
                read(imaging.to_pnm_bytes(frame_images[top.variant]),
                     top.variant + "+psm3", only_psm=SECONDARY_PSM)
    return reads


def best(reads):
    """The strongest reading (records.best_read); kept as S2's public name."""
    return best_read(reads)


def best_lines(reads):
    chosen = best(reads)
    return chosen.lines if chosen else []
