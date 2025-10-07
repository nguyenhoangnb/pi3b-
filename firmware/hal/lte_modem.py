import time 
import serial
import RPi.GPIO as GPIO
import subprocess

class LTEManager:
    def __init__(self, port="/dev/ttyAMA0", baudrate=115200, pwrkey_pin=17):
        self.port = port
        self.baudrate = baudrate
        self.pwrkey_pin = pwrkey_pin

        GPIO.setwarnings(False)
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(self.pwrkey_pin, GPIO.OUT, initial=GPIO.HIGH)
    
    def power_on(self):

        GPIO.output(self.pwrkey_pin, GPIO.HIGH)
        time.sleep(0.2)
        GPIO.output(self.pwrkey_pin, GPIO.LOW)
        time.sleep(2)
        GPIO.output(self.pwrkey_pin, GPIO.HIGH)
        time.sleep(5)

    def power_off(self):
        GPIO.output(self.pwrkey_pin, GPIO.HIGH)
        time.sleep(1.5)
        GPIO.output(self.pwrkey_pin, GPIO.LOW)
        time.sleep(3)
    
    def connect_serial(self):
        try:
            self.ser = serial.Serial(self.port, self.baudrate, timeout=1)
        except Exception as e:
            print("Serial connection failed: ", e)
            self.ser = None
    
    def send_at(self, cmd, timeout=2):
        if not self.ser:
            return None
        
        self.ser.write((cmd + "\r\n").encode())
        time.sleep(timeout)
        resp = self.ser.read_all().decode(errors="ignore")
        return resp
    
    def check_module(self):
        resp = self.send_at("AT")
        if resp and "OK" in resp:
            return True
        else:
            self.power_off()
            self.power_on()
            self.connect_serial()
            resp = self.send_at("AT")
            return "OK" in (resp or "")
    
    def check_network(self):
        result = {
            "sim_ready": False,
            "signal_quality": None,
            "registered": False
        }

        resp_cpin = self.send_at("AT+CPIN?")
        if resp_cpin and "READY" in resp_cpin:
            result["sim_ready"] = True
        
        resp_csq = self.send_at("AT+CSQ")
        if resp_csq:
            try:
                val = int(resp_csq.split(":")[1].split(",")[0].strip())
                result["signal_quality"] = val
            except Exception:
                result["signal_quality"] = None
        resp_creg = self.send_at("AT+CREG?")
        if resp_creg and (",1" in resp_creg or ",5" in resp_creg):
            result["registered"] = True
        
        return result
    
    def connect_ppp(self):
        print("Connecting to LTE (PPP)...")
        try:
            result = subprocess.run(["sudo", "pon", "lte"], check=False)
            # Kiểm tra giao diện ppp0 xuất hiện
            time.sleep(3)
            if subprocess.call("ifconfig ppp0 > /dev/null 2>&1", shell=True) == 0:
                return True
            else:
                return False
        except Exception as e:
            return False

    def disconnect_ppp(self):
        try:
            subprocess.run(["sudo", "poff", "lte"], check=False)
            time.sleep(2)
            if subprocess.call("ifconfig ppp0 > /dev/null 2>&1", shell=True) != 0:
                return True
            else:
                return False
        except Exception as e:
            return False    

def main():
    print("=== LTE Modem Test Suite ===\n")
    
    # Initialize LTE Manager
    lte = LTEManager(port="/dev/ttyAMA0", baudrate=115200, pwrkey_pin=10)
    
    try:
        # Test 1: Serial Connection
        print("1. Testing Serial Connection...")
        lte.connect_serial()
        if lte.ser and lte.ser.is_open:
            print("   ✓ Serial connection established")
        else:
            print("   ✗ Serial connection failed")
            return
        
        # Test 2: Power On
        print("\n2. Testing Power On...")
        lte.power_on()
        print("   ✓ Power on sequence completed")
        
        # Test 3: Module Check
        print("\n3. Testing Module Communication...")
        if lte.check_module():
            print("   ✓ Module responds to AT commands")
        else:
            print("   ✗ Module not responding")
            return
        
        # Test 4: Basic AT Commands
        print("\n4. Testing Basic AT Commands...")
        
        # Check firmware version
        resp = lte.send_at("ATI")
        if resp:
            print(f"   Module Info: {resp.strip()}")
        
        # Check IMEI
        resp = lte.send_at("AT+GSN")
        if resp and "OK" in resp:
            print(f"   IMEI: {resp.strip()}")
        
        # Test 5: Network Status
        print("\n5. Testing Network Status...")
        network_status = lte.check_network()
        
        print(f"   SIM Ready: {'✓' if network_status['sim_ready'] else '✗'}")
        
        if network_status['signal_quality'] is not None:
            signal = network_status['signal_quality']
            if signal == 99:
                print("   Signal Quality: Unknown")
            else:
                signal_dbm = -113 + signal * 2
                print(f"   Signal Quality: {signal} ({signal_dbm} dBm)")
        else:
            print("   Signal Quality: ✗ Unable to read")
        
        print(f"   Network Registered: {'✓' if network_status['registered'] else '✗'}")
        
        # Test 6: Additional Network Info
        print("\n6. Testing Additional Network Info...")
        
        # Check operator
        resp = lte.send_at("AT+COPS?")
        if resp:
            print(f"   Operator: {resp.strip()}")
        
        # Check connection type
        resp = lte.send_at("AT+CGATT?")
        if resp:
            print(f"   GPRS Attached: {resp.strip()}")
        
        # Test 7: PPP Connection (Optional - be careful as this affects network)
        print("\n7. Testing PPP Connection...")
        user_input = input("   Do you want to test PPP connection? (y/n): ")
        
        if user_input.lower() == 'y':
            print("   Attempting PPP connection...")
            if lte.connect_ppp():
                print("   ✓ PPP connection successful")
                
                # Test internet connectivity
                print("   Testing internet connectivity...")
                try:
                    result = subprocess.run(["ping", "-c", "3", "8.8.8.8"], 
                                          capture_output=True, text=True, timeout=15)
                    if result.returncode == 0:
                        print("   ✓ Internet connectivity confirmed")
                    else:
                        print("   ✗ No internet connectivity")
                except subprocess.TimeoutExpired:
                    print("   ✗ Ping timeout")
                except Exception as e:
                    print(f"   ✗ Ping error: {e}")
                
                # Disconnect
                print("   Disconnecting PPP...")
                if lte.disconnect_ppp():
                    print("   ✓ PPP disconnected successfully")
                else:
                    print("   ✗ PPP disconnection failed")
            else:
                print("   ✗ PPP connection failed")
        else:
            print("   Skipped PPP connection test")
        
        print("\n=== Test Summary ===")
        print("All basic tests completed successfully!")
        
    except KeyboardInterrupt:
        print("\n\nTest interrupted by user")
    except Exception as e:
        print(f"\nUnexpected error: {e}")
    finally:
        # Cleanup
        print("\nCleaning up...")
        try:
            if hasattr(lte, 'ser') and lte.ser and lte.ser.is_open:
                lte.ser.close()
                print("Serial connection closed")
        except:
            pass
        
        try:
            GPIO.cleanup()
            print("GPIO cleanup completed")
        except:
            pass

if __name__ == "__main__":
    main()