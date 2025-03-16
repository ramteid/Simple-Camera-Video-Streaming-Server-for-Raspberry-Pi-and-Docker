FROM python:3.9-bullseye

# System-Tools für Key-Import und Downloads installieren
RUN apt-get update && apt-get install -y --no-install-recommends \
    gnupg \
    dirmngr \
    curl \
    aria2 \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Raspberry Pi OS Repository und GPG-Key hinzufügen
RUN echo "deb http://archive.raspberrypi.org/debian bullseye main" >> /etc/apt/sources.list && \
    curl -sSL http://archive.raspberrypi.org/debian/raspberrypi.gpg.key | apt-key add - && \
    apt-get update && apt-get install -y --no-install-recommends \
        libcamera0 \
        python3-libcamera \
        python3-picamera2 \
        python3-pil \
        python3-numpy \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Install ARM-specific optimizations
RUN apt-get update && apt-get install -y --no-install-recommends \
    libjpeg-turbo-progs \
    python3-opencv \
    && rm -rf /var/lib/apt/lists/*

# Python-Pakete installieren, einschließlich FastAPI und Uvicorn
RUN pip install --no-cache-dir fastapi uvicorn psutil numpy

# Performance optimizations for Python
ENV PYTHONUNBUFFERED=1
ENV PYTHONFAULTHANDLER=1
ENV PYTHONOPTIMIZE=2
ENV OPENBLAS_NUM_THREADS=1
ENV MKL_NUM_THREADS=1

# Arbeitsverzeichnis festlegen
WORKDIR /app

# App-Skript kopieren
COPY app.py /app/

# PYTHONPATH setzen, damit Python die systemweiten Pakete findet
ENV PYTHONPATH="/usr/lib/python3/dist-packages"

# Uvicorn mit gevent Worker und erhöhtem Timeout starten
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8011", "--workers", "1", "--timeout-keep-alive", "300"]
