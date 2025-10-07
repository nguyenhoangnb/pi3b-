from __future__ import annotations
from flask import Blueprint, Response, send_from_directory
from pathlib import Path
import subprocess
from .helpers import rec_is_active, cfg_get

bp = Blueprint("liveview", __name__)

HLS_DIR = Path("/tmp/picam_hls")

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

def _mjpeg_from_v4l2(dev: str, fmt: str):
    cmd = ["ffmpeg","-hide_banner","-loglevel","error","-f","v4l2","-input_format","yuyv422",
           "-framerate","15","-video_size",fmt,"-i",dev,"-f","mpjpeg","-q:v","7","-pix_fmt","yuvj422p","-boundary_tag","frame","-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=0)
    try:
        while True:
            chunk = p.stdout.read(4096)
            if not chunk: break
            yield chunk
    finally:
        try: p.kill()
        except: pass

@bp.get("/hls/live.m3u8")
def hls_playlist():
    m3u = HLS_DIR / "live.m3u8"
    if not m3u.exists():
        return Response("#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:2\n#EXT-X-MEDIA-SEQUENCE:0\n", mimetype="application/vnd.apple.mpegurl")
    return send_from_directory(HLS_DIR, "live.m3u8")

@bp.get("/hls/<path:name>")
def hls_files(name: str):
    return send_from_directory(HLS_DIR, name)

@bp.get("/live.mjpg")
def live_mjpg():
    use_hls = rec_is_active() and (HLS_DIR/"live.m3u8").exists()
    gen = _mjpeg_from_hls() if use_hls else _mjpeg_from_v4l2(cfg_get("video.v4l2_device","/dev/video0"),
                                                             cfg_get("video.v4l2_format","1280x720"))
    return Response(gen, mimetype="multipart/x-mixed-replace; boundary=frame")
