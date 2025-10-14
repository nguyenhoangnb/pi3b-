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
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import cv2
from flask import Flask, Response  # For MJPEG stream
from moviepy import VideoFileClip, AudioFileClip  # For merging video + audio to MP4 (pip install moviepy)
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

        # Flask app for MJPEG stream (video only for web)
        self.app = Flask(__name__)
        self.frame_queue = []  # Simple queue for frames (latest only to reduce lag)
        self.frame_lock = threading.Lock()

    def check_liscam(self):
        """Tìm index camera hoạt động bằng cách thử các index từ 0 đến 9"""
        for cam in range(10):  # Thử lên đến /dev/video9
            cap = cv2.VideoCapture(cam, cv2.CAP_V4L2)
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
                device_index_raw = self.micro.get_first_available_device()
                if device_index_raw:
                    # Sử dụng helper function để convert
                    self.audio_device_index = _get_pyaudio_device_index(device_index_raw)
                    self.audio_dev = self.config['audio']['device']  # Giữ config cho log
                    
                    if self.audio_device_index is None:
                        print(f"⚠️ Không thể tìm device audio: {device_index_raw}")
                        self.audio_device_index = None
                    else:
                        # Kiểm tra device có hỗ trợ sample rate từ config không
                        p = pyaudio.PyAudio()
                        try:
                            device_info = p.get_device_info_by_index(self.audio_device_index)
                            supported_rates = [8000, 11025, 16000, 22050, 32000, 44100, 48000, 96000]
                            default_rate = int(device_info.get('defaultSampleRate', 44100))
                            
                            # Ưu tiên sample rate từ config nếu được hỗ trợ
                            config_rate = self.config['audio'].get('sample_rate', 44100)
                            if config_rate == default_rate:
                                self.audio_rate = config_rate
                            else:
                                # Thử các sample rate phổ biến
                                for rate in supported_rates:
                                    try:
                                        test_stream = p.open(
                                            format=pyaudio.paInt16,
                                            channels=1,
                                            rate=rate,
                                            input=True,
                                            input_device_index=self.audio_device_index,
                                            frames_per_buffer=1024,
                                            start=False
                                        )
                                        test_stream.close()
                                        self.audio_rate = rate
                                        print(f"   ✅ Tìm thấy sample rate phù hợp: {rate}Hz")
                                        break
                                    except:
                                        continue
                                else:
                                    print(f"⚠️ Không tìm được sample rate phù hợp, dùng mặc định {default_rate}Hz")
                                    self.audio_rate = default_rate
                        finally:
                            p.terminate()
                else:
                    self.audio_device_index = None
                    print("⚠️ Không tìm thấy thiết bị audio.")
                
                # Cấu hình audio cuối cùng
                self.audio_channels = self.config['audio'].get('channels', 1)
                print(f"   ↳ Audio sample rate: {self.audio_rate}Hz")
            
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
                print("   ↳ Audio: Không có thiết bị audio")
            print(f"   ↳ Storage: {self.output_dir}")
            print(f"   ↳ Segment: {self.segment_seconds}s")
            
            self.setup_flask_routes()
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
            print(f"⚠️ Lỗi đọc RTC: {e}")
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
        """Setup Flask routes for MJPEG stream (video only)"""
        @self.app.route('/video_feed')
        def video_feed():
            def gen_frames():
                while not self._stop_flag:
                    with self.frame_lock:
                        if self.frame_queue:
                            # Keep only latest frame to avoid lag
                            frame = self.frame_queue[-1]
                            self.frame_queue = [frame]  # Update to latest
                        else:
                            yield b''  # Empty frame
                            continue
                    ret, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                    if ret:
                        frame_bytes = buffer.tobytes()
                        yield (b'--frame\r\n'
                               b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
                    time.sleep(1 / self.video_fps)
            return Response(gen_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

    def _mux_to_mp4(self):
        """Ghép AVI + WAV thành MP4 bằng MoviePy (no FFmpeg subprocess)"""
        if not self.current_segment:
            return
        
        video_file = f"{self.current_segment}.avi"
        audio_file = f"{self.current_segment}.wav"
        mp4_file = f"{self.current_segment}.mp4"
        
        if not os.path.exists(video_file):
            print("⚠️ Không có file video để ghép.")
            return
        
        try:
            video = VideoFileClip(video_file)
            if os.path.exists(audio_file):
                audio = AudioFileClip(audio_file)
                final = video.set_audio(audio)
            else:
                final = video
            final.write_videofile(mp4_file, codec='libx264', audio_codec='aac' if os.path.exists(audio_file) else None, verbose=False, logger=None)
            print(f"✅ Ghép thành công: {mp4_file} (video AVI + audio WAV)")
            
            # Xóa file tạm
            os.remove(video_file)
            if os.path.exists(audio_file):
                os.remove(audio_file)
            final.close()
            video.close()
            if os.path.exists(audio_file):
                audio.close()
        except Exception as e:
            print(f"⚠️ Lỗi ghép MP4: {e}")

    def _start_new_segment(self):
        """Bắt đầu segment mới cho video + audio"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.current_segment = f"{self.output_dir}/{timestamp}_cam0"
        
        # Video writer (AVI for native OpenCV)
        fourcc = cv2.VideoWriter_fourcc(*'XVID')  # Or 'MJPG' if XVID not work
        self.video_writer = cv2.VideoWriter(f"{self.current_segment}.avi", fourcc, self.video_fps, (self.video_width, self.video_height))
        
        # Audio writer (WAV)
        if self.audio_device_index is not None:
            self.audio_writer = wave.open(f"{self.current_segment}.wav", 'wb')
            self.audio_writer.setnchannels(self.audio_channels)
            self.audio_writer.setsampwidth(2)  # 16-bit
            self.audio_writer.setframerate(self.audio_rate)
            self.audio_frames = []  # Reset buffer

        self.segment_start = time.time()
        print(f"📹 Bắt đầu segment mới: {self.current_segment} (AVI + WAV)")

    def _video_audio_thread(self):
        """Thread đọc video + audio, ghi segment, push frame cho stream, ghép MP4 khi kết thúc segment"""
        cap = cv2.VideoCapture(self.video_index, cv2.CAP_V4L2)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.video_width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.video_height)
        cap.set(cv2.CAP_PROP_FPS, self.video_fps)

        if not cap.isOpened():
            print("❌ Không mở được camera!")
            return

        p = pyaudio.PyAudio()
        stream = None
        if self.audio_device_index is not None:
            try:
                stream = p.open(
                    format=pyaudio.paInt16,
                    channels=self.audio_channels,
                    rate=self.audio_rate,
                    input=True,
                    input_device_index=self.audio_device_index,
                    frames_per_buffer=1024
                )
                print(f"✅ Khởi tạo audio stream thành công ({self.audio_rate}Hz)")
            except Exception as e:
                print(f"⚠️ Không thể mở audio stream: {e}")
                stream = None

        self._start_new_segment()

        while not self._stop_flag:
            ret, frame = cap.read()
            if not ret:
                print("⚠️ Không đọc được frame.")
                time.sleep(0.1)
                continue

            # Add overlay text direct
            overlay_text = self._get_overlay_text()
            lines = overlay_text.split('\n')
            y_offset = 10
            for line in lines:
                cv2.putText(frame, line, (10, y_offset), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
                y_offset += 25

            # Write raw BGR24 bytes to video writer
            self.video_writer.write(frame)

            # Push frame for MJPEG stream
            with self.frame_lock:
                self.frame_queue.append(frame)

            # Read and buffer audio if available
            if stream:
                try:
                    data = stream.read(1024, exception_on_overflow=False)
                    self.audio_frames.append(data)
                except Exception as e:
                    print(f"⚠️ Audio read error: {e}")

            # Check segment time
            if time.time() - self.segment_start >= self.segment_seconds:
                self.video_writer.release()
                if self.audio_writer:
                    self.audio_writer.writeframes(b''.join(self.audio_frames))
                    self.audio_writer.close()
                self._mux_to_mp4()  # Ghép AVI + WAV thành MP4
                self._start_new_segment()

            time.sleep(1 / self.video_fps)  # Control FPS

        # Final segment
        self.video_writer.release()
        if self.audio_writer:
            self.audio_writer.writeframes(b''.join(self.audio_frames))
            self.audio_writer.close()
        self._mux_to_mp4()  # Ghép cuối
        if stream:
            stream.stop_stream()
            stream.close()
            p.terminate()
        cap.release()
        print("✅ Video/Audio thread stopped.")

    def start(self):
        # Check if thread already running
        if hasattr(self, '_thread') and self._thread and self._thread.is_alive():
            print("⚠️ Streaming đang chạy!")
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
        print("   ↳ HLS tại:", self.hls_dir)

        # Start thread
        self._thread = threading.Thread(target=self._video_audio_thread, daemon=True)
        self._thread.start()

        time.sleep(2)  # Đợi setup

        if self._thread.is_alive():
            print("✅ Streaming thread đã khởi động thành công.")
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
        if hasattr(self, '_thread') and self._thread:
            print("⏱ Dừng video/audio thread...")
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                print("⚠️ Video thread vẫn đang chạy sau 5 giây timeout.")
        # Tắt LED khi dừng ghi
        self.led_control.off()
        print("✅ Đã dừng.")

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

        recorder.start()  # 🔹 chỉ chạy 1 lần
        print("📡 Đang stream... Nhấn Ctrl+C để dừng.")

        while True:
            time.sleep(1)  # Giữ chương trình chạy, không tạo thêm tiến trình mới

    except KeyboardInterrupt:
        print("\n🛑 Đang thoát...")
        recorder.cleanup()
    except Exception as e:
        print(f"❌ Lỗi chương trình: {e}")
        sys.exit(1)