# Simple Camera RTSP Streaming Server for Raspberry Pi and Docker

This is a simple RTSP video streaming app for Raspberry Pi. It overlays the current time and a loading spinner on the video. The stream is provided via RTSP, suitable for clients like VLC.

Made for and tested with Raspberry Pi 3B+ with OV5647 camera on Raspberry Pi OS with Desktop 32 Bit.

## Instructions
- Install Docker if you haven't already using `install-docker.sh` (works best on the 32-bit Raspberry OS)
- `docker-compose build`
- `docker-compose up -d`
- Open VLC or another RTSP client and connect to: `rtsp://YOURPI:8554/cam`

## Hints
- The default video size is 640x480. You can adjust it in `app.py`. Get available resolutions with `libcamera-hello --list-cameras`.
- The overlays (time and spinner) are rendered in Python before streaming.
- The system uses FFmpeg for efficient RTSP streaming.