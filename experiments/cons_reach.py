#!/usr/bin/env python3
"""Cross-reference the truth-DENIED conservative reviews against (a) the flags
reachability oracle and (b) the stamp/adjudicator channel in their debug, to
decide whether the deny evidence is SAFELY recoverable (visible, at-bar) or
organizer-intended-hard (hidden-only / OCR-destroyed)."""
import csv, json, sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CH = ROOT.parent / "mib-doc-challenge"
RD = Path("/tmp/p6_replay")
truth = {r["case_id"]: r for r in csv.DictReader(open(CH / "data/train_labels.csv"))}
pred = {json.loads(l)["case_id"]: json.loads(l) for l in open(RD / "predictions.jsonl")}
dbg  = {json.loads(l)["case_id"]: json.loads(l) for l in open(RD / "debug.jsonl")}
dev = set(json.loads((ROOT / "data_splits.json").read_text())["dev"])

# reachability detail from flags_oracle.jsonl (skip meta line)
reach = {}
for l in open(ROOT / "output/viz/flags_oracle.jsonl"):
    r = json.loads(l)
    if "case" not in r:
        continue
    reach[r["case"]] = {f: d["cls"] for f, d in r.get("missing", {}).items()}

cons_denied = [c for c in dev if c in pred and c in truth
               and pred[c]["adjudication"] == "NEEDS_REVIEW"
               and truth[c]["adjudication"] == "DENIED"]

print(f"truth-DENIED conservative reviews: {len(cons_denied)}\n")
cls_counter = Counter()
recoverable = []
for c in sorted(cons_denied):
    d = dbg[c]
    rr = reach.get(c, {})
    # strongest recoverability across this case's missing flags
    classes = set(rr.values())
    safe = classes & {"reachable", "value_blocked", "near_miss"}
    struck = d.get("struck")
    # does any read carry stamp/finding-ish evidence not yet acted on?
    doctypes = d.get("doc_types")
    tag = "SAFE-RECOVER" if safe else ("hidden/unreachable" if classes else "no-missing-flag")
    for v in (classes or {"(none)"}):
        cls_counter[v] += 1
    if safe:
        recoverable.append((c, rr))
    print(f"  {c}: missing_flag_classes={sorted(classes) or ['-']:} "
          f"struck={struck} doc_types={doctypes}")

print(f"\nmissing-flag class tally across the 39: {dict(cls_counter)}")
print(f"SAFELY recoverable (reachable/value_blocked/near_miss): {len(recoverable)}")
for c, rr in recoverable:
    print(f"  {c}: {rr}")
