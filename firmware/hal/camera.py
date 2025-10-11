#!/usr/bin/env python3
import subprocess
import threading
import numpy as np
import time
import sys

class FFmpegCamera:
    def __init__(self, device="/dev/video0", width=640, height=480, fps=25, pix_fmt="bgr24", ffmpeg_bin="ffmpeg"):
        self.device = device
        self.width = width
        self.height = height
        self.fps = fps
        self.pix_fmt = pix_fmt
        self.ffmpeg_bin = ffmpeg_bin
        self.proc = None
        self._stop = False
        self._stderr_thread = None
        self.frame_size = self.width * self.height * 3  # BGR24

    def _build_cmd(self):
        return [
            self.ffmpeg_bin, "-hide_banner", "-loglevel", "warning",
            "-f", "v4l2", "-framerate", str(self.fps),
            "-video_size", f"{self.width}x{self.height}", "-i", self.device,
            "-pix_fmt", self.pix_fmt, "-vcodec", "rawvideo", "-f", "rawvideo", "-"
        ]

    def _drain_stderr(self, stream):
        try:
            for line in iter(stream.readline, b''):
                if not line:
                    break
                print("[ffmpeg]", line.decode(errors='ignore').strip(), file=sys.stderr)
        except Exception:
            pass

    def start(self):
        if self.proc:
            return
        cmd = self._build_cmd()
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=10**7)
        self._stderr_thread = threading.Thread(target=self._drain_stderr, args=(self.proc.stderr,), daemon=True)
        self._stderr_thread.start()
        self._stop = False

    def read_frame(self, timeout=None):
        if not self.proc:
            return None
        buf = b''
        start = time.time()
        while len(buf) < self.frame_size:
            chunk = self.proc.stdout.read(self.frame_size - len(buf))
            if not chunk:
                return None
            buf += chunk
            if timeout is not None and (time.time() - start) > timeout:
                return None
        arr = np.frombuffer(buf, dtype=np.uint8).reshape((self.height, self.width, 3))
        return arr

    def stop(self):
        self._stop = True
        if self.proc:
            try:
                # Close stdout pipe to signal FFmpeg to stop
                self.proc.stdout.close()
            except:
                pass
            try:
                # Give FFmpeg time to cleanup gracefully
                self.proc.terminate()
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                # Force kill if it doesn't terminate
                self.proc.kill()
                try:
                    self.proc.wait(timeout=1)
                except:
                    pass
            except:
                pass
            finally:
                self.proc = None
