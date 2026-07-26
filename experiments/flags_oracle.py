#!/usr/bin/env python3
"""Reachability oracle for wrong risk_flags fields (TODO 1.10) — offline, no OCR.

risk_flags is the 8-weight field with no reachability bound: the selection
oracle (probe_arbitration.py, row 53) scoped scalar fields only, because flags
are a *scan across all reads*, not a per-read selection. This probe builds the
missing bound. For every dev case whose emitted risk_flags set is wrong, each
missing truth flag is classified by the strongest evidence ANY reading of the
packet holds for it, under the production matchers themselves
(signals._flags_in_line token path, signals._labelled_flag_value +
vocab.match_flag_value whole-value path):

  inferred_only     flag is in sig['flags'] but not emit_flags — policy already
                    acts on it; organizer ruling §1 forbids emitting it
  reachable        the shipped scan should emit it: token hit or value at the
                    shipped bars on a surface assemble keeps — plumbing gap,
                    expected ~0 by construction
  value_blocked    the value path reaches it at the shipped bars, but on a line
                    the token path already resolved a DIFFERENT flag from —
                    observed_flags' `if hits: continue` skips the rescue there,
                    so a two-flag value line emits only its legible half
  decoy_only       derivable, but only from a reading the decoy filter drops —
                    wrong-page attribution, a merge question not a matcher one
  near_miss        a labelled value argmaxes to the truth flag below the bars
                    (score/margin/quorum), or a token resolves at a relaxed
                    cutoff — Track 2 territory (matcher/bars too strict)
  hidden_only      the flag exists only in hidden text — the injection channel,
                    correctly not emitted; unreachable by design
  unreachable      no reading holds it in any form — OCR never read it
                    (Track 3 territory) or evidence genuinely absent

Extra (emitted-but-untrue) flags are traced back to the line(s) that matched,
with the path (token/value) and surface, for hand inspection.

Usage: experiments/flags_oracle.py [cache.jsonl] [--split dev|hold|all] [--full]
Default cache: output/cache/train_<RESTORE>.jsonl. Writes per-case detail to
output/viz/flags_oracle.jsonl; prints the summary tables.
"""
import argparse
import csv
import json
import re
from collections import Counter
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CH = ROOT.parent / "mib-doc-challenge"

from mib import cache, config, packet, parse, signals, vocab  # noqa: E402

TRUTH = {r["case_id"]: r for r in csv.DictReader(open(CH / "data/train_labels.csv"))}

# Relaxed token bar for the near-miss probe only — production is 0.7/0.15.
RELAX_CUTOFF, RELAX_MARGIN = 0.60, 0.10
# Argmax floor for counting a below-bar value as a near miss rather than noise:
# the mined safety table's false rows cluster at <=0.40 (signals.py bars).
NEAR_VALUE_FLOOR = 0.30

_TOKEN_SPLIT = signals._TOKEN_SPLIT
_NORM = re.compile(r"[^a-z0-9]+")


def flag_set(v):
    """Pipe-delimited field -> set, the way evaluate.py normalizes it."""
    return {t.strip().lower() for t in (v or "").split("|")
            if t.strip() and t.strip().lower() != "none"}


def _surfaces(pages, reads_by_page, case_id):
    """Every reading of the packet as (kind, kept, page_no, variant, lines).

    kind: 'text' | 'ocr' | 'hidden'. kept mirrors assemble: text/primary lines
    are kept unless the page is a decoy; variant reads additionally survive only
    if non-decoy. Hidden lines are never kept (untrusted channel, listed so the
    oracle can tell injection-only flags from genuinely absent ones).
    """
    from mib.records import best_read
    out = []
    for pt in pages:
        reads = reads_by_page.get(pt.page_no) or []
        primary = best_read(reads)
        if pt.is_scan_only and primary is not None and primary.lines:
            kept = not packet._decoy(primary.lines, case_id, ocr=True)
            out.append(("ocr", kept, pt.page_no, primary.variant, primary.lines))
        else:
            kept = not packet._decoy(pt.visible_lines, case_id,
                                     ocr=False) if pt.visible_lines else False
            out.append(("text", kept, pt.page_no, "", pt.visible_lines))
        for r in reads:
            if primary is not None and r is primary and pt.is_scan_only:
                continue
            if not r.lines:
                continue
            kept = pt.is_scan_only and not packet._decoy(r.lines, case_id, ocr=True)
            out.append(("ocr", kept, pt.page_no, r.variant, r.lines))
        if pt.hidden_lines:
            out.append(("hidden", False, pt.page_no, "", pt.hidden_lines))
    return out


def _relaxed_tokens(line):
    """Token path at the relaxed cutoff, same split/guards as _flags_in_line."""
    low = line.strip().lower()
    if signals._LEGEND_RE.search(low):
        return set()
    tokens = [t.strip(signals._STRIP) for t in _TOKEN_SPLIT.split(low)]
    found = set()
    for i, tok in enumerate(tokens):
        if not tok or any(w in signals._NEGATION for w in tokens[max(0, i - 2):i]):
            continue
        flag = vocab.match_flag_token(tok, cutoff=RELAX_CUTOFF, margin=RELAX_MARGIN)
        if flag:
            found.add(flag)
    return found


def scan_case(pages, reads_by_page, case_id):
    """All flag evidence in the packet, per surface. Returns a list of hit dicts."""
    hits = []
    for kind, kept, page_no, variant, lines in _surfaces(pages, reads_by_page, case_id):
        for line in lines:
            base = dict(kind=kind, kept=kept, page=page_no, variant=variant,
                        line=line)
            token_hits = signals._flags_in_line(line)
            for f in token_hits:
                hits.append(dict(base, path="token", flag=f, score=1.0, margin=1.0))
            for f in _relaxed_tokens(line) - token_hits:
                hits.append(dict(base, path="token_relaxed", flag=f,
                                 score=RELAX_CUTOFF, margin=RELAX_MARGIN))
            value = signals._labelled_flag_value(line)
            if value is not None:
                flag, score, margin = vocab.match_flag_value(value)
                if flag:
                    # blocked: production's rescue never scores this line — the
                    # token path already resolved something from it.
                    hits.append(dict(base, path="value", flag=flag,
                                     score=round(score, 3), margin=round(margin, 3),
                                     blocked=bool(token_hits)))
            # Bare containment on hidden text: injections often print the flag
            # verbatim without a label; the matchers above only see labelled or
            # tokenized shapes.
            if kind == "hidden":
                norm = _NORM.sub("", line.lower())
                for f in parse.ALL_FLAGS:
                    if _NORM.sub("", f) in norm:
                        hits.append(dict(base, path="contains", flag=f,
                                         score=1.0, margin=1.0))
    return hits


def classify_missing(flag, hits, inferred):
    """Strongest-evidence class for one missing truth flag (docstring order)."""
    mine = [h for h in hits if h["flag"] == flag]
    kept = [h for h in mine if h["kept"]]
    dropped = [h for h in mine if not h["kept"] and h["kind"] != "hidden"]
    def shipped(hs, unblocked_only):
        vals = [h for h in hs if h["path"] == "value"
                and not (unblocked_only and h.get("blocked"))]
        quorum = Counter(h["page"] for h in vals
                         if h["score"] >= signals.VALUE_QUORUM_SCORE
                         and h["margin"] >= signals.VALUE_QUORUM_MARGIN)
        return any(h["path"] == "token" for h in hs) or any(
            h["score"] >= signals.VALUE_SINGLE_SCORE
            and h["margin"] >= signals.VALUE_SINGLE_MARGIN for h in vals) or any(
            n >= signals.VALUE_QUORUM_N for n in quorum.values())

    if flag in inferred:
        return "inferred_only"
    if shipped(kept, unblocked_only=True):
        return "reachable"
    if shipped(kept, unblocked_only=False):
        return "value_blocked"
    if shipped(dropped, unblocked_only=False):
        return "decoy_only"
    near = [h for h in kept + dropped
            if h["path"] == "token_relaxed"
            or (h["path"] == "value" and h["score"] >= NEAR_VALUE_FLOOR)]
    if near:
        return "near_miss"
    if any(h["kind"] == "hidden" for h in mine):
        return "hidden_only"
    return "unreachable"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cache", nargs="?",
                    default=str(ROOT / f"output/cache/train_{config.RESTORE}.jsonl"))
    ap.add_argument("--split", default="dev", choices=["dev", "hold", "all"])
    ap.add_argument("--out", default=str(ROOT / "output/viz/flags_oracle.jsonl"))
    a = ap.parse_args()

    dev = set(json.loads((ROOT / "data_splits.json").read_text())["dev"])
    meta, recs = cache.read(a.cache)
    want = {"dev": lambda c: c in dev, "hold": lambda c: c not in dev,
            "all": lambda c: True}[a.split]

    n_cases = n_wrong = 0
    missing_by_class = Counter()          # class -> n flag instances
    missing_detail = Counter()            # (class, flag) -> n
    extra_by_path = Counter()             # best path -> n
    rows = []
    for rec in recs:
        cid = rec.get("stem")
        if cid not in TRUTH or not want(cid) or rec.get("error"):
            continue
        n_cases += 1
        pages, reads_by_page = cache.to_case(rec["pages"])
        pkt = packet.assemble(pages, reads_by_page, cid)
        values = packet.merge_fields(pkt)
        sig = signals.derive(pkt, values)
        emitted = set(sig["emit_flags"])
        truth = flag_set(TRUTH[cid]["risk_flags"])
        if emitted == truth:
            continue
        n_wrong += 1
        hits = scan_case(pages, reads_by_page, pkt.case_id)
        inferred = sig["flags"] - emitted
        row = dict(case=cid, truth=sorted(truth), emitted=sorted(emitted),
                   adjudication_truth=TRUTH[cid]["adjudication"],
                   missing={}, extra={})
        for f in sorted(truth - emitted):
            cls = classify_missing(f, hits, inferred)
            missing_by_class[cls] += 1
            missing_detail[(cls, f)] += 1
            best = max((h for h in hits if h["flag"] == f),
                       key=lambda h: (h["kept"], h["score"], h["margin"]),
                       default=None)
            row["missing"][f] = dict(cls=cls, best=best)
        for f in sorted(emitted - truth):
            srcs = [h for h in hits if h["flag"] == f and h["kept"]
                    and h["path"] in ("token", "value")]
            path = srcs[0]["path"] if srcs else "unknown"
            extra_by_path[path] += 1
            row["extra"][f] = dict(path=path, sources=srcs[:4])
        rows.append(row)

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with cache.open_write(out, config.stamp(
            artifact="flags_oracle", cache=str(a.cache), cache_meta=meta,
            split=a.split, n_cases=n_cases, n_wrong=n_wrong)) as f:
        for row in rows:
            cache.append(f, row)

    n_missing = sum(missing_by_class.values())
    n_extra = sum(extra_by_path.values())
    print(f"{a.split}: {n_cases} cases scored, {n_wrong} wrong risk_flags fields "
          f"({n_missing} missing flag instances, {n_extra} extra)")
    print("\nmissing flags by reachability class:")
    for cls in ("inferred_only", "reachable", "value_blocked", "decoy_only",
                "near_miss", "hidden_only", "unreachable"):
        if not missing_by_class[cls]:
            continue
        per_flag = ", ".join(f"{f} {n}" for (c, f), n in
                             sorted(missing_detail.items()) if c == cls)
        print(f"  {cls:14s} {missing_by_class[cls]:4d}   {per_flag}")
    print("\nextra flags by source path:")
    for path, n in extra_by_path.most_common():
        print(f"  {path:14s} {n:4d}")
    print(f"\ndetail -> {out}")


if __name__ == "__main__":
    main()
