#!/usr/bin/env python3
"""Closed-vocab exceedance probe (TODO 1.8's open half).

The per-read oracle marks species 37 / home_world 50 / declared_purpose 53
wrong dev fields ALL 0-reachable: no read's PARSE ever yields a snappable
value. Anchor autopsies (2026-07-26) show why — the label line is too garbled
for key_for (016 species: 'Shncies Conte LUNA SFCURIN', the value legible) or
every value read sits individually below the snap bar while the ensemble
points one way (013 home_world: 'Wie 106te' / 'Wiol-A06%0' / 'Walt-A06te' /
'Wl 106 te' for Wolf-1061c). The candidate mechanism is value-first scanning
(the row-18 flag principle) + cross-read aggregation: scan every OCR line the
merge already consults for token n-grams near a vocab entry, pool
confusion-weighted similarity per entry with page balance, emit the argmax at
a mined bar/margin. Injection guard: autocontrast makes white-text answer keys
OCR-visible (016's render+skew+local+autocon read carries the full key), so
_INJECTION_RE lines and comma-run key dumps never feed the scan.

Modes (cache defaults to the live grid substrate):
  bound    the arb_oracle target rows: does the aggregate argmax hit truth?
           This IS the full dev landscape for a fill-when-missing mechanism —
           every missing closed-vocab field is in the oracle wrong set.
  case S   dump one case's per-entry evidence (eyeball feed)

Usage: experiments/exceed_probe.py bound [--cache PATH] [--oracle PATH]
       experiments/exceed_probe.py case MIB-000013 [--cache PATH]
"""
import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mib import cache, config, packet as pk, vocab  # noqa: E402

CACHE = ROOT / "output/cache/train_grid.jsonl"
ORACLE = ROOT / "output/viz/arb_oracle.jsonl"
OUT = ROOT / "output/viz/exceed_bound.jsonl"

FIELDS = ("species_code", "home_world", "declared_purpose")


def _norm(s):
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


# entry -> space-stripped normal form; scanning compares stripped strings so
# token splits ('Wl 106 te') and fusions ('LUNA_SFCURIN') cost nothing.
ENTRIES = {
    "species_code": {e: _norm(e) for e in vocab.SPECIES},
    "home_world": {e: _norm(e) for e in vocab.HOME_WORLDS},
    "declared_purpose": {e: _norm(e) for e in vocab.PURPOSES},
}

# label forms for the corroboration boolean (recorded, not yet load-bearing)
LABELS = {
    "species_code": ("speciescode", "speciesmatch", "species"),
    "home_world": ("homeworld",),
    "declared_purpose": ("declaredpurpose", "purpose"),
}

SIM_FLOOR = 0.5          # record hits at/above this; bars mined offline
EXCESS_BASE = 0.55       # excess-mass baseline: debris sims hover at 0.50-0.55
_CASEID_RE = re.compile(r"MIB[-\s]?\d{6}", re.I)


def _key_dump(line):
    """A comma-run containing a case id is an answer-key dump, not evidence."""
    return line.count(",") >= 3 and _CASEID_RE.search(line) is not None


def _bag_overlap(a, b):
    ca, cb = Counter(a), Counter(b)
    return sum((ca & cb).values()) / max(len(a), len(b))


def _gram_sims(tokens, targets, floor=SIM_FLOOR):
    """Best weighted sim per target over all 1..4-token grams of the line."""
    best = {}
    n = len(tokens)
    for i in range(n):
        for k in (1, 2, 3, 4):
            if i + k > n:
                break
            g = "".join(tokens[i:i + k])
            if len(g) < 4:
                continue
            for name, t in targets.items():
                lo, hi = len(t) * 0.5, len(t) * 1.6
                if not lo <= len(g) <= hi:
                    continue
                if _bag_overlap(g, t) < 0.4:
                    continue
                s = vocab._weighted_sim(g, t)
                if s >= floor and s > best.get(name, 0.0):
                    best[name] = s
    return best


def _ocr_lines(rec):
    """(page_no, line) pairs from exactly the kvs merge_fields consults,
    deduped on (page, normalized text), injection-guarded."""
    pages, reads_by_page = cache.to_case(rec["pages"])
    packet = pk.assemble(pages, reads_by_page, rec["stem"])
    kvs = ([kv for _dt, kv in packet.variant_docs] +
           [kv for _dt, src, kv in packet.docs if src == pk.SRC_OCR])
    seen = set()
    out = []
    for kv in kvs:
        page_no = kv.get("_page_no")
        for line in kv.get("_raw") or []:
            if pk._INJECTION_RE.search(line) or _key_dump(line):
                continue
            key = (page_no, _norm(line))
            if len(key[1]) < 4 or key in seen:
                continue
            seen.add(key)
            out.append((page_no, line))
    return out


def scan_case(rec, field):
    """entry -> evidence dict for one case+field.

    Per distinct (page, normalized line) the entry's best gram sim is one
    observation; the stats offered to the offline miner:
      best      max sim anywhere
      pagesum   sum over pages of page-max (the vote's page-balance shape)
      excess    sum over distinct lines of max(0, sim - EXCESS_BASE) — the
                conf_excess_mass shape; distinct garbles of the same true
                value accumulate, dedup already removed variant echoes
      label_best  best sim among lines whose head also label-hits
    """
    targets = ENTRIES[field]
    labels = {f"_label{i}": l for i, l in enumerate(LABELS[field])}
    obs = {}                  # entry -> list of (page_no, sim, label_hit)
    for page_no, line in _ocr_lines(rec):
        tokens = re.findall(r"[a-z0-9]+", line.lower())
        if not tokens:
            continue
        sims = _gram_sims(tokens, targets)
        if not sims:
            continue
        lab = bool(_gram_sims(tokens, labels)) if labels else False
        for entry, s in sims.items():
            obs.setdefault(entry, []).append((page_no, s, lab))
    result = {}
    for entry, hits in obs.items():
        page_best = {}
        for page_no, s, _lab in hits:
            page_best[page_no] = max(page_best.get(page_no, 0.0), s)
        label_sims = [s for _p, s, lab in hits if lab]
        result[entry] = {
            "pages": len(page_best),
            "lines": len(hits),
            "best": round(max(s for _p, s, _l in hits), 3),
            "pagesum": round(sum(page_best.values()), 3),
            "excess": round(sum(max(0.0, s - EXCESS_BASE)
                                for _p, s, _l in hits), 3),
            "label_best": round(max(label_sims), 3) if label_sims else 0.0,
            "label_pages": len({p for p, _s, lab in hits if lab}),
        }
    return result


def _verdict(evidence, truth, stat="excess"):
    """(argmax, score, margin, hit) under the given statistic."""
    if not evidence:
        return None, 0.0, 0.0, False
    ranked = sorted(evidence.items(), key=lambda kv: -kv[1][stat])
    argmax, top = ranked[0]
    margin = top[stat] - (ranked[1][1][stat] if len(ranked) > 1 else 0.0)
    return argmax, top[stat], round(margin, 3), argmax == truth


def bound(cache_path, oracle_path):
    targets = {}
    for line in open(oracle_path):
        r = json.loads(line)
        if r["field"] in FIELDS:
            targets.setdefault(r["stem"], []).append(r)
    meta, recs = cache.read(cache_path)
    config.require_agreement([(str(cache_path), meta)])
    STATS = ("excess", "pagesum", "best")
    hit_by = {s: {f: Counter() for f in FIELDS} for s in STATS}
    rows = []
    for rec in recs:
        if rec.get("error") or rec["stem"] not in targets:
            continue
        for t in targets[rec["stem"]]:
            field, truth = t["field"], " ".join(str(t["truth"]).split())
            evidence = scan_case(rec, field)
            row = {"stem": rec["stem"], "field": field, "truth": t["truth"],
                   "predicted": t["predicted"], "evidence": evidence}
            for s in STATS:
                argmax, score, margin, hit = _verdict(evidence, truth, s)
                cls = ("FIX" if hit else
                       "NOHIT" if argmax is None else "WRONGFILL")
                hit_by[s][field][cls] += 1
                row[s] = {"argmax": argmax, "score": round(score, 3),
                          "margin": margin, "class": cls}
            rows.append(row)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    for s in STATS:
        print(f"--- statistic: {s}")
        print(f"{'field':18s} {'wrong':>5s} {'FIX':>4s} {'WRONG':>5s} {'NOHIT':>5s}")
        tot = Counter()
        for f in FIELDS:
            c = hit_by[s][f]
            tot.update(c)
            print(f"{f:18s} {sum(c.values()):5d} {c['FIX']:4d} "
                  f"{c['WRONGFILL']:5d} {c['NOHIT']:5d}")
        print(f"{'TOTAL':18s} {sum(tot.values()):5d} {tot['FIX']:4d} "
              f"{tot['WRONGFILL']:5d} {tot['NOHIT']:5d}")
    print(f"wrote {len(rows)} rows to {OUT}")


DEEP_FLOOR = 0.30        # diagnosis floor: is there ANY truth signal down here?
DEEP_BASE = 0.35         # aggregate excess baseline for the low-floor decoder


def _frag_hits(tokens, truth_norm):
    """Fragment check: grams matching a HALF of the truth at >=0.6 — the
    cross-line fragmentation case ('Wolf-' on one line, '1061c' on another)
    that whole-entry gram windows structurally cannot see."""
    n = len(truth_norm)
    if n < 8:
        return 0
    half = n // 2 + 1
    parts = {"head": truth_norm[:half], "tail": truth_norm[n - half:]}
    hits = _gram_sims(tokens, parts, floor=0.6)
    return len(hits)


def _shipped_bucket(row):
    """FIX / LOST / NOHIT for one bound row under the shipped pick+floor."""
    ev = row["evidence"]
    truth = " ".join(str(row["truth"]).split())
    if truth not in ev:
        return "NOHIT"
    pool = ({k: e for k, e in ev.items()
             if e.get("label_best", 0.0) >= _SHIPPED_LABEL_BAR} or ev)
    entry, e = max(pool.items(),
                   key=lambda kv: (kv[1]["best"], kv[1]["excess"]))
    accepted = (e["best"] >= _SHIPPED_ACCEPT
                or e.get("label_best", 0.0) >= _SHIPPED_LABEL_BAR)
    return "FIX" if (accepted and entry == truth) else "LOST"


_SHIPPED_LABEL_BAR = 0.55
_SHIPPED_ACCEPT = 0.60


def deep(cache_path, bound_path):
    """Price the residue: for targets the shipped fill missed, is there
    sub-floor or fragmented truth signal a low-floor ensemble decode (or char
    assembly) could use — or is the evidence genuinely dead?"""
    targets = {}
    for line in open(bound_path):
        r = json.loads(line)
        r["bucket"] = _shipped_bucket(r)
        targets.setdefault(r["stem"], []).append(r)
    meta, recs = cache.read(cache_path)
    config.require_agreement([(str(cache_path), meta)])
    classes = Counter()
    detail = []
    for rec in recs:
        if rec.get("error") or rec["stem"] not in targets:
            continue
        toks_per_line = [re.findall(r"[a-z0-9]+", line.lower())
                         for _p, line in _ocr_lines(rec)]
        for t in targets[rec["stem"]]:
            field, truth = t["field"], " ".join(str(t["truth"]).split())
            table = ENTRIES[field]
            truth_norm = table.get(truth)
            if truth_norm is None:
                continue
            agg = {}          # entry -> [excess_sum, first_count, best]
            truth_frag_lines = 0
            for tokens in toks_per_line:
                if _frag_hits(tokens, truth_norm):
                    truth_frag_lines += 1
                sims = _gram_sims(tokens, table, floor=DEEP_FLOOR)
                if not sims:
                    continue
                top = max(sims, key=sims.get)
                for entry, s in sims.items():
                    a = agg.setdefault(entry, [0.0, 0, 0.0])
                    a[0] += max(0.0, s - DEEP_BASE)
                    a[2] = max(a[2], s)
                    if entry == top:
                        a[1] += 1
            tr = agg.get(truth)
            if tr is None and truth_frag_lines == 0:
                cls = "DEAD"           # no whole-entry signal, no fragments
            elif tr is None or tr[0] == 0.0:
                cls = ("FRAG_ONLY" if truth_frag_lines else "DEAD")
            else:
                ranked = sorted(agg.items(), key=lambda kv: -kv[1][0])
                if ranked[0][0] == truth and (len(ranked) == 1 or
                                              ranked[0][1][0] > ranked[1][1][0]):
                    cls = "DECODER_WIN"
                else:
                    cls = "SIGNAL_LOSES"
            classes[(t["bucket"], cls)] += 1
            detail.append({"stem": rec["stem"], "field": field, "truth": truth,
                           "bucket": t["bucket"], "class": cls,
                           "truth_agg": tr, "frag_lines": truth_frag_lines,
                           "top3": sorted(((k, round(v[0], 3), v[1], round(v[2], 3))
                                           for k, v in agg.items()),
                                          key=lambda x: -x[1])[:3]})
    out = ROOT / "output/viz/exceed_deep.jsonl"
    with open(out, "w") as fh:
        for r in detail:
            fh.write(json.dumps(r) + "\n")
    print(f"{'bucket':7s} {'n':>3s}  DECODER_WIN SIGNAL_LOSES FRAG_ONLY DEAD")
    for bucket in ("NOHIT", "LOST", "FIX"):
        row = [classes.get((bucket, c), 0)
               for c in ("DECODER_WIN", "SIGNAL_LOSES", "FRAG_ONLY", "DEAD")]
        print(f"{bucket:7s} {sum(row):3d}  {row[0]:11d} {row[1]:12d} "
              f"{row[2]:9d} {row[3]:4d}")
    print(f"wrote {len(detail)} rows to {out}")


def one_case(cache_path, stem):
    meta, recs = cache.read(cache_path)
    config.require_agreement([(str(cache_path), meta)])
    rec = next((r for r in recs if r["stem"] == stem), None)
    if rec is None:
        raise SystemExit(f"{stem} not in cache")
    for field in FIELDS:
        evidence = scan_case(rec, field)
        print(f"--- {field}")
        for entry, e in sorted(evidence.items(), key=lambda kv: -kv[1]["pagesum"]):
            print(f"  {entry:22s} pagesum={e['pagesum']:6.3f} best={e['best']:5.3f} "
                  f"pages={e['pages']} lines={e['lines']} label_pages={e['label_pages']}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=["bound", "deep", "case"])
    ap.add_argument("stem", nargs="?")
    ap.add_argument("--cache", default=str(CACHE))
    ap.add_argument("--oracle", default=str(ORACLE))
    a = ap.parse_args()
    if a.cmd == "bound":
        bound(a.cache, a.oracle)
    elif a.cmd == "deep":
        deep(a.cache, OUT)
    else:
        if not a.stem:
            ap.error("case mode needs a stem")
        one_case(a.cache, a.stem)
