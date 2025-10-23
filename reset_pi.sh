#!/bin/bash
# =====================================================
# Raspberry Pi OS Reset Script
# Tác giả: ChatGPT
# Mục đích: Xóa sạch dữ liệu, gỡ toàn bộ gói, reset về trạng thái mới cài
# Cảnh báo: Toàn bộ dữ liệu, phần mềm và cấu hình người dùng sẽ bị xóa!
# =====================================================

echo "⚠️  CẢNH BÁO: Script này sẽ xóa toàn bộ dữ liệu và phần mềm trên hệ thống!"
read -p "Nhập 'YES' để xác nhận reset: " confirm

if [ "$confirm" != "YES" ]; then
  echo "❌ Huỷ thao tác."
  exit 1
fi

echo "🔹 Bắt đầu dọn dẹp hệ thống..."

# Xóa toàn bộ dữ liệu trong /home (ngoại trừ thư mục script)
sudo find /home -mindepth 1 -maxdepth 1 ! -name "$(whoami)" -exec rm -rf {} +

# Xóa cache và log
sudo rm -rf /var/log/*
sudo rm -rf /tmp/*

echo "🔹 Gỡ toàn bộ package cài thêm..."
sudo apt remove --purge -y $(dpkg -l | awk '/^ii/ { print $2 }' | grep -vE '^(raspberrypi|libc|bash|dpkg|apt|systemd|login|coreutils|sudo|util-linux|netbase|ifupdown|ca-certificates)')

echo "🔹 Làm sạch hệ thống..."
sudo apt autoremove -y
sudo apt clean

echo "🔹 Cài lại các gói cơ bản cần thiết cho Raspberry Pi OS..."
sudo apt install --reinstall -y raspberrypi-ui-mods raspberrypi-bootloader raspberrypi-kernel raspberrypi-net-mods network-manager

echo "🔹 Cập nhật hệ thống..."
sudo apt update && sudo apt full-upgrade -y

echo "✅ Reset hoàn tất. Hệ thống sẽ khởi động lại trong 5 giây..."
sleep 5
sudo reboot
