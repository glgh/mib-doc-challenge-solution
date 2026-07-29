#!/usr/bin/env python3
"""Confirm/refute the memory-pressure root cause WITHOUT a container run.

Feed the HEAVY block (ids 300-480) through ONE host process and, after each
case, record current RSS (ps) and peak RSS high-water (getrusage.ru_maxrss).
If a single worker's high-water plateaus near ~2 GB, then 4 concurrent workers
x that plateau ~= the 8 GB cgroup -> reclaim thrash. Correlate peak jumps with
page count so we can see WHICH cases drive the high-water.

macOS note: ru_maxrss is BYTES on darwin; `ps -o rss=` is KB. Both normalized
to MiB below.
"""
import sys, os, resource, subprocess, platform
from pathlib import Path
sys.path.insert(0, ".")
from mib import runner

MiB = 1024 * 1024
KB = 1024
_maxrss_is_bytes = platform.system() == "Darwin"  # Linux reports KB


def peak_mib():
    ru = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return (ru if _maxrss_is_bytes else ru * KB) / MiB


def rss_mib():
    out = subprocess.run(["ps", "-o", "rss=", "-p", str(os.getpid())],
                         capture_output=True, text=True).stdout.strip()
    return (int(out) * KB) / MiB if out else float("nan")


TRAIN = Path("../mib-doc-challenge/data/train")
allpdf = sorted(TRAIN.glob("*.pdf"))
def cid(p): return int(p.stem.split("-")[1])
heavy = [p for p in allpdf if 300 <= cid(p) <= 480]
print(f"feeding {len(heavy)} heavy cases through one process; "
      f"start peak={peak_mib():.0f} MiB rss={rss_mib():.0f} MiB", flush=True)

top = []  # (peak_jump, case, pages)
prev_peak = peak_mib()
for i, pdf in enumerate(heavy, 1):
    _, dbg = runner.predict(pdf)
    pk, rs = peak_mib(), rss_mib()
    jump = pk - prev_peak
    pages = dbg.get("n_pages")
    if jump > 20:
        top.append((jump, pdf.stem, pages))
    prev_peak = pk
    if i % 10 == 0 or jump > 50:
        marker = f"   <-- +{jump:.0f} MiB peak jump ({pages}p {pdf.stem})" if jump > 50 else ""
        print(f"  case {i:3d}: rss {rs:6.0f} MiB   peak(high-water) {pk:6.0f} MiB{marker}",
              flush=True)

print(f"\nfinal: peak high-water {peak_mib():.0f} MiB   current rss {rss_mib():.0f} MiB",
      flush=True)
print("x4 workers at peak high-water ~= "
      f"{4 * peak_mib() / 1024:.1f} GiB  (cgroup is 8 GiB)", flush=True)
print("\nbiggest peak jumps (MiB, case, pages):", flush=True)
for j, c, p in sorted(top, reverse=True)[:12]:
    print(f"    +{j:6.0f}  {c}  {p}p", flush=True)
