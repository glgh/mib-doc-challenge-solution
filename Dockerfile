FROM python:3.12-slim

WORKDIR /app

# Tesseract is a hard dependency, not an optional extra: ~25% of packets carry
# their visible content only as pixels, and mib/ocr.py shells out to it.
RUN apt-get update && apt-get install -y --no-install-recommends \
      tesseract-ocr tesseract-ocr-eng \
  && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY run.sh solution.py /app/
COPY mib /app/mib
RUN chmod +x /app/run.sh

# The contract runs us with a read-only root and a writable /tmp only.
# Scan restoration is no longer configurable: S2 always runs the full ladder
# (deskew + quarter-turn + shred-band realignment), fixed in mib/config.py. Note
# the scored submission is invoked with no `-e` at all, so anything pinned here
# is documentation for us, never a lever the grader can pull.
# MIB_OCR_PASSES is pinned so the shipped OCR recipe is visible in the image
# rather than inferred from a code default. `dual` (adding a PSM 3 pass per
# image) measured +0.87 dev at CFA 0, but its cost is concentrated in a heavy
# tail that tripped the 120s per-case budget and did not survive a
# contract-limits timing run — so it stays off until that is resolved.
ENV TMPDIR=/tmp \
    OMP_THREAD_LIMIT=1 \
    MIB_OCR_PASSES=psm11

ENTRYPOINT ["/app/run.sh"]
