#!/bin/bash
# Add sudo permissions for VideoRecorder service control

echo "Adding sudo permissions for picam-recorder service control..."

# Create sudoers rule for service control
sudo tee /etc/sudoers.d/picam-service-control > /dev/null << 'EOF'
# Allow admin user to control picam-recorder service without password
admin ALL=(root) NOPASSWD: /bin/systemctl start picam-recorder.service
admin ALL=(root) NOPASSWD: /bin/systemctl stop picam-recorder.service
admin ALL=(root) NOPASSWD: /bin/systemctl restart picam-recorder.service
admin ALL=(root) NOPASSWD: /bin/systemctl status picam-recorder.service
EOF

# Set correct permissions
sudo chmod 0440 /etc/sudoers.d/picam-service-control

# Validate sudoers syntax
if sudo visudo -c; then
    echo "✓ Sudoers configuration added successfully"
    echo "✓ WebUI can now control picam-recorder service"
else
    echo "❌ Sudoers syntax error, removing file"
    sudo rm -f /etc/sudoers.d/picam-service-control
    exit 1
fi

./install_service.sh