#!/bin/bash
set -e

# Remove any previous named pipe
PIPE=/tmp/vidpipe
rm -f $PIPE
mkfifo $PIPE

# Start the Python overlay process, outputting JPEG frames to the named pipe
python3 /app/app.py > $PIPE &

# Start ffmpeg to read from pipe and stream to rtsp-simple-server container
ffmpeg -r 10 -f image2pipe \
    -i $PIPE \
    -c:v libx264 \
    -preset ultrafast \
    -tune zerolatency \
    -profile:v baseline \
    -g 15 \
    -keyint_min 15 \
    -bf 0 \
    -flags +low_delay \
    -x264-params "nal-hrd=cbr:no-scenecut=1" \
    -b:v 800k \
    -maxrate 800k \
    -bufsize 400k \
    -f rtsp \
    -rtsp_transport tcp \
    -fflags nobuffer \
    -max_delay 0 \
    rtsp://rtsp-server:8554/cam