from __future__ import annotations
from flask import Blueprint, Response, request, abort
from werkzeug.middleware.proxy_fix import ProxyFix
import time
import requests
import threading
import queue
from functools import wraps
import re

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
    """Check if IP is in allowed ranges"""
    from ipaddress import ip_address, ip_network
    if not ip:
        return False
    try:
        client_ip = ip_address(ip)
        return any(client_ip in ip_network(allowed) for allowed in ALLOWED_IPS)
    except ValueError:
        return False

def validate_request(f):
    """Decorator to validate requests"""
    @wraps(f)
    def decorated(*args, **kwargs):
        # Get real IP even behind proxy
        client_ip = request.remote_addr
        
        # Basic request validation
        if not is_ip_allowed(client_ip):
            abort(403, "IP not allowed")
            
        # Validate User-Agent
        user_agent = request.headers.get('User-Agent', '')
        if not user_agent or len(user_agent) < 5:
            abort(400, "Invalid User-Agent")
            
        # Block suspicious patterns
        path = request.path
        if re.search(r'[;\'"]|\\x[0-9a-f]{2}|%[0-9a-f]{2}', path, re.I):
            abort(400, "Invalid characters in request")
            
        return f(*args, **kwargs)
    return decorated

bp = Blueprint("liveview", __name__)

# WebSocket URL của recorder service
RECORDER_WS_URL = "ws://localhost:5000"

# ============================================================
# MJPEG PROXY CLASS
# ============================================================

class MjpegProxy:
    def __init__(self, recorder_url=RECORDER_WS_URL):
        self.recorder_url = recorder_url
        self.queue = queue.Queue(maxsize=30)
        self.stop_event = threading.Event()
        self.thread = None
        self.lock = threading.Lock()
        self.active_clients = 0
        self.is_streaming = False

    def start(self):
        """Khởi động proxy thread để đọc từ recorder."""
        with self.lock:
            if self.thread and self.thread.is_alive():
                return  # Đã chạy rồi
            print("🚀 Bắt đầu MJPEG proxy (từ recorder)...")
            self.stop_event.clear()
            self.thread = threading.Thread(target=self._proxy_recorder, daemon=True)
            self.thread.start()

    def _proxy_recorder(self):
        """Luồng đọc MJPEG từ recorder và cache frame."""
        print("📡 Kết nối đến recorder MJPEG stream...")
        
        retry_count = 0
        max_retries = 10
        
        while retry_count < max_retries and not self.stop_event.is_set():
            try:
                # Kết nối đến recorder
                response = requests.get(
                    self.recorder_url,
                    stream=True,
                    timeout=RECORDER_TIMEOUT
                )
                
                if response.status_code != 200:
                    print(f"❌ Recorder trả về status {response.status_code}")
                    retry_count += 1
                    time.sleep(2)
                    continue
                
                print(f"✅ Kết nối recorder thành công (status 200)")
                self.is_streaming = True
                retry_count = 0
                
                # Parse MJPEG stream
                boundary = None
                frame_buffer = b""
                
                for chunk in response.iter_content(chunk_size=4096):
                    if self.stop_event.is_set():
                        break
                    
                    if not chunk:
                        continue
                    
                    frame_buffer += chunk
                    
                    # Tìm boundary delimiter (--frame)
                    if boundary is None:
                        boundary_idx = frame_buffer.find(b"--frame")
                        if boundary_idx >= 0:
                            boundary = b"--frame"
                    
                    # Tách frame khi tìm được boundary
                    if boundary:
                        while boundary in frame_buffer:
                            idx = frame_buffer.find(boundary)
                            if idx > 0:
                                frame_data = frame_buffer[:idx]
                                frame_buffer = frame_buffer[idx:]
                                
                                # Gửi frame vào queue
                                try:
                                    self.queue.put_nowait(frame_data)
                                except queue.Full:
                                    self.queue.get_nowait()  # Bỏ frame cũ
                                    self.queue.put_nowait(frame_data)
                            else:
                                break
                
                self.is_streaming = False
                print("⚠️ Kết nối recorder bị đóng, đang reconnect...")
                
            except requests.exceptions.ConnectionError:
                print(f"❌ Không thể kết nối đến recorder tại {self.recorder_url}")
                retry_count += 1
                self.is_streaming = False
                time.sleep(2)
            except requests.exceptions.Timeout:
                print("⚠️ Timeout khi đọc từ recorder")
                retry_count += 1
                self.is_streaming = False
                time.sleep(2)
            except Exception as e:
                print(f"❌ Lỗi proxy: {e}")
                retry_count += 1
                self.is_streaming = False
                time.sleep(2)
        
        print("🛑 MJPEG proxy thread dừng")

    def _mjpeg_generator(self):
        """Generator cho Flask stream MJPEG."""
        try:
            while not self.stop_event.is_set():
                try:
                    chunk = self.queue.get(timeout=2)
                    yield chunk
                    self.queue.task_done()
                except queue.Empty:
                    continue
        finally:
            self._client_disconnected()

    def _client_connected(self):
        with self.lock:
            self.active_clients += 1
            print(f"👥 Client kết nối (active: {self.active_clients})")
            self.start()

    def _client_disconnected(self):
        with self.lock:
            if self.active_clients > 0:
                self.active_clients -= 1
            print(f"👋 Client ngắt kết nối (active: {self.active_clients})")
            if self.active_clients == 0:
                print("🛑 Không còn client → dừng proxy...")
                self.stop()

    def stop(self):
        """Dừng proxy thread."""
        with self.lock:
            self.stop_event.set()
            if self.thread:
                self.thread.join(timeout=3)
            self.thread = None
            print("✅ Proxy đã dừng")

    def is_healthy(self):
        """Kiểm tra xem proxy có kết nối bình thường không."""
        return self.is_streaming and self.active_clients > 0


# Singleton instance
proxy = MjpegProxy()

# ============================================================
# HELPERS
# ============================================================

def _wait_for_recorder_ready(timeout: float = 5.0) -> bool:
    """Chờ recorder sẵn sàng."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            response = requests.head(RECORDER_WS_URL, timeout=2)
            if response.status_code == 200:
                print("✅ Recorder sẵn sàng")
                return True
        except requests.exceptions.RequestException as e:
            print(f"⚠️ Không thể kết nối đến recorder: {str(e)}")
        time.sleep(0.5)
    print(f"⚠️ Recorder không sẵn sàng sau {timeout}s tại {RECORDER_WS_URL}")
    return False

# ============================================================
# ROUTES
# ============================================================

@bp.get("/live")
@validate_request
def live_video():
    """Return HTML page with WebSocket video player."""
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Live Video</title>
        <style>
            body { margin: 0; background: #000; }
            #videoCanvas { width: 100%; max-height: 100vh; }
        </style>
    </head>
    <body>
        <canvas id="videoCanvas"></canvas>
        <script src="/static/socket.io.js"></script>
        <script>
            const canvas = document.getElementById('videoCanvas');
            const ctx = canvas.getContext('2d');
            // Kết nối đến recorder service (cùng domain)
            const socket = io(window.location.protocol + '//' + window.location.hostname + ':5000');
            
            socket.on('connect', () => {
                console.log('Connected to recorder');
            });
            
            socket.on('video_frame', (data) => {
                const img = new Image();
                img.onload = () => {
                    canvas.width = img.width;
                    canvas.height = img.height;
                    ctx.drawImage(img, 0, 0);
                };
                img.src = 'data:image/jpeg;base64,' + data.frame;
            });
            
            socket.on('disconnect', () => {
                console.log('Disconnected from recorder');
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
    health = {
        "status": "healthy" if proxy.is_healthy() else "degraded",
        "active_clients": proxy.active_clients,
        "is_streaming": proxy.is_streaming,
        "recorder_url": RECORDER_WS_URL
    }
    
    response = Response(
        response=health,
        status=200 if proxy.is_healthy() else 503,
        mimetype='application/json'
    )
    
    # Add security headers
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Cache-Control'] = 'no-store, no-cache'
    response.headers['X-Frame-Options'] = 'DENY'
    return response