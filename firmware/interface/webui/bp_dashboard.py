from __future__ import annotations
from flask import current_app
from flask import Blueprint, render_template_string
from pathlib import Path
from .helpers import cfg_get, leds_status, iface_is_up, rec_is_active, disk_info, list_media, client_prefers_hls, time_info, hw_inventory

bp = Blueprint("dashboard", __name__)

_HTML = r"""
<div class="card">
  <h1>PiCam WebUI</h1>
  <div class="kv">
    <div><b>Device ID</b></div><div>{{dev.get('id','')}}</div>
    <div><b>Model</b></div><div>{{dev.get('model','')}}</div>
    <div><b>HW Rev</b></div><div>{{dev.get('hw_rev','')}}</div>
    <div><b>FW Version</b></div><div>{{dev.get('fw_version','')}}</div>
  </div>
  <div style="margin:.8rem 0 .3rem"><b>LEDs</b></div>
  <div class="leds">
    {% for k in ['record','wifi','lte','gps','factory'] %}
      <div class="led {{ 'on' if leds[k]=='on' else ('blink' if leds[k]=='blink' else '') }}"><span class="dot"></span> {{k|capitalize}}</div>
    {% endfor %}
  </div>
  <div class="row" style="margin-top:.8rem">
    <form method="post" action="/action/record" class="row">
      {% if recording %}<button name="cmd" value="stop" class="danger">⏹ Stop Recording</button>
      {% else %}<button name="cmd" value="start">⏺ Start Recording</button>{% endif %}
    </form>
    <form method="post" action="/action/wifi" class="row">
      {% if wifi_up %}<button name="cmd" value="off" class="danger">Wi-Fi OFF</button>
      {% else %}<button name="cmd" value="on">Wi-Fi ON</button>{% endif %}
    </form>
    <div class="links small">
      <a href="/status">/status (JSON)</a>
      <a href="/?force=hls">Force HLS</a>
      <a href="/?force=mjpeg">Force MJPEG</a>
    </div>
  </div>
</div>


<div class="card">
  <h2>Hardware</h2>
  <div class="kv">
    <div><b>RTC</b></div>
    <div>Driver: {{hw.rtc.driver or '-'}}, Bus: {{hw.rtc.i2c_bus or '-'}}, Addr: {{hw.rtc.i2c_addr or '-'}}<br/>
        Kernel name: <code>{{hw.rtc.kernel_name or '-'}}</code></div>
    <div><b>Camera</b></div><div>Dev: {{hw.camera.dev}}, Name: {{hw.camera.name or '-'}}</div>
    <div><b>Storage</b></div><div>Mount: {{hw.storage.mount}}, FS: {{hw.storage.fstype or '-'}}, Label: {{hw.storage.label or '-'}}, Model: {{hw.storage.model or '-'}}, Size: {{hw.storage.size or '-'}}</div>
  </div>
</div>


<div class="card">
  <h2>Live view</h2>
  {% if prefer_hls %}
    <video controls autoplay playsinline muted>
      <source src="/hls/live.m3u8" type="application/vnd.apple.mpegurl">
    </video>
    <small>HLS (iOS/macOS). Nếu chậm, thử “Force MJPEG”.</small>
  {% else %}
    <img src="/live.mjpg" alt="MJPEG preview"/>
    <small>MJPEG (~15fps; PC/Android). Nếu đang ghi, tap từ HLS; không đụng camera lần 2.</small>
  {% endif %}
</div>


<div class="card">
  <h2>Clock</h2>
  <div class="kv">
    <div><b>Timezone</b></div><div>{{clock.tz}}</div>
    <div><b>System (Local)</b></div><div>{{clock.sys_local}}</div>
    <div><b>System (UTC)</b></div><div>{{clock.sys_utc}}</div>
    <div><b>RTC</b></div><div><code>{{clock.rtc}}</code></div>
  </div>
  <div class="row" style="margin-top:.6rem">
    <form method="post" action="/action/rtc" class="row">
      <button name="cmd" value="push">Sync Internet→RTC (Sys→RTC)</button>
      <button name="cmd" value="pull">Load RTC→System</button>
    </form>
    <div class="small muted">RTC lưu UTC; WebUI hiển thị GMT+7.</div>
  </div>
</div>


<div class="card">
  <h2>Playback & Storage</h2>
  <div class="kv">
    <div><b>Mount</b></div><div>{{storage.mount}}</div>
    <div><b>Path</b></div><div>{{storage.path}}</div>
    <div><b>Total</b></div><div>{{storage.total_gb}} GB</div>
    <div><b>Used</b></div><div>{{storage.used_gb}} GB</div>
    <div><b>Free</b></div><div>{{storage.free_gb}} GB</div>
    <div><b>Min Free</b></div><div>{{storage.min_free_gb}} GB</div>
  </div>
  <div class="row" style="margin:.6rem 0">
    <form method="post" action="/action/format" onsubmit="return confirm('Xoá TẤT CẢ file video trong thư mục ghi hình?');">
      <input type="hidden" name="confirm" value="YES">
      <button class="danger">Format (xoá file)</button>
    </form>
    <form method="post" action="/action/reset" onsubmit="return confirm('Reset factory?');">
      <button class="danger">Reset Factory</button>
    </form>
  </div>
  {% if files %}
  <table>
    <thead><tr><th>File</th><th class="right">Size (MB)</th><th class="right">Actions</th></tr></thead>
    <tbody>
      {% for f in files %}
      <tr>
        <td>{{f.name}}</td>
        <td class="right">{{"%.1f"|format(f.size_mb)}}</td>
        <td class="right"><a href="/download/{{f.name}}">Download</a></td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
  {% else %}<p class="muted">Chưa có file nào.</p>{% endif %}
</div>
"""

# trang tổng hợp (inline CSS)
_FRAME = r"""
<!doctype html><html><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>PiCam WebUI</title>
<style>
:root{--bg:#fff;--fg:#111;--muted:#666;--card:#fafafa;--bd:#e5e7eb;--ok:#16a34a;--warn:#f59e0b;--err:#dc2626;--off:#9ca3af}
*{box-sizing:border-box}body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;margin:20px;color:var(--fg);background:var(--bg)}
.wrap{max-width:980px;margin:auto}.card{background:var(--card);border:1px solid var(--bd);border-radius:14px;padding:16px;margin-bottom:14px;box-shadow:0 1px 8px rgba(0,0,0,.04)}
h1{margin:.2rem 0 1rem;font-size:1.6rem}h2{margin:.4rem 0 .8rem;font-size:1.1rem}
.kv{display:grid;grid-template-columns:160px 1fr;gap:.4rem 1rem}
.leds{display:flex;gap:10px;flex-wrap:wrap}.led{display:flex;align-items:center;gap:6px;padding:6px 10px;border-radius:9999px;border:1px solid var(--bd);background:#fff}
.dot{width:10px;height:10px;border-radius:9999px;background:var(--off)}.on .dot{background:var(--ok)}.blink .dot{animation:bl 1s linear infinite;background:var(--warn)}
@keyframes bl{0%{opacity:1}50%{opacity:.15}100%{opacity:1}}.muted{color:var(--muted)}
.row{display:flex;gap:10px;flex-wrap:wrap}button{border:1px solid var(--bd);background:#fff;padding:8px 12px;border-radius:10px;cursor:pointer}button:hover{background:#f3f4f6}
table{width:100%;border-collapse:collapse}th,td{padding:8px;border-bottom:1px solid var(--bd);text-align:left}.right{text-align:right}
.danger{color:#fff;background:var(--err);border-color:var(--err)}.danger:hover{filter:brightness(.95)}.small{font-size:.9rem}code{background:#f6f8fa;padding:2px 6px;border-radius:6px}
video,img{width:100%;max-height:62vh;background:#000;border-radius:12px}
</style></head><body><div class="wrap">{{body|safe}}</div></body></html>
"""

@bp.get("/")
def index():
    cfg = current_app.config["PICAM_CFG"]
    dev = (cfg.get("device") or {})
    record_root = Path(((cfg.get("paths") or {}).get("record_root") or "/media/ssd/picam"))
    st = disk_info(record_root)
    files = list_media(record_root)
    body = render_template_string(_HTML,
        dev=dev, leds=leds_status(), recording=rec_is_active(), wifi_up=iface_is_up(cfg_get("wifi.iface","wlan0")),
        prefer_hls=client_prefers_hls(),
        storage=dict(path=str(record_root), mount=record_root.anchor or "/", min_free_gb=float(cfg_get("storage.min_free_gb",10)), **st),
        files=files,
        clock=time_info(),
        hw=hw_inventory()
    )
    return render_template_string(_FRAME, body=body)
