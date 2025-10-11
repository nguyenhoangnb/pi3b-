#!/usr/bin/env python3
"""
Video Recording Module with LED control and overlays
Handles automatic recording with GPS, audio, and time overlays
FIXED: HLS streaming from single camera source
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
import json
from pathlib import Path
import shutil

# Add project path for imports (relative to current file location)
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from firmware.hal.camera import FFmpegCamera
from firmware.hal.usb_manager import USBManager
from firmware.hal.gpio_leds import gpioLed
from firmware.hal.gnss import GNSSModule
from firmware.hal.rtc import rtcModule
from firmware.config.config_loader import load

# Try to import Micro, but don't fail if audio dependencies are missing
try:
    from firmware.hal.micro import Micro
except Exception as e:
    print(f"⚠ Audio dependencies not available: {e}")
    # Create a dummy Micro class
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
        
        # Recording state
        self.is_recording = False
        self.temp_recording = False
        self.current_writer = None
        self.current_recorder_process = None  # FFmpeg process for video+audio recording
        self.segment_start_time = None
        self.recording_thread = None
        self._stop_recording = False
        
        # HLS streaming for live view
        self.hls_dir = Path("/tmp/picam_hls")
        self.hls_process = None
        self.hls_enabled = True
        self.hls_lock = threading.Lock()  # Thread safety for HLS process
        
        # Overlays
        self.enable_time_overlay = True
        self.enable_gps_overlay = True
        self.enable_audio = True
        
        # Initialize components
        self._initialize_components()
        
        # Setup signal handlers for safe shutdown (only in main thread)
        try:
            signal.signal(signal.SIGTERM, self._signal_handler)
            signal.signal(signal.SIGINT, self._signal_handler)
        except ValueError:
            # Signal handlers can only be set in main thread, skip if not main
            # This is expected when VideoRecorder is instantiated from Flask web server
            pass
        
        # Auto-start recording
        print("🚀 Auto-starting recording...")
        self.start_recording()
    
    def _load_config(self, config_file=None):
        """Load configuration from YAML file"""
        if config_file is None:
            # Use relative path from current file location
            config_file = Path(__file__).parent.parent / 'config' / 'device_full.yaml'
        if config_file and os.path.exists(config_file):
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
        """Convert YAML config to recorder format"""
        # Extract video settings
        video_config = yaml_config.get('video', {})
        v4l2_format = video_config.get('v4l2_format', '640x480')
        width, height = map(int, v4l2_format.split('x'))
        
        # Extract storage settings
        storage_config = yaml_config.get('storage', {})
        paths_config = yaml_config.get('paths', {})
        
        # Extract GPIO settings
        gpio_config = yaml_config.get('gpio', {})
        
        # Convert to recorder config format
        config = {
            'camera': {
                'device': video_config.get('v4l2_device', '/dev/video0'),
                'width': width,
                'height': height,
                'fps': video_config.get('v4l2_fps', 25)
            },
            'audio': {
                'enabled': yaml_config.get('capabilities', {}).get('audio', True),
                'device': yaml_config.get('audio', {}).get('device', None),  # None = default device
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
        """Default configuration as fallback"""
        return {
            'camera': {
                'device': '/dev/video0',
                'width': 640,
                'height': 480,
                'fps': 30
            },
            'audio': {
                'enabled': True,
                'device': None,  # None = default device
                'sample_rate': 48000,
                'channels': 1
            },
            'usb': {
                'path': '/media/ssd',
                'min_free_gb': 1.0,
                'min_free_percent': 10
            },
            'recording': {
                'segment_duration': 10 * 60,  # 10 minutes
                'format': 'mp4',
                'quality': 80
            },
            'leds': {
                'record_led_pin': 26
            },
            'overlays': {
                'font_scale': 0.7,
                'font_thickness': 2,
                'text_color': (255, 255, 255),
                'bg_color': (0, 0, 0),
                'timestamp_enabled': True,
                'gps_enabled': False
            },
            'capabilities': {
                'video': True,
                'audio': False,
                'gnss': False,
                'lte': False
            },
            'device': {
                'id': 'PICAM-DEFAULT',
                'model': 'PiCam'
            }
        }
    
    def _initialize_components(self):
        """Initialize all hardware components"""
        try:
            # Camera
            cam_config = self.config['camera']
            self.camera = FFmpegCamera(
                device=cam_config['device'],
                width=cam_config['width'],
                height=cam_config['height'],
                fps=cam_config['fps']
            )
            print("✓ Camera initialized")
            
            # USB Manager
            usb_config = self.config['usb']
            self.usb_manager = USBManager(
                path=usb_config['path'],
                min_free_gb=usb_config['min_free_gb'],
                min_free_percent=usb_config['min_free_percent']
            )
            print("✓ USB Manager initialized")
            print("USB config path", usb_config["path"])
            # Record LED
            led_pin = self.config['leds']['record_led_pin']
            self.record_led = gpioLed(led_pin)
            print("✓ Record LED initialized")
            
            # Microphone (optional - only if enabled in config)
            if self.config.get('audio', {}).get('enabled', True):
                try:
                    audio_config = self.config['audio']
                    self.micro = Micro(
                        alsa_device=audio_config.get('device', None),
                        sample_rate=audio_config.get('sample_rate', 48000)
                    )
                    if self.micro.check_device_available():
                        print("✓ Microphone initialized")
                        self.enable_audio = True
                    else:
                        print("⚠ No microphone detected")
                        self.micro = None
                        self.enable_audio = False
                except Exception as e:
                    print(f"⚠ Microphone not available: {e}")
                    self.micro = None
                    self.enable_audio = False
            else:
                print("ℹ Audio disabled in config")
                self.micro = None
                self.enable_audio = False
            
            # GNSS (optional - only if enabled in config)
            if self.config.get('capabilities', {}).get('gnss', False):
                try:
                    from firmware.hal.gnss import GNSSModule
                    self.gnss = GNSSModule()
                    print("✓ GNSS module initialized")
                except Exception as e:
                    print(f"⚠ GNSS not available: {e}")
                    self.gnss = None
                    self.enable_gps_overlay = False
            else:
                print("ℹ GNSS disabled in config")
                self.gnss = None
                self.enable_gps_overlay = False
            
            # RTC (optional)
            try:
                self.rtc = rtcModule()
                print("✓ RTC module initialized")
            except Exception as e:
                print(f"⚠ RTC not available: {e}")
                self.rtc = None
            
            # Setup HLS streaming
            self._setup_hls_streaming()
                
        except Exception as e:
            print(f"✗ Component initialization error: {e}")
            raise
    
    def _setup_hls_streaming(self):
        """Setup HLS streaming directory"""
        try:
            # Create HLS directory
            self.hls_dir.mkdir(parents=True, exist_ok=True)
            print(f"✓ HLS directory created: {self.hls_dir}")
            
            # Clean up old HLS files
            for file in self.hls_dir.glob("*"):
                file.unlink(missing_ok=True)
                
        except Exception as e:
            print(f"⚠ HLS setup error: {e}")
            self.hls_enabled = False
    
    def _start_hls_stream(self):
        """Start HLS streaming process (receives frames via pipe)"""
        if not self.hls_enabled:
            return False
            
        with self.hls_lock:
            # Stop existing process if any
            if self.hls_process:
                self._stop_hls_stream_internal()
            
            try:
                camera_config = self.config['camera']
                width = camera_config['width'] 
                height = camera_config['height']
                fps = camera_config['fps']
                
                # FFmpeg command for HLS streaming - read from pipe (stdin)
                cmd = [
                    "ffmpeg", "-hide_banner", "-loglevel", "error",
                    # Input from pipe (raw video frames)
                    "-f", "rawvideo",
                    "-pix_fmt", "bgr24",  # OpenCV format
                    "-s", f"{width}x{height}",
                    "-r", str(fps),
                    "-i", "pipe:0",  # Read from stdin
                    # Encoding settings
                    "-c:v", "libx264", 
                    "-preset", "ultrafast", 
                    "-tune", "zerolatency",
                    "-g", str(fps * 2),  # GOP size
                    "-keyint_min", str(fps),
                    # HLS settings for low latency
                    "-hls_time", "1",  # 1 second segments (reduced from 2)
                    "-hls_list_size", "2",  # Keep only 2 segments (reduced from 3)
                    "-hls_flags", "delete_segments+omit_endlist+independent_segments",
                    "-f", "hls", 
                    str(self.hls_dir / "live.m3u8")
                ]
                
                self.hls_process = subprocess.Popen(
                    cmd, 
                    stdin=subprocess.PIPE,  # Create pipe for writing frames
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE
                )
                
                # Check if process started successfully
                time.sleep(0.1)
                if self.hls_process.poll() is not None:
                    err = self.hls_process.stderr.read().decode('utf-8', errors='ignore')
                    print(f"❌ FFmpeg failed to start: {err}")
                    self.hls_process = None
                    return False
                
                print("✓ HLS streaming started (pipe mode)")
                return True
                
            except Exception as e:
                print(f"⚠ HLS stream start error: {e}")
                self.hls_process = None
                return False
    
    def _stop_hls_stream_internal(self):
        """Internal method to stop HLS (must be called with lock held)"""
        if not self.hls_process:
            return
            
        try:
            # Close stdin pipe first
            if self.hls_process.stdin:
                try:
                    self.hls_process.stdin.close()
                except:
                    pass
            
            # Terminate process gracefully
            self.hls_process.terminate()
            try:
                self.hls_process.wait(timeout=3)
                print("✓ HLS streaming stopped")
            except subprocess.TimeoutExpired:
                print("⚠ HLS process timeout, force killing...")
                self.hls_process.kill()
                self.hls_process.wait()
                
        except Exception as e:
            print(f"⚠ HLS stop error: {e}")
            try:
                self.hls_process.kill()
            except:
                pass
        finally:
            self.hls_process = None
    
    def _stop_hls_stream(self):
        """Stop HLS streaming process (thread-safe)"""
        with self.hls_lock:
            self._stop_hls_stream_internal()
    
    def _write_frame_to_hls(self, frame):
        """Write a frame to HLS stream (thread-safe)"""
        with self.hls_lock:
            if self.hls_process and self.hls_process.stdin:
                try:
                    # Write raw frame bytes to FFmpeg stdin
                    self.hls_process.stdin.write(frame.tobytes())
                    self.hls_process.stdin.flush()
                    return True
                except BrokenPipeError:
                    print("⚠ HLS pipe broken")
                    self.hls_process = None
                    return False
                except Exception as e:
                    print(f"⚠ HLS write error: {e}")
                    return False
        return False
    
    def _restart_hls_if_needed(self):
        """Restart HLS stream if it died"""
        with self.hls_lock:
            if self.hls_process and self.hls_process.poll() is not None:
                print("⚠ HLS process died, restarting...")
                self._stop_hls_stream_internal()
                time.sleep(0.5)
                return self._start_hls_stream()
        return True
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals for safe recording stop"""
        print(f"\n📡 Received signal {signum}, stopping recording safely...")
        self.stop_recording()
        sys.exit(0)
    
    def _update_led_status(self):
        """Update LED based on recording status"""
        if not self.record_led:
            return
            
        if not self.is_recording:
            # Not recording - LED off
            self.record_led.off()
        elif not self.usb_manager or not self.usb_manager.is_available():
            # Storage error - LED blinking
            self.record_led.blink(0.5)
        else:
            # Recording normally - LED solid on
            self.record_led.on()
    
    def _get_time_text(self):
        """Get current time text for overlay"""
        try:
            if self.rtc:
                # Use RTC time if available
                dt = self.rtc.read_time()
                print(dt)
            else:
                # Use system time
                dt = datetime.now()
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    def _get_gps_text(self):
        """Get GPS coordinates text for overlay"""
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
        """Add time and GPS overlays to frame"""
        if frame is None:
            return frame
        
        # Create a writable copy of the frame to avoid OpenCV readonly error
        frame = frame.copy()
        
        height, width = frame.shape[:2]
        overlay_config = self.config['overlays']
        
        # Time overlay (top-left) - check config
        if self.enable_time_overlay and self.config.get('overlays', {}).get('timestamp_enabled', True):
            time_text = self._get_time_text()
            
            # Add background rectangle for better readability
            (text_width, text_height), baseline = cv2.getTextSize(
                time_text, 
                cv2.FONT_HERSHEY_SIMPLEX, 
                overlay_config['font_scale'], 
                overlay_config['font_thickness']
            )
            
            # Background rectangle
            cv2.rectangle(frame, 
                         (10, 10), 
                         (20 + text_width, 20 + text_height + baseline),
                         overlay_config['bg_color'], -1)
            
            # Time text
            cv2.putText(frame, time_text, 
                       (15, 15 + text_height),
                       cv2.FONT_HERSHEY_SIMPLEX,
                       overlay_config['font_scale'],
                       overlay_config['text_color'],
                       overlay_config['font_thickness'])
        
        # GPS overlay (top-right) - check config
        if self.enable_gps_overlay and self.config.get('overlays', {}).get('gps_enabled', False):
            gps_text = self._get_gps_text()
            if gps_text:
                (text_width, text_height), baseline = cv2.getTextSize(
                    gps_text,
                    cv2.FONT_HERSHEY_SIMPLEX,
                    overlay_config['font_scale'],
                    overlay_config['font_thickness']
                )
                
                # Position at top-right
                x_pos = width - text_width - 20
                
                # Background rectangle
                cv2.rectangle(frame,
                             (x_pos - 5, 10),
                             (width - 10, 20 + text_height + baseline),
                             overlay_config['bg_color'], -1)
                
                # GPS text
                cv2.putText(frame, gps_text,
                           (x_pos, 15 + text_height),
                           cv2.FONT_HERSHEY_SIMPLEX,
                           overlay_config['font_scale'],
                           overlay_config['text_color'],
                           overlay_config['font_thickness'])
        
        return frame
    
    def _create_new_segment(self):
        """Create new recording segment"""
        # Close previous recording if exists
        if self.current_recorder_process:
            try:
                self.current_recorder_process.stdin.close()
                self.current_recorder_process.wait(timeout=2)
            except:
                self.current_recorder_process.kill()
            self.current_recorder_process = None
        
        # Create new filename
        filename = self.usb_manager.get_new_filename()
        
        # Create FFmpeg recording process with audio
        success = self._create_ffmpeg_recorder_with_audio(filename)
        
        if success:
            self.segment_start_time = time.time()
            print(f"✓ New segment started: {filename}")
            return True
        else:
            print(f"✗ Failed to start segment: {filename}")
            return False
    
    def _create_ffmpeg_recorder_with_audio(self, filename):
        """Create FFmpeg recording process optimized for Raspberry Pi 3B+"""
        try:
            cam_config = self.config['camera']
            audio_config = self.config.get('audio', {})
            width = cam_config.get('width', 640)
            height = cam_config.get('height', 480)
            fps = cam_config.get('fps', 15)

            # --- Chọn thư mục lưu ---
            base_dir = Path("/media/ssd") if Path("/media/ssd").exists() else Path("/home/pi/videos")
            base_dir.mkdir(parents=True, exist_ok=True)
            output_file = base_dir / filename

            # --- Xác định thiết bị audio ---
            use_audio = False
            if os.path.exists("/dev/snd"):
                use_audio = True

            # --- Tạo command ---
            cmd = [
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                # Video input (pipe)
                "-f", "rawvideo",
                "-pix_fmt", "bgr24",
                "-s", f"{width}x{height}",
                "-r", str(fps),
                "-i", "pipe:0",
            ]

            # --- Audio input (nếu có) ---
            if use_audio:
                # Ensure audio device string is valid
                audio_device = audio_config.get('device') or "plughw:1,0"
                # Defensive: convert None to default string
                if audio_device is None:
                    audio_device = "plughw:1,0"
                cmd.extend([
                    "-f", "alsa",
                    "-ac", str(audio_config.get('channels', 1)),
                    "-ar", str(audio_config.get('sample_rate', 16000)),
                    "-i", str(audio_device),
                ])

            # --- Encoding settings ---
            cmd.extend([
                "-c:v", "h264_v4l2m2m",       # phần cứng GPU encoder
                "-b:v", "2M",                 # bitrate vừa phải
                "-pix_fmt", "yuv420p",
            ])

            if use_audio:
                cmd.extend([
                    "-c:a", "aac",
                    "-b:a", "128k",
                    "-map", "0:v:0",
                    "-map", "1:a:0",
                ])
            else:
                cmd.append("-an")  # disable audio nếu không có mic

            # --- Các tùy chọn đồng bộ ---
            cmd.extend([
                "-vsync", "1",
                "-async", "1",
                "-movflags", "+faststart",
                str(output_file)
            ])

            print(f"🎬 Starting FFmpeg optimized recording → {output_file}")
            self.current_recorder_process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                bufsize=10**7  # tăng dung lượng buffer để tránh pipe broken
            )

            # --- Kiểm tra khởi động ---
            time.sleep(0.3)
            if self.current_recorder_process.poll() is not None:
                err = self.current_recorder_process.stderr.read().decode('utf-8', errors='ignore')
                print(f"❌ FFmpeg failed to start: {err}")
                self.current_recorder_process = None
                return False

            print("✅ FFmpeg hardware-accelerated recording started")
            return True

        except Exception as e:
            print(f"⚠ FFmpeg recorder error: {e}")
            return False
    
    
    def _should_create_new_segment(self):
        """Check if we should create a new segment"""
        if not self.segment_start_time:
            return True
        
        elapsed = time.time() - self.segment_start_time
        max_duration = self.config['recording']['segment_duration']
        
        return elapsed >= max_duration
    
    def _recording_loop(self):
        """Main recording loop"""
        print("🎬 Starting recording loop...")
        
        hls_restart_counter = 0
        
        try:
            # Start camera
            self.camera.start()
            
            while not self._stop_recording:
                # Update LED status
                self._update_led_status()
                
                # Check USB availability
                if not self.usb_manager.is_available():
                    print("⚠ USB disconnected, waiting...")
                    if self.current_recorder_process:
                        try:
                            self.current_recorder_process.stdin.close()
                            self.current_recorder_process.wait(timeout=2)
                        except:
                            self.current_recorder_process.kill()
                        self.current_recorder_process = None
                    self.usb_manager.wait_until_available()
                    continue
                
                # Check if we need a new segment
                if self._should_create_new_segment():
                    if not self._create_new_segment():
                        print("✗ Failed to create new segment, retrying...")
                        time.sleep(5)
                        continue
                
                # Read frame from camera
                try:
                    frame = self.camera.read_frame(timeout=1.0)
                    if frame is None:
                        continue
                    
                    # Add overlays for recording
                    frame_with_overlays = self._add_overlays(frame)
                    
                    # Write to FFmpeg recording
                    if self.current_recorder_process:
                        try:
                            self.current_recorder_process.stdin.write(frame_with_overlays.tobytes())
                            self.current_recorder_process.stdin.flush()
                        except BrokenPipeError:
                            print("⚠ Recording pipe broken, creating new segment...")
                            self.segment_start_time = 0  # Force new segment
                        except Exception as e:
                            print(f"⚠ Recording write error: {e}")
                    
                    # Write to HLS stream (without overlays for better performance)
                    if self.hls_enabled:
                        if not self._write_frame_to_hls(frame):
                            hls_restart_counter += 1
                            if hls_restart_counter >= 100:
                                print("⚠ Too many HLS write failures, restarting...")
                                self._restart_hls_if_needed()
                                hls_restart_counter = 0
                        else:
                            hls_restart_counter = 0
                    
                except Exception as e:
                    print(f"⚠ Frame processing error: {e}")
                    time.sleep(0.1)
                    continue
                
        except Exception as e:
            print(f"✗ Recording loop error: {e}")
        
        finally:
            self._cleanup_recording()
    
    def _cleanup_recording(self):
        """Clean up recording resources"""
        print("🧹 Cleaning up recording resources...")
        
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
            except:
                pass
        
        # Turn off LED
        if self.record_led:
            try:
                self.record_led.off()
            except:
                pass
            
    def start_recording(self):
        """Start recording in background thread"""
        if self.is_recording:
            print("⚠ Already recording")
            return False
        
        print("🚀 Starting video recording...")
        self.is_recording = True
        self._stop_recording = False
        
        # Start HLS streaming for live view
        self._start_hls_stream()
        
        # Start recording thread
        self.recording_thread = threading.Thread(
            target=self._recording_loop,
            daemon=True
        )
        self.recording_thread.start()
        
        print("✓ Recording started")
        return True
    
    def stop_recording(self):
        """Stop recording safely"""
        if not self.is_recording:
            print("⚠ Not currently recording")
            return
        
        print("🛑 Stopping recording...")
        self._stop_recording = True
        self.is_recording = False
        
        # Wait for recording thread to finish
        if self.recording_thread and self.recording_thread.is_alive():
            self.recording_thread.join(timeout=5)
        
        print("✓ Recording stopped safely")
    
    def get_status(self):
        """Get current recording status"""
        status = {
            'recording': self.is_recording,
            'usb_available': self.usb_manager.is_available() if self.usb_manager else False,
            'camera_active': self.camera.proc is not None if self.camera else False,
            'hls_active': self.hls_process is not None and self.hls_process.poll() is None,
            'audio_available': self.micro is not None and self.enable_audio,
            'recording_with_audio': self.current_recorder_process is not None,
            'current_segment_duration': 0
        }
        
        if self.segment_start_time:
            status['current_segment_duration'] = time.time() - self.segment_start_time
        
        if self.usb_manager and self.usb_manager.is_available():
            status['free_space_gb'] = self.usb_manager.get_free_space_gb()
            status['free_space_percent'] = self.usb_manager.get_free_space_percent()
        
        return status
    
    def cleanup(self):
        """Clean up all resources"""
        print("🧹 Cleaning up recorder...")
        
        # Stop recording first
        self.stop_recording()
        
        # Cleanup LED
        if self.record_led:
            try:
                self.record_led.cleanup()
            except Exception as e:
                print(f"⚠ LED cleanup error: {e}")
        
        # Cleanup microphone
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


def main():
    """Main service entry point for systemd"""
    print("🚀 PiCam VideoRecorder Service Starting...")

    recorder = None
    stop_event = threading.Event()

    def _on_signal(sig, frame):
        print(f"\n📡 Received signal {sig}, shutting down...")
        stop_event.set()

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    try:
        # Initialize recorder (constructor already auto-starts recording)
        recorder = VideoRecorder()
        print("✓ VideoRecorder service started and recording")

        # Keep process alive until signal received.
        # Optionally do a simple health check every second.
        while not stop_event.is_set():
            time.sleep(1)
            # Optional health check (uncomment if you want local restart)
            # if not recorder.is_recording:
            #     print("⚠ Recording stopped unexpectedly, restarting...")
            #     recorder.start_recording()

    except Exception as e:
        print(f"❌ Service error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if recorder:
            print("🧹 Cleaning up service...")
            recorder.cleanup()
        print("✓ VideoRecorder service stopped")

if __name__ == "__main__":
    main()