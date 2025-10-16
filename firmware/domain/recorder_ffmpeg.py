#!/usr/bin/env python3
"""
recorder_ffmpeg.py - Simple recorder using FFmpeg for both file recording and HLS streaming
- Single FFmpeg process for recording MP4 files + HLS stream
- No OpenCV, no PyAudio, no threading complexity
- Clean and stable
"""
import os
import sys
import time
import signal
import subprocess
import re
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from flask import Flask, Response, send_from_directory
from flask_cors import CORS
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
        self.output_dir = self.config['paths']['record_root']
        self.hls_dir = "/tmp/picam_hls"
        Path(self.hls_dir).mkdir(parents=True, exist_ok=True)
        
        # Recording settings
        self.segment_seconds = self.config['storage']['segment_seconds']
        
        # Hardware
        self.led_control = gpioLed(self.config['gpio'].get('record_led', 26))
        
        # RTC
        try:
            self.rtc = rtcModule()
            self.rtc_available = True
            print("✅ RTC initialized")
        except Exception as e:
            print(f"⚠️ RTC not available: {e}")
            self.rtc_available = False
        
        # GNSS
        try:
            if self.config['capabilities'].get('gnss', False):
                self.gnss = GNSSModule()
                self.gnss_available = True
                print("✅ GNSS initialized")
            else:
                self.gnss_available = False
        except Exception as e:
            print(f"⚠️ GNSS not available: {e}")
            self.gnss_available = False
        
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
        
        # Flask app for HLS serving
        self.app = Flask(__name__)
        
        # Enable CORS for all routes (allows WebUI to access HLS from different port)
        CORS(self.app, resources={r"/*": {"origins": "*"}})
        
        self.setup_flask_routes()
    
    def _storage_monitor_loop(self):
        """Monitor USB storage and update LED accordingly"""
        import time
        while not self._stop_flag and self.is_running():
            if not self.usb_manager.is_available():
                # USB disconnected - blink LED
                self.led_control.blink(0.3)
                print("⚠️ USB storage disconnected!")
            else:
                # USB connected and recording - LED should be solid on
                self.led_control.on()
            time.sleep(2)  # Check every 2 seconds
    
    def setup_flask_routes(self):
        """Setup Flask routes for HLS streaming"""
        
        @self.app.route('/')
        def index():
            return {
                "status": "running" if self.is_running() else "stopped",
                "hls_url": "/hls/stream.m3u8",
                "hls_dir": self.hls_dir,
                "ffmpeg_pid": self.ffmpeg_process.pid if self.ffmpeg_process else None
            }
        
        @self.app.route('/hls/<path:filename>')
        def serve_hls(filename):
            """Serve HLS playlist and segments"""
            file_path = Path(self.hls_dir) / filename
            if not file_path.exists():
                # Debug: list files in HLS directory
                files = list(Path(self.hls_dir).iterdir()) if Path(self.hls_dir).exists() else []
                return {
                    "error": "File not found",
                    "requested": str(file_path),
                    "hls_dir": self.hls_dir,
                    "files_in_dir": [str(f.name) for f in files]
                }, 404
            return send_from_directory(self.hls_dir, filename)
        
        @self.app.route('/health')
        def health():
            return {
                "status": "ok",
                "recording": self.is_running(),
                "storage_available": self.usb_manager.is_available(),
                "storage_space_ok": self.usb_manager.has_enough_space(),
                "ffmpeg_running": self.ffmpeg_process.poll() is None if self.ffmpeg_process else False
            }
    
    def get_video_device(self):
        """Find available camera"""
        video_dev = self.config['video'].get('v4l2_device', '/dev/video0')
        if Path(video_dev).exists():
            return video_dev
        
        # Try to find any available video device
        for i in range(10):
            dev = f'/dev/video{i}'
            if Path(dev).exists():
                print(f"✅ Found camera: {dev}")
                return dev
        
        raise Exception("No camera found")
    
    def get_audio_device(self):
        """Get audio device in ALSA format"""
        if not self.config['capabilities'].get('audio', False):
            print("ℹ️ Audio disabled in config")
            return None
        
        try:
            # List of devices to test (prioritize USB devices since card 0 is headphones only)
            # Card 1: HD camera microphone, Card 2: USB Audio Device
            test_devices = [
                "plughw:1,0",  # HD camera microphone (most likely)
                "plughw:2,0",  # USB Audio Device
                "hw:1,0",      # HD camera direct
                "hw:2,0",      # USB Audio direct
            ]
            
            print("🔍 Testing audio devices...")
            
            for alsa_device in test_devices:
                # Quick test with arecord (more reliable than FFmpeg test)
                test_cmd = [
                    'arecord',
                    '-D', alsa_device,
                    '-f', 'S16_LE',
                    '-r', '48000',
                    '-c', '1',
                    '-d', '0.5',  # 0.5 seconds
                    '/tmp/audio_test.wav'
                ]
                
                try:
                    result = subprocess.run(
                        test_cmd,
                        capture_output=True,
                        timeout=2
                    )
                    
                    if result.returncode == 0:
                        print(f"✅ Audio device verified: {alsa_device}")
                        # Clean up test file
                        try:
                            Path('/tmp/audio_test.wav').unlink()
                        except:
                            pass
                        return alsa_device
                    else:
                        stderr = result.stderr.decode('utf-8', errors='ignore')
                        if 'No such device' not in stderr and 'cannot find card' not in stderr:
                            print(f"⚠️ {alsa_device}: {stderr.split(chr(10))[0][:60]}")
                            
                except subprocess.TimeoutExpired:
                    print(f"⏱️ {alsa_device}: Timeout")
                except Exception:
                    pass
            
            print("⚠️ No working audio device found")
            return None
            
        except Exception as e:
            print(f"⚠️ Audio device error: {e}")
        
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
            audio_dev = self.get_audio_device()
        except Exception as e:
            print(f"❌ Device error: {e}")
            return False
        
        # Parse video settings
        video_size = self.config['video']['v4l2_format']  # "640x480"
        video_fps = self.config['video']['v4l2_fps']
        
        # Build FFmpeg command - Raspberry Pi camera uses YUYV format
        cmd = [
            'ffmpeg',
            '-f', 'v4l2',
            '-input_format', 'yuyv422',  # Raspberry Pi camera format
            '-video_size', video_size,
            '-framerate', str(video_fps),
            '-i', video_dev,
        ]
        
        # Add audio input if available
        if audio_dev:
            audio_rate = self.config['audio'].get('sample_rate', 48000)
            audio_channels = self.config['audio'].get('channels', 1)
            
            cmd.extend([
                '-f', 'alsa',
                '-channels', str(audio_channels),
                '-sample_rate', str(audio_rate),
                '-i', audio_dev,
            ])
            print(f"   ↳ Audio: {audio_dev} ({audio_channels}ch @ {audio_rate}Hz)")
        else:
            print(f"   ↳ Audio: Disabled (video only)")
        
        # Build video filter - TEMPORARY: Simple filter without overlay for testing
        # TODO: Add back timestamp and GPS overlay after camera works
        filter_string = 'scale=640:480:flags=bicubic,format=yuv420p'
        
        # Video codec settings (force Main profile for browser compatibility)
        cmd.extend([
            '-vf', filter_string,
            '-c:v', 'libx264',
            '-preset', 'veryfast',  # veryfast is better quality than ultrafast
            '-tune', 'zerolatency',
            '-profile:v', 'main',  # Main profile (compatible with MSE/HLS.js)
            '-level', '3.1',
            '-x264-params', 'nal-hrd=cbr',  # Constant bitrate for HLS
            '-g', str(video_fps * 2),  # Keyframe every 2 seconds
            '-keyint_min', str(video_fps * 2),  # Minimum keyframe interval
            '-sc_threshold', '0',
            '-b:v', '1200k',
            '-maxrate', '1500k',
            '-bufsize', '3000k',
            '-force_key_frames', f'expr:gte(t,n_forced*{2})',  # Force keyframes every 2s
            '-pix_fmt', 'yuv420p',  # Explicitly set pixel format for output
        ])
        
        # Audio codec if available
        if audio_dev:
            cmd.extend([
                '-c:a', 'aac',
                '-b:a', '128k',
            ])
        
        # Use tee muxer to output to both MP4 segments and HLS with single encode
        # This ensures both outputs have the same codec profile
        timestamp_pattern = f"{self.output_dir}/%Y%m%d_%H%M%S_cam0.mp4"
        
        cmd.extend([
            '-f', 'tee',
            '-map', '0:v',  # Map video stream
        ])
        
        if audio_dev:
            cmd.extend(['-map', '1:a'])  # Map audio stream if available
        
        # Tee output: MP4 segments | HLS stream
        tee_output = (
            f"[f=segment:segment_time={self.segment_seconds}:segment_format=mp4:"
            f"reset_timestamps=1:strftime=1]{timestamp_pattern}|"
            f"[f=hls:hls_time=2:hls_list_size=10:"
            f"hls_flags=delete_segments+independent_segments:"
            f"hls_segment_type=mpegts:start_number=0:"
            f"hls_segment_filename={self.hls_dir}/segment_%03d.ts]{self.hls_dir}/stream.m3u8"
        )
        
        cmd.append(tee_output)
        
        print(f"🎬 Starting FFmpeg recording...")
        print(f"   ↳ Video: {video_dev} ({video_size} @ {video_fps}fps)")
        print(f"   ↳ Output: {self.output_dir}/*.mp4")
        print(f"   ↳ HLS: {self.hls_dir}/stream.m3u8")
        print(f"   ↳ Segment: {self.segment_seconds}s")
        
        try:
            # Log full command for debugging
            print(f"   ↳ Command: {' '.join(cmd)}")
            
            self.ffmpeg_process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,  # Combine stderr with stdout
                stdin=subprocess.DEVNULL,
                universal_newlines=True,
                bufsize=1
            )
            
            print(f"✅ FFmpeg started (PID: {self.ffmpeg_process.pid})")
            
            # Start a thread to monitor FFmpeg output
            import threading
            def monitor_ffmpeg():
                for line in self.ffmpeg_process.stdout:
                    if 'error' in line.lower() or 'failed' in line.lower():
                        print(f"⚠️ FFmpeg: {line.strip()}")
            
            monitor_thread = threading.Thread(target=monitor_ffmpeg, daemon=True)
            monitor_thread.start()
            
            # Start storage monitoring thread
            self._storage_monitor_thread = threading.Thread(target=self._storage_monitor_loop, daemon=True)
            self._storage_monitor_thread.start()
            
            # Wait a bit to see if FFmpeg starts successfully
            time.sleep(1)
            if self.ffmpeg_process.poll() is not None:
                print(f"❌ FFmpeg exited immediately with code {self.ffmpeg_process.returncode}")
                return False
            
            self.led_control.on()
            return True
            
        except Exception as e:
            print(f"❌ Failed to start FFmpeg: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def stop_recording(self):
        """Stop FFmpeg recording"""
        if not self.is_running():
            return
        
        print("⏱ Stopping FFmpeg...")
        
        # Signal storage monitor to stop
        self._stop_flag = True
        
        try:
            # Send 'q' to FFmpeg stdin for graceful shutdown
            self.ffmpeg_process.terminate()
            self.ffmpeg_process.wait(timeout=10)
            print("   ✅ FFmpeg stopped")
        except subprocess.TimeoutExpired:
            print("   ⚠️ Timeout, force killing...")
            self.ffmpeg_process.kill()
            self.ffmpeg_process.wait()
        except Exception as e:
            print(f"   ⚠️ Error stopping FFmpeg: {e}")
        
        self.ffmpeg_process = None
        
        # Turn off LED when recording stops
        self.led_control.off()
        print("   💡 LED off")
    
    def is_running(self):
        """Check if FFmpeg is running"""
        return (self.ffmpeg_process is not None and 
                self.ffmpeg_process.poll() is None)
    
    def cleanup(self):
        """Cleanup resources"""
        print("🧹 Cleanup...")
        
        self.stop_recording()
        
        if hasattr(self, 'gnss') and self.gnss_available:
            try:
                self.gnss.close()
                print("📡 GNSS closed")
            except:
                pass
        
        if hasattr(self, 'rtc') and self.rtc_available:
            try:
                self.rtc.close()
                print("⏰ RTC closed")
            except:
                pass
        
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
        
        # Start recording
        if recorder.start_recording():
            print("📡 HLS stream: http://localhost:5000/hls/stream.m3u8")
            
            # Run Flask app
            recorder.app.run(host="0.0.0.0", port=5000, debug=False)
        else:
            print("❌ Failed to start recording")
            sys.exit(1)
            
    except KeyboardInterrupt:
        print("\n🛑 Keyboard interrupt")
        if recorder:
            recorder.cleanup()
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
