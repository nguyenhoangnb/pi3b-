#!/usr/bin/env python3
"""
Optimized Video Recorder for Raspberry Pi 3B+
Includes camera capture, HLS streaming, audio, GPS, LED status, and segment recording
"""

import os, sys, time, threading, signal, cv2, numpy as np, subprocess
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from firmware.hal.camera import FFmpegCamera
from firmware.hal.usb_manager import USBManager
from firmware.hal.gpio_leds import gpioLed
from firmware.hal.rtc import rtcModule
from firmware.config.config_loader import load

# Optional imports
try:
    from firmware.hal.micro import Micro
except Exception:
    class Micro:
        def __init__(self, *a, **kw): pass
        def check_device_available(self): return False
        def record(self, *a, **kw): return None
        def save(self, *a, **kw): pass

try:
    from firmware.hal.gnss import GNSSModule
except Exception:
    GNSSModule = None

class VideoRecorder:
    def __init__(self, config_file=None):
        self.config = self._load_config(config_file)

        self.camera = None
        self.usb_manager = None
        self.record_led = None
        self.micro = None
        self.gnss = None
        self.rtc = None

        self.is_recording = False
        self.current_recorder_process = None
        self.segment_start_time = None
        self.recording_thread = None
        self._stop_recording = False

        self.hls_dir = Path("/tmp/picam_hls")
        self.hls_process = None
        self.hls_enabled = True
        self.hls_lock = threading.Lock()

        self.enable_time_overlay = True
        self.enable_gps_overlay = True
        self.enable_audio = True

        self._initialize_components()

        # Signal handlers only in main thread
        try:
            signal.signal(signal.SIGTERM, self._signal_handler)
            signal.signal(signal.SIGINT, self._signal_handler)
        except ValueError:
            pass

        # Auto-start
        self.start_recording()

    # ---------- CONFIG ----------
    def _load_config(self, config_file=None):
        if not config_file:
            config_file = Path(__file__).parent.parent / 'config' / 'device_full.yaml'
        if config_file.exists():
            try:
                yaml_config = load(config_file)
                return self._convert_yaml_to_recorder_config(yaml_config)
            except: pass
        return self._default_config()

    def _convert_yaml_to_recorder_config(self, yaml_config):
        video = yaml_config.get('video', {})
        w, h = map(int, video.get('v4l2_format', '640x480').split('x'))
        storage = yaml_config.get('storage', {})
        paths = yaml_config.get('paths', {})
        gpio = yaml_config.get('gpio', {})

        return {
            'camera': {'device': video.get('v4l2_device', '/dev/video0'), 'width': w, 'height': h, 'fps': video.get('v4l2_fps', 15)},
            'audio': {'enabled': yaml_config.get('capabilities', {}).get('audio', True),
                      'device': yaml_config.get('audio', {}).get('device', None),
                      'sample_rate': yaml_config.get('audio', {}).get('sample_rate', 16000),
                      'channels': yaml_config.get('audio', {}).get('channels', 1)},
            'usb': {'path': paths.get('record_root', '/media/ssd'), 'min_free_gb': storage.get('min_free_gb',1.0),
                    'min_free_percent': 10},
            'recording': {'segment_duration': 2*60, 'format': 'mp4', 'quality': 80},
            'leds': {'record_led_pin': gpio.get('record_led',26)},
            'overlays': {'font_scale':0.7, 'font_thickness':2, 'text_color':(255,255,255),
                         'bg_color':(0,0,0), 'timestamp_enabled':True, 'gps_enabled': yaml_config.get('capabilities', {}).get('gnss', False)},
            'capabilities': yaml_config.get('capabilities', {}),
            'device': yaml_config.get('device', {})
        }

    def _default_config(self):
        return {
            'camera': {'device':'/dev/video0','width':640,'height':480,'fps':15},
            'audio': {'enabled': True,'device': None,'sample_rate':16000,'channels':1},
            'usb': {'path':'/media/ssd','min_free_gb':1.0,'min_free_percent':10},
            'recording': {'segment_duration': 2*60,'format':'mp4','quality':80},
            'leds': {'record_led_pin':26},
            'overlays': {'font_scale':0.7,'font_thickness':2,'text_color':(255,255,255),
                         'bg_color':(0,0,0),'timestamp_enabled':True,'gps_enabled':False},
            'capabilities': {'video': True,'audio': False,'gnss': False,'lte': False},
            'device': {'id':'PICAM-DEFAULT','model':'PiCam'}
        }

    # ---------- COMPONENTS ----------
    def _initialize_components(self):
        # Camera
        cam_cfg = self.config['camera']
        self.camera = FFmpegCamera(cam_cfg['device'], cam_cfg['width'], cam_cfg['height'], cam_cfg['fps'])
        print("✓ Camera initialized")

        # USB
        usb_cfg = self.config['usb']
        self.usb_manager = USBManager(usb_cfg['path'], usb_cfg['min_free_gb'], usb_cfg['min_free_percent'])
        print("✓ USB manager initialized")

        # LED
        self.record_led = gpioLed(self.config['leds']['record_led_pin'])
        print("✓ Record LED initialized")

        # Microphone
        if self.config['audio']['enabled']:
            self.micro = Micro(device=self.config['audio']['device'], sample_rate=self.config['audio']['sample_rate'])
            self.enable_audio = self.micro.check_device_available()
        else:
            self.micro = None
            self.enable_audio = False

        # GNSS
        if self.config['overlays']['gps_enabled'] and GNSSModule:
            self.gnss = GNSSModule()
        else:
            self.gnss = None
            self.enable_gps_overlay = False

        # RTC
        try: self.rtc = rtcModule()
        except: self.rtc = None

        # HLS
        self._setup_hls_streaming()

    def _setup_hls_streaming(self):
        self.hls_dir.mkdir(parents=True, exist_ok=True)
        for f in self.hls_dir.glob("*"): f.unlink(missing_ok=True)

    # ---------- HLS ----------
    def _start_hls_stream(self):
        if not self.hls_enabled: return False
        with self.hls_lock:
            if self.hls_process: self._stop_hls_stream_internal()
            try:
                w,h,fps = self.config['camera']['width'], self.config['camera']['height'], self.config['camera']['fps']
                cmd = [
                    "ffmpeg","-hide_banner","-loglevel","error",
                    "-f","rawvideo","-pix_fmt","bgr24","-s",f"{w}x{h}","-r",str(fps),"-i","pipe:0",
                    "-c:v","libx264","-preset","ultrafast","-tune","zerolatency",
                    "-g",str(fps*2),"-keyint_min",str(fps),
                    "-hls_time","1","-hls_list_size","2","-hls_flags","delete_segments+omit_endlist+independent_segments",
                    "-f","hls",str(self.hls_dir/"live.m3u8")
                ]
                self.hls_process = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                time.sleep(0.1)
                if self.hls_process.poll() is not None:
                    err=self.hls_process.stderr.read().decode('utf-8',errors='ignore')
                    print(f"❌ FFmpeg HLS failed: {err}")
                    self.hls_process=None; return False
                print("✓ HLS streaming started")
                return True
            except Exception as e: print(f"⚠ HLS start error: {e}"); self.hls_process=None; return False

    def _stop_hls_stream_internal(self):
        if not self.hls_process: return
        try:
            if self.hls_process.stdin: self.hls_process.stdin.close()
            self.hls_process.terminate()
            try: self.hls_process.wait(timeout=3)
            except: self.hls_process.kill(); self.hls_process.wait()
        finally: self.hls_process=None

    def _stop_hls_stream(self):
        with self.hls_lock: self._stop_hls_stream_internal()

    def _write_frame_to_hls(self, frame):
        with self.hls_lock:
            if self.hls_process and self.hls_process.stdin:
                try:
                    self.hls_process.stdin.write(frame.tobytes())
                    self.hls_process.stdin.flush()
                    return True
                except: 
                    print("⚠ HLS write broken, restarting...")
                    self._restart_hls_if_needed()
                    return False
            else:
                self._start_hls_stream()
                return False

    def _restart_hls_if_needed(self):
        with self.hls_lock:
            if self.hls_process and self.hls_process.poll() is not None:
                print("⚠ HLS process died, restarting...")
                self._stop_hls_stream_internal()
                time.sleep(0.5)
                return self._start_hls_stream()
        return True

    # ---------- OVERLAY ----------
    def _get_time_text(self):
        """Get current time text for overlay"""
        try:
            if self.rtc:
                try:
                    dt = self.rtc.read_time()
                except OSError as e:
                    print(f"⚠ RTC busy, fallback to system time: {e}")
                    dt = datetime.now()
            else:
                dt = datetime.now()
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


    def _get_gps_text(self):
        if not self.gnss or not self.enable_gps_overlay: return ""
        try:
            gps=self.gnss.get_location()
            if gps and gps.get('fix_quality',0)>0: return f"GPS:{gps.get('latitude',0):.6f},{gps.get('longitude',0):.6f}"
            return "GPS: No Fix"
        except: return "GPS: Error"

    
    def _add_overlays(self, frame, time_text=None):
        """Add time and GPS overlays to frame"""
        if frame is None:
            return frame

        frame = frame.copy()
        height, width = frame.shape[:2]
        overlay_config = self.config['overlays']

        # Time overlay (top-left)
        if self.enable_time_overlay and overlay_config.get('timestamp_enabled', True):
            if time_text is None:
                time_text = self._get_time_text()

            (text_width, text_height), baseline = cv2.getTextSize(
                time_text,
                cv2.FONT_HERSHEY_SIMPLEX,
                overlay_config['font_scale'],
                overlay_config['font_thickness']
            )

            cv2.rectangle(frame,
                        (10, 10),
                        (20 + text_width, 20 + text_height + baseline),
                        overlay_config['bg_color'], -1)

            cv2.putText(frame, time_text,
                        (15, 15 + text_height),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        overlay_config['font_scale'],
                        overlay_config['text_color'],
                        overlay_config['font_thickness'])

        # GPS overlay (top-right)
        if self.enable_gps_overlay and overlay_config.get('gps_enabled', False):
            gps_text = self._get_gps_text()
            if gps_text:
                (text_width, text_height), baseline = cv2.getTextSize(
                    gps_text,
                    cv2.FONT_HERSHEY_SIMPLEX,
                    overlay_config['font_scale'],
                    overlay_config['font_thickness']
                )
                x_pos = width - text_width - 20

                cv2.rectangle(frame,
                            (x_pos - 5, 10),
                            (width - 10, 20 + text_height + baseline),
                            overlay_config['bg_color'], -1)

                cv2.putText(frame, gps_text,
                            (x_pos, 15 + text_height),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            overlay_config['font_scale'],
                            overlay_config['text_color'],
                            overlay_config['font_thickness'])

        return frame


    # ---------- FFmpeg Recording ----------
    def _create_ffmpeg_recorder_with_audio(self, filename):
        try:
            cam=self.config['camera']
            aud=self.config['audio']
            w,h,fps=cam['width'],cam['height'],cam['fps']
            base_dir = Path("/media/ssd") if Path("/media/ssd").exists() else Path("/home/pi/videos")
            base_dir.mkdir(parents=True,exist_ok=True)
            out_file = base_dir / filename
            use_audio = self.enable_audio and os.path.exists("/dev/snd")
            cmd=["ffmpeg","-hide_banner","-loglevel","error","-f","rawvideo","-pix_fmt","bgr24","-s",f"{w}x{h}","-r",str(fps),"-i","pipe:0"]
            if use_audio:
                audio_device = aud.get('device') or "plughw:1,0"
                cmd.extend(["-f","alsa","-ac",str(aud.get('channels',1)),"-ar",str(aud.get('sample_rate',16000)),"-i",audio_device])
            cmd.extend(["-c:v","h264_v4l2m2m","-b:v","2M","-pix_fmt","yuv420p"])
            if use_audio: cmd.extend(["-c:a","aac","-b:a","128k","-map","0:v:0","-map","1:a:0"])
            else: cmd.append("-an")
            cmd.extend(["-vsync","1","-async","1","-movflags","+faststart",str(out_file)])
            self.current_recorder_process=subprocess.Popen(cmd,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,bufsize=10**7)
            time.sleep(0.3)
            if self.current_recorder_process.poll() is not None:
                err=self.current_recorder_process.stderr.read().decode('utf-8',errors='ignore')
                print(f"❌ FFmpeg failed: {err}")
                self.current_recorder_process=None
                return False
            print(f"✅ Recording started → {out_file}")
            return True
        except Exception as e: print(f"⚠ FFmpeg recorder error: {e}"); return False

    def _should_create_new_segment(self):
        if not self.segment_start_time: return True
        return (time.time()-self.segment_start_time)>=self.config['recording']['segment_duration']

    def _create_new_segment(self):
        if self.current_recorder_process:
            try: self.current_recorder_process.stdin.close(); self.current_recorder_process.wait(timeout=2)
            except: self.current_recorder_process.kill()
            self.current_recorder_process=None
        filename=self.usb_manager.get_new_filename()
        if self._create_ffmpeg_recorder_with_audio(filename):
            self.segment_start_time=time.time()
            return True
        return False

    # ---------- LED ----------
    def _update_led_status(self):
        if not self.record_led: return
        if not self.is_recording: self.record_led.off()
        elif not self.usb_manager or not self.usb_manager.is_available(): self.record_led.blink(0.5)
        else: self.record_led.on()

    # ---------- RECORDING LOOP ----------
    def _recording_loop(self):
        """Main recording loop (optimized RTC reading)"""
        print("🎬 Starting recording loop...")

        hls_restart_counter = 0
        last_rtc_update = 0
        current_time_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

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

                # Update time overlay once per second
                now = time.time()
                if now - last_rtc_update >= 1:
                    try:
                        if self.rtc:
                            # Optional: dùng lock nếu nhiều thread đọc RTC
                            dt = self.rtc.read_time()
                        else:
                            dt = datetime.now()
                        current_time_text = dt.strftime("%Y-%m-%d %H:%M:%S")
                    except OSError as e:
                        print(f"⚠ RTC busy, fallback to system time: {e}")
                        current_time_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    last_rtc_update = now

                # Read frame from camera
                try:
                    frame = self.camera.read_frame(timeout=1.0)
                    if frame is None:
                        continue

                    # Add overlays (use cached time)
                    frame_with_overlays = self._add_overlays(frame, time_text=current_time_text)

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

    def start_recording(self):
        if self.is_recording: return
        self.is_recording=True
        self._stop_recording=False
        self.recording_thread=threading.Thread(target=self._recording_loop,daemon=True)
        self.recording_thread.start()

    def stop_recording(self):
        self._stop_recording=True
        self.is_recording=False
        if self.recording_thread: self.recording_thread.join(timeout=5)
        self._cleanup_recording()
        self._stop_hls_stream()

    def _cleanup_recording(self):
        if self.current_recorder_process:
            try: self.current_recorder_process.stdin.close(); self.current_recorder_process.wait(timeout=2)
            except: self.current_recorder_process.kill()
            self.current_recorder_process=None

    # ---------- SIGNAL ----------
    def _signal_handler(self, sig, frame):
        print(f"⚡ Signal {sig} received, stopping recording...")
        self.stop_recording()
        sys.exit(0)

# ---------- MAIN ----------
if __name__=="__main__":
    rec=VideoRecorder()
    try:
        while True: time.sleep(1)
    except KeyboardInterrupt:
        rec.stop_recording()
