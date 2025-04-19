#!/bin/bash
set -e

# Remove any previous named pipe
PIPE=/tmp/vidpipe
rm -f $PIPE
mkfifo $PIPE

# Start the Python overlay process, outputting JPEG frames to the named pipe
python3 /app/app.py > $PIPE &

# Start ffmpeg to read from the pipe and serve RTSP
# The following command assumes 640x480 JPEG input at ~25fps
ffmpeg -re -f image2pipe -vcodec mjpeg -r 25 -i $PIPE \
    -c:v libx264 -preset ultrafast -tune zerolatency -pix_fmt yuv420p -f rtsp rtsp://0.0.0.0:8554/cam
