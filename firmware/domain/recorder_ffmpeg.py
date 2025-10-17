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
import queue
import threading

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
        self.output_dir = Path(__file__).parent.parent/ self.config['paths']['record_root']
        self.hls_dir = "/tmp/picam_hls"
        Path(self.hls_dir).mkdir(parents=True, exist_ok=True)
        
        # Recording settings
        self.segment_seconds = self.config['storage']['segment_seconds']
        
        # Hardware
        self.led_control = gpioLed(self.config['gpio'].get('record_led', 26))
        
        # RTC
        # try:
        #     self.rtc = rtcModule()
        #     self.rtc_available = True
        #     print("✅ RTC initialized")
        # except Exception as e:
        #     print(f"⚠️ RTC not available: {e}")
        #     self.rtc_available = False
        
        # # GNSS
        # try:
        #     if self.config['capabilities'].get('gnss', False):
        #         self.gnss = GNSSModule()
        #         self.gnss_available = True
        #         print("✅ GNSS initialized")
        #     else:
        #         self.gnss_available = False
        # except Exception as e:
        #     print(f"⚠️ GNSS not available: {e}")
        #     self.gnss_available = False
        
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
        """Get audio device in ALSA format with supported params"""
        if not self.config['audio'].get('enabled', False):
            print("ℹ️ Audio disabled in config")
            return None
        
        # NEW: Test devices (prefer hw for raw access)
        test_devices = [
            "hw:1,0",      # HD camera direct (priority)
            "plughw:1,0",  # HD camera plugin
            "hw:2,0",      # USB Audio direct
            "plughw:2,0",  # USB Audio plugin
        ]
        
        # NEW: Common param combos to test (USB mics often 44.1kHz stereo)
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
                    '-d', '1',  # 1 second test
                    '/tmp/audio_test.wav'
                ]
                try:
                    result = subprocess.run(
                        test_cmd,
                        capture_output=True,
                        timeout=3  # Slightly longer for USB init
                    )
                    if result.returncode == 0:
                        print(f"✅ Audio device verified: {alsa_device} ({params['channels']}ch @ {params['rate']}Hz)")
                        # Clean up
                        try:
                            Path('/tmp/audio_test.wav').unlink()
                        except:
                            pass
                        # NEW: Return dict for FFmpeg
                        return {
                            'device': alsa_device,
                            'rate': params['rate'],
                            'channels': params['channels']
                        }
                    else:
                        stderr = result.stderr.decode('utf-8', errors='ignore')
                        if 'No such device' not in stderr and 'cannot find card' not in stderr and len(stderr) > 0:
                            print(f"⚠️ {alsa_device} ({params['rate']}Hz/{params['channels']}ch): {stderr.split('\n')[0][:60]}")
                except subprocess.TimeoutExpired:
                    print(f"⏱️ {alsa_device} ({params['rate']}Hz/{params['channels']}ch): Timeout")
                except Exception:
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
        
        # Get devices - UPDATED: Audio now returns dict or None
        try:
            video_dev = self.get_video_device()
            # audio_info = self.get_audio_device()
        except Exception as e:
            print(f"❌ Device error: {e}")
            return False
        
        # NEW: Quick device lock check/kill
        # devs_to_check = [video_dev]
        # if audio_info:
        #     devs_to_check.append(audio_info['device'])
        # for dev in devs_to_check:
        #     try:
        #         if subprocess.run(['fuser', dev], capture_output=True).returncode == 0:
        #             print(f"⚠️ Device {dev} in use—killing processes")
        #             subprocess.run(['fuser', '-k', dev])
        #     except:
        #         pass
        
        # Parse video settings
        video_size = self.config['video']['v4l2_format']  # "640x480"
        video_fps = self.config['video']['v4l2_fps']
        
        # Build FFmpeg command
        cmd = [
            'ffmpeg',
            '-f', 'v4l2',
            '-input_format', 'yuyv422',
            '-video_size', video_size,
            '-framerate', str(video_fps),
            '-i', video_dev,
            # NEW: Low-latency for USB video
            '-fflags', 'nobuffer',
            '-flags', 'low_delay',
        ]
        
        # Add audio input if available
        # if audio_info:
        #     cmd.extend([
        #         '-f', 'alsa',
        #         '-channels', str(audio_info['channels']),
        #         '-sample_rate', str(audio_info['rate']),
        #         '-i', audio_info['device'],
        #         # NEW: Thread queue for Pi limits
        #         '-thread_queue_size', '512',
        #     ])
        #     print(f"   ↳ Audio: {audio_info['device']} ({audio_info['channels']}ch @ {audio_info['rate']}Hz)")
        # else:
        #     print(f"   ↳ Audio: Disabled (video only)")
        
        # Build video filter
        filter_string = 'scale=640:480:flags=bicubic,format=yuv420p'
        
        # Video codec settings
        cmd.extend([
            '-vf', filter_string,
            '-c:v', 'libx264',
            '-preset', 'veryfast',
            '-tune', 'zerolatency',
            '-profile:v', 'main',
            '-level', '3.1',
            '-x264-params', 'nal-hrd=cbr',
            '-g', str(video_fps * 2),
            '-keyint_min', str(video_fps * 2),
            '-sc_threshold', '0',
            '-b:v', '1200k',
            '-maxrate', '1500k',
            '-bufsize', '3000k',
            '-force_key_frames', f'expr:gte(t,n_forced*{2})',
            '-pix_fmt', 'yuv420p',
        ])
        
        # Audio codec if available
        # if audio_info:
        #     cmd.extend([
        #         '-c:a', 'aac',
        #         '-b:a', '128k',
        #         # NEW: A/V sync
        #         '-async', '1',
        #     ])
        
        # Tee muxer setup
        # NEW: Get current system time for segment naming
        from datetime import datetime
        start_time = datetime.now().strftime("%Y%m%d_%H%M%S")
        timestamp_pattern = f"{self.output_dir}/{start_time}_cam0_%03d.mp4"
        
        cmd.extend([
            '-f', 'tee',
            '-map', '0:v',  # Video map
        ])
        
        # if audio_info:
        #     cmd.extend(['-map', '1:a'])  # Audio map
        
        # Tee output - UPDATED: Use segment format with strftime and index for time-based segments
        tee_output = (
            f"[f=segment:segment_time={self.segment_seconds}:segment_format=mp4:"
            f"reset_timestamps=1:strftime=1:segment_list_flags=live]{timestamp_pattern}|"
            f"[f=hls:hls_time=2:hls_list_size=10:"
            f"hls_flags=delete_segments+independent_segments:"
            f"hls_segment_type=mpegts:start_number=0:"
            f"hls_segment_filename={self.hls_dir}/segment_%03d.ts]{self.hls_dir}/stream.m3u8"
        )
        
        cmd.append(tee_output)
        
        print(f"🎬 Starting FFmpeg recording...")
        print(f"   ↳ Video: {video_dev} ({video_size} @ {video_fps}fps)")
        print(f"   ↳ Output: {self.output_dir}/*.mp4 (starting from {start_time})")
        print(f"   ↳ HLS: {self.hls_dir}/stream.m3u8")
        print(f"   ↳ Segment: {self.segment_seconds}s")
        
        try:
            # Log command
            print(f"   ↳ Command: {' '.join(cmd)}")
            
            self.ffmpeg_process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                universal_newlines=True,
                bufsize=1
            )
            
            print(f"✅ FFmpeg started (PID: {self.ffmpeg_process.pid})")
            
            # ENHANCED: Monitoring with queue for non-blocking
            output_queue = queue.Queue()
            def monitor_ffmpeg():
                try:
                    for line in iter(self.ffmpeg_process.stdout.readline, ''):
                        output_queue.put(line)
                        lower_line = line.lower()
                        if any(word in lower_line for word in ['error', 'failed', 'no such device', 'invalid argument', 'ioctl', 'demuxing']):
                            print(f"⚠️ FFmpeg: {line.strip()}")
                except:
                    pass
            
            monitor_thread = threading.Thread(target=monitor_ffmpeg, daemon=True)
            monitor_thread.start()
            
            # Drain non-errors (silent by default)
            def drain_output():
                while self.is_running():
                    try:
                        line = output_queue.get(timeout=1)
                        # Uncomment for verbose: print(line.strip())
                    except queue.Empty:
                        continue
            
            drain_thread = threading.Thread(target=drain_output, daemon=True)
            drain_thread.start()
            
            # Storage monitor
            self._storage_monitor_thread = threading.Thread(target=self._storage_monitor_loop, daemon=True)
            self._storage_monitor_thread.start()
            
            # ENHANCED: Retry on early exit
            max_retries = 3
            for attempt in range(max_retries):
                time.sleep(2)  # USB init time
                if self.ffmpeg_process.poll() is None:
                    break
                print(f"⚠️ FFmpeg exited early (attempt {attempt+1}/{max_retries}): code {self.ffmpeg_process.returncode}")
                if attempt < max_retries - 1:
                    print("🔄 Retrying...")
                    self.ffmpeg_process = subprocess.Popen(
                        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        stdin=subprocess.DEVNULL, universal_newlines=True, bufsize=1
                    )
                    print(f"✅ Retry FFmpeg started (PID: {self.ffmpeg_process.pid})")
                else:
                    return False
            
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
        
        # if hasattr(self, 'gnss') and self.gnss_available:
        #     try:
        #         self.gnss.close()
        #         print("📡 GNSS closed")
        #     except:
        #         pass
        
        # if hasattr(self, 'rtc') and self.rtc_available:
        #     try:
        #         self.rtc.close()
        #         print("⏰ RTC closed")
        #     except:
        #         pass
        
        # print("✅ Cleanup complete")


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
