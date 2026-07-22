FROM python:3.12-slim

WORKDIR /app

# System deps (OCR + PDF rendering) get added here as the pipeline grows, e.g.:
# RUN apt-get update && apt-get install -y --no-install-recommends \
#     tesseract-ocr poppler-utils \
#   && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY run.sh solution.py /app/
RUN chmod +x /app/run.sh

ENTRYPOINT ["/app/run.sh"]
