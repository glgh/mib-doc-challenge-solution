#!/usr/bin/env python3
"""Hidden answer keys as a dev-side bug finder (row 80; user call 2026-07-27).

188/1000 train packets carry an injected answer key whose FIELD values are
90-95% true (fee 98%) while its verdict is false 188/188. That makes the keys
free ~true pseudo-labels sitting inside the packets themselves — including the
5,000 unlabeled validation PDFs. This tool joins a page-text cache against a
predictions file and emits every field where our output disagrees with the
key, classified by how much honest ink sits near the key value:

  both_printed  sim >= 0.95  the key value IS in honest ink and we emitted
                             something else — an arbitration/merge bug worklist
                             (MIB-000085/177: two visa values printed, merge
                             picked the wrong one)
  near          sim >= 0.70  garbled ink near the key value — snap/vote misses
  dead          sim <  0.70  no ink evidence — extraction genuinely blocked
                             (or the key is lying; ~5-10% per field)

STRICTLY DEV-SIDE. Key values must never reach runtime output: consuming the
hidden channel is the spec's penalized "filled in by injection" pattern (the
gated-snap design measured 74 gains / 1 poison on train and was still rejected
— row 80: the proximity gate admitted a value we PROVED absent from ink, and
the channel self-flags under manual code review). With --truth the report also
scores the key itself, keeping its per-field precision honest.

Usage:
  experiments/key_oracle.py [cache] --pred predictions.jsonl \
      [--truth train_labels.csv] [--out output/viz/key_oracle.jsonl]
"""
import argparse
import csv
import difflib
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mib import cache  # noqa: E402
from mib.packet import _INJECTION_RE as packet_inj_re  # noqa: E402

KEY_RE = re.compile(r"answer key.*?:\s*(MIB-\d{6},.*)", re.I)
FIELDS = ["applicant_name", "species_code", "home_world", "visa_class",
          "sponsor_id", "arrival_date", "declared_purpose", "risk_flags",
          "fee_status"]


def norm(field, v):
    v = (v or "").strip().lower()
    if field == "risk_flags":
        return "|".join(sorted(t for t in v.split("|") if t and t != "none")) or "none"
    return v


def packet_key(pages):
    """(key_values_by_field, normalized_hidden_lines) or (None, hidden)."""
    hidden = []
    key = None
    for p in pages:
        for line in p["hidden_lines"]:
            hidden.append(" ".join(line.lower().split()))
            m = KEY_RE.search(line)
            if m and key is None:
                parts = [x.strip() for x in m.group(1).split(",")]
                if len(parts) >= 11:
                    key = dict(zip(FIELDS, parts[1:10]))
    return key, hidden


def best_ink_sim(pages, target, hidden_norm):
    """Max similarity between the key value and any token window of any
    non-echo OCR line — how much honest ink supports the key's claim."""
    t = " ".join(target.lower().split())
    tw = len(t.split())
    best, best_line = 0.0, None
    for p in pages:
        for rd in p.get("reads") or []:
            for line in rd["lines"]:
                ln = " ".join(line.lower().split())
                if len(ln) < 3:
                    continue
                if any(ln in h or h in ln for h in hidden_norm if len(ln) >= 15):
                    continue  # the plant itself, not ink
                toks = ln.split()
                for w in (tw, tw + 1):
                    for i in range(max(1, len(toks) - w + 1)):
                        win = " ".join(toks[i:i + w])
                        r = difflib.SequenceMatcher(None, win, t).ratio()
                        if r > best:
                            best, best_line = r, line.strip()
    return best, best_line


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cache", nargs="?", default="output/cache/train_grid.jsonl")
    ap.add_argument("--pred", required=True)
    ap.add_argument("--truth", default=None)
    ap.add_argument("--out", default="output/viz/key_oracle.jsonl")
    args = ap.parse_args()

    preds = {}
    with open(args.pred) as f:
        for line in f:
            r = json.loads(line)
            preds[r["case_id"]] = r
    truth = {}
    if args.truth:
        with open(args.truth) as f:
            truth = {r["case_id"]: r for r in csv.DictReader(f)}

    meta, recs = cache.read(args.cache)
    out = open(ROOT / args.out, "w")
    out.write(json.dumps({"_meta": {"artifact": "key_oracle", "cache_meta": meta,
                                    "pred": args.pred}}) + "\n")
    n_keys, cls_counts = 0, Counter()
    key_score = Counter()
    rows = []
    for rec in recs:
        key, hidden = packet_key(rec["pages"])
        if not key or rec["stem"] not in preds:
            continue
        n_keys += 1
        ours = preds[rec["stem"]]
        t = truth.get(rec["stem"])
        for f in FIELDS:
            k, o = norm(f, key[f]), norm(f, ours.get(f))
            if t is not None:
                key_score[(f, k == norm(f, t[f]))] += 1
            if k == o:
                continue
            sim, line = best_ink_sim(rec["pages"], key[f], hidden)
            cls = ("both_printed" if sim >= 0.95
                   else "near" if sim >= 0.70 else "dead")
            # A match against an injection line is NOT honest-ink support —
            # the key value also lives inside the SYSTEM prompt, so a window of
            # it can score high. Flag it so the worklist isn't misread as
            # evidence (the echo filter drops whole-line matches, but a token
            # window of a longer injection can still slip through here).
            ink_inj = bool(line and packet_inj_re.search(line))
            row = {"case": rec["stem"], "field": f, "ours": ours.get(f),
                   "key": key[f], "ink_sim": round(sim, 3), "ink_line": line,
                   "ink_is_injection": ink_inj, "class": cls}
            if t is not None:
                row["truth"] = t[f]
                row["who_is_right"] = ("ours" if o == norm(f, t[f])
                                       else "key" if k == norm(f, t[f]) else "neither")
            cls_counts[cls] += 1
            rows.append(row)
            out.write(json.dumps(row) + "\n")
    out.close()

    print(f"{n_keys} key packets; {len(rows)} field disagreements: {dict(cls_counts)}")
    if truth:
        print("key per-field precision (truth mode):")
        for f in FIELDS:
            ok, bad = key_score[(f, True)], key_score[(f, False)]
            print(f"  {f:18s} {ok:4d}/{ok + bad}")
    print("\nboth_printed worklist (arbitration bugs — key value in honest ink):")
    for r in rows:
        if r["class"] == "both_printed":
            who = r.get("who_is_right", "?")
            print(f"  {r['case']} {r['field']:14s} ours={r['ours']!r:24s} "
                  f"key={r['key']!r:20s} right={who:7s} ink={r['ink_line']!r}"[:150])
    return 0


if __name__ == "__main__":
    sys.exit(main())
