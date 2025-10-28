from flask import Flask, Response, render_template_string
import subprocess

app = Flask(__name__)

# ---- Cấu hình ----
VIDEO_DEVICE = "/dev/video0"     # Camera (thay đổi nếu cần)
FRAME_RATE = "15"
RESOLUTION = "640x480"

# ---- Giao diện web ----
HTML_PAGE = """
<!DOCTYPE html>
<html>
<head>
    <title>Live Camera Stream (MJPEG + FFmpeg)</title>
    <style>
        body { text-align: center; background: #111; color: #eee; font-family: sans-serif; }
        img { width: 80%; border: 4px solid #444; border-radius: 10px; }
        h2 { color: #0f0; }
    </style>
</head>
<body>
    <h2>🎥 Live Camera Stream (FFmpeg → Flask)</h2>
    <img src="{{ url_for('video_feed') }}">
</body>
</html>
"""

def gen_frames():
    """Đọc camera qua FFmpeg và stream MJPEG về Flask"""
    ffmpeg_cmd = [
        "ffmpeg",
        "-f", "v4l2",
        "-framerate", FRAME_RATE,
        "-video_size", RESOLUTION,
        "-i", VIDEO_DEVICE,

        # Overlay thời gian hệ thống
        "-vf", "drawtext=text='%{localtime\\:%Y-%m-%d %H\\\\\\:%M\\\\\\:%S}':x=10:y=10:fontcolor=white:fontsize=20",

        # Xuất ra MJPEG
        "-f", "mjpeg",
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
    return render_template_string(HTML_PAGE)

@app.route("/video_feed")
def video_feed():
    return Response(gen_frames(), mimetype="multipart/x-mixed-replace; boundary=frame")

if __name__ == "__main__":
    print(f"🌐 Mở trình duyệt: http://<IP_RaspberryPi>:8080/")
    app.run(host="0.0.0.0", port=8080, debug=False)
