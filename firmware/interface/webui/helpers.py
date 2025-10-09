from __future__ import annotations
import subprocess, shutil, time, re, os
from pathlib import Path
from typing import Dict, Any, List
from flask import request, current_app
# import gpiod an toàn: nếu thiếu lib thì gpiod=None (WebUI vẫn chạy)
try:
    import gpiod
except Exception:
    gpiod = None

# Import recorder module
try:
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from firmware.domain.recorder import VideoRecorder
except Exception as e:
    print(f"Warning: Could not import VideoRecorder: {e}")
    VideoRecorder = None
    
APPLE_RE = re.compile(r"(iPhone|iPad|iPod|Macintosh).*Safari", re.I)

HLS_DIR = Path("/tmp/picam_hls")
FLAG_REC = Path("/tmp/picam.recorder.active")

# Global recorder instance
_recorder_instance = None

_gpio_lines = {}  # cache {name: (line, active_low)}

def _gpio_request_line(pin: int):
    # Nếu có gpiod thì request line, nếu không có thì trả None để fallback raspi-gpio
    if gpiod is None:
        return None
    try:
        ch = gpiod.Chip("gpiochip0")
        ln = ch.get_line(int(pin))
        ln.request(consumer="picam-led", type=gpiod.LINE_REQ_DIR_OUT, default_vals=[0])
        return ln
    except Exception:
        return None

def _gpio_set_named(name: str, on: bool):
    """
    name: 'record' | 'wifi' | 'lte' | 'gps' | 'factory'
    Đọc pin & active_low từ config, xuất mức tương ứng.
    """
    pin = cfg_get(f"gpio.{name}_led", None)
    if pin is None:
        return  # không cấu hình → bỏ qua

    active_low = bool(cfg_get(f"gpio.{name}_led_active_low", False))

    tup = _gpio_lines.get(name)
    ln = tup[0] if tup else None
    if ln is None:
        ln = _gpio_request_line(pin)
        _gpio_lines[name] = (ln, active_low)
    if ln is None:
        # Fallback dùng raspi-gpio nếu không có gpiod/không request được line
        try:
            val_on  = "dl" if active_low else "dh"
            val_off = "dh" if active_low else "dl"
            mode    = val_on if on else val_off
            subprocess.run(["/usr/bin/raspi-gpio","set",str(pin),"op",mode], check=False)
        except Exception:
            pass
        return
        
        
def _fstype(path: Path) -> str:
    try:
        out = subprocess.check_output(
            ["/usr/bin/findmnt","-n","-o","FSTYPE", str(path)],
            stderr=subprocess.DEVNULL, text=True
        ).strip()
        return out
    except Exception:
        return ""

def ensure_dirs(record_root: Path):
    # /media/ssd có thể là autofs khi chưa cắm USB → mkdir có thể ENODEV → bỏ qua
    try:
        record_root.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    try:
        HLS_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

def cfg_get(path: str, default=None):
    cfg = current_app.config.get("PICAM_CFG", {})
    cur = cfg
    for key in path.split("."):
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur

def run(cmd: List[str]) -> str:
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True, timeout=3)
        return out.strip()
    except Exception:
        return ""

def iface_has_ip(iface: str) -> bool:
    return "inet " in (run(["/usr/sbin/ip","-4","addr","show","dev",iface]) or run(["/sbin/ip","-4","addr","show","dev",iface]))

def iface_is_up(iface: str) -> bool:
    out = run(["/usr/sbin/ip","link","show","dev",iface]) or run(["/sbin/ip","link","show","dev",iface])
    return "state UP" in out or "UP" in out.split()

def lte_iface_present(iface: str) -> bool:
    return bool(run(["/usr/sbin/ip","link","show","dev",iface]) or run(["/sbin/ip","link","show","dev",iface]))

def disk_info(p: Path) -> Dict[str, Any]:
    gb = 1024**3
    try:
        total, used, free = shutil.disk_usage(str(p))
        return dict(total_gb=round(total/gb,1), used_gb=round(used/gb,1), free_gb=round(free/gb,1))
    except Exception:
        # khi autofs chưa mount thật → trả 0 thay vì 500
        return dict(total_gb=0.0, used_gb=0.0, free_gb=0.0)

def list_media(p: Path) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    try:
        if p.exists():
            for ext in ("*.mkv","*.mp4","*.ts"):
                for entry in p.glob(ext):
                    try:
                        items.append(dict(name=entry.name, size_mb=entry.stat().st_size/1024/1024))
                    except Exception:
                        pass
    except Exception:
        pass
    # 200 file mới nhất
    return list(sorted(items, key=lambda x: x["name"]))[-200:][::-1]

def get_recording_service_status():
    """Get detailed recording service status"""
    try:
        # Get service status
        result = subprocess.run([
            "systemctl", "show", "picam-recorder.service",
            "--property=ActiveState,SubState,MainPID,ExecMainStartTimestamp"
        ], capture_output=True, text=True, timeout=5)
        
        if result.returncode == 0:
            status = {}
            for line in result.stdout.strip().split('\n'):
                if '=' in line:
                    key, value = line.split('=', 1)
                    status[key] = value
            return status
        
    except Exception as e:
        print(f"⚠ Error getting service status: {e}")
    
    return {}

def rec_is_active() -> bool:
    """Check if recording is active by checking systemd service status"""
    try:
        # Check if the recording service is active
        result = subprocess.run([
            "systemctl", "is-active", "picam-recorder.service"
        ], capture_output=True, text=True, timeout=5)
        
        # Service is active if systemctl returns "active"
        is_service_active = result.stdout.strip() == "active"
        
        if is_service_active:
            return True
            
        # Fallback: check flag file
        return FLAG_REC.exists()
        
    except Exception as e:
        print(f"⚠ Error checking recording status: {e}")
        # Final fallback: check flag file
        return FLAG_REC.exists()

def set_recording(active: bool):
    """Start/stop recording using systemd service"""
    try:
        if active:
            # Start the recording service
            result = subprocess.run([
                "sudo", "systemctl", "start", "picam-recorder.service"
            ], capture_output=True, text=True, timeout=10)
            
            if result.returncode == 0:
                print("✓ Recording service started")
                # Set LED
                _gpio_set_named("record", True)
            else:
                print(f"⚠ Failed to start recording service: {result.stderr}")
                _set_recording_fallback(active)
        else:
            # Stop the recording service
            result = subprocess.run([
                "sudo", "systemctl", "stop", "picam-recorder.service"
            ], capture_output=True, text=True, timeout=10)
            
            if result.returncode == 0:
                print("✓ Recording service stopped")
                # Turn off LED
                _gpio_set_named("record", False)
            else:
                print(f"⚠ Failed to stop recording service: {result.stderr}")
                _set_recording_fallback(active)
                
    except Exception as e:
        print(f"⚠ Error controlling recording service: {e}")
        # Fallback to flag file method
        _set_recording_fallback(active)

def _set_recording_fallback(active: bool):
    """Fallback recording control using flag file"""
    # Bật/tắt LED ngay lập tức (ghi thực tế do service đảm nhiệm)
    try:
        _gpio_set_named("record", bool(active))
    except Exception:
        pass

    if active:
        try: FLAG_REC.write_text(str(int(time.time())))
        except Exception: pass
    else:
        try: FLAG_REC.unlink()
        except FileNotFoundError: pass
        except Exception: pass

def gps_device_present() -> bool:
    try:
        return Path(cfg_get("gnss.port","/dev/ttyACM0")).exists()
    except Exception:
        return False

def leds_status() -> Dict[str,str]:
    wifi = cfg_get("wifi.iface","wlan0")
    lte  = cfg_get("lte.iface","wwan0")
    rec = "on" if rec_is_active() else "off"
    try:
        wifi_state = "on" if iface_has_ip(wifi) else ("blink" if iface_is_up(wifi) else "off")
    except Exception:
        wifi_state = "off"
    try:
        lte_pres   = lte_iface_present(lte)
        lte_state  = "on" if (lte_pres and iface_has_ip(lte)) else ("blink" if lte_pres else "off")
    except Exception:
        lte_state = "off"
    gps_state  = "on" if gps_device_present() else "off"
    return dict(record=rec, wifi=wifi_state, lte=lte_state, gps=gps_state, factory="off")

def client_prefers_hls() -> bool:
    force = (request.args.get("force","") or "").lower()
    if force == "hls": return True
    if force == "mjpeg": return False
    ua = request.headers.get("User-Agent","")
    return bool(APPLE_RE.search(ua))


# --- Thời gian hệ thống & RTC (hiển thị lên WebUI) ---
from datetime import datetime, timezone
try:
    import zoneinfo  # Python 3.9+
except Exception:
    zoneinfo = None

def time_info() -> Dict[str, Any]:
    """
    Trả về dict thông tin thời gian để hiển thị:
    - tz: múi giờ cấu hình (Asia/Ho_Chi_Minh)
    - sys_local: thời gian hệ thống theo múi giờ VN
    - sys_utc: thời gian hệ thống UTC
    - rtc: chuỗi hwclock -r (UTC) nếu có
    """
    tzname = "Asia/Ho_Chi_Minh"
    try:
        tz = zoneinfo.ZoneInfo(tzname) if zoneinfo else None
    except Exception:
        tz = None
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    if tz:
        now_local = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
    else:
        now_local = run(["/usr/bin/date","+%Y-%m-%d %H:%M:%S"]) or ""
    rtc_str = run(["/sbin/hwclock","-r"]) or run(["/usr/sbin/hwclock","-r"])
    return dict(tz=tzname, sys_local=now_local, sys_utc=now_utc, rtc=rtc_str.strip())


# --- Thông tin phần cứng: RTC / Camera / Lưu trữ ---
def _readfile(p: str) -> str:
    try:
        return Path(p).read_text(encoding="utf-8", errors="ignore").strip()
    except Exception:
        return ""

def hw_inventory() -> Dict[str, Any]:
    """
    Trả về dict mô tả phần cứng đang kết nối để hiển thị lên WebUI.
    - rtc: model/bus/addr lấy từ dmesg hoặc /sys/class/rtc/rtc0
    - camera: tên từ /sys/class/video4linux/video0/name (nếu có)
    - storage: thông tin ổ gắn tại /media/ssd (FSTYPE, LABEL, MODEL, SIZE)
    """
    info: Dict[str, Any] = {}

    # RTC
    rtc_name = _readfile("/sys/class/rtc/rtc0/name") or ""
    # Dòng dmesg thường có dạng: "rtc-ds1307 1-0068: registered as rtc0"
    dmesg_line = ""
    try:
        dmesg_out = run(["/bin/dmesg"]) or ""
        for ln in dmesg_out.splitlines():
            if "rtc-" in ln and "registered as rtc0" in ln:
                dmesg_line = ln
        # ví dụ tách "rtc-ds1307 1-0068" -> driver=ds1307, bus=1, addr=0x68
        driver = ""
        bus = ""
        addr = ""
        import re as _re
        m = _re.search(r"rtc-([a-z0-9]+)\s+(\d+)-00([0-9a-f]{2})", dmesg_line, flags=_re.I)
        if m:
            driver = m.group(1).lower()
            bus = m.group(2)
            addr = "0x" + m.group(3)
    except Exception:
        driver = bus = addr = ""
    info["rtc"] = {
        "kernel_name": rtc_name,     # tên kernel (vd: ds1307 / ds3231 / pcf8523)
        "driver": driver or rtc_name,
        "i2c_bus": bus,
        "i2c_addr": addr
    }

    # Camera
    cam = {"dev": "/dev/video0", "name": ""}
    cam["name"] = _readfile("/sys/class/video4linux/video0/name") or ""
    if not cam["name"]:
        # fallback nhẹ: v4l2-ctl --all (nếu có)
        alltxt = run(["/usr/bin/v4l2-ctl","--all"])
        if alltxt:
            for ln in alltxt.splitlines():
                if "Card type" in ln or "Driver name" in ln:
                    cam["name"] = (cam["name"] + " " + ln.split(":",1)[-1].strip()).strip()
    info["camera"] = cam

    # Storage @ /media/ssd
    stor = {"mount": "/media/ssd", "label": "", "fstype": "", "model": "", "size": ""}
    try:
        import json as _json, subprocess
        ls = subprocess.check_output(["/usr/bin/lsblk","-J","-o","NAME,LABEL,FSTYPE,SIZE,MODEL,MOUNTPOINTS"], text=True)
        jo = _json.loads(ls)
        def walk(nodes):
            for nd in nodes:
                mps = (nd.get("mountpoints") or nd.get("mountpoint") or []) or []
                if "/media/ssd" in mps:
                    return nd
                if "children" in nd and nd["children"]:
                    r = walk(nd["children"])
                    if r: return r
            return None
        nd = walk(jo.get("blockdevices",[]))
        if nd:
            stor["label"] = nd.get("label") or ""
            stor["fstype"] = nd.get("fstype") or ""
            stor["model"] = nd.get("model") or ""
            stor["size"]  = nd.get("size") or ""
    except Exception:
        pass
    info["storage"] = stor

    return info
