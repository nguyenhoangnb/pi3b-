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
# CSS STYLE (Thêm mới)
# -----------------------------------------------------------
_STYLE = r"""
:root {
  --font-main: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  --bg-page: #f4f7fa;
  --bg-card: #ffffff;
  --text-main: #222;
  --text-muted: #777;
  --border-color: #e5e5e5;
  --led-off: #ddd;
  --led-on-rec: #f44336;
  --led-on-green: #4CAF50;
  --led-on-blue: #007bff;
  --radius: 12px;
  --shadow: 0 4px 12px rgba(0,0,0,0.05);
  --max-width: 900px;
  --color-danger: #d9534f;
  --color-danger-hover: #c9302c;
  --color-primary: #007bff;
  --color-primary-hover: #0056b3;
}
* { box-sizing: border-box; }
body {
  font-family: var(--font-main);
  background: var(--bg-page);
  color: var(--text-main);
  margin: 0;
  padding: 1rem;
}
.wrap {
  max-width: var(--max-width);
  margin: 1rem auto;
  display: grid;
  gap: 1.5rem;
}
.card {
  background: var(--bg-card);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  padding: 1.5rem 2rem;
  border: 1px solid var(--border-color);
}
h1, h2 {
  margin-top: 0;
  margin-bottom: 1rem;
  font-weight: 600;
}
h1 { font-size: 1.8rem; }
h2 { font-size: 1.4rem; border-bottom: 1px solid var(--border-color); padding-bottom: .5rem; }

/* Key-Value Grid */
.kv {
  display: grid;
  grid-template-columns: 140px 1fr;
  gap: .8rem 1rem;
  align-items: center;
}
.kv > div:nth-child(odd) {
  font-weight: 600;
  color: #555;
}
code {
  background: #eee;
  padding: .2em .4em;
  border-radius: 4px;
  font-family: monospace;
}
.muted { color: var(--text-muted); }
.small { font-size: 0.85rem; }

/* Buttons & Rows */
.row {
  display: flex;
  gap: .8rem;
  flex-wrap: wrap;
  align-items: center;
}
button {
  font-family: var(--font-main);
  font-size: .95rem;
  font-weight: 600;
  padding: .6rem 1rem;
  border-radius: 8px;
  border: 1px solid var(--border-color);
  background: #f9f9f9;
  cursor: pointer;
  transition: all 0.2s ease;
}
button:hover {
  border-color: #ccc;
  background: #f0f0f0;
}
button:active {
  transform: scale(0.98);
}
button[name="cmd"][value="start"] {
  background: var(--led-on-rec);
  color: white;
  border-color: var(--led-on-rec);
}
button[name="cmd"][value="start"]:hover { background: #d32f2f; border-color: #d32f2f; }

button.danger, button[name="cmd"][value="stop"] {
  background: var(--color-danger);
  color: white;
  border-color: var(--color-danger);
}
button.danger:hover, button[name="cmd"][value="stop"]:hover {
  background: var(--color-danger-hover);
  border-color: var(--color-danger-hover);
}
button[name="cmd"][value="on"] {
  background: var(--color-primary);
  color: white;
  border-color: var(--color-primary);
}
button[name="cmd"][value="on"]:hover {
  background: var(--color-primary-hover);
  border-color: var(--color-primary-hover);
}

/* LEDs */
.leds {
  display: flex;
  gap: 1.5rem;
  flex-wrap: wrap;
  padding-left: .2rem;
}
.led {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-weight: 500;
  text-transform: capitalize;
}
.led .dot {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: var(--led-off);
  border: 1px solid #ccc;
  transition: background 0.3s;
}
.led.on .dot {
  background: var(--led-on-green);
  border-color: #388E3C;
  box-shadow: 0 0 6px var(--led-on-green);
}
.led:nth-child(1).on .dot { /* Record LED */
  background: var(--led-on-rec);
  border-color: #c0392b;
  box-shadow: 0 0 6px var(--led-on-rec);
}
.led:nth-child(3).on .dot { /* LTE LED */
  background: var(--led-on-blue);
  border-color: #0056b3;
  box-shadow: 0 0 6px var(--led-on-blue);
}
.led.blink .dot {
  animation: blinker 1s linear infinite;
}
@keyframes blinker {
  50% { opacity: 0.2; }
}
.led.blink:nth-child(1) .dot { animation-name: blink-red; }
.led.blink:nth-child(2) .dot { animation-name: blink-green; }
.led.blink:nth-child(3) .dot { animation-name: blink-blue; }

@keyframes blink-red {
  50% { background: var(--led-on-rec); opacity: 0.2; }
}
@keyframes blink-green {
  50% { background: var(--led-on-green); opacity: 0.2; }
}
@keyframes blink-blue {
  50% { background: var(--led-on-blue); opacity: 0.2; }
}


/* Camera View Tabs */
.tab-container { margin-top: 1rem; }
.tab-buttons {
  border-bottom: 2px solid var(--border-color);
  margin-bottom: 1rem;
}
.tab-btn {
  background: none;
  border: none;
  padding: .8rem 1.2rem;
  cursor: pointer;
  font-size: 1rem;
  font-weight: 600;
  color: var(--text-muted);
  border-bottom: 3px solid transparent;
  margin-bottom: -2px;
  transition: all 0.2s ease;
}
.tab-btn:hover { color: var(--text-main); }
.tab-btn.active {
  border-bottom-color: var(--color-primary);
  color: var(--color-primary);
}
.tab-content { display: none; }
.tab-content.active { display: block; }
video {
  width: 100%;
  aspect-ratio: 4/3;
  max-height: 70vh;
  background: #000;
  border-radius: 8px;
}

/* Video List */
.video-list {
  max-height: 480px;
  overflow-y: auto;
  border: 1px solid var(--border-color);
  border-radius: 8px;
  margin-top: 1rem;
}
.video-item {
  padding: .8rem 1rem;
  border-bottom: 1px solid var(--border-color);
  cursor: pointer;
}
.video-item:last-child { border-bottom: none; }
.video-item:hover { background: #f9f9f9; }
.video-info {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.video-name { font-weight: 500; }
.video-size { font-size: 0.9rem; color: var(--text-muted); }

/* Table */
table {
  width: 100%;
  border-collapse: collapse;
  margin-top: 1rem;
  font-size: 0.95rem;
}
th, td {
  padding: .8rem;
  border: 1px solid var(--border-color);
  text-align: left;
}
thead {
  background: var(--bg-page);
  font-weight: 600;
}
tbody tr:nth-child(even) { background: var(--bg-page); }
.right { text-align: right; }
td a {
  color: var(--color-primary);
  text-decoration: none;
  font-weight: 500;
}
td a:hover { text-decoration: underline; }
"""

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

  <div class="row" style="margin-top:1.5rem">
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
        <video id="videoPlayer" controls style="width:100%; background:#000; border-radius:8px;">
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
        <p class="muted" style="margin-top:1rem;">Không có video nào.</p>
      {% endif %}
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
  <h2>Clock</h2>
  <div class="kv">
    <div><b>Timezone</b></div><div>{{clock.tz}}</div>
    <div><b>System (Local)</b></div><div>{{clock.sys_local}}</div>
    <div><b>System (UTC)</b></div><div>{{clock.sys_utc}}</div>
    <div><b>RTC</b></div><div><code>{{clock.rtc}}</code></div>
  </div>
  <div class="row" style="margin-top:1rem">
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
  <div class="row" style="margin:1rem 0 0">
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
    <p class="muted" style="margin-top:1rem;">Chưa có file nào.</p>
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
  
  // Dừng video đang phát ở tab kia
  const liveVideo = document.getElementById('videoStream');
  const recordedVideo = document.getElementById('videoPlayer');
  
  if (tab === 'live') {
    if (recordedVideo) recordedVideo.pause();
    if (liveVideo) liveVideo.play().catch(e => console.log('Autoplay blocked'));
  } else {
    if (liveVideo) liveVideo.pause();
  }
}
function playVideo(url) { 
  const vp = document.getElementById('videoPlayer'); 
  vp.src = url; 
  vp.play(); 
}
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

    # <--- SỬA ĐỔI: Truyền biến _STYLE vào template
    return render_template_string(_FRAME, body=body, style=_STYLE)


# -----------------------------------------------------------
# ROUTE PHỤC VỤ FILE HLS
# -----------------------------------------------------------
# <--- SỬA ĐỔI: Bỏ comment cho route này
@bp.route("/hls/<path:filename>")
def serve_hls(filename):
    """Phục vụ file HLS (.m3u8, .ts)"""
    # Ngăn chặn Path Traversal
    if re.search(r"(\.\.|%2e%2e|%00)", filename):
        current_app.logger.warning(f"HLS: Invalid path attempt: {filename}")
        abort(400)
    
    file_path = HLS_DIR.joinpath(filename).resolve()
    
    # Kiểm tra xem file có thực sự nằm trong HLS_DIR không
    if not file_path.exists() or not str(file_path).startswith(str(HLS_DIR.resolve())):
        current_app.logger.warning(f"HLS: File not found or access denied: {file_path}")
        abort(404)
        
    if filename.endswith(".m3u8"):
        mime = "application/vnd.apple.mpegurl"
    elif filename.endswith(".ts"):
        mime = "video/mp2t"
    else:
        mime = "application/octet-stream"
    
    # Thêm header để tránh cache
    response = send_from_directory(HLS_DIR, filename, mimetype=mime)
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response