#!/bin/bash
# Script: setup_my_camera.sh
# Mục đích: Ánh xạ camera USB Generic HD camera thành /dev/my_camera

# Vendor/Product ID của camera
VENDOR_ID="1902"
PRODUCT_ID="0327"
SYMLINK_NAME="my_camera"

# Tạo file udev rule
RULE_FILE="/etc/udev/rules.d/99-${SYMLINK_NAME}.rules"

echo "⚡ Tạo udev rule cho camera: $SYMLINK_NAME"
sudo bash -c "cat > $RULE_FILE <<EOF
# Ánh xạ Generic HD camera thành /dev/${SYMLINK_NAME}
SUBSYSTEM==\"video4linux\", ATTR{idVendor}==\"${VENDOR_ID}\", ATTR{idProduct}==\"${PRODUCT_ID}\", SYMLINK+=\"${SYMLINK_NAME}\"
EOF"

# Reload udev rules
echo "🔄 Reloading udev rules..."
sudo udevadm control --reload-rules
sudo udevadm trigger

# Kiểm tra
if [ -e "/dev/${SYMLINK_NAME}" ]; then
    echo "✅ /dev/${SYMLINK_NAME} đã sẵn sàng"
    ls -l "/dev/${SYMLINK_NAME}"
else
    echo "❌ Lỗi: /dev/${SYMLINK_NAME} chưa tạo được"
fi
