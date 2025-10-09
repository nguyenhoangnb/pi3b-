#!/usr/bin/env bash
set -Eeuo pipefail

sudo apt-get install -y ffmpeg fonts-dejavu-core python3-libgpiod 

# ============================================================================
# PiCam • setup_once.sh (merged & annotated)
# ----------------------------------------------------------------------------
# PURPOSE
#   One-shot provisioning for a fresh Raspberry Pi OS (Debian 12 Bookworm):
#     - Fix system clock (NTP/HTTP fallback) & set timezone (GMT+7)
#     - Enable I2C + install battery RTC overlay (DS3231 by default)
#     - Ensure RTC<->System sync services (RTC at boot; Internet->RTC after net)
#     - Prepare USB storage mount (LABEL=PICAM -> /media/ssd, automount)
#     - Install core packages, Python venv & deps
#     - Enable/refresh picam-web & picam-api services (if present)
#     - Grant WebUI permission to call `hwclock` via sudo (no password)
#
# USAGE
#   sudo -E ./setup_once.sh
#
# SAFE TO RE-RUN
#   The script is idempotent where practical: it checks before changing.
#
# NOTE
#   This script assumes a USB partition labeled PICAM for recordings.
#   Set/update your drive label accordingly (ext4: e2label; exFAT: exfatlabel; NTFS: ntfslabel).
#
# ============================================================================

# --------------------------- Utilities & guards -----------------------------
log()   { printf "\033[1;32m[+] %s\033[0m\n" "$*"; }
warn()  { printf "\033[1;33m[!] %s\033[0m\n" "$*"; }
err()   { printf "\033[1;31m[-] %s\033[0m\n" "$*"; }
die()   { err "$*"; exit 1; }
need_root() { [[ ${EUID:-$(id -u)} -eq 0 ]] || die "Run as root: sudo -E $0"; }
have_cmd()  { command -v "$1" >/dev/null 2>&1; }
ensure_pkg(){ dpkg -s "$1" >/dev/null 2>&1 || { log "Installing APT: $1"; apt-get install -y "$1"; }; }

reset_service() {
  # Install/refresh a systemd unit file and enable it
  local unit="$1" src="$2"
  systemctl stop "$unit" >/dev/null 2>&1 || true
  systemctl disable "$unit" >/dev/null 2>&1 || true
  systemctl unmask "$unit" >/dev/null 2>&1 || true
  install -m 0644 "$src" "/etc/systemd/system/$unit"
  systemctl daemon-reload
  systemctl enable --now "$unit"
}

ensure_timezone() {
  # Set system timezone to Vietnam (GMT+7)
  local tz="Asia/Ho_Chi_Minh"
  if [[ "$(timedatectl show -p Timezone --value 2>/dev/null || echo UTC)" != "$tz" ]]; then
    log "Setting timezone to $tz"
    timedatectl set-timezone "$tz" || true
  fi
}

try_timesyncd_sync() {
  # Try to sync time via systemd-timesyncd (NTP). Return 0 if synced.
  systemctl enable --now systemd-timesyncd >/dev/null 2>&1 || true
  timedatectl set-ntp true || true
  systemctl restart systemd-timesyncd || true
  for _ in $(seq 1 90); do
    timedatectl show -p NTPSynchronized --value 2>/dev/null | grep -qi '^yes$' && {
      log "NTP synchronized via systemd-timesyncd"; return 0; }
    sleep 1
  done
  return 1
}

fallback_http_date() {
  # Fallback: set clock using HTTP Date header when NTP unavailable
  have_cmd curl || return 1
  local host date_hdr when
  for host in google.com cloudflare.com microsoft.com time.cloudflare.com; do
    date_hdr="$(curl -sI --max-time 5 "https://$host" | awk -F': ' 'tolower($1)=="date"{print $2; exit}')"
    when="$(date -d "$date_hdr" '+%Y-%m-%d %H:%M:%S' 2>/dev/null || true)"
    if [[ -n "${when:-}" ]]; then
      log "Setting system clock from HTTP Date ($host): $when"
      date -s "$when" || return 1
      hwclock -w 2>/dev/null || true
      return 0
    fi
  done
  return 1
}

auto_fix_time() {
  # Ensure sane time early so TLS/APT works
  ensure_timezone
  local y; y="$(date +%Y || echo 1970)"
  if (( y < 2024 || y > 2035 )); then
    warn "Clock far off (year=$y). Using HTTP Date fallback first."
    fallback_http_date && return 0
  fi
  try_timesyncd_sync || { warn "NTP not ready, using HTTP Date fallback"; fallback_http_date || true; }
}

# ----------------------------- MAIN START ----------------------------------
need_root
export DEBIAN_FRONTEND=noninteractive
PROJECT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$PROJECT_DIR"

: "${PICAM_CONFIG:=firmware/config/device_full.yaml}"
: "${RTC_OVERLAY:=ds3231}"     # change to ds1307 or pcf8523 if needed

log "Auto-fixing system time"
ensure_pkg curl
auto_fix_time || true
date; timedatectl status | sed -n '1,8p' || true

log "APT update"
apt-get update -y

# Filesystems for typical USB drives (exFAT/NTFS). ext4 works OOTB.
ensure_pkg exfatprogs
ensure_pkg exfat-fuse
ensure_pkg ntfs-3g

# ----------------- I2C + RTC overlay + RTC sync services -------------------
log "Enable I2C and add RTC overlay in /boot/firmware/config.txt"
ensure_pkg i2c-tools
ensure_pkg tzdata

CFG="/boot/firmware/config.txt"
[[ -f "$CFG" ]] || die "Missing $CFG"
cp -a "$CFG" "$CFG.bak.$(date +%F_%H%M%S)"

# 1) Ensure dtparam=i2c_arm=on under [all]
if grep -qE '^\s*#\s*dtparam=i2c_arm=on' "$CFG"; then
  sed -i 's/^\s*#\s*dtparam=i2c_arm=on/dtparam=i2c_arm=on/' "$CFG"
fi
grep -qE '^\s*dtparam=i2c_arm=on' "$CFG" || sed -i '/^\[all\]/a dtparam=i2c_arm=on' "$CFG"

# 2) Add/replace dtoverlay=i2c-rtc,<model> under [all]
if grep -qE '^\s*dtoverlay=i2c-rtc' "$CFG"; then
  sed -i "s#^\s*dtoverlay=i2c-rtc.*#dtoverlay=i2c-rtc,${RTC_OVERLAY}#" "$CFG"
else
  sed -i "/^\[all\]/a dtoverlay=i2c-rtc,${RTC_OVERLAY}" "$CFG"
fi

log "Remove fake-hwclock (use real RTC instead)"
apt-get purge -y fake-hwclock || true
systemctl disable --now fake-hwclock || true

# Some Debian builds skip hwclock when running under systemd.
# Comment the early-exit in /lib/udev/hwclock-set so RTC is read on boot.
UDEV=/lib/udev/hwclock-set
if [[ -f "$UDEV" ]]; then
  cp -a "$UDEV" "$UDEV.bak.$(date +%F_%H%M%S)"
  sed -i 's/^\(if \[ -e \/run\/systemd\/system \].*\)$/# \1/' "$UDEV"
  sed -i 's/^\(\s*exit 0\s*\)$/# \1/' "$UDEV"
fi

log "Create service: RTC -> System at early boot"
cat > /etc/systemd/system/rtc-hctosys.service <<'UNIT'
[Unit]
Description=Load time from RTC at boot (early)
DefaultDependencies=no
Before=sysinit.target time-sync.target
After=dev-rtc.device

[Service]
Type=oneshot
ExecStart=/sbin/hwclock --hctosys

[Install]
WantedBy=sysinit.target
UNIT

log "Create service: Internet/NTP -> RTC once after network"
cat > /usr/local/sbin/rtc_sync.sh <<'SYNC'
#!/usr/bin/env bash
set -euo pipefail
LOG=/var/log/rtc-sync.log
echo "[$(date)] rtc_sync start" | tee -a "$LOG"

# wait up to 2 minutes for NTP sync
for i in {1..120}; do
  ok=$(timedatectl show -p NTPSynchronized --value 2>/dev/null || echo no)
  if [ "$ok" = "yes" ]; then
    echo "[$(date)] NTP synchronized" | tee -a "$LOG"
    break
  fi
  sleep 1
done

/sbin/hwclock --systohc
ret=$?
echo "[$(date)] hwclock --systohc exit=$ret" | tee -a "$LOG"
SYNC
chmod +x /usr/local/sbin/rtc_sync.sh

cat > /etc/systemd/system/rtc-sync.service <<'UNIT'
[Unit]
Description=Sync Internet time to RTC once after boot
Wants=network-online.target time-sync.target
After=network-online.target time-sync.target

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/rtc_sync.sh

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable rtc-hctosys.service rtc-sync.service

# Allow WebUI user (admin) to call hwclock without password
if [[ ! -f /etc/sudoers.d/picam-rtc ]]; then
  echo 'admin ALL=(root) NOPASSWD: /sbin/hwclock' > /etc/sudoers.d/picam-rtc
  chmod 0440 /etc/sudoers.d/picam-rtc
  visudo -c || true
fi

# ---------------- USB automount: LABEL=PICAM -> /media/ssd -----------------
ensure_usb_mount() {
  local MNT="/media/ssd"
  # Use 'auto' so ntfs/exfat/ext4 all work; systemd.automount for on-demand
  local LINE='LABEL=PICAM  /media/ssd  auto  defaults,nofail,x-systemd.automount,x-systemd.idle-timeout=60,uid=admin,gid=admin  0  0'
  log "Configuring automount $MNT (LABEL=PICAM)"
  mkdir -p "$MNT"
  if ! grep -qF "$LINE" /etc/fstab; then
    # drop any previous PICAM entries to avoid duplicates
    sed -i '/\/media\/ssd/d' /etc/fstab || true
    echo "$LINE" >> /etc/fstab
    log "Added fstab entry: $LINE"
  else
    log "fstab already contains PICAM entry"
  fi
  systemctl daemon-reload
  systemctl restart local-fs.target || true
  # touch mountpoint to trigger automount if drive is present
  ls -la "$MNT" >/dev/null 2>&1 || true
  if mount | grep -q " $MNT "; then
    log "USB mounted at $MNT"
  else
    warn "Not mounted yet. Ensure your partition has LABEL=PICAM (e2label/exfatlabel/ntfslabel)."
  fi
}
ensure_usb_mount

# Prepare recording folders when mount is active
RECORD_ROOT="/media/ssd/picam"
LOG_DIR="/media/ssd/picam/logs"
if mount | grep -q " /media/ssd "; then
  mkdir -p "$RECORD_ROOT" "$LOG_DIR"
  chown -R admin:admin /media/ssd || true
  log "Record root ready at $RECORD_ROOT"
else
  warn "/media/ssd not mounted yet — skipping mkdir."
fi

# ----------------------- Core packages & Python venv -----------------------
for p in python3-venv python3-pip python3-gpiozero ffmpeg curl jq git dnsutils iproute2 wpasupplicant unzip python3-dev portaudio19-dev; do
  ensure_pkg "$p"
done

# Python venv & deps (if your repo has requirements.txt it will be used)
if [[ ! -d ".venv" ]]; then
  log "Create venv .venv"
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade --quiet pip setuptools wheel
[[ -f requirements.txt ]] && pip install -r requirements.txt
pip install --upgrade fastapi uvicorn[standard] pydantic pyyaml httpx flask

# ------------------------- Env file for services ---------------------------
ENV_FILE="/etc/default/picam"
mkdir -p "$(dirname "$ENV_FILE")"
cat > "$ENV_FILE" <<EOF
PICAM_CONFIG=${PICAM_CONFIG}
PICAM_BIND=0.0.0.0
PICAM_PORT=8080
PICAM_API_PORT=8081
EOF
log "Wrote $ENV_FILE"

# ---------------------- Install/refresh systemd units ----------------------
if [[ -f "systemd/picam-web.service" && -f "systemd/picam-api.service" ]]; then
  for unit in systemd/picam-web.service systemd/picam-api.service; do
    if ! grep -q "^EnvironmentFile=" "$unit"; then
      awk '
        /^\[Service\]$/ && !seen { print; print "EnvironmentFile=/etc/default/picam"; seen=1; next }
        { print }
      ' "$unit" > "$unit.tmp" && mv "$unit.tmp" "$unit"
    fi
  done
  reset_service "picam-web.service" "systemd/picam-web.service"
  reset_service "picam-api.service" "systemd/picam-api.service"
else
  systemctl daemon-reload
  systemctl enable --now picam-web.service picam-api.service || true
fi

# --------------------------- Network & GPIO misc ---------------------------
systemctl enable --now wpa_supplicant >/dev/null 2>&1 || true
systemctl stop hostapd dnsmasq >/dev/null 2>&1 || true
systemctl disable hostapd dnsmasq >/dev/null 2>&1 || true

# Permissions for admin to access /dev/gpiomem (LED/Reed)
id -nG admin | tr ' ' '\n' | grep -qx gpio || usermod -aG gpio admin || true

log "Done. Status overview:"
systemctl --no-pager --full status picam-web.service || true
systemctl --no-pager --full status picam-api.service || true

echo
echo "================ NEXT STEPS ================"
echo "• Reboot to apply RTC overlay: sudo reboot"
echo "• After boot:"
echo "    - sudo i2cdetect -y 1      # should see 0x68 for DS3231/DS1307"
echo "    - sudo hwclock -r          # read RTC (UTC)"
echo "    - timedatectl              # NTP synchronized: yes (when online)"
echo "• WebUI → http://<Pi-IP>:8080/"
echo "• API   → http://<Pi-IP>:8081/docs"
echo "==========================================="


# ----------------------------------------------------------------------------
# [BỔ SUNG] Bật i2c-dev để có /dev/i2c-1 cho i2cdetect (không ảnh hưởng RTC)
# ----------------------------------------------------------------------------
log "Đảm bảo module i2c-dev được nạp lúc boot và ngay bây giờ"
echo "i2c-dev" > /etc/modules-load.d/i2c-dev.conf
modprobe i2c-dev 2>/dev/null || true

# ----------------------------------------------------------------------------
# [BỔ SUNG] Không ghi đè model RTC nếu đã có trong config.txt
# ----------------------------------------------------------------------------
CFG="/boot/firmware/config.txt"
if grep -qE '^\s*dtoverlay=i2c-rtc' "$CFG"; then
  log "config.txt đã có dtoverlay=i2c-rtc → giữ nguyên model hiện có."
else
  # Nếu chưa có thì thêm dòng mới (mặc định ds3231; có thể đổi qua RTC_OVERLAY env)
  sed -i "/^\[all\]/a dtoverlay=i2c-rtc,${RTC_OVERLAY:-ds3231}" "$CFG"
fi

# ----------------------------------------------------------------------------
# [BỔ SUNG] Sửa lỗi NTFS 'Input/output error' (ổ bẩn/dirty) bằng ntfsfix
# ----------------------------------------------------------------------------
fix_ntfs_if_dirty() {
  local dev mp="/media/ssd"
  dev="$(lsblk -no PKNAME,NAME,MOUNTPOINT,FSTYPE | awk '$3=="/media/ssd"{print "/dev/"$2; exit}')"
  if [[ -n "$dev" ]]; then
    warn "Thử ntfsfix nếu là NTFS và có lỗi I/O..."
    umount "$mp" 2>/dev/null || true
    ntfsfix "$dev" 2>/dev/null || true
    mount "$mp" 2>/dev/null || true
  fi
}
# Nếu chown trước đó báo I/O error thì chạy sửa
if dmesg | tail -n 200 | grep -qi "I/O error"; then
  fix_ntfs_if_dirty
fi

# ----------------------------------------------------------------------------
# [BỔ SUNG] Tạo service picam-web.service mặc định nếu chưa có file unit
# ----------------------------------------------------------------------------
if [[ ! -f /etc/systemd/system/picam-web.service ]]; then
  log "Tạo picam-web.service mặc định (nếu repo không cung cấp sẵn)"
  cat > /etc/systemd/system/picam-web.service <<'UNIT'
[Unit]
Description=PiCam WebUI (Flask)
After=network-online.target
Wants=network-online.target

[Service]
User=admin
WorkingDirectory=/home/admin
EnvironmentFile=/etc/default/picam
ExecStart=/usr/bin/python3 /home/admin/run_webui.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
UNIT

  systemctl daemon-reload
  systemctl enable --now picam-web.service || true
fi

# Bật recorder mặc định (Record ON)
sudo systemctl enable --now picam-recorder.service || true

# Cho phép admin điều khiển service recorder qua WebUI (không hỏi sudo password)
echo 'admin ALL=(root) NOPASSWD: /bin/systemctl start picam-recorder.service, /bin/systemctl stop picam-recorder.service' | sudo tee /etc/sudoers.d/picam-rec-ctl >/dev/null
sudo visudo -c || true
