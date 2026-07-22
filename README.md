# mib-doc-challenge-solution

Solution for the [MIB Doc Challenge](https://github.com/8090-inc/mib-doc-challenge): an offline
document pipeline that reads a directory of case-packet PDFs and writes per-case field
extractions plus an `APPROVED` / `DENIED` / `NEEDS_REVIEW` adjudication to `predictions.jsonl`.

## Run

```bash
docker build -t mib-submission .
docker run --rm --network none \
  --mount type=bind,src=/path/to/pdfs,dst=/input,readonly \
  --mount type=bind,src=/path/to/output,dst=/output \
  mib-submission /input /output/predictions.jsonl
```

## Layout

- `solution.py` — the pipeline (extraction + adjudication rules)
- `run.sh` — container entrypoint
- `Dockerfile` — offline runtime image (no network access at runtime)

Skeleton derived from the challenge repo's MIT-licensed `Dockerfile.template` /
`run.sh.template` / `examples/offline_baseline`.
