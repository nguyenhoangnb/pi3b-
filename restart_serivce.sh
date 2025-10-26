#!/bin/bash

SERVICES_FILE="./services_restart.list"

if [ ! -f "$SERVICES_FILE" ]; then
    echo "❌ File $SERVICES_FILE không tồn tại!"
    exit 1
fi

while IFS= read -r service || [ -n "$service" ]; do
    [[ -z "$service" || "$service" =~ ^# ]] && continue

    echo "🔄 Restarting service: $service"

    if sudo systemctl restart "$service"; then
        echo "✅ $service restarted successfully"
    else
        echo "⚠ Failed to restart $service"
    fi

    status=$(systemctl is-active "$service")
    echo "   Status: $status"
done < "$SERVICES_FILE"
