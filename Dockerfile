FROM arm32v7/python:3.9-slim-bullseye

# Install required tools for key operations
# Mount caches from host to accelerate the build
RUN --mount=target=/var/lib/apt/lists,type=cache,sharing=locked \
    --mount=target=/var/cache/apt,type=cache,sharing=locked \
    apt update && apt install -y --no-install-recommends gnupg dirmngr curl

# Add Raspberry Pi OS repository and key
RUN echo "deb http://archive.raspberrypi.org/debian bullseye main" >> /etc/apt/sources.list && \
    curl -sSL http://archive.raspberrypi.org/debian/raspberrypi.gpg.key | apt-key add -

RUN --mount=target=/var/lib/apt/lists,type=cache,sharing=locked \
    --mount=target=/var/cache/apt,type=cache,sharing=locked \
    apt update && \
    apt install -y --no-install-recommends \
        python3-libcamera \
        python3-picamera2 \
        python3-pil \
        python3-numpy \
        fonts-dejavu \
    && rm -rf /var/lib/apt/lists/*

# enable Python to find the packages installed by apt
ENV PYTHONPATH="/usr/lib/python3/dist-packages"

# Install FastAPI and Uvicorn
RUN pip install --no-cache-dir fastapi uvicorn

WORKDIR /app
COPY app.py /app/

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8011", "--timeout-keep-alive", "0"]
