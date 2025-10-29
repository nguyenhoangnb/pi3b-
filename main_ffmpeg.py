#!/usr/bin/env python3
from __future__ import annotations
from flask import Flask, Response, abort, render_template_string, send_from_directory, request
from functools import wraps
from pathlib import Path
import subprocess
import re
import os
import threading
import time

# ============================================================
# CONFIG
# ============================================================

VIDEO_DEVICE = "/dev/video0"
FRAME_RATE = "15"
RESOLUTION = "640x480"
HLS_DIR = Path("/tmp/picam_hls")

app = Flask(__name__)
os.makedirs(HLS_DIR, exist_ok=True)

# ============================================================
# SECURITY VALIDATION
# ============================================================

def validate_request(f):
    """Decorator kiểm tra path để tránh tấn công path traversal"""
    @wraps(f)
    def decorated(*args, **kwargs):
        path = request.path
        if re.search(r'(\.\.|%2e%2e|%252e%252e|[\x00-\x1f\x7f]|[\'";]|\\x[0-9a-f]{2})', path, re.I):
            abort(400, "Invalid characters in request path")
        return f(*args, **kwargs)
    return decorated

# ============================================================
# FFmpeg PROCESS
# ============================================================

def start_ffmpeg():
    """Chạy FFmpeg để stream từ camera ra HLS"""
    ffmpeg_cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-f", "v4l2",
        "-framerate", FRAME_RATE,
        "-video_size", RESOLUTION,
        "-i", VIDEO_DEVICE,
        "-vf", "drawtext=text='%{localtime\\:%Y-%m-%d %H\\\\\\:%M\\\\\\:%S}':x=10:y=10:fontcolor=white:fontsize=20",
        "-vcodec", "h264_v4l2m2m",
        "-preset", "ultrafast",
        "-tune", "zerolatency",
        "-pix_fmt", "yuv420p",
        "-f", "hls",
        "-hls_time", "2",
        "-hls_list_size", "5",
        "-hls_flags", "delete_segments+append_list",
        str(HLS_DIR / "stream.m3u8")
    ]
    print("🎬 Starting FFmpeg:", " ".join(ffmpeg_cmd))
    return subprocess.Popen(ffmpeg_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# Tự động đảm bảo FFmpeg luôn chạy
def ffmpeg_watchdog():
    process = None
    while True:
        if not process or process.poll() is not None:
            print("🔁 FFmpeg not running — restarting...")
            process = start_ffmpeg()
        time.sleep(5)

threading.Thread(target=ffmpeg_watchdog, daemon=True).start()

# ============================================================
# ROUTES
# ============================================================

@app.route("/")
def root():
    return '<h3 style="color:white;text-align:center;background:black;padding:20px">Go to <a href="/live">/live</a> to view stream</h3>'

@app.route("/live")
@validate_request
def live_video():
    """Giao diện HTML phát HLS"""
    hls_url = "/hls/stream.m3u8"
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>📷 Live Camera Stream (HLS)</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <script src="https://cdn.jsdelivr.net/npm/hls.js@latest"></script>
        <style>
            body {{ background:#000; color:#eee; text-align:center; font-family:sans-serif; }}
            video {{ width:90%; max-width:1280px; margin-top:20px; border-radius:8px; }}
            h2 {{ color:#0f0; }}
        </style>
    </head>
    <body>
        <h2>🎥 Live Camera Stream</h2>
        <p id="status">🔄 Connecting...</p>
        <video id="video" controls autoplay muted></video>
        <script>
            const video = document.getElementById('video');
            const statusEl = document.getElementById('status');
            const hlsUrl = '{hls_url}';
            if (Hls.isSupported()) {{
                const hls = new Hls({{ maxBufferLength:4, maxMaxBufferLength:8, lowLatencyMode:true }});
                hls.loadSource(hlsUrl);
                hls.attachMedia(video);
                hls.on(Hls.Events.MANIFEST_PARSED, () => {{
                    statusEl.textContent = "✅ Streaming (HLS)";
                    video.play().catch(() => statusEl.textContent="▶ Click Play to Start");
                }});
                hls.on(Hls.Events.ERROR, (event, data) => {{
                    console.warn("HLS error", data);
                    if (data.fatal) {{
                        statusEl.textContent = "✖ Error: " + data.type;
                        statusEl.style.color = "#f44";
                        hls.destroy();
                    }}
                }});
            }} else if (video.canPlayType('application/vnd.apple.mpegurl')) {{
                video.src = hlsUrl;
                video.addEventListener('loadedmetadata', () => video.play());
                statusEl.textContent = "✅ Streaming (Native HLS)";
            }} else {{
                statusEl.textContent = "✖ Browser not supported";
                statusEl.style.color = "#f44";
            }}
        </script>
    </body>
    </html>
    """
    return render_template_string(html)

@app.route("/hls/<path:filename>")
@validate_request
def serve_hls(filename):
    """Phục vụ file HLS (m3u8, ts)"""
    file_path = HLS_DIR / filename
    if not file_path.exists() or not file_path.is_file():
        abort(404, "File not found")

    if filename.endswith(".m3u8"):
        mimetype = "application/vnd.apple.mpegurl"
    elif filename.endswith(".ts"):
        mimetype = "video/mp2t"
    else:
        mimetype = "application/octet-stream"

    return send_from_directory(HLS_DIR, filename, mimetype=mimetype)

# ============================================================
# MAIN ENTRY
# ============================================================

if __name__ == "__main__":
    print(f"🌐 Flask HLS stream running at: http://<IP>:8080/live")
    print(f"💾 HLS output: {HLS_DIR}")
    app.run(host="0.0.0.0", port=8080, debug=False)
