#!/usr/bin/env python3
"""Locate the per-case time sink on a monster case (docker gate tail).
Arms faulthandler to dump the stack every 15s (so a hang prints WHERE it is
stuck without waiting the full 20+ min), then runs runner.predict on one PDF
under cProfile. Usage: experiments/_profile_hang.py MIB-000441 [seconds]"""
import cProfile, faulthandler, os, pstats, sys, threading, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from mib import runner  # noqa: E402

case = sys.argv[1] if len(sys.argv) > 1 else "MIB-000441"
budget = int(sys.argv[2]) if len(sys.argv) > 2 else 80
pdf = ROOT.parent / "mib-doc-challenge" / "data" / "train" / f"{case}.pdf"
print(f"profiling {pdf} (watchdog {budget}s)", flush=True)

def _watchdog():
    time.sleep(budget)
    print(f"\n=== WATCHDOG: still running at {budget}s — stack above is the hang; "
          f"killing ===", flush=True)
    faulthandler.dump_traceback()
    os._exit(3)
threading.Thread(target=_watchdog, daemon=True).start()

faulthandler.dump_traceback_later(15, repeat=True)
pr = cProfile.Profile()
t0 = time.perf_counter()
pr.enable()
try:
    record, dbg = runner.predict(pdf)
finally:
    pr.disable()
    faulthandler.cancel_dump_traceback_later()
dt = time.perf_counter() - t0
print(f"\n=== {case} finished in {dt:.1f}s -> {record['adjudication']} ===", flush=True)
st = pstats.Stats(pr).sort_stats("cumulative")
st.print_stats(20)
st.sort_stats("tottime").print_stats(15)
