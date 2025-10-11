#!/usr/bin/env python3
"""
Video Recording Module with FFmpeg-python library
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
from pathlib import Path
import shutil

# FFmpeg-python library
try:
    import ffmpeg
except ImportError:
    print("❌ ffmpeg-python not installed. Install with: pip install ffmpeg-python")
    sys.exit(1)

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

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
        self.usb_manager = None
        self.record_led = None
        self.micro = None
        self.gnss = None
        self.rtc = None
        self.rtc_lock = threading.Lock()

        # Recording state
        self.is_recording = False
        self.current_recorder_process = None
        self.segment_start_time = None
        self.recording_thread = None
        self._stop_recording = False

        # Camera stream
        self.camera_stream = None
        self.frame_queue = []
        self.frame_queue_lock = threading.Lock()
        self.max_queue_size = 30

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

    # ============ CONFIG ============
    def _load_config(self, config_file=None):
        """Load configuration from YAML file or use defaults"""
        if config_file is None:
            config_file = Path(__file__).parent.parent / 'config' / 'device_full.yaml'
        else:
            config_file = Path(config_file)
        
        if config_file.exists():
            try:
                yaml_config = load(config_file)
                print(f"✓ Loaded config from: {config_file}")
                return self._parse_yaml_config(yaml_config)
            except Exception as e:
                print(f"⚠ Error loading config: {e}")
                return self._get_default_config()
        else:
            print(f"⚠ Config file not found: {config_file}")
            return self._get_default_config()

    def _parse_yaml_config(self, yaml_config):
        """Parse YAML config and convert to recorder internal format"""
        video = yaml_config.get('video', {})
        audio = yaml_config.get('audio', {})
        storage = yaml_config.get('storage', {})
        paths = yaml_config.get('paths', {})
        gpio = yaml_config.get('gpio', {})
        caps = yaml_config.get('capabilities', {})
        device_info = yaml_config.get('device', {})
        
        v4l2_format = video.get('v4l2_format', '640x480')
        try:
            width, height = map(int, v4l2_format.split('x'))
        except ValueError:
            print(f"⚠ Invalid v4l2_format '{v4l2_format}', using 640x480")
            width, height = 640, 480
        
        config = {
            'camera': {
                'device': video.get('v4l2_device', '/dev/video0'),
                'width': width,
                'height': height,
                'fps': video.get('v4l2_fps', 30)
            },
            'audio': {
                'enabled': caps.get('audio', False),
                'device': audio.get('device'),
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

    # ============ COMPONENTS ============
    def _initialize_components(self):
        try:
            storage = self.config['storage']
            self.usb_manager = USBManager(
                path=storage['path'],
                min_free_gb=storage['min_free_gb'],
                min_free_percent=10
            )
            print("✓ USB Manager initialized")
            self.segment_duration = storage['segment_seconds']
            
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
                    self.enable_audio = False

            if self.config.get('capabilities', {}).get('gnss', False):
                try:
                    self.gnss = GNSSModule()
                    print("✓ GNSS initialized")
                except Exception as e:
                    print(f"⚠ GNSS not available: {e}")
                    self.enable_gps_overlay = False

            if self.config.get('capabilities', {}).get('rtc', False):
                try:
                    self.rtc = rtcModule()
                    print("✓ RTC module initialized")
                    try:
                        _ = self.rtc.read_time()
                    except Exception as e:
                        print(f"⚠ RTC test failed: {e}, disabling RTC")
                        self.rtc.close()
                        self.rtc = None
                except Exception as e:
                    print(f"⚠ RTC not available: {e}")

            self._setup_hls_streaming()
        except Exception as e:
            print(f"✗ Component initialization error: {e}")
            raise

    # ============ RTC / TIME ============
    def _get_time_text(self):
        """Get time text with RTC fallback to system time"""
        try:
            if self.rtc:
                if self.rtc_lock.acquire(blocking=False):
                    try:
                        dt = self.rtc.read_time()
                        return dt.strftime("%Y-%m-%d %H:%M:%S")
                    finally:
                        self.rtc_lock.release()
                else:
                    dt = datetime.now()
                    return dt.strftime("%Y-%m-%d %H:%M:%S")
            else:
                dt = datetime.now()
                return dt.strftime("%Y-%m-%d %H:%M:%S")
        except:
            dt = datetime.now()
            return dt.strftime("%Y-%m-%d %H:%M:%S")

    # ============ GPS / OVERLAY ============
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
        except:
            return "GPS: Error"

    def _add_overlays(self, frame):
        if frame is None:
            return frame
        frame = frame.copy()
        height, width = frame.shape[:2]
        cfg = self.config['overlay']

        if self.enable_time_overlay and cfg.get('timestamp_enabled', True):
            time_text = self._get_time_text()
            cv2.putText(frame, time_text, (10, height - 10), cv2.FONT_HERSHEY_SIMPLEX, 
                       cfg['font_scale'], cfg['text_color'], cfg['font_thickness'], cv2.LINE_AA)

        if self.enable_gps_overlay and cfg.get('gps_enabled', False):
            gps_text = self._get_gps_text()
            cv2.putText(frame, gps_text, (10, height - 30), cv2.FONT_HERSHEY_SIMPLEX, 
                       cfg['font_scale'], cfg['text_color'], cfg['font_thickness'], cv2.LINE_AA)

        return frame

    def _should_create_new_segment(self):
        if not self.segment_start_time:
            return True
        return (time.time() - self.segment_start_time) >= self.segment_duration

    # ============ HLS STREAM ============
    def _setup_hls_streaming(self):
        """Setup HLS streaming directory"""
        self.hls_dir.mkdir(parents=True, exist_ok=True)
        if self.hls_enabled:
            print(f"🎬 HLS streaming directory: {self.hls_dir}")
    
    def _start_hls_stream(self):
        """Start HLS streaming process using ffmpeg-python"""
        if not self.hls_enabled or self.hls_process:
            return False
        
        try:
            # Clean up old HLS files
            for f in self.hls_dir.glob("*.ts"):
                f.unlink()
            for f in self.hls_dir.glob("*.m3u8"):
                f.unlink()
            
            hls_playlist = self.hls_dir / "live.m3u8"
            width, height, fps = self.config['camera']['width'], self.config['camera']['height'], self.config['camera']['fps']
            
            # Build FFmpeg command using ffmpeg-python
            stream = (
                ffmpeg
                .input('pipe:0', format='rawvideo', pix_fmt='bgr24', s=f'{width}x{height}', r=target_fps)
                .filter('scale', width, height)  # No upscale, keep original size
                .output(str(hls_playlist),
                    vcodec='libx264',
                    preset='ultrafast',
                    tune='zerolatency',
                    **{'b:v': '800k'},  # Higher bitrate for better quality
                    g=target_fps,  # GOP size = frame rate
                    **{'r': target_fps},  # Force constant frame rate
                    pix_fmt='yuv420p',
                    f='hls',
                    hls_time=2,
                    hls_list_size=5,
                    hls_flags='delete_segments+omit_endlist',  # Add omit_endlist for live streaming
                    hls_segment_filename=str(self.hls_dir / 'segment_%03d.ts')
                )
                .global_args('-hide_banner', '-loglevel', 'error')
            )
            
            print(f"🌐 Starting HLS stream: {hls_playlist}")
            self.hls_process = ffmpeg.run_async(
                stream,
                pipe_stdin=True,
                pipe_stdout=False,
                pipe_stderr=False
            )
            print("✓ HLS stream started")
            return True
            
        except Exception as e:
            print(f"✗ Failed to start HLS stream: {e}")
            self.hls_process = None
            return False
    
    def _stop_hls_stream(self):
        """Stop HLS streaming process"""
        if self.hls_process:
            try:
                self.hls_process.stdin.close()
                self.hls_process.wait(timeout=2)
                print("✓ HLS stream stopped")
            except:
                try:
                    self.hls_process.kill()
                except:
                    pass
            self.hls_process = None
    
    def _write_frame_to_hls(self, frame):
        """Write frame to HLS stream"""
        if not self.hls_process:
            return False
        
        if self.hls_process.poll() is not None:
            self.hls_process = None
            return False
        
        try:
            self.hls_process.stdin.write(frame.tobytes())
            self.hls_process.stdin.flush()
            return True
        except (BrokenPipeError, OSError):
            self.hls_process = None
            return False

    # ============ RECORDING ============
    def _create_new_segment(self):
        """Create new recording segment using ffmpeg-python with audio support"""
        if not self.usb_manager.is_available():
            print("⚠ USB not available, waiting...")
            self.usb_manager.wait_until_available()

        if not self.usb_manager.has_enough_space():
            print("⚠ Not enough space, cleaning up old files...")
            return False

        # Close previous FFmpeg process
        if self.current_recorder_process:
            try:
                print("⏹ Closing previous segment...")
                self.current_recorder_process.stdin.close()
                try:
                    self.current_recorder_process.wait(timeout=0.5)
                except:
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

        width, height, fps = self.config['camera']['width'], self.config['camera']['height'], self.config['camera']['fps']
        
        try:
            # Build video input from pipe
            video = ffmpeg.input('pipe:0', format='rawvideo', pix_fmt='bgr24', s=f'{width}x{height}', r=fps)
            video = video.filter('scale', width, height)
            
            # Check if audio is enabled
            if self.micro and self.enable_audio:
                audio_device = self.config['audio'].get('device', 'hw:1,0')
                if audio_device.startswith("hw:"):
                    ffmpeg_audio_device = audio_device.replace("hw:", "plughw:", 1)
                else:
                    ffmpeg_audio_device = audio_device
                
                audio_rate = self.config['audio'].get('sample_rate', 48000)
                audio_ch = self.config['audio'].get('channels', 1)
                
                # Build audio input from ALSA device
                audio = ffmpeg.input(ffmpeg_audio_device, f='alsa', ac=audio_ch, ar=audio_rate, 
                                    thread_queue_size='512')
                
                # Combine video and audio
                output = ffmpeg.output(video, audio, str(output_file),
                    vcodec='libx264',
                    preset='ultrafast',
                    tune='zerolatency',
                    **{'b:v': '1M'},
                    pix_fmt='yuv420p',
                    acodec='aac',
                    **{'b:a': '128k'},
                    max_interleave_delta='0',
                    fflags='+genpts'
                ).global_args('-hide_banner', '-loglevel', 'error', '-y')
                
                print(f"✓ Audio enabled: {ffmpeg_audio_device} ({audio_rate}Hz, {audio_ch}ch)")
            else:
                # Video only
                output = ffmpeg.output(video, str(output_file),
                    vcodec='libx264',
                    preset='ultrafast',
                    tune='zerolatency',
                    **{'b:v': '1M'},
                    pix_fmt='yuv420p',
                    an=None,  # No audio
                    max_interleave_delta='0',
                    fflags='+genpts'
                ).global_args('-hide_banner', '-loglevel', 'error', '-y')
                print("ℹ Audio disabled")
            
            print(f"🎬 Starting FFmpeg recording: {output_file}")
            
            self.current_recorder_process = ffmpeg.run_async(
                output,
                pipe_stdin=True,
                pipe_stdout=False,
                pipe_stderr=False
            )
            
            time.sleep(0.2)
            if self.current_recorder_process.poll() is not None:
                print(f"✗ FFmpeg died immediately")
                self.current_recorder_process = None
                return False
            
            self.segment_start_time = time.time()
            print(f"✅ New segment started: {output_file}")
            return True
            
        except Exception as e:
            print(f"✗ Failed to start FFmpeg: {e}")
            import traceback
            traceback.print_exc()
            return False

    def _recording_loop(self):
        """Main recording loop with camera capture"""
        print("🎥 Recording thread started")
        
        try:
            # Start camera capture using ffmpeg-python
            width = self.config['camera'].get('width', 640)
            height = self.config['camera'].get('height', 480)
            fps = self.config['camera'].get('fps', 30)  # This will be adjusted based on resolution
            
            # Camera outputs YUYV 4:2:2 at specific resolutions/framerates
            if width == 640 and height == 480:
                target_fps = 30
            elif width == 320 and height == 240:
                target_fps = 15
            else:
                print(f"⚠ Unsupported resolution {width}x{height}, defaulting to 640x480@30fps")
                width, height = 640, 480
                target_fps = 30

            camera_stream = (
                ffmpeg
                .input(self.config['camera']['device'], 
                       format='v4l2',
                       input_format='yuyv422',  # Camera native format
                       video_size=f'{width}x{height}',
                       framerate=target_fps)
                .output('pipe:', format='rawvideo', pix_fmt='bgr24')  # Convert to BGR24
                .global_args('-hide_banner', '-loglevel', 'error')
            )
            
            print(f"📸 Starting camera: {self.config['camera']['device']} (YUYV422→BGR24)")
            self.camera_stream = ffmpeg.run_async(
                camera_stream,
                pipe_stdin=False,
                pipe_stdout=True,
                pipe_stderr=False
            )
            
            # Test read frame
            frame_size = width * height * 3
            test_buf = self.camera_stream.stdout.read(frame_size)
            if not test_buf or len(test_buf) < frame_size:
                print("✗ Camera test failed - no frames available!")
                return
            
            test_frame = np.frombuffer(test_buf, dtype=np.uint8).reshape((height, width, 3))
            print(f"✓ Camera test OK - frame shape: {test_frame.shape}")
            
            # Start HLS stream
            if self.hls_enabled:
                self._start_hls_stream()
            
            frame_count = 0
            last_report = time.time()
            no_frame_count = 0
            
            while not self._stop_recording:
                # Create new segment if needed
                if self._should_create_new_segment():
                    if not self._create_new_segment():
                        print("✗ Failed to create segment, retrying in 5s...")
                        time.sleep(5)
                        continue

                # Read frame from camera
                frame_buf = self.camera_stream.stdout.read(frame_size)
                if not frame_buf or len(frame_buf) < frame_size:
                    no_frame_count += 1
                    if no_frame_count > 300:
                        print("⚠ No frames from camera for 3s, attempting restart...")
                        try:
                            if self.hls_enabled and self.hls_process:
                                self._stop_hls_stream()
                            
                            self.camera_stream.stdin.close()
                            self.camera_stream.wait()
                            time.sleep(1)
                            
                            # Restart camera
                            self.camera_stream = ffmpeg.run_async(
                                camera_stream,
                                pipe_stdin=False,
                                pipe_stdout=True,
                                pipe_stderr=False
                            )
                            print("✓ Camera restarted")
                            
                            if self.hls_enabled:
                                # time.sleep(0.5)
                                self._start_hls_stream()
                            
                            self.segment_start_time = 0
                            no_frame_count = 0
                        except Exception as e:
                            print(f"✗ Camera restart failed: {e}")
                            time.sleep(5)
                            no_frame_count = 0
                    time.sleep(0.01)
                    continue
                
                no_frame_count = 0
                frame = np.frombuffer(frame_buf, dtype=np.uint8).reshape((height, width, 3))
                
                # Add overlays
                frame_with_overlay = self._add_overlays(frame)
                
                # Write to recording FFmpeg
                if self.current_recorder_process:
                    try:
                        self.current_recorder_process.stdin.write(frame_with_overlay.tobytes())
                        self.current_recorder_process.stdin.flush()
                        frame_count += 1
                    except (BrokenPipeError, OSError):
                        print("⚠ FFmpeg pipe broken, will create new segment")
                        self.segment_start_time = 0
                        self.current_recorder_process = None
                
                # Write to HLS stream (use original frame without overlay)
                if self.hls_enabled:
                    self._write_frame_to_hls(frame)  # Use original frame
                
                # Report progress every 5 seconds
                if time.time() - last_report >= 5.0:
                    fps = frame_count / 5.0
                    print(f"📊 Recording: {frame_count} frames in 5s ({fps:.1f} fps)")
                    frame_count = 0
                    last_report = time.time()

        except Exception as e:
            print(f"✗ Recording loop error: {e}")
        finally:
            # Cleanup
            if self.hls_enabled:
                self._stop_hls_stream()
            
            if self.current_recorder_process:
                try:
                    self.current_recorder_process.stdin.close()
                    self.current_recorder_process.wait(timeout=2)
                    print("✓ FFmpeg process closed")
                except:
                    try:
                        self.current_recorder_process.kill()
                    except:
                        pass
                self.current_recorder_process = None
            
            if self.camera_stream:
                try:
                    self.camera_stream.stdin.close()
                    self.camera_stream.wait(timeout=2)
                    print("✓ Camera stopped")
                except:
                    try:
                        self.camera_stream.kill()
                    except:
                        pass
                self.camera_stream = None

    # ============ CONTROL ============
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

    def _signal_handler(self, signum, frame):
        print(f"⚠ Signal {signum} received, stopping recording")
        self.stop_recording()
        sys.exit(0)

    def cleanup(self):
        """Clean up all resources safely"""
        print("🧹 Cleaning up recorder...")
        self.stop_recording()
        self._stop_hls_stream()

        if self.current_recorder_process:
            try:
                print("⏹ Stopping FFmpeg recorder...")
                self.current_recorder_process.stdin.close()
                self.current_recorder_process.wait(timeout=3)
                print("✓ FFmpeg recorder stopped")
            except:
                try:
                    self.current_recorder_process.kill()
                except:
                    pass
            self.current_recorder_process = None

        if self.camera_stream:
            try:
                self.camera_stream.stdin.close()
                self.camera_stream.wait(timeout=2)
            except:
                try:
                    self.camera_stream.kill()
                except:
                    pass
            self.camera_stream = None

        if self.record_led:
            try:
                self.record_led.off()
                self.record_led.cleanup()
            except Exception as e:
                print(f"⚠ LED cleanup error: {e}")

        if self.gnss:
            try:
                self.gnss.close()
            except Exception as e:
                print(f"⚠ GNSS cleanup error: {e}")

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