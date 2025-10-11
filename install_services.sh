#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(pwd)"
UNIT_DIR="/etc/systemd/system"
ENV_FILE="/etc/default/picam"
SERVICES_FILE="${PROJECT_DIR}/services.list"

echo "🚀 [PiCam Installer] Cài đặt các service từ $SERVICES_FILE"

if ! [ -f "$SERVICES_FILE" ]; then
  echo "❌ Không tìm thấy file $SERVICES_FILE"
  exit 1
fi

echo "[+] Tạo EnvironmentFile ${ENV_FILE} (nếu chưa có)"
sudo mkdir -p "$(dirname "$ENV_FILE")"
if ! [ -f "$ENV_FILE" ]; then
  cat <<EOF | sudo tee "$ENV_FILE" >/dev/null
PICAM_CONFIG=firmware/config/device_full.yaml
PICAM_BIND=0.0.0.0
PICAM_PORT=8080
PICAM_API_PORT=8081
EOF
fi

mapfile -t SERVICES < "$SERVICES_FILE"

for u in "${SERVICES[@]}"; do
  if systemctl list-unit-files | grep -q "$u"; then
    echo "[−] Gỡ bỏ service cũ: $u"
    sudo systemctl stop "$u" || true
    sudo systemctl disable "$u" || true
    sudo rm -f "${UNIT_DIR}/$u"
  fi
done

echo "[+] Cài các service mới..."
for u in "${SERVICES[@]}"; do
  SRC="${PROJECT_DIR}/systemd/$u"
  DST="${UNIT_DIR}/$u"
  if [ -f "$SRC" ]; then
    echo "→ Cài $u"
    sudo install -m 0644 "$SRC" "$DST"
    sudo systemd-analyze verify "$DST" || {
      echo "❌ $u có lỗi cú pháp — bỏ qua"
      continue
    }
  else
    echo "[!] Thiếu file: $SRC"
  fi
done

sudo systemctl daemon-reload

for u in "${SERVICES[@]}"; do
  sudo systemctl enable --now "$u" || true
done

echo "[+] Trạng thái:"
for u in "${SERVICES[@]}"; do
  systemctl --no-pager --full status "$u" || true
done

echo "✅ Hoàn tất cài đặt dịch vụ PiCam!"
