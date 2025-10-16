#!/usr/bin/env python3
# recorder.py - PiStreamer with OpenCV + PyAudio + MoviePy (no direct FFmpeg subprocess, merge AVI + WAV to MP4)
import os
import time
import signal
from datetime import datetime
import threading
from pathlib import Path
import sys
import tempfile
import wave  # Built-in for WAV audio
import pyaudio  # For audio capture
from moviepy.video.io.ffmpeg_tools import ffmpeg_merge_video_audio
import requests
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import cv2

class SegmentManager:
    """Class quản lý segment cho video và audio recording"""
    def __init__(self, output_dir, segment_seconds):
        self.output_dir = output_dir
        self.segment_seconds = segment_seconds
        self.current_segment = None
        self.segment_start = 0  # Khởi tạo với 0 thay vì None
        self._lock = threading.Lock()
        self._segment_complete = {'video': False, 'audio': False}
        self._merge_event = threading.Event()
        
    def start_new_segment(self):
        """Bắt đầu segment mới và trả về thông tin segment"""
        with self._lock:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.current_segment = f"{self.output_dir}/{timestamp}_cam0"
            self.segment_start = time.time()
            self._segment_complete = {'video': False, 'audio': False}
            self._merge_event.clear()
            return self.current_segment
            
    def mark_complete(self, stream_type):
        """Đánh dấu một luồng (video/audio) đã hoàn thành segment"""
        with self._lock:
            self._segment_complete[stream_type] = True
            if all(self._segment_complete.values()):
                self._merge_event.set()
                
    def wait_for_merge(self, timeout=None):
        """Đợi cả video và audio hoàn thành để ghép file"""
        return self._merge_event.wait(timeout)
        
    def should_start_new(self):
        """Kiểm tra xem đã đến lúc bắt đầu segment mới chưa"""
        return time.time() - self.segment_start >= self.segment_seconds
        
    def get_current_paths(self):
        """Lấy đường dẫn file cho segment hiện tại"""
        return {
            'video': f"{self.current_segment}.avi",
            'audio': f"{self.current_segment}.wav",
            'output': f"{self.current_segment}.mp4"
        }
from flask import Flask, Response, current_app
from flask_socketio import SocketIO, emit  # For WebSocket stream
from moviepy import VideoFileClip, AudioFileClip  # For merging video + audio to MP4 (pip install moviepy)
import base64  # For encoding frame to base64
from firmware.hal.usb_manager import USBManager    
from firmware.hal.gpio_leds import gpioLed
from firmware.hal.gnss import GNSSModule
from firmware.hal.rtc import rtcModule
from firmware.hal.micro import Micro
from firmware.config.config_loader import load
def _get_pyaudio_device_index(device_name_or_index):
    """
    Convert device name (hw:1,0) hoặc string index thành PyAudio device index.
    
    Args:
        device_name_or_index: có thể là:
            - int: 0, 1, 2... (PyAudio index)
            - str: "hw:1,0" (ALSA name)
            - str: "1" (string number)
    
    Returns:
        int: PyAudio device index, hoặc None nếu không tìm được
    """
    import pyaudio
    
    # Nếu đã là int, trả về ngay
    if isinstance(device_name_or_index, int):
        return device_name_or_index
    
    # Nếu là string number, convert sang int
    if isinstance(device_name_or_index, str):
        try:
            return int(device_name_or_index)
        except ValueError:
            pass  # Không phải number, tiếp tục search
    
    # Search device theo tên (ALSA name như "hw:1,0")
    p = pyaudio.PyAudio()
    device_str = str(device_name_or_index).lower()
    
    try:
        for i in range(p.get_device_count()):
            info = p.get_device_info_by_index(i)
            device_name = info.get('name', '').lower()
            
            # Check nếu tên device match
            if device_str in device_name:
                print(f"   ✅ Tìm thấy device '{device_name}' tại index {i}")
                p.terminate()
                return i
    finally:
        p.terminate()
    
    print(f"   ⚠️ Không tìm thấy device '{device_name_or_index}'")
    return None
class PiStreamer:
    def __init__(self,
                 video_dev=0,  # Index for OpenCV
                 audio_dev="hw:1,0",
                 output_dir="/media/ssd",
                 hls_dir="/tmp/picam_hls",  # Not used
                 segment_seconds=30,  # Short for test
                 led_pin=26):
        self.video_dev = video_dev
        self.audio_dev = audio_dev
        self.output_dir = output_dir
        self.hls_dir = hls_dir
        self.segment_seconds = segment_seconds
        self.config_file = Path(__file__).parent.parent / 'config' / 'device_full.yaml'
        self.config = load(self.config_file)
        self.led_control = gpioLed(self.config['gpio'].get('record_led', 26))
        self.led_thread = None
        self.led_running = False
        
        # Init RTC and GNSS
        try:
            self.rtc = rtcModule()
            self.rtc_available = True
            print("✅ RTC module khởi tạo thành công")
        except Exception as e:
            print(f"⚠️ Không thể khởi tạo RTC: {e}")
            self.rtc_available = False

        try:
            if self.config['capabilities'].get('gnss', False):
                self.gnss = GNSSModule()
                self.gnss_available = True
                print("✅ GNSS module khởi tạo thành công")
            else:
                print("ℹ️ GNSS không được bật trong cấu hình")
                self.gnss_available = False
        except Exception as e:
            print(f"⚠️ Không thể khởi tạo GNSS: {e}")
            self.gnss_available = False

        self._stop_flag = False
        self.cap = None
        self.video_writer = None
        self.audio_writer = None
        self.current_segment = None
        self.segment_start = None
        self.audio_frames = []  # Buffer for audio frames
        self.audio_device_index = None
        self.micro = None

        # Flask app with SocketIO for WebSocket stream
        self.app = Flask(__name__)
        self.app.debug = False  # Disable debug mode to prevent auto-reload
        self.socketio = SocketIO(self.app, cors_allowed_origins="*", async_mode='threading')
        self.frame_queue = []  # Simple queue for frames (latest only to reduce lag)
        self.frame_lock = threading.Lock()
        self.ws_clients = set()  # Track connected WebSocket clients

    def check_liscam(self):
        """Tìm index camera hoạt động - đơn giản như Flask example"""
        for cam in range(10):  # Thử lên đến /dev/video9
            cap = cv2.VideoCapture(cam)
            if cap.isOpened():
                cap.release()
                print(f"✅ Tìm thấy camera tại index {cam}")
                return cam
        print("❌ Không tìm thấy camera nào hoạt động!")
        return 0  # Fallback

    def initial(self):
        """Khởi tạo các thông số từ file cấu hình"""
        try:
            # Khởi tạo USB Storage Manager
            self.usb_manager = USBManager(
                path=self.config['paths']['record_root'],
                min_free_gb=self.config['storage'].get('min_free_gb', 1.0),
                min_free_percent=self.config['storage'].get('min_free_percent', 10),
                camera_id=self.config['device'].get('id', 'PICAM-DEFAULT')
            )
            
            # Kiểm tra và đợi USB storage
            if not self.usb_manager.is_available():
                print("⚠️ Đang đợi USB storage...")
                # Bắt đầu nhấp nháy LED khi không có USB
                self._start_led_blink()
                self.usb_manager.wait_until_available()
                # Dừng nhấp nháy khi đã có USB
                self._stop_led_blink()
            
            # Kiểm tra dung lượng trống
            if not self.usb_manager.has_enough_space():
                print("⚠️ Dung lượng trống không đủ, đang dọn dẹp...")
                self.usb_manager.cleanup_old_files()
                if not self.usb_manager.has_enough_space():
                    raise Exception("Không đủ dung lượng trống sau khi dọn dẹp")
            
            print("✅ USB Storage sẵn sàng")
            
            # Cấu hình video - sử dụng index thay vì path
            self.video_index = self.check_liscam()  # Lưu index int
            self.video_size = self.config['video']['v4l2_format']  # e.g., '640x480'
            self.video_fps = self.config['video']['v4l2_fps']
            width, height = map(int, self.video_size.split('x'))
            self.video_width = width
            self.video_height = height

            # Cấu hình audio nếu được bật
            if self.config['capabilities'].get('audio', False):
                self.micro = Micro()
                device_str = self.micro.get_first_available_device()
                
                if device_str:
                    # Parse device string
                    # Có thể là: "hw:1,0" hoặc "[1] HD camera: USB Audio (hw:1,0)"
                    self.audio_device_index = None
                    
                    # Case 1: Format "[index] name (hw:x,y)"
                    if device_str.startswith('['):
                        try:
                            parts = device_str.split(']')[0].split('[')
                            if len(parts) > 1:
                                self.audio_device_index = int(parts[1].strip())
                        except:
                            pass
                    
                    # Case 2: Just "hw:x,y" - need to find index by querying PyAudio
                    if self.audio_device_index is None and device_str.startswith('hw:'):
                        # Parse hw:x,y to get card and device numbers
                        try:
                            hw_parts = device_str.replace('hw:', '').split(',')
                            card_num = int(hw_parts[0])
                            
                            # Find PyAudio device index by searching for matching ALSA name
                            import pyaudio
                            p = pyaudio.PyAudio()
                            try:
                                for i in range(p.get_device_count()):
                                    info = p.get_device_info_by_index(i)
                                    name = info.get('name', '').lower()
                                    # Check if device name contains "hw:x,y" pattern
                                    if f"hw:{card_num}" in name.lower() or f"card{card_num}" in name.lower():
                                        if info.get('maxInputChannels', 0) > 0:
                                            self.audio_device_index = i
                                            print(f"   ↳ Tìm thấy PyAudio device index: {i} ({info['name']})")
                                            break
                            finally:
                                p.terminate()
                        except Exception as e:
                            print(f"⚠️ Lỗi parse hw string: {e}")
                    
                    self.audio_dev = device_str  # Giữ config cho log
                    
                    if self.audio_device_index is None:
                        print(f"⚠️ Không thể tìm PyAudio device index từ: {device_str}")
                        self.audio_device_index = None
                    else:
                        # Kiểm tra device có hỗ trợ sample rate từ config không
                        p = pyaudio.PyAudio()
                        try:
                            device_info = p.get_device_info_by_index(self.audio_device_index)
                            print(f"   ↳ Device info: {device_info.get('name')}")
                            print(f"   ↳ Max input channels: {device_info.get('maxInputChannels')}")
                            print(f"   ↳ Default sample rate: {device_info.get('defaultSampleRate')}Hz")
                            
                            # Kiểm tra số channels hỗ trợ
                            max_channels = int(device_info.get('maxInputChannels', 0))
                            if max_channels == 0:
                                print(f"⚠️ Device không hỗ trợ input")
                                self.audio_device_index = None
                            else:
                                # Chọn channels phù hợp
                                config_channels = self.config['audio'].get('channels', 1)
                                self.audio_channels = min(config_channels, max_channels)
                                
                                # Lấy default sample rate từ thiết bị
                                default_rate = int(device_info.get('defaultSampleRate', 44100))
                                
                                # Thử default rate trước (thường là rate device hỗ trợ tốt nhất)
                                supported_rates = [default_rate, 44100, 48000, 16000, 22050, 32000, 8000, 11025]
                                # Loại bỏ duplicate
                                supported_rates = list(dict.fromkeys(supported_rates))
                                
                                self.audio_rate = None
                                for rate in supported_rates:
                                    # Thử với cả mono và stereo
                                    for test_channels in [self.audio_channels, 1, 2]:
                                        if test_channels > max_channels:
                                            continue
                                        try:
                                            # Test với input stream
                                            test_stream = p.open(
                                                format=pyaudio.paInt16,
                                                channels=test_channels,
                                                rate=rate,
                                                input=True,
                                                input_device_index=self.audio_device_index,
                                                frames_per_buffer=1024,
                                                start=False
                                            )
                                            test_stream.close()
                                            self.audio_rate = rate
                                            self.audio_channels = test_channels
                                            print(f"   ✅ Tìm thấy cấu hình phù hợp: {rate}Hz, {test_channels}ch")
                                            break
                                        except Exception as e:
                                            # Debug: in ra lỗi cụ thể
                                            if "Invalid sample rate" in str(e):
                                                pass  # Rate không hỗ trợ, thử rate khác
                                            continue
                                    if self.audio_rate is not None:
                                        break
                                
                                if self.audio_rate is None:
                                    print(f"⚠️ Không tìm được sample rate phù hợp")
                                    print(f"   ↳ Thử các rate: {supported_rates}")
                                    print(f"   ↳ Default rate của device: {default_rate}Hz")
                                    self.audio_device_index = None
                        except Exception as e:
                            print(f"⚠️ Lỗi kiểm tra device: {e}")
                            self.audio_device_index = None
                        finally:
                            p.terminate()
                else:
                    self.audio_device_index = None
                    print("⚠️ Không tìm thấy thiết bị audio.")
                
                # Kiểm tra cuối cùng
                if self.audio_device_index is not None and hasattr(self, 'audio_rate'):
                    print(f"   ↳ Audio config: {self.audio_channels}ch @ {self.audio_rate}Hz")
                else:
                    self.audio_device_index = None
                    print("   ✖️ Audio: Không thể khởi tạo")
            else:
                self.audio_device_index = None
            
            # Cấu hình lưu trữ
            self.output_dir = self.config['paths']['record_root']
            self.segment_seconds = self.config['storage']['segment_seconds']
            
            # Đảm bảo thư mục tồn tại
            os.makedirs(self.output_dir, exist_ok=True)
            os.makedirs(self.hls_dir, exist_ok=True)
            
            print("✅ Đã khởi tạo cấu hình:")
            print(f"   ↳ Video: index {self.video_index} ({self.video_size} @ {self.video_fps}fps)")
            if hasattr(self, 'audio_dev') and self.audio_device_index is not None:
                print(f"   ↳ Audio: {self.audio_dev} (index {self.audio_device_index}, {self.audio_channels}ch @ {self.audio_rate}Hz)")
            else:
                print("   ✖️ Audio: Không có thiết bị audio")
            print(f"   ↳ Storage: {self.output_dir}")
            print(f"   ↳ Segment: {self.segment_seconds}s")
            
            # Setup Flask routes (always setup broadcast thread)
            self.setup_flask_routes()
            print("   ✅ Flask routes đã được thiết lập")
            return True
            
        except KeyError as e:
            print(f"❌ Lỗi cấu hình: Thiếu thông số {e}")
            return False
        except Exception as e:
            print(f"❌ Lỗi khởi tạo: {e}")
            return False

    def _get_rtc_time(self):
        """Đọc thời gian từ RTC module"""
        try:
            if self.rtc_available:
                rtc_time = self.rtc.read_time()
                return rtc_time.strftime("%Y-%m-%d %H:%M:%S")
            else:
                return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        except Exception as e:
            # print(f"⚠️ Lỗi đọc RTC: {e}")
            return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _get_gps_info(self):
        """Đọc thông tin GPS từ GNSS module"""
        try:
            if self.gnss_available:
                gps_data = self.gnss.get_location()
                if gps_data and gps_data.get('fix_quality', 0) > 0:
                    lat = gps_data.get('latitude', 0)
                    lon = gps_data.get('longitude', 0)
                    speed = gps_data.get('speed', 0)
                    alt = gps_data.get('altitude', 0)
                    sats = gps_data.get('satellites', 0)
                    return f"GPS: {lat:.6f}, {lon:.6f} | Alt: {alt:.1f}m | Spd: {speed:.1f}km/h | Sats: {sats}"
                return "GPS: Chờ tín hiệu"
            return None
        except Exception as e:
            print(f"⚠️ Lỗi đọc GPS: {e}")
            return None

    def _get_overlay_text(self):
        """Lấy text overlay (thay vì file, dùng direct cho OpenCV)"""
        timestamp = self._get_rtc_time()
        gps_info = self._get_gps_info() or "GPS: Waiting for signal"
        return f"{timestamp}\n{gps_info}"

    def setup_flask_routes(self):
        """Setup WebSocket handlers for video stream"""
        socketio = self.socketio  # Reference to SocketIO instance
        ws_clients = self.ws_clients  # Reference to clients set
        
        # Only register routes once
        if '/' not in [rule.rule for rule in self.app.url_map.iter_rules()]:
            @self.app.route('/')
            def index():
                return "Recorder service running (WebSocket + MJPEG enabled)"
            
            @self.app.route('/stream')
            def mjpeg_stream():
                """MJPEG stream endpoint (không cần WebSocket)"""
                def gen_frames():
                    """Generator để stream MJPEG frames"""
                    while not self._stop_flag:
                        with self.frame_lock:
                            if not self.frame_queue:
                                time.sleep(0.05)
                                continue
                            frame = self.frame_queue[-1]
                        
                        # Encode frame as JPEG (lower quality = faster)
                        ret, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 60])
                        if ret:
                            frame_bytes = buffer.tobytes()
                            yield (b'--frame\r\n'
                                   b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
                        
                        time.sleep(1 / self.video_fps)  # Control FPS
                
                return Response(gen_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')
        
        @socketio.on('connect')
        def handle_connect():
            print(f"👤 Client connected")
            # We can't easily get session_id here without request, just increment
        
        @socketio.on('disconnect')
        def handle_disconnect():
            print(f"👋 Client disconnected")
            
        def broadcast_frame():
            """Broadcast video frame to all connected clients"""
            print("🎬 Broadcast thread started")
            frame_count = 0
            while not self._stop_flag:
                # Always broadcast if there are frames
                with self.frame_lock:
                    if not self.frame_queue:
                        time.sleep(0.05)
                        continue
                    frame = self.frame_queue[-1]
                    self.frame_queue = [frame]  # Keep only latest
                
                try:
                    # Encode frame as JPEG then base64 (lower quality = faster)
                    ret, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 60])
                    if ret:
                        b64_frame = base64.b64encode(buffer).decode('utf-8')
                        # Broadcast to all clients
                        socketio.emit('video_frame', {'frame': b64_frame})
                        frame_count += 1
                        if frame_count % 30 == 0:  # Log every 30 frames (2 seconds at 15fps)
                            print(f"📡 Broadcasted {frame_count} frames")
                except Exception as e:
                    print(f"❌ Error broadcasting frame: {e}")
                
                time.sleep(1 / self.video_fps)  # Control FPS
                
        # Start broadcasting thread
        self.broadcast_thread = threading.Thread(target=broadcast_frame, daemon=True)
        self.broadcast_thread.start()

    def _mux_to_mp4(self):
        """Ghép AVI + WAV thành MP4 bằng ffmpeg_merge_video_audio"""
        if not hasattr(self, 'segment_manager'):
            return
            
        paths = self.segment_manager.get_current_paths()
        video_file = paths['video']
        audio_file = paths['audio']
        mp4_file = paths['output']
        
        if not os.path.exists(video_file):
            print("⚠️ Không có file video để ghép.")
            return

        try:
            # Sử dụng ffmpeg_merge_video_audio để ghép
            if os.path.exists(audio_file):
                # Ghép video và audio
                ffmpeg_merge_video_audio(
                    video_file,
                    audio_file,
                    mp4_file,
                    video_codec="libx264",
                    audio_codec="aac",
                )
                print(f"✅ Ghép thành công: {mp4_file} (video AVI + audio WAV)")
            else:
                # Chỉ convert video sang MP4 không có audio
                ffmpeg_merge_video_audio(
                    video_file,
                    None,
                    mp4_file,
                    video_codec="libx264",
                    audio_codec="aac",
                )
                print(f"✅ Convert thành công: {mp4_file} (video only)")
            
            # Cleanup source files sau khi ghép thành công
            try:
                if os.path.exists(video_file):
                    os.remove(video_file)
                    print(f"   ↳ Đã xóa file video: {video_file}")
                if os.path.exists(audio_file):
                    os.remove(audio_file)
                    print(f"   ↳ Đã xóa file audio: {audio_file}")
            except Exception as e:
                print(f"⚠️ Lỗi xoá file nguồn: {e}")
                print(f"   ↳ Video exists: {os.path.exists(video_file)}")
                print(f"   ↳ Audio exists: {os.path.exists(audio_file)}")
                
        except Exception as e:
            print(f"⚠️ Lỗi ghép MP4: {e}")
            # Cleanup on error
            if os.path.exists(mp4_file):
                try:
                    os.remove(mp4_file)
                    print(f"   ↳ Đã xóa file MP4 lỗi: {mp4_file}")
                except Exception as e:
                    print(f"⚠️ Lỗi xóa file MP4: {e}")

    def _audio_thread(self):
        """Thread đọc và ghi audio độc lập"""
        if self.audio_device_index is None:
            print("⚠️ Không có thiết bị audio, audio thread không chạy")
            return

        # Cấu hình cố định để đảm bảo tính ổn định
        CHUNK = 1024
        FORMAT = pyaudio.paInt16
        CHANNELS = 1
        RATE = 48000

        p = pyaudio.PyAudio()
        try:
            stream = p.open(
                format=FORMAT,
                channels=CHANNELS,
                rate=RATE,
                frames_per_buffer=CHUNK,
                input=True,
                # input_device_index=self.audio_device_index
            )
            print(f"✅ Khởi tạo audio stream thành công ({RATE}Hz, {CHANNELS} channels)")
        except Exception as e:
            print(f"⚠️ Không thể mở audio stream: {e}")
            p.terminate()
            return

        # Đảm bảo có SegmentManager và segment đã được bắt đầu
        if not hasattr(self, 'segment_manager'):
            self.segment_manager = SegmentManager(self.output_dir, self.segment_seconds)
            
        # Đợi segment được khởi tạo bởi video thread
        wait_start = time.time()
        while self.segment_manager.current_segment is None:
            if time.time() - wait_start > 5:  # Timeout sau 5 giây
                print("⚠️ Timeout chờ video thread khởi tạo segment")
                return
            time.sleep(0.1)

        # Bắt đầu ghi audio vào segment hiện tại
        current_segment = self.segment_manager.get_current_paths()['audio']
        current_writer = wave.open(current_segment, 'wb')
        current_writer.setnchannels(CHANNELS)
        current_writer.setsampwidth(p.get_sample_size(FORMAT))  # 16-bit PCM
        current_writer.setframerate(RATE)
        audio_frames = []  # Initialize array to store frames

        while not self._stop_flag:
            try:
                data = stream.read(CHUNK)  # Đọc chunk data từ stream
                audio_frames.append(data)
                
                # Kiểm tra segment mới
                if self.segment_manager.should_start_new():
                    # Ghi toàn bộ frames vào file WAV
                    current_writer.writeframes(b''.join(audio_frames))
                    current_writer.close()
                    self.segment_manager.mark_complete('audio')
                    
                    # Đợi video hoàn thành và ghép file
                    if self.segment_manager.wait_for_merge(timeout=1.0):
                        self._mux_to_mp4()
                    
                    # Bắt đầu segment mới
                    current_segment = self.segment_manager.get_current_paths()['audio']
                    current_writer = wave.open(current_segment, 'wb')
                    current_writer.setnchannels(CHANNELS)
                    current_writer.setsampwidth(p.get_sample_size(FORMAT))
                    current_writer.setframerate(RATE)
                    audio_frames = []  # Reset frame buffer
                    
            except Exception as e:
                print(f"⚠️ Lỗi đọc audio: {e}")
                time.sleep(0.1)

        # Ghi nốt phần cuối
        if audio_frames:
            current_writer.writeframes(b''.join(audio_frames))
        current_writer.close()
        self.segment_manager.mark_complete('audio')
        
        # Cleanup
        stream.stop_stream()
        stream.close()
        p.terminate()
        print("✅ Audio thread stopped.")

    def _video_thread(self):
        """Thread đọc và ghi video độc lập với auto-reconnect"""
        cap = None
        reconnect_attempts = 0
        max_reconnect = 5
        
        def init_camera():
            """Khởi tạo hoặc khởi tạo lại camera - đơn giản như Flask example"""
            new_cap = cv2.VideoCapture(self.video_index)
            if new_cap.isOpened():
                # Chỉ set resolution, không set buffer hay FPS phức tạp
                new_cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.video_width)
                new_cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.video_height)
                return new_cap
            return None
        
        # Khởi tạo camera lần đầu
        cap = init_camera()
        self.cap = cap  # Lưu reference để cleanup sau
        
        if cap is None:
            print("❌ Không mở được camera!")
            return
        
        # Khởi tạo SegmentManager nếu chưa có
        if not hasattr(self, 'segment_manager'):
            self.segment_manager = SegmentManager(self.output_dir, self.segment_seconds)
            
        # Bắt đầu segment đầu tiên
        current_segment = self.segment_manager.start_new_segment()
        current_writer = cv2.VideoWriter(
            f"{current_segment}.avi",
            cv2.VideoWriter_fourcc(*'XVID'),
            self.video_fps,
            (self.video_width, self.video_height)
        )

        while not self._stop_flag:
            ret, frame = cap.read()
            if not ret:
                print("⚠️ Không đọc được frame, thử reconnect...")
                
                # Đóng camera hiện tại
                try:
                    cap.release()
                    time.sleep(1)  # Đợi driver reset
                except:
                    pass
                
                # Thử reconnect
                reconnect_attempts += 1
                if reconnect_attempts > max_reconnect:
                    print(f"❌ Đã thử reconnect {max_reconnect} lần thất bại, dừng video thread")
                    break
                
                print(f"🔄 Đang reconnect camera... (lần {reconnect_attempts}/{max_reconnect})")
                cap = init_camera()
                self.cap = cap
                
                if cap is None:
                    print("❌ Reconnect thất bại, thử lại sau 2 giây...")
                    time.sleep(2)
                    continue
                else:
                    print("✅ Reconnect camera thành công!")
                    reconnect_attempts = 0  # Reset counter khi thành công
                    continue

            # Reset reconnect counter khi đọc frame thành công
            reconnect_attempts = 0

            # Add overlay text direct (chỉ mỗi 2 giây thay vì mỗi frame)
            current_time = time.time()
            if not hasattr(self, '_last_overlay_update'):
                self._last_overlay_update = 0
            
            if current_time - self._last_overlay_update >= 1.0:
                self._overlay_text_cached = self._get_overlay_text()
                self._last_overlay_update = current_time
            
            # Dùng cached text
            if hasattr(self, '_overlay_text_cached'):
                overlay_text = self._overlay_text_cached
                lines = overlay_text.split('\n')
                y_offset = 10
                for line in lines:
                    cv2.putText(frame, line, (10, y_offset), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
                    y_offset += 25

            # Write video frame
            current_writer.write(frame)

            # Push frame for MJPEG stream (chỉ giữ frame mới nhất)
            with self.frame_lock:
                self.frame_queue = [frame]  # Overwrite thay vì append

            # Check if need new segment
            if self.segment_manager.should_start_new():
                current_writer.release()
                self.segment_manager.mark_complete('video')
                
                # Đợi audio hoàn thành và bắt đầu segment mới
                if self.segment_manager.wait_for_merge(timeout=1.0):
                    self._mux_to_mp4()
                    
                current_segment = self.segment_manager.start_new_segment()
                current_writer = cv2.VideoWriter(
                    f"{current_segment}.avi",
                    cv2.VideoWriter_fourcc(*'XVID'),
                    self.video_fps,
                    (self.video_width, self.video_height)
                )

            time.sleep(1 / self.video_fps)  # Control FPS

        # Final segment
        current_writer.release()
        self.segment_manager.mark_complete('video')
        if self.segment_manager.wait_for_merge(timeout=1.0):
            self._mux_to_mp4()
        cap.release()
        print("✅ Video thread stopped.")

    def start(self):
        # Check if threads already running
        if hasattr(self, '_video_thread_obj') and self._video_thread_obj and self._video_thread_obj.is_alive():
            print("⚠️ Video thread đang chạy!")
            return
        if hasattr(self, '_audio_thread_obj') and self._audio_thread_obj and self._audio_thread_obj.is_alive():
            print("⚠️ Audio thread đang chạy!")
            return

        # Kiểm tra lại storage trước khi bắt đầu ghi
        if hasattr(self, 'usb_manager'):
            if not self.usb_manager.is_available():
                print("⚠️ USB storage không khả dụng!")
                # Nhấp nháy LED khi USB không khả dụng
                self.led_control.blink(0.5)  # Nhấp nháy với tần số 0.5 giây
                return
            if not self.usb_manager.has_enough_space():
                print("⚠️ Không đủ dung lượng trống!")
                return

        self._stop_flag = False
        print(f"🚀 Bắt đầu ghi và stream (mỗi {self.segment_seconds}s lưu 1 file MP4 ghép video+audio)...")
        print("   ↳ Lưu tại:", self.output_dir)
        
        # Initialize segment manager before starting threads
        self.segment_manager = SegmentManager(self.output_dir, self.segment_seconds)
        
        # Start video thread first
        self._video_thread_obj = threading.Thread(target=self._video_thread, daemon=True)
        self._video_thread_obj.start()
        
        # Wait for video thread to initialize
        start_time = time.time()
        while self.segment_manager.current_segment is None:
            if time.time() - start_time > 5:
                print("⚠️ Timeout chờ video thread khởi tạo")
                return
            time.sleep(0.1)
            
        print("✅ Video thread đã khởi động")

        # Start audio thread if device available
        if self.audio_device_index is not None:
            self._audio_thread_obj = threading.Thread(target=self._audio_thread, daemon=True)
            self._audio_thread_obj.start()
            print("✅ Audio thread đã khởi động")

        time.sleep(2)  # Đợi setup

        if self._video_thread_obj.is_alive():
            print("✅ Video thread đã khởi động.")
            if hasattr(self, '_audio_thread_obj') and self._audio_thread_obj.is_alive():
                print("✅ Audio thread đã khởi động.")
            # Bật LED khi bắt đầu ghi
            self.led_control.on()
        else:
            print("❌ Video thread không khởi động được.")

    def _led_blink(self):
        """Hàm điều khiển LED nhấp nháy"""
        while self.led_running:
            self.led_control.on()
            time.sleep(0.5)
            self.led_control.off()
            time.sleep(0.5)

    def _start_led_blink(self):
        """Bắt đầu nhấp nháy LED trong thread riêng"""
        self.led_running = True
        self.led_thread = threading.Thread(target=self._led_blink)
        self.led_thread.daemon = True
        self.led_thread.start()

    def _stop_led_blink(self):
        """Dừng nhấp nháy LED"""
        self.led_running = False
        if self.led_thread:
            self.led_thread.join(timeout=1)
        self.led_control.off()

    def stop(self):
        self._stop_flag = True
        # Dừng video thread
        if hasattr(self, '_video_thread_obj') and self._video_thread_obj:
            print("⏱ Dừng video thread...")
            self._video_thread_obj.join(timeout=5)
            if self._video_thread_obj.is_alive():
                print("⚠️ Video thread vẫn đang chạy sau 5 giây timeout.")
                
        # Dừng audio thread
        if hasattr(self, '_audio_thread_obj') and self._audio_thread_obj:
            print("⏱ Dừng audio thread...")
            self._audio_thread_obj.join(timeout=5)
            if self._audio_thread_obj.is_alive():
                print("⚠️ Audio thread vẫn đang chạy sau 5 giây timeout.")
                
        # Tắt LED khi dừng ghi
        self.led_control.off()
        print("✅ Đã dừng các thread.")

    def cleanup(self):
        """
        Dừng an toàn threads, FFmpeg, các module phần cứng (LED, GNSS, RTC),
        tránh crash camera trên Raspberry Pi.
        """
        print("🧹 Bắt đầu cleanup...")

        # 1️⃣ Set stop flag để threads tự dừng
        self._stop_flag = True

        # 2️⃣ Dừng video/audio/mux threads
        self.stop()
        
        # 2.5️⃣ Force release camera nếu còn tồn đọng
        if hasattr(self, 'cap') and self.cap is not None:
            try:
                if self.cap.isOpened():
                    self.cap.release()
                    print("📹 Camera đã được release")
                    time.sleep(0.5)  # Đợi driver reset
            except Exception as e:
                print(f"⚠️ Lỗi release camera: {e}")
        
        # Force giải phóng tài nguyên OpenCV
        try:
            cv2.destroyAllWindows()
        except:
            pass

        # 3️⃣ Tắt LED (nếu có)
        if hasattr(self, 'led_control'):
            try:
                self.led_control.off()
                print("💡 LED đã tắt")
            except Exception as e:
                print(f"⚠️ Lỗi khi tắt LED: {e}")

        # 4️⃣ Đóng GNSS module (nếu có)
        if hasattr(self, 'gnss') and getattr(self, 'gnss_available', False):
            try:
                self.gnss.close()
                print("📡 GNSS module đã đóng")
            except Exception as e:
                print(f"⚠️ Lỗi khi đóng GNSS: {e}")

        # 5️⃣ Đóng RTC module (nếu có)
        if hasattr(self, 'rtc') and getattr(self, 'rtc_available', False):
            try:
                self.rtc.close()
                print("⏰ RTC module đã đóng")
            except Exception as e:
                print(f"⚠️ Lỗi khi đóng RTC: {e}")

        print("✅ Cleanup hoàn tất, tất cả module đã dừng an toàn.")


def signal_handler(signum, frame):
    """Xử lý tín hiệu để thoát an toàn"""
    print("\n🛑 Nhận tín hiệu dừng, đang thoát...")
    if 'recorder' in globals():
        recorder.cleanup()
    sys.exit(0)

if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        recorder = PiStreamer()
        if not recorder.initial():
            print("❌ Khởi tạo thất bại, đang thoát...")
            sys.exit(1)

        recorder.start()  # 🔹 Start recorder threads
        print("📡 Đang stream... WebSocket server tại ws://localhost:5000")
        
        # Run SocketIO app
        recorder.socketio.run(recorder.app, host="0.0.0.0", port=5000, allow_unsafe_werkzeug=True)

    except KeyboardInterrupt:
        print("\n🛑 Đang thoát...")
        recorder.cleanup()
    except Exception as e:
        print(f"❌ Lỗi chương trình: {e}")
        sys.exit(1)