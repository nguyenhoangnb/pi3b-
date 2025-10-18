# Tính năng Recorder với Overlay và LED Status

## ✅ Các tính năng đã implement

### 1. **Video Overlay (Timestamp + GPS)**

#### Timestamp Overlay
- **Vị trí**: Góc trên bên trái
- **Nội dung**: `YYYY-MM-DD HH:MM:SS` (thời gian địa phương)
- **Màu sắc**: Chữ trắng, nền đen trong suốt
- **Font**: DejaVu Sans Mono, size 20

#### GPS Overlay
- **Vị trí**: Góc dưới bên trái
- **Nội dung**: 
  - Có GPS fix: `GPS: lat, lon (N sats)` (màu vàng)
  - Chưa có fix: `GPS: No Fix` (màu đỏ)
- **Font**: DejaVu Sans Mono, size 16

### 2. **LED Status Indicators**

#### LED Behavior:
- **🟢 Sáng liên tục**: Recording bình thường, USB storage OK
- **🟡 Nhấp nháy (0.3s)**: USB storage bị ngắt kết nối trong khi recording
- **⚫ Tắt**: Recorder đã dừng

#### Monitoring:
- Background thread kiểm tra USB storage mỗi 2 giây
- Tự động update LED status theo trạng thái storage

### 3. **Dual Output với Tee Muxer**

- **MP4 Segments**: `/media/ssd/YYYYMMDD_HHMMSS_cam0.mp4` (30s/file)
- **HLS Stream**: `/tmp/picam_hls/stream.m3u8` (2s segments)
- **Single Encode**: Cùng một encode stream cho cả 2 outputs
- **Codec**: H.264 Main Profile, yuv420p (browser-compatible)

## 📋 Cách sử dụng

### Khởi động Recorder
```bash
cd /home/hoang/pi3b-/firmware/domain
nohup python3 recorder_ffmpeg.py > /tmp/recorder.log 2>&1 &
```

### Kiểm tra status
```bash
# Check logs
tail -f /tmp/recorder.log

# Check health endpoint
curl http://localhost:5000/health

# Check HLS stream
curl http://localhost:5000/hls/stream.m3u8

# Check outputs
ls -lh /media/ssd/*.mp4 | tail -5
ls -lh /tmp/picam_hls/
```

### Dừng Recorder
```bash
pkill -f "recorder_ffmpeg.py"
```

## 🎬 Test Video Overlay

### Extract frame để xem overlay:
```bash
./test_video_overlay.sh
```

Hoặc manually:
```bash
# Get latest MP4
LATEST=$(ls -t /media/ssd/*.mp4 | head -1)

# Extract frame at 5 seconds
ffmpeg -ss 5 -i "$LATEST" -frames:v 1 /tmp/frame.jpg

# Open frame
xdg-open /tmp/frame.jpg
```

## 🔧 Cấu hình

### Config file: `firmware/config/device_full.yaml`

```yaml
capabilities:
  video: True
  audio: True
  gnss: True  # Enable GPS overlay

gpio:
  record_led: 26  # LED pin for recording status

overlay:
  timestamp_font: "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
  timestamp_size: 24
```

## 🐛 Troubleshooting

### GPS không hiển thị tọa độ
- Kiểm tra GPS module có kết nối: `ls /dev/ttyACM* /dev/ttyUSB*`
- Test GPS: `python3 firmware/hal/gnss.py`
- Đảm bảo antenna có tầm nhìn ra trời

### LED không hoạt động
- Kiểm tra GPIO pin đúng: `gpio readall` (nếu có)
- Verify fake_rpi on dev machine (LED sẽ không thực sự sáng)
- Trên Raspberry Pi thật, LED sẽ hoạt động bình thường

### Overlay text không xuất hiện
- Kiểm tra font path: `ls /usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf`
- Install fonts: `sudo apt install fonts-dejavu-core`
- Check FFmpeg command trong log có chứa `drawtext`

### USB storage monitor
- Storage monitor chạy mỗi 2 giây
- Khi USB disconnect: LED nhấp nháy, recording tiếp tục (buffer in RAM)
- Khi USB reconnect: LED sáng trở lại

## 📊 Performance

### Resource Usage:
- **CPU**: ~40-60% (H.264 encoding @ veryfast preset)
- **Memory**: ~150-200MB
- **Storage**: 
  - MP4: ~1.2MB/s (H.264 1200kbps + AAC 128kbps)
  - HLS: 400-500KB per 2s segment

### Encoding Settings:
- **Preset**: veryfast (balance between speed & quality)
- **Profile**: main (browser-compatible)
- **Pixel Format**: yuv420p (standard for web)
- **Keyframe Interval**: 2 seconds (good for HLS seeking)

## 🎯 Next Steps

### Possible Enhancements:
1. **Dynamic GPS update**: Update GPS coordinates periodically (not just at start)
2. **Speed overlay**: Add vehicle speed from GPS
3. **Altitude overlay**: Add altitude from GPS
4. **Battery status**: Add battery level indicator
5. **Recording indicator**: Add blinking red dot for recording
6. **Custom overlays**: Allow user-defined text overlays

### GPS Dynamic Update Example:
```python
# Create a file with current GPS coordinates
# FFmpeg can read from file using drawtext with text file
def update_gps_overlay_file(self):
    while self.is_running():
        gps = self.gnss.get_location()
        if gps['latitude']:
            with open('/tmp/gps_overlay.txt', 'w') as f:
                f.write(f"GPS: {gps['latitude']:.6f}, {gps['longitude']:.6f}")
        time.sleep(1)
```

Then in FFmpeg:
```
drawtext=textfile=/tmp/gps_overlay.txt:reload=1:...
```
