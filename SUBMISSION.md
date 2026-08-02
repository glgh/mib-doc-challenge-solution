# Submission — MIB Doc Challenge ("Intergalactic Intake")

**Public solution repo:** https://github.com/glgh/mib-doc-challenge-solution

That repo contains the `Dockerfile`, the full pipeline under `mib/`, the development tooling under `scripts/` and `experiments/`, and the complete change record under `docs/`.

## What this is

An offline document pipeline. It reads a directory of case-packet PDFs and writes one JSONL row per case with the twelve schema fields and an `APPROVED` / `DENIED` / `NEEDS_REVIEW` adjudication.

No LLM, no VLM, no multimodal model, no cloud OCR or document API, and no network access at runtime. The stack is PyMuPDF for text and layout, Tesseract for OCR, NumPy and Pillow for the scan-repair geometry, and a hand-written rules cascade for the adjudication. There are no learned model artifacts of any kind in the image — the only fitted numbers are a per-branch confidence table (16 floats plus a small cell-keyed refinement), fitted on the training split and checked into the repo as JSON.

## How to run it

```bash
docker build -t mib-submission .
docker run --rm --network none --cpus 4 --memory 8g --pids-limit 512 --read-only \
  --tmpfs /tmp:rw,nosuid,nodev,size=2g \
  --mount type=bind,src=/path/to/pdfs,dst=/input,readonly \
  --mount type=bind,src=/path/to/output,dst=/output \
  mib-submission /input /output/predictions.jsonl
```

The image is 577 MB uncompressed. It writes only to `/output` and to the `/tmp` tmpfs.

## Contract compliance

| Requirement | Status |
| --- | --- |
| No network at runtime | `--network none`; nothing in the image opens a socket |
| No LLM / VLM / cloud API | Tesseract + classical CV + rules only |
| Read-only root filesystem | Temp files go to `/tmp`, output to `/output` |
| Image ≤ 4 GiB | 577 MB |
| Model artifacts ≤ 250 MiB each, ≤ 1 GiB total | No model artifacts; the confidence table is ~2 KB of JSON |
| Runtime ≤ 6 s/PDF average, ≤ 30,000 s total | Measured on the full 5,000-case validation set under the exact contract limits: **4.72 s/PDF**, **23,606 s total** against the 30,000 s cap. Per-case compute mean 18.84 s, p50 17.83 s, p99 50.09 s, max 66.84 s (four workers run concurrently, so per-case cost is ~4× the wall-clock average). Validation packets run ~40% heavier than train ones, so the real headroom is **1.27×**, not the 1.5× the 1,000-case train gate suggested. See the note below on what that assumes about the hardware. |
| Predictions ≤ 25 MiB | Well under |
| No hardcoded answers or per-case lookups | No case list, case id or file name is read at runtime. The only literal id in `mib/` is the unmatchable `MIB-000000` sentinel in `emit.py`; every other one appears in a comment. `experiments/` and `scripts/` are offline analysis and are excluded from the build context. |
| Runs from a clean checkout | `docker build` from a fresh clone is the only step |

### A caveat on the runtime figure

The 23,606 s was measured with `--cpus 4` on an Apple-silicon host, and the contract fixes the vCPU *count* but never the vCPU *speed* — there is no reference machine in `DOCKER_SUBMISSION.md` or `EVALUATION.md`. On AWS and GCP a vCPU is normally one hardware thread, so "4 vCPU" on an SMT instance is two physical cores. Tesseract is compute-bound and cache-hungry, which is the bad case for SMT: two threads on one core contend for the same execution units. On such a host the effective parallelism is nearer 2.5 cores than 4, and 23,606 s could approach the 30,000 s limit.

The failure mode is graceful rather than catastrophic. The pipeline streams and flushes each row as it completes, so a container stopped at the limit leaves a valid, scoreable submission covering everything finished by then, rather than an empty file.

## Where to look first

- `MEMO.md` — the technical memo: approach, failure modes, what I would improve.
- `docs/STATUS.md` — the front page: current state, and what was tried and rejected.
- `docs/ALGORITHM.md` — how the pipeline works, stage by stage.
- `docs/experiments.md` — one scored row per change, including the rejections.

## Reproducing the numbers

```bash
# from the challenge repo root
python3 scripts/evaluate.py --truth data/train_labels.csv \
  --submission /path/to/predictions.jsonl
```

Train, on the frozen 700/300 split (seed 8090, `data_splits.json`): dev 128.15 / 150, holdout 128.52 / 150, 0 catastrophic false approvals, 0 missing rows.
