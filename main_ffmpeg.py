#!/usr/bin/env python3
import os, subprocess, threading, time
from flask import Flask, Response, render_template_string

app = Flask(__name__)
HLS_DIR = "/tmp/picam_hls"
PORT = 8080

os.makedirs(HLS_DIR, exist_ok=True)

# === Khởi động FFmpeg ghi ra HLS ===
def start_ffmpeg():
    while True:
        cmd = [
            "ffmpeg",
            "-f", "v4l2",
            "-framerate", "30",
            "-video_size", "640x480",
            "-i", "/dev/video0",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-tune", "zerolatency",
            "-f", "hls",
            "-hls_time", "2",
            "-hls_list_size", "5",
            "-hls_flags", "delete_segments",
            f"{HLS_DIR}/stream.m3u8"
        ]

        print("🔁 Starting FFmpeg...")
        process = subprocess.Popen(cmd)
        process.wait()
        print("⚠️ FFmpeg exited, restarting in 3s...")
        time.sleep(3)

threading.Thread(target=start_ffmpeg, daemon=True).start()

# === Flask routes ===
@app.route("/")
def index():
    return render_template_string("""
    <html>
      <head>
        <title>PiCam Stream</title>
      </head>
      <body style="background:#000;display:flex;justify-content:center;align-items:center;height:100vh;">
        <video id="player" controls autoplay muted playsinline width="640" height="480"></video>
        <script src="https://cdn.jsdelivr.net/npm/hls.js@latest"></script>
        <script>
          const video = document.getElementById('player');
          if (Hls.isSupported()) {
            const hls = new Hls();
            hls.loadSource('/hls/stream.m3u8');
            hls.attachMedia(video);
            hls.on(Hls.Events.ERROR, function(event, data) {
              console.error("HLS error:", data);
            });
          } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
            video.src = '/hls/stream.m3u8';
          }
        </script>
      </body>
    </html>
    """)

@app.route("/hls/<path:filename>")
def hls(filename):
    return Response(open(os.path.join(HLS_DIR, filename), "rb"), mimetype="video/MP2T")

if __name__ == "__main__":
    print(f"🌐 Flask starting on port {PORT}")
    app.run(host="0.0.0.0", port=PORT)
