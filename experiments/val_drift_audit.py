#!/usr/bin/env python3
"""Pre-submission val-drift + escape audit (STATUS 5.6 + gain hunt).

Reads an existing val replay's predictions.jsonl (no scoring — val is unlabeled)
and reports the things dev cannot show: distribution drift, the row-39 DENIED
cell's density, the STALE_CUTOFF margin, closed-vocab escapes (the clean_approve
CFA route + invisible extraction misses), and revoked-sponsor fires.

Usage: .venv/bin/python experiments/val_drift_audit.py [pred.jsonl]
"""
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from mib import vocab, policy  # noqa: E402

PRED = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "output/replay_val_injcap/predictions.jsonl"
STALE = policy.STALE_CUTOFF
VISA = {"XW-1", "XW-2", "DIP-1", "MED-3", "TRANSIT-7"}
recs = [json.loads(l) for l in open(PRED)]
N = len(recs)
print(f"# val-drift audit  ({PRED.name}, N={N})\n")


def parse_d(s):
    try:
        return date.fromisoformat(s)
    except Exception:
        return None


# --- 1. adjudication distribution (train baseline from CLAUDE.md: NR 55.7 / DEN 34.8 / APP 9.5) ---
adj = Counter(r.get("adjudication") for r in recs)
print("## adjudication distribution  (train predicted: NR 55.7 / DEN 34.8 / APP 9.5)")
for k in ("NEEDS_REVIEW", "DENIED", "APPROVED"):
    print(f"  {k:14s} {adj.get(k,0):5d}  {100*adj.get(k,0)/N:5.1f}%")
print()

# --- 2. row-39 cell: fee unknown & arrival < STALE & visa != DIP-1  (dev 1.14%, old val 7.52%) ---
cell = [r for r in recs
        if r.get("fee_status") == "unknown"
        and (d := parse_d(r.get("arrival_date", ""))) and d < STALE
        and r.get("visa_class") != "DIP-1"]
cell_den = sum(1 for r in cell if r.get("adjudication") == "DENIED")
print(f"## row-39 reorder cell  (fee=unknown & arrival<{STALE} & visa!=DIP-1)")
print(f"  members: {len(cell):4d} = {100*len(cell)/N:.2f}% of val   (dev 1.14%, old-rev val 7.52%)")
print(f"  of those predicted DENIED: {cell_den} ({100*cell_den/max(1,len(cell)):.0f}% of cell)")
print()

# --- 3. STALE_CUTOFF margin: arrivals bracketing the cutoff ---
days = sorted((d - STALE).days for r in recs if (d := parse_d(r.get("arrival_date", ""))))
below = [x for x in days if x < 0]
print(f"## STALE_CUTOFF margin  (cutoff {STALE}; dev margin 37d, old-rev val 2d)")
if below:
    print(f"  arrivals before cutoff: {len(below)}   closest below: {below[-1]}d   "
          f"(nearest above: {min([x for x in days if x>=0], default='-')}d)")
print()

# --- 4. closed-vocab escapes: emitted value not in the mined enumeration ---
VSETS = {"home_world": set(vocab.HOME_WORLDS), "species_code": set(vocab.SPECIES),
         "declared_purpose": set(vocab.PURPOSES), "visa_class": VISA}
print("## closed-vocab escapes  (emitted value outside the train enumeration)")
for f, allowed in VSETS.items():
    esc = Counter(r.get(f) for r in recs
                  if r.get(f) and r.get(f) not in ("unknown", "none") and r.get(f) not in allowed)
    n = sum(esc.values())
    top = ", ".join(f"{v!r}×{c}" for v, c in esc.most_common(5))
    print(f"  {f:16s} escapes={n:4d}  {top}")
print()

# --- 5. revoked-sponsor fires + a new-sponsor-id scan ---
rev = [r for r in recs if r.get("sponsor_id") in vocab.REVOKED_SPONSORS]
revadj = Counter(r.get("adjudication") for r in rev)
print(f"## revoked-sponsor emissions  (ids: {sorted(vocab.REVOKED_SPONSORS)})")
print(f"  fires: {len(rev)}   adjudications: {dict(revadj)}")
