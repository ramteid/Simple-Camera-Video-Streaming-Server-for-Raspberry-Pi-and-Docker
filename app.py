from flask import Flask, Response
from picamera2 import Picamera2
from PIL import Image
import io, threading, time, logging

# Set the size of the video stream here
image_size_x = 640
image_size_y = 480

app = Flask(__name__)
latest_frame = None
frame_lock = threading.Lock()
stop_thread = False

def initialize_camera():
    picam = Picamera2()
    config = picam.create_still_configuration(main={"size": (image_size_x, image_size_y)})
    picam.configure(config)
    picam.start()
    return picam

def frame_updater():
    global latest_frame, stop_thread
    picam = None

    while not stop_thread:
        try:
            if picam is None:
                picam = initialize_camera()
                logging.info("Camera initialized")

            # take current picture
            frame = picam.capture_array()
            img = Image.fromarray(frame)
            buf = io.BytesIO()
            img.save(buf, format='JPEG')
            jpeg = buf.getvalue()

            with frame_lock:
                latest_frame = jpeg

            # reduce cpu load
            time.sleep(0.01)

        except Exception as e:
            logging.error(f"Camera error: {e}")
            # release camera to allow re-initializing it
            if picam:
                try:
                    picam.stop()
                except:
                    pass
                picam = None
            logging.info("Retrying camera initialization...")
            time.sleep(2)

def generate_frames():
    while True:
        with frame_lock:
            if latest_frame is not None:
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + latest_frame + b'\r\n')
        time.sleep(0.01)

@app.route('/')
def index():
    return "<html><body><img src='/stream'></body></html>"

@app.route('/stream')
def stream():
    return Response(generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

# thread needs to be started on module import
threading.Thread(target=frame_updater, daemon=True).start()

if __name__ == '__main__':
    try:
        app.run(host='0.0.0.0', port=8011)
    finally:
        # stop thread on shutdown
        stop_thread = True
