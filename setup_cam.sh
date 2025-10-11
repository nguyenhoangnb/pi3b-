#!/bin/bash

VENDOR="1902"
PRODUCT="0327"
SYMLINK_NAME="my_camera"

RULE_FILE="/etc/udev/rules.d/99-my_camera.rules"

sudo rm -f "$RULE_FILE"

echo "SUBSYSTEM==\"video4linux\", ATTRS{idVendor}==\"$VENDOR\", ATTRS{idProduct}==\"$PRODUCT\", SYMLINK+=\"$SYMLINK_NAME\"" | sudo tee "$RULE_FILE" > /dev/null

echo "🔄 Reloading udev rules..."
sudo udevadm control --reload-rules
sudo udevadm trigger

sleep 1

if [ -e "/dev/$SYMLINK_NAME" ]; then
    echo "✅ /dev/$SYMLINK_NAME đã được tạo thành công"
else
    echo "❌ Không thể tạo /dev/$SYMLINK_NAME"
fi
