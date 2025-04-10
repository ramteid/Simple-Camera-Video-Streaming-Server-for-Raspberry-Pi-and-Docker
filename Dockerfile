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
        python3-gevent \
        python3-flask \
        python3-gunicorn \
        fonts-dejavu \
    && rm -rf /var/lib/apt/lists/*

# enable Python to find the packages installed by apt
ENV PYTHONPATH="/usr/lib/python3/dist-packages"

WORKDIR /app
COPY app.py /app/

CMD ["python", "-m", "gunicorn", "-b", "0.0.0.0:8011", "--workers", "1", "--worker-class", "gevent", "app:app", "--timeout", "0"]
#CMD ["python", "-m", "gunicorn", "-b", "0.0.0.0:8011", "--workers", "1", "--worker-class", "sync", "app:app"]
