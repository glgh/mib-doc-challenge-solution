FROM python:3.12-slim

WORKDIR /app

# Tesseract is a hard dependency, not an optional extra: ~25% of packets carry
# their visible content only as pixels, and mib/stages/render.py shells out to it.
RUN apt-get update && apt-get install -y --no-install-recommends \
      tesseract-ocr tesseract-ocr-eng \
  && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY run.sh solution.py /app/
COPY mib /app/mib
RUN chmod +x /app/run.sh

# The contract runs us with a read-only root and a writable /tmp only.
#
# The scored submission is invoked with no `-e` at all, so the shipped recipe is
# whatever mib/config.py resolves by default — this image pins nothing that
# selects it. That recipe is GRID_PRESETS["grid"]: S2 enumerates a composition
# grid (source -> orientation -> deskew -> deshred/local -> optical) with
# geom=(skew,turn1,turn3,deshred,local), opt=(adapt,autocon) over corrected
# frames, a PSM-3 layout pass on truncated field labels, and a >=200-DPI render
# floor. Every read crosses the S2/S3 seam; a plurality vote settles the rest.
# The MIB_* env knobs exist for A/B runs only and all stamp themselves into the
# `restore` provenance field, so a run that used one cannot be mistaken for a
# shipped run. OCR is a single PSM 11 pass per image: the PSM 3+11 dual pass
# measured +0.87 dev at CFA 0 but put its cost in a heavy tail that tripped the
# per-case budget, so only the gated layout-pass tier survives.
#
# glibc allocator containment. Measured (experiments/_rss_probe.py): one worker's
# RSS high-water ratchets to ~2.2 GiB over a run of heavy OCR pages and is never
# returned (current RSS == high-water at every sample), so 4 workers sum to
# ~8.7 GiB > the 8 GiB cgroup and thrash whenever a heavy-page cluster overlaps
# their high-RSS windows (multi-minute per-case stalls, then recovery). MALLOC_
# TRIM_THRESHOLD_ pins the trim threshold low so freed OCR frame arrays return to
# the OS instead of ratcheting the arena; MALLOC_ARENA_MAX=2 caps per-process
# arenas (4 single-threaded workers gain nothing from the default 8*ncpu arenas
# but pay their fragmentation). Paired with maxtasksperchild in solution.py.
# Output is byte-identical — this is resident-memory hygiene, not a recipe knob.
ENV TMPDIR=/tmp \
    OMP_THREAD_LIMIT=1 \
    MALLOC_ARENA_MAX=2 \
    MALLOC_TRIM_THRESHOLD_=131072

ENTRYPOINT ["/app/run.sh"]
