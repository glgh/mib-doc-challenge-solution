#!/usr/bin/env python3
"""Zero-OCR arbitration metric lab (plan Track A1/A2, TODO 1.7).

The selection metric `records.conf_excess_mass` has three measured mis-ranking
modes (findings.md 2026-07-26): NL-prior under-payment of domain tokens (023),
single-word debris volume (023's `f`), and mid-page watermark furniture mass
(794's `SAMPLE DENIAL`). Every candidate fix below is a pure function of the
stored conf tuples (schema 4), so the whole lab replays against the full-corpus
cache with zero OCR: patch `records.conf_excess_mass`, re-run S3-S5, score the
dev split.

Subcommands (cache defaults to output/cache/train_conf4.jsonl):
  census             which pages change their winning read, per variant vs shipped
  score V [V...]     replay the corpus under each variant, score the dev split
  diff V             field-level diff vs the shipped-metric replay, truth-annotated,
                     dev cases only — the eyeball feed
Usage: experiments/probe_arbitration.py {census|score|diff} [variants] [--cache PATH]
"""
import argparse
import collections
import difflib
import functools
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CH = ROOT.parent / "mib-doc-challenge"

from mib import cache, config, emit, records, runner, vocab  # noqa: E402

CACHE = ROOT / "output/cache/train_conf4.jsonl"
OUT = ROOT / "output/viz/arb_census.jsonl"
EVAL_BASE = ROOT / "output/eval"

BASELINE = records.CONF_BASELINE          # 40.0
FOOTER_Y = records.FOOTER_Y               # 0.90
DOM_FLOOR = 25.0                          # lowered junk floor for domain-verified lines
DOM_W = 0.5                               # per-hit multiplicative bonus (domw only)

# Furniture watermarks, not evidence: the "sample denial" trap plus stamp-copy
# vocabulary. Guard fires only when EVERY alpha token of a line is furniture, so
# a real sentence containing "copy" keeps its mass.
WATERMARK = {"SAMPLE", "DENIAL", "SPECIMEN", "COPY", "VOID", "DRAFT", "DUPLICATE"}

# --- domain worth (A1) -------------------------------------------------------

ID_RE = re.compile(r"\b(?:M[I1l]B|[S5]PN)\s*[-–—:]?\s*\d{4,6}\b", re.I)
DATE_RE = re.compile(r"\b\d{4}[-–—/.]\d{2}[-–—/.]\d{2}\b")
VISA_RE = re.compile(r"\b(?:XW[-=\s]?[12]|DIP[-=\s]?1|MED[-=\s]?3|TRANSIT[-=\s]?7)\b", re.I)
LABEL_RE = re.compile(
    r"case\s*.?d|applicant|species|home\s*wor|visa\s*cla|arriv|purpose|sponsor|"
    r"fee\s*stat|observed", re.I)
FEE_RE = re.compile(r"\b(?:paid|waived|unpaid)\b", re.I)

_SPECIES_WORDS = [s.replace("_", " ") for s in vocab.SPECIES]


@functools.lru_cache(maxsize=1 << 16)
def domain_hits(text):
    """How many domain-anchored things this line visibly contains.

    Regex classes are cheap and run on every line; the fuzzy lexicon pass
    (species / home world, the 023 class) is gated on a field label being
    present, both for cost and because a lexicon word floating outside any
    labelled line is weaker evidence. Memoized: reads of the same page repeat
    most of their lines verbatim.
    """
    hits = 0
    hits += 2 * len(ID_RE.findall(text))          # case/sponsor ids: strongest anchors
    hits += len(DATE_RE.findall(text))
    hits += len(VISA_RE.findall(text))
    hits += len(FEE_RE.findall(text))
    labelled = bool(LABEL_RE.search(text))
    hits += labelled
    if labelled:
        up = re.sub(r"[^A-Z ]+", " ", text.upper())
        if difflib.get_close_matches(up.split(":")[-1].strip(), _SPECIES_WORDS,
                                     n=1, cutoff=0.75):
            hits += 2
        elif difflib.get_close_matches(text.split(":")[-1].strip(),
                                       vocab.HOME_WORLDS, n=1, cutoff=0.75):
            hits += 2
    return hits


def _is_watermark(text):
    tokens = re.findall(r"[A-Za-z]+", text)
    return bool(tokens) and all(t.upper() in WATERMARK for t in tokens)


# --- metric variants ---------------------------------------------------------
# Each takes a Read and returns a float, or None when the read has no conf
# (best_read then falls back to `quality`, exactly like the shipped metric).

def _entries(read):
    if read.conf is None:
        return None
    return [e for e in read.conf if e[2] < FOOTER_Y]


def _text(entry):
    return entry[3] if len(entry) > 3 else ""


def m_shipped(read):
    ents = _entries(read)
    if ents is None:
        return None
    return sum(max(0.0, e[0] - BASELINE) * e[1] for e in ents)


def m_mw2(read):
    """A2a: lines of a single word contribute nothing (023's `f`)."""
    ents = _entries(read)
    if ents is None:
        return None
    return sum(max(0.0, e[0] - BASELINE) * e[1] for e in ents if e[1] >= 2)


def m_wmk(read):
    """A2b: watermark-furniture lines contribute nothing (794's SAMPLE DENIAL)."""
    ents = _entries(read)
    if ents is None:
        return None
    return sum(max(0.0, e[0] - BASELINE) * e[1] for e in ents
               if not _is_watermark(_text(e)))


def m_guards(read):
    """A2: both guards."""
    ents = _entries(read)
    if ents is None:
        return None
    return sum(max(0.0, e[0] - BASELINE) * e[1] for e in ents
               if e[1] >= 2 and not _is_watermark(_text(e)))


def m_pw(read):
    """Per-word mean excess (the volume-bias fix measured on 352), guarded."""
    ents = _entries(read)
    if ents is None:
        return None
    kept = [e for e in ents if e[1] >= 2 and not _is_watermark(_text(e))]
    words = sum(e[1] for e in kept)
    if not words:
        return 0.0
    return sum(max(0.0, e[0] - BASELINE) * e[1] for e in kept) / words


def m_dom(read):
    """A1: guards + lowered conf floor for domain-verified lines.

    The recognizer's NL prior pays `AQUARIAN MANTIS` conf ~40 — zero excess
    over the shipped floor — while junk syllables clear it. A line whose text
    pattern-matches our closed world (ids, dates, visa codes, labelled lexicon
    values) has *independent* evidence of being real text, so its junk floor
    drops to DOM_FLOOR. Still a floor, not a bypass: hallucinated ids below
    DOM_FLOOR stay at zero (the 990 anti-pathology). Domain lines are also
    exempt from the min-words guard — `XW-1` alone on a line is one word.
    """
    ents = _entries(read)
    if ents is None:
        return None
    total = 0.0
    for e in ents:
        text = _text(e)
        if _is_watermark(text):
            continue
        hits = domain_hits(text) if text else 0
        if hits:
            total += max(0.0, e[0] - DOM_FLOOR) * e[1]
        elif e[1] >= 2:
            total += max(0.0, e[0] - BASELINE) * e[1]
    return total


def m_domw(read):
    """A1b: dom + multiplicative worth (1 + DOM_W * hits) on domain lines."""
    ents = _entries(read)
    if ents is None:
        return None
    total = 0.0
    for e in ents:
        text = _text(e)
        if _is_watermark(text):
            continue
        hits = domain_hits(text) if text else 0
        if hits:
            total += max(0.0, e[0] - DOM_FLOOR) * e[1] * (1.0 + DOM_W * hits)
        elif e[1] >= 2:
            total += max(0.0, e[0] - BASELINE) * e[1]
    return total


VARIANTS = {"shipped": m_shipped, "mw2": m_mw2, "wmk": m_wmk, "guards": m_guards,
            "pw": m_pw, "dom": m_dom, "domw": m_domw}


# --- harness -----------------------------------------------------------------

def load(cache_path):
    meta, recs = cache.read(cache_path)
    config.require_agreement([(str(cache_path), meta)])
    if meta and meta.get("schema", 0) < 4:
        raise SystemExit("need a schema-4 cache (conf tuples carry text)")
    return meta, recs


def census(cache_path, names):
    """Which pages would hand the primary slot to a different read, per variant."""
    _meta, recs = load(cache_path)
    fns = {n: VARIANTS[n] for n in names}
    changed = {n: 0 for n in fns}
    pages_with_choice = 0
    rows = []
    for rec in recs:
        if rec.get("error"):
            continue
        _pages, reads_by_page = cache.to_case(rec["pages"])
        for page_no, reads in reads_by_page.items():
            scored = [(r, m_shipped(r)) for r in reads]
            if sum(s is not None for _r, s in scored) < 2:
                continue
            pages_with_choice += 1
            base = max((r for r, s in scored if s is not None),
                       key=lambda r: m_shipped(r))
            row = None
            for name, fn in fns.items():
                if name == "shipped":
                    continue
                alt = max((r for r in reads if fn(r) is not None),
                          key=fn, default=None)
                if alt is not None and alt.variant != base.variant:
                    changed[name] += 1
                    if row is None:
                        row = {"stem": rec["stem"], "page": page_no,
                               "shipped": base.variant, "flips": {}}
                    row["flips"][name] = {
                        "to": alt.variant,
                        "shipped_margin": round(m_shipped(base) - (m_shipped(alt) or 0), 1),
                        "variant_margin": round(fn(alt) - (fn(base) or 0), 1),
                    }
            if row:
                rows.append(row)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    print(f"{pages_with_choice} pages with a real choice (>=2 conf reads)")
    for name, n in sorted(changed.items()):
        if name != "shipped":
            print(f"  {name:8s} flips the winner on {n} pages")
    print(f"wrote {len(rows)} flip rows to {OUT}")


def replay_with(cache_path, name):
    """Replay S3-S5 under a patched metric -> (predictions, debugs, cache meta)."""
    fn = VARIANTS[name]
    meta, recs = load(cache_path)
    original = records.conf_excess_mass
    records.conf_excess_mass = fn
    try:
        out, debugs = [], []
        for rec in recs:
            if rec.get("error"):
                continue
            pages, reads_by_page = cache.to_case(rec["pages"])
            record, debug = runner.predict_from_evidence(pages, reads_by_page,
                                                         rec["stem"])
            out.append(record)
            debugs.append(debug)
    finally:
        records.conf_excess_mass = original
    return emit.dedupe(out), debugs, meta


def score(cache_path, names):
    sys.path.insert(0, str(ROOT / "scripts"))
    import score_split
    for name in names:
        preds, _debugs, cmeta = replay_with(cache_path, name)
        out_dir = EVAL_BASE / f"arb_{name}"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "predictions.jsonl").write_text(
            "".join(json.dumps(r, sort_keys=True) + "\n" for r in preds))
        (out_dir / "meta.json").write_text(json.dumps(config.stamp(
            artifact="eval", probe=f"arbitration:{name}",
            restore=(cmeta or {}).get("restore"),
            replayed_from=str(cache_path)), indent=2, sort_keys=True) + "\n")
        print(f"\n==== variant {name} -> {out_dir} ====")
        score_split.main(out_dir, "dev")


def diff(cache_path, name):
    """Field diff vs shipped, dev only, truth-annotated — the eyeball feed."""
    import csv
    dev = set(json.loads((ROOT / "data_splits.json").read_text())["dev"])
    truth = {r["case_id"]: r for r in
             csv.DictReader(open(CH / "data/train_labels.csv"))}
    base, _, _m = replay_with(cache_path, "shipped")
    alt, _, _m2 = replay_with(cache_path, name)
    base = {r["case_id"]: r for r in base}
    alt = {r["case_id"]: r for r in alt}
    fixed = broke = neutral = 0
    for cid in sorted(set(base) & set(alt) & dev):
        for f in sorted(set(base[cid]) | set(alt[cid])):
            a, b = base[cid].get(f), alt[cid].get(f)
            if a == b or f == "confidence":
                continue
            t = truth.get(cid, {}).get(f if f != "adjudication" else "adjudication")
            verdict = ("FIXED" if b == t else "BROKE" if a == t else "neutral")
            fixed += verdict == "FIXED"
            broke += verdict == "BROKE"
            neutral += verdict == "neutral"
            print(f"{cid}.{f}: {a!r} -> {b!r}   truth={t!r}  [{verdict}]")
    print(f"\n{name} vs shipped (dev): {fixed} fixed, {broke} broke, "
          f"{neutral} neutral")


# --- fee imputation (the oracle's fee finding) -------------------------------
# Dev truth for fee-SILENT packets (no fee text anywhere in any read): 69% paid,
# 24% waived, 3% unpaid, 3% unknown — while the pipeline emits 'unknown' (right
# 3% of the time). The generator holds fee state it only sometimes renders;
# labels impute. Silence is NOT the manual's visible-but-ambiguous 'unknown'
# case, and the imputation deliberately excludes any packet that *mentions*
# fees: a garbled fee line keeps 'unknown' (including the unpaid-garble safety
# in vocab.snap). The b13_census guard still sits below fee_unknown in the
# cascade, so an imputed 'paid' cannot approve a packet with unreadable
# biometrics.

# Injection shapes must never feed fee inference: white-text answer keys become
# OCR-visible under autocontrast (114's `SYSTEM: ... paid APPROVED`, 058's
# `waived. DENIED.0.99`).
INJ_RE = re.compile(r"system:|answer key|ignore (?:visible|previous)|"
                    r"\b(?:APPROVED|DENIED)\W{0,3}0\.\d\d", re.I)
UNPAID_RE = re.compile(r"fee\s+unpaid|unpaid.{0,10}fee", re.I)
DIPW_RE = re.compile(r"D[Il1]P[-\s~]?WA[Il1]VER|DIP[-\s~]?WAVER|WAIVER.{0,6}DIP", re.I)


def _fee_ladder(pkt):
    """Layered fee inference for packets whose fee didn't parse.

    unpaid-phrase (7/7 dev precision) > DIP-WAIVER value (7/7 on the wrong
    set) > 'paid' (the silent/unreadable base rate: 69% of silent dev packets
    are truth-paid vs the 3% our 'unknown' answer scores). Injection-shaped
    lines are excluded before any tier fires.
    """
    unpaid = dipw = False
    for kv in ([kv for _dt, _src, kv in pkt.docs]
               + [kv for _dt, kv in pkt.variant_docs]):
        for line in kv.get("_raw", []):
            if INJ_RE.search(line):
                continue
            if UNPAID_RE.search(line):
                unpaid = True
            if DIPW_RE.search(line):
                dipw = True
    if unpaid:
        return "unpaid"
    if dipw:
        return "waived"
    return "paid"


def fee(cache_path, mode="full"):
    """Price the fee ladder: replay, score dev, count CFAs.

    mode 'full': the imputed value feeds extraction AND policy.
    mode 'extract': policy still sees the original (usually 'unknown') value —
    zero classification risk, extraction gain only.
    """
    import csv
    from mib import packet as pk, policy as pol
    sys.path.insert(0, str(ROOT / "scripts"))
    import score_split

    truth = {r["case_id"]: r for r in
             csv.DictReader(open(CH / "data/train_labels.csv"))}
    dev = set(json.loads((ROOT / "data_splits.json").read_text())["dev"])

    orig_merge, orig_adj = pk.merge_fields, pol.adjudicate

    def imputing(pkt, provenance=None):
        values = orig_merge(pkt, provenance)
        if (values.get("fee_status") or "unknown") == "unknown":
            ladder = _fee_ladder(pkt)
            # 'evidence' mode: the unpaid-phrase and DIP-WAIVER tiers are
            # visible document evidence and feed policy; only the silent->paid
            # base-rate tier is inference and stays extraction-side.
            if mode == "evidence" and ladder == "paid":
                values["_fee_policy"] = values.get("fee_status")
            elif mode == "extract":
                values["_fee_policy"] = values.get("fee_status")
            values["fee_status"] = ladder
        return values

    def policy_sees_original(values, sig):
        if "_fee_policy" in values:
            shadow = dict(values)
            shadow["fee_status"] = values["_fee_policy"]
            return orig_adj(shadow, sig)
        return orig_adj(values, sig)

    pk.merge_fields = imputing
    if mode in ("extract", "evidence"):
        pol.adjudicate = policy_sees_original
    try:
        preds, _debugs, cmeta = replay_with(cache_path, "shipped")
    finally:
        pk.merge_fields, pol.adjudicate = orig_merge, orig_adj
    for r in preds:
        r.pop("_fee_policy", None)

    out_dir = EVAL_BASE / f"arb_fee_{mode}"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "predictions.jsonl").write_text(
        "".join(json.dumps(r, sort_keys=True) + "\n" for r in preds))
    (out_dir / "meta.json").write_text(json.dumps(config.stamp(
        artifact="eval", probe=f"arbitration:fee_{mode}",
        restore=(cmeta or {}).get("restore"),
        replayed_from=str(cache_path)), indent=2, sort_keys=True) + "\n")

    cfa = [r["case_id"] for r in preds
           if r["case_id"] in dev and r["adjudication"] == "APPROVED"
           and truth.get(r["case_id"], {}).get("adjudication") == "DENIED"]
    print(f"[{mode}] dev false approvals: {len(cfa)} {cfa[:10]}")
    base = {json.loads(l)["case_id"]: json.loads(l)
            for l in open(EVAL_BASE / "arb_shipped/predictions.jsonl")}
    flips = collections.Counter()
    for r in preds:
        cid = r["case_id"]
        if cid in dev and cid in base and r["adjudication"] != base[cid]["adjudication"]:
            t = truth.get(cid, {}).get("adjudication")
            flips[(base[cid]["adjudication"], r["adjudication"], t)] += 1
    for (a, b, t), n in flips.most_common():
        print(f"  adjud {a} -> {b} (truth {t}): {n}")
    score_split.main(out_dir, "dev")


ORACLE_OUT = ROOT / "output/viz/arb_oracle.jsonl"

# The 8 scalar schema fields the S4 vote settles. risk_flags (signals scan) and
# case_id (its own vote) travel different machinery and get their own oracles.
SCALAR_FIELDS = ("applicant_name", "species_code", "home_world", "visa_class",
                 "declared_purpose", "sponsor_id", "arrival_date", "fee_status")


def _norm(v):
    return " ".join(str(v or "").split()).strip().rstrip(".")


def oracle(cache_path, pred_path):
    """Upper bound for ALL selection/fusion work (A3's addressable market).

    For every dev case field the given replay gets wrong (non-empty truth),
    ask: does the truth string appear in ANY read's parse of ANY page, raw or
    snap-repaired? Reachable = a merge that chose perfectly would have scored
    it; unreachable = no arbitration can help, the value was never read.
    Predictions must come from the same rev/substrate as the cache, or the
    wrong-set is stale and the bound aims at already-fixed cases.
    """
    import csv
    from mib import packet as pk
    dev = set(json.loads((ROOT / "data_splits.json").read_text())["dev"])
    truth = {r["case_id"]: r for r in
             csv.DictReader(open(CH / "data/train_labels.csv"))}
    print(f"predictions: {pred_path}")
    preds = {json.loads(l)["case_id"]: json.loads(l)
             for l in open(pred_path)}
    _meta, recs = load(cache_path)

    counts = {f: [0, 0] for f in SCALAR_FIELDS}    # field -> [wrong, reachable]
    rows = []
    for rec in recs:
        stem = rec["stem"]
        if rec.get("error") or stem not in dev or stem not in preds:
            continue
        t, p = truth.get(stem), preds[stem]
        if not t:
            continue
        wrong = [f for f in SCALAR_FIELDS
                 if _norm(t.get(f)) and _norm(p.get(f)) != _norm(t.get(f))]
        if not wrong:
            continue
        pages, reads_by_page = cache.to_case(rec["pages"])
        # every parse the pipeline could have seen: text layer + every read
        kvs = []
        for pt in pages:
            kvs.append(("text", pt.page_no,
                        pk._parse_lines(pt.visible_lines, ocr=False)))
            for r in reads_by_page.get(pt.page_no) or []:
                kvs.append((r.variant, pt.page_no,
                            pk._parse_lines(r.lines, ocr=True)))
        for f in wrong:
            counts[f][0] += 1
            tv = _norm(t.get(f))
            sources = []
            for variant, page_no, kv in kvs:
                raws = [kv.get(f)]
                if f == "applicant_name":
                    raws.append(kv.get("registry_name"))
                for raw in raws:
                    if not raw:
                        continue
                    for cand in (raw, vocab.snap(f, raw)):
                        if cand and _norm(cand) == tv:
                            sources.append(f"{variant}/p{page_no}")
                            break
            if sources:
                counts[f][1] += 1
            rows.append({"stem": stem, "field": f, "truth": t.get(f),
                         "predicted": p.get(f), "reachable": bool(sources),
                         "sources": sources[:8]})
    with open(ORACLE_OUT, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    print(f"{'field':18s} wrong reachable")
    tot_w = tot_r = 0
    for f, (w, r) in counts.items():
        print(f"{f:18s} {w:5d} {r:9d}")
        tot_w += w
        tot_r += r
    print(f"{'TOTAL':18s} {tot_w:5d} {tot_r:9d}")
    print(f"wrote {len(rows)} rows to {ORACLE_OUT}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=["census", "score", "diff", "oracle", "fee"])
    ap.add_argument("variants", nargs="*", default=None)
    ap.add_argument("--cache", default=str(CACHE))
    ap.add_argument("--pred", default=str(EVAL_BASE / "arb_shipped/predictions.jsonl"),
                    help="oracle only: predictions.jsonl from the SAME rev/substrate "
                         "as --cache")
    a = ap.parse_args()
    names = a.variants or list(VARIANTS)
    if a.cmd not in ("fee", "oracle"):
        bad = [n for n in names if n not in VARIANTS]
        if bad:
            ap.error(f"unknown variant(s) {bad}; have {list(VARIANTS)}")
    if a.cmd == "census":
        census(a.cache, names)
    elif a.cmd == "score":
        score(a.cache, names)
    elif a.cmd == "oracle":
        oracle(a.cache, a.pred)
    elif a.cmd == "fee":
        fee(a.cache, a.variants[0] if a.variants else "full")
    else:
        diff(a.cache, names[0])
