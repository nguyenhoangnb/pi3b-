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
    max_retries = 3
    retry_count = 0
    
    while retry_count < max_retries:
        try:
            process = (
                ffmpeg
                .input(str(HLS_DIR / "live.m3u8"), 
                    re=None,
                    fflags='nobuffer+discardcorrupt',  # Discard corrupt packets
                    flags='low_delay',   # Enable low delay flags
                    analyzeduration='500000',  # Reduce analyze time
                    probesize='500000'   # Reduce probe size
                )
                .output('pipe:', 
                    format='mpjpeg',
                    **{
                        'q:v': 5,               # Slightly better quality
                        'pix_fmt': 'yuvj422p',
                        'boundary_tag': 'frame',
                        'vsync': '0',           # Disable frame dropping
                        'max_delay': '500000',  # 0.5s max delay
                        'thread_queue_size': '512',  # Larger queue
                        'max_muxing_queue_size': '1024'  # Prevent muxing queue overflow
                    })
                .global_args('-hide_banner', '-loglevel', 'error', '-xerror')  # Exit on error
                .run_async(pipe_stdout=True, pipe_stderr=True)
            )
            
            import select
            no_data_count = 0
            
            while True:
                # Wait for data with shorter timeout
                if select.select([process.stdout], [], [], 0.2)[0]:  # 0.2s timeout
                    chunk = process.stdout.read(8192)  # Larger buffer size
                    if not chunk:
                        print("⚠ Empty chunk received, stream may have ended")
                        break
                    no_data_count = 0  # Reset counter on successful read
                    yield chunk
                else:
                    no_data_count += 1
                    if no_data_count > 25:  # About 5 seconds without data
                        print("⚠ No data received for 5s, restarting stream")
                        break
                    continue  # Keep trying instead of yielding empty data
                    
            # If we get here, the stream has ended or timed out
            print("⚠ Stream ended, attempting restart")
            raise Exception("Stream ended")
        except Exception as e:
            print(f"⚠ Stream error: {e}")
            retry_count += 1
            if retry_count < max_retries:
                print(f"🔄 Retrying stream ({retry_count}/{max_retries})...")
                time.sleep(1)  # Wait before retry
            continue
        finally:
            try:
                process.kill()
                process.wait(timeout=1)
            except:
                pass
    
    print("❌ Max retries reached, stream ended")


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
    """Serve HLS playlist file with auto-reload"""
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
    
    response = send_from_directory(HLS_DIR, "live.m3u8")
    # Add headers for auto-reload
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


@bp.get("/hls/<path:name>")
def hls_files(name: str):
    """Serve HLS segment files (.ts) with auto-reload"""
    response = send_from_directory(HLS_DIR, name)
    # Add headers for auto-reload and proper caching
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


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