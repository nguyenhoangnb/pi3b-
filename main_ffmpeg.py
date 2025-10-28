from __future__ import annotations
from flask import Flask, Blueprint, Response, request, abort, render_template_string, send_from_directory
from functools import wraps
from pathlib import Path
import subprocess
import os
import re

# ============================================================
# CẤU HÌNH
# ============================================================

app = Flask(__name__)
bp = Blueprint("liveview", __name__)

VIDEO_DEVICE = "/dev/video0"
HLS_DIR = Path("/tmp/picam_hls")
FRAME_RATE = "15"
RESOLUTION = "640x480"

os.makedirs(HLS_DIR, exist_ok=True)

# ============================================================
# BẢO MẬT REQUEST
# ============================================================

def validate_request(f):
    """Decorator để kiểm tra yêu cầu đầu vào tránh path traversal"""
    @wraps(f)
    def decorated(*args, **kwargs):
        path = request.path
        if re.search(r'(\.\.|%2e%2e|%252e%252e|[\x00-\x1f\x7f]|[\'";]|\\x[0-9a-f]{2})', path, re.I):
            abort(400, "Invalid characters in request path")
        return f(*args, **kwargs)
    return decorated

# ============================================================
# ROUTES
# ============================================================

@bp.get("/live")
@validate_request
def live_video():
    """Trang HTML xem HLS"""
    hls_url = "/hls/stream.m3u8"
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Live Camera Stream (HLS)</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <script src="https://cdn.jsdelivr.net/npm/hls.js@latest"></script>
        <style>
            body {{ margin: 0; background: #000; text-align: center; font-family: sans-serif; color: #eee; }}
            h2 {{ margin: 20px 0 10px; font-size: 24px; }}
            #videoStream {{ width: 90%; max-width: 1280px; max-height: 80vh; border-radius: 8px; margin: 20px auto; display: block; }}
            .status {{ color: #0f0; font-size: 14px; }}
            .error {{ color: #f00; font-size: 14px; }}
        </style>
    </head>
    <body>
        <h2>📷 Live Camera Stream (HLS)</h2>
        <p id="status">● Connecting...</p>
        <video id="videoStream" controls autoplay muted></video>
        <p style="font-size:12px;color:#999;">HLS: {HLS_DIR}</p>

        <script>
            const statusEl = document.getElementById('status');
            const video = document.getElementById('videoStream');
            const hlsUrl = '{hls_url}';
            if (Hls.isSupported()) {{
                const hls = new Hls({{ maxBufferLength: 4, maxMaxBufferLength: 10, lowLatencyMode: true }});
                hls.loadSource(hlsUrl);
                hls.attachMedia(video);
                hls.on(Hls.Events.MANIFEST_PARSED, function() {{
                    statusEl.textContent = '● Streaming (HLS)';
                    statusEl.className = 'status';
                    video.play().catch(e => statusEl.textContent = '● Ready (click play)');
                }});
                hls.on(Hls.Events.ERROR, function(event, data) {{
                    if (data.fatal) {{
                        statusEl.className = 'error';
                        statusEl.textContent = '✖ Error: ' + data.type;
                    }}
                }});
            }} else if (video.canPlayType('application/vnd.apple.mpegurl')) {{
                video.src = hlsUrl;
                video.addEventListener('loadedmetadata', () => video.play());
                statusEl.textContent = '● Streaming (Native HLS)';
            }} else {{
                statusEl.textContent = '✖ HLS not supported in this browser';
                statusEl.className = 'error';
            }}
        </script>
    </body>
    </html>
    """
    return render_template_string(html)


@bp.route("/hls/<path:filename>")
@validate_request
def serve_hls(filename):
    """Trả file HLS (.m3u8, .ts)"""
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

app.register_blueprint(bp)

# ============================================================
# ROUTE GỐC (REDIRECT)
# ============================================================

@app.route("/")
def root():
    """Chuyển hướng từ / sang /live"""
    return "<meta http-equiv='refresh' content='0; url=/live'>"

# ============================================================
# KHỞI ĐỘNG FFMPEG
# ============================================================

def start_ffmpeg():
    os.system("pkill -f 'ffmpeg.*picam_hls' || true")

    ffmpeg_cmd = [
        "ffmpeg",
        "-f", "v4l2",
        "-framerate", FRAME_RATE,
        "-video_size", RESOLUTION,
        "-i", VIDEO_DEVICE,
        "-vf", "drawtext=text='%{localtime\\:%Y-%m-%d %H\\\\\\:%M\\\\\\:%S}':x=10:y=10:fontcolor=white:fontsize=20",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-tune", "zerolatency",
        "-f", "hls",
        "-hls_time", "3",
        "-hls_list_size", "5",
        "-hls_flags", "delete_segments+append_list",
        "-hls_allow_cache", "0",
        str(HLS_DIR / "stream.m3u8")
    ]

    print("🎬 Khởi động FFmpeg stream...")
    subprocess.Popen(ffmpeg_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    start_ffmpeg()
    print(f"🌐 Mở trình duyệt tại: http://<IP_RaspberryPi>:5000/  (hoặc /live)")
    print(f"💾 HLS tạm tại: {HLS_DIR}")
    app.run(host="0.0.0.0", port=8080, debug=False)
