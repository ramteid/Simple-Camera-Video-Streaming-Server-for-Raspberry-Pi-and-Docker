ARG DEBIAN_FRONTEND=noninteractive
FROM arm32v7/python:3.9-slim-bullseye

# Mount caches from host to accelerate the build
RUN --mount=target=/var/lib/apt/lists,type=cache,sharing=locked \
    --mount=target=/var/cache/apt,type=cache,sharing=locked \
    apt update && apt install -y --no-install-recommends gnupg dirmngr curl

# Add Raspberry Pi OS repository and key
RUN echo "deb http://archive.raspberrypi.org/debian bullseye main" >> /etc/apt/sources.list && \
    curl -sSL http://archive.raspberrypi.org/debian/raspberrypi.gpg.key | apt-key add -

RUN --mount=type=cache,target=/var/lib/apt/lists \
    --mount=type=cache,target=/var/cache/apt \
    apt update && apt install -y --no-install-recommends \
        python3-libcamera \
        python3-picamera2 \
        python3-pil \
        python3-numpy \
        fonts-dejavu \
        ffmpeg

# enable Python to find the packages installed by apt
ENV PYTHONPATH="/usr/lib/python3/dist-packages"

WORKDIR /app
COPY app.py /app/
COPY run-rtsp.sh /app/

RUN chmod +x /app/run-rtsp.sh

CMD ["/app/run-rtsp.sh"]
