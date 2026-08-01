"""Shared helpers for the probe suite (experiments/probes/).

Offline only: locates cells in the real scans, provides a battery of binarization
methods, and OCRs via tesseract. No mib/ mutation."""
import io, os, re, subprocess, sys, tempfile
import numpy as np
import scipy.ndimage as ndi
import fitz
from PIL import Image

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
from mib import grammar  # noqa: E402

TRAIN = os.path.join(ROOT, "..", "mib-doc-challenge", "data", "train", "{}.pdf")
LABELS = os.path.join(ROOT, "..", "mib-doc-challenge", "data", "train_labels.csv")


def load_cases():
    import csv
    truth = {r["case_id"]: r for r in csv.DictReader(open(LABELS))}
    cases = []
    for line in open(os.path.join(os.path.dirname(__file__), "cases.txt")):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        kind, cid = line.split()
        cases.append((kind, cid, truth[cid]))
    return cases


# ---- cell location --------------------------------------------------------
def _tsv(img):
    with tempfile.NamedTemporaryFile(suffix=".png") as f:
        img.save(f.name)
        out = subprocess.run(["tesseract", f.name, "stdout", "--psm", "6", "tsv"],
                             capture_output=True, text=True).stdout
    return [r.split("\t") for r in out.splitlines()[1:] if r.strip()]


def line_crop(cid, prefix_re):
    """Native-res grayscale crop of the first line matching prefix_re, or None."""
    doc = fitz.open(TRAIN.format(cid))
    try:
        for pg in doc:
            for xref, *_ in pg.get_images(full=True):
                pix = fitz.Pixmap(doc, xref)
                if pix.n > 4:
                    pix = fitz.Pixmap(fitz.csRGB, pix)
                img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("L")
                lines = {}
                for r in _tsv(img):
                    if len(r) >= 12:
                        lines.setdefault((r[2], r[3], r[4]), []).append(r)
                for ws in lines.values():
                    if re.search(prefix_re, " ".join(w[11] for w in ws)):
                        xs = [int(w[6]) for w in ws]; ys = [int(w[7]) for w in ws]
                        xe = [int(w[6]) + int(w[8]) for w in ws]
                        ye = [int(w[7]) + int(w[9]) for w in ws]
                        return img.crop((max(0, min(xs) - 6), max(0, min(ys) - 6),
                                         min(img.width, max(xe) + 6),
                                         min(img.height, max(ye) + 6)))
    finally:
        doc.close()
    return None


def sponsor_crop(cid, zoom=4):
    c = line_crop(cid, r"SP[NHM]|BP[NH]")
    if c is None:
        return None
    return np.asarray(c.resize((c.width * zoom, c.height * zoom), Image.LANCZOS))


# ---- binarizations (grayscale uint8 -> bool ink mask, True = ink) ----------
def _otsu_t(g):
    h = np.histogram(g, 256, (0, 256))[0].astype(float); tot = g.size
    sm = (np.arange(256) * h).sum(); sb = wb = mx = t = 0.0
    for i in range(256):
        wb += h[i]; wf = tot - wb
        if wb == 0 or wf == 0:
            continue
        sb += i * h[i]; mb = sb / wb; mf = (sm - sb) / wf
        v = wb * wf * (mb - mf) ** 2
        if v > mx:
            mx, t = v, i
    return t

def otsu(g):            return g < _otsu_t(g)
def raw127(g):          return g < 127

def _local(g, w):
    g = g.astype(np.float64)
    m = ndi.uniform_filter(g, w)
    s = np.sqrt(np.maximum(ndi.uniform_filter(g * g, w) - m * m, 0))
    return g, m, s

def sauvola(g, w=41, k=0.2, R=128.0):
    gf, m, s = _local(g, w)
    return gf < m * (1 + k * (s / R - 1))

def niblack(g, w=41, k=-0.2):
    gf, m, s = _local(g, w)
    return gf < m + k * s

def wolf(g, w=41, k=0.5):
    gf, m, s = _local(g, w)
    R = s.max() or 1.0; M = float(gf.min())
    return gf < m - k * (1 - s / R) * (m - M)

def adaptive_gauss(g, sigma=15, C=8):
    gf = g.astype(np.float64)
    return gf < ndi.gaussian_filter(gf, sigma) - C

def illum_otsu(g, sigma=25):
    gf = g.astype(np.float64)
    norm = np.clip(gf - ndi.gaussian_filter(gf, sigma) + 128, 0, 255).astype(np.uint8)
    return norm < _otsu_t(norm)

def opened(ink, it=1):   return ndi.binary_opening(ink, iterations=it)
def eroded(ink, it=1):   return ndi.binary_erosion(ink, iterations=it)


# ---- OCR ------------------------------------------------------------------
def _tess(ink, psm, whitelist=None):
    im = Image.fromarray((~ink * 255).astype(np.uint8))
    with tempfile.NamedTemporaryFile(suffix=".png") as f:
        im.save(f.name)
        cfg = ["tesseract", f.name, "stdout", "--psm", str(psm)]
        if whitelist:
            cfg += ["-c", f"tessedit_char_whitelist={whitelist}"]
        return subprocess.run(cfg, capture_output=True, text=True).stdout

def sponsor_cell(ink, psm=7):
    """Extract a 4-digit sponsor cell from a binarized line, or ''."""
    v = grammar.coerce_sponsor_id(_tess(ink, psm))
    if v:
        return v.replace("SPN-", "")
    return "".join(re.findall(r"\d", _tess(ink, psm, "0123456789")))[:4]
