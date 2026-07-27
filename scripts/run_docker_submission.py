#!/usr/bin/env python3
"""Build and run the submission container under the EXACT contract limits, then
report runtime against the budget. This is the only figure that decides whether a
config (OCR recipe, image contents) can ship — every other timing
in this repo is a contended-laptop number (see docs/experiments.md, the struck-out
wall columns). It is the measuring instrument the OCR-engine swing is judged by.

The contract (CLAUDE.md / DOCKER_SUBMISSION.md) runs us as:

    docker run --rm --network none --cpus 4 --memory 8g --pids-limit 512 \
      --read-only --tmpfs /tmp:rw,nosuid,nodev,size=2g \
      ... <image> /input /output/predictions.jsonl

Budgets: 6 s/PDF average, hard total 30,000 s for the 5,000-PDF validation set
(a container still running at 30,000 s is stopped and scored on partial output).

What this adds over a bare `docker run`:

- Times build and run separately (build time is not per-PDF).
- Refuses to time unless the Docker VM is otherwise idle — Docker Desktop's VM
  fits exactly one contract-sized container, so a second running container
  invalidates the CPU/memory isolation the measurement depends on.
- Points MIB_DEBUG_JSONL at the writable /output mount so per-case `cost_ms`
  (stamped in solution.py) survives the --rm container and we can report the
  heavy tail (p99/max), not just the mean. The real submission sets no sidecar.
- Reads the /output/meta.json provenance stamp and asserts the container ran the
  config we asked for.
- Optional parity: diff the container predictions against a --reference file.
  NOTE this reports the diff rather than asserting byte-identity: read_case OCR is
  documented as mildly non-deterministic (~0.01 pts across runs, docs/experiments.md
  row 18 note), so two independent full-pipeline runs are not expected to match to
  the byte. A near-zero adjudication diff is the pass; a large one means the
  container is running a different pipeline than we score on the host.

Usage:
    scripts/run_docker_submission.py [--input DIR] [--limit N]
                                     [--reference PRED.jsonl]
                                     [--keep-output] [--no-build]

Defaults: --input ../mib-doc-challenge/data/train. --limit stages the first N PDFs into a temp dir for a fast smoke test;
omit it for the real full-corpus baseline.
"""
import argparse
import json
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
IMAGE = "mib-submission"
DEFAULT_INPUT = ROOT / ".." / "mib-doc-challenge" / "data" / "train"

# The restoration level is fixed in code now, not passed in. Imported rather than
# repeated so the stamp assertion below cannot drift from what the image builds.
# mib.config is stdlib-only, so this stays importable outside .venv.
from mib.config import RESTORE  # noqa: E402

PER_PDF_BUDGET_S = 6.0          # contract average
VALIDATION_N = 5000             # the private validation set size
TOTAL_BUDGET_S = 30_000         # hard wall for the 5,000-PDF set


def _run(cmd, **kw):
    """Run a subprocess, echoing the command so the exact invocation is on the
    record (a runtime measurement is only reproducible if the flags are visible)."""
    print("+ " + " ".join(str(c) for c in cmd), flush=True)
    return subprocess.run(cmd, **kw)


def assert_vm_idle():
    """The Docker Desktop VM holds one contract-sized container's worth of CPU and
    RAM; a second running container steals from the isolation we are measuring. So
    a timing run requires an otherwise-empty VM. Refuse rather than record a number
    contended by another container (the exact mistake the laptop figures made)."""
    res = _run(["docker", "ps", "-q"], capture_output=True, text=True)
    running = [c for c in res.stdout.split() if c]
    if running:
        names = _run(["docker", "ps", "--format", "{{.Names}} ({{.Image}})"],
                     capture_output=True, text=True).stdout.strip()
        raise SystemExit(
            f"\nrefusing to time: {len(running)} container(s) already running — the "
            f"VM fits one contract container, so this would contend the measurement.\n"
            f"stop them first:\n{names}\n")


def build_image():
    t0 = time.perf_counter()
    res = _run(["docker", "build", "-t", IMAGE, str(ROOT)])
    if res.returncode:
        raise SystemExit("docker build failed")
    print(f"\nbuild: {time.perf_counter() - t0:.0f}s", flush=True)


def image_size_gib():
    res = _run(["docker", "image", "inspect", IMAGE, "--format", "{{.Size}}"],
               capture_output=True, text=True)
    try:
        return int(res.stdout.strip()) / 1024**3
    except ValueError:
        return None


def stage_input(input_dir, limit):
    """Return (dir_to_mount, cleanup_fn). With --limit, copy the first N PDFs into
    a temp dir so a smoke test doesn't run the whole corpus; the container mounts
    input read-only, so symlinks pointing outside the mount would not resolve."""
    pdfs = sorted(Path(input_dir).glob("*.pdf"))
    if not pdfs:
        raise SystemExit(f"no PDFs under {input_dir}")
    if limit is None or limit >= len(pdfs):
        return Path(input_dir), (lambda: None), len(pdfs)
    staged = Path(tempfile.mkdtemp(prefix="mib_gate_in_"))
    for pdf in pdfs[:limit]:
        shutil.copy2(pdf, staged / pdf.name)
    return staged, (lambda: shutil.rmtree(staged, ignore_errors=True)), limit


def run_container(mount_in, mount_out):
    """The contract flags verbatim, plus a sidecar env for our own timing. Returns
    wall seconds around the container (the figure the contract's 6 s/PDF measures:
    it includes container start, the pool, and the streamed writes)."""
    cmd = [
        "docker", "run", "--rm",
        "--network", "none",
        "--cpus", "4",
        "--memory", "8g",
        "--pids-limit", "512",
        "--read-only",
        "--tmpfs", "/tmp:rw,nosuid,nodev,size=2g",
        "--mount", f"type=bind,src={mount_in},dst=/input,readonly",
        "--mount", f"type=bind,src={mount_out},dst=/output",
        # Our instrument only: the sidecar rides the writable /output mount so
        # per-case cost_ms survives --rm. The scored submission sets nothing here.
        "-e", "MIB_DEBUG_JSONL=/output/debug.jsonl",
        IMAGE, "/input", "/output/predictions.jsonl",
    ]
    t0 = time.perf_counter()
    res = _run(cmd)
    wall = time.perf_counter() - t0
    if res.returncode:
        raise SystemExit(f"container exited {res.returncode} after {wall:.0f}s")
    return wall


def _pct(values, q):
    """Nearest-rank percentile without numpy (this script must run outside .venv)."""
    if not values:
        return float("nan")
    s = sorted(values)
    i = min(len(s) - 1, int(round(q / 100 * (len(s) - 1))))
    return s[i]


def report_runtime(out_dir, n_pdfs, wall):
    preds = out_dir / "predictions.jsonl"
    n_rows = sum(1 for _ in preds.open()) if preds.exists() else 0

    costs = []
    debug = out_dir / "debug.jsonl"
    if debug.exists():
        for line in debug.open():
            try:
                costs.append(json.loads(line)["cost_ms"] / 1000.0)
            except (json.JSONDecodeError, KeyError):
                pass

    per_pdf = wall / n_pdfs if n_pdfs else float("nan")
    projected = per_pdf * VALIDATION_N

    print("\n== runtime ==")
    print(f"  PDFs in:              {n_pdfs}")
    print(f"  prediction rows out:  {n_rows}")
    print(f"  container wall:        {wall:.0f}s")
    print(f"  end-to-end per PDF:    {per_pdf:.2f}s   (budget {PER_PDF_BUDGET_S:.0f}s)"
          f"   {'OK' if per_pdf <= PER_PDF_BUDGET_S else 'OVER'}")
    if costs:
        print(f"  per-case compute (cost_ms, {len(costs)} cases):")
        print(f"    mean {statistics.mean(costs):.2f}s  p50 {_pct(costs,50):.2f}s  "
              f"p90 {_pct(costs,90):.2f}s  p99 {_pct(costs,99):.2f}s  "
              f"max {max(costs):.2f}s")
    print(f"  projected {VALIDATION_N}-PDF total: {projected/3600:.2f}h "
          f"({projected:.0f}s)   (budget {TOTAL_BUDGET_S/3600:.1f}h / {TOTAL_BUDGET_S}s)"
          f"   {'OK' if projected <= TOTAL_BUDGET_S else 'OVER'}")
    return n_rows


def check_stamp(out_dir):
    meta_path = out_dir / "meta.json"
    print("\n== provenance ==")
    if not meta_path.exists():
        print("  WARNING: no meta.json — cannot confirm the container's config.")
        return
    meta = json.loads(meta_path.read_text())
    print(f"  container ran: restore={meta.get('restore')} "
          f"rev={meta.get('git_rev')}{'+dirty' if meta.get('git_dirty') else ''}")
    if meta.get("restore") != RESTORE:
        raise SystemExit(
            f"  MISMATCH: asked for restore={RESTORE!r} but the container stamped "
            f"{meta.get('restore')!r}. The image is probably stale — "
            f"rebuild (drop --no-build).")
    print("  OK: container config matches the requested config.")


def check_parity(out_dir, reference):
    """Report how far the container output drifts from a reference run. This is a
    diff, not an assertion: read_case OCR is mildly non-deterministic, so two full
    runs are not byte-identical. A near-zero adjudication diff is the pass."""
    ref_path = Path(reference)
    if not ref_path.exists():
        print(f"\n== parity ==\n  WARNING: reference {ref_path} not found — skipped.")
        return
    def load(p):
        rows = {}
        for line in Path(p).open():
            line = line.strip()
            if line:
                r = json.loads(line)
                rows[r["case_id"]] = r
        return rows
    cont = load(out_dir / "predictions.jsonl")
    ref = load(ref_path)

    print("\n== parity vs reference ==")
    only_c = cont.keys() - ref.keys()
    only_r = ref.keys() - cont.keys()
    shared = cont.keys() & ref.keys()
    field_diffs, adj_diffs = {}, 0
    for cid in shared:
        for k in set(cont[cid]) | set(ref[cid]):
            if cont[cid].get(k) != ref[cid].get(k):
                field_diffs[k] = field_diffs.get(k, 0) + 1
                if k == "adjudication":
                    adj_diffs += 1
    print(f"  reference: {ref_path}")
    print(f"  case_ids only in container: {len(only_c)}   only in reference: {len(only_r)}")
    print(f"  shared cases: {len(shared)}   adjudication diffs: {adj_diffs}")
    if field_diffs:
        for k, n in sorted(field_diffs.items(), key=lambda kv: -kv[1]):
            print(f"    {k:16s} differs on {n} case(s)")
    if only_c or only_r:
        print("  WARN: case_id sets differ — the container saw a different input set.")
    elif adj_diffs == 0 and not field_diffs:
        print("  PASS: byte-identical to the reference.")
    elif adj_diffs == 0:
        print("  NOTE: fields drift but every adjudication agrees — within OCR jitter.")
    else:
        frac = adj_diffs / max(1, len(shared))
        print(f"  {'NOTE' if frac < 0.005 else 'WARN'}: {frac:.2%} of adjudications "
              f"differ ({'within' if frac < 0.005 else 'ABOVE'} documented OCR jitter).")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", default=str(DEFAULT_INPUT))
    ap.add_argument("--limit", type=int, default=None,
                    help="stage only the first N PDFs (fast smoke test)")
    ap.add_argument("--reference", default=None,
                    help="predictions.jsonl to diff the container output against")
    ap.add_argument("--out", default=str(ROOT / "output" / "docker_gate"))
    ap.add_argument("--no-build", action="store_true", help="reuse the existing image")
    ap.add_argument("--keep-output", action="store_true")
    args = ap.parse_args()

    assert_vm_idle()
    if not args.no_build:
        build_image()
    size = image_size_gib()
    if size is not None:
        print(f"image: {size:.2f} GiB (cap 4 GiB)   {'OK' if size <= 4 else 'OVER'}")

    out_dir = Path(args.out) / RESTORE
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    mount_in, cleanup, n_pdfs = stage_input(args.input, args.limit)
    try:
        wall = run_container(mount_in.resolve(), out_dir.resolve())
    finally:
        cleanup()

    check_stamp(out_dir)
    report_runtime(out_dir, n_pdfs, wall)
    if args.reference:
        check_parity(out_dir, args.reference)

    print(f"\noutput in {out_dir}")
    if not args.keep_output:
        print("  (pass --keep-output to retain predictions/debug/meta)")


if __name__ == "__main__":
    main()
