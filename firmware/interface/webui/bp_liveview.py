from __future__ import annotations
from flask import Blueprint, Response, request, abort, render_template_string
from functools import wraps
import re
import requests
import json

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

# HLS stream URL - use request host to support both localhost and remote access
def get_hls_url():
    """Get HLS URL based on request host - use proxy route on same port"""
    # Use proxy route on port 8080 instead of direct port 5000
    return f"http://{request.host}/hls/stream.m3u8"

# ============================================================
# ROUTES
# ============================================================

@bp.get("/live")
@validate_request
def live_video():
    """Return HTML page with HLS video player"""
    # Get dynamic HLS URL based on request host
    hls_url = get_hls_url()
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Live Camera Stream</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <script src="/static/hls.min.js"></script>  <!-- Reverted to static as per user -->
        <style>
            body {{ 
                margin: 0; 
                background: #000; 
                text-align: center;
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                color: #eee;
            }}
            h2 {{
                margin: 20px 0 10px 0;
                font-size: 24px;
            }}
            .status {{
                color: #0f0;
                font-size: 14px;
                margin: 10px 0;
            }}
            .error {{
                color: #f00;
                font-size: 14px;
                margin: 10px 0;
            }}
            #videoStream {{ 
                width: 90%; 
                max-width: 1280px;
                max-height: 80vh; 
                border: 2px solid #444;
                border-radius: 8px;
                margin: 20px auto;
                display: block;
                box-shadow: 0 4px 20px rgba(0,0,0,0.5);
                background: #000;
            }}
            .info {{
                font-size: 12px;
                color: #999;
                margin-top: 10px;
            }}
        </style>
    </head>
    <body>
        <h2>📷 Live Camera Stream (HLS)</h2>
        <p class="status" id="status">● Connecting...</p>
        <video id="videoStream" controls autoplay muted></video>
        <p class="info">HLS stream from recorder service (proxied via port 8080)</p>
        
        <script>
            const statusEl = document.getElementById('status');
            const video = document.getElementById('videoStream');
            const hlsUrl = '{hls_url}';
            
            if (Hls.isSupported()) {{
                const hls = new Hls({{
                    maxBufferLength: 4,
                    maxMaxBufferLength: 10,
                    lowLatencyMode: true,
                    enableWorker: true
                }});
                
                hls.loadSource(hlsUrl);
                hls.attachMedia(video);
                
                hls.on(Hls.Events.MANIFEST_PARSED, function() {{
                    console.log('✅ HLS manifest loaded');
                    statusEl.className = 'status';
                    statusEl.textContent = '● Streaming (HLS)';
                    video.play().catch(e => {{
                        console.log('Autoplay prevented:', e);
                        statusEl.textContent = '● Ready (click play)';
                    }});
                }});
                
                hls.on(Hls.Events.ERROR, function(event, data) {{
                    console.error('HLS Error:', data);
                    if (data.fatal) {{
                        statusEl.className = 'error';
                        switch(data.type) {{
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
                        }}
                    }}
                }});
            }} else if (video.canPlayType('application/vnd.apple.mpegurl')) {{
                // Native HLS support (Safari, iOS)
                video.src = hlsUrl;
                video.addEventListener('loadedmetadata', function() {{
                    console.log('✅ Native HLS loaded');
                    statusEl.className = 'status';
                    statusEl.textContent = '● Streaming (Native HLS)';
                    video.play().catch(e => {{
                        console.log('Autoplay prevented:', e);
                        statusEl.textContent = '● Ready (click play)';
                    }});
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

@bp.get("/live/stream")
@validate_request
def live_stream_embed():
    """Return embeddable HLS stream page for iframe"""
    hls_url = get_hls_url()  # FIXED: Use dynamic proxy URL instead of hardcoded localhost:5000
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <script src="/static/hls.min.js"></script>  <!-- Reverted to static as per user -->
        <style>
            * {{ margin: 0; padding: 0; }}
            body {{ 
                background: #000; 
                overflow: hidden;
                width: 100vw;
                height: 100vh;
            }}
            #videoStream {{ 
                width: 100%;
                height: 100%;
                object-fit: contain;
                display: block;
                background: #000;
            }}
        </style>
    </head>
    <body>
        <video id="videoStream" controls autoplay muted></video>
        
        <script>
            const video = document.getElementById('videoStream');
            const hlsUrl = '{hls_url}';  <!-- FIXED: Dynamic proxy -->
            
            if (Hls.isSupported()) {{
                const hls = new Hls({{
                    maxBufferLength: 4,
                    maxMaxBufferLength: 10,
                    lowLatencyMode: true
                }});
                
                hls.loadSource(hlsUrl);
                hls.attachMedia(video);
                
                hls.on(Hls.Events.MANIFEST_PARSED, function() {{
                    video.play().catch(e => console.log('Autoplay prevented:', e));
                }});
                
                hls.on(Hls.Events.ERROR, function(event, data) {{
                    if (data.fatal) {{
                        switch(data.type) {{
                            case Hls.ErrorTypes.NETWORK_ERROR:
                                setTimeout(() => hls.startLoad(), 1000);
                                break;
                            case Hls.ErrorTypes.MEDIA_ERROR:
                                hls.recoverMediaError();
                                break;
                            default:
                                hls.destroy();
                                break;
                        }}
                    }}
                }});
            }} else if (video.canPlayType('application/vnd.apple.mpegurl')) {{
                video.src = hlsUrl;
                video.addEventListener('loadedmetadata', function() {{
                    video.play().catch(e => console.log('Autoplay prevented:', e));
                }});
            }}
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
        response = requests.head("http://localhost:5000/health", timeout=2)
        is_healthy = response.status_code == 200
    except:
        is_healthy = False
    
    health = {
        "status": "healthy" if is_healthy else "degraded",
        "recorder_url": f"http://localhost:5000",  # FIXED: Use localhost for internal check
        "stream_type": "hls",
        "stream_url": get_hls_url()
    }
    
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

# ============================================================
# HLS PROXY ROUTES - Forward from port 8080 to recorder port 5000
# ============================================================

@bp.get("/hls/<path:filename>")
@validate_request
def hls_proxy(filename):
    """Proxy HLS files from recorder service to avoid opening port 5000"""
    # Validate filename to prevent path traversal - improved
    if not re.match(r'^[a-zA-Z0-9_\-\.\/]+$', filename) or '..' in filename:
        abort(400, "Invalid filename")
    
    try:
        # Forward request to recorder service on localhost:5000
        recorder_url = f"http://localhost:5000/hls/{filename}"
        resp = requests.get(recorder_url, timeout=5, stream=True)  # Already stream=True
        
        # Determine content type
        if filename.endswith('.m3u8'):
            content_type = 'application/vnd.apple.mpegurl'
        elif filename.endswith('.ts'):
            content_type = 'video/mp2t'
        else:
            content_type = resp.headers.get('Content-Type', 'application/octet-stream')
        
        # FIXED: Stream response to avoid loading full content into memory
        def generate():
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    yield chunk
        
        response = Response(
            generate(),
            status=resp.status_code,
            mimetype=content_type
        )
        
        # Add CORS headers for HLS
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        
        # Cache control for HLS segments
        if filename.endswith('.m3u8'):
            response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        else:
            response.headers['Cache-Control'] = 'public, max-age=3600'
        
        # Forward other relevant headers
        for header in ['Content-Length', 'Content-Range']:
            if header in resp.headers:
                response.headers[header] = resp.headers[header]
        
        return response
        
    except requests.exceptions.RequestException as e:
        abort(502, f"Recorder service unavailable: {str(e)}")

@bp.route("/hls/<path:filename>", methods=['OPTIONS'])
def hls_proxy_options(filename):
    """Handle CORS preflight for HLS"""
    response = Response()
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return response