import os, time
from pathlib import Path
from fastapi import FastAPI, Depends, Header, HTTPException
from pydantic import BaseModel

# load YAML config
from firmware.config.config_loader import load as load_cfg
CFG_PATH = os.environ.get("PICAM_CONFIG", "firmware/config/device_full.yaml")
CFG = load_cfg(CFG_PATH)

API_KEY = (CFG.get("api", {}) or {}).get("api_key", "")

app = FastAPI(title="PiCam Mobile API", version="1.0.0")

async def require_api_key(x_api_key: str = Header(default="")):
    if not API_KEY or x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return True

@app.get("/api/v1/status")
async def status(_: bool = Depends(require_api_key)):
    return {
        "ok": True,
        "now": time.strftime("%Y-%m-%d %H:%M:%S"),
        "device": CFG.get("device", {}),
        "capabilities": CFG.get("capabilities", {}),
        "config_path": CFG_PATH,
    }
