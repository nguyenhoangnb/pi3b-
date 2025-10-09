from __future__ import annotations
from flask import Blueprint, Response, send_from_directory
from pathlib import Path
import subprocess
import time
from .helpers import rec_is_active, cfg_get, get_recording_service_status, set_recording

bp = Blueprint("liveview", __name__)
HLS_DIR = Path("/tmp/picam_hls/")
HLS_DIR.mkdir(parents=True, exist_ok=True)


def _mjpeg_from_hls():
    """Convert HLS stream to MJPEG"""
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-re",  # Read input at native frame rate
        "-i", str(HLS_DIR / "live.m3u8"),
        "-f", "mpjpeg",
        "-q:v", "7",
        "-pix_fmt", "yuvj422p",
        "-boundary_tag", "frame",
        "-"
    ]
    
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=0)
    try:
        while True:
            chunk = p.stdout.read(4096)
            if not chunk:
                break
            yield chunk
    finally:
        try:
            p.kill()
            p.wait(timeout=2)
        except:
            pass


def _mjpeg_from_v4l2(dev: str, fmt: str):
    """Stream MJPEG directly from camera"""
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-f", "v4l2",
        "-input_format", "yuyv422",
        "-framerate", "15",
        "-video_size", fmt,
        "-i", dev,
        "-f", "mpjpeg",
        "-q:v", "7",
        "-pix_fmt", "yuvj422p",
        "-boundary_tag", "frame",
        "-"
    ]
    
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=0)
    try:
        while True:
            chunk = p.stdout.read(4096)
            if not chunk:
                break
            yield chunk
    finally:
        try:
            p.kill()
            p.wait(timeout=2)
        except:
            pass


def _ensure_recorder_running():
    """
    Ensure recorder service is running to provide HLS stream.
    Returns True if service is active or successfully started.
    """
    # Check if recording service is already active
    if rec_is_active():
        print("✓ Recording service already active")
        return True
    
    # Try to start the recording service
    print("🚀 Starting recording service for live view...")
    try:
        set_recording(True)
        
        # Wait a bit for service to actually start
        time.sleep(2.0)
        
        # Check if it's now active
        if rec_is_active():
            print("✓ Recording service started successfully")
            return True
        else:
            print("⚠ Recording service failed to start")
            return False
        
    except Exception as e:
        print(f"⚠ Error starting recording service: {e}")
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
        response = Response(
            "#EXTM3U\n"
            "#EXT-X-VERSION:3\n"
            "#EXT-X-TARGETDURATION:2\n"
            "#EXT-X-MEDIA-SEQUENCE:0\n",
            mimetype="application/vnd.apple.mpegurl"
        )
    else:
        response = send_from_directory(HLS_DIR, "live.m3u8")
    
    # Add cache control headers for live streaming
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache' 
    response.headers['Expires'] = '0'
    return response


@bp.get("/hls/<path:name>")
def hls_files(name: str):
    """Serve HLS segment files (.ts)"""
    return send_from_directory(HLS_DIR, name)


@bp.get("/live.mjpg")
def live_mjpg():
    """
    Serve live MJPEG stream.
    - If recorder service is active and HLS available: transcode from HLS
    - Otherwise: stream directly from camera
    """
    # Try to ensure recorder service is running
    service_started = _ensure_recorder_running()
    
    # Determine which source to use
    use_hls = False
    
    if service_started and rec_is_active():
        # Wait for HLS to be ready (with timeout)
        if _wait_for_hls_ready(timeout=5.0):
            use_hls = True
            print("📹 Using HLS source for MJPEG")
        else:
            print("⚠ HLS not ready, falling back to V4L2")
    else:
        print("ℹ Recording service not active, using V4L2 directly")
    
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