# /home/admin/run_webui.py
import os
from firmware.config.config_loader import load as load_cfg
from firmware.interface.webui import create_app

cfg_path = os.environ.get("PICAM_CONFIG", "firmware/config/device_full.yaml")
cfg = load_cfg(cfg_path)

app = create_app(cfg)
host = (cfg.get("webui", {}) or {}).get("host", "0.0.0.0")
port = int((cfg.get("webui", {}) or {}).get("port", 8080))

if __name__ == "__main__":
    # Simple Flask app - no SocketIO needed for HLS
    app.run(host=host, port=port, debug=False)
