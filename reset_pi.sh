#!/bin/bash
# ================================================
#  Raspberry Pi Debian 13 Reset Script
#  Author: ChatGPT (GPT-5)
#  Purpose: Reinstall base system packages, clean cache, and clear user data
# ================================================

echo "⚠️  WARNING: This will reinstall base Debian packages and DELETE all files in /home/admin/"
read -p "Do you want to continue? (yes/no): " confirm

if [[ "$confirm" != "yes" ]]; then
  echo "❌ Operation cancelled."
  exit 1
fi

echo "🔧 Cleaning old package caches..."
sudo apt clean

echo "📦 Updating package lists..."
sudo apt update -y

echo "♻️  Reinstalling essential Debian packages..."
sudo apt install --reinstall debian-goodies base-files bash coreutils systemd network-manager openssh-server sudo nano -y

echo "⬆️  Upgrading all packages to latest versions..."
sudo apt full-upgrade -y

echo "🧹 Removing unused packages..."
sudo apt autoremove --purge -y
sudo apt autoclean

echo "🗑️  Clearing all files in /home/admin..."
sudo rm -rf /home/admin/*

echo "✅ Reset completed successfully!"
echo "🔁 Rebooting system in 5 seconds..."
sleep 5
sudo reboot
