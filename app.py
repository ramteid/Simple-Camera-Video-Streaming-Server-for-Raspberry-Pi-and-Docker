import sys
import builtins

# Redirect print statements to stderr because stdout is used for image data
_original_print = builtins.print
def _print(*args, **kwargs):
    if kwargs.get("file", sys.stdout) == sys.stdout:
        kwargs["file"] = sys.stderr
    _original_print(*args, **kwargs)
builtins.print = _print

try:
    from picamera2 import Picamera2
except ModuleNotFoundError as e:
    print(f"Module Import Error: {e}. Please install the required package.", file=sys.stderr)
    sys.exit(1)
from PIL import Image, ImageDraw, ImageFont
import io, asyncio, logging
from datetime import datetime
from zoneinfo import ZoneInfo
import time

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s in %(module)s: %(message)s',
    stream=sys.stderr
)

# Configuration parameters
IMAGE_SIZE_X = 640
IMAGE_SIZE_Y = 480
TARGET_FPS = 30
TARGET_FRAME_TIME = 1.0 / TARGET_FPS
TIMEZONE = 'Europe/Berlin'
timezone = ZoneInfo(TIMEZONE)

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

def draw_spinner(draw, center_x, center_y, radius, angle, color=(255, 255, 255)):
    spinner_arc = 270  # constant spinner arc angle
    bbox = [center_x - radius, center_y - radius, center_x + radius, center_y + radius]
    draw.arc(bbox, start=angle, end=angle + spinner_arc, fill=color, width=3)

def draw_timestamp(img):
    """Draws a timestamp and spinner in the bottom-right corner of the image."""
    draw = ImageDraw.Draw(img)
    timestamp = datetime.now(timezone).strftime("%H:%M:%S")
    padding = 10
    # Use textbbox for precise measurement of text dimensions
    text_bbox = draw.textbbox((0, 0), timestamp, font=font)
    text_width = text_bbox[2] - text_bbox[0]
    text_height = text_bbox[3] - text_bbox[1]
    x = IMAGE_SIZE_X - text_width - padding
    y = IMAGE_SIZE_Y - text_height - padding

    spinner_radius = int(text_height * 1.5)
    spinner_center_x = x + text_width / 2  # more precise center (using float)
    spinner_center_y = y - spinner_radius - padding
    spinner_angle = (time.time() * 360) % 360
    draw_spinner(draw, spinner_center_x, spinner_center_y, spinner_radius, spinner_angle)

    # Draw a semi-transparent rectangle using the precise text dimensions
    rect = [x - 5, y - 5, x + text_width + 5, y + text_height + 5]
    draw.rectangle(rect, fill=(0, 0, 0, 128))
    draw.text((x, y), timestamp, font=font, fill=(255, 255, 255))

async def main():
    picam = None
    loop = asyncio.get_event_loop()
    while True:
        start_time = time.perf_counter()
        try:
            if picam is None:
                picam = initialize_camera()
            frame = await loop.run_in_executor(None, picam.capture_array)
            img = Image.fromarray(frame)
            draw_timestamp(img)
            jpeg_buffer.seek(0)
            img.save(jpeg_buffer, format='JPEG', quality=75, optimize=False)
            jpeg_buffer.truncate()
            jpeg = jpeg_buffer.getvalue()
            sys.stdout.buffer.write(jpeg)
            sys.stdout.buffer.flush()
        except Exception as e:
            logging.error(f"Camera error: {e}")
            if picam:
                try:
                    await loop.run_in_executor(None, picam.stop)
                except Exception:
                    pass
                try:
                    await loop.run_in_executor(None, picam.close)
                except Exception:
                    pass
                picam = None
            await asyncio.sleep(2)
        finally:
            elapsed = time.perf_counter() - start_time
            sleep_time = max(0, TARGET_FRAME_TIME - elapsed)
            await asyncio.sleep(sleep_time)

if __name__ == "__main__":
    asyncio.run(main())
