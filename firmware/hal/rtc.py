import smbus2
import time
from datetime import datetime
class rtcModule:
    def __init__(self, bus=1, address=0x68):
        self.address = address
        try:
            self.bus = smbus2.SMBus(bus)
        except FileNotFoundError:
            raise RuntimeError(f"I2C bus not found")
    
    def is_connected(self):
        try:
            self.bus.read_byte(self.address)
            return True
        except(OSError, IOError):
            return False

    def _bcd_to_dec(self, bcd):
        return (bcd // 16) * 10 + (bcd  % 16)
    
    def _dec_to_bcd(self, dec):
        return (dec // 10) * 16 + (dec % 10)
    
    def read_time(self):
        data = self.bus.read_i2c_block_data(self.address, 0x00, 7)
        second = self._bcd_to_dec(data[0] & 0x7F)
        minute = self._bcd_to_dec(data[1])
        hour = self._bcd_to_dec(data[2] & 0x3F)
        day = self._bcd_to_dec(data[4])
        month = self._bcd_to_dec(data[5] & 0x1F)
        year = self._bcd_to_dec(data[6] + 2000)
        return datetime(year, month, day, hour, minute, second)
    
    def set_time(self, dt):
        if isinstance(dt, str):
            dt = datetime.strptime(dt, "%Y-%m-%d %H:%M:%S")
            data = [
                self._dec_to_bcd(dt.second),
                self._dec_to_bcd(dt.minute),
                self._dec_to_bcd(dt.hour),
                self._dec_to_bcd(dt.isoweekday()),
                self._dec_to_bcd(dt.day),
                self._dec_to_bcd(dt.month),
                self._dec_to_bcd(dt.year - 2000),
            ]
            self.bus.write_i2c_block_data(self.address, 0x00, data)
    
    def sync_to_system(self):
        dt = self.read_time()
        timestr = dt.strftime("%Y-%m-%d %H:%M:%S")
        import os
        os.system(f'sudo date -s "{timestr}"')
    
    def close(self):
        self.bus.close()

def main():
    print("=== RTC Module Simple Test ===")
    
    try:
        # Initialize RTC
        rtc = rtcModule()
        print("✓ RTC module initialized")
        
        # Test connection
        if rtc.is_connected():
            print("✓ RTC is connected")
        else:
            print("✗ RTC not connected")
            return
        
        # Read current time
        print("\nReading RTC time...")
        rtc_time = rtc.read_time()
        print(f"RTC Time: {rtc_time}")
        
        # Compare with system time
        system_time = datetime.now()
        print(f"System Time: {system_time}")
        
        time_diff = abs((rtc_time - system_time).total_seconds())
        print(f"Time difference: {time_diff:.2f} seconds")
        
        # Test setting time (optional)
        choice = input("\nSet RTC to current system time? (y/n): ")
        if choice.lower() == 'y':
            rtc.set_time(system_time)
            print("✓ RTC time updated")
            
            # Verify
            new_rtc_time = rtc.read_time()
            print(f"New RTC Time: {new_rtc_time}")
        
        print("\n✓ RTC test completed!")
        
    except Exception as e:
        print(f"✗ Error: {e}")
    finally:
        try:
            rtc.close()
            print("✓ RTC connection closed")
        except:
            pass

if __name__ == "__main__":
    main()