#!/usr/bin/env python3
import os
import subprocess
import time
import signal
from datetime import datetime
import threading
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from firmware.hal.usb_manager import USBManager    
from firmware.hal.gpio_leds import gpioLed
from firmware.hal.gnss import GNSSModule
from firmware.hal.rtc import rtcModule
from firmware.config.config_loader import load
class PiStreamer:
    def __init__(self,
                 video_dev="/dev/video0",
                 audio_dev="hw:1,0",
                 output_dir="/media/ssd",
                 hls_dir="/tmp/picam_hls",
                 segment_seconds=600,
                 led_pin=26):  # thêm tham số LED pin
        self.video_dev = video_dev
        self.audio_dev = audio_dev
        self.output_dir = output_dir
        self.hls_dir = hls_dir
        self.segment_seconds = segment_seconds
        self.ffmpeg_process = None
        self.config_file = Path(__file__).parent.parent / 'config' / 'device_full.yaml'
        self.config = load(self.config_file)
        self.initial()
        # Khởi tạo LED với GPIO pin từ config
        self.led_control = gpioLed(self.config['gpio'].get('record_led', 26))
        self.led_thread = None
        self.led_running = False
        
        self.overlay_file = "/tmp/overlay.txt"
        self._stop_flag = False
        self._overlay_thread = None
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
            self.video_dev = self.config['video']['v4l2_device']
            self.video_size = self.config['video']['v4l2_format']
            self.video_fps = self.config['video']['v4l2_fps']

            # Cấu hình audio nếu được bật
            if self.config['capabilities'].get('audio', False):
                self.audio_dev = self.config['audio']['device']
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
            if hasattr(self, 'audio_dev'):
                print(f"   ↳ Audio: {self.audio_dev} ({self.audio_channels}ch @ {self.audio_rate}Hz)")
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
    def _update_overlay_file(self):
        os.makedirs(os.path.dirname(self.overlay_file), exist_ok=True)
        while not self._stop_flag:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            gps_info = self._get_gps_info() or "GPS: Waiting for signal"
            with open(self.overlay_file, "w") as f:
                f.write(f"{timestamp}\n{gps_info}")
            time.sleep(1)
    def _build_ffmpeg_cmd(self):
        """Tạo lệnh FFmpeg lưu file YYYYMMDD_HHMMSS_cam0.mp4 + overlay timestamp + GPS"""
        hls_path = os.path.join(self.hls_dir, "live.m3u8")

        # 🔹 Tên file theo thời gian, không tạo thư mục con
        filename = datetime.now().strftime("%Y%m%d_%H%M%S") + "_cam0.mp4"
        record_path = os.path.join(self.output_dir, filename)
        os.makedirs(self.output_dir, exist_ok=True)

        # 🔹 Overlay text (đọc từ /tmp/overlay.txt nếu có thread cập nhật)
        display_text = (
            "drawtext=textfile=/tmp/overlay.txt:reload=1"
            ":fontcolor=white"
            ":fontsize=24"
            ":box=1"
            ":boxcolor=black@0.5"
            ":x=10"
            ":y=10"
            ":line_spacing=5"
        )

        cmd = [
            "ffmpeg",
            "-hide_banner", "-loglevel", "error",
            "-f", "v4l2", "-framerate", "25", "-video_size", "640x480",
            "-i", self.video_dev,
            "-f", "alsa", "-i", self.audio_dev,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-vf", display_text,
            "-c:a", "aac", "-b:a", "128k",
            "-map", "0:v", "-map", "1:a",
            "-f", "tee",
            (
                f"[f=segment:strftime=1:segment_time={self.segment_seconds}:reset_timestamps=1]"
                f"'{self.output_dir}/%Y%m%d_%H%M%S_cam0.mp4'|"
                f"[f=hls:hls_time=4:hls_list_size=5:hls_flags=delete_segments]{hls_path}"
            )
        ]

        return cmd



    def start(self):
        if self.ffmpeg_process and self.ffmpeg_process.poll() is None:
            print("⚠️ FFmpeg đang chạy!")
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

        cmd = self._build_ffmpeg_cmd()
        self._stop_flag = False
        self._overlay_thread = threading.Thread(target=self._update_overlay_file, daemon=True)
        self._overlay_thread.start()
        print(f"🚀 Bắt đầu ghi và stream (mỗi {self.segment_seconds}s lưu 1 file)...")
        print("   ↳ Lưu tại:", self.output_dir)
        print("   ↳ HLS tại:", self.hls_dir)
        print("   ↳ URL: http://<ip-pi>:8080/hls/stream.m3u8")

        self.ffmpeg_process = subprocess.Popen(cmd)
        time.sleep(2)

        if self.ffmpeg_process.poll() is None:
            print("✅ FFmpeg đã khởi động thành công.")
            # Bật LED khi bắt đầu ghi
            self.led_control.on()
        else:
            print("❌ FFmpeg không thể khởi động, kiểm tra thiết bị video/audio.")

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
            self.led_thread.join()
        self.led_control.off()

    def stop(self):
        if self.ffmpeg_process and self.ffmpeg_process.poll() is None:
            print("🛑 Dừng FFmpeg...")
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
    def cleanup(self):
        if self.ffmpeg_process and self.ffmpeg_process.poll() is None:
            print("🛑 Dừng FFmpeg...")
            self.ffmpeg_process.send_signal(signal.SIGINT)
            try:
                self.ffmpeg_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.ffmpeg_process.kill()
        self._stop_flag = True
        if self._overlay_thread:
            self._overlay_thread.join(timeout=2)
        # Tắt LED
        if hasattr(self, 'led_control'):
            self.led_control.off()

        # Đóng GNSS module
        if hasattr(self, 'gnss') and self.gnss_available:
            try:
                self.gnss.close()
                print("✅ Đã đóng GNSS module")
            except Exception as e:
                print(f"⚠️ Lỗi khi đóng GNSS: {e}")

        # Đóng RTC module
        if hasattr(self, 'rtc') and self.rtc_available:
            try:
                self.rtc.close()
                print("✅ Đã đóng RTC module")
            except Exception as e:
                print(f"⚠️ Lỗi khi đóng RTC: {e}")

        print("✅ Đã dừng và dọn dẹp xong.")

def signal_handler(signum, frame):
    """Xử lý tín hiệu để thoát an toàn"""
    print("\n🛑 Nhận tín hiệu dừng, đang thoát...")
    if 'recorder' in globals():
        recorder.cleanup()
    sys.exit(0)

if __name__ == "__main__":
    # Đăng ký handler cho SIGINT và SIGTERM
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        # Khởi tạo recorder
        recorder = PiStreamer()
        if not recorder.initial():
            print("❌ Khởi tạo thất bại, đang thoát...")
            sys.exit(1)

        # Menu điều khiển đơn giản
        while True:
            print("\n=== Menu điều khiển ===")
            print("1. Bắt đầu ghi")
            print("2. Dừng ghi")
            print("3. Thoát")
            print("====================")

            try:
                choice = input("Chọn (1-3): ").strip()
                
                if choice == '1':
                    recorder.start()
                elif choice == '2':
                    recorder.cleanup()
                elif choice == '3':
                    print("🛑 Đang thoát...")
                    recorder.cleanup()
                    break
                else:
                    print("❌ Lựa chọn không hợp lệ!")
            
            except KeyboardInterrupt:
                print("\n🛑 Đang thoát...")
                recorder.cleanup()
                break
            except Exception as e:
                print(f"❌ Lỗi: {e}")
                continue

    except Exception as e:
        print(f"❌ Lỗi chương trình: {e}")
        sys.exit(1)
