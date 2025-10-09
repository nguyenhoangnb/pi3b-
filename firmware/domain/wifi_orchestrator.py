#!/usr/bin/env python3
"""
WiFi Orchestrator - Quản lý WiFi theo logic Reed switch
- WiFi mặc định OFF
- Reed switch trigger → WiFi client → WiFi AP → Auto OFF
- LED status indication
- Auto timeout management
"""

import time
import threading
import subprocess
import sys
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from firmware.hal.wifi import WifiManager
from firmware.hal.gpio_leds import gpioLed
from firmware.hal.reed_switch import ReedSwitch

class WiFiOrchestrator:
    def __init__(self, config):
        self.config = config
        self.wifi_config = config.get('wifi', {})
        self.gpio_config = config.get('gpio', {})
        
        # WiFi settings
        self.client_ssid = self.wifi_config.get('ssid', 'PICAM')
        self.client_password = self.wifi_config.get('password', '0123456789')
        self.ap_ssid = self.wifi_config.get('ap_ssid', 'PICAM')
        self.ap_password = self.wifi_config.get('ap_password', None)  # None = open AP
        
        # Timing settings
        self.client_timeout = 30  # 30s để connect client
        self.ap_wait_timeout = 40  # 40s để chờ trước khi start AP
        self.ap_connection_timeout = 70  # 70s để chờ device connect AP
        self.auto_off_timeout = 15 * 60  # 15 phút auto-off
        
        # Components
        self.wifi_manager = WifiManager(
            client_ssid=self.client_ssid,
            client_pass=self.client_password,
            ssid_prefix_ap=self.ap_ssid,
            ap_password=self.ap_password
        )
        
        # LEDs and Reed switch
        self.wifi_led = None
        self.reed_switch = None
        
        # State management
        self.state = "OFF"  # OFF, CLIENT_CONNECTING, CLIENT_CONNECTED, AP_STARTING, AP_ACTIVE, AP_WAITING
        self.last_reed_time = None
        self.auto_off_timer = None
        self.state_timer = None
        self.running = False
        
        # Thread safety
        self.state_lock = threading.Lock()
        
        self._initialize_components()
    
    def _initialize_components(self):
        """Initialize GPIO components"""
        try:
            # WiFi LED
            wifi_led_pin = self.gpio_config.get('wifi_led')
            if wifi_led_pin:
                self.wifi_led = gpioLed(wifi_led_pin)
                print(f"✓ WiFi LED initialized on pin {wifi_led_pin}")
            
            # Reed switch
            reed_pin = self.gpio_config.get('reed')
            if reed_pin:
                self.reed_switch = ReedSwitch(
                    pin=reed_pin,
                    callback=self._reed_triggered,
                    debounce_time=0.5
                )
                print(f"✓ Reed switch initialized on pin {reed_pin}")
                
        except Exception as e:
            print(f"⚠ Component initialization error: {e}")
    
    def _reed_triggered(self):
        """Reed switch callback - start WiFi sequence"""
        print("🔔 Reed switch triggered - Starting WiFi sequence")
        
        with self.state_lock:
            self.last_reed_time = datetime.now()
            
            # Reset auto-off timer (15 phút)
            self._reset_auto_off_timer()
            
            # Start WiFi sequence
            if self.state == "OFF":
                self._start_wifi_sequence()
            else:
                print("ℹ WiFi already active, resetting timer")
    
    def _start_wifi_sequence(self):
        """Bắt đầu sequence: Client → AP → Auto-off"""
        print("🚀 Starting WiFi sequence...")
        
        # Cancel any existing timers
        self._cancel_timers()
        
        # Start client connection phase
        self._transition_to_client_mode()
    
    def _transition_to_client_mode(self):
        """Phase 1: Thử kết nối WiFi client"""
        with self.state_lock:
            self.state = "CLIENT_CONNECTING"
        
        print(f"📱 Phase 1: Connecting to WiFi client ({self.client_ssid})")
        
        # LED nháy 0.5s
        if self.wifi_led:
            self.wifi_led.blink(0.5)
        
        # Start client connection in background
        threading.Thread(target=self._try_client_connection, daemon=True).start()
        
        # Set timer for client timeout
        self.state_timer = threading.Timer(self.client_timeout, self._client_timeout)
        self.state_timer.start()
    
    def _try_client_connection(self):
        """Thử kết nối WiFi client"""
        try:
            success = self.wifi_manager.start_client()
            
            if success:
                # Kiểm tra Internet connectivity
                if self.wifi_manager.check_wifi_connected():
                    self._client_connected()
                else:
                    print("⚠ Connected to WiFi but no Internet")
                    # Vẫn coi là thành công nếu connect được WiFi
                    self._client_connected()
            else:
                print("✗ Client connection failed")
                # Timeout sẽ handle việc chuyển sang AP mode
                
        except Exception as e:
            print(f"⚠ Client connection error: {e}")
    
    def _client_connected(self):
        """Client kết nối thành công"""
        print("✅ WiFi client connected successfully")
        
        with self.state_lock:
            self.state = "CLIENT_CONNECTED"
        
        # Cancel timeout timer
        if self.state_timer:
            self.state_timer.cancel()
        
        # LED sáng đứng
        if self.wifi_led:
            self.wifi_led.on()
        
        print("💡 WiFi LED: ON (client connected)")
    
    def _client_timeout(self):
        """Client connection timeout - chuyển sang AP mode"""
        print(f"⏰ Client connection timeout ({self.client_timeout}s)")
        
        with self.state_lock:
            if self.state == "CLIENT_CONNECTING":
                # Chờ thêm 40s trước khi start AP
                print(f"⏳ Waiting {self.ap_wait_timeout}s before starting AP...")
                
                self.state_timer = threading.Timer(self.ap_wait_timeout, self._transition_to_ap_mode)
                self.state_timer.start()
    
    def _transition_to_ap_mode(self):
        """Phase 2: Chuyển sang AP hotspot mode"""
        with self.state_lock:
            self.state = "AP_STARTING"
        
        print(f"📡 Phase 2: Starting AP hotspot ({self.ap_ssid})")
        
        # Get WiFi interface serial for SSID
        ap_ssid = self._get_wifi_serial_ssid()
        
        # Update WiFi manager với SSID mới
        self.wifi_manager.ssid_prefix_ap = ap_ssid
        self.wifi_manager.con_name = f"{ap_ssid}-AP"
        
        # LED nháy nhanh 0.5s
        if self.wifi_led:
            self.wifi_led.blink(0.25)  # Nháy nhanh hơn cho AP mode
        
        # Start AP in background
        threading.Thread(target=self._start_ap_mode, daemon=True).start()
    
    def _get_wifi_serial_ssid(self):
        """Lấy serial number của WiFi interface làm SSID"""
        try:
            # Thử lấy MAC address của WiFi interface
            wifi_iface = self.wifi_manager.get_wifi_interface()
            if wifi_iface:
                result = subprocess.run([
                    'cat', f'/sys/class/net/{wifi_iface}/address'
                ], capture_output=True, text=True)
                
                if result.returncode == 0:
                    mac = result.stdout.strip().replace(':', '').upper()
                    return f"PICAM-{mac[-6:]}"  # Lấy 6 ký tự cuối MAC
            
            # Fallback
            return self.ap_ssid
            
        except Exception as e:
            print(f"⚠ Error getting WiFi serial: {e}")
            return self.ap_ssid
    
    def _start_ap_mode(self):
        """Start AP hotspot"""
        try:
            success = self.wifi_manager.start_ap_from_ethernet()
            
            if success:
                with self.state_lock:
                    self.state = "AP_ACTIVE"
                
                print("✅ AP hotspot started successfully")
                
                # Bắt đầu đợi client kết nối
                self._wait_for_ap_clients()
            else:
                print("✗ Failed to start AP hotspot")
                self._wifi_failed()
                
        except Exception as e:
            print(f"⚠ AP start error: {e}")
            self._wifi_failed()
    
    def _wait_for_ap_clients(self):
        """Đợi device kết nối tới AP"""
        print(f"⏳ Waiting for clients to connect to AP ({self.ap_connection_timeout}s)")
        
        with self.state_lock:
            self.state = "AP_WAITING"
        
        # Set timer cho AP connection timeout
        self.state_timer = threading.Timer(self.ap_connection_timeout, self._ap_timeout)
        self.state_timer.start()
        
        # Monitor for client connections
        threading.Thread(target=self._monitor_ap_clients, daemon=True).start()
    
    def _monitor_ap_clients(self):
        """Monitor cho client connections"""
        while self.state == "AP_WAITING":
            try:
                if self.wifi_manager.is_client_connected():
                    self._ap_client_connected()
                    break
                time.sleep(2)  # Check every 2 seconds
            except Exception as e:
                print(f"⚠ Error monitoring AP clients: {e}")
                break
    
    def _ap_client_connected(self):
        """Client đã kết nối tới AP"""
        print("✅ Client connected to AP hotspot")
        
        with self.state_lock:
            self.state = "AP_CONNECTED"
        
        # Cancel timeout timer
        if self.state_timer:
            self.state_timer.cancel()
        
        # LED sáng đứng
        if self.wifi_led:
            self.wifi_led.on()
        
        print("💡 WiFi LED: ON (AP client connected)")
    
    def _ap_timeout(self):
        """AP connection timeout - tắt WiFi"""
        print(f"⏰ AP connection timeout ({self.ap_connection_timeout}s) - No clients connected")
        self._wifi_failed()
    
    def _wifi_failed(self):
        """WiFi sequence failed - turn off"""
        print("❌ WiFi sequence failed - Turning OFF")
        self._turn_off_wifi()
    
    def _turn_off_wifi(self):
        """Tắt WiFi và cleanup"""
        print("🔌 Turning OFF WiFi...")
        
        with self.state_lock:
            self.state = "OFF"
        
        try:
            # Stop hotspot nếu đang chạy
            self.wifi_manager.stop_hotspot()
            
            # Disconnect WiFi client
            subprocess.run("sudo nmcli dev disconnect wlan0", shell=True, stderr=subprocess.DEVNULL)
            
            # Turn off WiFi radio
            subprocess.run("sudo nmcli radio wifi off", shell=True)
            
        except Exception as e:
            print(f"⚠ Error turning off WiFi: {e}")
        
        # Turn off LED
        if self.wifi_led:
            self.wifi_led.off()
        
        # Cancel all timers
        self._cancel_timers()
        
        print("✓ WiFi OFF")
    
    def _reset_auto_off_timer(self):
        """Reset auto-off timer (15 phút)"""
        if self.auto_off_timer:
            self.auto_off_timer.cancel()
        
        self.auto_off_timer = threading.Timer(self.auto_off_timeout, self._auto_off_timeout)
        self.auto_off_timer.start()
        
        print(f"⏰ Auto-off timer reset ({self.auto_off_timeout//60} minutes)")
    
    def _auto_off_timeout(self):
        """Auto-off timeout - tắt WiFi sau 15 phút"""
        print("⏰ Auto-off timeout (15 minutes) - Turning OFF WiFi")
        self._turn_off_wifi()
    
    def _cancel_timers(self):
        """Cancel tất cả timers"""
        if self.state_timer:
            self.state_timer.cancel()
            self.state_timer = None
        
        if self.auto_off_timer:
            self.auto_off_timer.cancel()
            self.auto_off_timer = None
    
    def start(self):
        """Start WiFi orchestrator"""
        print("🚀 Starting WiFi Orchestrator...")
        self.running = True
        
        # Ensure WiFi is OFF initially
        self._turn_off_wifi()
        
        # Start reed switch monitoring
        if self.reed_switch:
            self.reed_switch.start()
        
        print("✓ WiFi Orchestrator started (WiFi OFF, waiting for Reed trigger)")
    
    def stop(self):
        """Stop WiFi orchestrator"""
        print("🛑 Stopping WiFi Orchestrator...")
        self.running = False
        
        # Stop reed switch
        if self.reed_switch:
            self.reed_switch.stop()
        
        # Turn off WiFi
        self._turn_off_wifi()
        
        # Cleanup components
        if self.wifi_led:
            self.wifi_led.cleanup()
        
        print("✓ WiFi Orchestrator stopped")
    
    def get_status(self):
        """Get current WiFi status"""
        with self.state_lock:
            return {
                'state': self.state,
                'last_reed_time': self.last_reed_time.isoformat() if self.last_reed_time else None,
                'client_ssid': self.client_ssid,
                'ap_ssid': self._get_wifi_serial_ssid(),
                'running': self.running
            }


def main():
    """Test WiFi Orchestrator"""
    print("=== WiFi Orchestrator Test ===")
    
    # Mock config
    config = {
        'wifi': {
            'ssid': 'PICAM',
            'password': '0123456789',
            'ap_ssid': 'PICAM',
            'ap_password': None
        },
        'gpio': {
            'wifi_led': 13,
            'reed': 17
        }
    }
    
    orchestrator = WiFiOrchestrator(config)
    
    try:
        orchestrator.start()
        
        print("\n=== Controls ===")
        print("Press Enter to simulate Reed switch trigger")
        print("Type 'status' to check current state")
        print("Type 'exit' to quit")
        
        while True:
            cmd = input("\n> ").strip().lower()
            
            if cmd == "exit":
                break
            elif cmd == "status":
                status = orchestrator.get_status()
                print(f"📊 Status: {status}")
            elif cmd == "":
                print("🔔 Simulating Reed switch trigger...")
                orchestrator._reed_triggered()
            else:
                print("Unknown command")
                
    except KeyboardInterrupt:
        print("\nInterrupted")
    finally:
        orchestrator.stop()


if __name__ == "__main__":
    main()