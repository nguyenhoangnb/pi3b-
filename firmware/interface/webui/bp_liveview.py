from __future__ import annotations
from flask import Blueprint, Response, send_from_directory
from pathlib import Path
import subprocess
import time
import cv2

from .helpers import rec_is_active, cfg_get, get_recording_service_status, set_recording

bp = Blueprint("liveview", __name__)
HLS_DIR = Path("/tmp/picam_hls/")
HLS_DIR.mkdir(parents=True, exist_ok=True)

# Global recorder instance (set by main app)
_recorder_instance = None

def set_recorder_instance(recorder):
    """Set the global recorder instance for live view access"""
    global _recorder_instance
    _recorder_instance = recorder


def _mjpeg_direct_from_queue(recorder):
    """
    Stream MJPEG directly from recorder's frame queue
    LOWEST LATENCY METHOD - ~30-100ms delay
    """
    # Enable streaming on recorder
    recorder.enable_live_streaming()
    
    try:
        print("📹 Direct frame queue streaming started")
        frame_count = 0
        
        while True:
            # Get frame from queue (1 second timeout)
            frame = recorder.get_live_frame(timeout=1.0)
            
            if frame is None:
                # No frame available - check if recording is still active
                if not recorder.is_recording:
                    print("⚠ Recording stopped, ending stream")
                    break
                continue
            
            # Encode frame to JPEG
            try:
                ret, jpeg = cv2.imencode(
                    '.jpg', 
                    frame, 
                    [cv2.IMWRITE_JPEG_QUALITY, 85]  # Good balance of quality/speed
                )
                
                if not ret:
                    continue
                
                # Yield as MJPEG frame
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + 
                       jpeg.tobytes() + 
                       b'\r\n')
                
                frame_count += 1
                
            except Exception as e:
                print(f"⚠ Frame encode error: {e}")
                continue
                
    except Exception as e:
        print(f"⚠ Direct streaming error: {e}")
    finally:
        # Disable streaming on recorder
        recorder.disable_live_streaming()
        print(f"✓ Direct streaming ended (sent {frame_count} frames)")


def _mjpeg_from_hls_optimized():
    """
    Convert HLS stream to MJPEG - OPTIMIZED
    REMOVED: -re flag (major cause of delay!)
    Latency: ~1-2 seconds (backup method)
    """
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        # CRITICAL: Removed "-re" flag to eliminate artificial delay
        "-i", str(HLS_DIR / "live.m3u8"),
        "-f", "mpjpeg",
        "-q:v", "5",  # Lower quality = faster (5 instead of 7)
        "-pix_fmt", "yuvj422p",
        "-boundary_tag", "frame",
        "-"
    ]
    
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=0)
    
    try:
        chunk_count = 0
        while True:
            chunk = p.stdout.read(4096)
            if not chunk:
                break
            yield chunk
            chunk_count += 1
    finally:
        try:
            p.kill()
            p.wait(timeout=2)
            print(f"✓ HLS transcoding ended (sent {chunk_count} chunks)")
        except:
            pass


def _mjpeg_from_v4l2(dev: str, fmt: str):
    """
    Stream MJPEG directly from camera (DEPRECATED - don't use)
    This bypasses recorder and creates conflicts
    """
    print("⚠ Direct V4L2 streaming is deprecated and may cause conflicts")
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
    """Serve HLS playlist file - only when recording is active"""
    # Only serve HLS when recorder is running
    if not rec_is_active():
        return Response(
            "#EXTM3U\n"
            "#EXT-X-VERSION:3\n"
            "#EXT-X-TARGETDURATION:2\n"
            "#EXT-X-MEDIA-SEQUENCE:0\n"
            "#EXT-X-ENDLIST\n",
            mimetype="application/vnd.apple.mpegurl",
            status=404
        )
    
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
    """Serve HLS segment files (.ts) - only when recording is active"""
    # Only serve HLS segments when recorder is running
    if not rec_is_active():
        from flask import abort
        abort(404)
    
    return send_from_directory(HLS_DIR, name)


@bp.get("/live.mjpg")
def live_mjpg():
    """
    Serve live MJPEG stream - OPTIMIZED FOR LOW LATENCY
    
    Priority order:
    1. Direct frame queue (LOWEST latency ~30-100ms) - RECOMMENDED
    2. HLS transcoding (Higher latency ~1-2s) - FALLBACK
    
    Usage:
    - Set use_direct=1 for lowest latency (default)
    - Set use_direct=0 to force HLS method
    
    Example: /live.mjpg?use_direct=1
    """
    from flask import request
    
    # Only allow live view when recorder is running
    if not rec_is_active():
        error_response = b'--frame\r\nContent-Type: text/plain\r\n\r\nLive view only available when recording is active\r\n'
        return Response(
            error_response,
            mimetype="multipart/x-mixed-replace; boundary=frame"
        )
    
    # Check if direct streaming is requested (default: yes)
    use_direct = request.args.get('use_direct', '1') == '1'
    
    # Method 1: Direct frame queue streaming (LOWEST LATENCY - RECOMMENDED)
    if use_direct and _recorder_instance:
        try:
            print("📹 Using direct frame queue for MJPEG (lowest latency)")
            gen = _mjpeg_direct_from_queue(_recorder_instance)
            return Response(
                gen,
                mimetype="multipart/x-mixed-replace; boundary=frame"
            )
        except Exception as e:
            print(f"⚠ Direct streaming failed: {e}, falling back to HLS")
            # Fall through to HLS method
    
    # Method 2: HLS transcoding (FALLBACK - higher latency but more compatible)
    # Wait for HLS to be ready (with timeout)
    if not _wait_for_hls_ready(timeout=5.0):
        error_response = b'--frame\r\nContent-Type: text/plain\r\n\r\nHLS stream not ready. Please wait a moment and refresh.\r\n'
        return Response(
            error_response,
            mimetype="multipart/x-mixed-replace; boundary=frame"
        )
    
    print("📹 Using HLS source for MJPEG (higher latency - fallback)")
    gen = _mjpeg_from_hls_optimized()
    
    return Response(
        gen,
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )


@bp.get("/live_hls.mjpg")
def live_hls_mjpg():
    """
    Force HLS-based MJPEG streaming (for testing/compatibility)
    Higher latency (~1-2s) but more reliable for some clients
    """
    # Only allow live view when recorder is running
    if not rec_is_active():
        error_response = b'--frame\r\nContent-Type: text/plain\r\n\r\nLive view only available when recording is active\r\n'
        return Response(
            error_response,
            mimetype="multipart/x-mixed-replace; boundary=frame"
        )
    
    # Wait for HLS to be ready
    if not _wait_for_hls_ready(timeout=5.0):
        error_response = b'--frame\r\nContent-Type: text/plain\r\n\r\nHLS stream not ready. Please wait a moment and refresh.\r\n'
        return Response(
            error_response,
            mimetype="multipart/x-mixed-replace; boundary=frame"
        )
    
    print("📹 Using HLS source for MJPEG (forced)")
    gen = _mjpeg_from_hls_optimized()
    
    return Response(
        gen,
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )


@bp.get("/streaming_status")
def streaming_status():
    """Get current streaming status"""
    from flask import jsonify
    
    status = {
        'recording_active': rec_is_active(),
        'recorder_available': _recorder_instance is not None,
        'hls_available': False,
        'direct_streaming_available': False
    }
    
    # Check HLS availability
    m3u8_file = HLS_DIR / "live.m3u8"
    if m3u8_file.exists() and m3u8_file.stat().st_size > 0:
        status['hls_available'] = True
    
    # Check direct streaming availability
    if _recorder_instance and _recorder_instance.is_recording:
        status['direct_streaming_available'] = True
        status['streaming_enabled'] = _recorder_instance.streaming_enabled
    
    return jsonify(status)