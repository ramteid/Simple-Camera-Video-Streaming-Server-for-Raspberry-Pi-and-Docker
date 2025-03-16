import asyncio, weakref
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse
from picamera2 import Picamera2
from PIL import Image, ImageDraw, ImageFont
import io, threading, time, logging
from datetime import datetime
from zoneinfo import ZoneInfo
import psutil
from statistics import mean
import os
import numpy as np
import signal
import sys
from contextlib import contextmanager
from typing import Optional
from queue import Queue
import gc

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s in %(module)s: %(message)s'
)

# Configuration parameters
IMAGE_SIZE_X = 640# Reduced from 640
IMAGE_SIZE_Y = 480# Reduced from 480
SLEEP_TIME_SECONDS = 0.04  # reduces CPU load (~ 25 FPS)
TIMEZONE = 'Europe/Berlin'
MAX_CLIENTS = 10  # Maximum number of concurrent clients
FRAME_JPEG_QUALITY = 90  # Configurable JPEG quality
SKIP_FRAMES_THRESHOLD = 2  # Skip frame if client queue has more than this many frames
MIN_JPEG_QUALITY = 60
MAX_JPEG_QUALITY = 90
MIN_FPS = 5
MAX_FPS = 25
TARGET_CPU_PERCENT = 75
MEASUREMENT_INTERVAL = 5  # seconds
TEMP_THROTTLE_THRESHOLD = 80  # Celsius
ARM_PAGE_SIZE = 4096  # Align buffers to ARM page size
THREAD_SHUTDOWN_TIMEOUT = 5.0
MEMORY_WARNING_THRESHOLD = 85  # percent
CAMERA_RECOVERY_TIMEOUT = 30  # seconds
THREAD_WATCHDOG_TIMEOUT = 10.0  # seconds
FRAME_RECOVERY_ATTEMPTS = 3
BUFFER_POOL_SIZE = 4
CAMERA_HEALTH_CHECK_INTERVAL = 30  # seconds
CAMERA_RECONNECT_DELAY = 5  # seconds

# Pre-allocated MIME boundary headers
FRAME_HEADER = b'--frame\r\nContent-Type: image/jpeg\r\n\r\n'
FRAME_FOOTER = b'\r\n'

# Health monitoring
class HealthStatus:
    """Thread-safe health status container"""
    def __init__(self):
        self._lock = threading.Lock()
        self._status = {
            'camera_ok': False,
            'last_frame_time': 0,
            'memory_usage': 0,
            'active_clients': 0,
            'frame_rate': 0,
            'last_error': None,
            'error_count': 0
        }

    def update(self, **kwargs):
        with self._lock:
            self._status.update(kwargs)

    def get(self):
        with self._lock:
            return dict(self._status)

    def increment_error(self, error_msg):
        with self._lock:
            self._status['error_count'] += 1
            self._status['last_error'] = error_msg

# Replace global health status with thread-safe version
health_status = HealthStatus()

app = FastAPI()

# Replace global frame management with async structures
frame_queue = asyncio.Queue(maxsize=1)
client_queues = set()
client_lock = asyncio.Lock()

# Global buffers for frame processing
jpeg_buffer = io.BytesIO()  # Reusable buffer for JPEG conversion

def check_memory():
    """Monitor memory usage and log warnings."""
    memory = psutil.virtual_memory()
    health_status.update(memory_usage=memory.percent)
    if memory.percent > MEMORY_WARNING_THRESHOLD:
        logging.warning(f"High memory usage: {memory.percent}%")
    return memory.percent > 95  # Critical threshold

def get_event_loop():
    """Safely get or create event loop."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop

class SafeFrameBuffer:
    """Thread-safe frame buffer management."""
    def __init__(self, size):
        self.buffer = bytearray(size)
        self.lock = threading.Lock()
        self._view = None
    
    @property
    def view(self):
        if self._view is None:
            self._view = memoryview(self.buffer)
        return self._view
    
    def __del__(self):
        if self._view is not None:
            self._view.release()

class SafeMIMEBuffer:
    """Thread-safe MIME buffer with proper cleanup."""
    def __init__(self, initial_size):
        self.buffer = bytearray(initial_size)
        self.lock = threading.Lock()
        self._view = None
    
    def ensure_size(self, size):
        with self.lock:
            if len(self.buffer) < size:
                self.buffer.extend(bytearray(size - len(self.buffer)))
            return memoryview(self.buffer)[:size]
    
    def __del__(self):
        if self._view is not None:
            self._view.release()

# Replace global buffers with safe versions
frame_buffers = SafeFrameBuffer(
    ((IMAGE_SIZE_X * IMAGE_SIZE_Y * 3 + ARM_PAGE_SIZE - 1) 
     // ARM_PAGE_SIZE * ARM_PAGE_SIZE)
)
mime_buffer = SafeMIMEBuffer(IMAGE_SIZE_X * IMAGE_SIZE_Y * 3)

class FrameBufferPool:
    """Thread-safe pool of frame buffers to reduce allocations."""
    def __init__(self, buffer_size: int, pool_size: int):
        self.pool = Queue(maxsize=pool_size)
        for _ in range(pool_size):
            buf = bytearray(buffer_size)
            self.pool.put(memoryview(buf))
        self._size = buffer_size

    @contextmanager
    def get_buffer(self):
        buf = None
        try:
            buf = self.pool.get(timeout=0.1)
            yield buf
        finally:
            if buf is not None:
                try:
                    self.pool.put(buf)
                except:
                    pass  # Pool might be full

# Add buffer pool instance
frame_buffer_pool = FrameBufferPool(
    ((IMAGE_SIZE_X * IMAGE_SIZE_Y * 3 + ARM_PAGE_SIZE - 1) 
     // ARM_PAGE_SIZE * ARM_PAGE_SIZE),
    BUFFER_POOL_SIZE
)

# Load the font (ensure the font path is correct)
font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
font_size = IMAGE_SIZE_Y // 30  # Adjust font size based on image height
try:
    font = ImageFont.truetype(font_path, font_size)
except IOError:
    logging.warning(f"Font {font_path} not found. Using default font.")
    font = ImageFont.load_default()

# Function to read CPU temperature
def get_cpu_temp():
    try:
        with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
            return float(f.read()) / 1000.0
    except:
        return 0.0

class AdaptiveQualityController:
    def __init__(self):
        self.jpeg_quality = MAX_JPEG_QUALITY
        self.frame_interval = 1.0 / MAX_FPS
        self.cpu_samples = []
        self.temp_samples = []
        self.last_adjustment = time.monotonic()
        
    def update(self):
        current_time = time.monotonic()
        if current_time - self.last_adjustment < MEASUREMENT_INTERVAL:
            return
            
        cpu_percent = psutil.cpu_percent()
        cpu_temp = get_cpu_temp()
        
        self.cpu_samples.append(cpu_percent)
        self.temp_samples.append(cpu_temp)
        if len(self.cpu_samples) > 10:
            self.cpu_samples.pop(0)
            self.temp_samples.pop(0)
            
        avg_cpu = mean(self.cpu_samples)
        avg_temp = mean(self.temp_samples)
        
        # Adjust quality based on both CPU load and temperature
        if avg_temp > TEMP_THROTTLE_THRESHOLD or avg_cpu > TARGET_CPU_PERCENT:
            if self.jpeg_quality > MIN_JPEG_QUALITY:
                self.jpeg_quality -= 5
            elif 1.0 / self.frame_interval > MIN_FPS:
                self.frame_interval *= 1.2
        elif avg_temp < TEMP_THROTTLE_THRESHOLD - 5 and avg_cpu < TARGET_CPU_PERCENT - 10:
            if 1.0 / self.frame_interval < MAX_FPS:
                self.frame_interval /= 1.1
            elif self.jpeg_quality < MAX_JPEG_QUALITY:
                self.jpeg_quality += 5
                
        self.last_adjustment = current_time
        
    def should_skip_frame(self):
        return psutil.cpu_percent() > 90

# Global state
quality_controller = AdaptiveQualityController()
stop_event = threading.Event()
frame_thread = None

# Thread-safe buffer management
buffer_lock = threading.Lock()

def initialize_camera():
    """Initialize and start the camera with optimized settings."""
    try:
        # Try to clean up any existing camera instances using multiple methods
        try:
            # Try to release any existing Picamera2 instances
            for obj in gc.get_objects():
                if isinstance(obj, Picamera2):
                    try:
                        obj.close()
                    except:
                        pass
            
            # Alternative cleanup methods
            try:
                import subprocess
                # Try v4l2-ctl if available
                subprocess.run(['v4l2-ctl', '--list-devices'], timeout=1, capture_output=True)
                subprocess.run(['v4l2-ctl', '--device=/dev/video0', '--stream-mmap=0'], timeout=1, capture_output=True)
            except (FileNotFoundError, subprocess.SubProcessError):
                pass
                
        except Exception as e:
            logging.debug(f"Camera cleanup attempt failed (non-critical): {e}")
        
        # Small delay to allow camera to reset
        time.sleep(1.0)  # Increased delay for more reliable initialization
        
        picam = Picamera2()
        
        # Wait for camera to be ready
        picam.global_camera_info()
        
        # Get camera native resolution
        properties = picam.sensor_resolution
        logging.info(f"Camera sensor resolution: {properties}")
        
        # Configure for native resolution with BGR format
        config = picam.create_still_configuration(
            main={
                "size": (IMAGE_SIZE_X, IMAGE_SIZE_Y),  # Fix: Use X, Y order (width, height)
                "format": "BGR888"  # Changed from RGB888 to BGR888
            },
            controls={
                "FrameDurationLimits": (33333, 100000),
                "NoiseReductionMode": 0
            }
        )
        
        # Configure with error checking
        try:
            picam.configure(config)
            logging.info("Camera configuration applied successfully")
        except Exception as e:
            raise RuntimeError(f"Camera configuration failed: {e}")
            
        # Start camera
        picam.start()
        
        # Verify camera is working by capturing a test frame
        try:
            test_frame = picam.capture_array()
            logging.info(f"Test frame shape: {test_frame.shape}")
            if test_frame.shape != (IMAGE_SIZE_Y, IMAGE_SIZE_X, 3):
                # Only log a warning since the dimensions might be swapped
                logging.warning(f"Unexpected frame size: {test_frame.shape}, expected ({IMAGE_SIZE_Y}, {IMAGE_SIZE_X}, 3)")
            logging.info("Camera started successfully")
            return picam
        except Exception as e:
            raise RuntimeError(f"Camera startup verification failed: {e}")
        
    except Exception as e:
        logging.error(f"Camera initialization failed: {e}")
        # Try to clean up on error
        try:
            if 'picam' in locals():
                picam.close()
                logging.info("Camera closed successfully after failure")
        except Exception as cleanup_error:
            logging.error(f"Camera cleanup failed: {cleanup_error}")
        return None

def cleanup_resources():
    """Clean up all resources properly."""
    stop_event.set()
    
    # Stop camera first
    camera_manager.stop()
    
    # Clean up frame thread
    if frame_thread and frame_thread.is_alive():
        try:
            frame_thread.join(timeout=THREAD_SHUTDOWN_TIMEOUT)
        except TimeoutError:
            logging.error("Frame thread shutdown timed out")
    
    # Clear all buffers
    global frame_buffers, mime_buffer, jpeg_buffer, frame_buffer_pool
    frame_buffers = None
    mime_buffer = None
    if jpeg_buffer:
        jpeg_buffer.close()
        jpeg_buffer = None
    frame_buffer_pool = None
    
    # Force garbage collection
    gc.collect()

def signal_handler(signum, frame):
    """Handle system signals gracefully."""
    logging.info(f"Received signal {signum}, shutting down...")
    cleanup_resources()
    sys.exit(0)

class FrameThreadWatchdog:
    """Monitors frame thread health and restarts if needed."""
    def __init__(self):
        self.last_frame_time = 0
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self.recovery_attempts = 0
        
    def update(self):
        with self._lock:
            self.last_frame_time = time.monotonic()
            
    def check_health(self):
        with self._lock:
            if not self._thread or not self._thread.is_alive():
                if self.recovery_attempts < FRAME_RECOVERY_ATTEMPTS:
                    self.recovery_attempts += 1
                    logging.warning(f"Attempting frame thread recovery ({self.recovery_attempts}/{FRAME_RECOVERY_ATTEMPTS})")
                    self._restart_thread()
                return False
            self.recovery_attempts = 0
            return time.monotonic() - self.last_frame_time < THREAD_WATCHDOG_TIMEOUT
    
    def set_thread(self, thread: threading.Thread):
        with self._lock:
            self._thread = thread

    def _restart_thread(self):
        try:
            cleanup_resources()  # Clean up old resources
            gc.collect()  # Force garbage collection
            start_frame_thread_once()
        except Exception as e:
            logging.error(f"Thread recovery failed: {e}")

# Add watchdog instance
frame_watchdog = FrameThreadWatchdog()

class CameraManager:
    """Manages camera lifecycle and health checks."""
    def __init__(self):
        self._camera = None
        self._lock = threading.Lock()
        self._last_health_check = 0
        self._last_init_attempt = 0
        self._init_retry_delay = 5.0  # Minimum time between init attempts
        
    def get_camera(self):
        current_time = time.monotonic()
        with self._lock:
            if (self._camera is not None and 
                current_time - self._last_health_check > CAMERA_HEALTH_CHECK_INTERVAL):
                self._check_health()
            return self._camera
    
    def initialize(self):
        current_time = time.monotonic()
        with self._lock:
            # Add retry delay
            if current_time - self._last_init_attempt < self._init_retry_delay:
                return False
                
            self._last_init_attempt = current_time
            if self._camera is None:
                try:
                    self._camera = initialize_camera()
                    if self._camera:
                        self._last_health_check = current_time
                except Exception as e:
                    logging.error(f"Camera initialization failed: {e}")
                    self._cleanup()
            return self._camera is not None
    
    def _check_health(self):
        try:
            if self._camera:
                # Try to capture a test frame
                self._camera.capture_array()
                self._last_health_check = time.monotonic()
        except Exception as e:
            logging.error(f"Camera health check failed: {e}")
            self._cleanup()
    
    def _cleanup(self):
        if self._camera:
            try:
                self._camera.stop()
                self._camera.close()
            except:
                pass
            self._camera = None
    
    def stop(self):
        with self._lock:
            self._cleanup()

# Replace global camera management with manager instance
camera_manager = CameraManager()

# Replace frame_updater function with safer version
def frame_updater():
    """Thread that continuously grabs frames and stores the latest JPEG."""
    global health_status
    picam = None
    last_frame_time = 0
    frames_count = 0
    last_fps_check = time.monotonic()
    last_error_time = 0
    loop = None
    loop_retries = 0
    MAX_LOOP_RETRIES = 3
    
    def ensure_loop_running():
        nonlocal loop, loop_retries
        if loop is None or not loop.is_running():
            if loop_retries >= MAX_LOOP_RETRIES:
                logging.error("Failed to start event loop after multiple attempts")
                return False
            try:
                if loop:
                    try:
                        loop.stop()
                        loop.close()
                    except:
                        pass
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                # Start loop in a separate thread
                loop_thread = threading.Thread(target=loop.run_forever, daemon=True)
                loop_thread.start()
                loop_retries += 1
                time.sleep(0.1)  # Give loop time to start
                if not loop.is_running():
                    raise RuntimeError("Event loop failed to start")
                logging.info("Event loop started successfully")
                return True
            except Exception as e:
                logging.error(f"Failed to start event loop: {e}")
                return False
        return True

    try:
        os.nice(10)
        if not ensure_loop_running():
            return
    except Exception as e:
        logging.error(f"Setup error: {e}")
        return

    while not stop_event.is_set():
        try:
            if not ensure_loop_running():
                time.sleep(1)
                continue
                
            # Update watchdog
            frame_watchdog.update()
            
            # Update health metrics with thread safety
            current_time = time.monotonic()
            if current_time - last_fps_check >= 1.0:
                health_status.update(frame_rate=frames_count)
                frames_count = 0
                last_fps_check = current_time
                
                if check_memory():
                    logging.error("Critical memory usage - skipping frames")
                    time.sleep(1)
                    continue

            if current_time - last_frame_time < quality_controller.frame_interval:
                logging.warning("Frame interval not reached")
                time.sleep(0.001)
                continue

            # Skip frame if system is overwhelmed
            if quality_controller.should_skip_frame():
                logging.warning("Skipping frame due to high CPU load")
                time.sleep(0.1)
                continue

            # Camera error recovery timeout
            if picam is None and current_time - last_error_time < CAMERA_RECOVERY_TIMEOUT:
                time.sleep(1)
                continue

            # Update quality settings
            quality_controller.update()

            # Camera management
            if not camera_manager.get_camera():
                if current_time - last_error_time < CAMERA_RECONNECT_DELAY:
                    time.sleep(1)
                    continue
                if not camera_manager.initialize():
                    last_error_time = current_time
                    health_status.increment_error("Camera initialization failed")
                    continue

            # Use camera through manager
            camera = camera_manager.get_camera()
            if not camera:
                continue

            # Capture directly into pre-allocated buffer if possible
            with frame_buffer_pool.get_buffer() as frame_buffer:
                frame = camera.capture_array()
                if frame is None:
                    raise RuntimeError("Captured frame is None")
                
                # Log frame info for debugging
                logging.debug(f"Frame shape: {frame.shape}, dtype: {frame.dtype}, size: {frame.size}")
                
                # Handle frame resizing before array operations
                if frame.shape != (IMAGE_SIZE_Y, IMAGE_SIZE_X, 3):
                    # Convert to PIL Image for high-quality resizing
                    pil_img = Image.fromarray(frame)
                    pil_img = pil_img.resize((IMAGE_SIZE_X, IMAGE_SIZE_Y), Image.Resampling.LANCZOS)
                    frame = np.array(pil_img)
                
                np_frame = None
                try:
                    frame_copy = np.array(frame, copy=True)
                    np_frame = np.frombuffer(frame_buffer, dtype=np.uint8)
                    np_frame = np_frame.reshape((IMAGE_SIZE_Y, IMAGE_SIZE_X, 3))
                    np.copyto(np_frame, frame_copy)
                    img = Image.fromarray(np_frame)
                    draw_timestamp(img)
                except (ValueError, TypeError, BufferError) as e:
                    logging.error(f"Array operation error: {e}")
                    continue
                finally:
                    del frame_copy
                    del np_frame

            # Reuse JPEG buffer with adaptive quality
            jpeg_buffer.seek(0)
            img.save(jpeg_buffer, format='JPEG', 
                    quality=int(quality_controller.jpeg_quality), 
                    optimize=True)
            jpeg_buffer.truncate()
            
            # Create MIME frame in pre-allocated buffer
            jpeg_data = jpeg_buffer.getvalue()
            mime_len = len(FRAME_HEADER) + len(jpeg_data) + len(FRAME_FOOTER)
            
            # Use safe MIME buffer allocation
            frame_data = mime_buffer.ensure_size(mime_len)
            frame_data[0:len(FRAME_HEADER)] = FRAME_HEADER
            frame_data[len(FRAME_HEADER):len(FRAME_HEADER) + len(jpeg_data)] = jpeg_data
            frame_data[len(FRAME_HEADER) + len(jpeg_data):mime_len] = FRAME_FOOTER
            
            if loop and loop.is_running():
                try:
                    future = asyncio.run_coroutine_threadsafe(
                        update_client_queues(frame_data), 
                        loop
                    )
                    future.result(timeout=1.0)
                except Exception as e:
                    logging.error(f"Failed to update client queues: {e}")
                    # Reset loop on failure
                    loop_retries = 0
                    ensure_loop_running()
            else:
                logging.error("Event loop not available")
                loop_retries = 0
                time.sleep(1)
                continue

            health_status.update(
                camera_ok=True,
                last_frame_time=current_time,
                error_count=0,
                last_error=None
            )
            frames_count += 1
            last_frame_time = current_time

        except Exception as e:
            health_status.update(camera_ok=False)
            health_status.increment_error(str(e))
            logging.error(f"Camera error: {e}")
            camera_manager.stop()  # Cleanup camera on error
            last_error_time = current_time
            time.sleep(1)

        finally:
            if img:
                try:
                    img.close()
                except:
                    pass

    # Cleanup
    camera_manager.stop()
    if loop:
        try:
            loop.stop()
            loop.close()
        except:
            pass

async def update_client_queues(frame):
    """Update all client queues with the new frame."""
    async with client_lock:
        health_status.update(active_clients=len(client_queues))
        disconnected = set()
        current_clients = len(client_queues)
        
        # Adjust skip threshold based on number of clients
        dynamic_skip_threshold = max(1, min(5, current_clients // 2))
        
        for queue in client_queues:
            try:
                # Skip frame for slow clients more aggressively as load increases
                if queue.qsize() >= dynamic_skip_threshold:
                    continue
                    
                # Remove old frame if queue is full
                if queue.full():
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                        
                await queue.put(frame)
            except Exception:
                disconnected.add(queue)
        
        # Remove disconnected clients
        for queue in disconnected:
            client_queues.remove(queue)
        
        if disconnected:
            logging.info(f"Removed {len(disconnected)} disconnected clients. Active clients: {len(client_queues)}")

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

    # Draw spinner above the timestamp - 5x larger
    spinner_radius = (text_height // 2) * 5
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

async def generate_frames(request: Request):
    """Asynchronous generator function for streaming frames to a single client."""
    client_queue = asyncio.Queue(maxsize=2)  # Allow for one frame buffering
    
    async with client_lock:
        if len(client_queues) >= MAX_CLIENTS:
            logging.warning("Maximum number of clients reached")
            return
        client_queues.add(client_queue)
        logging.info(f"New client connected. Active clients: {len(client_queues)}")
    
    try:
        while True:
            if await request.is_disconnected():
                break
            
            try:
                frame_data = await asyncio.wait_for(client_queue.get(), timeout=5.0)
                yield bytes(frame_data)  # Convert memoryview to bytes
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logging.error(f"Stream error: {e}")
                break
    finally:
        async with client_lock:
            client_queues.remove(client_queue)
            logging.info(f"Client disconnected. Active clients: {len(client_queues)}")

@app.get('/')
async def index():
    return """
        <html>
        <body style="margin: 0; overflow: hidden;">
            <img style="max-width: 100vw; max-height: 100vh; width: auto; height: auto; display: block; margin: auto;" src="/stream">
        </body>
        </html>
        """

@app.get('/stream')
async def stream(request: Request):
    return StreamingResponse(
        generate_frames(request),
        media_type='multipart/x-mixed-replace; boundary=frame'
    )

# Update health check to use thread-safe access
@app.get('/health')
async def health_check():
    """Health check endpoint."""
    status = health_status.get()
    
    if not frame_watchdog.check_health():
        raise HTTPException(status_code=503, detail="Frame thread not healthy")
    
    if not status['camera_ok']:
        raise HTTPException(status_code=503, detail="Camera not functioning")
    
    if time.monotonic() - status['last_frame_time'] > 5.0:
        raise HTTPException(status_code=503, detail="Frame capture stalled")
    
    if status['memory_usage'] > MEMORY_WARNING_THRESHOLD:
        raise HTTPException(status_code=503, detail="High memory usage")
    
    return status

def start_frame_thread_once():
    """Start the frame-updater thread if not already running."""
    global frame_thread
    # Make sure we only start the thread once
    if frame_thread is None or not frame_thread.is_alive():
        logging.info("Starting frame_updater thread.")
        frame_thread = threading.Thread(target=frame_updater, daemon=True)
        frame_thread.start()
        frame_watchdog.set_thread(frame_thread)

@app.on_event("startup")
def initialize_on_startup():
    """
    Lazy initialization hook:
    This will run once per worker process (on the first request),
    ensuring the camera thread is started in the *worker*, not the master.
    """
    start_frame_thread_once()

@app.on_event("shutdown")
async def shutdown_event():
    """Ensure clean shutdown of all resources."""
    logging.info("Shutting down application...")
    stop_event.set()
    camera_manager.stop()
    
    if frame_thread and frame_thread.is_alive():
        try:
            frame_thread.join(timeout=THREAD_SHUTDOWN_TIMEOUT)
        except TimeoutError:
            logging.error("Frame thread shutdown timed out")
    
    # Clean up buffers
    global frame_buffers
    frame_buffers = None
    
    # Clear all client queues
    async with client_lock:
        for queue in client_queues.copy():
            try:
                while not queue.empty():
                    queue.get_nowait()
            except:
                pass
        client_queues.clear()

if __name__ == '__main__':
    # Register signal handlers
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    import uvicorn
    try:
        # Running in plain Uvicorn mode for local debug
        uvicorn.run(app, host='0.0.0.0', port=8011)
    finally:
        # Stop the thread on shutdown
        cleanup_resources()
