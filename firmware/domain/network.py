#!/usr/bin/env python3
"""
WiFi Orchestrator - Quản lý WiFi theo logic Reed switch
State machine:
1. OFF → Reed trigger → CLIENT_CONNECTING (0-30s)
2. Nếu fail → AP_STARTING (~40s) → AP_READY
3. Nếu có kết nối (client hoặc AP có client) → ONLINE
4. Hết 15 phút từ lần chạm Reed → OFF
5. Bất kỳ lúc nào quá 70s mà không có kết nối → OFF
"""

import time
import threading
import subprocess
from pathlib import Path
from datetime import datetime, timedelta
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from firmware.hal.wifi import WifiManager
from firmware.hal.gpio_leds import gpioLed
from firmware.hal.reed_switch import ReedSwitch
from firmware.config.config_loader import load
class WiFiOrchestrator:
    def __init__(self, config):
        self.config = config
        self.wifi_config = self.config.get('wifi', {})
        self.gpio_config = self.config.get('gpio', {})
        self.device_config = self.config.get('device', {})
        self.paths_config = self.config.get('paths', {})

        # WiFi settings
        self.client_ssid = self.wifi_config.get('ssid', 'PICAM')
        self.client_password = self.wifi_config.get('password', '0123456789')
        self.ap_ssid = self.wifi_config.get('ap_ssid', 'PICAM')
        self.ap_password = self.wifi_config.get('ap_password', None)

        # GPIO pins
        self.led_wifi = self.gpio_config.get('wifi_led', 13)

        
        # Timing settings theo yêu cầu
        self.client_timeout = 30  # 30s để connect client
        self.ap_wait_timeout = 10  # 10s chờ trước khi start AP (total ~40s)
        self.no_connection_timeout = 70  # 70s không có kết nối → OFF
        self.auto_off_timeout = 180  # 15 phút auto-off
        
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

        # LED blink thread control
        self._led_blink_thread = None
        self._led_blink_stop = threading.Event()
        
        # State management - simplified states
        self.state = "OFF"  # OFF, CLIENT_CONNECTING, AP_STARTING, AP_READY, ONLINE
        self.last_reed_time = None
        self.sequence_start_time = None
        
        # Timers
        self.auto_off_timer = None
        self.client_timeout_timer = None
        self.no_connection_timer = None
        self.running = False
        
        # Thread safety
        self.state_lock = threading.Lock()
        self._monitor_thread = None
        self._stop_monitor = threading.Event()
        
        self._initialize_components()
    
    def _initialize_components(self):
        """Initialize GPIO components"""
        try:
            wifi_led_pin = self.gpio_config.get('wifi_led')
            if wifi_led_pin:
                self.wifi_led = gpioLed(wifi_led_pin)
                print(f"✓ WiFi LED initialized on pin {wifi_led_pin}")
            
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

    # LED management -------------------------------------------------
    def _led_blink_loop(self, interval: float):
        """Background loop that toggles LED until stopped."""
        if not self.wifi_led:
            return

        state_on = False
        stop = self._led_blink_stop
        try:
            while not stop.is_set():
                if state_on:
                    self.wifi_led.on()
                else:
                    self.wifi_led.off()
                state_on = not state_on
                # wait supports early exit
                stop.wait(interval)
        except Exception as e:
            print(f"⚠ LED blink loop error: {e}")

    def _start_led_blinking(self, interval: float = 0.5):
        """Start continuous blinking at given interval (seconds)."""
        if not self.wifi_led:
            return

        # stop existing blink thread if any
        self._stop_led_blinking()
        self._led_blink_stop.clear()
        self._led_blink_thread = threading.Thread(
            target=self._led_blink_loop, args=(interval,), daemon=True
        )
        self._led_blink_thread.start()
        print(f"⏳ WiFi LED: started blinking ({interval}s)")

    def _stop_led_blinking(self):
        """Stop continuous blinking."""
        if not self.wifi_led:
            return

        try:
            if self._led_blink_thread and self._led_blink_thread.is_alive():
                self._led_blink_stop.set()
                # give it a short moment to finish
                self._led_blink_thread.join(timeout=0.5)
        except Exception:
            pass
        self._led_blink_thread = None

    
    def _reed_triggered(self):
        """Reed switch callback - start WiFi sequence"""
        print("🔔 Reed switch triggered - Starting WiFi sequence")
        
        with self.state_lock:
            self.last_reed_time = datetime.now()
            current_state = self.state
        
        # Reset auto-off timer (15 phút)
        self._reset_auto_off_timer()
        
        # Start WiFi sequence nếu đang OFF
        if current_state == "OFF":
            threading.Thread(target=self._start_wifi_sequence, daemon=True).start()
        else:
            print("ℹ WiFi already active, resetting 15-min timer")
    
    def _start_wifi_sequence(self):
        """Bắt đầu sequence: OFF → CLIENT_CONNECTING"""
        print("🚀 Starting WiFi sequence...")
        
        with self.state_lock:
            self.sequence_start_time = datetime.now()
        
        # Cancel old timers
        self._cancel_all_timers()
        
        # Start 70s no-connection timeout
        self._start_no_connection_timer()
        
        # Transition to CLIENT_CONNECTING
        self._transition_to_client_connecting()
    
    def _transition_to_client_connecting(self):
        """Phase 1: CLIENT_CONNECTING (0-30s)"""
        with self.state_lock:
            self.state = "CLIENT_CONNECTING"
        
        print(f"📱 State: CLIENT_CONNECTING → Trying to connect to {self.client_ssid} (30s timeout)")
        
        
        
        # Start client connection
        threading.Thread(target=self._try_client_connection, daemon=True).start()
        
        # Set 30s timeout
        self.client_timeout_timer = threading.Timer(self.client_timeout, self._client_connection_timeout)
        self.client_timeout_timer.start()
    
    def _try_client_connection(self):
        """Thử kết nối WiFi client"""
        try:
            success = self.wifi_manager.start_client()
            # LED: handled by blink thread; ensure we're blinking while waiting
            # (no-op here)
            if success:
                print("✅ Client connection successful!")
                self._transition_to_online("CLIENT")
            else:
                print("✗ Client connection failed")
                
        except Exception as e:
            print(f"⚠ Client connection error: {e}")
    
    def _client_connection_timeout(self):
        """30s timeout → chuyển sang AP mode"""
        
        with self.state_lock:
            current_state = self.state
        # keep blinking while waiting; nothing to do here
        if current_state == "CLIENT_CONNECTING":
            print(f"⏰ Client connection timeout (30s)")
            # Chờ thêm 10s trước khi start AP (total ~40s)
            print(f"⏳ Waiting {self.ap_wait_timeout}s before starting AP...")
            time.sleep(self.ap_wait_timeout)
            self._transition_to_ap_starting()
    
    def _transition_to_ap_starting(self):
        """Phase 2: AP_STARTING → AP_READY"""
        with self.state_lock:
            self.state = "AP_STARTING"
        
        print(f"📡 State: AP_STARTING → Starting hotspot")
        
        # Ensure blink thread is running (faster blink for AP_STARTING)
        self._start_led_blinking(0.25)
        
        # Get WiFi serial SSID
        ap_ssid = self._get_wifi_serial_ssid()
        self.wifi_manager.ssid_prefix_ap = ap_ssid
        self.wifi_manager.con_name = f"{ap_ssid}-AP"
        
        # Start AP
        threading.Thread(target=self._start_ap_mode, daemon=True).start()
    
    def _get_wifi_serial_ssid(self):
        """Lấy MAC address làm SSID"""
        try:
            wifi_iface = self.wifi_manager.get_wifi_interface()
            if wifi_iface:
                result = subprocess.run([
                    'cat', f'/sys/class/net/{wifi_iface}/address'
                ], capture_output=True, text=True)
                
                if result.returncode == 0:
                    mac = result.stdout.strip().replace(':', '').upper()
                    return f"PICAM-{mac[-6:]}"
            
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
                    self.state = "AP_READY"
                
                print("✅ State: AP_READY → Hotspot active, waiting for clients")
                
                # Start monitoring for clients
                # AP_READY should blink slowly while waiting for clients
                self._start_led_blinking(0.5)
                self._start_ap_client_monitoring()
            else:
                print("✗ Failed to start AP hotspot")
                # 70s timer sẽ tự động tắt nếu không có kết nối
                
        except Exception as e:
            print(f"⚠ AP start error: {e}")
    
    def _start_ap_client_monitoring(self):
        """Monitor AP clients để chuyển sang ONLINE"""
        self._stop_monitor.clear()
        
        def monitor():
            print("🕵️ Monitoring AP clients...")
            while not self._stop_monitor.is_set():
                with self.state_lock:
                    current_state = self.state
                # LED handled by blink thread while waiting
                if current_state not in ["AP_READY", "ONLINE"]:
                    break
                
                try:
                    if self.wifi_manager.is_client_connected():
                        if current_state == "AP_READY":
                            self._transition_to_online("AP")
                    else:
                        # Nếu đang ONLINE nhưng mất kết nối
                        if current_state == "ONLINE":
                            self._transition_to_ap_ready_waiting()
                    
                    time.sleep(2)
                except Exception as e:
                    print(f"⚠ Error monitoring AP clients: {e}")
                    break
        
        self._monitor_thread = threading.Thread(target=monitor, daemon=True)
        self._monitor_thread.start()
    
    def _transition_to_online(self, source):
        """Transition to ONLINE state"""
        with self.state_lock:
            self.state = "ONLINE"
        # Stop blinking and set steady ON
        self._stop_led_blinking()
        if self.wifi_led:
            self.wifi_led.on()
        
        print(f"✅ State: ONLINE (via {source})")
        
        # Cancel 70s no-connection timer
        if self.no_connection_timer:
            self.no_connection_timer.cancel()
            self.no_connection_timer = None
        
        print("💡 WiFi LED: ON (connected)")
        
        # Nếu từ AP, tiếp tục monitor
        if source == "AP" and not (self._monitor_thread and self._monitor_thread.is_alive()):
            self._start_ap_client_monitoring()
    
    def _transition_to_ap_ready_waiting(self):
        """Quay lại AP_READY khi client disconnect"""
        print("⚠️ Client disconnected from AP")
        
        with self.state_lock:
            self.state = "AP_READY"
        
        # LED should blink slowly while waiting
        self._start_led_blinking(0.5)
        print("💡 WiFi LED: BLINK (waiting for client)")
    
    def _start_no_connection_timer(self):
        """Start 70s timer - tự động OFF nếu không có kết nối"""
        if self.no_connection_timer:
            self.no_connection_timer.cancel()
        
        self.no_connection_timer = threading.Timer(
            self.no_connection_timeout, 
            self._no_connection_timeout
        )
        self.no_connection_timer.start()
        
        print(f"⏰ No-connection timer started (70s) - WiFi will turn OFF if no connection")
    
    def _no_connection_timeout(self):
        """70s timeout - không có kết nối → OFF"""
        with self.state_lock:
            current_state = self.state
        
        if current_state != "ONLINE":
            print(f"⏰ No-connection timeout (70s) - State: {current_state}")
            print("❌ No connection established within 70s → Turning OFF")
            self._turn_off_wifi()
    
    def _reset_auto_off_timer(self):
        """Reset auto-off timer (15 phút từ lần chạm Reed cuối)"""
        if self.auto_off_timer:
            self.auto_off_timer.cancel()
        
        self.auto_off_timer = threading.Timer(self.auto_off_timeout, self._auto_off_timeout)
        self.auto_off_timer.start()
        
        print(f"⏰ Auto-off timer RESET → WiFi will turn OFF after 15 minutes")
    
    def _auto_off_timeout(self):
        """15 phút timeout → OFF"""
        print("🕐 ===== AUTO-OFF TIMEOUT =====")
        print("⏰ 15 minutes elapsed since last Reed trigger")
        print("🔌 Turning OFF WiFi...")
        self._turn_off_wifi()
    
    def _turn_off_wifi(self):
        """Tắt WiFi hoàn toàn và cleanup"""
        print("🔌 Turning OFF WiFi completely...")
        
        with self.state_lock:
            self.state = "OFF"
            self.sequence_start_time = None
        
        # Stop monitoring
        self._stop_monitor.set()
        
        try:
            # Stop hotspot
            print("🛑 Stopping hotspot...")
            self.wifi_manager.stop_hotspot()
            
            # Disconnect WiFi client
            wifi_iface = self.wifi_manager.get_wifi_interface()
            if wifi_iface:
                print(f"🔌 Disconnecting WiFi client ({wifi_iface})...")
                subprocess.run(f"sudo nmcli dev disconnect {wifi_iface}", 
                             shell=True, stderr=subprocess.DEVNULL)
            
            # Turn off WiFi radio
            print("📻 Turning OFF WiFi radio...")
            subprocess.run("sudo nmcli radio wifi off", shell=True)
            
        except Exception as e:
            print(f"⚠ Error turning off WiFi: {e}")
        
        # Turn off LED
        # Stop blinking and turn off LED
        self._stop_led_blinking()
        if self.wifi_led:
            self.wifi_led.off()
            print("💡 WiFi LED: OFF")
        
        # Cancel all timers
        self._cancel_all_timers()
        
        print("✓ WiFi completely OFF - State: OFF")
    
    def _cancel_all_timers(self):
        """Cancel tất cả timers"""
        if self.client_timeout_timer:
            self.client_timeout_timer.cancel()
            self.client_timeout_timer = None
        
        if self.no_connection_timer:
            self.no_connection_timer.cancel()
            self.no_connection_timer = None
        
        if self.auto_off_timer:
            self.auto_off_timer.cancel()
            self.auto_off_timer = None
        
        self._stop_monitor.set()
        # ensure led blinking stopped when cancelling timers
        self._stop_led_blinking()
    
    def start(self):
        """Start WiFi orchestrator"""
        print("🚀 Starting WiFi Orchestrator...")
        self.running = True
        
        # Ensure WiFi is OFF initially
        self._turn_off_wifi()
        
        # Start reed switch monitoring
        if self.reed_switch:
            self.reed_switch.start()
        
        print("✓ WiFi Orchestrator started")
        print("📋 State Machine:")
        print("   1. OFF → Reed → CLIENT_CONNECTING (0-30s)")
        print("   2. Fail → AP_STARTING (~40s) → AP_READY")
        print("   3. Connection → ONLINE")
        print("   4. 15 min timeout → OFF")
        print("   5. 70s no connection → OFF")
    
    def stop(self):
        """Stop WiFi orchestrator"""
        print("🛑 Stopping WiFi Orchestrator...")
        self.running = False
        
        if self.reed_switch:
            self.reed_switch.stop()
        
        self._turn_off_wifi()
        
        if self.wifi_led:
            self.wifi_led.cleanup()
        
        print("✓ WiFi Orchestrator stopped")
    
    def get_auto_off_remaining(self):
        """Tính thời gian còn lại của auto-off timer"""
        if not self.last_reed_time:
            return 0
        
        elapsed = (datetime.now() - self.last_reed_time).total_seconds()
        remaining = max(0, self.auto_off_timeout - elapsed)
        return remaining
    
    def get_sequence_elapsed(self):
        """Tính thời gian đã trôi qua từ khi bắt đầu sequence"""
        if not self.sequence_start_time:
            return 0
        
        elapsed = (datetime.now() - self.sequence_start_time).total_seconds()
        return elapsed
    
    def get_status(self):
        """Get current WiFi status"""
        with self.state_lock:
            remaining_seconds = self.get_auto_off_remaining()
            remaining_minutes = remaining_seconds / 60
            sequence_elapsed = self.get_sequence_elapsed()
            
            return {
                'state': self.state,
                'last_reed_time': self.last_reed_time.isoformat() if self.last_reed_time else None,
                'sequence_elapsed_seconds': round(sequence_elapsed, 1),
                'client_ssid': self.client_ssid,
                'ap_ssid': self._get_wifi_serial_ssid(),
                'running': self.running,
                'auto_off_remaining_minutes': round(remaining_minutes, 1),
                'auto_off_active': remaining_seconds > 0
            }


def main():
    """Test WiFi Orchestrator"""
    print("=== 🧭 WiFi Orchestrator Simulation ===")
    config_file = Path(__file__).parent.parent / 'config' / 'device_full.yaml'
    config_data = load(config_file)
    orchestrator = WiFiOrchestrator(config_data)
    
    try:
        orchestrator.start()
        
        print("\n=== Controls ===")
        print("1 → Simulate Reed trigger")
        print("s → Show status")
        print("x → Exit\n")

        while True:
            cmd = input("> ").strip().lower()
            
            if cmd == "x":
                print("👋 Exiting...")
                break

            elif cmd == "1":
                print("🔔 Simulating Reed trigger...")
                orchestrator._reed_triggered()

            elif cmd == "s":
                status = orchestrator.get_status()
                print(f"\n📊 WiFi Status:")
                print(f"   🔸 State: {status['state']}")
                print(f"   🔸 Sequence elapsed: {status['sequence_elapsed_seconds']}s")
                print(f"   🔸 Client SSID: {status['client_ssid']}")
                print(f"   🔸 AP SSID: {status['ap_ssid']}")
                if status['auto_off_active']:
                    print(f"   ⏰ Auto-off in: {status['auto_off_remaining_minutes']} min")
                else:
                    print(f"   ⏰ Auto-off: Inactive")
                print()

            else:
                print("⚠ Unknown command")
                
    except KeyboardInterrupt:
        print("\n🛑 Interrupted")
    finally:
        orchestrator.stop()

if __name__ == "__main__":
    main()