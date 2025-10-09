from __future__ import annotations
from flask import current_app
from flask import Blueprint, jsonify, send_from_directory, abort
from pathlib import Path
from .helpers import disk_info, cfg_get, get_recording_service_status, leds_status

bp = Blueprint("storage", __name__)

@bp.get("/status")
def status_json():
    rr = Path(cfg_get("paths.record_root","/media/ssd/picam"))
    st = disk_info(rr)
    
    # Get recorder status if available
    recorder_status = {}
    recorder = get_recording_service_status()
    if recorder:
        try:
            # recorder_status = recorder.get_status()
            print(recorder_status)
        except Exception as e:
            recorder_status = {"error": str(e)}
    
    return jsonify(dict(
        ok=True,
        device=(current_app.config.get("PICAM_CFG") or {}).get("device", {}),
        leds=leds_status(),  # Enhanced LED status with recorder integration
        storage=dict(path=str(rr), **st, min_free_gb=float(cfg_get("storage.min_free_gb",10))),
        recording=recorder_status,  # Add recorder status
        wifi_iface=cfg_get("wifi.iface","wlan0"),
        lte_iface=cfg_get("lte.iface","wwan0"),
        gnss_port=cfg_get("gnss.port","/dev/ttyACM0"),
    ))

@bp.route("/download/<path:fname>")
def download_file(fname: str):
    rr = Path(cfg_get("paths.record_root","/media/ssd/picam"))
    p = (rr / fname).resolve()
    if rr not in p.parents and p != rr: abort(403)
    if not p.exists(): abort(404)
    return send_from_directory(rr, p.name, as_attachment=True)
