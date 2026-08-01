# Submission — MIB Doc Challenge ("Intergalactic Intake")

**Public solution repo:** `<FILL IN: https://github.com/<your-github-username>/mib-doc-challenge-solution>`

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
| Runtime ≤ 6 s/PDF average, ≤ 30,000 s total | 1,000-case gate under the exact contract limits: per-case p50 13.4 s, p99 49.9 s, max 66.0 s inside the 4-vCPU container, projecting ~19,000 s wall for 5,000 PDFs — **≈3.8 s/PDF average** against the 6 s budget. `<UPDATE with the measured 5,000-case wall clock>` |
| Predictions ≤ 25 MiB | Well under |
| No hardcoded answers or per-case lookups | No case list, case id or file name is read at runtime. The only literal id in `mib/` is the unmatchable `MIB-000000` sentinel in `emit.py`; every other one appears in a comment. `experiments/` and `scripts/` are offline analysis and are excluded from the build context. |
| Runs from a clean checkout | `docker build` from a fresh clone is the only step |

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
