from __future__ import annotations
from flask import Blueprint, Response, request, abort, render_template_string
from functools import wraps
import re
from pathlib import Path

# ============================================================
# CONFIG & SECURITY
# ============================================================

def validate_request(f):
    """Decorator to validate requests"""
    @wraps(f)
    def decorated(*args, **kwargs):
        # Basic path validation - improved regex for common attacks
        path = request.path
        if re.search(r'(\.\.|%2e%2e|%252e%252e|[\x00-\x1f\x7f]|[\'";]|\\x[0-9a-f]{2})', path, re.I):
            abort(400, "Invalid characters in request")
            
        return f(*args, **kwargs)
    return decorated

bp = Blueprint("liveview", __name__)

# HLS directory (local files)
HLS_DIR = "/tmp/picam_hls"

# ============================================================
# ROUTES
# ============================================================

@bp.get("/live")
@validate_request
def live_video():
    """Return HTML page with HLS video player"""
    hls_url = f"{HLS_DIR}/stream.m3u8"
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Live Camera Stream</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <script src="/static/hls.min.js"></script>
        <style>
            body {{ margin: 0; background: #000; text-align: center; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; color: #eee; }}
            h2 {{ margin: 20px 0 10px 0; font-size: 24px; }}
            .status {{ color: #0f0; font-size: 14px; margin: 10px 0; }}
            .error {{ color: #f00; font-size: 14px; margin: 10px 0; }}
            #videoStream {{ width: 90%; max-width: 1280px; max-height: 80vh; border: 2px solid #444; border-radius: 8px; margin: 20px auto; display: block; box-shadow: 0 4px 20px rgba(0,0,0,0.5); background: #000; }}
            .info {{ font-size: 12px; color: #999; margin-top: 10px; }}
        </style>
    </head>
    <body>
        <h2>📷 Live Camera Stream (HLS)</h2>
        <p class="status" id="status">● Connecting...</p>
        <video id="videoStream" controls autoplay muted></video>
        <p class="info">HLS stream from local files in /tmp/picam_hls</p>
        <script>
            const statusEl = document.getElementById('status');
            const video = document.getElementById('videoStream');
            const hlsUrl = '{hls_url}';
            if (Hls.isSupported()) {{
                const hls = new Hls({{ maxBufferLength: 4, maxMaxBufferLength: 10, lowLatencyMode: true, enableWorker: true }});
                hls.loadSource(hlsUrl);
                hls.attachMedia(video);
                hls.on(Hls.Events.MANIFEST_PARSED, function() {{
                    statusEl.className = 'status';
                    statusEl.textContent = '● Streaming (HLS)';
                    video.play().catch(e => {{ statusEl.textContent = '● Ready (click play)'; }});
                }});
                hls.on(Hls.Events.ERROR, function(event, data) {{
                    if (data.fatal) {{
                        statusEl.className = 'error';
                        switch(data.type) {{
                            case Hls.ErrorTypes.NETWORK_ERROR:
                                statusEl.textContent = '✖ Network error, retrying...';
                                setTimeout(() => hls.startLoad(), 1000);
                                break;
                            case Hls.ErrorTypes.MEDIA_ERROR:
                                statusEl.textContent = '✖ Media error, recovering...';
                                hls.recoverMediaError();
                                break;
                            default:
                                statusEl.textContent = '✖ Fatal error';
                                hls.destroy();
                                break;
                        }}
                    }}
                }});
            }} else if (video.canPlayType('application/vnd.apple.mpegurl')) {{
                video.src = hlsUrl;
                video.addEventListener('loadedmetadata', function() {{
                    statusEl.className = 'status';
                    statusEl.textContent = '● Streaming (Native HLS)';
                    video.play().catch(e => {{ statusEl.textContent = '● Ready (click play)'; }});
                }});
            }} else {{
                statusEl.className = 'error';
                statusEl.textContent = '✖ HLS not supported in this browser';
            }}
        </script>
    </body>
    </html>
    """
    return render_template_string(html)