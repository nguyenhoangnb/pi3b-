#!/usr/bin/env python3
"""
recorder_ffmpeg.py - Simple recorder using FFmpeg for both file recording and HLS streaming
FIXED VERSION - Sửa lỗi HLS streaming
UPDATED VERSION - Thêm overlay timestamp và tự động khởi động lại
"""
import os
import sys
import time
import signal
import subprocess
import re
from datetime import datetime
from pathlib import Path
import queue
import threading
import traceback

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from firmware.hal.usb_manager import USBManager
from firmware.hal.gpio_leds import gpioLed
from firmware.hal.gnss import GNSSModule
from firmware.hal.rtc import rtcModule
from firmware.hal.micro import Micro
from firmware.config.config_loader import load


class FFmpegRecorder:
    """Simple video recorder using FFmpeg"""

    def __init__(self):
        self.config_file = Path(__file__).parent.parent / 'config' / 'device_full.yaml'
        self.config = load(self.config_file)

        # Paths
        self.output_dir = Path(__file__).parent.parent / self.config['paths']['record_root']
        self.hls_dir = "/tmp/picam_hls"
        Path(self.hls_dir).mkdir(parents=True, exist_ok=True)

        # Recording settings
        self.segment_seconds = self.config['storage']['segment_seconds']

        # Hardware
        self.led_control = gpioLed(self.config['gpio'].get('record_led', 26))

        # USB Storage Manager
        self.usb_manager = USBManager(
            path=self.output_dir,
            min_free_gb=self.config['storage'].get('min_free_gb', 1.0),
            min_free_percent=self.config['storage'].get('min_free_percent', 10),
            camera_id=self.config['device'].get('id', 'PICAM')
        )

        # FFmpeg process
        self.ffmpeg_process = None
        self._stop_flag = False

        # Storage monitoring thread
        self._storage_monitor_thread = None

    def _storage_monitor_loop(self):
        """Monitor USB storage and update LED accordingly"""
        while not self._stop_flag and self.is_running():
            if not self.usb_manager.is_available():
                self.led_control.blink(0.3)
                print("⚠️ USB storage disconnected!")
            else:
                self.led_control.on()
            time.sleep(2)

    def get_video_device(self):
        """Find available camera"""
        video_dev = self.config['video'].get('v4l2_device', '/dev/video0')
        if Path(video_dev).exists():
            return video_dev

        for i in range(10):
            dev = f'/dev/video{i}'
            if Path(dev).exists():
                print(f"✅ Found camera: {dev}")
                return dev

        raise Exception("No camera found")

    def get_audio_device(self):
        """Get audio device in ALSA format with supported params"""
        if not self.config['audio'].get('enabled', False):
            print("ℹ️ Audio disabled in config")
            return None

        test_devices = [
            "hw:1,0",
            "plughw:1,0",
            "hw:2,0",
            "plughw:2,0",
        ]

        test_params = [
            {'rate': 44100, 'channels': 2},
            {'rate': 48000, 'channels': 2},
            {'rate': 44100, 'channels': 1},
            {'rate': 48000, 'channels': 1},
        ]

        print("🔍 Testing audio devices...")
        for alsa_device in test_devices:
            for params in test_params:
                test_cmd = [
                    'arecord',
                    '-D', alsa_device,
                    '-f', 'S16_LE',
                    '-r', str(params['rate']),
                    '-c', str(params['channels']),
                    '-d', '1',
                    '/tmp/audio_test.wav'
                ]
                try:
                    result = subprocess.run(test_cmd, capture_output=True, timeout=3)
                    if result.returncode == 0:
                        print(f"✅ Audio device verified: {alsa_device} ({params['channels']}ch @ {params['rate']}Hz)")
                        try:
                            Path('/tmp/audio_test.wav').unlink()
                        except:
                            pass
                        return {
                            'device': alsa_device,
                            'rate': params['rate'],
                            'channels': params['channels']
                        }
                except:
                    pass
        print("⚠️ No working audio device found—falling back to video-only")
        return None

    def start_recording(self):
        """Start FFmpeg recording + HLS streaming"""
        
        if self.is_running():
            print("⚠️ Already recording")
            return False
        
        # Check storage
        if not self.usb_manager.is_available():
            print("❌ USB storage not available")
            self.led_control.blink(0.5)
            return False
        
        if not self.usb_manager.has_enough_space():
            print("⚠️ Low storage space, cleaning up...")
            self.usb_manager.cleanup_old_files()
            if not self.usb_manager.has_enough_space():
                print("❌ Not enough storage space")
                return False
        
        # Clear old HLS files
        for f in Path(self.hls_dir).glob("*.ts"):
            try:
                f.unlink()
            except:
                pass
        for f in Path(self.hls_dir).glob("*.m3u8"):
            try:
                f.unlink()
            except:
                pass
        
        # Get devices
        try:
            video_dev = self.get_video_device()
        except Exception as e:
            print(f"❌ Device error: {e}")
            return False
        
        # Quick device lock check/kill
        for dev in [video_dev]:
            try:
                if subprocess.run(['fuser', dev], capture_output=True).returncode == 0:
                    print(f"⚠️ Device {dev} in use—killing processes")
                    subprocess.run(['fuser', '-k', dev])
            except:
                pass
        
        # Parse video settings
        video_size = self.config['video']['v4l2_format']
        video_fps = self.config['video']['v4l2_fps']
        
        # Build FFmpeg command
        cmd = [
            'ffmpeg',
            '-f', 'v4l2',
            '-input_format', 'yuyv422',
            '-video_size', video_size,
            '-framerate', str(video_fps),
            '-i', video_dev,
            '-fflags', 'nobuffer',
            '-flags', 'low_delay',
        ]
        
        ## ◀️ THÊM MỚI: Logic tìm phông chữ cho timestamp
        # Tìm một phông chữ. Cài đặt 'fonts-dejavu-core' nếu không có
        font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        if not Path(font_path).exists():
            font_path = "/usr/share/fonts/truetype/freefont/FreeSans.ttf" # Fallback
            if not Path(font_path).exists():
                print("⚠️ WARNING: Không tìm thấy phông chữ. Overlay timestamp có thể thất bại.")
                print("  ↳ Thử cài đặt: sudo apt-get install fonts-dejavu-core")
                font_path = "default" # Để FFmpeg tự thử

        # Định dạng timestamp, lưu ý \\: để escape dấu : cho FFmpeg
        timestamp_format = '%{localtime\\:%Y-%m-%d %H\\:%M\\:%S}'
        
        filter_string = (
            f"scale=640:480:flags=bicubic,"
            f"drawtext=fontfile='{font_path}':"
            f"text='%{{localtime\\:%Y-%m-%d %H\\\\:%M\\\\:%S}}':"
            f"fontcolor=white:fontsize=20:box=1:boxcolor=black@0.5:"
            f"boxborderw=5:x=(w-text_w-10):y=10,"
            f"format=yuv420p"
        )
        ## ◀️ KẾT THÚC THAY ĐỔI
        
        # Video codec settings
        cmd.extend([
            '-vf', filter_string,
            '-c:v', 'libx264',
            '-preset', 'ultrafast',  # Thay đổi từ veryfast → ultrafast cho streaming
            '-tune', 'zerolatency',
            '-profile:v', 'baseline',  # Thay đổi từ main → baseline (tương thích tốt hơn)
            '-level', '3.0',
            '-g', str(video_fps * 2),
            '-keyint_min', str(video_fps),
            '-sc_threshold', '0',
            '-b:v', '800k',  # Giảm bitrate cho streaming mượt hơn
            '-maxrate', '1000k',
            '-bufsize', '2000k',
            '-pix_fmt', 'yuv420p',
        ])
        
        # Tee muxer setup
        start_time = datetime.now().strftime("%Y%m%d_%H%M%S")
        timestamp_pattern = f"{self.output_dir}/{start_time}_cam0_%03d.mp4"
        
        cmd.extend([
            '-f', 'tee',
            '-map', '0:v',
        ])
        
        # ✅ FIX: Tee output với cấu hình HLS tối ưu
        tee_output = (
            f"[f=segment:segment_time={self.segment_seconds}:segment_format=mp4:"
            f"segment_start_number=0:"
            f"reset_timestamps=1:segment_list_flags=live]{timestamp_pattern}|"  # <-- ĐÃ SỬA: Xóa strftime=1
            f"[f=hls:hls_time=2:hls_list_size=5:"
            f"hls_flags=delete_segments+independent_segments+append_list:"
            f"hls_segment_type=mpegts:start_number=0:"
            f"hls_allow_cache=0:"
            f"hls_segment_filename={self.hls_dir}/segment_%03d.ts]{self.hls_dir}/stream.m3u8"
        )
        
        cmd.append(tee_output)
        
        print(f"🎬 Starting FFmpeg recording...")
        print(f"  ↳ Video: {video_dev} ({video_size} @ {video_fps}fps)")
        print(f"  ↳ Output: {self.output_dir}/*.mp4")
        print(f"  ↳ HLS: {self.hls_dir}/stream.m3u8")
        print(f"  ↳ Segment: {self.segment_seconds}s")
        
        try:
            # Log command for debugging
            cmd_str = ' '.join(cmd)
            print(f"  ↳ Command: {cmd_str[:200]}...")
            
            self.ffmpeg_process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                universal_newlines=True,
                bufsize=1
            )
            
            print(f"✅ FFmpeg started (PID: {self.ffmpeg_process.pid})")
            
            # Enhanced monitoring with queue
            output_queue = queue.Queue()
            def monitor_ffmpeg():
                try:
                    for line in iter(self.ffmpeg_process.stdout.readline, ''):
                        output_queue.put(line)
                        lower_line = line.lower()
                        # ✅ FIX 5: Log HLS-specific errors
                        if any(word in lower_line for word in ['error', 'failed', 'no such device', 
                                                               'invalid argument', 'ioctl', 
                                                               'demuxing', 'hls', 'segment']):
                            print(f"⚠️ FFmpeg: {line.strip()}")
                except:
                    pass
            
            monitor_thread = threading.Thread(target=monitor_ffmpeg, daemon=True)
            monitor_thread.start()
            
            # Drain output
            def drain_output():
                while self.is_running():
                    try:
                        line = output_queue.get(timeout=1)
                        # Uncomment để xem full log: print(line.strip())
                    except queue.Empty:
                        continue
            
            drain_thread = threading.Thread(target=drain_output, daemon=True)
            drain_thread.start()
            
            # Storage monitor
            self._storage_monitor_thread = threading.Thread(target=self._storage_monitor_loop, daemon=True)
            self._storage_monitor_thread.start()
            
            # Wait for FFmpeg to start
            time.sleep(3)  # Tăng từ 2→3s cho USB init
            
            if self.ffmpeg_process.poll() is not None:
                print(f"❌ FFmpeg exited early: code {self.ffmpeg_process.returncode}")
                return False
            
            # ✅ FIX 6: Verify HLS files created
            time.sleep(2)
            if not Path(f"{self.hls_dir}/stream.m3u8").exists():
                print(f"⚠️ Warning: stream.m3u8 not created yet")
            else:
                print(f"✅ HLS playlist created successfully")
            
            self.led_control.on()
            return True
            
        except Exception as e:
            print(f"❌ Failed to start FFmpeg: {e}")
            traceback.print_exc()
            return False

    def stop_recording(self):
        """Stop FFmpeg recording"""
        if not self.is_running():
            return

        print("⏱ Stopping FFmpeg...")

        self._stop_flag = True

        try:
            self.ffmpeg_process.terminate()
            self.ffmpeg_process.wait(timeout=10)
            print("  ✅ FFmpeg stopped")
        except subprocess.TimeoutExpired:
            print("  ⚠️ Timeout, force killing...")
            self.ffmpeg_process.kill()
            self.ffmpeg_process.wait()
        except Exception as e:
            print(f"  ⚠️ Error stopping FFmpeg: {e}")

        self.ffmpeg_process = None
        self.led_control.off()
        print("  💡 LED off")

    def is_running(self):
        """Check if FFmpeg is running"""
        return (self.ffmpeg_process is not None and
                self.ffmpeg_process.poll() is None)

    def cleanup(self):
        """Cleanup resources"""
        print("🧹 Cleanup...")
        self.stop_recording()
        print("✅ Cleanup complete")


# Global recorder instance
recorder = None

def signal_handler(signum, frame):
    """Handle shutdown signals"""
    print("\n🛑 Shutting down...")
    if recorder:
        recorder.cleanup()
    sys.exit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        recorder = FFmpegRecorder()

        if recorder.start_recording():
            print(f"📡 HLS stream available at: {recorder.hls_dir}/stream.m3u8")
            print("  ↳ Test with: ffplay /tmp/picam_hls/stream.m3u8")
            print("  ↳ Or web browser: http://your-pi-ip/live")
            
            # ◀️ ◀️ ◀️ THAY ĐỔI: Thêm vòng lặp tự động khởi động lại ◀️ ◀️ ◀️
            while True:
                if not recorder.is_running():
                    print("⚠️ FFmpeg process stopped unexpectedly! Restarting in 5s...")
                    recorder.cleanup()  # Dọn dẹp tiến trình cũ
                    time.sleep(5)
                    
                    # Cập nhật lại _stop_flag trước khi khởi động lại
                    recorder._stop_flag = False 
                    
                    if not recorder.start_recording():
                        print("❌ Failed to restart recording. Exiting.")
                        sys.exit(1)
                    else:
                        print("✅ FFmpeg restarted successfully.")
                
                time.sleep(2) # Kiểm tra trạng thái mỗi 2 giây
            # ◀️ ◀️ ◀️ KẾT THÚC THAY ĐỔI ◀️ ◀️ ◀️
            
        else:
            print("❌ Failed to start recording")
            sys.exit(1)

    except KeyboardInterrupt:
        print("\n🛑 Keyboard interrupt")
        if recorder:
            recorder.cleanup()
    except Exception as e:
        print(f"❌ Error: {e}")
        traceback.print_exc()
        sys.exit(1)