#!/usr/bin/env python3
"""
recorder_opencv.py - Simple recorder using OpenCV for segmented video recording with overlays
- Uses OpenCV to capture and process frames
- Saves MP4 segments every segment_seconds
- Overlays current system time (and GPS if available)
- Shares frames via internal queue for potential streaming
"""
import os
import sys
import time
import signal
import threading
import queue
import socket
import cv2
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from flask import Flask, jsonify
from flask_cors import CORS
from firmware.hal.usb_manager import USBManager
from firmware.hal.gpio_leds import gpioLed
from firmware.hal.gnss import GNSSModule
from firmware.hal.rtc import rtcModule
from firmware.config.config_loader import load


class OpenCVRecorder:
    """Simple video recorder using OpenCV"""
    
    def __init__(self):
        self.config_file = Path(__file__).parent.parent / 'config' / 'device_full.yaml'
        self.config = load(self.config_file)
        
        # Paths
        self.output_dir = self.config['paths']['record_root']
        Path(self.output_dir).mkdir(parents=True, exist_ok=True)
        
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
        
        # OpenCV components
        self.cap = None
        self.video_writer = None
        self.recording_thread = None
        self.stream_server_thread = None
        self._stop_flag = False
        self.segment_start = None
        
        # Frame queue for sharing (thread-safe)
        self.frame_queue = queue.Queue(maxsize=5)
        
        # Stream port for TCP sharing
        self.stream_port = self.config.get('stream', {}).get('tcp_port', 9000)
        
        # Flask app for health/status
        self.app = Flask(__name__)
        
        # Enable CORS for all routes
        CORS(self.app, resources={r"/*": {"origins": "*"}})
        
        self.setup_flask_routes()
    
    def _storage_monitor_loop(self):
        """Monitor USB storage and update LED accordingly"""
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
        """Setup Flask routes for status and health"""
        
        @self.app.route('/')
        def index():
            return {
                "status": "running" if self.is_running() else "stopped",
                "output_dir": self.output_dir,
                "segment_seconds": self.segment_seconds,
                "stream_port": self.stream_port
            }
        
        @self.app.route('/health')
        def health():
            return {
                "status": "ok",
                "recording": self.is_running(),
                "storage_available": self.usb_manager.is_available(),
                "storage_space_ok": self.usb_manager.has_enough_space(),
                "cap_opened": self.cap.isOpened() if self.cap else False
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
    
    def _recording_loop(self):
        """Main recording loop in thread"""
        video_size = self.config['video']['v4l2_format']  # "640x480"
        video_fps = self.config['video']['v4l2_fps']
        width, height = map(int, video_size.split('x'))
        
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        
        self.segment_start = time.time()
        
        while not self._stop_flag:
            ret, frame = self.cap.read()
            if not ret:
                print("⚠️ Failed to read frame")
                time.sleep(0.1)
                continue
            
            # Get current time for overlay (use RTC if available, else system time)
            if self.rtc_available:
                try:
                    rtc_time = self.rtc.get_time()  # Assume rtcModule has get_time() returning datetime
                    timestamp = rtc_time.strftime("%Y-%m-%d %H:%M:%S")
                except:
                    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            else:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            # Overlay timestamp
            cv2.putText(frame, timestamp, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
            
            # Overlay GPS if available
            gps_text = None
            if self.gnss_available:
                try:
                    gps_data = self.gnss.get_location()
                    if gps_data.get('latitude') and gps_data.get('longitude'):
                        lat = gps_data['latitude']
                        lon = gps_data['longitude']
                        sats = gps_data.get('num_sats', 0)
                        gps_text = f"GPS: {lat:.6f}, {lon:.6f} ({sats} sats)"
                    else:
                        gps_text = "GPS: No Fix"
                except Exception as e:
                    gps_text = f"GPS: Error ({e})"
            
            if gps_text:
                cv2.putText(frame, gps_text, (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
            
            # Share frame to queue if not full (for streaming)
            try:
                if not self.frame_queue.full():
                    self.frame_queue.put_nowait(frame.copy())
            except queue.Full:
                pass  # Drop frame if queue full
            
            # Check if new segment needed
            current_time = time.time()
            if current_time - self.segment_start >= self.segment_seconds:
                # Close current writer
                if self.video_writer:
                    self.video_writer.release()
                    print(f"✅ Segment saved, elapsed: {current_time - self.segment_start:.1f}s")
                
                # Start new segment
                now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"{self.output_dir}/{now_str}_cam0.mp4"
                self.video_writer = cv2.VideoWriter(filename, fourcc, video_fps, (width, height))
                print(f"📹 New segment: {filename}")
                
                self.segment_start = current_time
            
            # Write frame if writer is open
            if self.video_writer:
                self.video_writer.write(frame)
            
            # Sleep to match FPS
            time.sleep(1.0 / video_fps)
        
        # Cleanup on stop
        if self.video_writer:
            self.video_writer.release()
            print("✅ Final segment saved")
        print("🛑 Recording loop stopped")
    
    def _stream_server_loop(self):
        """TCP server loop to share JPEG frames with external clients (e.g., liveview)"""
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(('0.0.0.0', self.stream_port))
        server.listen(1)
        print(f"🔌 Frame stream server listening on tcp://0.0.0.0:{self.stream_port}")
        
        while not self._stop_flag:
            try:
                client, addr = server.accept()
                print(f"📡 Stream client connected from {addr}")
                
                while not self._stop_flag:
                    try:
                        frame = self.frame_queue.get(timeout=1)
                        ret, jpeg = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
                        if ret:
                            # Send length + data
                            client.sendall(len(jpeg).to_bytes(4, 'big') + jpeg.tobytes())
                        self.frame_queue.task_done()
                    except queue.Empty:
                        continue
                    except Exception as e:
                        print(f"⚠️ Stream send error: {e}")
                        break
                
                client.close()
                print(f"📡 Stream client disconnected from {addr}")
            except Exception as e:
                print(f"⚠️ Stream server error: {e}")
                time.sleep(1)
        
        server.close()
        print("🛑 Frame stream server stopped")
    
    def start_recording(self):
        """Start OpenCV recording"""
        
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
        
        # Get device
        try:
            video_dev = self.get_video_device()
        except Exception as e:
            print(f"❌ Device error: {e}")
            return False
        
        # Parse video settings
        video_size = self.config['video']['v4l2_format']  # "640x480"
        video_fps = self.config['video']['v4l2_fps']
        width, height = map(int, video_size.split('x'))
        
        # Open camera
        self.cap = cv2.VideoCapture(video_dev)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self.cap.set(cv2.CAP_PROP_FPS, video_fps)
        
        if not self.cap.isOpened():
            print("❌ Failed to open camera")
            self.cap.release()
            return False
        
        print(f"🎬 Starting OpenCV recording...")
        print(f"   ↳ Video: {video_dev} ({width}x{height} @ {video_fps}fps)")
        print(f"   ↳ Output: {self.output_dir}/*.mp4")
        print(f"   ↳ Segment: {self.segment_seconds}s")
        
        # Start recording thread
        self._stop_flag = False
        self.recording_thread = threading.Thread(target=self._recording_loop, daemon=True)
        self.recording_thread.start()
        
        # Start stream server thread
        self.stream_server_thread = threading.Thread(target=self._stream_server_loop, daemon=True)
        self.stream_server_thread.start()
        
        # Start storage monitoring thread
        self._storage_monitor_thread = threading.Thread(target=self._storage_monitor_loop, daemon=True)
        self._storage_monitor_thread.start()
        
        # Brief wait to check if started
        time.sleep(1)
        if not self.cap.isOpened():
            print("❌ Camera closed unexpectedly")
            return False
        
        self.led_control.on()
        return True
    
    def stop_recording(self):
        """Stop OpenCV recording"""
        if not self.is_running():
            return
        
        print("⏱ Stopping recording...")
        
        # Signal threads to stop
        self._stop_flag = True
        
        # Wait for recording thread
        if self.recording_thread:
            self.recording_thread.join(timeout=5)
        
        # Release resources
        if self.video_writer:
            self.video_writer.release()
        if self.cap:
            self.cap.release()
        
        self.video_writer = None
        self.cap = None
        self.recording_thread = None
        self.segment_start = None
        
        self.led_control.off()
        print("   💡 LED off")
        print("✅ Recording stopped")
    
    def is_running(self):
        """Check if recording is active"""
        return (self.cap is not None and self.cap.isOpened() and not self._stop_flag)
    
    def cleanup(self):
        """Cleanup resources"""
        print("🧹 Cleanup...")
        
        self.stop_recording()
        
        # Clear frame queue
        while not self.frame_queue.empty():
            try:
                self.frame_queue.get_nowait()
            except:
                break
        
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
        recorder = OpenCVRecorder()
        
        # Start recording
        if recorder.start_recording():
            print("📡 Status endpoint: http://localhost:5000/health")
            print("🔌 Frame stream TCP port: 9000 (for bp_liveview.py)")
            
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