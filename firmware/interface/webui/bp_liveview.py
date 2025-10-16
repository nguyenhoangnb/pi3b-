from __future__ import annotations
from flask import Blueprint, Response, request, abort, render_template_string
from functools import wraps
import re

# ============================================================
# CONFIG & SECURITY
# ============================================================

def validate_request(f):
    """Decorator to validate requests"""
    @wraps(f)
    def decorated(*args, **kwargs):
        # Basic path validation
        path = request.path
        if re.search(r'[;\'"]|\\x[0-9a-f]{2}', path, re.I):
            abort(400, "Invalid characters in request")
            
        return f(*args, **kwargs)
    return decorated

bp = Blueprint("liveview", __name__)

# HLS stream URL (served by recorder on port 5000)
HLS_STREAM_URL = "http://localhost:5000/hls/stream.m3u8"

# ============================================================
# ROUTES
# ============================================================

@bp.get("/live")
@validate_request
def live_video():
    """Return HTML page with HLS video player"""
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Live Camera Stream</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <script src="/static/hls.min.js"></script>
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
                background: #000;
            }
            .info {
                font-size: 12px;
                color: #999;
                margin-top: 10px;
            }
        </style>
    </head>
    <body>
        <h2>📷 Live Camera Stream (HLS)</h2>
        <p class="status" id="status">● Connecting...</p>
        <video id="videoStream" controls autoplay muted></video>
        <p class="info">HLS stream from recorder service (port 5000)</p>
        
        <script>
            const statusEl = document.getElementById('status');
            const video = document.getElementById('videoStream');
            const hlsUrl = 'http://localhost:5000/hls/stream.m3u8';
            
            if (Hls.isSupported()) {
                const hls = new Hls({
                    maxBufferLength: 4,
                    maxMaxBufferLength: 10,
                    lowLatencyMode: true,
                    enableWorker: true
                });
                
                hls.loadSource(hlsUrl);
                hls.attachMedia(video);
                
                hls.on(Hls.Events.MANIFEST_PARSED, function() {
                    console.log('✅ HLS manifest loaded');
                    statusEl.className = 'status';
                    statusEl.textContent = '● Streaming (HLS)';
                    video.play().catch(e => {
                        console.log('Autoplay prevented:', e);
                        statusEl.textContent = '● Ready (click play)';
                    });
                });
                
                hls.on(Hls.Events.ERROR, function(event, data) {
                    console.error('HLS Error:', data);
                    if (data.fatal) {
                        statusEl.className = 'error';
                        switch(data.type) {
                            case Hls.ErrorTypes.NETWORK_ERROR:
                                statusEl.textContent = '✖ Network error, retrying...';
                                console.log('Network error, trying to recover...');
                                setTimeout(() => hls.startLoad(), 1000);
                                break;
                            case Hls.ErrorTypes.MEDIA_ERROR:
                                statusEl.textContent = '✖ Media error, recovering...';
                                console.log('Media error, trying to recover...');
                                hls.recoverMediaError();
                                break;
                            default:
                                statusEl.textContent = '✖ Fatal error';
                                console.log('Fatal error, destroying HLS...');
                                hls.destroy();
                                break;
                        }
                    }
                });
            } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
                // Native HLS support (Safari, iOS)
                video.src = hlsUrl;
                video.addEventListener('loadedmetadata', function() {
                    console.log('✅ Native HLS loaded');
                    statusEl.className = 'status';
                    statusEl.textContent = '● Streaming (Native HLS)';
                    video.play().catch(e => {
                        console.log('Autoplay prevented:', e);
                        statusEl.textContent = '● Ready (click play)';
                    });
                });
                
                video.addEventListener('error', function(e) {
                    console.error('Video error:', e);
                    statusEl.className = 'error';
                    statusEl.textContent = '✖ Stream error';
                });
            } else {
                statusEl.className = 'error';
                statusEl.textContent = '✖ HLS not supported in this browser';
            }
        </script>
    </body>
    </html>
    """
    return Response(html, mimetype='text/html')

@bp.get("/live/stream")
@validate_request
def live_stream_embed():
    """Return embeddable HLS stream page for iframe"""
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <script src="/static/hls.min.js"></script>
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
                background: #000;
            }
        </style>
    </head>
    <body>
        <video id="videoStream" controls autoplay muted></video>
        
        <script>
            const video = document.getElementById('videoStream');
            const hlsUrl = 'http://localhost:5000/hls/stream.m3u8';
            
            if (Hls.isSupported()) {
                const hls = new Hls({
                    maxBufferLength: 4,
                    maxMaxBufferLength: 10,
                    lowLatencyMode: true
                });
                
                hls.loadSource(hlsUrl);
                hls.attachMedia(video);
                
                hls.on(Hls.Events.MANIFEST_PARSED, function() {
                    video.play().catch(e => console.log('Autoplay prevented:', e));
                });
                
                hls.on(Hls.Events.ERROR, function(event, data) {
                    if (data.fatal) {
                        switch(data.type) {
                            case Hls.ErrorTypes.NETWORK_ERROR:
                                setTimeout(() => hls.startLoad(), 1000);
                                break;
                            case Hls.ErrorTypes.MEDIA_ERROR:
                                hls.recoverMediaError();
                                break;
                            default:
                                hls.destroy();
                                break;
                        }
                    }
                });
            } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
                video.src = hlsUrl;
                video.addEventListener('loadedmetadata', function() {
                    video.play().catch(e => console.log('Autoplay prevented:', e));
                });
            }
        </script>
    </body>
    </html>
    """
    return Response(html, mimetype='text/html')

@bp.get("/stream/health")
@validate_request
def stream_health():
    """Endpoint to check stream health"""
    try:
        import requests
        response = requests.head("http://localhost:5000/health", timeout=2)
        is_healthy = response.status_code == 200
    except:
        is_healthy = False
    
    health = {
        "status": "healthy" if is_healthy else "degraded",
        "recorder_url": "http://localhost:5000",
        "stream_type": "hls",
        "stream_url": HLS_STREAM_URL
    }
    
    import json
    response = Response(
        response=json.dumps(health),
        status=200 if is_healthy else 503,
        mimetype='application/json'
    )
    
    # Add security headers
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Cache-Control'] = 'no-store, no-cache'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    return response