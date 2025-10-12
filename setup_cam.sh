#!/bin/bash
#------------------------------------------------------------
# create_my_camera_rule.sh
# Tự động tạo udev rule để map camera USB cố định thành /dev/my_camera
#------------------------------------------------------------

VENDOR_ID="1902"
PRODUCT_ID="0327"
SYMLINK_NAME="my_camera"
RULES_FILE="/etc/udev/rules.d/99-usb-${SYMLINK_NAME}.rules"

echo "🔧 Đang tạo udev rule cho camera..."
echo "   Vendor ID : $VENDOR_ID"
echo "   Product ID: $PRODUCT_ID"
echo "   Symlink    : /dev/$SYMLINK_NAME"
echo "   Rule file  : $RULES_FILE"

# Nội dung rule
RULE="SUBSYSTEM==\"video4linux\", ATTRS{idVendor}==\"$VENDOR_ID\", ATTRS{idProduct}==\"$PRODUCT_ID\", SYMLINK+=\"$SYMLINK_NAME\", MODE=\"0666\""

# Tạo file rule
echo "$RULE" | sudo tee "$RULES_FILE" > /dev/null

# Reload udev
echo "♻️  Reload udev rules..."
sudo udevadm control --reload-rules
sudo udevadm trigger

# Kiểm tra lại
echo "✅ Đã tạo udev rule:"
echo "------------------------------------------------------"
cat "$RULES_FILE"
echo "------------------------------------------------------"
echo "🎯 Kiểm tra sau khi cắm lại camera:"
echo "   ls -l /dev/$SYMLINK_NAME"
