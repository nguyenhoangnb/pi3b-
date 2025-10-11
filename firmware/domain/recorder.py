#!/usr/bin/env python3
"""
Video Recording Module with LED control and overlays
Handles automatic recording with GPS, audio, and time overlays
HLS streaming from single camera source
"""

import os
import sys
import time
import threading
import signal
import cv2
import numpy as np
from datetime import datetime
import subprocess
from pathlib import Path
import shutil

# Add project path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from firmware.hal.camera import FFmpegCamera
from firmware.hal.usb_manager import USBManager
from firmware.hal.gpio_leds import gpioLed
from firmware.hal.gnss import GNSSModule
from firmware.hal.rtc import rtcModule
from firmware.config.config_loader import load

# Try to import Micro, fallback to dummy if not available
try:
    from firmware.hal.micro import Micro
except Exception as e:
    print(f"⚠ Audio dependencies not available: {e}")
    class Micro:
        def __init__(self, *args, **kwargs):
            pass
        def check_device_available(self):
            return False
        def record(self, *args, **kwargs):
            return None
        def save(self, *args, **kwargs):
            pass

class VideoRecorder:
    def __init__(self, config_file=None):
        """Initialize video recorder with configuration"""
        self.config = self._load_config(config_file)

        # Components
        self.camera = None
        self.usb_manager = None
        self.record_led = None
        self.micro = None
        self.gnss = None
        self.rtc = None
        self.rtc_lock = threading.Lock()  # ← lock để thread-safe đọc RTC

        # Recording state
        self.is_recording = False
        self.current_recorder_process = None
        self.segment_start_time = None
        self.recording_thread = None
        self._stop_recording = False

        # HLS streaming
        self.hls_dir = Path("/tmp/picam_hls")
        self.hls_process = None
        self.hls_enabled = True
        self.hls_lock = threading.Lock()

        # Overlays
        self.enable_time_overlay = True
        self.enable_gps_overlay = True
        self.enable_audio = True

        # Initialize components
        self._initialize_components()

        # Setup signal handlers (main thread)
        try:
            signal.signal(signal.SIGTERM, self._signal_handler)
            signal.signal(signal.SIGINT, self._signal_handler)
        except ValueError:
            pass

        # Auto-start recording
        print("🚀 Auto-starting recording...")
        self.start_recording()

    # ---------------- CONFIG ----------------
    def _load_config(self, config_file=None):
        """Load configuration from YAML file or use defaults"""
        # Determine config file path
        if config_file is None:
            config_file = Path(__file__).parent.parent / 'config' / 'device_full.yaml'
        else:
            config_file = Path(config_file)
        
        # Try to load YAML config
        if config_file.exists():
            try:
                yaml_config = load(config_file)
                print(f"✓ Loaded config from: {config_file}")
                return self._parse_yaml_config(yaml_config)
            except Exception as e:
                print(f"⚠ Error loading config: {e}")
                print("⚠ Falling back to default config")
                return self._get_default_config()
        else:
            print(f"⚠ Config file not found: {config_file}")
            print("⚠ Using default config")
            return self._get_default_config()

    def _parse_yaml_config(self, yaml_config):
        """Parse YAML config and convert to recorder internal format"""
        # Extract sections
        video = yaml_config.get('video', {})
        audio = yaml_config.get('audio', {})
        storage = yaml_config.get('storage', {})
        paths = yaml_config.get('paths', {})
        gpio = yaml_config.get('gpio', {})
        caps = yaml_config.get('capabilities', {})
        device_info = yaml_config.get('device', {})
        
        # Parse video format
        v4l2_format = video.get('v4l2_format', '640x480')
        try:
            width, height = map(int, v4l2_format.split('x'))
        except ValueError:
            print(f"⚠ Invalid v4l2_format '{v4l2_format}', using 640x480")
            width, height = 640, 480
        
        # Build internal config
        config = {
            'camera': {
                'device': video.get('v4l2_device', '/dev/video0'),
                'width': width,
                'height': height,
                'fps': video.get('v4l2_fps', 30)
            },
            'audio': {
                'enabled': caps.get('audio', False),
                'device': audio.get('device'),  # None = auto-detect
                'sample_rate': audio.get('sample_rate', 48000),
                'channels': audio.get('channels', 1)
            },
            'storage': {
                'path': paths.get('record_root', '/media/ssd'),
                'min_free_gb': storage.get('min_free_gb', 1.0),
                'segment_seconds': storage.get('segment_seconds', 600),
                'container': storage.get('container', 'mp4')
            },
            'gpio': {
                'record_led': gpio.get('record_led', 26),
                'wifi_led': gpio.get('wifi_led', 13),
                'lte_led': gpio.get('lte_led', 6)
            },
            'overlay': {
                'timestamp_enabled': True,
                'gps_enabled': caps.get('gnss', False),
                'font_scale': 0.7,
                'font_thickness': 2,
                'text_color': (255, 255, 255),
                'bg_color': (0, 0, 0)
            },
            'capabilities': {
                'video': caps.get('video', True),
                'audio': caps.get('audio', False),
                'gnss': caps.get('gnss', False),
                'lte': caps.get('lte', False)
            },
            'device': {
                'id': device_info.get('id', 'PICAM-UNKNOWN'),
                'model': device_info.get('model', 'PiCam'),
                'hw_rev': device_info.get('hw_rev', 'A0'),
                'fw_version': device_info.get('fw_version', '0.0.0')
            }
        }
        
        print(f"✓ Camera: {config['camera']['device']} @ {width}x{height} {config['camera']['fps']}fps")
        print(f"✓ Audio: {'enabled' if config['audio']['enabled'] else 'disabled'}")
        print(f"✓ Storage: {config['storage']['path']} (min {config['storage']['min_free_gb']}GB)")
        print(f"✓ Segment: {config['storage']['segment_seconds']}s per file")
        
        return config

    def _get_default_config(self):
        """Get default configuration when YAML file is not available"""
        return {
            'camera': {
                'device': '/dev/video0',
                'width': 640,
                'height': 480,
                'fps': 30
            },
            'audio': {
                'enabled': False,
                'device': None,
                'sample_rate': 48000,
                'channels': 1
            },
            'storage': {
                'path': '/media/ssd',
                'min_free_gb': 1.0,
                'segment_seconds': 600,
                'container': 'mp4'
            },
            'gpio': {
                'record_led': 26,
                'wifi_led': 13,
                'lte_led': 6
            },
            'overlay': {
                'timestamp_enabled': True,
                'gps_enabled': False,
                'font_scale': 0.7,
                'font_thickness': 2,
                'text_color': (255, 255, 255),
                'bg_color': (0, 0, 0)
            },
            'capabilities': {
                'video': True,
                'audio': False,
                'gnss': False,
                'lte': False
            },
            'device': {
                'id': 'PICAM-DEFAULT',
                'model': 'PiCam',
                'hw_rev': 'A0',
                'fw_version': '0.0.0'
            }
        }

    # ---------------- COMPONENTS ----------------
    def _initialize_components(self):
        try:
            # Camera
            cam = self.config['camera']
            self.camera = FFmpegCamera(
                device=cam['device'],
                width=cam['width'],
                height=cam['height'],
                fps=cam['fps']
            )
            print("✓ Camera initialized")

            # Storage manager
            storage = self.config['storage']
            self.usb_manager = USBManager(
                path=storage['path'],
                min_free_gb=storage['min_free_gb'],
                min_free_percent=10  # Fixed at 10%
            )
            print("✓ USB Manager initialized")
            self.segment_duration = storage['segment_seconds']
            
            # LED
            self.record_led = gpioLed(self.config['gpio']['record_led'])
            print("✓ Record LED initialized")

            if self.config.get('audio', {}).get('enabled', True):
                try:
                    audio_config = self.config['audio']
                    self.micro = Micro(
                        device=audio_config.get('device', None),
                        sample_rate=audio_config.get('sample_rate', 48000)
                    )
                    self.enable_audio = self.micro.check_device_available()
                except Exception as e:
                    print(f"⚠ Microphone not available: {e}")
                    self.micro = None
                    self.enable_audio = False
            else:
                self.micro = None
                self.enable_audio = False

            if self.config.get('capabilities', {}).get('gnss', False):
                try:
                    self.gnss = GNSSModule()
                    print("✓ GNSS initialized")
                except Exception as e:
                    print(f"⚠ GNSS not available: {e}")
                    self.gnss = None
                    self.enable_gps_overlay = False
            else:
                self.gnss = None
                self.enable_gps_overlay = False

            # RTC (optional - only if you really need hardware RTC)
            # Disable if getting "Device or resource busy" errors
            if self.config.get('capabilities', {}).get('rtc', False):
                try:
                    self.rtc = rtcModule()
                    print("✓ RTC module initialized")
                    # Test read RTC to ensure it works
                    try:
                        _ = self.rtc.read_time()
                    except Exception as e:
                        print(f"⚠ RTC test failed: {e}, disabling RTC")
                        self.rtc.close()
                        self.rtc = None
                except Exception as e:
                    print(f"⚠ RTC not available: {e}")
                    self.rtc = None
            else:
                print("ℹ RTC disabled in config")
                self.rtc = None

            self._setup_hls_streaming()
        except Exception as e:
            print(f"✗ Component initialization error: {e}")
            raise

    # ---------------- RTC / TIME ----------------
    def _get_time_text(self):
        """Get time text with RTC fallback to system time"""
        try:
            if self.rtc:
                # Try to acquire lock with timeout
                if self.rtc_lock.acquire(blocking=False):
                    try:
                        dt = self.rtc.read_time()
                        return dt.strftime("%Y-%m-%d %H:%M:%S")
                    finally:
                        self.rtc_lock.release()
                else:
                    # Lock busy, use system time
                    dt = datetime.now()
                    return dt.strftime("%Y-%m-%d %H:%M:%S")
            else:
                # No RTC, use system time
                dt = datetime.now()
                return dt.strftime("%Y-%m-%d %H:%M:%S")
        except OSError as e:
            # RTC hardware busy, fallback to system time (suppress warning spam)
            dt = datetime.now()
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception as e:
            # Any other error, use system time
            dt = datetime.now()
            return dt.strftime("%Y-%m-%d %H:%M:%S")

    # ---------------- GPS / OVERLAY ----------------
    def _get_gps_text(self):
        if not self.gnss or not self.enable_gps_overlay:
            return ""
        try:
            gps_data = self.gnss.get_location()
            if gps_data and gps_data.get('fix_quality', 0) > 0:
                lat = gps_data.get('latitude', 0)
                lon = gps_data.get('longitude', 0)
                return f"GPS: {lat:.6f}, {lon:.6f}"
            else:
                return "GPS: No Fix"
        except Exception:
            return "GPS: Error"

    def _add_overlays(self, frame):
        if frame is None:
            return frame
        frame = frame.copy()
        height, width = frame.shape[:2]
        cfg = self.config['overlay']

        # Time overlay
        if self.enable_time_overlay and cfg.get('timestamp_enabled', True):
            time_text = self._get_time_text()
            cv2.putText(frame, time_text, (10, height - 10), cv2.FONT_HERSHEY_SIMPLEX, cfg['font_scale'], cfg['text_color'], cfg['font_thickness'], cv2.LINE_AA)

        # GPS overlay
        if self.enable_gps_overlay and cfg.get('gps_enabled', False):
            gps_text = self._get_gps_text()
            cv2.putText(frame, gps_text, (10, height - 30), cv2.FONT_HERSHEY_SIMPLEX, cfg['font_scale'], cfg['text_color'], cfg['font_thickness'], cv2.LINE_AA)

        return frame
    def _should_create_new_segment(self):
        if not self.segment_start_time:
            return True
        return (time.time() - self.segment_start_time) >= self.segment_duration
    # ---------------- HLS STREAM ----------------
    def _setup_hls_streaming(self):
        self.hls_dir.mkdir(parents=True, exist_ok=True)
        if self.hls_enabled:
            print(f"🎬 HLS streaming directory: {self.hls_dir}")
    
    def _write_frame_to_ffmpeg(self, frame):
        """Write frame to FFmpeg stdin pipe"""
        if not self.current_recorder_process:
            return False
        
        # Check if FFmpeg process is still alive
        if self.current_recorder_process.poll() is not None:
            # FFmpeg died, read stderr
            try:
                stderr = self.current_recorder_process.stderr.read().decode('utf-8', errors='ignore')
                print(f"✗ FFmpeg died: {stderr}")
            except:
                print("✗ FFmpeg died (no stderr)")
            self.current_recorder_process = None
            self.segment_start_time = 0  # Force new segment
            return False
        
        try:
            # Write frame bytes to FFmpeg stdin
            self.current_recorder_process.stdin.write(frame.tobytes())
            self.current_recorder_process.stdin.flush()
            return True
        except BrokenPipeError:
            print("⚠ FFmpeg pipe broken, will create new segment")
            self.segment_start_time = 0  # Force new segment
            self.current_recorder_process = None
            return False
        except Exception as e:
            print(f"⚠ Error writing frame to FFmpeg: {e}")
            return False

    # ---------------- RECORDING LOOP ----------------
    def _recording_loop(self):
        print("🎥 Recording thread started")
        
        # Start camera FIRST before creating segment
        try:
            self.camera.start()
            print("✓ Camera started in recording loop")
        except Exception as e:
            print(f"✗ Failed to start camera: {e}")
            return
        
        # Test camera by reading a frame
        print("📸 Testing camera...")
        test_frame = self.camera.read_frame()
        if test_frame is None:
            print("✗ Camera test failed - no frames available!")
            return
        print(f"✓ Camera test OK - frame shape: {test_frame.shape}")
        
        frame_count = 0
        last_report = time.time()
        
        while not self._stop_recording:
            # Create new segment if needed
            if self._should_create_new_segment():
                if not self._create_new_segment():
                    print("✗ Failed to create segment, retrying in 5s...")
                    time.sleep(5)
                    continue

            # Read frame from camera
            frame = self.camera.read_frame()
            if frame is None:
                time.sleep(0.01)
                continue

            # Add overlays
            frame_with_overlay = self._add_overlays(frame)
            
            # Write to FFmpeg
            if self._write_frame_to_ffmpeg(frame_with_overlay):
                frame_count += 1
            
            # Report progress every 5 seconds
            if time.time() - last_report >= 5.0:
                fps = frame_count / 5.0
                print(f"📊 Recording: {frame_count} frames in 5s ({fps:.1f} fps)")
                frame_count = 0
                last_report = time.time()

            # Don't sleep - read as fast as camera provides frames

        # Cleanup FFmpeg when stopping
        if self.current_recorder_process:
            try:
                self.current_recorder_process.stdin.close()
                self.current_recorder_process.wait(timeout=2)
                print("✓ FFmpeg process closed")
            except:
                self.current_recorder_process.kill()
            self.current_recorder_process = None
        
        # Stop camera
        try:
            self.camera.stop()
            print("✓ Camera stopped")
        except:
            pass

    def _create_new_segment(self):
        """Create new recording segment with FFmpeg"""
        # Đảm bảo USB sẵn sàng
        if not self.usb_manager.is_available():
            print("⚠ USB not available, waiting...")
            self.usb_manager.wait_until_available()

        # Kiểm tra dung lượng
        if not self.usb_manager.has_enough_space():
            print("⚠ Not enough space, cleaning up old files...")
            # TODO: Implement cleanup old files
            return False

        # Close previous FFmpeg process
        if self.current_recorder_process:
            try:
                print("⏹ Closing previous segment...")
                self.current_recorder_process.stdin.close()
                # Don't wait - just terminate immediately to avoid blocking
                self.current_recorder_process.terminate()
                # Give it 0.5s to finish gracefully, then kill
                try:
                    self.current_recorder_process.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    self.current_recorder_process.kill()
                print("✓ Previous segment closed")
            except Exception as e:
                print(f"⚠ Error closing previous segment: {e}")
                try:
                    self.current_recorder_process.kill()
                except:
                    pass
            self.current_recorder_process = None

        # Create output file path
        now = datetime.now().strftime("%Y%m%d-%H%M%S")
        base_dir = Path(self.config['storage']['path'])
        base_dir.mkdir(parents=True, exist_ok=True)
        output_file = base_dir / f"{now}.mp4"

        # Build FFmpeg command
        width, height, fps = self.config['camera']['width'], self.config['camera']['height'], self.config['camera']['fps']
        cmd = [
            "ffmpeg",
            "-y",  # overwrite
            "-f", "rawvideo",
            "-pix_fmt", "bgr24",
            "-s", f"{width}x{height}",
            "-r", str(fps),
            "-i", "pipe:0"  # Read video from stdin
        ]

        # Add audio if available
        if self.micro and self.enable_audio:
            audio_device = self.config['audio'].get('device') or "plughw:1,0"
            audio_rate = self.config['audio'].get('sample_rate', 48000)
            audio_ch = self.config['audio'].get('channels', 1)
            cmd.extend([
                "-f", "alsa",
                "-thread_queue_size", "512",
                "-ac", str(audio_ch),
                "-ar", str(audio_rate),
                "-i", audio_device,
                "-c:a", "aac",
                "-b:a", "128k",
                "-map", "0:v:0",
                "-map", "1:a:0"
            ])
            print(f"✓ Audio enabled: {audio_device}")
        else:
            cmd.extend(["-an"])
            print("ℹ Audio disabled")

        # Video encoding - use software encoder for stability
        cmd.extend([
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-tune", "zerolatency",
            "-b:v", "1M",
            "-pix_fmt", "yuv420p",
            "-shortest",  # Stop when shortest input ends (video or audio)
            "-fflags", "+genpts",  # Generate presentation timestamps
            "-max_interleave_delta", "0",  # Don't wait for audio/video sync
            str(output_file)
        ])

        # Start FFmpeg process
        print(f"🎬 Starting FFmpeg: {' '.join(cmd)}")
        try:
            self.current_recorder_process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                bufsize=10**7
            )
            
            # Check if FFmpeg started successfully
            time.sleep(0.2)
            if self.current_recorder_process.poll() is not None:
                stderr = self.current_recorder_process.stderr.read().decode('utf-8', errors='ignore')
                print(f"✗ FFmpeg died immediately: {stderr}")
                self.current_recorder_process = None
                return False
            
            self.segment_start_time = time.time()
            print(f"✅ New segment started: {output_file}")
            return True
            
        except Exception as e:
            print(f"✗ Failed to start FFmpeg: {e}")
            return False

    # ---------------- RECORDING CONTROL ----------------
    def start_recording(self):
        if self.is_recording:
            print("ℹ Already recording")
            return
        self.is_recording = True
        self._stop_recording = False
        self.recording_thread = threading.Thread(target=self._recording_loop, daemon=True)
        self.recording_thread.start()
        self.record_led.on()
        print("✅ Recording started")

    def stop_recording(self):
        if not self.is_recording:
            return
        self._stop_recording = True
        if self.recording_thread:
            self.recording_thread.join()
        self.record_led.off()
        self.is_recording = False
        print("🛑 Recording stopped")

    # ---------------- SIGNAL HANDLER ----------------
    def _signal_handler(self, signum, frame):
        print(f"⚠ Signal {signum} received, stopping recording")
        self.stop_recording()
        sys.exit(0)
    def cleanup(self):
        """Clean up all resources safely"""
        print("🧹 Cleaning up recorder...")

        # Stop recording first
        self.stop_recording()

        # Stop HLS streaming
        self._stop_hls_stream()

        # Close FFmpeg recording process
        if self.current_recorder_process:
            try:
                print("⏹ Stopping FFmpeg recorder...")
                self.current_recorder_process.stdin.close()
                self.current_recorder_process.wait(timeout=3)
                print("✓ FFmpeg recorder stopped")
            except:
                print("⚠ Force killing FFmpeg recorder...")
                self.current_recorder_process.kill()
            self.current_recorder_process = None

        # Stop camera
        if self.camera:
            try:
                self.camera.stop()
            except Exception as e:
                print(f"⚠ Camera stop error: {e}")

        # Turn off LED
        if self.record_led:
            try:
                self.record_led.off()
                self.record_led.cleanup()
            except Exception as e:
                print(f"⚠ LED cleanup error: {e}")

        # Cleanup Microphone
        if self.micro:
            try:
                # Stop any ongoing recording
                print("✓ Microphone cleanup completed")
            except Exception as e:
                print(f"⚠ Microphone cleanup error: {e}")

        # Cleanup GNSS
        if self.gnss:
            try:
                self.gnss.close()
            except Exception as e:
                print(f"⚠ GNSS cleanup error: {e}")

        # Cleanup RTC
        if self.rtc:
            try:
                self.rtc.close()
            except Exception as e:
                print(f"⚠ RTC cleanup error: {e}")

        print("✓ Recorder cleanup completed")

if __name__ == "__main__":
    rec = VideoRecorder()
    print("🎬 Recorder started. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("🛑 Stopping recorder...")
        rec.stop_recording()
        rec.cleanup()