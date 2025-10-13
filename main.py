from flask import Flask, Response, render_template_string
import cv2

app = Flask(__name__)

# Mở camera (0 là mặc định cho webcam / CSI / USB camera)

camera = None
for cam in range(2):
    camera = cv2.VideoCapture(cam)
    if not camera.isOpened():
        continue
    else:
        break

# Kiểm tra xem camera mở được không
if not camera.isOpened():
    raise RuntimeError("Không thể mở camera!")

# Template HTML đơn giản
HTML_PAGE = """
<!DOCTYPE html>
<html>
<head>
    <title>Live Camera Stream</title>
    <style>
        body { text-align: center; background: #111; color: #eee; font-family: sans-serif; }
        img { width: 80%; border: 4px solid #444; border-radius: 10px; }
    </style>
</head>
<body>
    <h2>📷 Live Camera Stream</h2>
    <img src="{{ url_for('video_feed') }}">
</body>
</html>
"""

def gen_frames():
    """Đọc frame từ camera và encode JPEG để stream"""
    while True:
        success, frame = camera.read()
        if not success:
            break

        # Chuyển frame thành JPEG
        ret, buffer = cv2.imencode('.jpg', frame)
        frame_bytes = buffer.tobytes()

        # Gửi dữ liệu theo chuẩn multipart/x-mixed-replace để trình duyệt hiển thị liên tục
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')

@app.route('/')
def index():
    return render_template_string(HTML_PAGE)

@app.route('/video_feed')
def video_feed():
    # Trả về stream video MJPEG
    return Response(gen_frames(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

if __name__ == '__main__':
    # Chạy Flask server, mở cho toàn mạng LAN xem được
    app.run(host='0.0.0.0', port=8080, debug=False)
