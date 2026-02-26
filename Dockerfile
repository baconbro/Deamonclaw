FROM python:3.12-slim

WORKDIR /app

# System dependencies for audio (pyaudio) and screen capture (scrot / tesseract)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    portaudio19-dev \
    scrot \
    tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY daemon_sidecar/ ./daemon_sidecar/
COPY daemon.yaml .

EXPOSE 8400

CMD ["python", "-m", "uvicorn", "daemon_sidecar.main:app", \
     "--host", "0.0.0.0", "--port", "8400", "--log-level", "info"]
