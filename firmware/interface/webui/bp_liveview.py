from __future__ import annotations
from flask import Blueprint, Response, send_from_directory
from pathlib import Path
import time
import subprocess
import threading
import queue

# ============================================================
# CONFIG
# ============================================================

bp = Blueprint("liveview", __name__)
HLS_DIR = Path("/tmp/picam_hls/")
HLS_DIR.mkdir(parents=True, exist_ok=True)

# ============================================================
# MJPEG STREAMER CLASS
# ============================================================

class MjpegStreamer:
    def __init__(self):
        self.process = None
        self.queue = queue.Queue(maxsize=30)
        self.stop_event = threading.Event()
        self.thread = None
        self.lock = threading.Lock()
        self.active_clients = 0  # đếm số client đang xem

    def start(self):
        """Chạy FFmpeg nếu chưa chạy."""
        with self.lock:
            if self.thread and self.thread.is_alive():
                return  # Đã chạy rồi
            print("🚀 Starting MJPEG streamer (FFmpeg)...")
            self.stop_event.clear()
            self.thread = threading.Thread(target=self._run_ffmpeg, daemon=True)
            self.thread.start()

    def _run_ffmpeg(self):
        """Luồng chạy ffmpeg để đọc từ live.m3u8."""
        max_retries = 10
        retry_count = 0
        while retry_count < max_retries and not self.stop_event.is_set():
            cmd = [
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5",
                "-rw_timeout", "30000000",
                "-fflags", "+genpts+discardcorrupt+nobuffer",
                "-i", str(HLS_DIR / "live.m3u8"),
                "-f", "mpjpeg", "-q:v", "7", "-pix_fmt", "yuvj422p",
                "-"
            ]
            try:
                self.process = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, bufsize=0, stderr=subprocess.DEVNULL
                )
                print("🎥 FFmpeg process started (PID %d)" % self.process.pid)
            except Exception as e:
                print(f"❌ Failed to start FFmpeg: {e}")
                retry_count += 1
                time.sleep(2)
                continue

            no_data_count = 0
            while not self.stop_event.is_set():
                chunk = self.process.stdout.read(4096)
                if not chunk:
                    if self.process.poll() is None:
                        no_data_count += 1
                        if no_data_count > 100:  # ~20s không có dữ liệu
                            print("⚠️ No data for 20s, restarting FFmpeg...")
                            break
                        time.sleep(0.2)
                        continue
                    else:
                        print("⚠️ FFmpeg exited unexpectedly, restarting...")
                        break
                no_data_count = 0
                try:
                    self.queue.put_nowait(chunk)
                except queue.Full:
                    self.queue.get_nowait()  # bỏ frame cũ, tránh overflow
                    self.queue.put_nowait(chunk)

            self._terminate_process()
            retry_count += 1
            if retry_count < max_retries and not self.stop_event.is_set():
                print("🔁 Restarting FFmpeg...")
                time.sleep(2)
            else:
                break

        print("🛑 FFmpeg thread stopped")

    def _terminate_process(self):
        """Kết thúc tiến trình FFmpeg."""
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
            finally:
                self.process = None

    def _mjpeg_from_hls(self):
        """Generator cho Flask stream MJPEG."""
        try:
            while not self.stop_event.is_set():
                try:
                    chunk = self.queue.get(timeout=2)
                    yield chunk
                    self.queue.task_done()
                except queue.Empty:
                    continue
        finally:
            self._client_disconnected()

    def _client_connected(self):
        with self.lock:
            self.active_clients += 1
            print(f"👥 Client connected (active: {self.active_clients})")
            self.start()

    def _client_disconnected(self):
        with self.lock:
            if self.active_clients > 0:
                self.active_clients -= 1
            print(f"👋 Client disconnected (active: {self.active_clients})")
            if self.active_clients == 0:
                print("🛑 No active clients → stopping FFmpeg...")
                self.stop()

    def stop(self):
        """Dừng FFmpeg và thread."""
        with self.lock:
            self.stop_event.set()
            if self.process:
                self._terminate_process()
            if self.thread:
                self.thread.join(timeout=3)
            self.thread = None
            print("✅ Streamer stopped cleanly")


# Singleton instance
streamer = MjpegStreamer()

# ============================================================
# HELPERS
# ============================================================

def _wait_for_hls_ready(timeout: float = 5.0) -> bool:
    """Chờ file HLS playlist sẵn sàng."""
    m3u8_file = HLS_DIR / "live.m3u8"
    start = time.time()
    while time.time() - start < timeout:
        if m3u8_file.exists() and m3u8_file.stat().st_size > 0:
            return True
        time.sleep(0.2)
    print("⚠️ HLS playlist not ready")
    return False

# ============================================================
# ROUTES
# ============================================================

@bp.get("/hls/live.m3u8")
def hls_playlist():
    m3u8_file = HLS_DIR / "live.m3u8"
    if not m3u8_file.exists():
        return Response(
            "#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:2\n#EXT-X-MEDIA-SEQUENCE:0\n",
            mimetype="application/vnd.apple.mpegurl"
        )
    resp = send_from_directory(HLS_DIR, "live.m3u8")
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp

@bp.get("/hls/<path:name>")
def hls_segment(name: str):
    resp = send_from_directory(HLS_DIR, name)
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp

@bp.get("/live.mjpg")
def live_mjpeg():
    if not _wait_for_hls_ready():
        return Response("HLS not ready.", status=503)

    streamer._client_connected()
    return Response(
        streamer._mjpeg_from_hls(),
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )
