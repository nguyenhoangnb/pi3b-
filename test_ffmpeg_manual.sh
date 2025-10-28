#!/bin/bash
# ==============================================
# remove_picam_webui.sh
# Script xóa hoàn toàn service PiCam WebUI (Flask)
# ==============================================

SERVICE_NAME=web.service
SERVICE_PATH=/etc/systemd/system/$SERVICE_NAME

echo "🧹 Đang gỡ service: $SERVICE_NAME ..."

# Kiểm tra service có tồn tại không
if [ -f "$SERVICE_PATH" ]; then
    echo "🛑 Dừng service nếu đang chạy..."
    sudo systemctl stop $SERVICE_NAME 2>/dev/null

    echo "🚫 Tắt auto start..."
    sudo systemctl disable $SERVICE_NAME 2>/dev/null

    echo "🗑️ Xóa file service..."
    sudo rm -f $SERVICE_PATH

    echo "🔄 Reload lại systemd daemon..."
    sudo systemctl daemon-reload

    echo "✅ Đã gỡ bỏ thành công service: $SERVICE_NAME"
else
    echo "⚠️ Không tìm thấy file service: $SERVICE_PATH"
fi

echo "📋 Kiểm tra lại danh sách service (tùy chọn):"
echo "sudo systemctl list-units --type=service | grep picam"
