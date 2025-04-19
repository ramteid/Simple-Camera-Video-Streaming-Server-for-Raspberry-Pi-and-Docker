import sys
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

def main():
    picam = None
    while True:
        try:
            if picam is None:
                picam = initialize_camera()
            frame = picam.capture_array()
            img = Image.fromarray(frame)
            draw_timestamp(img)
            jpeg_buffer.seek(0)
            img.save(jpeg_buffer, format='JPEG', quality=90, optimize=True)
            jpeg_buffer.truncate()
            jpeg = jpeg_buffer.getvalue()
            sys.stdout.buffer.write(jpeg)
            sys.stdout.buffer.flush()
            time.sleep(SLEEP_TIME_SECONDS)
        except Exception as e:
            logging.error(f"Camera error: {e}")
            if picam:
                try:
                    picam.stop()
                except Exception:
                    pass
                try:
                    picam.close()
                except Exception:
                    pass
                picam = None
            time.sleep(2)

if __name__ == "__main__":
    main()
