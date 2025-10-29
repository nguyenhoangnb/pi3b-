#!/usr/bin/env python3
from flask import Flask, Response, abort, render_template_string, send_from_directory, request
from functools import wraps
from pathlib import Path
import subprocess
import re
import os
import threading
import time

# ================= CONFIG =================
VIDEO_DEVICE = "/dev/video0"
FRAME_RATE = 10
RESOLUTION = "640x480"
HLS_DIR = Path("/tmp/picam_hls")
HLS_FILE = HLS_DIR / "stream.m3u8"

app = Flask(__name__)
HLS_DIR.mkdir(parents=True, exist_ok=True)

# ================= SECURITY =================
def validate_request(f):
    """Ngăn path traversal"""
    @wraps(f)
    def decorated(*args, **kwargs):
        path = request.path
        if re.search(r'(\.\.|%2e%2e|%252e%252e|[\x00-\x1f\x7f]|[\'";]|\\x[0-9a-f]{2})', path, re.I):
            abort(400, "Invalid characters in request path")
        return f(*args, **kwargs)
    return decorated

# ================= FFmpeg =================
ffmpeg_process = None
ffmpeg_lock = threading.Lock()

def start_ffmpeg():
    """Khởi động FFmpeg để stream HLS"""
    if not HLS_DIR.exists():
        HLS_DIR.mkdir(parents=True)
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-f", "v4l2",
        "-framerate", str(FRAME_RATE),
        "-video_size", RESOLUTION,
        "-i", VIDEO_DEVICE,
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-tune", "zerolatency",
        "-pix_fmt", "yuv420p",
        "-f", "hls",
        "-hls_time", "2",
        "-hls_list_size", "5",
        "-hls_flags", "delete_segments+append_list",
        str(HLS_FILE)
    ]
    print("🎬 Starting FFmpeg:", " ".join(cmd))
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def ffmpeg_watchdog():
    global ffmpeg_process
    delay = 1  # initial restart delay
    while True:
        with ffmpeg_lock:
            if ffmpeg_process is None or ffmpeg_process.poll() is not None:
                print(f"🔁 FFmpeg not running or exited -> (re)starting in {delay}s...")
                time.sleep(delay)
                ffmpeg_process = start_ffmpeg()
                delay = min(delay * 2, 10)  # tăng dần nếu crash liên tục
            else:
                delay = 1  # reset delay nếu FFmpeg chạy bình thường
        time.sleep(2)

threading.Thread(target=ffmpeg_watchdog, daemon=True).start()

# ================= ROUTES =================
@app.route("/")
def root():
    return '<h3 style="color:white;text-align:center;background:black;padding:20px">Go to <a href="/live">/live</a> to view stream</h3>'

@app.route("/live")
@validate_request
def live_video():
    """Giao diện HLS HTML"""
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
            const hlsUrl = '/hls/stream.m3u8';
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
    """Phục vụ HLS file"""
    file_path = HLS_DIR / filename
    if not file_path.exists() or not file_path.is_file():
        abort(404, "File not found")

    mimetype = "application/vnd.apple.mpegurl" if filename.endswith(".m3u8") else "video/mp2t"
    return send_from_directory(HLS_DIR, filename, mimetype=mimetype)

# ================= MAIN =================
if __name__ == "__main__":
    print(f"🌐 Flask HLS stream running at: http://<IP>:8080/live")
    print(f"💾 HLS output: {HLS_DIR}")
    app.run(host="0.0.0.0", port=8080, debug=False)
