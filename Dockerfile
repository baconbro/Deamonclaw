FROM python:3.12-slim

WORKDIR /app

# ── System packages ────────────────────────────────────────────────────────────
# audio   : portaudio19-dev (pyaudio), libasound2
# screen  : tesseract-ocr (pytesseract OCR), xdotool (active window title)
# video   : libgl1 (opencv headless runtime)
# build   : gcc, libpq-dev (asyncpg)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    portaudio19-dev \
    libasound2 \
    tesseract-ocr \
    xdotool \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

# ── Python dependencies ────────────────────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download the sentence-transformer model during the build so the first
# container start doesn't need an internet connection at runtime.
RUN python -c "\
from sentence_transformers import SentenceTransformer; \
SentenceTransformer('all-MiniLM-L6-v2')" || true

# ── Application source ─────────────────────────────────────────────────────────
COPY daemon_sidecar/ ./daemon_sidecar/
COPY daemon.yaml .

EXPOSE 8400

CMD ["python", "-m", "uvicorn", "daemon_sidecar.main:app", \
     "--host", "0.0.0.0", "--port", "8400", "--log-level", "info"]
