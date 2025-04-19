from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from picamera2 import Picamera2
from PIL import Image, ImageDraw, ImageFont
import io, asyncio, logging
from datetime import datetime
from zoneinfo import ZoneInfo
import time

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s in %(module)s: %(message)s'
)

# Configuration parameters
IMAGE_SIZE_X = 640
IMAGE_SIZE_Y = 480
SLEEP_TIME_SECONDS = 0.04  # reduces CPU load (~ 25 FPS)
TIMEZONE = 'Europe/Berlin'

app = FastAPI()

# Globals for camera data
latest_frame = None
frame_lock = asyncio.Lock()
stop_event = asyncio.Event()

# We'll create the task **lazily** (on first request) so that
# it happens inside the actual Gunicorn worker context.
frame_task = None

# Buffer for JPEG conversion
jpeg_buffer = io.BytesIO()

# Load the font (ensure the font path is correct)
font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
font_size = IMAGE_SIZE_Y // 30  # Adjust font size based on image height
try:
    font = ImageFont.truetype(font_path, font_size)
except IOError:
    logging.warning(f"Font {font_path} not found. Using default font.")
    font = ImageFont.load_default()

def initialize_camera():
    """Initialize and start the camera."""
    picam = Picamera2()
    config = picam.create_still_configuration(main={"size": (IMAGE_SIZE_X, IMAGE_SIZE_Y)})
    picam.configure(config)
    picam.start()
    return picam

async def frame_updater():
    """Async task that continuously grabs frames and stores the latest JPEG."""
    global latest_frame
    picam = None

    while not stop_event.is_set():
        try:
            if picam is None:
                picam = initialize_camera()
                logging.info("Camera initialized")

            # Capture the current frame
            frame = picam.capture_array()
            img = Image.fromarray(frame)

            # Draw timestamp
            draw_timestamp(img)

            # Convert to JPEG (re-use the buffer)
            jpeg_buffer.seek(0)
            img.save(jpeg_buffer, format='JPEG', quality=90, optimize=True)
            jpeg_buffer.truncate()
            jpeg = jpeg_buffer.getvalue()

            async with frame_lock:
                latest_frame = jpeg

            await asyncio.sleep(SLEEP_TIME_SECONDS)

        except Exception as e:
            logging.error(f"Camera error: {e}")
            # Release resources to allow reinitialization
            if picam:
                try:
                    picam.stop()
                except Exception as stop_exc:
                    logging.warning(f"Error stopping camera: {stop_exc}")
                try:
                    picam.close()
                except Exception as close_exc:
                    logging.warning(f"Error closing camera: {close_exc}")
                picam = None
            logging.info("Reinitializing camera in 2 seconds...")
            await asyncio.sleep(2)

    # Release resources when stopping the task
    if picam:
        try:
            picam.stop()
        except Exception:
            pass
        try:
            picam.close()
        except Exception:
            pass

def draw_spinner(draw, center_x, center_y, radius, angle, color=(255, 255, 255)):
    """Draws a spinner at the specified location."""
    start_angle = angle
    end_angle = angle + 270  # Spinner arc length
    draw.arc(
        [center_x - radius, center_y - radius, center_x + radius, center_y + radius],
        start=start_angle,
        end=end_angle,
        fill=color,
        width=3
    )

def draw_timestamp(img):
    """Draws a timestamp and spinner in the bottom-right corner of the image."""
    draw = ImageDraw.Draw(img)
    timezone = ZoneInfo(TIMEZONE)
    timestamp = datetime.now(timezone).strftime("%H:%M:%S")
    text_width, text_height = draw.textsize(timestamp, font=font)

    # Position: bottom right with some padding
    padding = 10
    x = IMAGE_SIZE_X - text_width - padding
    y = IMAGE_SIZE_Y - text_height - padding

    # Draw spinner above the timestamp
    spinner_radius = int(text_height * 1.5)  # 3 times larger than original
    spinner_center_x = x + text_width // 2
    spinner_center_y = y - spinner_radius - padding
    current_time = time.time()
    spinner_angle = (current_time * 360) % 360
    draw_spinner(draw, spinner_center_x, spinner_center_y, spinner_radius, spinner_angle)

    # Optional: semi-transparent rectangle
    rectangle_padding = 5
    rectangle_x0 = x - rectangle_padding
    rectangle_y0 = y - rectangle_padding
    rectangle_x1 = x + text_width + rectangle_padding
    rectangle_y1 = y + text_height + rectangle_padding
    draw.rectangle(
        [rectangle_x0, rectangle_y0, rectangle_x1, rectangle_y1],
        fill=(0, 0, 0, 128)
    )

    draw.text((x, y), timestamp, font=font, fill=(255, 255, 255))

async def generate_frames():
    """Async generator function for streaming frames."""
    while True:
        async with frame_lock:
            if latest_frame is not None:
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + latest_frame + b'\r\n')
        await asyncio.sleep(SLEEP_TIME_SECONDS)

@app.get("/")
async def index():
    return """
        <html>
        <body style="margin: 0; overflow: hidden;">
            <img style="max-width: 100vw; max-height: 100vh; width: auto; height: auto; display: block; margin: auto;" src="/stream">
        </body>
        </html>
        """

@app.get("/stream")
async def stream():
    return StreamingResponse(generate_frames(), media_type='multipart/x-mixed-replace; boundary=frame')

def start_frame_task_once():
    """Start the frame-updater async task if not already running."""
    global frame_task
    # Make sure we only start the task once
    if frame_task is None or frame_task.done():
        logging.info("Starting frame_updater async task.")
        loop = asyncio.get_event_loop()
        frame_task = loop.create_task(frame_updater())

@app.on_event("startup")
async def initialize_on_first_request():
    """
    Lazy initialization hook:
    This will run once per worker process (on the first request),
    ensuring the camera task is started in the *worker*, not the master.
    """
    start_frame_task_once()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8011, reload=True)
