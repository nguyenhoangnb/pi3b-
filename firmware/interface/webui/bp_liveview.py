from __future__ import annotations
from flask import Blueprint, Response, send_from_directory
from pathlib import Path
import time
from .helpers import rec_is_active, get_recorder

bp = Blueprint("liveview", __name__)
HLS_DIR = Path("/tmp/picam_hls/")
HLS_DIR.mkdir(parents=True, exist_ok=True)

import subprocess
import threading
import queue

class MjpegStreamer:
    def __init__(self):
        self.process = None
        self.queue = queue.Queue(maxsize=20)  # Buffer nhỏ để tránh memory leak
        self.stop_event = threading.Event()
        self.thread = None
        self.lock = threading.Lock()

    def start(self):
        with self.lock:
            if self.thread and self.thread.is_alive():
                print("⚠️ Streamer đang chạy!")
                return
            self.stop_event.clear()
            self.thread = threading.Thread(target=self._run_ffmpeg, daemon=True)
            self.thread.start()
            print("🚀 MJPEG streamer started in background thread")

    def _run_ffmpeg(self):
        max_retries = 10
        retry_count = 0
        while retry_count < max_retries and not self.stop_event.is_set():
            cmd = [
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-re", "-i", str(HLS_DIR / "live.m3u8"),
                "-f", "mpjpeg", "-q:v", "7", "-pix_fmt", "yuvj422p",
                "-boundary_tag", "frame",
                # Flags chống EOF và reconnect
                "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5",
                "-rw_timeout", "30000000",  # 30s timeout
                "-fflags", "+genpts+discardcorrupt+nobuffer",
                "-"
            ]
            self.process = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=0, stderr=subprocess.DEVNULL)
            
            no_data_count = 0
            while not self.stop_event.is_set():
                chunk = self.process.stdout.read(4096)
                if not chunk:
                    if self.process.poll() is None:
                        no_data_count += 1
                        if no_data_count > 100:  # ~20s no data → restart
                            print("⚠️ No data for 20s, restarting FFmpeg...")
                            break
                        time.sleep(0.2)
                        continue
                    else:
                        print("⚠️ FFmpeg ended, restarting...")
                        break
                no_data_count = 0
                try:
                    self.queue.put_nowait(chunk)
                except queue.Full:
                    print("⚠️ Queue full, dropping chunk")
            
            try:
                self.process.terminate()
                self.process.wait(timeout=3)
            except:
                self.process.kill()
            
            retry_count += 1
            if retry_count < max_retries:
                time.sleep(2)
            else:
                print("❌ Max retries reached for FFmpeg")

    def _mjpeg_from_hls(self):
        """Generator yield chunks từ queue (non-blocking)"""
        while not self.stop_event.is_set():
            try:
                chunk = self.queue.get(timeout=2)  # Timeout để check stop
                self.queue.task_done()
                yield chunk
            except queue.Empty:
                continue

    def stop(self):
        with self.lock:
            self.stop_event.set()
            if self.thread:
                self.thread.join(timeout=3)
            if self.process:
                self.process.kill()

# Singleton instance
streamer = MjpegStreamer()

def _ensure_recorder_running() -> bool:
    """Check if recorder is active (HLS generator)."""
    recorder = get_recorder()
    if recorder is None:
        print("⚠ Recorder not available")
        return False

    if not rec_is_active():
        print("⚠ Recorder not running, cannot stream HLS")
        return False

    return True


def _wait_for_hls_ready(timeout: float = 5.0) -> bool:
    """Wait for HLS playlist to be ready."""
    m3u8_file = HLS_DIR / "live.m3u8"
    start = time.time()
    while time.time() - start < timeout:
        if m3u8_file.exists() and m3u8_file.stat().st_size > 0:
            return True
        time.sleep(0.2)
    print("⚠ HLS playlist not ready")
    return False


@bp.get("/hls/live.m3u8")
def hls_playlist():
    """Serve HLS playlist."""
    m3u8_file = HLS_DIR / "live.m3u8"
    if not m3u8_file.exists():
        return Response(
            "#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:2\n#EXT-X-MEDIA-SEQUENCE:0\n",
            mimetype="application/vnd.apple.mpegurl"
        )

    resp = send_from_directory(HLS_DIR, "live.m3u8")
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp


@bp.get("/hls/<path:name>")
def hls_segment(name: str):
    """Serve HLS segment files (.ts)."""
    resp = send_from_directory(HLS_DIR, name)
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp


@bp.get("/live.mjpg")
def live_mjpeg():
    """Serve MJPEG live stream converted from HLS."""
    # if not _ensure_recorder_running():
    #     return Response("Recorder not active. Start recording first.", status=503)

    if not _wait_for_hls_ready():
        return Response("HLS not ready.", status=503)

    # Start streamer nếu chưa chạy
    streamer.start()

    print("📹 Streaming MJPEG from HLS ...")
    gen = streamer._mjpeg_from_hls()
    return Response(gen, mimetype="multipart/x-mixed-replace; boundary=frame")