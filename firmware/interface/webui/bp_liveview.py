from __future__ import annotations
from flask import Blueprint, Response, request, abort, render_template_string, send_from_directory
from functools import wraps
from pathlib import Path
import re

# ============================================================
# CONFIG
# ============================================================

bp = Blueprint("liveview", __name__)

# Thư mục chứa các file HLS (do FFmpeg sinh ra)
HLS_DIR = Path("/tmp/picam_hls")

# ============================================================
# SECURITY VALIDATION
# ============================================================

def validate_request(f):
    """Decorator để kiểm tra yêu cầu đầu vào tránh path traversal"""
    @wraps(f)
    def decorated(*args, **kwargs):
        path = request.path
        # Regex lọc các chuỗi độc hại
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
    """Giao diện HTML để xem video HLS"""
    hls_url = "/hls/stream.m3u8"
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Live Camera Stream</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <script src="/static/hls.min.js"></script>
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
        <p style="font-size:12px;color:#999;">HLS served from /tmp/picam_hls</p>

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
    """Phục vụ file HLS (m3u8, ts) từ thư mục /tmp/picam_hls"""
    file_path = HLS_DIR / filename
    if not file_path.exists() or not file_path.is_file():
        abort(404, "File not found")

    # Trả về file đúng MIME type
    if filename.endswith(".m3u8"):
        mimetype = "application/vnd.apple.mpegurl"
    elif filename.endswith(".ts"):
        mimetype = "video/mp2t"
    else:
        mimetype = "application/octet-stream"

    return send_from_directory(HLS_DIR, filename, mimetype=mimetype)
