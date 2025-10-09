#!/usr/bin/env bash
set -euo pipefail
# Cài đặt/refresh service cho WebUI & API từ thư mục dự án hiện tại.
# Chạy lệnh này trong thư mục gốc dự án (chứa folder systemd/ và run_webui.py):
#   sudo -E bash install_services.sh

PROJECT_DIR="$(pwd)"
UNIT_DIR="/etc/systemd/system"
ENV_FILE="/etc/default/picam"

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

echo "[+] Copy unit files -> ${UNIT_DIR}"
for u in picam-web.service picam-api.service; do
  if [ -f "${PROJECT_DIR}/systemd/$u" ]; then
    sudo install -m 0644 "${PROJECT_DIR}/systemd/$u" "${UNIT_DIR}/$u"
    sudo systemctl unmask "$u" || true
    sudo systemctl daemon-reload
    sudo systemctl enable --now "$u"
  else
    echo "[!] Thiếu ${PROJECT_DIR}/systemd/$u"
  fi
done

echo "[+] Trạng thái:"
systemctl --no-pager --full status picam-web.service || true
systemctl --no-pager --full status picam-api.service || true
