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
import csv
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


# --- resume -----------------------------------------------------------------
#
# A 5,000-case run is ~6 hours, and losing it to an infrastructure fault (Docker
# Desktop's VM died at case 4,260 on 2026-08-01) means paying for the whole
# corpus again. Resume re-predicts only the missing case ids and merges.
#
# This lives HERE and not in solution.py on purpose. solution.py is the shipped
# entrypoint; the graders run it once against an empty /output, where resume can
# never apply, so putting merge logic on the contract path would be pure risk.
#
# Correctness rests on one property, established in experiments.md row 97 (the
# same one that let worker recycling ship as output-invariant): a case is a pure
# function of its PDF, with no cross-case worker state. The single exception is
# the corpus-level pass `corpus.revise`, which counts sponsor-id recurrence over
# the whole corpus — so it is re-applied over the MERGED set here, exactly as
# solution.py applies it over a complete run.


def _read_jsonl(path):
    return [json.loads(line) for line in Path(path).open() if line.strip()]


def load_partial(out_dir):
    """(records, debugs) from a partial run, or (None, None) if there is none.

    Refuses a partial it cannot merge safely: the debug sidecar must exist and
    align 1:1 with predictions, because `corpus.revise` consults each case's
    branch to honour precedence (a case already decided by a higher-ranked
    branch must not be revised). Without branches the merge would silently
    over-deny.
    """
    preds, debug = out_dir / "predictions.jsonl", out_dir / "debug.jsonl"
    if not preds.exists() or preds.stat().st_size == 0:
        return None, None
    records = _read_jsonl(preds)
    if not debug.exists():
        raise SystemExit(
            f"{preds} holds {len(records)} rows but {debug} is missing.\n"
            f"Resume needs the sidecar: corpus.revise reads each case's branch to\n"
            f"respect precedence. Re-run without --resume.")
    debugs = _read_jsonl(debug)
    ids_p = [r["case_id"] for r in records]
    ids_d = [d.get("case_id") for d in debugs]
    if ids_p != ids_d:
        raise SystemExit(
            f"predictions ({len(ids_p)}) and debug ({len(ids_d)}) disagree — "
            f"cannot merge. Re-run without --resume.")
    if len(set(ids_p)) != len(ids_p):
        raise SystemExit("partial predictions contain duplicate case ids.")
    return records, debugs


def stage_missing(input_dir, done_ids):
    """Copy only the PDFs whose case id is not already predicted. Same contract as
    stage_input: copy, never symlink — the mount is read-only inside."""
    pdfs = [p for p in sorted(Path(input_dir).glob("*.pdf")) if p.stem not in done_ids]
    if not pdfs:
        return None, (lambda: None), 0
    staged = Path(tempfile.mkdtemp(prefix="mib_resume_in_"))
    for pdf in pdfs:
        shutil.copy2(pdf, staged / pdf.name)
    return staged, (lambda: shutil.rmtree(staged, ignore_errors=True)), len(pdfs)


def merge_and_revise(base, new, out_dir):
    """Merge (records, debugs) pairs, re-apply the corpus pass, write the result.

    The subset run applied `corpus.revise` to its own slice, whose recurrence
    spectrum is not the corpus's. That is only harmful if it actually revised
    something — it acts solely on ids absent from vocab.REVOKED_SPONSORS — so we
    assert the no-op rather than assume it.
    """
    from mib import corpus                                       # noqa: PLC0415

    (b_rec, b_dbg), (n_rec, n_dbg) = base, new
    tainted = [d["case_id"] for d in n_dbg if d.get("sponsor_source") == "recurring"]
    if tainted:
        raise SystemExit(
            f"the resumed slice's corpus pass revised {len(tainted)} case(s) "
            f"({tainted[:5]}...) off a partial spectrum. Merging would not "
            f"reproduce a single run — re-run the whole corpus without --resume.")

    records = sorted(b_rec + n_rec, key=lambda r: r["case_id"])
    debugs = sorted(b_dbg + n_dbg, key=lambda d: d["case_id"])
    ids = [r["case_id"] for r in records]
    if len(set(ids)) != len(ids):
        raise SystemExit("merge produced duplicate case ids — refusing to write.")

    new_ids, revised = corpus.revise(records, debugs)
    print(f"\n== merge ==")
    print(f"  carried over: {len(b_rec)}   newly predicted: {len(n_rec)}   "
          f"total: {len(records)}")
    print(f"  corpus pass over the merged set: "
          f"{f'{revised} case(s) revised, new ids {sorted(new_ids)}' if revised else 'no-op'}")

    (out_dir / "predictions.jsonl").write_text(
        "".join(json.dumps(r, sort_keys=True) + "\n" for r in records))
    (out_dir / "debug.jsonl").write_text(
        "".join(json.dumps(d, sort_keys=True) + "\n" for d in debugs))
    return records


def check_against_manifest(out_dir, manifest):
    """The submission must cover exactly the manifest's case ids."""
    if not Path(manifest).exists():
        print(f"\n== manifest ==\n  WARNING: {manifest} not found — skipped.")
        return
    with open(manifest, newline="") as fh:
        want = {r["case_id"] for r in csv.DictReader(fh)}
    got = {r["case_id"] for r in _read_jsonl(out_dir / "predictions.jsonl")}
    print("\n== manifest ==")
    print(f"  manifest {len(want)}   predicted {len(got)}   "
          f"missing {len(want - got)}   unexpected {len(got - want)}")
    if want != got:
        raise SystemExit("  case id set does not match the manifest.")
    print("  OK: exact coverage.")


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
    ap.add_argument("--resume", action="store_true",
                    help="continue a partial run in --out: predict only the case ids "
                         "missing from its predictions.jsonl, then merge and re-apply "
                         "the corpus pass over the complete set")
    ap.add_argument("--manifest", default=None,
                    help="csv with a case_id column; assert exact coverage after a run")
    args = ap.parse_args()

    assert_vm_idle()
    if not args.no_build:
        build_image()
    size = image_size_gib()
    if size is not None:
        print(f"image: {size:.2f} GiB (cap 4 GiB)   {'OK' if size <= 4 else 'OVER'}")

    out_dir = Path(args.out) / RESTORE
    base_rec, base_dbg = (load_partial(out_dir) if args.resume else (None, None))

    if base_rec is None:
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
    else:
        # Resume. The subset runs into its OWN directory: solution.py opens the
        # output with "w", so pointing the container at the partial file would
        # truncate exactly the work we are trying to keep.
        done = {r["case_id"] for r in base_rec}
        print(f"\n== resume ==\n  {len(done)} case(s) already predicted in {out_dir}")
        mount_in, cleanup, n_pdfs = stage_missing(args.input, done)
        if n_pdfs == 0:
            print("  nothing missing — merging and re-applying the corpus pass only.")
            merge_and_revise((base_rec, base_dbg), ([], []), out_dir)
            wall = 0.0
        else:
            print(f"  {n_pdfs} case(s) still to predict")
            sub_dir = Path(tempfile.mkdtemp(prefix="mib_resume_out_"))
            try:
                wall = run_container(mount_in.resolve(), sub_dir.resolve())
                check_stamp(sub_dir)
                report_runtime(sub_dir, n_pdfs, wall)
                new_rec, new_dbg = load_partial(sub_dir)
                merge_and_revise((base_rec, base_dbg), (new_rec or [], new_dbg or []),
                                 out_dir)
                shutil.copy2(sub_dir / "meta.json", out_dir / "meta.json")
            finally:
                cleanup()
                shutil.rmtree(sub_dir, ignore_errors=True)

    if args.manifest:
        check_against_manifest(out_dir, args.manifest)
    if args.reference:
        check_parity(out_dir, args.reference)

    print(f"\noutput in {out_dir}")
    if not args.keep_output:
        print("  (pass --keep-output to retain predictions/debug/meta)")


if __name__ == "__main__":
    main()
