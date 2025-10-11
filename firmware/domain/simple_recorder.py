from __future__ import annotations
import cv2
import time
import threading
import numpy as np
from pathlib import Path
import pyaudio
import wave
import subprocess
from datetime import datetime
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# Hardware components
from firmware.hal.usb_manager import USBManager
from firmware.hal.gpio_leds import gpioLed
from firmware.hal.gnss import GNSSModule
from firmware.hal.rtc import rtcModule
from firmware.config.config_loader import load
import ffmpeg
# Check if ffmpeg is available
try:
    # Check ffmpeg command availability
    subprocess.run(['ffmpeg', '-version'], 
                  capture_output=True, 
                  check=True)
    FFMPEG_AVAILABLE = True
except (subprocess.SubprocessError, FileNotFoundError):
    FFMPEG_AVAILABLE = False
    print("⚠ FFmpeg not installed, video conversion and HLS disabled")

class VideoRecorder:
    def __init__(self, config=None):
        """Initialize video recorder with simple OpenCV capture"""
        # Initialize thread management
        self.segment_thread = None
        self.segment_ready = threading.Event()
        self.segment_ready.set()  # Initially ready
        
        # Default config
        default_config = {
            'camera': {
                'device': 0,  # Default camera
                'width': 640,
                'height': 480,
                'fps': 30,
                'fourcc': 'MJPG'
            },
            'audio': {
                'enabled': True,
                'channels': 1,
                'rate': 44100,
                'chunk': 1024,
                'format': pyaudio.paInt16
            },
            'storage': {
                'path': '/media/ssd',
                'segment_seconds': 30,  # Split files every 30 seconds
                'container': 'mkv',     # Container format
                'filename_pattern': "%Y%m%d-%H%M%S.mkv"  # Filename pattern
            },
            'gpio': {
                'record_led': 26  # Default GPIO pin for record LED
            },
            'overlay': {
                'timestamp': True,
                'gps': False,
                'text_color': (255, 255, 255),
                'bg_color': (0, 0, 0),
                'font_scale': 0.7,
                'thickness': 2
            },
            'hls': {
                'enabled': True,
                'dir': '/tmp/picam_hls',
                'segment_time': 2,
                'list_size': 5,
                'bitrate': '800k'
            }
        }
        # Deep merge configs
        self.config = default_config.copy()
        if config:
            for section in default_config:
                if section in config:
                    if isinstance(default_config[section], dict):
                        self.config[section].update(config[section])
        
        # Initialize components
        self.is_recording = False
        self.frame_count = 0
        self.start_time = None
        
        # Initialize hardware components
        self._init_hardware_components()
        
        # Initialize camera with optimized settings
        self.camera = cv2.VideoCapture(self.config['camera']['device'])
        
        # Set optimized camera properties
        self.camera.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # Minimize buffering delay
        self.camera.set(cv2.CAP_PROP_FRAME_WIDTH, self.config['camera']['width'])
        self.camera.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config['camera']['height'])
        self.camera.set(cv2.CAP_PROP_FPS, self.config['camera']['fps'])
        
        # Force RGB color space conversion
        try:
            self.camera.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
            self.camera.set(cv2.CAP_PROP_CONVERT_RGB, 1)  # Ensure RGB conversion
        except:
            print("⚠ Failed to set camera color format properties")
            
        # Check if camera opened successfully
        if not self.camera.isOpened():
            raise Exception("Error: Could not open camera")
            
        # Verify we got the requested parameters
        self.width = int(self.camera.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.height = int(self.camera.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.fps = int(self.camera.get(cv2.CAP_PROP_FPS))
        
        # Warm up the camera
        print("Warming up camera...")
        for _ in range(10):
            self.camera.read()
            time.sleep(0.1)
        
        print(f"Camera initialized: {self.width}x{self.height} @ {self.fps}fps")
        
        # Initialize audio if enabled
        self.audio_enabled = self.config['audio']['enabled']
        if self.audio_enabled:
            try:
                self.audio = pyaudio.PyAudio()
                # Find the specified audio device
                device_found = False
                device_info = None
                
                if self.config['audio'].get('device'):
                    # Try to find device by name/id
                    for i in range(self.audio.get_device_count()):
                        info = self.audio.get_device_info_by_index(i)
                        if self.config['audio']['device'] in str(info['name']):
                            device_info = info
                            device_found = True
                            break
                
                if not device_found:
                    # Use default device
                    device_info = self.audio.get_default_input_device_info()
                    print(f"⚠ Audio device not found, using default: {device_info['name']}")
                else:
                    print(f"✓ Using audio device: {device_info['name']}")
                
                self.audio_device_index = device_info['index']
                self.audio_frames = []
            except Exception as e:
                print(f"⚠ Failed to initialize audio: {e}")
                self.audio_enabled = False
        
        # Create storage path
        self.storage_path = Path(self.config['storage']['path'])
        self.storage_path.mkdir(parents=True, exist_ok=True)
    
    def _setup_hls(self):
        """Setup HLS streaming"""
        if not self.config['hls']['enabled'] or not FFMPEG_AVAILABLE:
            return False
            
        try:
            # Create HLS directory
            hls_dir = Path(self.config['hls']['dir'])
            hls_dir.mkdir(parents=True, exist_ok=True)
            
            # Clean up old files
            for f in hls_dir.glob("*.ts"):
                f.unlink()
            for f in hls_dir.glob("*.m3u8"):
                f.unlink()
            
            # Setup FFmpeg command for HLS
            stream = (
                ffmpeg
                .input('pipe:0', format='rawvideo', pix_fmt='bgr24',
                       s=f'{self.width}x{self.height}', r=self.fps)
                .output(str(hls_dir / "live.m3u8"),
                    format='hls',
                    hls_time=self.config['hls']['segment_time'],
                    hls_list_size=self.config['hls']['list_size'],
                    hls_flags='delete_segments+omit_endlist',
                    video_bitrate=self.config['hls']['bitrate'],
                    preset='ultrafast',
                    tune='zerolatency',
                    g=self.fps,  # GOP size = fps
                    pix_fmt='yuv420p'
                )
                .global_args('-hide_banner', '-loglevel', 'error')
            )
            
            # Start FFmpeg process
            self.hls_process = ffmpeg.run_async(
                stream, pipe_stdin=True, pipe_stdout=False, pipe_stderr=False
            )
            print(f"✓ HLS stream started: {hls_dir}/live.m3u8")
            return True
            
        except Exception as e:
            print(f"✗ Failed to start HLS: {e}")
            return False

    def start_recording(self):
        """Start recording video and audio"""
        if self.is_recording:
            print("Already recording")
            return
        
        self.is_recording = True
        self.frame_count = 0
        self.start_time = time.time()
        
        # Generate output filename with timestamp
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.video_file = self.storage_path / f"{timestamp}.avi"
        
        # Initialize video writer
        fourcc = cv2.VideoWriter_fourcc(*self.config['camera']['fourcc'])
        self.video_writer = cv2.VideoWriter(
            str(self.video_file),
            fourcc,
            self.fps,
            (self.width, self.height)
        )
        
        # Setup HLS streaming
        self._setup_hls()
        
        # Check and wait for USB storage
        if hasattr(self, 'usb_manager'):
            if not self.usb_manager.is_available():
                print("⚠ USB not available")
                self.usb_manager.wait_until_available()
            
            while not self.usb_manager.has_enough_space():
                print("⚠ Not enough storage space")
                self.usb_manager.cleanup_old_files()
                if not self.usb_manager.has_enough_space():
                    print("❌ Could not free enough space")
                    return False
                
            print("✓ Storage ready")

        # Turn on record LED
        if hasattr(self, 'record_led'):
            self.record_led.on()

        # Start recording threads
        self.record_thread = threading.Thread(target=self._record_loop)
        self.record_thread.start()
        
        if self.audio_enabled:
            self.audio_file = self.storage_path / f"{timestamp}.wav"
            try:
                self.audio_stream = self.audio.open(
                    format=self.config['audio']['format'],
                    channels=self.config['audio']['channels'],
                    rate=self.config['audio']['rate'],
                    input=True,
                    input_device_index=self.audio_device_index,
                    frames_per_buffer=self.config['audio']['chunk']
                )
            except Exception as e:
                print(f"⚠ Failed to open audio stream: {e}")
                self.audio_enabled = False
                return
            self.audio_frames = []
            self.audio_thread = threading.Thread(target=self._record_audio)
            self.audio_thread.start()
        
        print(f"Recording started: {self.video_file}")
    
    def stop_recording(self):
        """Stop recording and save files"""
        if not self.is_recording:
            return
        
        self.is_recording = False
        
        # Wait for threads to finish
        if hasattr(self, 'record_thread'):
            self.record_thread.join()
        
        # Close video writer
        current_video = None
        if hasattr(self, 'video_writer'):
            self.video_writer.release()
            current_video = self.video_file
        
        # Handle audio recording
        if self.audio_enabled and hasattr(self, 'audio_stream'):
            self.audio_stream.stop_stream()
            self.audio_stream.close()
            
            # Wait for audio thread
            if hasattr(self, 'audio_thread'):
                self.audio_thread.join()
            
            # Save final audio segment
            if self.audio_frames and current_video:
                current_audio = current_video.with_suffix('.wav')
                with wave.open(str(current_audio), 'wb') as wf:
                    wf.setnchannels(self.config['audio']['channels'])
                    wf.setsampwidth(self.audio.get_sample_size(self.config['audio']['format']))
                    wf.setframerate(self.config['audio']['rate'])
                    wf.writeframes(b''.join(self.audio_frames))
                
                # Convert final segment
                self._convert_to_mp4(current_video, current_audio)
        
        # Calculate actual FPS
        elapsed_time = time.time() - self.start_time
        recorded_fps = self.frame_count / elapsed_time
        print(f"Recording stopped: {self.frame_count} frames in {elapsed_time:.1f}s ({recorded_fps:.1f} fps)")
    
    def _write_frame_to_hls(self, frame):
        """Write frame to HLS stream"""
        if not hasattr(self, 'hls_process') or not self.hls_process:
            return False
            
        if self.hls_process.poll() is not None:
            self.hls_process = None
            return False
            
        try:
            self.hls_process.stdin.write(frame.tobytes())
            return True
        except (BrokenPipeError, OSError):
            self.hls_process = None
            return False

    def _should_create_new_segment(self):
        """Check if it's time to create a new segment"""
        if not hasattr(self, 'segment_start_time'):
            print("First segment creation")
            return True
        segment_duration = time.time() - self.segment_start_time
        should_create = segment_duration >= self.config['storage']['segment_seconds']
        if should_create:
            print(f"Segment duration {segment_duration:.1f}s exceeded limit of {self.config['storage']['segment_seconds']}s")
        return should_create

    def _convert_to_mp4(self, video_file, audio_file=None):
        """Convert video to MP4, optionally merging with audio"""
        try:
            output_file = video_file.with_suffix('.mp4')
            
            if audio_file and self.audio_enabled:
                # Merge video and audio using ffmpeg command
                cmd = [
                    'ffmpeg',
                    '-i', str(video_file),
                    '-i', str(audio_file),
                    '-c:v', 'copy',
                    '-c:a', 'aac',
                    '-strict', 'experimental',
                    '-loglevel', 'error',
                    '-y',
                    str(output_file)
                ]
                
                # Run FFmpeg
                process = subprocess.run(cmd, 
                                      capture_output=True, 
                                      text=True)
                
                if process.returncode == 0:
                    # Remove original files after successful merge
                    video_file.unlink()
                    audio_file.unlink()
                    print(f"✓ Created MP4 with audio: {output_file}")
                else:
                    print(f"⚠ FFmpeg error: {process.stderr}")
                    return False
                
            else:
                # Just convert video to MP4 without audio
                cmd = [
                    'ffmpeg',
                    '-i', str(video_file),
                    '-c:v', 'copy',
                    '-an',
                    '-loglevel', 'error',
                    '-y',
                    str(output_file)
                ]
                
                # Run FFmpeg
                process = subprocess.run(cmd, 
                                      capture_output=True, 
                                      text=True)
                
                if process.returncode == 0:
                    # Remove original file after successful conversion
                    video_file.unlink()
                    print(f"✓ Created MP4: {output_file}")
                else:
                    print(f"⚠ FFmpeg error: {process.stderr}")
                    return False
            
            return True
            
        except Exception as e:
            print(f"✗ Failed to convert video: {e}")
            return False

    def _finalize_segment(self, video_file, audio_file=None):
        """Finalize a segment in a separate thread"""
        try:
            # Convert to MP4 in background
            if self.audio_enabled and audio_file:
                self._convert_to_mp4(video_file, audio_file)
            else:
                self._convert_to_mp4(video_file)
        finally:
            self.segment_ready.set()  # Mark that we're ready for next segment

    def _create_new_segment(self):
        """Create new video and audio segment"""
        # Don't create new segment if previous one is still processing
        if not self.segment_ready.is_set():
            print("⚠ Still processing previous segment, skipping segmentation")
            return True
            
        # Check storage space and handle USB events
        if not self.usb_manager.has_enough_space():
            print("⚠ Storage space low, cleaning up...")
            self.usb_manager.cleanup_old_files()
            if not self.usb_manager.has_enough_space():
                print("❌ Could not free enough space")
                self.stop_recording()
                return False

        # Close current video writer
        if hasattr(self, 'video_writer'):
            self.video_writer.release()
            current_video = self.video_file

        # Handle audio segment in background
        if (self.audio_enabled and hasattr(self, 'audio_frames') and 
            hasattr(self, 'video_file') and self.audio_frames):
            
            # Save current audio frames
            current_audio = self.video_file.with_suffix('.wav')
            with wave.open(str(current_audio), 'wb') as wf:
                wf.setnchannels(self.config['audio']['channels'])
                wf.setsampwidth(self.audio.get_sample_size(self.config['audio']['format']))
                wf.setframerate(self.config['audio']['rate'])
                wf.writeframes(b''.join(self.audio_frames))
            
            # Reset audio frames for new segment
            audio_frames = self.audio_frames
            self.audio_frames = []
            
            # Start background conversion
            self.segment_ready.clear()
            self.segment_thread = threading.Thread(
                target=self._finalize_segment,
                args=(current_video, current_audio)
            )
            self.segment_thread.start()
        else:
            # Start background conversion without audio
            self.segment_ready.clear()
            self.segment_thread = threading.Thread(
                target=self._finalize_segment,
                args=(current_video,)
            )
            self.segment_thread.start()

        # Generate new filename with timestamp (use .mkv for temp files)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S.mkv")
        self.video_file = self.storage_path / timestamp

        # Initialize new video writer
        fourcc = cv2.VideoWriter_fourcc(*self.config['camera']['fourcc'])
        self.video_writer = cv2.VideoWriter(
            str(self.video_file),
            fourcc,
            self.fps,
            (self.width, self.height)
        )

        self.segment_start_time = time.time()
        print(f"✓ New segment started: {self.video_file}")
        return True

    def _record_loop(self):
        """Main video recording loop"""
        self.segment_start_time = time.time()
        last_frame_time = time.time()
        last_usb_check = time.time()
        frames_in_segment = 0
        frame_count_at_start = self.frame_count
        errors_count = 0
        
        while self.is_recording:
            try:
                # Only check USB status every second
                current_time = time.time()
                if current_time - last_usb_check > 1.0:
                    if hasattr(self, 'usb_manager') and not self.usb_manager.is_available():
                        print("⚠ USB disconnected during recording")
                        self.usb_manager.wait_until_available()
                        if not self.is_recording:
                            break
                    last_usb_check = current_time
                
                # Log frame timing
                current_time = time.time()
                frame_interval = current_time - last_frame_time
                if frame_interval > 1.0/self.fps * 2:  # If frame took twice as long as expected
                    print(f"⚠ Slow frame: {frame_interval:.3f}s (target: {1.0/self.fps:.3f}s)")
                last_frame_time = current_time
                
                # Check if need to create new segment
                if self._should_create_new_segment():
                    if not self._create_new_segment():
                        print("✗ Failed to create new segment")
                        break
            except Exception as e:
                print(f"⚠ Error in main record loop: {e}")
                break

            # Calculate target time for this frame
            target_time = last_frame_time + 1.0/self.fps
            
            # Read frame with timing
            start_read = time.time()
            ret, frame = self.camera.read()
            read_time = time.time() - start_read
            
            if ret:
                errors_count = 0  # Reset error counter on successful read
                try:
                    # Check frame format and convert if needed
                    if len(frame.shape) != 3 or frame.shape[2] != 3:
                        print(f"⚠ Converting frame format from {frame.shape} to RGB")
                        frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR if len(frame.shape) == 2 else cv2.COLOR_BGR2RGB)
                    
                    # Direct write for maximum performance
                    if self.video_writer.isOpened():
                        self.video_writer.write(frame)
                    else:
                        print("⚠ Video writer is not opened!")
                        break
                        
                    # Simplified frame processing pipeline
                    frame_for_hls = frame  # Use original frame for HLS
                    
                    # Only add overlays if enabled and we're not behind
                    if read_time < 0.03:  # Stricter timing check
                        if self.config['overlay']['timestamp'] or self.config['overlay']['gps']:
                            frame_for_hls = self._add_overlays(frame.copy())
                    
                    # Write to HLS stream if enabled and not too far behind
                    if (self.config['hls']['enabled'] and FFMPEG_AVAILABLE and 
                        hasattr(self, 'hls_process') and read_time < 0.05):  # Relaxed timing for HLS
                        try:
                            self.hls_process.stdin.write(frame_for_hls.tobytes())
                        except (BrokenPipeError, OSError):
                            pass  # Skip HLS if having issues
                            
                            self.frame_count += 1
                            frame_processed = True
                            
                            # Log progress every 30 frames
                            if self.frame_count % 30 == 0:
                                elapsed = time.time() - self.start_time
                                current_fps = self.frame_count / elapsed
                                print(f"Recording: {self.frame_count} frames, {elapsed:.1f}s ({current_fps:.1f} fps)")
                 
                            
                    # Only sleep if we processed the frame quickly
                    current_time = time.time()
                    if current_time < target_time and read_time < 0.05:
                        time.sleep(target_time - current_time)
                    
                except Exception as e:
                    print(f"⚠ Error writing frame: {e}")
                    break
            else:
                errors_count += 1
                print(f"⚠ Failed to read frame from camera ({errors_count})")
                
                if errors_count >= 5:
                    print("❌ Too many camera read failures, stopping recording")
                    break
                    
                # Try to recover by reinitializing camera
                if errors_count == 3:
                    print("⚠ Attempting to reinitialize camera...")
                    self.camera.release()
                    time.sleep(1)
                    self.camera = cv2.VideoCapture(self.config['camera']['device'])
                    if not self.camera.isOpened():
                        print("❌ Failed to reinitialize camera")
                        break
                        
                time.sleep(0.1)  # Brief pause before retry
    
    def _record_audio(self):
        """Audio recording loop"""
        while self.is_recording:
            data = self.audio_stream.read(self.config['audio']['chunk'])
            self.audio_frames.append(data)
    
    def _init_hardware_components(self):
        """Initialize hardware components"""
        try:
            # USB Storage Manager
            # Initialize USB manager with config values
            self.usb_manager = USBManager(
                path=self.config['storage']['path'],
                min_free_gb=self.config.get('storage', {}).get('min_free_gb', 1.0),
                min_free_percent=self.config.get('storage', {}).get('min_free_percent', 10),
                camera_id=self.config.get('device', {}).get('id', 1)
            )
            print("✓ USB Manager initialized")

            # Record LED
            if 'gpio' in self.config and 'record_led' in self.config['gpio']:
                try:
                    self.record_led = gpioLed(self.config['gpio']['record_led'])
                    # Test LED
                    self.record_led.off()  # Make sure LED is off initially
                    print("✓ Record LED initialized")
                except Exception as e:
                    print(f"⚠ Record LED init failed: {e}")
                    self.record_led = None
            
            # GNSS Module
            if self.config.get('capabilities', {}).get('gnss', False):
                try:
                    self.gnss = GNSSModule()
                    print("✓ GNSS initialized")
                except Exception as e:
                    print(f"⚠ GNSS not available: {e}")
                    self.gnss = None
            
            # RTC Module
            if self.config.get('capabilities', {}).get('rtc', False):
                try:
                    self.rtc = rtcModule()
                    print("✓ RTC initialized")
                    # Test RTC
                    try:
                        _ = self.rtc.read_time()
                    except Exception as e:
                        print(f"⚠ RTC test failed: {e}")
                        self.rtc = None
                except Exception as e:
                    print(f"⚠ RTC not available: {e}")
                    self.rtc = None
            
        except Exception as e:
            print(f"✗ Hardware initialization error: {e}")
            raise

    def _get_time_text(self):
        """Get time text with RTC fallback"""
        try:
            if hasattr(self, 'rtc') and self.rtc:
                dt = self.rtc.read_time()
                return dt.strftime("%Y-%m-%d %H:%M:%S")
        except:
            pass
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _get_gps_text(self):
        """Get GPS text if available"""
        if not hasattr(self, 'gnss') or not self.gnss:
            return None
        
        try:
            gps_data = self.gnss.get_location()
            if gps_data and gps_data.get('fix_quality', 0) > 0:
                lat = gps_data.get('latitude', 0)
                lon = gps_data.get('longitude', 0)
                return f"GPS: {lat:.6f}, {lon:.6f}"
            return "GPS: No Fix"
        except:
            return "GPS: Error"

    def _add_overlays(self, frame):
        """Add overlays (timestamp, GPS, etc) to frame"""
        cfg = self.config['overlay']
        
        # Skip if no overlays needed
        if not (cfg.get('timestamp', True) or cfg.get('gps', False)):
            return frame
            
        # Make a copy only if we need to add overlays
        frame = frame.copy()
        height = frame.shape[0]
        y_pos = height - 10
        
        # Pre-configure common text parameters
        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = cfg.get('font_scale', 0.7)
        color = cfg.get('text_color', (255, 255, 255))
        thickness = cfg.get('thickness', 2)
        
        # Timestamp from RTC or system (most common case first)
        if cfg.get('timestamp', True):
            cv2.putText(
                frame, self._get_time_text(),
                (10, y_pos), font, font_scale,
                color, thickness, cv2.LINE_AA
            )
            y_pos -= 30
        
        # GPS coordinates if enabled
        if cfg.get('gps', False) and hasattr(self, 'gnss') and self.gnss:
            gps_text = self._get_gps_text()
            if gps_text:
                cv2.putText(
                    frame, gps_text,
                    (10, y_pos), font, font_scale,
                    color, thickness, cv2.LINE_AA
                )
        
        return frame
    
    def cleanup(self):
        """Clean up resources"""
        self.stop_recording()
        
        # Stop HLS stream
        if hasattr(self, 'hls_process') and self.hls_process:
            try:
                self.hls_process.stdin.close()
                self.hls_process.wait(timeout=2)
            except:
                try:
                    self.hls_process.kill()
                except:
                    pass
            self.hls_process = None
        
        # Turn off record LED
        if hasattr(self, 'record_led'):
            try:
                self.record_led.off()
                self.record_led.cleanup()
            except Exception as e:
                print(f"⚠ LED cleanup error: {e}")

        # Close GNSS
        if hasattr(self, 'gnss') and self.gnss:
            try:
                self.gnss.close()
            except Exception as e:
                print(f"⚠ GNSS cleanup error: {e}")

        # Close RTC
        if hasattr(self, 'rtc') and self.rtc:
            try:
                self.rtc.close()
            except Exception as e:
                print(f"⚠ RTC cleanup error: {e}")
        
        if hasattr(self, 'camera'):
            self.camera.release()
        
        if self.audio_enabled:
            self.audio.terminate()
        
        cv2.destroyAllWindows()
        print("Cleanup completed")


if __name__ == "__main__":
    # Load config from YAML file
    try:
        config_file = Path(__file__).parent.parent / 'config' / 'device_full.yaml'
        yaml_config = load(config_file)
        print(f"✓ Loaded config from {config_file}")
        
        # Map config values from YAML to recorder config structure
        recorder_config = {
            'camera': {
                'device': yaml_config['video']['v4l2_device'],
                'width': int(yaml_config['video']['v4l2_format'].split('x')[0]),
                'height': int(yaml_config['video']['v4l2_format'].split('x')[1]),
                'fps': yaml_config['video']['v4l2_fps'],
                'fourcc': 'MJPG'
            },
            'audio': {
                'enabled': yaml_config['capabilities'].get('audio', False),
                'device': yaml_config.get('audio', {}).get('device'),
                'channels': yaml_config.get('audio', {}).get('channels', 1),
                'rate': yaml_config.get('audio', {}).get('sample_rate', 44100),
                'format': pyaudio.paInt16,  # Fixed format
                'chunk': 1024  # Fixed chunk size
            },
            'storage': {
                'path': yaml_config['paths']['record_root'],
                'segment_seconds': yaml_config['storage']['segment_seconds'],
                'container': yaml_config['storage']['container'],
                'filename_pattern': yaml_config['storage']['filename_pattern']
            },
            'gpio': {
                'record_led': yaml_config['gpio'].get('record_led')
            },
            'capabilities': {
                'gnss': yaml_config['capabilities'].get('gnss', False),
                'rtc': True  # Default to True since it's a core feature
            }
        }
        print("✓ Config mapped successfully")
   
        recorder = VideoRecorder(recorder_config)
        print("Press Enter to start recording...")
        input()
        
        recorder.start_recording()
        print("Recording... Press Enter to stop.")
        input()
        
        recorder.stop_recording()
        recorder.cleanup()
        
    except KeyboardInterrupt:
        print("\nStopping...")
        if 'recorder' in locals():
            recorder.cleanup()
    except Exception as e:
        print(f"Error: {e}")
        if 'recorder' in locals():
            recorder.cleanup()
            recorder.cleanup()