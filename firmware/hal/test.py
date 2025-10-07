import time
import subprocess

class WifiManagerMock:
    def __init__(self):
        self.connected = False

    def is_connected(self):
        # Giả lập: sau 10s thì có mạng, hoặc đổi False để test timeout
        return self.connected

    def get_serial_number(self):
        return "NQT"

    def start_hotspot(self):
        print("📡 [MOCK] Hotspot started with SSID:", self.get_serial_number())

    def auto_connect(self):
        print("📶 [MOCK] Checking Wi-Fi connection...")
        start = time.time()
        while time.time() - start < 30:
            if self.is_connected():
                print("✅ [MOCK] Wi-Fi connected successfully.")
                return True
            print("⏳ Waiting for connection...")
            time.sleep(2)

        print("⚠️ [MOCK] Wi-Fi not connected after 30s. Waiting 40s more...")
        time.sleep(3)  # rút ngắn còn 3s cho test
        if not self.is_connected():
            print("🚧 [MOCK] Switching to AP mode...")
            self.start_hotspot()
        else:
            print("✅ [MOCK] Connected after waiting.")
            return True


if __name__ == "__main__":
    wifi = WifiManagerMock()
    wifi.auto_connect()
