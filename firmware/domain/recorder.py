#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PiCam recorder: đọc config YAML, ghi video theo segment vào record_root,
OSD thời gian (localtime) góc phải dưới: chữ trắng, nền xám; tự bật/tắt LED và FLAG.
"""

import os, sys, time, signal, subprocess, shutil
from pathlib import Path

# --- GPIO Record LED (giữ quyền pin trong suốt thời gian ghi)
try:
    import gpiod
except Exception:
    gpiod = None
    
    
CFG_FILE = Path("/home/admin/firmware/config/device_full.yaml")
FLAG_REC = Path("/tmp/picam.recorder.active")


class RecordLED:
    """
    Điều khiển LED record bằng libgpiod (GPIO26).
    - Giữ handle (request) cho đến khi off() để tránh bị reset về LOW khi giải phóng.
    """
    def __init__(self, line=26, chip="/dev/gpiochip0"):
        self.line = int(line)
        self.chip = chip
        self.req = None

    def on(self):
        if gpiod is None:
            return
        if self.req is None:
            cfg = { self.line: gpiod.LineSettings(direction=gpiod.LineDirection.OUTPUT) }
            self.req = gpiod.request_lines(self.chip, consumer="picam-rec", config=cfg)
        self.req.set_value(self.line, 1)

    def off(self):
        if gpiod is None:
            return
        try:
            if self.req:
                self.req.set_value(self.line, 0)
                self.req.release()
        finally:
            self.req = None
            
            
def sh(cmd:list[str]) -> str:
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False).stdout

def load_cfg():
    import yaml
    with open(CFG_FILE, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def led_record(on: bool, pin: int = 26, active_low: bool = False):
    """Bật/tắt LED qua gpiod nếu có, nếu không fallback raspi-gpio."""
    try:
        import gpiod  # có thể không cài, nên đặt trong try
    except Exception:
        gpiod = None

    if gpiod is not None:
        try:
            ch = gpiod.Chip("gpiochip0")
            ln = ch.get_line(int(pin))
            ln.request(consumer="picam-led", type=gpiod.LINE_REQ_DIR_OUT, default_vals=[0])
            val = 0 if (on and active_low) or ((not on) and (not active_low)) else 1
            ln.set_value(val)
            return
        except Exception:
            pass

    # fallback
    try:
        val_on  = "dl" if active_low else "dh"
        val_off = "dh" if active_low else "dl"
        mode    = val_on if on else val_off
        subprocess.run(["/usr/bin/raspi-gpio","set",str(pin),"op",mode], check=False)
    except Exception:
        pass

def ensure_min_free(root: Path, min_free_gb: float):
    gb = 1024**3
    try:
        while True:
            total, used, free = shutil.disk_usage(str(root))
            if free/gb >= float(min_free_gb): return
            files = sorted([p for p in root.glob("*.mkv")] + [p for p in root.glob("*.mp4")] + [p for p in root.glob("*.ts")],
                           key=lambda p: p.stat().st_mtime)
            if not files: return
            files[0].unlink(missing_ok=True)
    except Exception:
        pass

def main():
    cfg = load_cfg()

    rec_root = Path(((cfg.get("paths") or {}).get("record_root") or "/media/ssd/picam"))
    rec_root.mkdir(parents=True, exist_ok=True)

    stg = cfg.get("storage") or {}
    min_free_gb     = float(stg.get("min_free_gb", 10))
    segment_seconds = int(stg.get("segment_seconds", 60))
    container       = (stg.get("container") or "mkv").lower()
    pattern         = (stg.get("filename_pattern") or "%Y%m%d-%H%M%S.mkv")
    if not pattern.endswith(("."+container)):
        base = pattern.rsplit(".",1)[0]
        pattern = base + "." + container

    vid  = cfg.get("video") or {}
    dev  = vid.get("v4l2_device") or "/dev/video0"
    size = str(vid.get("v4l2_format") or "640x480")
    fps  = int(vid.get("v4l2_fps") or 25)

    gpio = cfg.get("gpio") or {}
    record_led_pin = int(gpio.get("record_led", 26))
    record_led_active_low = bool(gpio.get("record_led_active_low", False))

    # giữ tối thiểu dung lượng
    ensure_min_free(rec_root, min_free_gb)

    # cờ + LED
    try: FLAG_REC.write_text(str(int(time.time())))
    except Exception: pass
    led_record(True, record_led_pin, record_led_active_low)
    
 



    # OSD thời gian localtime: chữ trắng, nền xám, góc phải dưới
    draw = (
        "drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf:"
        "text=%{localtime\\:%Y-%m-%d %H\\\\\\:%M\\\\\\:%S}:"
        "x=w-tw-16:y=h-th-16:fontcolor=white:box=1:boxcolor=gray@0.6:boxborderw=8"
    )

    out_tmpl = str(rec_root / pattern)

    cmd = [
        "ffmpeg","-hide_banner","-loglevel","error",
        "-f","v4l2","-input_format","yuyv422","-framerate", str(fps),
        "-video_size", size,"-i", dev,
        "-c:v","libx264","-preset","veryfast","-crf","28","-pix_fmt","yuv420p",
        "-vf", draw,
        "-f","segment","-segment_time", str(segment_seconds),
        "-reset_timestamps","1","-strftime","1",
        out_tmpl
    ]
    print("Running:", " ".join(cmd), flush=True)

    p = subprocess.Popen(cmd)

    def cleanup(*_):
        try: p.terminate()
        except: pass
        try: FLAG_REC.unlink()
        except: pass
        led_record(False, record_led_pin, record_led_active_low)
        sys.exit(0)

    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup)

    rc = p.wait()
    cleanup()
    sys.exit(rc)

if __name__ == "__main__":
    main()
