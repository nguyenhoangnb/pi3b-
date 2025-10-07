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

    def _run_cmd(self, cmd):
        return os.system(cmd) == 0

    def check_wifi_connected(self):
        return os.system("ping -c 1 -W 2 8.8.8.8 > /dev/null 2>&1") == 0
    
    def start_client(self):
        if not hasattr(self, 'wifi_interface'):
            self.wifi_interface = self.get_wifi_interface()
        
        if not self.wifi_interface:
            print("✗ Error: No WiFi interface found")
            return False
        
        print(f"Using WiFi interface: {self.wifi_interface}")
        
        self._run_cmd("sudo rfkill unblock wifi")
        self._run_cmd(f"sudo ifconfig {self.wifi_interface} up")
        
        # Try to connect
        connect_cmd = f"sudo nmcli dev wifi connect '{self.client_ssid}' password '{self.client_pass}' ifname {self.wifi_interface}"
        print(f"Connecting to {self.client_ssid}...")
        
        if self._run_cmd(connect_cmd):
            print("✓ Connection command successful")
            return True
        else:
            print("✗ Connection command failed")
            return False
        
    def start_ap_from_ethernet(self):
        """
        🧭 Start Wi-Fi hotspot that shares Internet from Ethernet (or LTE)
        """
        import time

        print("🌐 Checking for Ethernet connection...")
        eth_iface = self._run_cmd("nmcli dev | grep ethernet | grep connected | awk '{print $1}'")
        if not eth_iface:
            print("⚠ No active Ethernet found. Will start AP without Internet.")
            eth_iface = None
        else:
            print(f"✓ Ethernet interface: {eth_iface.strip()}")

        # Detect Wi-Fi interface
        if not hasattr(self, 'wifi_interface'):
            self.wifi_interface = self.get_wifi_interface()

        if not self.wifi_interface:
            print("✗ Error: No WiFi interface found")
            return False

        print(f"📡 Using WiFi interface: {self.wifi_interface}")

        # Disconnect Wi-Fi
        self._run_cmd(f"sudo nmcli dev disconnect {self.wifi_interface}")
        time.sleep(1)

        # Delete any old AP connections
        self._run_cmd(f"sudo nmcli con delete '{self.con_name}' 2>/dev/null")
        self._run_cmd("sudo nmcli con delete 'Hotspot' 2>/dev/null")

        # Create open hotspot
        print("🛠 Creating hotspot connection...")
        cmds = [
            f"sudo nmcli con add type wifi ifname {self.wifi_interface} mode ap con-name '{self.con_name}' ssid '{self.ssid_prefix_ap}'",
            f"sudo nmcli con modify '{self.con_name}' 802-11-wireless.band bg",
            f"sudo nmcli con modify '{self.con_name}' ipv4.method shared",
            f"sudo nmcli con modify '{self.con_name}' ipv4.addresses 192.168.4.1/24",
            f"sudo nmcli con modify '{self.con_name}' wifi-sec.key-mgmt none"
        ]

        for cmd in cmds:
            self._run_cmd(cmd)

        # Activate connection
        print("🚀 Activating hotspot...")
        if not self._run_cmd(f"sudo nmcli con up '{self.con_name}'"):
            print("✗ Failed to activate hotspot")
            return False

        time.sleep(3)
        print("✅ Hotspot is active")

        # NAT forwarding (share Internet if Ethernet or LTE exists)
        if eth_iface:
            print(f"🔁 Setting up NAT (share {eth_iface.strip()} → {self.wifi_interface})...")
            self._run_cmd("sudo sysctl -w net.ipv4.ip_forward=1")
            self._run_cmd(f"sudo iptables -t nat -A POSTROUTING -o {eth_iface.strip()} -j MASQUERADE")
            self._run_cmd(f"sudo iptables -A FORWARD -i {eth_iface.strip()} -o {self.wifi_interface} -m state --state RELATED,ESTABLISHED -j ACCEPT")
            self._run_cmd(f"sudo iptables -A FORWARD -i {self.wifi_interface} -o {eth_iface.strip()} -j ACCEPT")
            print("✓ NAT rules applied (clients should have Internet)")

        return True
    def is_client_connected():
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

def main():
    print("=== 🌐 WiFi Manager Utility ===")
    wifi = WifiManager(
        client_ssid="NQT", 
        client_pass="11345678", 
        ssid_prefix_ap="PICAM"
    )

    print("✓ WiFiManager initialized")

    while True:
        print("\n===== MENU =====")
        print("1. Start Client (connect to WiFi)")
        print("2. Start Hotspot (share from Ethernet if available)")
        print("3. Check Internet Connectivity")
        print("4. Stop Hotspot (remove AP)")
        print("5. Exit")
        print("================")
        choice = input("Select option (1–5): ").strip()

        if choice == "1":
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

        elif choice == "2":
            print("\n📡 Starting Access Point mode...")
            if wifi.start_ap_from_ethernet():
                print("✅ Hotspot active! You can connect devices to it.")
            else:
                print("✗ Failed to start hotspot.")

        elif choice == "3":
            print("\n🔎 Checking Internet connectivity...")
            if wifi.check_wifi_connected():
                print("✅ Internet is reachable (ping 8.8.8.8 OK)")
            else:
                print("✗ No Internet connection.")

        elif choice == "4":
            print("\n🛑 Stopping hotspot...")
            wifi._run_cmd(f"sudo nmcli con down '{wifi.con_name}'")
            wifi._run_cmd(f"sudo nmcli con delete '{wifi.con_name}' 2>/dev/null")
            print("✓ Hotspot stopped and removed.")

        elif choice == "5":
            print("\n👋 Exiting WiFi Manager.")
            break

        else:
            print("⚠ Invalid selection. Please choose 1–5.")

if __name__ =="__main__":
    main()