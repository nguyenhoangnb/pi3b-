#!/usr/bin/env bash
# -------------------------------------------------------
# 📦 Script: check_services.sh
# 🔧 Mục đích: Kiểm tra trạng thái của từng service có trong file services.list
# -------------------------------------------------------

SERVICE_LIST_FILE="$(dirname "$0")/services.list"

if [ ! -f "$SERVICE_LIST_FILE" ]; then
    echo "❌ Không tìm thấy file $SERVICE_LIST_FILE"
    exit 1
fi

echo "🚀 [PiCam] Kiểm tra trạng thái các service trong $SERVICE_LIST_FILE"
echo "-----------------------------------------------------------"

# Đọc từng dòng trong file services.list
while IFS= read -r service || [ -n "$service" ]; do
    # Bỏ qua dòng trống hoặc dòng comment (#)
    [[ -z "$service" || "$service" =~ ^# ]] && continue

    # Kiểm tra trạng thái service
    STATUS=$(systemctl is-active "$service" 2>/dev/null)
    ENABLED=$(systemctl is-enabled "$service" 2>/dev/null)

    if [ "$STATUS" = "active" ]; then
        echo "✅ $service → đang chạy ($ENABLED)"
    elif [ "$STATUS" = "inactive" ]; then
        echo "⚪ $service → đã dừng ($ENABLED)"
    elif [ "$STATUS" = "failed" ]; then
        echo "❌ $service → lỗi khởi động ($ENABLED)"
    else
        echo "⚠️  $service → không tồn tại hoặc chưa cài ($ENABLED)"
    fi
done < "$SERVICE_LIST_FILE"

echo "-----------------------------------------------------------"
echo "🟢 Kiểm tra hoàn tất!"
