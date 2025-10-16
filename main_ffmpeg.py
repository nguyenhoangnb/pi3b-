#!/usr/bin/env python3
"""
main_ffmpeg.py - Simple entry point for FFmpeg-based recorder
"""
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from firmware.domain.recorder_ffmpeg import FFmpegRecorder, signal_handler
import signal

if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    recorder = FFmpegRecorder()
    
    if recorder.start_recording():
        print("✅ Recording started")
        print("📡 HLS stream: http://localhost:5000/hls/stream.m3u8")
        print("Press Ctrl+C to stop")
        
        # Run Flask server
        try:
            recorder.app.run(host="0.0.0.0", port=5000, debug=False)
        except KeyboardInterrupt:
            print("\n🛑 Stopping...")
        finally:
            recorder.cleanup()
    else:
        print("❌ Failed to start recording")
        sys.exit(1)
