from __future__ import annotations
from flask import Blueprint, Response, send_from_directory
from pathlib import Path
import ffmpeg
import time
import select
from .helpers import rec_is_active, get_recorder

bp = Blueprint("liveview", __name__)
HLS_DIR = Path("/tmp/picam_hls/")
HLS_DIR.mkdir(parents=True, exist_ok=True)

import subprocess
def _mjpeg_from_hls():
    cmd = ["ffmpeg","-hide_banner","-loglevel","error","-re","-i", str(HLS_DIR/"live.m3u8"),
           "-f","mpjpeg","-q:v","7","-pix_fmt","yuvj422p","-boundary_tag","frame","-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=0)
    try:
        while True:
            chunk = p.stdout.read(4096)
            if not chunk: break
            yield chunk
    finally:
        try: p.kill()
        except: pass

def _ensure_recorder_running() -> bool:
    """Check if recorder is active (HLS generator)."""
    recorder = get_recorder()
    if recorder is None:
        print("⚠ Recorder not available")
        return False

    if not rec_is_active():
        print("⚠ Recorder not running, cannot stream HLS")
        return False

    return True


def _wait_for_hls_ready(timeout: float = 2.0) -> bool:
    """Wait for HLS playlist to be ready."""
    m3u8_file = HLS_DIR / "live.m3u8"
    start = time.time()
    while time.time() - start < timeout:
        if m3u8_file.exists() and m3u8_file.stat().st_size > 0:
            return True
        time.sleep(0.2)
    print("⚠ HLS playlist not ready")
    return False


@bp.get("/hls/live.m3u8")
def hls_playlist():
    """Serve HLS playlist."""
    m3u8_file = HLS_DIR / "live.m3u8"
    if not m3u8_file.exists():
        return Response(
            "#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:2\n#EXT-X-MEDIA-SEQUENCE:0\n",
            mimetype="application/vnd.apple.mpegurl"
        )

    resp = send_from_directory(HLS_DIR, "live.m3u8")
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp


@bp.get("/hls/<path:name>")
def hls_segment(name: str):
    """Serve HLS segment files (.ts)."""
    resp = send_from_directory(HLS_DIR, name)
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp


@bp.get("/live.mjpg")
def live_mjpeg():
    """Serve MJPEG live stream converted from HLS."""
    # if not _ensure_recorder_running():
    #     return Response("Recorder not active. Start recording first.", status=503)

    if not _wait_for_hls_ready(2.0):
        return Response("HLS not ready.", status=503)

    print("📹 Streaming MJPEG from HLS ...")
    gen = _mjpeg_from_hls()
    return Response(gen, mimetype="multipart/x-mixed-replace; boundary=frame")
