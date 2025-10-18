from __future__ import annotations
from flask import Blueprint, current_app, render_template_string, send_from_directory, abort, request
from pathlib import Path
from .helpers import cfg_get, leds_status, iface_is_up, rec_is_active, disk_info, list_media, time_info, hw_inventory
import re

bp = Blueprint("dashboard", __name__)

# -----------------------------------------------------------
# HLS PATH CONFIG
# -----------------------------------------------------------
HLS_DIR = Path("/tmp/picam_hls")

# -----------------------------------------------------------
# HTML BODY (dashboard)
# -----------------------------------------------------------
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
      <div class="led {{ 'on' if leds[k]=='on' else ('blink' if leds[k]=='blink' else '') }}">
        <span class="dot"></span> {{k|capitalize}}
      </div>
    {% endfor %}
  </div>

  <div class="row" style="margin-top:.8rem">
    <form method="post" action="/action/record" class="row">
      {% if recording %}
        <button name="cmd" value="stop" class="danger">⏹ Stop Recording</button>
      {% else %}
        <button name="cmd" value="start">⏺ Start Recording</button>
      {% endif %}
    </form>
    <form method="post" action="/action/wifi" class="row">
      {% if wifi_up %}
        <button name="cmd" value="off" class="danger">Wi-Fi OFF</button>
      {% else %}
        <button name="cmd" value="on">Wi-Fi ON</button>
      {% endif %}
    </form>
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
  <h2>Camera View</h2>
  <div class="tab-container">
    <div class="tab-buttons">
      <button onclick="switchTab('live')" class="tab-btn active" id="liveBtn">Live Stream</button>
      <button onclick="switchTab('recorded')" class="tab-btn" id="recordedBtn">Recorded Videos</button>
    </div>

    <div id="liveView" class="tab-content active">
      <video id="videoStream" controls autoplay muted
        style="width:100%; aspect-ratio: 4/3; max-height:70vh; background:#000; border-radius:8px;">
      </video>
      <small>HLS stream (~{{video_fps}}fps). Real-time từ Flask route <code>/hls/stream.m3u8</code>.</small>
    </div>

    <div id="recordedView" class="tab-content">
      <div id="playerContainer">
        <video id="videoPlayer" controls style="width:100%; height:480px; background:#000;">
          <p>Your browser doesn't support HTML5 video.</p>
        </video>
      </div>
      {% if files %}
      <div class="video-list">
        {% for f in files %}
          {% if f.name.endswith('.mp4') %}
          <div class="video-item" onclick="playVideo('/download/{{f.name}}')">
            <div class="video-info">
              <div class="video-name">{{f.name}}</div>
              <div class="video-size">{{"%.1f"|format(f.size_mb)}} MB</div>
            </div>
          </div>
          {% endif %}
        {% endfor %}
      </div>
      {% else %}
        <p class="muted">Không có video nào.</p>
      {% endif %}
    </div>
  </div>
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
  {% else %}
    <p class="muted">Chưa có file nào.</p>
  {% endif %}
</div>
"""

# -----------------------------------------------------------
# FRAME + SCRIPT
# -----------------------------------------------------------
_FRAME = r"""
<!doctype html>
<html><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>PiCam WebUI</title>
<style>
{{style|safe}}
</style>
<script src="/static/hls.min.js"></script>
<script>
document.addEventListener('DOMContentLoaded', function() {
  const video = document.getElementById('videoStream');
  const hlsUrl = '/hls/stream.m3u8';

  if (Hls.isSupported()) {
      const hls = new Hls({ maxBufferLength: 4, maxMaxBufferLength: 10, lowLatencyMode: true });
      hls.loadSource(hlsUrl);
      hls.attachMedia(video);
      hls.on(Hls.Events.MANIFEST_PARSED, () => video.play().catch(e => console.log('Autoplay blocked')));
      hls.on(Hls.Events.ERROR, (ev, data) => console.warn('HLS error', data));
  } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
      video.src = hlsUrl;
      video.addEventListener('loadedmetadata', () => video.play());
  }
});

function switchTab(tab) {
  document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
  document.getElementById(tab + 'Btn').classList.add('active');
  document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
  document.getElementById(tab + 'View').classList.add('active');
  if (tab === 'live') { const vp = document.getElementById('videoPlayer'); if (vp) vp.pause(); }
}
function playVideo(url) { const vp = document.getElementById('videoPlayer'); vp.src = url; vp.play(); }
</script>
</head>
<body><div class="wrap">{{body|safe}}</div></body>
</html>
"""

# -----------------------------------------------------------
# MAIN PAGE
# -----------------------------------------------------------
@bp.get("/")
def index():
    cfg = current_app.config["PICAM_CFG"]
    dev = (cfg.get("device") or {})

    record_root = Path(((cfg.get("paths") or {}).get("record_root") or "/media/ssd/picam"))
    try:
        st = disk_info(record_root) if record_root.exists() else {'total_gb':0,'used_gb':0,'free_gb':0,'mount':str(record_root)}
    except OSError as e:
        current_app.logger.error(f"disk_info error: {e}")
        st = {'total_gb':0,'used_gb':0,'free_gb':0,'mount':str(record_root)}

    files = list_media(record_root)
    storage_info = { 'path': str(record_root),
                     'min_free_gb': float(cfg_get("storage.min_free_gb",10)),
                     **st }

    video_fps = cfg_get("video.fps", 15)

    body = render_template_string(_HTML,
        dev=dev, leds=leds_status(), recording=rec_is_active(),
        wifi_up=iface_is_up(cfg_get("wifi.iface","wlan0")),
        video_fps=video_fps, storage=storage_info,
        files=files, clock=time_info(), hw=hw_inventory()
    )



    return render_template_string(_FRAME, body=body)


# -----------------------------------------------------------
# ROUTE PHỤC VỤ FILE HLS
# -----------------------------------------------------------
# @bp.route("/hls/<path:filename>")
# def serve_hls(filename):
#     """Phục vụ file HLS (.m3u8, .ts)"""
#     if re.search(r"(\.\.|%2e%2e|%00)", filename):
#         abort(400)
#     file_path = HLS_DIR / filename
#     if not file_path.exists():
#         abort(404)
#     if filename.endswith(".m3u8"):
#         mime = "application/vnd.apple.mpegurl"
#     elif filename.endswith(".ts"):
#         mime = "video/mp2t"
#     else:
#         mime = "application/octet-stream"
#     return send_from_directory(HLS_DIR, filename, mimetype=mime)
