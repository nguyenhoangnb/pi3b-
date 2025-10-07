PiCam QUICKSTART (dịch vụ WebUI & API)
=======================================

1) Cài phụ thuộc & RTC/USB (nếu chưa làm):
   sudo -E bash setup_once_patched.sh
   sudo reboot

2) Cài dịch vụ từ project (chạy trong thư mục gốc dự án này):
   cd $(dirname $0)
   sudo -E bash install_services.sh

3) Mở WebUI:
   http://<IP-Pi>:8080/

4) Nếu WebUI chưa lên, xem log:
   journalctl -u picam-web.service -n 200 --no-pager
   journalctl -u picam-api.service -n 200 --no-pager
