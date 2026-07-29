#!/usr/bin/env python3
"""Early verdict on the Tier-A Docker gate without waiting the full ~1h.

The gate streams per-case cost_ms to output/docker_gate/grid/debug.jsonl in
sorted-case-id order (parent imap). The original blowup was ids ~300-480 (mean
217s, max 1663s). Emit ONE verdict line and exit when any of:
  - a case exceeds 80s (tail present -> Tier A insufficient),
  - the stream stalls >150s on one case (imap head-of-line block = a live tail),
  - 520 cases stream cleanly (past the heavy region, no tail -> pass-so-far),
  - the gate prints its runtime summary (done).
"""
import json, os, sys, time

DBG = "output/docker_gate/grid/debug.jsonl"
GATE = sys.argv[1]
TAIL_MS = 80_000
PAST_HEAVY = 520
STALL_S = 150


def gate_done():
    try:
        return "runtime ==" in open(GATE).read()
    except OSError:
        return False


while not os.path.exists(DBG):
    if gate_done():
        print("gate finished before debug streamed", flush=True); sys.exit(0)
    time.sleep(5)

prev_n, prev_t = 0, time.time()
while True:
    try:
        c = [json.loads(l).get("cost_ms", 0) or 0 for l in open(DBG) if l.strip()]
    except OSError:
        c = []
    n = len(c)
    mx = int(max(c)) if c else 0
    tail = sum(1 for x in c if x > TAIL_MS)
    if tail:
        print(f"TAIL DETECTED after {n} cases: {tail} case(s) >80s, "
              f"max {mx/1000:.0f}s — Tier A INSUFFICIENT", flush=True); sys.exit(2)
    if n >= PAST_HEAVY:
        print(f"PASS-SO-FAR: {n} cases streamed through the heavy region, "
              f"max cost_ms {mx/1000:.1f}s (<80s), zero tail", flush=True); sys.exit(0)
    if gate_done():
        print(f"gate finished: {n} cases, max {mx/1000:.1f}s, tail(>80s)={tail}",
              flush=True); sys.exit(0)
    if n > prev_n:
        prev_n, prev_t = n, time.time()
    elif time.time() - prev_t > STALL_S:
        print(f"STALL: no new case for {STALL_S}s, stuck at case {n} "
              f"(imap head-of-line block = a live multi-minute tail) — "
              f"Tier A likely INSUFFICIENT", flush=True); sys.exit(3)
    time.sleep(15)
