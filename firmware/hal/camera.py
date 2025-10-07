#!/usr/bin/env python3
import subprocess
import threading
import numpy as np
import time
import sys
import cv2
import os

USB_PATH = "/media/hoang/UBUNTU 22_0"  # chỉnh lại đường dẫn USB của bạn
SAVE_INTERVAL = 1 * 60  # 10 phút
FPS = 25

class FFmpegCamera:
    def __init__(self, device="/dev/video0", width=640, height=480, fps=FPS, pix_fmt="bgr24", ffmpeg_bin="ffmpeg", extra_input_args=None, log_level="warning"):
        self.device = device
        self.width = int(width)
        self.height = int(height)
        self.fps = int(fps)
        self.pix_fmt = pix_fmt
        self.ffmpeg_bin = ffmpeg_bin
        self.extra_input_args = extra_input_args or []
        self.log_level = log_level
        self.proc = None
        self._stop = False
        self._stderr_thread = None
        self.frame_size = self.width * self.height * (3 if "rgb" in self.pix_fmt or "bgr" in self.pix_fmt else 1)

    def _build_cmd(self, out_pipe=True):
        input_args = ["-f", "v4l2", "-framerate", str(self.fps), "-video_size", f"{self.width}x{self.height}", "-i", self.device]
        if self.extra_input_args:
            input_args = self.extra_input_args + input_args
        out_args = ["-pix_fmt", self.pix_fmt, "-vcodec", "rawvideo", "-f", "rawvideo"]
        if out_pipe:
            out_args += ["-"]
        else:
            out_args += ["-y", "out.raw"]
        cmd = [self.ffmpeg_bin, "-hide_banner", "-loglevel", self.log_level] + input_args + out_args
        return cmd

    def _drain_stderr(self, stream):
        try:
            for line in iter(stream.readline, b''):
                if not line:
                    break
                try:
                    s = line.decode('utf-8', errors='ignore').strip()
                except:
                    s = str(line)
                print("[ffmpeg]", s, file=sys.stderr)
        except Exception:
            pass

    def start(self):
        if self.proc:
            return True
        cmd = self._build_cmd(out_pipe=True)
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=10**7)
        self._stderr_thread = threading.Thread(target=self._drain_stderr, args=(self.proc.stderr,), daemon=True)
        self._stderr_thread.start()
        self._stop = False
        return True

    def read_frame(self, timeout=None):
        if not self.proc or self.proc.stdout is None:
            raise RuntimeError("FFmpeg process is not started")
        to_read = self.frame_size
        buf = b''
        start = time.time()
        while len(buf) < to_read:
            chunk = self.proc.stdout.read(to_read - len(buf))
            if not chunk:
                return None
            buf += chunk
            if timeout is not None and (time.time() - start) > timeout:
                return None
        if "bgr24" == self.pix_fmt or "rgb24" == self.pix_fmt:
            arr = np.frombuffer(buf, dtype=np.uint8)
            if arr.size != to_read:
                return None
            arr = arr.reshape((self.height, self.width, 3))
            if self.pix_fmt == "rgb24":
                arr = arr[..., ::-1]
            return arr
        else:
            raise NotImplementedError(f"pix_fmt {self.pix_fmt} not supported for direct read")

    def frames(self):
        if not self.proc:
            self.start()
        while not self._stop:
            frame = self.read_frame()
            if frame is None:
                break
            yield frame
        self.stop()

    def stop(self):
        self._stop = True
        if self.proc:
            try:
                self.proc.kill()
            except Exception:
                pass
            try:
                self.proc.wait(timeout=1)
            except Exception:
                pass
            self.proc = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.stop()
from usb_manager import USBManager

def record_loop():
    usb = USBManager("/media/hoang/UBUNTU 22_0", min_free_percent=10, min_free_gb=1.0, camera_id=1)
    cam = FFmpegCamera(device="/dev/video0", width=640, height=480, fps=FPS)
    cam.start()

    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    out = None
    start_time = time.time()

    while True:
        if not usb.is_available():
            if out:
                out.release()
                out = None
            usb.wait_until_available()
            start_time = time.time()

        if not usb.has_enough_space():
            print("⏸️ Dừng ghi tạm thời do dung lượng không đủ.")
            time.sleep(5)
            continue

        frame = cam.read_frame()
        if frame is None:
            print("❌ Không đọc được khung hình.")
            break

        if out is None or (time.time() - start_time) >= SAVE_INTERVAL:
            if out:
                out.release()
            filename = usb.get_new_filename()
            out = cv2.VideoWriter(filename, fourcc, FPS, (cam.width, cam.height))
            print(f"💾 Ghi video mới: {filename}")
            start_time = time.time()

        out.write(frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    cam.stop()
    if out:
        out.release()
    cv2.destroyAllWindows()



if __name__ == "__main__":
    record_loop()
