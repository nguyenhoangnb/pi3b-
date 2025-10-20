#!/bin/bash
# Script: chmod_all.sh
# Mục đích: Cấp quyền thực thi (+x) cho tất cả các file .sh trong thư mục hiện tại

# Lặp qua tất cả file có đuôi .sh
for file in *.sh; do
    # Kiểm tra xem file có tồn tại không (tránh lỗi nếu không có file .sh)
    if [ -f "$file" ]; then
        chmod +x "$file"
        echo "Đã cấp quyền thực thi cho: $file"
    fi
done
