#!/bin/bash
# --------------------------------------------------
# Stop all systemd services listed in services.list
# Each line in services.list already contains .service
# --------------------------------------------------

SERVICES_FILE="/home/admin/pi3b-/services.list"

# Kiểm tra file tồn tại
if [ ! -f "$SERVICES_FILE" ]; then
    echo "❌ File $SERVICES_FILE không tồn tại!"
    exit 1
fi

echo "🛑 Dừng tất cả service trong $SERVICES_FILE ..."

# Đọc từng dòng trong file, dừng service và disable để không tự chạy lại khi boot
while IFS= read -r SERVICE_NAME || [ -n "$SERVICE_NAME" ]; do
    # Bỏ qua dòng trống hoặc dòng comment (#)
    [[ -z "$SERVICE_NAME" || "$SERVICE_NAME" =~ ^# ]] && continue

    echo "→ Đang dừng: $SERVICE_NAME"
    sudo systemctl stop "$SERVICE_NAME"

    if systemctl is-active --quiet "$SERVICE_NAME"; then
        echo "   ❌ Không thể dừng $SERVICE_NAME"
    else
        echo "   ✅ Đã dừng $SERVICE_NAME"
    fi

    echo "→ Đang tắt chế độ tự khởi động: $SERVICE_NAME"
    sudo systemctl disable "$SERVICE_NAME"
    if systemctl is-enabled --quiet "$SERVICE_NAME"; then
        echo "   ❌ Không thể disable $SERVICE_NAME"
    else
        echo "   ✅ Đã disable $SERVICE_NAME"
    fi

done < "$SERVICES_FILE"

echo "✅ Hoàn tất dừng và disable tất cả service."
