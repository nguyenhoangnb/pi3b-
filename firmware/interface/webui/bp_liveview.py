from __future__ import annotations
from flask import Blueprint, Response, send_from_directory
from pathlib import Path
import ffmpeg
import time
from .helpers import rec_is_active, cfg_get, get_recorder, start_service, check_service

bp = Blueprint("liveview", __name__)
HLS_DIR = Path("/tmp/picam_hls/")
HLS_DIR.mkdir(parents=True, exist_ok=True)


def _mjpeg_from_hls():
    """Convert HLS stream to MJPEG"""
    try:
        process = (
            ffmpeg
            .input(str(HLS_DIR / "live.m3u8"), re=None)
            .output('pipe:', format='mpjpeg', **{
                'q:v': 7,
                'pix_fmt': 'yuvj422p',
                'boundary_tag': 'frame'
            })
            .run_async(pipe_stdout=True, pipe_stderr=True, quiet=True)
        )
        
        while True:
            chunk = process.stdout.read(4096)
            if not chunk:
                break
            yield chunk
    finally:
        try:
            process.kill()
            process.wait()
        except:
            pass


def _mjpeg_from_v4l2(dev: str, fmt: str):
    """Stream MJPEG directly from camera"""
    try:
        # Parse width and height from format string (e.g., "1280x720")
        width, height = fmt.split('x')
        
        process = (
            ffmpeg
            .input(dev, format='v4l2', input_format='yuyv422', 
                   framerate=15, video_size=fmt)
            .output('pipe:', format='mpjpeg', **{
                'q:v': 7,
                'pix_fmt': 'yuvj422p',
                'boundary_tag': 'frame'
            })
            .run_async(pipe_stdout=True, pipe_stderr=True, quiet=True)
        )
        
        while True:
            chunk = process.stdout.read(4096)
            if not chunk:
                break
            yield chunk
    finally:
        try:
            process.kill()
            process.wait()
        except:
            pass


def _ensure_recorder_running():
    """
    Check if recorder is running to provide HLS stream.
    Returns True if recorder is active.
    NOTE: Does NOT auto-start recorder to avoid camera conflicts.
    """
    recorder = get_recorder()
    if recorder is None:
        print("⚠ VideoRecorder not available")
        return False
    
    # Just check if recording is active, don't start it
    if recorder:
        return True
    
    print("ℹ Recorder not active")
    return False


def _wait_for_hls_ready(timeout: float = 5.0) -> bool:
    """
    Wait for HLS stream to be ready.
    Returns True if HLS files exist, False if timeout.
    """
    m3u8_file = HLS_DIR / "live.m3u8"
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        # Check if m3u8 file exists and has content
        if m3u8_file.exists():
            try:
                # Check if file has some content (not empty)
                if m3u8_file.stat().st_size > 0:
                    # Also check if at least one .ts segment exists
                    ts_files = list(HLS_DIR.glob("*.ts"))
                    if ts_files:
                        print(f"✓ HLS ready: {m3u8_file}")
                        return True
            except:
                pass
        
        time.sleep(0.2)  # Check every 200ms
    
    print(f"⚠ HLS not ready after {timeout}s")
    return False


@bp.get("/hls/live.m3u8")
def hls_playlist():
    """Serve HLS playlist file"""
    m3u8_file = HLS_DIR / "live.m3u8"
    
    if not m3u8_file.exists():
        # Return empty playlist if file doesn't exist
        return Response(
            "#EXTM3U\n"
            "#EXT-X-VERSION:3\n"
            "#EXT-X-TARGETDURATION:2\n"
            "#EXT-X-MEDIA-SEQUENCE:0\n",
            mimetype="application/vnd.apple.mpegurl"
        )
    
    return send_from_directory(HLS_DIR, "live.m3u8")


@bp.get("/hls/<path:name>")
def hls_files(name: str):
    """Serve HLS segment files (.ts)"""
    return send_from_directory(HLS_DIR, name)


@bp.get("/live.mjpg")
def live_mjpg():
    """
    Serve live MJPEG stream.
    - If recorder is active and HLS available: transcode from HLS
    - Otherwise: stream directly from camera
    """
    # Try to ensure recorder is running
    recorder_started = _ensure_recorder_running()
    
    # Determine which source to use
    use_hls = False
    
    if recorder_started and rec_is_active():
        # Wait for HLS to be ready (with timeout)
        if _wait_for_hls_ready(timeout=3.0):
            use_hls = True
            print("📹 Using HLS source for MJPEG")
        else:
            print("⚠ HLS not ready, falling back to V4L2")
    else:
        print("ℹ Recorder not active, using V4L2 directly")
    
    # Generate MJPEG stream
    if use_hls:
        gen = _mjpeg_from_hls()
    else:
        dev = cfg_get("video.v4l2_device", "/dev/video0")
        fmt = cfg_get("video.v4l2_format", "1280x720")
        gen = _mjpeg_from_v4l2(dev, fmt)
    
    return Response(
        gen,
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )