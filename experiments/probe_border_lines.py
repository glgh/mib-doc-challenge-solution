#!/usr/bin/env python3
"""Independent border-line tracker — candidate successor to `_band_offsets`.

The shipped reader demands the full border pair per row (left+right dark span of
border width). `probe_border_cover.py` showed that starves it: on the worst pool
pages <15% of rows pass, mostly because the right line fades out or crop-edge
specks stretch the span — the border itself is often straight and readable from
one line. This probe tracks each border line independently:

- candidate rows are dark-run starts (left line) / ends (right line);
- a candidate only counts with VERTICAL SUPPORT: >=MIN_SUPPORT of the +-SUPPORT_H
  neighbor rows must show a start/end within +-2 columns. Isolated specks and
  1-3 row horizontal form rules have no support; a faded line with gaps does.
- a candidate's column must also carry >=MIN_GLOBAL rows page-wide, so a tall
  glyph stem inside one text line cannot impersonate a border line;
- plausibility: left line in the left 35% of the page, right line in the right 35%.

Offset per row: left line where readable, else right line minus border width.
When both lines are readable they must agree with the border width within
+-WIDTH_TOL or the row stays unmeasured (the pair test survives as a cross-check
only). Same hold-last-shift walk as shipped downstream.

Scoring: (1) the geometry hard set — shred positives must fire, negatives must
no-op, with OCR evidence per cell; (2) the under-deshred pool — coverage and
edge-trace raggedness vs the shipped reader (geometry only, no OCR); (3) PNG
strips for the biggest coverage gains (--viz N).

Usage:
  experiments/probe_border_lines.py [--procs 8] [--viz 8] [--skip-pool]
"""
import argparse
import json
import multiprocessing
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

import pipeline                        # noqa: E402
import probe_border_cover as pc        # noqa: E402
import repair_bench as rb              # noqa: E402
from mib import imaging                # noqa: E402

SUPPORT_H = 9                          # +- rows in the vertical-support window
MIN_SUPPORT = 6                        # rows of the window that must agree
MIN_GLOBAL = 12                        # rows page-wide a line column must carry
WIDTH_TOL = 6                          # same as the shipped reader
EDGE_FRAC = 0.35                       # each line must live in its own page third
OUT = ROOT / "output/viz/border_lines.jsonl"


def _supported(mask, H, W):
    """Per-cell vertical support: rows within +-SUPPORT_H showing a candidate
    within +-2 columns. Boxcar via cumsum; no per-row Python loops."""
    acc = np.zeros((H, W), np.float32)
    for dx in (-2, -1, 0, 1, 2):
        acc += np.roll(mask, dx, axis=1).astype(np.float32)
    acc = np.minimum(acc, 1.0)
    ii = np.vstack([np.zeros((1, W), np.float32), np.cumsum(acc, axis=0)])
    y0 = np.clip(np.arange(H) - SUPPORT_H, 0, H)
    y1 = np.clip(np.arange(H) + SUPPORT_H + 1, 0, H)
    return ii[y1] - ii[y0], acc


def line_offsets(gray):
    """Per-row offsets from independently tracked border lines, or None when the
    page shows no trackable border (mirrors the shipped gates)."""
    dark = imaging.ink_mask(gray, thresh=150)
    H, W = dark.shape
    starts = dark & ~np.roll(dark, 1, axis=1)
    starts[:, 0] = dark[:, 0]
    ends = dark & ~np.roll(dark, -1, axis=1)
    ends[:, -1] = dark[:, -1]

    sup_l, acc_l = _supported(starts, H, W)
    sup_r, acc_r = _supported(ends, H, W)
    glob_l = acc_l.sum(axis=0)          # rows page-wide with a start near column x
    glob_r = acc_r.sum(axis=0)

    ok_l = starts & (sup_l >= MIN_SUPPORT) & (glob_l >= MIN_GLOBAL)[None, :]
    ok_r = ends & (sup_r >= MIN_SUPPORT) & (glob_r >= MIN_GLOBAL)[None, :]
    ok_l[:, int(W * EDGE_FRAC):] = False
    ok_r[:, :int(W * (1 - EDGE_FRAC))] = False

    left = np.where(ok_l.any(axis=1), ok_l.argmax(axis=1), np.nan)
    right_rev = ok_r[:, ::-1]
    right = np.where(right_rev.any(axis=1), W - 1 - right_rev.argmax(axis=1), np.nan)

    both = ~np.isnan(left) & ~np.isnan(right)
    if both.sum() < 20:
        return None
    bw = float(np.median((right - left)[both]))
    if bw < W / 2:
        return None

    offsets = np.full(H, np.nan)
    pair_ok = both & (np.abs((right - left) - bw) <= WIDTH_TOL)
    offsets[pair_ok] = left[pair_ok]
    only_l = ~np.isnan(left) & np.isnan(right)
    offsets[only_l] = left[only_l]
    only_r = np.isnan(left) & ~np.isnan(right)
    offsets[only_r] = right[only_r] - bw
    if np.count_nonzero(~np.isnan(offsets)) < 20:
        return None
    return offsets


def realign_lines(gray):
    """Tracker offsets through the shipped hold-last-shift walk."""
    offsets = line_offsets(gray)
    if offsets is None:
        return None
    return pc.realign_from(gray, offsets)


MIN_MOVE = 4                           # px; a band shifted less needs no repair
CONTENT_TOL = 18                       # px; border/content shift agreement (probe_shred)
RULE_RUN = 25                          # rows; darker vertical runs are structure, not text
MIN_BAND_INK = 200                     # px of text ink a band needs to be verifiable


def _text_ink(gray):
    """Ink mask with long vertical runs removed (form rules, border lines), so a
    content profile reflects glyphs. Verifying against raw ink is circular: the
    rules that fool the border reader dominate the correlation too (037's
    constant-bias phantom). Adaptive mask, not the fixed INK cut: faint pages
    (125) have no sub-128 glyphs and would verify against nothing."""
    dark = imaging.ink_mask(gray)
    H, W = dark.shape
    fwd = np.zeros((H, W), np.int32)
    run = np.zeros(W, np.int32)
    for y in range(H):
        run = (run + 1) * dark[y]
        fwd[y] = run
    total = fwd.copy()
    for y in range(H - 2, -1, -1):
        cont = dark[y] & dark[y + 1]
        total[y] = np.where(cont, np.maximum(total[y], total[y + 1]), total[y])
    return dark & (total < RULE_RUN)


DEGEN = 150                            # |best_shift| beyond this = pinned at the span edge


SNAP_TOL = 10                          # px; tracker fill within this of the local pair level
                                       # is jitter, not structure — snap it (seams are >15px)


def merged_offsets(gray):
    """Pair-reader offsets where the width test passes (precision), line-tracker
    offsets filling the rows it cannot measure (recall).

    A fill only contributes NEW STRUCTURE: a tracker value within SNAP_TOL of
    the nearest pair-measured level is that level (the tracker's one-line
    reading carries a few px of bias — 045's fills sat 7px off inside a flat
    band, corrupting its median and the walk). Only a plateau genuinely apart
    from the local pair level (clipped bands, faded lines) keeps its own value."""
    pair = imaging._band_offsets(gray)
    lines = line_offsets(gray)
    if pair is None and lines is None:
        return None
    if pair is None:
        return lines
    if lines is None:
        return pair
    merged = pair.copy()
    meas = np.flatnonzero(~np.isnan(pair))
    prev_i = -1
    for y in np.flatnonzero(np.isnan(pair) & ~np.isnan(lines)):
        while prev_i + 1 < len(meas) and meas[prev_i + 1] < y:
            prev_i += 1
        cands = []
        if prev_i >= 0:
            cands.append(meas[prev_i])
        if prev_i + 1 < len(meas):
            cands.append(meas[prev_i + 1])
        nearest = pair[min(cands, key=lambda i: abs(i - y))]
        merged[y] = nearest if abs(lines[y] - nearest) <= SNAP_TOL else lines[y]
    return merged


VOTE_CORR = 0.15                       # below this the correlation is pure noise: no vote


def _best_shift_corr(profile, ref, span=160):
    """probe_shred._best_shift plus the peak correlation, so callers can tell a
    confident measurement from noise."""
    best_s, best_c = 0, -1.0
    ref = ref - ref.mean()
    for s in range(-span, span + 1, 2):
        p = np.roll(profile, s).astype(float)
        p -= p.mean()
        denom = (np.linalg.norm(p) * np.linalg.norm(ref)) or 1.0
        c = float(p @ ref) / denom
        if c > best_c:
            best_c, best_s = c, s
    return best_s, best_c


def realign_verified(gray):
    """Merged offsets, applied on the TEXT's vote, band by band or page-wide.

    Forward-filled offsets segment into bands at >15px seams (probe_shred).
    Every moving band with enough text ink and a non-junk correlation (peak not
    pinned at the span edge, corr >= VOTE_CORR) votes: its text column-profile
    either agrees with the border's implied shift within CONTENT_TOL or it
    disagrees. The peak VALUE is deliberately not the confidence — on sparse
    text real alignments peak low (762: five bands matching the border within
    2px at corr 0.19-0.27); matching a +-160px search within tolerance is the
    evidence. Verdicts: agreements outnumber disagreements -> the shred is
    confirmed, apply the full border walk (762); mixed -> move only the
    agreeing bands (037: the one genuinely sheared line moves, the phantom
    bands stay, the applicant name survives); only disagreements -> veto,
    no-op; nobody can vote (faint scans: 125) -> fall back to the full walk,
    shipped behaviour, with the ev guard still downstream."""
    import probe_shred as ps
    offsets = merged_offsets(gray)
    if offsets is None:
        return None
    profile = offsets.copy()
    last = np.nanmedian(offsets)
    for y in range(len(profile)):
        if not np.isnan(profile[y]):
            last = profile[y]
        profile[y] = last
    bands = ps._bands(profile)
    if not bands:
        return None
    ink = _text_ink(gray)
    # the reference must itself carry text: correlating against a near-empty
    # profile returns garbage for every band (045's pair profile picked a
    # text-free largest band and no vote was ever real)
    texted = [(a, b) for a, b in bands if ink[a:b].sum() >= MIN_BAND_INK]
    ref_band = max(texted, key=lambda ab: ab[1] - ab[0]) if texted else None
    agree, disagree = [], 0
    moving = False
    if ref_band is not None:
        ref_profile = ink[ref_band[0]:ref_band[1]].sum(axis=0)
        ref_off = float(np.nanmedian(profile[ref_band[0]:ref_band[1]]))
    else:
        ref_off = float(np.nanmedian(profile))
    for a, b in bands:
        border_shift = ref_off - float(np.nanmedian(profile[a:b]))
        if abs(border_shift) < MIN_MOVE:
            continue
        moving = True
        if ref_band is None:
            continue                               # no text anywhere: nobody can vote
        band_profile = ink[a:b].sum(axis=0)
        if band_profile.sum() < MIN_BAND_INK:
            continue
        content_shift, corr = _best_shift_corr(band_profile, ref_profile)
        if abs(content_shift) >= DEGEN or corr < VOTE_CORR:
            continue
        if abs(content_shift - border_shift) <= CONTENT_TOL:
            agree.append((a, b))
        else:
            disagree += 1
    if not moving:
        return None
    if not agree and not disagree:
        return pc.realign_from(gray, offsets)      # unverifiable: shipped behaviour
    if not agree:
        return None                                # contradicted everywhere: veto
    if len(agree) >= disagree:
        return pc.realign_from(gray, offsets)      # confirmed (ties: the border's own
                                                   # physical reading outranks a sparse-
                                                   # text draw; the ev guard backstops)
    out = gray.copy()                              # mixed: only the agreeing bands move
    moved = False
    for a, b in agree:
        for y in range(a, b):
            s = int(round(ref_off - profile[y]))
            if s:
                out[y] = np.roll(gray[y], s)
                moved = True
    return out if moved else None


def _base(case, page):
    src = rb.source_gray(case, page)
    _, ori = pipeline.orient(src)
    oriented = ori if ori is not None else src
    _, dsk = pipeline.deskew(oriented)
    return dsk if dsk is not None else oriented


def score_hard():
    """Geometry hard set: shred labels vs tracker fire/no-op, ev per cell."""
    print("--- hard set (geometry track) ---")
    agree = total = 0
    for case, page, expect in rb.load_hard():
        base = _base(case, page)
        shipped = imaging.realign_bands(base)
        tracked = realign_verified(base)
        ev_base, _ = rb._read(base)
        ev_ship = rb._read(shipped)[0] if shipped is not None else None
        ev_trk = rb._read(tracked)[0] if tracked is not None else None
        verdict = ""
        if "shred" in expect:
            want = expect["shred"]
            got = tracked is not None
            total += 1
            # a no-op walk (all offsets equal) returns None: counts as no-fire
            if got == want:
                agree += 1
                verdict = "OK"
            else:
                verdict = f"MISS (want shred={want})"
        print(f"{case} p{page}: ev base={ev_base} shipped={ev_ship} "
              f"tracker={ev_trk} fired={tracked is not None} {verdict}")
    if total:
        print(f"shred-label agreement: {agree}/{total}")


def pool_page(pair):
    stem, page = pair
    try:
        base = _base(stem, page)
        c = pc.classify(base)
        offsets = line_offsets(base)
        row = {"stem": stem, "page": page}
        dark_rows = sum(1 for l, _ in pc._spans(base) if l >= 0)
        row["cover_pair"] = round(c["n_trust"] / max(c["n_dark"], 1), 3) if c else None
        if offsets is None:
            row.update(tracker=False)
            return row
        measured = int(np.count_nonzero(~np.isnan(offsets)))
        ext = pc._extent(offsets)
        padded = pc.realign_padded(base, offsets)
        row.update(
            tracker=True,
            cover_lines=round(measured / max(dark_rows, 1), 3),
            edge_lines=pc.edge_stats(padded if padded is not None else base, ext),
            differs=bool(padded is not None and
                         not np.array_equal(padded, pc.realign_padded(
                             base, c["trusted"]) if c else base)))
        return row
    except Exception as exc:           # noqa: BLE001
        return {"stem": stem, "page": page, "error": f"{type(exc).__name__}: {exc}"}


def viz(results, n, out_dir):
    """PNG strips for the biggest coverage gains: base vs shipped vs tracker,
    OCR'd. Quality judgment stays with the user."""
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 22)
    gain = [r for r in results if r.get("tracker") and r.get("cover_pair") is not None]
    gain.sort(key=lambda r: -(r["cover_lines"] - r["cover_pair"]))
    paths = []
    for r in gain[:n]:
        base = _base(r["stem"], r["page"])
        shipped = imaging.realign_bands(base)
        tracked = realign_verified(base)
        cells = []
        for g, name in ((base, "deskewed base"),
                        (shipped if shipped is not None else base,
                         "shipped deshred" if shipped is not None else "shipped no-op"),
                        (tracked if tracked is not None else base,
                         "line-tracker deshred" if tracked is not None else "tracker no-op")):
            ev, _ = rb._read(g)
            cells.append((g, f"{name} · ev {ev}"))
        cw = 640
        ims = []
        for g, _ in cells:
            im = Image.fromarray(g).convert("L").convert("RGB")
            ims.append(im.resize((cw, max(1, round(im.height * cw / im.width))),
                                 Image.LANCZOS))
        ch = max(im.height for im in ims)
        cap_h, pad, head_h = 34, 10, 44
        board = Image.new("RGB", (3 * (cw + pad) + pad, head_h + ch + cap_h + pad),
                          (15, 20, 22))
        d = ImageDraw.Draw(board)
        d.text((pad, 10),
               f"{r['stem']} p{r['page']} · pair cover {r['cover_pair']} -> "
               f"lines {r['cover_lines']}", font=font, fill=(221, 227, 226))
        for i, (im, (g, lab)) in enumerate(zip(ims, cells)):
            x = pad + i * (cw + pad)
            board.paste(im, (x, head_h))
            d.text((x, head_h + ch + 8), lab, font=font, fill=(163, 179, 180))
        p = out_dir / f"border_lines_{r['stem']}_p{r['page']}.png"
        board.save(p)
        paths.append(p)
        print(p, file=sys.stderr)
    return paths


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--procs", type=int, default=8)
    ap.add_argument("--viz", type=int, default=0)
    ap.add_argument("--viz-dir", default=str(ROOT / "output/viz"))
    ap.add_argument("--skip-pool", action="store_true")
    args = ap.parse_args()

    score_hard()
    if args.skip_pool:
        return
    pages = pc.pool_pages()
    print(f"--- pool ({len(pages)} pages, {args.procs} procs) ---")
    with multiprocessing.Pool(args.procs) as mp:
        results = mp.map(pool_page, pages)
    OUT.write_text("\n".join(json.dumps(r) for r in results) + "\n")

    ok = [r for r in results if "error" not in r]
    tracked = [r for r in ok if r.get("tracker")]
    paired = [r for r in tracked if r.get("cover_pair") is not None]
    if paired:
        dc = sorted(r["cover_lines"] - r["cover_pair"] for r in paired)
        cl = sorted(r["cover_lines"] for r in paired)
        print(f"tracker fires on {len(tracked)}/{len(ok)} · coverage "
              f"pair->lines median {np.median([r['cover_pair'] for r in paired]):.2f}"
              f"->{np.median(cl):.2f} · delta p10/p50/p90 "
              f"{dc[int(len(dc) * .1)]:+.2f}/{dc[len(dc) // 2]:+.2f}/"
              f"{dc[int(len(dc) * .9)]:+.2f}")
        es = [r["edge_lines"] for r in tracked if r.get("edge_lines")]
        ragged = sum(1 for e in es if e["ragged_frac"] > 0.05)
        print(f"edge-trace after tracker realign: ragged_frac>0.05 on "
              f"{ragged}/{len(es)} pages (shipped reader: 240)")
        print(f"tracker output differs from shipped on "
              f"{sum(1 for r in tracked if r.get('differs'))} pages")
    if args.viz:
        viz(ok, args.viz, Path(args.viz_dir))


if __name__ == "__main__":
    main()
