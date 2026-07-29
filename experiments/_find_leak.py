#!/usr/bin/env python3
"""Localize the cross-case accumulation: feed DISTINCT cases through one process,
periodically re-time a fixed light probe (441) and dump the largest mutable
globals across mib modules. If probe time ramps while some global's len grows,
that global is the leak."""
import sys, time
from pathlib import Path
sys.path.insert(0, ".")
from mib import runner

TRAIN = Path("../mib-doc-challenge/data/train")
train = sorted(TRAIN.glob("*.pdf"))
probe = TRAIN / "MIB-000441.pdf"


def big_globals():
    big = []
    for name, mod in list(sys.modules.items()):
        if not name.startswith("mib"):
            continue
        for attr in vars(mod) if hasattr(mod, "__dict__") else []:
            if attr.startswith("__"):
                continue
            v = getattr(mod, attr, None)
            if isinstance(v, (dict, set, list)):
                try:
                    n = len(v)
                except TypeError:
                    continue
                if n > 100:
                    big.append((n, f"{name}.{attr}", type(v).__name__))
    big.sort(reverse=True)
    return big[:6]


for i, pdf in enumerate(train[:240], 1):
    if pdf.name == probe.name:
        continue
    runner.predict(pdf)
    if i % 40 == 0:
        t0 = time.perf_counter()
        runner.predict(probe)
        dt = (time.perf_counter() - t0) * 1000
        print(f"after {i:4d} distinct cases: probe(441)={dt:9.1f}ms", flush=True)
        for n, where, typ in big_globals():
            print(f"      {n:8d}  {where}  ({typ})", flush=True)
