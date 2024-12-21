from flask import Flask, Response, stream_with_context
from picamera2 import Picamera2
from PIL import Image, ImageDraw, ImageFont
import io, threading, time, logging
from datetime import datetime
from zoneinfo import ZoneInfo

# Logging configuration set at the very beginning
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s in %(module)s: %(message)s'
)

# Configuration parameters
IMAGE_SIZE_X = 640
IMAGE_SIZE_Y = 480
SLEEP_TIME_SECONDS = 0.04  # approximately 25 FPS

app = Flask(__name__)
latest_frame = None
frame_lock = threading.Lock()
stop_thread = False

# Load the font (ensure the font path is correct)
font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
font_size = IMAGE_SIZE_Y // 30  # Adjust font size based on image height
try:
    font = ImageFont.truetype(font_path, font_size)
except IOError:
    logging.warning(f"Font {font_path} not found. Using default font.")
    font = ImageFont.load_default()

def initialize_camera():
    picam = Picamera2()
    config = picam.create_still_configuration(main={"size": (IMAGE_SIZE_X, IMAGE_SIZE_Y)})
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

            # Capture the current frame
            frame = picam.capture_array()
            img = Image.fromarray(frame)

            # Draw the timestamp
            draw = ImageDraw.Draw(img)
            timezone = ZoneInfo('Europe/Berlin')
            timestamp = datetime.now(timezone).strftime("%H:%M:%S")
            text_width, text_height = draw.textsize(timestamp, font=font)

            # Position: bottom right with some padding
            padding = 10
            x = IMAGE_SIZE_X - text_width - padding
            y = IMAGE_SIZE_Y - text_height - padding

            # Optional: Add a semi-transparent rectangle behind the text for better visibility
            rectangle_padding = 5
            rectangle_x0 = x - rectangle_padding
            rectangle_y0 = y - rectangle_padding
            rectangle_x1 = x + text_width + rectangle_padding
            rectangle_y1 = y + text_height + rectangle_padding
            draw.rectangle(
                [rectangle_x0, rectangle_y0, rectangle_x1, rectangle_y1],
                fill=(0, 0, 0, 128)  # Semi-transparent black
            )

            # Draw the text
            draw.text((x, y), timestamp, font=font, fill=(255, 255, 255))

            # Convert back to JPEG
            buf = io.BytesIO()
            img.save(buf, format='JPEG')
            jpeg = buf.getvalue()

            with frame_lock:
                latest_frame = jpeg

            # Reduce CPU load
            time.sleep(SLEEP_TIME_SECONDS)

        except Exception as e:
            logging.error(f"Camera error: {e}")
            # Release resources to allow reinitialization
            if picam:
                try:
                    picam.stop()
                except:
                    pass
                picam = None
            logging.info("Reinitializing camera...")
            time.sleep(2)

    # Release resources when stopping the thread
    if picam:
        try:
            picam.stop()
        except:
            pass

def generate_frames():
    while True:
        with frame_lock:
            if latest_frame is not None:
                yield (b'--frame\r\n'
                        b'Content-Type: image/jpeg\r\n\r\n' + latest_frame + b'\r\n')
        time.sleep(SLEEP_TIME_SECONDS)

@app.route('/')
def index():
    return "<html><body><img style='width: 100%; height: auto;' src='/stream'></body></html>"

@app.route('/stream')
def stream():
    return Response(stream_with_context(generate_frames()), mimetype='multipart/x-mixed-replace; boundary=frame')

# Start the frame updater thread
frame_thread = threading.Thread(target=frame_updater, daemon=True)
frame_thread.start()

if __name__ == '__main__':
    try:
        app.run(host='0.0.0.0', port=8011)
    finally:
        # Stop the thread on shutdown
        stop_thread = True
        frame_thread.join()
