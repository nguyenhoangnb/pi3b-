#!/bin/bash
# Script: vào pi3b-, kích hoạt venv và cd vào firmware/domain

# Đi tới thư mục pi3b-
cd ~/pi3b- || { echo "❌ Thư mục pi3b- không tồn tại"; exit 1; }

git pull
# Kích hoạt virtual environment
if [ -f ".venv/bin/activate" ]; then
    echo "🚀 Kích hoạt virtual environment..."
    source .venv/bin/activate
else
    echo "⚠ Không tìm thấy .venv/bin/activate"
fi

# Chuyển vào thư mục firmware/domain
cd firmware/domain || { echo "❌ Thư mục firmware/domain không tồn tại"; exit 1; }

# In ra vị trí hiện tại
echo "📍 Current directory: $(pwd)"
