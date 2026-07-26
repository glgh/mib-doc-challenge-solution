#!/usr/bin/env python3
"""The revoked-id-mutation instrument (Track 6 prerequisite; row 55's 578/590).

Restoration churn can mutate a sponsor-id misread until it lands on a REVOKED
id (`SPN-6187` -> `SPN-4040` under the local rung) — a hard-denial trigger
fabricated by repair, CFA-adjacent the day it happens to a truth-APPROVED
case. This counts the class instead of guessing: for every case whose emitted
sponsor_id is revoked, does that exact id appear in any RAW read (embedded/
render, un-restored) or the text layer, or only in restored variants?

Usage: experiments/revoked_churn.py [cache.jsonl] [predictions.jsonl]
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mib import cache, config  # noqa: E402
from mib.vocab import REVOKED_SPONSORS  # noqa: E402


def main(cache_path, pred_path):
    meta, records = cache.read(cache_path)
    preds = {}
    with open(pred_path) as f:
        for line in f:
            if not line.strip():
                continue
            d = json.loads(line)
            if "case_id" in d:
                preds[d["case_id"]] = d
    config.require_agreement([(str(cache_path), meta)])

    flagged = []
    for rec in records:
        stem = rec.get("stem")
        pred = preds.get(stem)
        if not pred or pred.get("sponsor_id") not in REVOKED_SPONSORS:
            continue
        sid = pred["sponsor_id"]
        raw_hit = restored_hit = text_hit = False
        for p in rec["pages"]:
            for src in ("visible_lines", "hidden_lines"):
                if any(sid in ln for ln in p.get(src) or []):
                    text_hit = True
            for rd in p.get("reads") or []:
                hit = any(sid in ln for ln in rd.get("lines") or [])
                if not hit:
                    continue
                # raw = the un-restored source read (no chain segments)
                if rd.get("variant") in ("embedded", "render"):
                    raw_hit = True
                else:
                    restored_hit = True
        prov = ("text" if text_hit else
                "raw-ocr" if raw_hit else
                "RESTORED-ONLY" if restored_hit else
                "NOWHERE (snap-fabricated?)")
        flagged.append((stem, sid, prov, pred.get("adjudication")))

    print(f"emitted revoked sponsor ids: {len(flagged)}")
    churn = [f for f in flagged if f[2] in ("RESTORED-ONLY", "NOWHERE (snap-fabricated?)")]
    for stem, sid, prov, adj in sorted(flagged):
        mark = "  <-- churn class" if prov.startswith(("RESTORED", "NOWHERE")) else ""
        print(f"  {stem}  {sid}  {prov:14}  adj={adj}{mark}")
    print(f"\nchurn class (revoked id visible ONLY via restoration): {len(churn)}")
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    sys.exit(main(args[0] if args else ROOT / f"output/cache/train_{config.RESTORE}.jsonl",
                  args[1] if len(args) > 1 else ROOT / "output/replay_votepage/predictions.jsonl"))
