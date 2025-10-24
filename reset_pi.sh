#!/bin/bash
echo "⚠️  Bắt đầu reset Raspberry Pi OS (sạch toàn bộ, giữ SSH và mạng)..."
sleep 3

# 1️⃣ Giữ lại cấu hình mạng & SSH
sudo mkdir -p /backup_keep
sudo cp -r /etc/ssh /backup_keep/
sudo cp -r /etc/network /backup_keep/ 2>/dev/null
sudo cp /etc/hostname /backup_keep/
sudo cp /etc/hosts /backup_keep/
sudo cp /etc/dhcpcd.conf /backup_keep/ 2>/dev/null
sudo cp -r /etc/wpa_supplicant /backup_keep/ 2>/dev/null

echo "✅ Backup SSH và cấu hình mạng hoàn tất."

# 2️⃣ Xoá toàn bộ dữ liệu user (home, cache, logs, ROS, python, ...)
sudo rm -rf /home/pi/*
sudo rm -rf /opt/*
sudo rm -rf /usr/local/*
sudo rm -rf /var/log/*
sudo apt clean
sudo apt autoclean
sudo apt autoremove --purge -y

echo "🧹 Đã xoá sạch dữ liệu người dùng và ứng dụng tùy chỉnh."

# 3️⃣ Cài lại các gói lõi hệ thống
sudo apt update
sudo apt install --reinstall -y \
  raspberrypi-bootloader raspberrypi-kernel \
  raspberrypi-ui-mods raspberrypi-sys-mods \
  pi-bluetooth raspi-config lxappearance lxsession \
  openssh-server net-tools dhcpcd5 ifupdown \
  network-manager

echo "🔧 Đã cài lại hệ thống lõi."

# 4️⃣ Khôi phục SSH và cấu hình mạng
sudo cp -r /backup_keep/ssh /etc/
sudo cp -r /backup_keep/network /etc/ 2>/dev/null
sudo cp /backup_keep/hostname /etc/
sudo cp /backup_keep/hosts /etc/
sudo cp /backup_keep/dhcpcd.conf /etc/ 2>/dev/null
sudo cp -r /backup_keep/wpa_supplicant /etc/ 2>/dev/null

echo "🔁 Đã khôi phục cấu hình SSH và mạng."

# 5️⃣ Dọn lại package và nâng cấp hệ thống
sudo apt full-upgrade -y
sudo apt autoremove --purge -y
sudo apt autoclean
sudo apt clean

echo "✅ Reset hoàn tất. Sẽ khởi động lại sau 5 giây..."
sleep 5
sudo reboot
