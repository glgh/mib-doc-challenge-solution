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
# MIB_RESTORE is pinned rather than left to the default so the shipped config is
# visible in the image instead of inferred from code. `skew` is also the default
# in mib/config.py; deeper levels (turn, bands) score better but cost 8-10x the
# runtime, which does not fit the 30,000s budget until the detect-first rework.
ENV TMPDIR=/tmp \
    OMP_THREAD_LIMIT=1 \
    MIB_RESTORE=skew

ENTRYPOINT ["/app/run.sh"]
