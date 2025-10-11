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
            .input(str(HLS_DIR / "live.m3u8"), 
                re=None,
                fflags='nobuffer',  # Disable input buffering
                flags='low_delay'   # Enable low delay flags
            )
            .output('pipe:', 
                format='mpjpeg',
                **{
                    'q:v': 5,               # Slightly better quality
                    'pix_fmt': 'yuvj422p',
                    'boundary_tag': 'frame',
                    'vsync': '0',           # Disable frame dropping
                    'max_delay': '500000',  # 0.5s max delay
                    'thread_queue_size': '512'  # Larger queue for better throughput
                })
            .global_args('-hide_banner', '-loglevel', 'error')
            .run_async(pipe_stdout=True, pipe_stderr=True)
        )
        
        import select
        
        while True:
            # Wait for data with timeout
            if select.select([process.stdout], [], [], 0.5)[0]:  # 0.5s timeout
                chunk = process.stdout.read(8192)  # Larger buffer size
                if not chunk:
                    break
                yield chunk
            else:
                # No data available, yield empty chunk to keep connection alive
                yield b''
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


def _wait_for_hls_ready(timeout: float = 1.0) -> bool:
    """
    Quick check if HLS stream is ready.
    Returns True if m3u8 file exists and has content.
    """
    m3u8_file = HLS_DIR / "live.m3u8"
    start_time = time.time()
    
    # Only check m3u8 file with shorter timeout
    while time.time() - start_time < timeout:
        if m3u8_file.exists():
            try:
                if m3u8_file.stat().st_size > 0:
                    return True
            except:
                pass
        time.sleep(0.1)  # Shorter sleep interval
    
    print(f"⚠ HLS playlist not ready")
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
    Serve live MJPEG stream from HLS only.
    Returns error if HLS is not available (recorder must be running).
    """
    # Check if recorder is running
    recorder_started = _ensure_recorder_running()
    
    if not recorder_started or not rec_is_active():
        print("❌ Recorder not active, cannot stream")
        return Response(
            "Recorder is not active. Please start recording first.",
            status=503,
            mimetype="text/plain"
        )
    
    # Wait for HLS to be ready
    if not _wait_for_hls_ready(timeout=1.0):  # Match the default timeout
        print("❌ HLS not ready after 1s")
        return Response(
            "HLS stream not ready. Please wait for recorder to initialize.",
            status=503,
            mimetype="text/plain"
        )
    
    # Generate MJPEG stream from HLS
    print("📹 Streaming MJPEG from HLS")
    gen = _mjpeg_from_hls()
    
    return Response(
        gen,
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )