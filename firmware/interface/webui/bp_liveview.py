from __future__ import annotations
from flask import Blueprint, Response, request, abort, render_template_string
from flask_socketio import emit
from werkzeug.middleware.proxy_fix import ProxyFix
import time
from functools import wraps
import re
import socketio as sio_client  # Socket.IO client để kết nối đến recorder

# ============================================================
# CONFIG & SECURITY
# ============================================================
RECORDER_TIMEOUT=100
# Allowed IP ranges (adjust these to match your legitimate client IPs)
ALLOWED_IPS = [
    '127.0.0.1',      # localhost
    '192.168.0.0/16', # typical LAN
    '10.0.0.0/8',     # private network
    '172.16.0.0/12'   # private network
]

def is_ip_allowed(ip: str) -> bool:
    """Check if IP is in allowed ranges - DISABLED cho public access"""
    # Cho phép tất cả IP truy cập (vì đã có router firewall bảo vệ)
    return True

def validate_request(f):
    """Decorator to validate requests - SIMPLIFIED cho public access"""
    @wraps(f)
    def decorated(*args, **kwargs):
        # Bỏ qua IP check và User-Agent check
        # Chỉ giữ lại basic path validation
        path = request.path
        if re.search(r'[;\'"]|\\x[0-9a-f]{2}', path, re.I):
            abort(400, "Invalid characters in request")
            
        return f(*args, **kwargs)
    return decorated

bp = Blueprint("liveview", __name__)

# URL của recorder service WebSocket (local only - không public)
RECORDER_WS_URL = "http://localhost:5000"  # WebSocket endpoint

# SocketIO client để kết nối đến recorder
recorder_client = None

def setup_socketio_proxy(socketio_server):
    """Setup WebSocket proxy: WebUI (port 8080) <-> Recorder (port 5000)"""
    global recorder_client
    
    print("🔧 Setting up WebSocket proxy...")
    
    # Tạo Socket.IO client kết nối đến recorder
    recorder_client = sio_client.Client(reconnection=True, reconnection_attempts=0)
    
    @recorder_client.on('connect')
    def on_recorder_connect():
        print("✅ Proxy connected to recorder (port 5000)")
    
    @recorder_client.on('disconnect')
    def on_recorder_disconnect():
        print("❌ Proxy disconnected from recorder")
    
    @recorder_client.on('video_frame')
    def on_recorder_video_frame(data):
        """Forward video frames từ recorder đến tất cả WebUI clients"""
        socketio_server.emit('video_frame', data, namespace='/')
    
    # Kết nối đến recorder
    try:
        recorder_client.connect(RECORDER_WS_URL, transports=['websocket'])
        print(f"📡 WebSocket proxy started: WebUI (8080) -> Recorder (5000)")
    except Exception as e:
        print(f"⚠️ Could not connect to recorder: {e}")
    
    # Handlers cho WebUI clients
    @socketio_server.on('connect')
    def handle_client_connect():
        print(f"👥 Client connected to WebUI proxy")
    
    @socketio_server.on('disconnect')
    def handle_client_disconnect():
        print(f"👋 Client disconnected from WebUI proxy")

# ============================================================
# ROUTES
# ============================================================

@bp.get("/live")
@validate_request
def live_video():
    """Return HTML page with WebSocket video player - kết nối trực tiếp đến recorder WS."""
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Live Camera Stream</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <script src="/static/socket.io.js"></script>
        <style>
            body { 
                margin: 0; 
                background: #000; 
                text-align: center;
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                color: #eee;
            }
            h2 {
                margin: 20px 0 10px 0;
                font-size: 24px;
            }
            .status {
                color: #0f0;
                font-size: 14px;
                margin: 10px 0;
            }
            .error {
                color: #f00;
                font-size: 14px;
                margin: 10px 0;
            }
            #videoStream { 
                width: 90%; 
                max-width: 1280px;
                max-height: 80vh; 
                border: 2px solid #444;
                border-radius: 8px;
                margin: 20px auto;
                display: block;
                box-shadow: 0 4px 20px rgba(0,0,0,0.5);
            }
            .info {
                font-size: 12px;
                color: #999;
                margin-top: 10px;
            }
        </style>
    </head>
    <body>
        <h2>📷 Live Camera Stream</h2>
        <p class="status" id="status">● Connecting to recorder...</p>
        <img id="videoStream" src="" alt="Live Stream">
        <p class="info">Stream via WebSocket from recorder service (port 5000)</p>
        
        <script>
            const statusEl = document.getElementById('status');
            const videoEl = document.getElementById('videoStream');
            
            // Kết nối đến WebUI WebSocket proxy (cùng server, không cần port 5000)
            const socket = io({
                transports: ['websocket'],
                reconnection: true,
                reconnectionDelay: 1000,
                reconnectionDelayMax: 5000,
                reconnectionAttempts: Infinity
            });
            
            socket.on('connect', () => {
                console.log('✅ Connected to WebUI proxy');
                statusEl.className = 'status';
                statusEl.textContent = '● Streaming via WebSocket proxy (port 8080)';
            });
            
            socket.on('disconnect', () => {
                console.log('❌ Disconnected from WebUI proxy');
                statusEl.className = 'error';
                statusEl.textContent = '✖ Disconnected from server';
                videoEl.src = '';
            });
            
            socket.on('video_frame', (data) => {
                // Nhận base64 frame từ WebUI proxy
                videoEl.src = 'data:image/jpeg;base64,' + data.frame;
            });
            
            socket.on('connect_error', (error) => {
                console.error('Connection error:', error);
                statusEl.className = 'error';
                statusEl.textContent = '✖ Cannot connect to server';
            });
        </script>
    </body>
    </html>
    """
    return Response(html, mimetype='text/html')

@bp.get("/live/stream")
@validate_request
def live_stream_embed():
    """Return embeddable WebSocket stream page for iframe/img tag."""
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <script src="/static/socket.io.js"></script>
        <style>
            * { margin: 0; padding: 0; }
            body { 
                background: #000; 
                overflow: hidden;
                width: 100vw;
                height: 100vh;
            }
            #videoStream { 
                width: 100%;
                height: 100%;
                object-fit: contain;
                display: block;
                max-width: 100%;
                max-height: 100%;
            }
        </style>
    </head>
    <body>
        <img id="videoStream" src="" alt="Live Stream">
        
        <script>
            const videoEl = document.getElementById('videoStream');
            
            // Kết nối đến WebUI WebSocket proxy (cùng server, không cần port 5000)
            const socket = io({
                transports: ['websocket'],
                reconnection: true,
                reconnectionDelay: 1000,
                reconnectionDelayMax: 5000,
                reconnectionAttempts: Infinity
            });
            
            socket.on('connect', () => {
                console.log('✅ Connected to WebUI proxy');
            });
            
            socket.on('disconnect', () => {
                console.log('❌ Disconnected from WebUI proxy');
                videoEl.src = '';
            });
            
            socket.on('video_frame', (data) => {
                // Nhận base64 frame từ WebUI proxy
                videoEl.src = 'data:image/jpeg;base64,' + data.frame;
            });
            
            socket.on('connect_error', (error) => {
                console.error('Connection error:', error);
            });
        </script>
    </body>
    </html>
    """
    return Response(html, mimetype='text/html')

@bp.get("/stream/health")
@validate_request
def stream_health():
    """Endpoint kiểm tra trạng thái stream."""
    # Kiểm tra recorder có sẵn không
    try:
        import requests
        response = requests.head("http://localhost:5000", timeout=2)
        is_healthy = response.status_code == 200
    except:
        is_healthy = False
    
    health = {
        "status": "healthy" if is_healthy else "degraded",
        "recorder_url": RECORDER_WS_URL,
        "stream_type": "websocket"
    }
    
    response = Response(
        response=str(health),
        status=200 if is_healthy else 503,
        mimetype='application/json'
    )
    
    # Add security headers
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Cache-Control'] = 'no-store, no-cache'
    response.headers['X-Frame-Options'] = 'DENY'
    return response