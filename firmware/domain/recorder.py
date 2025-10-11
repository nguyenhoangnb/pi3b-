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
        if config_file is None:
            config_file = Path(__file__).parent.parent / 'config' / 'device_full.yaml'
        if config_file.exists():
            try:
                yaml_config = load(config_file)
                print(f"✓ Loaded config from: {config_file}")
                return self._convert_yaml_to_recorder_config(yaml_config)
            except Exception as e:
                print(f"⚠ Error loading config: {e}")
                return self._default_config()
        else:
            print("⚠ No config file found, using defaults")
            return self._default_config()

    def _convert_yaml_to_recorder_config(self, yaml_config):
        video_config = yaml_config.get('video', {})
        v4l2_format = video_config.get('v4l2_format', '640x480')
        width, height = map(int, v4l2_format.split('x'))

        storage_config = yaml_config.get('storage', {})
        paths_config = yaml_config.get('paths', {})
        gpio_config = yaml_config.get('gpio', {})

        config = {
            'camera': {
                'device': video_config.get('v4l2_device', '/dev/video0'),
                'width': width,
                'height': height,
                'fps': video_config.get('v4l2_fps', 25)
            },
            'audio': {
                'enabled': yaml_config.get('capabilities', {}).get('audio', True),
                'device': yaml_config.get('audio', {}).get('device', None),
                'sample_rate': yaml_config.get('audio', {}).get('sample_rate', 48000),
                'channels': yaml_config.get('audio', {}).get('channels', 1)
            },
            'usb': {
                'path': paths_config.get('record_root', '/media/ssd'),
                'min_free_gb': storage_config.get('min_free_gb', 1.0),
                'min_free_percent': 10
            },
            'recording': {
                'segment_duration': storage_config.get('segment_seconds', 600),
                'format': 'XVID' if storage_config.get('container') == 'mkv' else 'mp4v',
                'quality': 80
            },
            'leds': {
                'record_led_pin': gpio_config.get('record_led', 26)
            },
            'overlays': {
                'font_scale': 0.7,
                'font_thickness': 2,
                'text_color': (255, 255, 255),
                'bg_color': (0, 0, 0),
                'timestamp_enabled': True,
                'gps_enabled': yaml_config.get('capabilities', {}).get('gnss', False)
            },
            'capabilities': yaml_config.get('capabilities', {}),
            'device': yaml_config.get('device', {})
        }
        return config

    def _default_config(self):
        return {
            'camera': {'device': '/dev/video0','width': 640,'height': 480,'fps': 30},
            'audio': {'enabled': True,'device': None,'sample_rate': 48000,'channels': 1},
            'usb': {'path': '/media/ssd','min_free_gb': 1.0,'min_free_percent': 10},
            'recording': {'segment_duration': 600,'format': 'mp4','quality': 80},
            'leds': {'record_led_pin': 26},
            'overlays': {'font_scale': 0.7,'font_thickness': 2,'text_color': (255,255,255),'bg_color': (0,0,0),'timestamp_enabled': True,'gps_enabled': False},
            'capabilities': {'video': True,'audio': False,'gnss': False,'lte': False},
            'device': {'id': 'PICAM-DEFAULT','model': 'PiCam'}
        }

    # ---------------- COMPONENTS ----------------
    def _initialize_components(self):
        try:
            cam_config = self.config['camera']
            self.camera = FFmpegCamera(device=cam_config['device'], width=cam_config['width'], height=cam_config['height'], fps=cam_config['fps'])
            print("✓ Camera initialized")

            usb_config = self.config['usb']
            self.usb_manager = USBManager(path=usb_config['path'], min_free_gb=usb_config['min_free_gb'], min_free_percent=usb_config['min_free_percent'])
            print("✓ USB Manager initialized")

            self.record_led = gpioLed(self.config['leds']['record_led_pin'])
            print("✓ Record LED initialized")

            if self.config.get('audio', {}).get('enabled', True):
                try:
                    audio_config = self.config['audio']
                    self.micro = Micro(
                        alsa_device=audio_config.get('device', None),
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

            try:
                self.rtc = rtcModule()
                print("✓ RTC module initialized")
            except Exception as e:
                print(f"⚠ RTC not available: {e}")
                self.rtc = None

            self._setup_hls_streaming()
        except Exception as e:
            print(f"✗ Component initialization error: {e}")
            raise

    # ---------------- RTC / TIME ----------------
    def _get_time_text(self):
        try:
            if self.rtc:
                with self.rtc_lock:
                    dt = self.rtc.read_time()
            else:
                dt = datetime.now()
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except OSError as e:
            print(f"⚠ RTC busy, fallback to system time: {e}")
            return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

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
        cfg = self.config['overlays']

        # Time overlay
        if self.enable_time_overlay and cfg.get('timestamp_enabled', True):
            time_text = self._get_time_text()
            cv2.putText(frame, time_text, (10, height - 10), cv2.FONT_HERSHEY_SIMPLEX, cfg['font_scale'], cfg['text_color'], cfg['font_thickness'], cv2.LINE_AA)

        # GPS overlay
        if self.enable_gps_overlay:
            gps_text = self._get_gps_text()
            cv2.putText(frame, gps_text, (10, height - 30), cv2.FONT_HERSHEY_SIMPLEX, cfg['font_scale'], cfg['text_color'], cfg['font_thickness'], cv2.LINE_AA)

        return frame

    # ---------------- HLS STREAM ----------------
    def _setup_hls_streaming(self):
        self.hls_dir.mkdir(parents=True, exist_ok=True)
        if self.hls_enabled:
            print(f"🎬 HLS streaming directory: {self.hls_dir}")

    # ---------------- RECORDING LOOP ----------------
    def _recording_loop(self):
        print("🎥 Recording thread started")
        segment_duration = self.config['recording']['segment_duration']
        while not self._stop_recording:
            try:
                frame = self.camera.read()
                frame_with_overlay = self._add_overlays(frame)
                # TODO: send to FFmpeg process
            except Exception as e:
                print(f"⚠ Recording loop error: {e}")
            time.sleep(0.04)  # ~25fps

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

# ---------------- MAIN ----------------
if __name__ == "__main__":
    rec = VideoRecorder()
