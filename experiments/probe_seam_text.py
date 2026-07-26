#!/usr/bin/env python3
"""Text-centric deshred quick tests (plan: let-s-pause-here-and-luminous-comet).

The reframe under test: only seams that cut THROUGH text matter for OCR;
whitespace seams are cosmetic; engine confidence (the shipped selection metric,
records.conf_excess_mass) is the sensor the border work never used.

Subcommands (each prints a summary; census/pairs also write jsonl to output/viz):

  qta   Deshred arbitration replay census over output/cache/hard_conf4.jsonl:
        same-source pre/deshred read pairs, sign(dEv) x sign(dConf) matrix,
        top disagreements dumped for user eyeballing. Zero OCR.
  qtb   Seam-through-text census over the under-deshred pool + all cache scan
        pages + the geometry anchors: every seam classified cuts-text /
        one-sided / whitespace by glyph-ink mass on both sides; page classes
        add NO-BORDER and LOW-COVERAGE-UNKNOWN. Zero OCR, parallel.
  qtc   Whitespace no-op validation: joins qta pairs x qtb page classes —
        on pages whose seams are all whitespace, dConf and dEv should be ~0.
  qtd   Targeted conf OCR on the wall anchors 037 p0 / 045 p2: base vs shipped
        realign vs tracker realign, conf_excess_mass + the 'Arivara' name
        check. Holdout cases: diagnosis only, nothing is tuned here.
  qte   Seam-local conf replay: does the deshred variant's conf gain
        concentrate in windows around cuts-text seams? Zero OCR.

Usage: experiments/probe_seam_text.py {qta|qtb|qtc|qtd|qte} [--procs 8]
"""
import argparse
import json
import multiprocessing
import sys
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

import probe_border_cover as pc        # noqa: E402
import probe_border_lines as pl        # noqa: E402
import probe_shred as ps               # noqa: E402
import repair_bench as rb              # noqa: E402
from mib import imaging                # noqa: E402
from mib.records import CONF_BASELINE, FOOTER_Y  # noqa: E402

CACHE = ROOT / "output/cache/hard_conf4.jsonl"
CENSUS_OUT = ROOT / "output/viz/seam_text.jsonl"
PAIRS_OUT = ROOT / "output/viz/qta_pairs.jsonl"

SEAM_WIN = 3                           # rows each side of a seam that count as "at" it
RULE_ROW_FRAC = 0.4                    # a row with text-ink over this fraction of the
                                       # width is a horizontal rule, not text (037 trap)
TEXT_MASS_MIN = 40                     # px of glyph ink on a side to call it text
LOW_COVER = 0.3                        # merged coverage below this: seams unknowable


def mass(conf):
    """conf_excess_mass over raw cached conf tuples (3- or 4-tuples)."""
    if conf is None:
        return None
    return sum(max(0.0, e[0] - CONF_BASELINE) * e[1] for e in conf if e[2] < FOOTER_Y)


def cache_records():
    with open(CACHE) as f:
        for i, line in enumerate(f):
            rec = json.loads(line)
            if i == 0 and "_meta" in rec:
                continue
            yield rec


def same_source_pairs(reads):
    """(source, pre_read, deshred_read) — pre is the +skew rung when present
    (the deshred rung composes deskew first), else the raw source read."""
    by = {r["variant"]: r for r in reads}
    for src in ("embedded", "render"):
        desh = by.get(f"{src}+deshred")
        if desh is None:
            continue
        pre = by.get(f"{src}+skew", by.get(src))
        if pre is not None:
            yield src, pre, desh


# --- qta ---------------------------------------------------------------------

def qta():
    cells = Counter()
    pairs = []
    for rec in cache_records():
        for pg in rec.get("pages") or []:
            for src, pre, desh in same_source_pairs(pg.get("reads") or []):
                m0, m1 = mass(pre.get("conf")), mass(desh.get("conf"))
                if m0 is None or m1 is None:
                    continue
                dev = desh["quality"] - pre["quality"]
                dconf = m1 - m0
                sgn = lambda x, eps: "0" if abs(x) <= eps else ("+" if x > 0 else "-")
                cells[(sgn(dev, 0), sgn(dconf, 1.0))] += 1
                pairs.append({"stem": rec["stem"], "page": pg["page_no"], "src": src,
                              "pre": pre["variant"], "dev": dev,
                              "dconf": round(dconf, 1),
                              "pre_lines": pre["lines"], "desh_lines": desh["lines"]})
    PAIRS_OUT.write_text("\n".join(json.dumps(p) for p in pairs) + "\n")
    n = sum(cells.values())
    print(f"qta: {n} same-source pre/deshred pairs "
          f"({len({(p['stem'], p['page']) for p in pairs})} pages)")
    print("      dConf: -      0      +")
    for de in ("-", "0", "+"):
        row = [cells.get((de, dc), 0) for dc in ("-", "0", "+")]
        print(f"  dEv {de}: {row[0]:5d}  {row[1]:5d}  {row[2]:5d}")
    ties = sum(cells.get(("0", dc), 0) for dc in ("-", "+"))
    print(f"ev-tie but conf-decides: {ties} pairs "
          f"({100 * ties / max(n, 1):.0f}%) — the guard-granularity win")
    dis = [p for p in pairs if p["dev"] * p["dconf"] < 0 and abs(p["dev"]) > 0]
    dis.sort(key=lambda p: -abs(p["dconf"]))
    print(f"sign disagreements (ev vs conf): {len(dis)}; top 10 for eyeballing:")
    for p in dis[:10]:
        print(f"  {p['stem']} p{p['page']} {p['src']}: dEv={p['dev']:+d} "
              f"dConf={p['dconf']:+.0f}")
        print(f"    pre : {' | '.join(p['pre_lines'][:3])[:110]}")
        print(f"    desh: {' | '.join(p['desh_lines'][:3])[:110]}")


# --- qtb ---------------------------------------------------------------------

def text_row_profile(gray):
    """Per-row glyph-ink pixel count, with full-width horizontal-rule rows
    zeroed (037's rules survive _text_ink's vertical-run stripping)."""
    ink = pl._text_ink(gray)
    prof = ink.sum(axis=1).astype(float)
    prof[prof > RULE_ROW_FRAC * gray.shape[1]] = 0.0
    return prof


def line_pitch(prof):
    """Median row distance between text-line block centers, or None."""
    rows = prof > 0
    centers, start = [], None
    for y, on in enumerate(rows):
        if on and start is None:
            start = y
        elif not on and start is not None:
            centers.append((start + y) / 2)
            start = None
    if start is not None:
        centers.append((start + len(rows)) / 2)
    if len(centers) < 3:
        return None
    return float(np.median(np.diff(centers)))


def census_page(job):
    stem, page, tags = job
    try:
        base = pl._base(stem, page)
        offsets = pl.merged_offsets(base)
        row = {"stem": stem, "page": page, "tags": tags}
        if offsets is None:
            row["cls"] = "NO-BORDER"
            return row
        dark_rows = sum(1 for l, _ in pc._spans(base) if l >= 0)
        cover = float(np.count_nonzero(~np.isnan(offsets))) / max(dark_rows, 1)
        row["cover"] = round(cover, 3)
        profile = offsets.copy()
        last = np.nanmedian(offsets)
        for y in range(len(profile)):
            if not np.isnan(profile[y]):
                last = profile[y]
            profile[y] = last
        seams = [int(s) for s in ps._steps(profile)]
        prof = text_row_profile(base)
        pitch = line_pitch(prof)
        out = []
        for s in seams:
            above = float(prof[max(0, s - SEAM_WIN + 1):s + 1].sum())
            below = float(prof[s + 1:s + 1 + SEAM_WIN].sum())
            a, b = above >= TEXT_MASS_MIN, below >= TEXT_MASS_MIN
            cls = "cuts-text" if (a and b) else ("one-sided" if (a or b) else "whitespace")
            out.append({"y": s, "cls": cls, "above": round(above), "below": round(below)})
        row.update(seams=out, pitch=None if pitch is None else round(pitch, 1),
                   height=int(base.shape[0]))
        if cover < LOW_COVER:
            row["cls"] = "LOW-COVERAGE-UNKNOWN"
        elif not seams:
            row["cls"] = "no-seams"
        elif any(s["cls"] == "cuts-text" for s in out):
            row["cls"] = "cuts-text"
        elif any(s["cls"] == "one-sided" for s in out):
            row["cls"] = "one-sided"
        else:
            row["cls"] = "whitespace-only"
        return row
    except Exception as exc:           # noqa: BLE001
        return {"stem": stem, "page": page, "tags": tags,
                "error": f"{type(exc).__name__}: {exc}"}


def qtb(procs):
    jobs = {}
    for stem, page in pc.pool_pages():
        jobs[(stem, page)] = {"pool"}
    for rec in cache_records():
        for pg in rec.get("pages") or []:
            if pg.get("is_scan_only") and pg.get("reads"):
                jobs.setdefault((rec["stem"], pg["page_no"]), set()).add("cache")
    for case, page, _ in rb.load_hard():
        jobs.setdefault((case, page), set()).add("anchor")
    joblist = [(s, p, sorted(t)) for (s, p), t in sorted(jobs.items())]
    print(f"qtb: {len(joblist)} pages "
          f"(pool {sum('pool' in t for _, _, t in joblist)}, "
          f"cache {sum('cache' in t for _, _, t in joblist)}, "
          f"anchor {sum('anchor' in t for _, _, t in joblist)}) on {procs} procs",
          file=sys.stderr)
    with multiprocessing.Pool(procs) as mp:
        rows = mp.map(census_page, joblist)
    CENSUS_OUT.write_text("\n".join(json.dumps(r) for r in rows) + "\n")

    ok = [r for r in rows if "error" not in r]
    print(f"qtb: {len(ok)} pages classified ({len(rows) - len(ok)} errors)")
    for scope in ("pool", "cache", "anchor"):
        sub = [r for r in ok if scope in r["tags"]]
        print(f"  [{scope}] {len(sub)} pages: "
              + " ".join(f"{c}={n}" for c, n in
                         Counter(r["cls"] for r in sub).most_common()))
    # the anchor table — the discriminator correlation couldn't provide
    print("anchor table (geometry registry):")
    expect = {(c, p): e for c, p, e in rb.load_hard()}
    for r in ok:
        if "anchor" not in r["tags"]:
            continue
        e = expect.get((r["stem"], r["page"]), {})
        seams = r.get("seams") or []
        print(f"  {r['stem']} p{r['page']}: cls={r['cls']} cover={r.get('cover')} "
              f"seams={len(seams)} cuts={sum(s['cls'] == 'cuts-text' for s in seams)} "
              f"expect={e}")
    # third-candidate OCR cost: tracker-differs pages that actually cut text
    bl = ROOT / "output/viz/border_lines.jsonl"
    if bl.exists():
        differs = {(r["stem"], r["page"]) for r in map(json.loads, open(bl))
                   if r.get("differs")}
        pool_rows = [r for r in ok if "pool" in r["tags"]]
        hot = [r for r in pool_rows if (r["stem"], r["page"]) in differs
               and r["cls"] == "cuts-text"]
        print(f"third-candidate cost join: tracker differs on {len(differs)} pool "
              f"pages; of those, cuts-text {len(hot)} — only these can pay for an "
              f"extra OCR pass")


# --- qtc ---------------------------------------------------------------------

def qtc():
    census = {(r["stem"], r["page"]): r for r in map(json.loads, open(CENSUS_OUT))
              if "error" not in r}
    groups = {"whitespace-only": [], "cuts-text": [], "one-sided": []}
    for p in map(json.loads, open(PAIRS_OUT)):
        c = census.get((p["stem"], p["page"]))
        if c and c.get("cls") in groups:
            groups[c["cls"]].append(p)
    print("qtc: |dConf| and |dEv| of the deshred rung by page seam class")
    for cls, ps_ in groups.items():
        if not ps_:
            print(f"  {cls}: no joined pairs")
            continue
        dc = [abs(p["dconf"]) for p in ps_]
        de = [abs(p["dev"]) for p in ps_]
        moved = sum(1 for p in ps_ if abs(p["dconf"]) > 10)
        print(f"  {cls}: {len(ps_)} pairs · median|dConf| {np.median(dc):.0f} "
              f"p90 {np.percentile(dc, 90):.0f} · median|dEv| {np.median(de):.0f} · "
              f"|dConf|>10 on {moved} ({100 * moved / len(ps_):.0f}%)")
    ws = sorted(groups["whitespace-only"], key=lambda p: -abs(p["dconf"]))[:5]
    if ws:
        print("  top whitespace-only |dConf| offenders (premise violations if large):")
        for p in ws:
            print(f"    {p['stem']} p{p['page']} {p['src']} dConf={p['dconf']:+.0f} "
                  f"dEv={p['dev']:+d}")


# --- qtd ---------------------------------------------------------------------

def qtd():
    import tempfile
    from mib.stages import render
    print("qtd: wall anchors under the shipped conf selector (holdout: diagnosis only)")
    for case, page in (("MIB-000037", 0), ("MIB-000045", 2)):
        base = pl._base(case, page)
        cands = [("base", base)]
        shipped = imaging.realign_bands(base)
        if shipped is not None:
            cands.append(("shipped", shipped))
        tracked = pl.realign_verified(base)
        if tracked is not None:
            cands.append(("tracker", tracked))
        print(f"  {case} p{page}:")
        for name, g in cands:
            with tempfile.TemporaryDirectory(prefix="qtd") as tmp:
                p = Path(tmp) / "x.png"
                p.write_bytes(imaging.to_png_bytes(g))
                lines, conf = render._recognize(p)
            m = mass(conf)
            ev = render.evidence_score(lines)
            txt = " ".join(lines).lower()
            print(f"    {name:8s} ev={ev:2d} conf_mass={m:7.0f} "
                  f"arivara={'arivara' in txt} zavoss={'zavoss' in txt}")


# --- qte ---------------------------------------------------------------------

def qte():
    census = {(r["stem"], r["page"]): r for r in map(json.loads, open(CENSUS_OUT))
              if "error" not in r and r.get("seams")}
    stats = {"cuts-text": [], "one-sided": [], "whitespace": []}
    joined = 0
    for rec in cache_records():
        for pg in rec.get("pages") or []:
            c = census.get((rec["stem"], pg["page_no"]))
            if c is None:
                continue
            H = c["height"]
            pitch = c.get("pitch") or 20.0
            win = max(pitch, 2 * SEAM_WIN) / H          # fraction-space window
            for src, pre, desh in same_source_pairs(pg.get("reads") or []):
                if pre.get("conf") is None or desh.get("conf") is None:
                    continue
                joined += 1
                for s in c["seams"]:
                    frac = s["y"] / H
                    lo, hi = frac - 2 * win, frac + win   # widened upward (y=top edge)
                    local = lambda conf: sum(
                        max(0.0, e[0] - CONF_BASELINE) * e[1] for e in conf
                        if lo <= e[2] <= hi and e[2] < FOOTER_Y)
                    stats[s["cls"]].append(local(desh["conf"]) - local(pre["conf"]))
    print(f"qte: {joined} read pairs joined to census seams")
    for cls, ds in stats.items():
        if not ds:
            print(f"  {cls}: no seams joined")
            continue
        ds = np.array(ds)
        print(f"  {cls}: {len(ds)} seams · mean d(local conf mass) {ds.mean():+.0f} · "
              f"median {np.median(ds):+.0f} · gain>10 on {(ds > 10).mean() * 100:.0f}% "
              f"· loss<-10 on {(ds < -10).mean() * 100:.0f}%")


# --- qtf ---------------------------------------------------------------------

QTF_TOL = 6                            # px; local vs border shift agreement
QTF_SAMPLE = 50                        # pool cuts-text seams to measure (seeded)


def _masked_ink(gray):
    """Glyph ink with horizontal-rule rows zeroed (same trap guard as qtb)."""
    ink = pl._text_ink(gray).astype(float)
    rule = ink.sum(axis=1) > RULE_ROW_FRAC * gray.shape[1]
    ink[rule] = 0.0
    return ink


def seam_local_shift(gray, profile, s, pitch):
    """(local_shift, corr, border_shift, above_mass, below_mass) at seam s.

    Windows of half a line pitch each side of the cut hold the two halves of
    the same text line; the shift aligning the below half onto the above half
    is measured from their column ink profiles. border_shift is what the
    border's offset jump implies for the same alignment."""
    w = max(int(round((pitch or 20) / 2)), 4)
    ink = _masked_ink(gray)
    above = ink[max(0, s - w + 1):s + 1].sum(axis=0)
    below = ink[s + 1:s + 1 + w].sum(axis=0)
    if above.sum() < TEXT_MASS_MIN or below.sum() < TEXT_MASS_MIN:
        return None
    shift, corr = pl._best_shift_corr(below, above)
    border = float(profile[s] - profile[s + 1])
    return {"local": shift, "corr": round(corr, 2), "border": round(border, 1),
            "above_mass": int(above.sum()), "below_mass": int(below.sum())}


def qtf():
    census = [r for r in map(json.loads, open(CENSUS_OUT)) if "error" not in r
              and r.get("seams")]
    anchors = [r for r in census if "anchor" in r["tags"]]
    pool = [r for r in census if "anchor" not in r["tags"]
            and any(s["cls"] == "cuts-text" for s in r["seams"])]
    import random
    rng = random.Random(8090)
    rng.shuffle(pool)

    expect = {(c, p): e for c, p, e in rb.load_hard()}

    def run_page(r, only_cuts=True):
        base = pl._base(r["stem"], r["page"])
        offsets = pl.merged_offsets(base)
        if offsets is None:
            return []
        profile = offsets.copy()
        last = np.nanmedian(offsets)
        for y in range(len(profile)):
            if not np.isnan(profile[y]):
                last = profile[y]
            profile[y] = last
        out = []
        for s in r["seams"]:
            if only_cuts and s["cls"] != "cuts-text":
                continue
            m = seam_local_shift(base, profile, s["y"], r.get("pitch"))
            if m is not None:
                m.update(stem=r["stem"], page=r["page"], y=s["y"])
                out.append(m)
        return out

    print("qtf phase 1 — anchors (geometry registry):")
    shred_hits = shred_total = 0
    for r in sorted(anchors, key=lambda r: r["stem"]):
        e = expect.get((r["stem"], r["page"]), {})
        rows = run_page(r)
        for m in rows:
            ag = abs(m["local"] - m["border"]) <= QTF_TOL
            if e.get("shred") is True:
                shred_total += 1
                shred_hits += ag
            print(f"  {m['stem']} p{m['page']} y={m['y']}: local={m['local']:+4d} "
                  f"border={m['border']:+6.1f} corr={m['corr']:.2f} "
                  f"{'AGREE' if ag else 'differ'}  (expect {e})")
    if shred_total:
        print(f"true-shred anchor agreement: {shred_hits}/{shred_total} "
              f"({100 * shred_hits / shred_total:.0f}%)")

    print(f"qtf phase 1 — pool sample (target {QTF_SAMPLE} cuts-text seams):")
    measures = []
    for r in pool:
        if len(measures) >= QTF_SAMPLE:
            break
        measures.extend(run_page(r))
    measures = measures[:QTF_SAMPLE]
    if measures:
        corrs = np.array([m["corr"] for m in measures])
        agrees = np.array([abs(m["local"] - m["border"]) <= QTF_TOL
                           for m in measures])
        strong = corrs >= 0.4
        print(f"  {len(measures)} seams · corr median {np.median(corrs):.2f} "
              f"p10 {np.percentile(corrs, 10):.2f} p90 {np.percentile(corrs, 90):.2f} "
              f"(cross-band regime was 0.19-0.27)")
        print(f"  local-vs-border agree(<= {QTF_TOL}px): {agrees.mean() * 100:.0f}% "
              f"overall · {agrees[strong].mean() * 100:.0f}% among corr>=0.4 "
              f"({strong.sum()} seams)")
        bad = [m for m in measures if abs(m["local"] - m["border"]) > QTF_TOL]
        bad.sort(key=lambda m: -m["corr"])
        print("  strongest-corr disagreements (border may be wrong there — "
              "these are where local repair would differ):")
        for m in bad[:8]:
            print(f"    {m['stem']} p{m['page']} y={m['y']}: local={m['local']:+4d} "
                  f"border={m['border']:+6.1f} corr={m['corr']:.2f}")


QTF_MIN_CORR = 0.4                     # a local estimate below this doesn't override


def realign_local(gray, census_row):
    """Border walk with strong local-text overrides at cuts-text seams.

    The border's offsets drive the walk as usual, but wherever a cuts-text seam
    has a confident local glyph-half estimate, the band below the seam is
    adjusted so the relative shift across the seam equals what the TEXT wants —
    including local=0, "this text never moved, leave it" (165's rescue).
    Whitespace seams keep border behaviour (cosmetic by the census)."""
    offsets = pl.merged_offsets(gray)
    if offsets is None:
        return None
    profile = offsets.copy()
    last = np.nanmedian(offsets)
    for y in range(len(profile)):
        if not np.isnan(profile[y]):
            last = profile[y]
        profile[y] = last
    seams = sorted(census_row["seams"], key=lambda s: s["y"])
    all_seam_ys = [s["y"] for s in seams]
    p2 = profile.copy()
    for s in seams:
        if s["cls"] != "cuts-text":
            continue
        m = seam_local_shift(gray, profile, s["y"], census_row.get("pitch"))
        if m is None or m["corr"] < QTF_MIN_CORR or abs(m["local"]) >= 150:
            continue
        delta = m["border"] - m["local"]
        if not delta:
            continue
        nxt = next((y for y in all_seam_ys if y > s["y"]), len(profile) - 1)
        p2[s["y"] + 1:nxt + 1] += delta
    return pc.realign_from(gray, p2)


def qtf2():
    import tempfile
    from mib.stages import render

    def score(g):
        with tempfile.TemporaryDirectory(prefix="qtf2") as tmp:
            p = Path(tmp) / "x.png"
            p.write_bytes(imaging.to_png_bytes(g))
            lines, conf = render._recognize(p)
        return render.evidence_score(lines), mass(conf), " ".join(lines).lower()

    census = {(r["stem"], r["page"]): r for r in map(json.loads, open(CENSUS_OUT))
              if "error" not in r and r.get("seams")}
    anchors = [(c, p) for c, p, e in rb.load_hard() if (c, p) in census]
    disagree_pool = [("MIB-000243", 2), ("MIB-000258", 2), ("MIB-000459", 1),
                     ("MIB-000146", 0), ("MIB-000690", 0), ("MIB-000933", 1),
                     ("MIB-000760", 2), ("MIB-000591", 1)]
    targets = anchors + [t for t in disagree_pool if t in census]
    print("qtf phase 2 — border walk + strong local overrides, OCR per candidate")
    print(f"{'page':22s} {'base':>12s} {'shipped':>12s} {'local':>12s}")
    for stem, page in targets:
        base = pl._base(stem, page)
        shipped = imaging.realign_bands(base)
        localv = realign_local(base, census[(stem, page)])
        row = [f"{stem} p{page}:"]
        marks = {}
        for name, g in (("base", base), ("shipped", shipped), ("local", localv)):
            if g is None:
                row.append(f"{'—':>12s}")
                continue
            ev, cm, txt = score(g)
            row.append(f"{ev:3d}/{cm:7.0f}")
            if stem == "MIB-000037":
                marks[name] = "arivara" in txt
        print(f"{row[0]:22s} {row[1]:>12s} {row[2]:>12s} {row[3]:>12s}"
              + (f"   name: {marks}" if marks else ""))


# --- qtf3: the graduated, self-contained corrector ---------------------------

def realign_local_sc(gray):
    """Profile-consistent local corrector, no census dependency — the candidate
    shape for promotion. Everything derives from ONE substrate (the merged
    offsets): seams from its ffilled profile, seam text classes from the rule-
    masked glyph profile, local overrides measured and applied against the same
    profile. Returns None when there is nothing to move."""
    offsets = pl.merged_offsets(gray)
    if offsets is None:
        return None
    profile = offsets.copy()
    last = np.nanmedian(offsets)
    for y in range(len(profile)):
        if not np.isnan(profile[y]):
            last = profile[y]
        profile[y] = last
    seams = [int(s) for s in ps._steps(profile)]
    if not seams:
        return None
    prof = text_row_profile(gray)
    pitch = line_pitch(prof)
    p2 = profile.copy()
    for i, s in enumerate(seams):
        above = float(prof[max(0, s - SEAM_WIN + 1):s + 1].sum())
        below = float(prof[s + 1:s + 1 + SEAM_WIN].sum())
        if above < TEXT_MASS_MIN or below < TEXT_MASS_MIN:
            continue                   # not a cuts-text seam: border keeps it
        m = seam_local_shift(gray, profile, s, pitch)
        if m is None or m["corr"] < QTF_MIN_CORR or abs(m["local"]) >= 150:
            continue
        delta = m["border"] - m["local"]
        if not delta:
            continue
        nxt = seams[i + 1] if i + 1 < len(seams) else len(profile) - 1
        p2[s + 1:nxt + 1] += delta
    return pc.realign_from(gray, p2)


def qtf3():
    import tempfile
    from mib.stages import render

    def score(g):
        with tempfile.TemporaryDirectory(prefix="qtf3") as tmp:
            p = Path(tmp) / "x.png"
            p.write_bytes(imaging.to_png_bytes(g))
            lines, conf = render._recognize(p)
        return render.evidence_score(lines), mass(conf), " ".join(lines).lower()

    table = [("MIB-000165", 1), ("MIB-000690", 0), ("MIB-000933", 1),
             ("MIB-000125", 4), ("MIB-000013", 2), ("MIB-000237", 1),
             ("MIB-000243", 2), ("MIB-000459", 1), ("MIB-000591", 1),
             ("MIB-000045", 2), ("MIB-000037", 0), ("MIB-000089", 1),
             ("MIB-000065", 1), ("MIB-000025", 4), ("MIB-000221", 0),
             ("MIB-000189", 2)]
    print("qtf3 — self-contained corrector vs base/shipped (ev/conf; guard = max conf)")
    print(f"{'page':20s} {'base':>10s} {'shipped':>10s} {'local-sc':>10s}  best")
    wins = pars = 0
    for stem, page in table:
        base = pl._base(stem, page)
        cands = {"base": base, "shipped": imaging.realign_bands(base),
                 "local": realign_local_sc(base)}
        scores = {}
        marks = {}
        row = [f"{stem} p{page}:"]
        for name in ("base", "shipped", "local"):
            g = cands[name]
            if g is None:
                row.append(f"{'—':>10s}")
                continue
            ev, cm, txt = score(g)
            scores[name] = cm
            row.append(f"{ev:2d}/{cm:6.0f}")
            if stem == "MIB-000037":
                marks[name] = "arivara" in txt
        best = max(scores, key=scores.get)
        if best == "local":
            wins += 1
        if scores.get("local") == scores.get("shipped"):
            pars += 1
        print(f"{row[0]:20s} {row[1]:>10s} {row[2]:>10s} {row[3]:>10s}  {best}"
              + (f"   name: {marks}" if marks else ""))
    print(f"local best-by-conf on {wins}/{len(table)} pages ({pars} shipped-parity)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["qta", "qtb", "qtc", "qtd", "qte", "qtf",
                                    "qtf2", "qtf3"])
    ap.add_argument("--procs", type=int, default=8)
    args = ap.parse_args()
    if args.cmd == "qtb":
        qtb(args.procs)
    else:
        {"qta": qta, "qtc": qtc, "qtd": qtd, "qte": qte, "qtf": qtf,
         "qtf2": qtf2, "qtf3": qtf3}[args.cmd]()


if __name__ == "__main__":
    main()
