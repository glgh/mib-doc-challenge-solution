#!/usr/bin/env python3
"""P1 — multi-binarization pushed past the +0 sweep, on the sponsor case set.

Baseline to beat = the shipped pipeline's emitted sponsor (Sauvola+autocontrast
in the full vote). A method 'recovers' a case when its cell read == truth."""
import _probe_util as U

# what the full pipeline actually emitted (the baseline read for each cell)
EMITTED = {"MIB-000784": "2283", "MIB-000395": "6148", "MIB-000594": "4887",
           "MIB-000554": "1388", "MIB-000870": "7581", "MIB-000008": "2813",
           "MIB-000667": "4297", "MIB-000714": "6384"}

# the binarization battery (grayscale -> ink mask)
METHODS = {
    "raw>127":        U.raw127,
    "otsu":           U.otsu,
    "otsu+open":      lambda g: U.opened(U.otsu(g)),
    "sauvola w25k.2": lambda g: U.sauvola(g, 25, 0.2),
    "sauvola w41k.2": lambda g: U.sauvola(g, 41, 0.2),
    "sauvola w41k.34":lambda g: U.sauvola(g, 41, 0.34),
    "sauvola w81k.25":lambda g: U.sauvola(g, 81, 0.25),
    "niblack w41":    lambda g: U.niblack(g, 41, -0.2),
    "wolf w41":       lambda g: U.wolf(g, 41, 0.5),
    "adapt-gauss":    lambda g: U.adaptive_gauss(g, 15, 8),
    "illum+otsu":     lambda g: U.illum_otsu(g, 25),
    "illum+otsu+open":lambda g: U.opened(U.illum_otsu(g, 25)),
}

cases = [(cid, tr) for kind, cid, tr in U.load_cases() if kind == "sponsor"]
print(f"P1 multi-binarization on {len(cases)} sponsor cells\n")
recovered_by = {}          # cid -> set of methods that recover
method_hits = {m: 0 for m in METHODS}
located = 0
for cid, tr in cases:
    truth = tr["sponsor_id"].replace("SPN-", "")
    g = U.sponsor_crop(cid)
    if g is None:
        print(f"{cid} truth {truth}  emitted {EMITTED.get(cid,'?')}  -> (cell not located)")
        continue
    located += 1
    reads = {m: U.sponsor_cell(fn(g)) for m, fn in METHODS.items()}
    hits = [m for m, v in reads.items() if v == truth]
    for m in hits:
        method_hits[m] += 1
    if hits:
        recovered_by[cid] = hits
    tag = f"RECOVERED by {hits}" if hits else "none recovered"
    print(f"{cid} truth {truth}  emitted {EMITTED.get(cid,'?')}  -> {tag}")
    for m, v in reads.items():
        mark = " <-- truth" if v == truth else ""
        print(f"      {m:16} {v or '-':6}{mark}")
    print()

print("== summary ==")
print(f"cells located: {located}/{len(cases)}")
newly = [cid for cid in recovered_by if EMITTED.get(cid) != recovered_by  # placeholder
         and EMITTED.get(cid) != cid]
newly = list(recovered_by.keys())   # every recovered case is one the pipeline got wrong
print(f"cells recovered by some binarization that the pipeline missed: {len(newly)}  {newly}")
print("method recovery counts:")
for m, n in sorted(method_hits.items(), key=lambda x: -x[1]):
    if n:
        print(f"  {m:16} {n}")
print(f"\nGO/NO-GO (P1): need >=3 newly-recovered -> {'GO' if len(newly)>=3 else 'NO-GO'} ({len(newly)})")
