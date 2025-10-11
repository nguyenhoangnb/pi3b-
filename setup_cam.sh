#!/bin/bash

# File: setup_cam.sh
# Mục đích: Tạo udev symlink /dev/my_camera cho camera USB "Generic HD camera"

CAMERA_NAME="Generic HD camera"
SYMLINK_NAME="my_camera"

echo "⚡ Tạo udev rule cho camera: $SYMLINK_NAME"

RULE_FILE="/etc/udev/rules.d/99-my_camera.rules"

# Xoá rule cũ nếu có
sudo rm -f "$RULE_FILE"

# Lấy danh sách video devices
VIDEO_DEVICES=$(ls /dev/video* 2>/dev/null)
if [ -z "$VIDEO_DEVICES" ]; then
    echo "❌ Không tìm thấy video device nào"
    exit 1
fi

# Tìm device phù hợp với ATTR{name} của camera
MATCHED_DEV=""
for dev in $VIDEO_DEVICES; do
    NAME=$(udevadm info -q property -n "$dev" | grep '^ID_V4L_PRODUCT=' | cut -d= -f2)
    if [ "$NAME" == "$CAMERA_NAME" ]; then
        MATCHED_DEV="$dev"
        break
    fi
done

if [ -z "$MATCHED_DEV" ]; then
    echo "❌ Không tìm thấy camera với tên '$CAMERA_NAME'"
    exit 1
fi

# Tạo udev rule
echo "SUBSYSTEM==\"video4linux\", ATTR{name}==\"$CAMERA_NAME\", SYMLINK+=\"$SYMLINK_NAME\"" | sudo tee "$RULE_FILE" > /dev/null

# Reload udev
echo "🔄 Reloading udev rules..."
sudo udevadm control --reload-rules
sudo udevadm trigger

sleep 1

# Kiểm tra symlink
if [ -e "/dev/$SYMLINK_NAME" ]; then
    echo "✅ /dev/$SYMLINK_NAME đã được tạo thành công, trỏ tới $MATCHED_DEV"
else
    echo "❌ Không thể tạo /dev/$SYMLINK_NAME"
fi
