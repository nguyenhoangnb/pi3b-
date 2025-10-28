from flask import Flask, Response, render_template_string
import cv2
import datetime

app = Flask(__name__)

# Mở camera (tự dò camera khả dụng)
camera = None
for cam in range(31):
    camera = cv2.VideoCapture(cam)
    if camera.isOpened():
        print(f"✅ Đang sử dụng camera: /dev/video{cam}")
        break

if not camera or not camera.isOpened():
    raise RuntimeError("❌ Không thể mở camera!")

# HTML giao diện đơn giản
HTML_PAGE = """
<!DOCTYPE html>
<html>
<head>
    <title>Live Camera Stream</title>
    <style>
        body { text-align: center; background: #111; color: #eee; font-family: sans-serif; }
        img { width: 80%; border: 4px solid #444; border-radius: 10px; }
        h2 { color: #0f0; }
    </style>
</head>
<body>
    <h2>📷 Live Camera Stream</h2>
    <img src="{{ url_for('video_feed') }}">
    <p>⏱ Hiển thị thời gian thực trên mỗi khung hình</p>
</body>
</html>
"""

def gen_frames():
    """Đọc frame từ camera, overlay thời gian, rồi encode JPEG"""
    font = cv2.FONT_HERSHEY_SIMPLEX

    while True:
        success, frame = camera.read()
        if not success:
            break

        # Lấy thời gian hiện tại (hệ thống)
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Overlay thời gian lên góc trên bên trái
        cv2.putText(frame, timestamp, (10, 30), font, 0.8, (255, 255, 255), 2, cv2.LINE_AA)

        # Encode frame thành JPEG
        ret, buffer = cv2.imencode('.jpg', frame)
        frame_bytes = buffer.tobytes()

        # Stream theo định dạng multipart/x-mixed-replace
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')

@app.route('/')
def index():
    return render_template_string(HTML_PAGE)

@app.route('/video_feed')
def video_feed():
    return Response(gen_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

if __name__ == '__main__':
    # Chạy server để truy cập qua LAN
    print("🌐 Flask MJPEG stream chạy tại: http://<IP_RaspberryPi>:8080/")
    app.run(host='0.0.0.0', port=8080, debug=False)
