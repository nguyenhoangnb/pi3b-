import os
import time
import subprocess 
import re

class WifiManager:
    def __init__(self, client_ssid="PICAM", client_pass="0123456789", ssid_prefix_ap="PICAM-"):
        self.client_ssid = client_ssid
        self.client_pass = client_pass
        self.ssid_prefix_ap = ssid_prefix_ap
        self.con_name = f"{self.ssid_prefix_ap}-AP"
        self.ap_password = "12345678"
        self.wifi_interface = None
        self.turn_off_wifi()
    def _run_cmd(self, cmd):
        return os.system(cmd) == 0

    def get_wifi_interface(self):
        try:
            output = subprocess.check_output(
                "nmcli -t -f DEVICE,TYPE dev | grep ':wifi' | cut -d: -f1",
                shell=True, text=True
            ).strip().splitlines()
            if not output:
                print("✗ No WiFi interface found.")
                return None

            # Filter out p2p-dev interfaces
            for iface in output:
                if not iface.startswith("p2p-dev-"):
                    print(f"✅ WiFi interface detected: {iface}")
                    return iface
            print("✗ Only found p2p-dev-* interfaces (not usable).")
            return None
        except subprocess.CalledProcessError:
            print("✗ Failed to detect WiFi interface.")
            return None

    def turn_off_wifi(self):
        """
        🚫 Turn off WiFi radio if it's currently enabled.
        """
        print("📶 Checking WiFi radio status before turning off...")
        try:
            result = subprocess.check_output("nmcli radio wifi", shell=True, text=True).strip()
            if result == "enabled":
                print("🔻 WiFi is enabled. Turning it off...")
                if self._run_cmd("sudo nmcli radio wifi off"):
                    time.sleep(1)
                    print("✅ WiFi turned off successfully")
                    return True
                else:
                    print("✗ Failed to turn off WiFi")
                    return False
            else:
                print("⚙️ WiFi is already disabled")
                return True
        except subprocess.CalledProcessError:
            print("✗ Failed to check WiFi status")
            return False
    


    def turn_on_wifi(self):
        """
        Turn on WiFi radio if it's disabled.
        """
        print("📶 Checking WiFi radio status...")
        try:
            # Check if WiFi is enabled
            result = subprocess.check_output("nmcli radio wifi", shell=True, text=True).strip()
            if result == "disabled":
                print("🔄 WiFi is disabled. Turning it on...")
                if self._run_cmd("sudo nmcli radio wifi on"):
                    time.sleep(2)
                    print("✅ WiFi turned on successfully")
                    return True
                else:
                    print("✗ Failed to turn on WiFi")
                    return False
            else:
                print("✅ WiFi is already enabled")
                return True
        except subprocess.CalledProcessError:
            print("✗ Failed to check WiFi status")
            return False

    def check_wifi_connected(self):
        return os.system("ping -c 1 -W 2 8.8.8.8 > /dev/null 2>&1") == 0
    
    def start_client(self):
        # Ensure WiFi is turned on first
        if not self.turn_on_wifi():
            print("✗ Cannot proceed - WiFi radio is off")
            return False
            
        print(f"🔌 Connecting to {self.client_ssid}...")
        connect_cmd = f"sudo nmcli dev wifi connect '{self.client_ssid}' password '{self.client_pass}'"
        if not self._run_cmd(connect_cmd):
            print("✗ Connection command failed")
            return False
        
        # Verify connection
        ssid_now = subprocess.getoutput("iwgetid -r")
        if ssid_now == self.client_ssid:
            print(f"✅ Connected to {ssid_now}")
            return True
        else:
            print("⚠️ Connection not established")
            return False
        
    def start_ap_from_ethernet(self):
        """
        🧭 Start Wi-Fi hotspot that shares Internet from Ethernet (or LTE)
        """
        # Ensure WiFi is turned on first
        if not self.turn_on_wifi():
            print("✗ Cannot proceed - WiFi radio is off")
            return False
            
        print("🌐 Checking for Ethernet connection...")
        eth_iface = subprocess.getoutput("nmcli dev | grep ethernet | grep connected | awk '{print $1}'").strip()
        if not eth_iface:
            print("⚠ No active Ethernet found. Will start AP without Internet.")
            eth_iface = None
        else:
            print(f"✓ Ethernet interface: {eth_iface}")

        # Detect Wi-Fi interface
        if not self.wifi_interface:
            self.wifi_interface = self.get_wifi_interface()

        if not self.wifi_interface:
            print("✗ Error: No WiFi interface found")
            return False

        print(f"📡 Using WiFi interface: {self.wifi_interface}")

        # Disconnect Wi-Fi if active
        subprocess.run(f"sudo nmcli dev disconnect {self.wifi_interface}", shell=True, stderr=subprocess.DEVNULL)
        time.sleep(1)

        # Delete old AP configs
        subprocess.run(f"sudo nmcli con delete '{self.con_name}' 2>/dev/null", shell=True)
        subprocess.run("sudo nmcli con delete 'Hotspot' 2>/dev/null", shell=True)

        # Generate SSID
        ssid_name = f"{self.ssid_prefix_ap}{os.getpid() % 1000}"

        print(f"🛠 Creating hotspot connection (SSID: {ssid_name})...")
        cmds = [
            f"sudo nmcli con add type wifi ifname {self.wifi_interface} mode ap con-name '{self.con_name}' ssid '{ssid_name}'",
            f"sudo nmcli con modify '{self.con_name}' 802-11-wireless.band bg",
            f"sudo nmcli con modify '{self.con_name}' ipv4.method shared",
            f"sudo nmcli con modify '{self.con_name}' ipv4.addresses 192.168.4.1/24",
            f"sudo nmcli con modify '{self.con_name}' wifi-sec.key-mgmt wpa-psk",
            f"sudo nmcli con modify '{self.con_name}' wifi-sec.psk '{self.ap_password}'"
        ]

        for cmd in cmds:
            subprocess.run(cmd, shell=True)

        print("🚀 Activating hotspot...")
        # Explicitly specify the interface when bringing up the connection
        activate_cmd = f"sudo nmcli con up '{self.con_name}' ifname {self.wifi_interface}"
        if subprocess.call(activate_cmd, shell=True) != 0:
            print("✗ Failed to activate hotspot")
            return False

        time.sleep(3)
        print("✅ Hotspot is active")

        # NAT forwarding (optional)
        if eth_iface:
            print(f"🔁 Setting up NAT (share {eth_iface} → {self.wifi_interface})...")
            subprocess.run("sudo sysctl -w net.ipv4.ip_forward=1", shell=True)
            subprocess.run(f"sudo iptables -t nat -A POSTROUTING -o {eth_iface} -j MASQUERADE", shell=True)
            subprocess.run(f"sudo iptables -A FORWARD -i {eth_iface} -o {self.wifi_interface} -m state --state RELATED,ESTABLISHED -j ACCEPT", shell=True)
            subprocess.run(f"sudo iptables -A FORWARD -i {self.wifi_interface} -o {eth_iface} -j ACCEPT", shell=True)
            print("✓ NAT rules applied (clients should have Internet)")

        return True
    
    def is_client_connected(self):
        """
        Return True if any device is connected to the hotspot (based on ARP table).
        """
        try:
            result = subprocess.check_output(["arp", "-n"], text=True)
            for line in result.splitlines():
                match = re.search(r"(\d+\.\d+\.\d+\.\d+)\s+\S+\s+([0-9a-f:]{17})", line, re.IGNORECASE)
                if match:
                    # Found at least one connected client
                    return True
            return False
        except subprocess.CalledProcessError:
            return False

    def stop_hotspot(self):
        """
        Stop and remove the hotspot connection.
        """
        print("🛑 Stopping hotspot...")
        self._run_cmd(f"sudo nmcli con down '{self.con_name}'")
        self._run_cmd(f"sudo nmcli con delete '{self.con_name}' 2>/dev/null")
        print("✓ Hotspot stopped and removed.")

def main():
    print("=== 🌐 WiFi Manager Utility ===")
    wifi = WifiManager(
        client_ssid="bytehome 5GHz", 
        client_pass="Toilatoi1994", 
        ssid_prefix_ap="PICAM"
    )

    print("✓ WiFiManager initialized")

    while True:
        print("\n===== MENU =====")
        print("1. Turn On WiFi")
        print("2. Start Client (connect to WiFi)")
        print("3. Start Hotspot (share from Ethernet if available)")
        print("4. Check Internet Connectivity")
        print("5. Stop Hotspot (remove AP)")
        print("6. Exit")
        print("================")
        choice = input("Select option (1–6): ").strip()

        if choice == "1":
            print("\n📶 Turning on WiFi...")
            wifi.turn_on_wifi()

        elif choice == "2":
            print("\n🔌 Switching to client mode...")
            ok = wifi.start_client()
            if ok:
                print("⏳ Waiting for connection...")
                time.sleep(5)
                if wifi.check_wifi_connected():
                    print("✅ Connected to Internet!")
                else:
                    print("⚠ Connected to WiFi but no Internet access.")
            else:
                print("✗ Failed to start client mode.")

        elif choice == "3":
            print("\n📡 Starting Access Point mode...")
            if wifi.start_ap_from_ethernet():
                print("✅ Hotspot active! You can connect devices to it.")
            else:
                print("✗ Failed to start hotspot.")

        elif choice == "4":
            print("\n🔎 Checking Internet connectivity...")
            if wifi.check_wifi_connected():
                print("✅ Internet is reachable (ping 8.8.8.8 OK)")
            else:
                print("✗ No Internet connection.")

        elif choice == "5":
            wifi.stop_hotspot()

        elif choice == "6":
            print("\n👋 Exiting WiFi Manager.")
            break

        else:
            print("⚠ Invalid selection. Please choose 1–6.")

if __name__ == "__main__":
    main()