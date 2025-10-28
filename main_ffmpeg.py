from flask import Flask, Response, render_template_string
import subprocess
import os

app = Flask(__name__)

# ---- Cấu hình ----
VIDEO_DEVICE = "/dev/video0"
HLS_DIR = "/home/admin/hls"   # đổi đường dẫn nếu cần (VD: /media/usb/hls)
FRAME_RATE = "15"
RESOLUTION = "640x480"

# Tạo thư mục lưu HLS nếu chưa có
os.makedirs(HLS_DIR, exist_ok=True)

HTML_PAGE = """
<!DOCTYPE html>
<html>
<head>
    <title>Live Camera Stream (HLS + FFmpeg)</title>
    <style>
        body { text-align: center; background: #111; color: #eee; font-family: sans-serif; }
        img { width: 80%; border: 4px solid #444; border-radius: 10px; }
        h2 { color: #0f0; }
    </style>
</head>
<body>
    <h2>🎥 Live Camera Stream (HLS + FFmpeg)</h2>
    <img src="{{ url_for('video_feed') }}">
    <p>💾 Ghi HLS tại: {{ hls_path }}</p>
</body>
</html>
"""

def gen_frames():
    """Chạy FFmpeg ghi HLS + xuất MJPEG"""
    ffmpeg_cmd = [
        "ffmpeg",
        "-f", "v4l2",
        "-framerate", FRAME_RATE,
        "-video_size", RESOLUTION,
        "-i", VIDEO_DEVICE,

        # Overlay thời gian hệ thống lên video
        "-vf", "drawtext=text='%{localtime\\:%Y-%m-%d %H\\\\\\:%M\\\\\\:%S}':x=10:y=10:fontcolor=white:fontsize=20",

        # Ghi HLS ra thư mục
        "-f", "hls",
        "-hls_time", "5",
        "-hls_list_size", "3",
        "-hls_flags", "delete_segments",
        os.path.join(HLS_DIR, "stream.m3u8"),

        # Đồng thời stream MJPEG ra stdout cho Flask
        "-f", "yuv420p",
        "-q:v", "5",
        "pipe:1"
    ]

    process = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=10**7)

    try:
        while True:
            chunk = process.stdout.read(1024)
            if not chunk:
                break
            yield (b"--frame\r\n"
                   b"Content-Type: image/jpeg\r\n\r\n" + chunk + b"\r\n")
    except GeneratorExit:
        process.kill()

@app.route("/")
def index():
    return render_template_string(HTML_PAGE, hls_path=HLS_DIR)

@app.route("/video_feed")
def video_feed():
    return Response(gen_frames(), mimetype="multipart/x-mixed-replace; boundary=frame")

if __name__ == "__main__":
    print(f"🌐 Flask FFmpeg HLS stream running at: http://<IP_RaspberryPi>:8080/")
    print(f"💾 HLS saved to: {HLS_DIR}")
    app.run(host="0.0.0.0", port=8080, debug=False)
