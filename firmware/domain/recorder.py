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
        """Create a new video segment file with audio support"""
        if not self.usb_manager.is_available():
            print("⚠ USB not available, waiting...")
            self.usb_manager.wait_until_available()
        
        if not self.usb_manager.has_enough_space():
            print("⚠ Insufficient space, cleaning up...")
            return False
        
        # Close current recording process if exists
        if self.current_recorder_process:
            try:
                self.current_recorder_process.stdin.close()
                self.current_recorder_process.terminate()
                self.current_recorder_process.wait(timeout=3)
                print("✓ Previous recording process closed safely")
            except Exception as e:
                print(f"⚠ Error closing previous recording process: {e}")
        
        # Close current OpenCV writer if exists (fallback)
        if self.current_writer:
            try:
                self.current_writer.release()
            except Exception as e:
                print(f"⚠ Error closing OpenCV writer: {e}")
        
        # Create new filename
        filename = self.usb_manager.get_new_filename()
        
        # Try to create FFmpeg recording process with audio (if available)
        if self.enable_audio and self.micro:
            success = self._create_ffmpeg_recorder_with_audio(filename)
        else:
            success = self._create_opencv_recorder(filename)
        
        if success:
            self.segment_start_time = time.time()
            print(f"📹 New segment: {os.path.basename(filename)}")
            return True
        else:
            print(f"✗ Failed to create recorder for {filename}")
            return False
    
    def _create_ffmpeg_recorder_with_audio(self, filename):
        """Create FFmpeg recording process with video and audio"""
        try:
            cam_config = self.config['camera']
            audio_config = self.config['audio']
            
            # Build FFmpeg command for recording with audio
            cmd = [
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                # Video input from pipe
                "-f", "rawvideo",
                "-pix_fmt", "bgr24",
                "-s", f"{cam_config['width']}x{cam_config['height']}",
                "-r", str(cam_config['fps']),
                "-i", "pipe:0",
                # Audio input from ALSA
                "-f", "pulse" if audio_config.get('device') is None else "alsa",
                "-ac", str(audio_config['channels']),
                "-ar", str(audio_config['sample_rate']),
            ]
            
            # Add audio device
            if audio_config.get('device'):
                cmd.extend(["-i", f"hw:{audio_config['device']}"])
            else:
                cmd.extend(["-i", "default"])  # Use default audio device
            
            # Encoding settings
            cmd.extend([
                # Video encoding
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", "23",
                "-pix_fmt", "yuv420p",
                # Audio encoding
                "-c:a", "aac",
                "-b:a", "128k",
                # Sync settings
                "-map", "0:v:0",  # Map video from first input
                "-map", "1:a:0",  # Map audio from second input
                "-vsync", "1",
                "-async", "1",
                # Output
                filename
            ])
            
            print(f"🎬 Starting FFmpeg recording with audio: {' '.join(cmd[6:12])}...")
            
            self.current_recorder_process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE
            )
            
            # Check if process started successfully
            time.sleep(0.2)
            if self.current_recorder_process.poll() is not None:
                err = self.current_recorder_process.stderr.read().decode('utf-8', errors='ignore')
                print(f"❌ FFmpeg recording failed to start: {err}")
                self.current_recorder_process = None
                return False
            
            print("✓ FFmpeg recording with audio started")
            return True
            
        except Exception as e:
            print(f"⚠ FFmpeg audio recording error: {e}")
            return False
    
    def _create_opencv_recorder(self, filename):
        """Fallback: Create OpenCV video writer (video only)"""
        try:
            fourcc = cv2.VideoWriter_fourcc(*self.config['recording']['format'])
            cam_config = self.config['camera']
            
            self.current_writer = cv2.VideoWriter(
                filename,
                fourcc,
                cam_config['fps'],
                (cam_config['width'], cam_config['height'])
            )
            
            if self.current_writer.isOpened():
                print("✓ OpenCV video recording started (no audio)")
                return True
            else:
                print("✗ Failed to create OpenCV video writer")
                return False
                
        except Exception as e:
            print(f"⚠ OpenCV recording error: {e}")
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
                    if self.current_writer:
                        self.current_writer.release()
                        self.current_writer = None
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
                    
                    # Write to recording (FFmpeg with audio or OpenCV fallback)
                    if self.current_recorder_process:
                        # FFmpeg recording with audio
                        try:
                            self.current_recorder_process.stdin.write(frame_with_overlays.tobytes())
                            self.current_recorder_process.stdin.flush()
                        except BrokenPipeError:
                            print("⚠ Recording pipe broken")
                            self.current_recorder_process = None
                        except Exception as e:
                            print(f"⚠ Recording write error: {e}")
                    elif self.current_writer and self.current_writer.isOpened():
                        # OpenCV fallback (video only)
                        self.current_writer.write(frame_with_overlays)
                    
                    # Write to HLS stream (without overlays for better performance)
                    # Use original frame for live view
                    if self.hls_enabled:
                        if not self._write_frame_to_hls(frame):
                            # Restart HLS every 100 failed writes
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
        
        # Close recording process (FFmpeg with audio)
        if self.current_recorder_process:
            try:
                if self.current_recorder_process.stdin:
                    self.current_recorder_process.stdin.close()
                self.current_recorder_process.terminate()
                self.current_recorder_process.wait(timeout=5)
                print("✓ Recording process closed")
            except Exception as e:
                print(f"⚠ Error closing recording process: {e}")
                try:
                    self.current_recorder_process.kill()
                except:
                    pass
            self.current_recorder_process = None
        
        # Close video writer (fallback)
        if self.current_writer:
            try:
                self.current_writer.release()
                print("✓ Video writer closed")
            except Exception as e:
                print(f"⚠ Error closing video writer: {e}")
            self.current_writer = None
        
        # Stop camera
        if self.camera:
            try:
                self.camera.stop()
                print("✓ Camera stopped")
            except Exception as e:
                print(f"⚠ Error stopping camera: {e}")
        
        # Turn off LED
        if self.record_led:
            try:
                self.record_led.off()
                print("✓ Record LED turned off")
            except Exception as e:
                print(f"⚠ Error with LED: {e}")
    
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
    """Test the video recorder"""
    print("=== Video Recorder Test ===\n")
    
    try:
        # Create recorder
        recorder = VideoRecorder()
        print("✓ Recorder initialized\n")
        
        while True:
            print("\n=== Recording Control ===")
            print("1. Start Recording")
            print("2. Stop Recording") 
            print("3. Show Status")
            print("4. Toggle Time Overlay")
            print("5. Toggle GPS Overlay")
            print("6. Test LED")
            print("7. Restart HLS")
            print("8. Test Audio")
            print("9. Exit")
            
            choice = input("Enter choice (1-9): ").strip()
            
            if choice == "1":
                recorder.start_recording()
                
            elif choice == "2":
                recorder.stop_recording()
                
            elif choice == "3":
                status = recorder.get_status()
                print("\n📊 Current Status:")
                for key, value in status.items():
                    print(f"  {key}: {value}")
                
            elif choice == "4":
                recorder.enable_time_overlay = not recorder.enable_time_overlay
                print(f"Time overlay: {'ON' if recorder.enable_time_overlay else 'OFF'}")
                
            elif choice == "5":
                recorder.enable_gps_overlay = not recorder.enable_gps_overlay
                print(f"GPS overlay: {'ON' if recorder.enable_gps_overlay else 'OFF'}")
                
            elif choice == "6":
                if recorder.record_led:
                    print("Testing LED...")
                    for i in range(3):
                        recorder.record_led.on()
                        time.sleep(0.5)
                        recorder.record_led.off()
                        time.sleep(0.5)
                    print("LED test completed")
                else:
                    print("LED not available")
            
            elif choice == "7":
                print("Restarting HLS stream...")
                recorder._stop_hls_stream()
                time.sleep(1)
                if recorder._start_hls_stream():
                    print("✓ HLS restarted")
                else:
                    print("✗ HLS restart failed")
                
            elif choice == "8":
                if recorder.micro:
                    print("Testing microphone...")
                    try:
                        print("Recording 3 seconds of audio...")
                        audio_data = recorder.micro.record(duration=3)
                        print(f"✓ Audio recorded: {len(audio_data)} samples")
                        
                        test_file = "/tmp/test_audio.wav"
                        recorder.micro.save(test_file)
                        print(f"✓ Audio saved to {test_file}")
                        
                        # Show audio device info
                        import sounddevice as sd
                        devices = sd.query_devices()
                        print(f"✓ Available audio devices: {len(devices)}")
                        
                    except Exception as e:
                        print(f"⚠ Audio test failed: {e}")
                else:
                    print("⚠ No microphone available")
                    
            elif choice == "9":
                print("Exiting...")
                break
                
            else:
                print("Invalid choice")
        
    except KeyboardInterrupt:
        print("\nInterrupted by user")
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        try:
            recorder.cleanup()
        except:
            pass

if __name__ == "__main__":
    main()