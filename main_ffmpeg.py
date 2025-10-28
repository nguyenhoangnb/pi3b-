from flask import Flask, send_from_directory, render_template_string
import subprocess
import os
import signal

app = Flask(__name__)

# ==== Cấu hình ====
VIDEO_DEVICE = "/dev/video0"
HLS_DIR = "/home/admin/hls"      # Thư mục lưu HLS
FRAME_RATE = "15"
RESOLUTION = "640x480"

# ==== Khởi tạo ====
os.makedirs(HLS_DIR, exist_ok=True)

# ==== HTML giao diện ====
HTML_PAGE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>🎥 Live Camera Stream (HLS + FFmpeg)</title>
    <style>
        body { background: #111; color: #eee; text-align: center; font-family: sans-serif; }
        video { width: 80%; border: 3px solid #555; border-radius: 10px; margin-top: 20px; }
        h2 { color: #0f0; }
        p { color: #ccc; }
    </style>
</head>
<body>
    <h2>🎥 Live Camera Stream (HLS + FFmpeg)</h2>
    <video id="videoPlayer" controls autoplay muted playsinline>
        <source src="/hls/stream.m3u8" type="application/x-mpegURL">
        Trình duyệt của bạn không hỗ trợ HLS.
    </video>
    <p>💾 HLS được lưu tại: {{ hls_path }}</p>
</body>
</html>
"""

# ==== Hàm khởi chạy FFmpeg ghi HLS ====
def start_hls_stream():
    cmd = [
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
        "-hls_time", "5",
        "-hls_list_size", "5",
        "-hls_flags", "delete_segments+append_list",
        os.path.join(HLS_DIR, "stream.m3u8")
    ]
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# ==== Flask Routes ====

@app.route("/")
def index():
    return render_template_string(HTML_PAGE, hls_path=HLS_DIR)

@app.route("/hls/<path:filename>")
def serve_hls(filename):
    return send_from_directory(HLS_DIR, filename)

# ==== Chạy server ====
if __name__ == "__main__":
    print(f"🌐 Flask HLS stream running at: http://<IP_RaspberryPi>:8080/")
    print(f"💾 HLS files saved to: {HLS_DIR}")

    ffmpeg_process = start_hls_stream()

    try:
        app.run(host="0.0.0.0", port=8080, debug=False)
    finally:
        # Khi dừng Flask → dừng luôn ffmpeg
        if ffmpeg_process.poll() is None:
            ffmpeg_process.send_signal(signal.SIGINT)
            ffmpeg_process.wait()
