from __future__ import annotations
from flask import Blueprint, Response, request, abort, render_template_string
from functools import wraps
import re
import socket
import time

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

# Recorder TCP settings (match with recorder)
TCP_HOST = '127.0.0.1'
TCP_PORT = 9000

# Template HTML đơn giản
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Live Camera Stream</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body { text-align: center; background: #111; color: #eee; font-family: sans-serif; margin: 0; }
        img { width: 80%; max-width: 1280px; border: 4px solid #444; border-radius: 10px; margin: 20px auto; display: block; box-shadow: 0 4px 20px rgba(0,0,0,0.5); background: #000; }
        h2 { margin: 20px 0 10px 0; font-size: 24px; }
        .status { color: #0f0; font-size: 14px; margin: 10px 0; }
        .error { color: #f00; font-size: 14px; margin: 10px 0; }
        .info { font-size: 12px; color: #999; margin-top: 10px; }
    </style>
</head>
<body>
    <h2>📷 Live Camera Stream (MJPEG)</h2>
    <p class="status" id="status">● Connecting...</p>
    <img id="videoStream" src="{{ url_for('video_feed') }}" alt="Live Stream">
    <p class="info">MJPEG stream from recorder service (via TCP proxy)</p>
    <script>
        const statusEl = document.getElementById('status');
        const img = document.getElementById('videoStream');
        img.onerror = function() {
            statusEl.className = 'error';
            statusEl.textContent = '✖ Stream error - retrying...';
            setTimeout(() => { location.reload(); }, 3000);
        };
        img.onload = function() {
            statusEl.className = 'status';
            statusEl.textContent = '● Streaming (MJPEG)';
        };
    </script>
</body>
</html>
"""

def connect_to_stream():
    """Connect to recorder's TCP stream"""
    while True:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect((TCP_HOST, TCP_PORT))
            return sock
        except Exception as e:
            time.sleep(5)
            continue

def gen_frames():
    """Generate MJPEG frames from TCP stream"""
    sock = None
    while True:
        if sock is None:
            sock = connect_to_stream()
        
        try:
            # Receive length
            len_bytes = sock.recv(4)
            if len_bytes == b'':
                sock.close()
                sock = None
                continue
            
            length = int.from_bytes(len_bytes, 'big')
            if length <= 0:
                continue
            
            # Receive JPEG data
            jpeg = b''
            while len(jpeg) < length:
                chunk = sock.recv(min(4096, length - len(jpeg)))
                if not chunk:
                    sock.close()
                    sock = None
                    break
                jpeg += chunk
            
            if len(jpeg) == length:
                # Yield as MJPEG part
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + jpeg + b'\r\n')
        except Exception:
            if sock:
                sock.close()
            sock = None
            time.sleep(1)

# ============================================================
# ROUTES
# ============================================================

@bp.route("/live", methods=["GET"])
@validate_request
def live_video():
    """Return HTML page with MJPEG video stream"""
    return render_template_string(HTML_TEMPLATE)

@bp.route('/video_feed', methods=['GET'])
@validate_request
def video_feed():
    """MJPEG stream endpoint"""
    return Response(gen_frames(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

@bp.route('/live/video_feed', methods=['GET'])
@validate_request
def live_video_feed_alias():
    return video_feed()