#!/bin/bash

VENDOR="1902"
PRODUCT="0327"
SYMLINK_NAME="my_camera"

# Tìm device video USB đúng Vendor/Product
VIDEO_DEV=$(for dev in /dev/video*; do
    if udevadm info -q property -n "$dev" | grep -q "ID_VENDOR_ID=$VENDOR" && \
       udevadm info -q property -n "$dev" | grep -q "ID_MODEL_ID=$PRODUCT"; then
        echo "$dev"
        break
    fi
done)

if [ -z "$VIDEO_DEV" ]; then
    echo "❌ Không tìm thấy camera với VendorID=$VENDOR ProductID=$PRODUCT"
    exit 1
fi

# Tạo symlink
sudo ln -sf "$VIDEO_DEV" "/dev/$SYMLINK_NAME"
echo "✅ /dev/$SYMLINK_NAME → $VIDEO_DEV đã được tạo"
