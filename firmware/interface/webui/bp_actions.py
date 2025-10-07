from __future__ import annotations
from flask import Blueprint, request, redirect
import subprocess
from pathlib import Path
import os
from .helpers import cfg_get, set_recording

bp = Blueprint("actions", __name__)

@bp.post("/action/record")
def action_record():
    """Enhanced recording control with VideoRecorder integration"""
    cmd = request.form.get("cmd","")
    if cmd == "start":
        # Use the enhanced set_recording function that handles VideoRecorder
        set_recording(True)
        # Also try to start systemd service as fallback
        # os.system("sudo systemctl start picam-recorder.service >/dev/null 2>&1")
    elif cmd == "stop":
        # Use the enhanced set_recording function
        set_recording(False)
        # Also stop systemd service
        # os.system("sudo systemctl stop picam-recorder.service >/dev/null 2>&1")
    return redirect("/")

@bp.post("/action/wifi")
def action_wifi():
    # TODO: nối thật bằng systemd-oneshot để ip link set up/down
    return redirect("/")

@bp.post("/action/format")
def action_format():
    if (request.form.get("confirm") or "") != "YES":
        return redirect("/")
    rr = Path(cfg_get("paths.record_root","/media/ssd/picam"))
    for e in rr.glob("*"):
        try:
            if e.is_file(): e.unlink()
        except: pass
    return redirect("/")

@bp.post("/action/reset")
def action_reset():
    Path("/tmp/picam.factory.reset").write_text("1")
    return redirect("/")


@bp.post("/action/rtc")
def action_rtc():
    """
    Xử lý 2 nút trên WebUI:
    - cmd=push : Ghi giờ hệ thống (đã đồng bộ Internet) -> RTC (systohc)
    - cmd=pull : Đọc từ RTC -> Hệ thống (hctosys) khi không có mạng
    Yêu cầu sudoers: admin NOPASSWD: /sbin/hwclock (setup_once.sh đã tạo)
    """
    cmd = (request.form.get("cmd") or "").lower()
    try:
        if cmd == "push":
            subprocess.run(["sudo","/sbin/hwclock","--systohc"], check=True)
        elif cmd == "pull":
            subprocess.run(["sudo","/sbin/hwclock","--hctosys"], check=True)
    except Exception:
        pass
    return redirect("/")
