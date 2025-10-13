#!/usr/bin/env python3
import os
import subprocess
import time
import signal
from datetime import datetime
import threading
from pathlib import Path
import sys
import queue
import tempfile
import pyaudio  # Thêm import PyAudio cho audio capture
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import cv2
from firmware.hal.usb_manager import USBManager    
from firmware.hal.gpio_leds import gpioLed
from firmware.hal.gnss import GNSSModule
from firmware.hal.rtc import rtcModule
from firmware.hal.micro import Micro
from firmware.config.config_loader import load

class PiStreamer:
    def __init__(self,
                 video_dev="/dev/video0",
                 audio_dev="hw:1,0",
                 output_dir="/media/ssd",
                 hls_dir="/tmp/picam_hls",
                 segment_seconds=600,
                 led_pin=26):  # thêm tham số LED pin
        self.list_video = ["/dev/video0", "/dev/video1"]
        self.video_dev = video_dev
        self.audio_dev = audio_dev
        self.output_dir = output_dir
        self.hls_dir = hls_dir
        self.segment_seconds = segment_seconds
        self.ffmpeg_process = None
        self.config_file = Path(__file__).parent.parent / 'config' / 'device_full.yaml'
        self.config = load(self.config_file)
        # Khởi tạo LED với GPIO pin từ config
        self.led_control = gpioLed(self.config['gpio'].get('record_led', 26))
        self.led_thread = None
        self.led_running = False
        
        self.initial()
        self._stop_flag = False
        self._overlay_thread = None  # Giữ nhưng sẽ không dùng file nữa

        self.micro = None
        # Khởi tạo RTC module
        try:
            self.rtc = rtcModule()
            self.rtc_available = True
            print("✅ RTC module khởi tạo thành công")
        except Exception as e:
            print(f"⚠️ Không thể khởi tạo RTC: {e}")
            self.rtc_available = False

        # Khởi tạo GNSS module
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

        # Threads cho streaming mới (OpenCV + PyAudio + FFmpeg mux)
        self.video_thread = None
        self.audio_thread = None
        self.mux_thread = None
        self.video_pipe = None
        self.audio_pipe = None

    def check_liscam(self):
        for cam in range(2):
            cap = cv2.VideoCapture(cam)
            if cap.isOpened():
                cap.release()
                return f"/dev/video{cam}"
        return "/dev/video0"  # Fallback

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
            
            # Cấu hình video
            self.video_dev = self.check_liscam()
            self.video_size = self.config['video']['v4l2_format']
            self.video_fps = self.config['video']['v4l2_fps']

            # Cấu hình audio nếu được bật
            if self.config['capabilities'].get('audio', False):
                self.micro = Micro()
                if self.micro.get_first_available_device():
                    self.audio_dev = self.config['audio']['device']
                print("Audio", self.audio_dev)
                self.audio_rate = self.config['audio'].get('sample_rate', 48000)
                self.audio_channels = self.config['audio'].get('channels', 1)
            
            # Cấu hình lưu trữ
            self.output_dir = self.config['paths']['record_root']
            self.segment_seconds = self.config['storage']['segment_seconds']
            
            # Đảm bảo thư mục tồn tại
            os.makedirs(self.output_dir, exist_ok=True)
            os.makedirs(self.hls_dir, exist_ok=True)
            
            print("✅ Đã khởi tạo cấu hình:")
            print(f"   ↳ Video: {self.video_dev} ({self.video_size} @ {self.video_fps}fps)")
            if hasattr(self, 'audio_dev') and self.micro.get_first_available_device():
                print(f"   ↳ Audio: {self.audio_dev} ({self.audio_channels}ch @ {self.audio_rate}Hz)")
            else:
                print("   ↳ Audio: Không có thiết bị audio")
            print(f"   ↳ Storage: {self.output_dir}")
            print(f"   ↳ Segment: {self.segment_seconds}s")
            
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

    # Bỏ _update_overlay_file vì dùng direct text trong OpenCV

    def _build_mux_cmd(self):
        """Tạo lệnh FFmpeg mux từ pipes (raw video + audio)"""
        hls_path = os.path.join(self.hls_dir, "live.m3u8")
        video_size = "640x480"  # Hardcode cho đơn giản, có thể lấy từ config
        cmd = [
            "ffmpeg",
            "-hide_banner", "-loglevel", "error",
            "-f", "rawvideo",
            "-pixel_format", "bgr24",  # OpenCV default
            "-video_size", video_size,
            "-framerate", str(self.video_fps),
            "-i", self.video_pipe,  # Raw video pipe
        ]

        # Optional audio part
        if self.micro and self.micro.get_first_available_device():
            cmd += [
                "-f", "s16le",  # Raw audio format từ PyAudio
                "-ar", str(self.audio_rate),
                "-ac", str(self.audio_channels),
                "-i", self.audio_pipe,  # Raw audio pipe
                "-c:a", "aac",
                "-b:a", "128k",
            ]
            map_args = ["-map", "0:v", "-map", "1:a"]
        else:
            print("⚠️ Không có thiết bị audio, sẽ chỉ ghi hình (không có tiếng).")
            map_args = ["-map", "0:v"]

        # Video encoding (không cần vf vì overlay đã add trong OpenCV)
        cmd += [
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "23",
        ]

        # Mapping and output
        cmd += map_args + [
            "-f", "tee",
            f"[f=segment:strftime=1:segment_time={self.segment_seconds}:reset_timestamps=1]",
            f"'{self.output_dir}/%Y%m%d_%H%M%S_cam0.mp4'|[f=hls:hls_time=4:hls_list_size=5:hls_flags=delete_segments]{hls_path}"
        ]
        print("Mux cmd:", " ".join(cmd))  # Debug
        return cmd

    def _video_thread_func(self):
        """Thread đọc video từ OpenCV, add overlay, write raw vào pipe"""
        cap = cv2.VideoCapture(self.video_dev, cv2.CAP_V4L2)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        cap.set(cv2.CAP_PROP_FPS, self.video_fps)

        if not cap.isOpened():
            print("❌ Không mở được camera!")
            return

        with open(self.video_pipe, 'wb') as pipe:
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

                # Write raw BGR24 bytes vào pipe
                pipe.write(frame.tobytes())
                pipe.flush()

                time.sleep(1 / self.video_fps)  # Control FPS

        cap.release()
        print("✅ Video thread stopped.")

    def _audio_thread_func(self):
        """Thread đọc audio từ PyAudio, write raw vào pipe"""
        if not self.micro or not self.micro.get_first_available_device():
            print("⚠️ Không có audio device, bỏ qua audio thread.")
            return

        p = pyaudio.PyAudio()
        try:
            stream = p.open(
                format=pyaudio.paInt16,
                channels=self.audio_channels,
                rate=self.audio_rate,
                input=True,
                input_device_index=self.micro.get_first_available_device(),
                frames_per_buffer=1024
            )

            with open(self.audio_pipe, 'wb') as pipe:
                while not self._stop_flag:
                    try:
                        data = stream.read(1024, exception_on_overflow=False)
                        pipe.write(data)
                        pipe.flush()
                    except Exception as e:
                        print(f"⚠️ Audio read error: {e}")
                        time.sleep(0.01)

        except Exception as e:
            print(f"❌ Audio init error: {e}")
        finally:
            stream.stop_stream()
            stream.close()
            p.terminate()
            print("✅ Audio thread stopped.")

    def _mux_thread_func(self):
        """Thread chạy FFmpeg mux từ pipes"""
        cmd = self._build_mux_cmd()
        self.ffmpeg_process = subprocess.Popen(cmd)
        self.ffmpeg_process.wait()
        if self.ffmpeg_process.returncode != 0:
            print(f"⚠️ Mux FFmpeg exited with code {self.ffmpeg_process.returncode}")
        else:
            print("✅ Mux thread stopped.")

    def start(self):
        if self.video_thread and self.video_thread.is_alive():
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

        # Tạo named pipes
        temp_dir = tempfile.gettempdir()
        self.video_pipe = os.path.join(temp_dir, 'video_pipe.raw')
        self.audio_pipe = os.path.join(temp_dir, 'audio_pipe.raw')
        os.mkfifo(self.video_pipe)
        if self.micro and self.micro.get_first_available_device():
            os.mkfifo(self.audio_pipe)

        self._stop_flag = False
        print(f"🚀 Bắt đầu ghi và stream (mỗi {self.segment_seconds}s lưu 1 file)...")
        print("   ↳ Lưu tại:", self.output_dir)
        print("   ↳ HLS tại:", self.hls_dir)

        # Start threads
        self.video_thread = threading.Thread(target=self._video_thread_func, daemon=True)
        self.audio_thread = threading.Thread(target=self._audio_thread_func, daemon=True) if self.micro and self.micro.get_first_available_device() else None
        self.mux_thread = threading.Thread(target=self._mux_thread_func, daemon=True)

        self.video_thread.start()
        if self.audio_thread:
            self.audio_thread.start()
        self.mux_thread.start()

        time.sleep(2)  # Đợi setup

        if self.video_thread.is_alive():
            print("✅ Streaming threads đã khởi động thành công.")
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
        if self.ffmpeg_process and self.ffmpeg_process.poll() is None:
            print("🛑 Dừng FFmpeg mux...")
            self.ffmpeg_process.send_signal(signal.SIGINT)
            try:
                self.ffmpeg_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.ffmpeg_process.kill()
            # Tắt LED khi dừng ghi
            self.led_control.off()
            print("✅ Đã dừng.")
        else:
            print("⚠️ Không có tiến trình FFmpeg đang chạy.")

        # Cleanup pipes
        if self.video_pipe and os.path.exists(self.video_pipe):
            os.unlink(self.video_pipe)
        if self.audio_pipe and os.path.exists(self.audio_pipe):
            os.unlink(self.audio_pipe)

    def cleanup(self):
        """
        Dừng an toàn threads, FFmpeg, các module phần cứng (LED, GNSS, RTC),
        tránh crash camera trên Raspberry Pi.
        """
        print("🧹 Bắt đầu cleanup...")

        # 1️⃣ Set stop flag để threads tự dừng
        self._stop_flag = True

        # 2️⃣ Dừng video/audio/mux threads
        if self.video_thread:
            print("⏱ Dừng video thread...")
            self.video_thread.join(timeout=5)
            if self.video_thread.is_alive():
                print("⚠️ Video thread vẫn đang chạy sau 5 giây timeout.")

        if self.audio_thread:
            print("⏱ Dừng audio thread...")
            self.audio_thread.join(timeout=5)
            if self.audio_thread.is_alive():
                print("⚠️ Audio thread vẫn đang chạy sau 5 giây timeout.")

        # 3️⃣ Dừng FFmpeg an toàn (mux)
        if hasattr(self, "ffmpeg_process") and self.ffmpeg_process and self.ffmpeg_process.poll() is None:
            print("🛑 Dừng FFmpeg...")
            try:
                # gửi SIGINT để FFmpeg flush buffer
                self.ffmpeg_process.send_signal(signal.SIGINT)
                self.ffmpeg_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                print("⚠️ FFmpeg không phản hồi, kill đột ngột...")
                self.ffmpeg_process.kill()
                self.ffmpeg_process.wait()
            print(f"✅ FFmpeg đã dừng, returncode={self.ffmpeg_process.returncode}")

        # 4️⃣ Cleanup pipes
        if self.video_pipe and os.path.exists(self.video_pipe):
            os.unlink(self.video_pipe)
        if self.audio_pipe and os.path.exists(self.audio_pipe):
            os.unlink(self.audio_pipe)

        # 5️⃣ Tắt LED (nếu có)
        if hasattr(self, 'led_control'):
            try:
                self.led_control.off()
                print("💡 LED đã tắt")
            except Exception as e:
                print(f"⚠️ Lỗi khi tắt LED: {e}")

        # 6️⃣ Đóng GNSS module (nếu có)
        if hasattr(self, 'gnss') and getattr(self, 'gnss_available', False):
            try:
                self.gnss.close()
                print("📡 GNSS module đã đóng")
            except Exception as e:
                print(f"⚠️ Lỗi khi đóng GNSS: {e}")

        # 7️⃣ Đóng RTC module (nếu có)
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