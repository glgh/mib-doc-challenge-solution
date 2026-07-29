#!/usr/bin/env python3
"""Pin the cross-case accumulator: feed the HEAVY block (ids 300-480) through one
process (that's what fills it), probe a fixed light case periodically, and when
the probe goes slow, cProfile it (what is being walked?) and report which object
TYPE grew most since start (gc census — catches C-level and hidden structures a
module-global scan misses)."""
import sys, time, gc, cProfile, pstats, io
from collections import Counter
from pathlib import Path
sys.path.insert(0, ".")
from mib import runner

TRAIN = Path("../mib-doc-challenge/data/train")
allpdf = sorted(TRAIN.glob("*.pdf"))
def cid(p): return int(p.stem.split("-")[1])
heavy = [p for p in allpdf if 300 <= cid(p) <= 480]
probe = TRAIN / "MIB-000441.pdf"
print(f"feeding {len(heavy)} heavy cases; probe=MIB-000441", flush=True)


def typecensus():
    c = Counter()
    for o in gc.get_objects():
        c[type(o).__name__] += 1
    return c


base = typecensus()
for i, pdf in enumerate(heavy, 1):
    if pdf.name == probe.name:
        continue
    runner.predict(pdf)
    if i % 20 == 0:
        t0 = time.perf_counter()
        runner.predict(probe)
        dt = (time.perf_counter() - t0) * 1000
        now = typecensus()
        grow = sorted(((now[k] - base.get(k, 0), k) for k in now), reverse=True)[:6]
        print(f"\nafter {i:3d} heavy cases: probe(441) = {dt:9.1f} ms   "
              f"(gc objects {sum(now.values())})", flush=True)
        print(f"    top type growth: {grow}", flush=True)
        if dt > 300:
            pr = cProfile.Profile()
            pr.enable(); runner.predict(probe); pr.disable()
            s = io.StringIO()
            pstats.Stats(pr, stream=s).sort_stats("tottime").print_stats(8)
            print("    --- cProfile of the SLOW probe (tottime) ---", flush=True)
            for ln in s.getvalue().splitlines()[4:16]:
                print("    " + ln, flush=True)
            break
print("done", flush=True)
