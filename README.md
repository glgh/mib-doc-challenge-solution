# mib-doc-challenge-solution

Solution for the [MIB Doc Challenge](https://github.com/8090-inc/mib-doc-challenge): an offline document pipeline that reads a directory of case-packet PDFs and writes per-case field extractions plus an `APPROVED` / `DENIED` / `NEEDS_REVIEW` adjudication to `predictions.jsonl`.

## Run

```bash
docker build -t mib-submission .
docker run --rm --network none \
  --mount type=bind,src=/path/to/pdfs,dst=/input,readonly \
  --mount type=bind,src=/path/to/output,dst=/output \
  mib-submission /input /output/predictions.jsonl
```

## Layout

- `solution.py` — thin CLI; parallelism and output streaming only
- `mib/` — the pipeline, as staged transforms: `stages/extract` (PDF → page text, hidden spans quarantined) → `stages/render` (OCR of scan-only pages) → `parse` → `packet` (assemble + merge) → `policy` (adjudicate) → `emit` (schema safety net). `runner.py` sequences them, `records.py` defines what crosses each seam.
- `run.sh` — container entrypoint
- `Dockerfile` — offline runtime image (no network access at runtime)

`scripts/` and `experiments/` are offline analysis only — neither is imported by the pipeline, and neither reaches the container image. The per-case lists in `experiments/` (`hard_set.txt`, `hard_cases.jsonl`) are iteration sets for development: **no case list, case id, or file name is read at runtime.** The only literal case id in `mib/` is the unmatchable `MIB-000000` sentinel in `emit.py`; every other one appears in a comment.

The seam between `render` and `parse` is a cache boundary: OCR is ~95% of runtime, so `scripts/dump_text.py` materializes page text once and `scripts/replay.py` re-runs everything downstream in seconds.

## Docs

Start with [docs/STATUS.md](docs/STATUS.md) — where things are, and what has already been tried and rejected. Then [docs/experiments.md](docs/experiments.md) for the scored change log.

Skeleton derived from the challenge repo's MIT-licensed `Dockerfile.template` / `run.sh.template` / `examples/offline_baseline`.
