#!/bin/bash
sudo apt update
sudo apt install git
# Biến repo URL
REPO_URL="https://github.com/nguyenhoangnb/pi3b-.git"
TARGET_DIR="$HOME/pi3b-"  # Thư mục đích để clone

# Nếu thư mục đã tồn tại, cập nhật repo; nếu chưa, clone
if [ -d "$TARGET_DIR" ]; then
    echo "📦 Repository đã tồn tại, cập nhật..."
    cd "$TARGET_DIR" || exit 1
    git pull
else
    echo "📦 Cloning repository..."
    git clone "$REPO_URL" "$TARGET_DIR"
    cd "$TARGET_DIR" || exit 1
fi

# Chạy script setup_one.sh
if [ -f "setup_one.sh" ]; then
    echo "🚀 Running setup_one.sh..."
    chmod +x setup_one.sh
    ./setup_one.sh
else
    echo "⚠️ Không tìm thấy setup_one.sh trong $TARGET_DIR"
fi
