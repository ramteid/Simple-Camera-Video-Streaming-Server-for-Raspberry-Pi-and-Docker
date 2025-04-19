# Simple Camera Video Streaming Server for Raspberry Pi and Docker

This is a simple video streaming app for Raspberry Pi. I made this because existing alternatives didn't fit or work. The most popular app MotionEye is hopelessly outdated and was impossible to get to work on the latest Raspberry Pi OS. Also I didn't need any overhead like user authentication. <br>
<br>
Made for and tested with Raspberry Pi 3B+ with OV5647 camera on Raspberry Pi OS with Desktop 32 Bit.

## Instructions
- Install Docker if you haven't already using `install-docker.sh` (works best on the 32-bit Raspberry OS)
- `docker-compose build`
- `docker-compose up -d`
- Open a browser with `http://YOURPI:8011`
- Or access the stream directly with an app like VNC: `http://YOURPI:8011/stream`

## Hints
- I'm using a older Python Docker image (3.9) because the Python packages I found are mostly older, too.
- The default video size is 640x480. You can adjust it in `app.py`. Get available resolutions with `libcamera-hello --list-cameras`.