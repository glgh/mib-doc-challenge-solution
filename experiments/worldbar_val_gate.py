#!/usr/bin/env python3
"""world_bar 0.70->0.65 val gate: does lowering the home_world snap bar admit any
WRONG embargo-world snaps on the 5,000 unlabeled val cases? (dev showed +0.10 via
one truth-matching recovery, MIB-000013; val is the transfer check.)

Val is unlabeled, so the gate is: for every case whose emitted home_world NEWLY
becomes a vocab world at 0.65, report whether it is an EMBARGO world and whether
the adjudication flips to DENIED — and print the raw source line so a human can
judge whether the snap is a plausible near-miss or a forced junk snap.

Reuses parsed pages across both bar values (one cache load).
"""
import json
import sys
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from mib import cache, runner, emit, vocab, policy  # noqa: E402

CACHE = ROOT / "output" / "cache" / "val_text_latest.jsonl"
EMBARGO = set(getattr(policy, "FULL_EMBARGO_WORLDS", set())) | \
          set(getattr(policy, "PARTIAL_EMBARGO_WORLDS", set()))
WORLDS = set(vocab.HOME_WORLDS)

_meta, records = cache.read(CACHE)
CASES = []
for rec in records:
    if rec.get("error"):
        continue
    pages, ocr_lines = cache.to_case(rec["pages"])
    CASES.append((rec["stem"], pages, ocr_lines))
print(f"loaded {len(CASES)} val cases; embargo worlds tracked: {sorted(EMBARGO)}\n")


def run():
    out = {}
    for stem, pages, ocr_lines in CASES:
        record, _ = runner.predict_from_evidence(pages, ocr_lines, stem)
        out[record["case_id"]] = record
    return out


base = run()
old = vocab._SNAP_BARS["home_world"]
vocab._SNAP_BARS["home_world"] = 0.65
try:
    lowered = run()
finally:
    vocab._SNAP_BARS["home_world"] = old

# --- diff ---
new_snaps = []          # home_world changed to a vocab world at 0.65
adj_flips = []          # adjudication changed
for c in base:
    b, n = base[c], lowered[c]
    if b["home_world"] != n["home_world"] and n["home_world"] in WORLDS:
        new_snaps.append((c, b["home_world"], n["home_world"]))
    if b["adjudication"] != n["adjudication"]:
        adj_flips.append((c, b["adjudication"], n["adjudication"],
                          b["home_world"], n["home_world"]))

emb_snaps = [x for x in new_snaps if x[2] in EMBARGO]
print(f"home_world newly snapped to a vocab world: {len(new_snaps)}")
print(f"  of those into an EMBARGO world:          {len(emb_snaps)}")
for c, o, w in emb_snaps:
    print(f"    {c}: {o!r} -> {w!r}")
print(f"\nadjudication flips (any direction):        {len(adj_flips)}")
print(f"  direction: {Counter((a,b) for _,a,b,_,_ in adj_flips)}")
for c, a, b, ow, nw in adj_flips:
    emb = " EMBARGO" if nw in EMBARGO else ""
    print(f"    {c}: {a} -> {b}   world {ow!r} -> {nw!r}{emb}")
