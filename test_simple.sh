#!/bin/bash
# ==============================================
# setup_picam_webui.sh
# Script tự động tạo và kích hoạt service PiCam WebUI (Flask)
# ==============================================

SERVICE_NAME=web.service
SERVICE_PATH=/etc/systemd/system/$SERVICE_NAME

echo "🔧 Đang tạo systemd service: $SERVICE_PATH ..."

sudo bash -c "cat > $SERVICE_PATH" <<'EOF'
[Unit]
Description=PiCam WebUI (Flask)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=admin
Group=admin
WorkingDirectory=/home/admin/pi3b-
EnvironmentFile=/etc/default/picam
Environment=PYTHONUNBUFFERED=1
ExecStart=/usr/bin/env bash -lc 'source .venv/bin/activate; exec python /home/admin/pi3b-/main_ffmpeg.py'
Restart=on-failure
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

echo "✅ Service file đã được tạo."

# Nạp lại systemd
echo "🔄 Reload systemd daemon..."
sudo systemctl daemon-reload

# Kích hoạt auto start
echo "🚀 Bật auto start cho service..."
sudo systemctl enable --now $SERVICE_NAME

# Hiển thị trạng thái
echo "📋 Trạng thái service:"
sudo systemctl status $SERVICE_NAME --no-pager
