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
import signal

# ============================================================
# CONFIG
# ============================================================
VIDEO_DEVICE = "/dev/video0"
FRAME_RATE = "15"
RESOLUTION = "640x480"
HLS_DIR = Path("/tmp/picam_hls")
FFMPEG_LOG = HLS_DIR / "ffmpeg.log"
HLS_SEGMENT_TEMPLATE = HLS_DIR / "segment_%03d.ts"

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
        if re.search(r'(\.\.|%2e%2e|%252e%252e|[\x00-\x1f\x7f]|[\'\";]|\\x[0-9a-f]{2})', path, re.I):
            abort(400, "Invalid characters in request path")
        return f(*args, **kwargs)
    return decorated

# ============================================================
# FFMPEG MANAGEMENT
# ============================================================
_ffmpeg_proc: subprocess.Popen | None = None
_ffmpeg_lock = threading.Lock()

def _kill_existing_ffmpeg():
    """Kill ffmpeg processes that were started to write into our HLS_DIR (best-effort)."""
    try:
        # kill processes that reference our HLS_DIR in their commandline
        # (fallback: kill any ffmpeg to avoid duplicates — use with care)
        os.system(f"pkill -f '{str(HLS_DIR)}' || true")
        # also try to pkill any leftover ffmpeg (lower risk on single-purpose device)
        os.system("pkill -f ffmpeg || true")
    except Exception:
        pass

def start_ffmpeg():
    """Start FFmpeg to produce HLS. Returns subprocess.Popen."""
    global _ffmpeg_proc

    with _ffmpeg_lock:
        if _ffmpeg_proc and _ffmpeg_proc.poll() is None:
            print("🔁 FFmpeg already running (PID: {})".format(_ffmpeg_proc.pid))
            return _ffmpeg_proc

        # ensure previous ffmpeg not left behind
        _kill_existing_ffmpeg()

        # Remove stale .m3u8 to avoid HLS.js confusion on startup
        try:
            m3u = HLS_DIR / "stream.m3u8"
            if m3u.exists():
                m3u.unlink()
        except Exception:
            pass

        # Build FFmpeg command
        ffmpeg_cmd = [
            "ffmpeg",
            "-hide_banner",
            "-fflags", "nobuffer",
            "-loglevel", "info",
            "-use_wallclock_as_timestamps", "1",
            "-f", "v4l2",
            "-framerate", str(FRAME_RATE),
            "-video_size", RESOLUTION,
            "-i", VIDEO_DEVICE,
            # overlay timestamp (if font missing ffmpeg may warn but continue)
            "-vf", "drawtext=text='%{localtime\\:%Y-%m-%d %H\\\\\\:%M\\\\\\:%S}':x=10:y=10:fontcolor=white:fontsize=20:box=1:boxcolor=0x00000099",
            # use Pi hardware encoder (if available)
            "-vcodec", "h264_v4l2m2m",
            # tune for low-latency
            "-preset", "veryfast",
            "-tune", "zerolatency",
            "-pix_fmt", "yuv420p",
            # HLS options
            "-f", "hls",
            "-hls_time", "3",
            "-hls_list_size", "5",
            "-hls_flags", "delete_segments+append_list+independent_segments",
            "-hls_allow_cache", "0",
            "-hls_segment_filename", str(HLS_SEGMENT_TEMPLATE),
            str(HLS_DIR / "stream.m3u8"),
        ]

        # Open ffmpeg log file (append)
        logf = open(FFMPEG_LOG, "a", buffering=1)
        print("🎬 Starting FFmpeg:", " ".join(ffmpeg_cmd))
        _ffmpeg_proc = subprocess.Popen(
            ffmpeg_cmd,
            stdout=logf,
            stderr=logf,
            stdin=subprocess.DEVNULL,
            start_new_session=True
        )
        print(f"✅ FFmpeg started (PID: {_ffmpeg_proc.pid}), logging -> {FFMPEG_LOG}")
        return _ffmpeg_proc

def stop_ffmpeg():
    global _ffmpeg_proc
    with _ffmpeg_lock:
        if not _ffmpeg_proc:
            return
        try:
            print(f"🛑 Stopping FFmpeg (PID: {_ffmpeg_proc.pid})")
            _ffmpeg_proc.send_signal(signal.SIGINT)
            try:
                _ffmpeg_proc.wait(timeout=4)
            except subprocess.TimeoutExpired:
                _ffmpeg_proc.kill()
            print("✅ FFmpeg stopped")
        except Exception as e:
            print("Error stopping ffmpeg:", e)
        _ffmpeg_proc = None

def ffmpeg_watchdog_loop():
    """Ensure ffmpeg keeps running; restart if it dies."""
    while True:
        try:
            proc = None
            with _ffmpeg_lock:
                proc = _ffmpeg_proc
            if not proc or proc.poll() is not None:
                print("🔁 FFmpeg not running or exited -> (re)starting")
                start_ffmpeg()
            # check log file size occasionally and rotate small logs to avoid giant logs
            try:
                if FFMPEG_LOG.exists() and FFMPEG_LOG.stat().st_size > 10 * 1024 * 1024:
                    FFMPEG_LOG.rename(str(FFMPEG_LOG) + ".old")
            except Exception:
                pass
        except Exception as e:
            print("Watchdog error:", e)
        time.sleep(4)

# ============================================================
# ROUTES
# ============================================================
@app.route("/")
def root():
    return '<h3 style="color:white;text-align:center;background:black;padding:20px">Go to <a href="/live">/live</a> to view stream</h3>'

@app.route("/live")
@validate_request
def live_video():
    """Serve HTML page with HLS.js player"""
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
            .status {{ margin: 6px 0 0; font-size:14px; }}
        </style>
    </head>
    <body>
        <h2>🎥 Live Camera Stream</h2>
        <p id="status" class="status">🔄 Connecting...</p>
        <video id="video" controls autoplay muted playsinline></video>
        <p style="font-size:12px;color:#999;">HLS dir: {HLS_DIR}</p>
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
                        // try to recover non-fatal errors (Hls can attempt recoveries automatically)
                        try {{ hls.recoverMediaError(); }} catch(e){{}}
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
    """Serve HLS files (.m3u8, .ts) with correct MIME types."""
    file_path = HLS_DIR / filename
    if not file_path.exists() or not file_path.is_file():
        abort(404, "File not found")

    if filename.endswith(".m3u8"):
        mimetype = "application/vnd.apple.mpegurl"
    elif filename.endswith(".ts"):
        mimetype = "video/mp2t"
    else:
        mimetype = "application/octet-stream"

    # Use send_from_directory to avoid path traversal issues
    return send_from_directory(HLS_DIR, filename, mimetype=mimetype)

# Optional control endpoints
@app.route("/_start")
def web_start():
    """Start ffmpeg (manual)"""
    start_ffmpeg()
    return "started"

@app.route("/_stop")
def web_stop():
    stop_ffmpeg()
    return "stopped"

# ============================================================
# MAIN
# ============================================================
if __name__ == "__main__":
    print(f"🌐 Flask HLS stream (port 8080). HLS dir: {HLS_DIR}")
    # start watchdog thread that ensures ffmpeg runs
    t = threading.Thread(target=ffmpeg_watchdog_loop, daemon=True)
    t.start()
    # run flask
    app.run(host="0.0.0.0", port=8080, debug=False)
