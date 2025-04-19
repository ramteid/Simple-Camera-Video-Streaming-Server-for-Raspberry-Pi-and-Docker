FROM arm32v7/python:3.9-slim-bullseye

RUN apt update && apt install -y --no-install-recommends \
    curl gnupg dirmngr \
    && echo "deb http://archive.raspberrypi.org/debian bullseye main" >> /etc/apt/sources.list \
    && curl -sSL http://archive.raspberrypi.org/debian/raspberrypi.gpg.key | apt-key add -

RUN apt update && apt install -y --no-install-recommends \
    ffmpeg python3-libcamera python3-picamera2 python3-pil python3-numpy fonts-dejavu \
    && rm -rf /var/lib/apt/lists/*

# enable Python to find the packages installed by apt
ENV PYTHONPATH="/usr/lib/python3/dist-packages"

WORKDIR /app
COPY app.py /app/
COPY run-rtsp.sh /app/

RUN chmod +x /app/run-rtsp.sh

CMD ["/app/run-rtsp.sh"]
