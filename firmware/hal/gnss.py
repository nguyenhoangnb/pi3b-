
"""Simple GNSS (GPS) module for USB NEO-6M/7M/8M receivers.

This implementation reads NMEA sentences from a serial device in a
background thread and extracts basic position information (latitude,
longitude, fix quality, satellites, hdop, timestamp). It is defensive
and falls back to a dummy implementation when pyserial is not present
or no device can be opened.
"""
from datetime import datetime
import threading
import time
import glob

try:
    import serial
    from serial.serialutil import SerialException
except Exception:
    serial = None
    SerialException = Exception


def _nmea_to_decimal(degmin, hemi):
    """Convert NMEA lat/lon (ddmm.mmmm) + hemisphere to decimal degrees."""
    try:
        if not degmin:
            return None
        # split degrees and minutes
        if '.' not in degmin:
            return None
        parts = degmin.split('.')
        head = parts[0]
        # degrees are the first 2 (lat) or 3 (lon) digits depending on length
        if len(head) <= 4:  # lat typically DDMM
            deg_len = 2
        else:
            deg_len = 3
        degrees = int(head[:deg_len])
        minutes = float(head[deg_len:] + '.' + parts[1])
        dec = degrees + minutes / 60.0
        if hemi in ('S', 'W'):
            dec = -dec
        return dec
    except Exception:
        return None


class GNSSModule:
    def __init__(self, port=None, baudrate=9600, timeout=1.0):
        """Create GNSSModule. If port is None, try common serial device paths.

        Args:
            port: serial device path or None to auto-detect
            baudrate: serial baud rate (default 9600 for many NEO modules)
            timeout: read timeout in seconds
        """
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout

        self._serial = None
        self._thread = None
        self._stop = threading.Event()
        self._lock = threading.Lock()

        # latest parsed data
        self._data = {
            'fix_quality': 0,
            'latitude': None,
            'longitude': None,
            'num_sats': 0,
            'hdop': None,
            'timestamp': None,
        }

        # Try to open serial and start reader thread
        self._open_and_start()

    def _find_ports(self):
        # common device glob patterns for USB serial GNSS receivers
        patterns = ['/dev/ttyUSB*', '/dev/ttyACM*', '/dev/serial/by-id/*', '/dev/ttyAMA*', '/dev/serial0']
        ports = []
        for p in patterns:
            ports.extend(glob.glob(p))
        # remove duplicates while preserving order
        seen = set()
        out = []
        for p in ports:
            if p not in seen:
                seen.add(p)
                out.append(p)
        return out

    def _open_and_start(self):
        if serial is None:
            # pyserial not installed — fallback dummy
            return

        ports = [self.port] if self.port else self._find_ports()
        for p in ports:
            if p is None:
                continue
            try:
                s = serial.Serial(p, baudrate=self.baudrate, timeout=self.timeout)
                # flush
                time.sleep(0.1)
                s.reset_input_buffer()
                self._serial = s
                # start reader thread
                self._thread = threading.Thread(target=self._reader_loop, daemon=True)
                self._thread.start()
                return
            except SerialException:
                continue
            except Exception:
                continue

    def _reader_loop(self):
        if not self._serial:
            return
        ser = self._serial
        while not self._stop.is_set():
            try:
                line = ser.readline()
                if not line:
                    continue
                try:
                    s = line.decode('ascii', errors='ignore').strip()
                except Exception:
                    continue
                if not s.startswith('$'):
                    continue
                parts = s.split(',')
                tag = parts[0][3:] if len(parts[0]) > 3 else parts[0]
                # Handle GGA (fix data) and RMC (recommended minimum)
                if tag.endswith('GGA') or tag == 'GGA':
                    # $--GGA,time,lat,NS,lon,EW,quality,num_sats,hdop,alt,...
                    try:
                        lat = parts[2]
                        ns = parts[3]
                        lon = parts[4]
                        ew = parts[5]
                        quality = int(parts[6]) if parts[6] else 0
                        num_sats = int(parts[7]) if parts[7] else 0
                        hdop = float(parts[8]) if parts[8] else None
                        ts = parts[1]
                        latf = _nmea_to_decimal(lat, ns)
                        lonf = _nmea_to_decimal(lon, ew)
                        with self._lock:
                            self._data.update({
                                'fix_quality': quality,
                                'latitude': latf,
                                'longitude': lonf,
                                'num_sats': num_sats,
                                'hdop': hdop,
                                'timestamp': datetime.utcnow(),
                            })
                    except Exception:
                        continue
                elif tag.endswith('RMC') or tag == 'RMC':
                    # $--RMC,time,status,lat,NS,lon,EW,speed,track,date,magvar,...
                    try:
                        status = parts[2]
                        lat = parts[3]
                        ns = parts[4]
                        lon = parts[5]
                        ew = parts[6]
                        date_str = parts[9]
                        latf = _nmea_to_decimal(lat, ns)
                        lonf = _nmea_to_decimal(lon, ew)
                        quality = 1 if status == 'A' else 0
                        with self._lock:
                            self._data.update({
                                'fix_quality': quality,
                                'latitude': latf,
                                'longitude': lonf,
                                'timestamp': datetime.utcnow(),
                            })
                    except Exception:
                        continue
                # otherwise ignore other sentences
            except SerialException:
                break
            except Exception:
                # short sleep to avoid busy loop on unexpected errors
                time.sleep(0.1)

    def get_location(self):
        """Return the latest GPS location data as a dict.

        Keys: fix_quality (int), latitude (float), longitude (float),
        num_sats (int), hdop (float), timestamp (datetime).
        """
        with self._lock:
            return dict(self._data)

    def close(self):
        """Stop background thread and close serial port."""
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        try:
            if self._serial:
                self._serial.close()
        except Exception:
            pass

    # convenience alias used by some callers
    def get_time(self):
        with self._lock:
            ts = self._data.get('timestamp')
            return ts


def main():
    """Test GNSS module - continuous monitoring"""
    import sys
    
    print("🛰️  GNSS Module Test")
    print("=" * 60)
    
    # Initialize GNSS
    print("🔍 Searching for GNSS device...")
    gnss = GNSSModule()
    
    if not gnss._serial:
        print("❌ No GNSS device found!")
        print("\nSearched ports:")
        for port in gnss._find_ports():
            print(f"  - {port}")
        print("\nMake sure:")
        print("  1. GNSS device is connected via USB")
        print("  2. User has permission to access serial ports")
        print("     Run: sudo usermod -a -G dialout $USER")
        print("     Then logout and login again")
        return 1
    
    print(f"✅ GNSS device found: {gnss._serial.port}")
    print(f"   Baudrate: {gnss.baudrate}")
    print(f"   Timeout: {gnss.timeout}s")
    print("\n📡 Waiting for GPS signal...")
    print("   (This may take a few minutes outdoors)")
    print("\nPress Ctrl+C to stop\n")
    print("-" * 60)
    
    try:
        last_fix = 0
        no_fix_count = 0
        
        while True:
            data = gnss.get_location()
            fix = data.get('fix_quality', 0)
            lat = data.get('latitude')
            lon = data.get('longitude')
            sats = data.get('num_sats', 0)
            hdop = data.get('hdop')
            ts = data.get('timestamp')
            
            # Clear line and move cursor up
            print("\r" + " " * 60 + "\r", end='')
            
            if fix > 0 and lat is not None and lon is not None:
                # GPS Fix acquired
                if fix != last_fix:
                    print("\n🎯 GPS FIX ACQUIRED!")
                    print("-" * 60)
                
                print(f"📍 Position:")
                print(f"   Latitude:  {lat:>12.6f}°")
                print(f"   Longitude: {lon:>12.6f}°")
                print(f"   Google Maps: https://www.google.com/maps?q={lat},{lon}")
                print(f"\n📊 Quality:")
                print(f"   Fix Quality: {fix} ({'GPS' if fix == 1 else 'DGPS' if fix == 2 else 'Unknown'})")
                print(f"   Satellites:  {sats}")
                print(f"   HDOP:        {hdop if hdop else 'N/A'}")
                print(f"   Timestamp:   {ts.strftime('%H:%M:%S UTC') if ts else 'N/A'}")
                print("-" * 60)
                
                no_fix_count = 0
            else:
                # No fix yet
                no_fix_count += 1
                dots = "." * (no_fix_count % 4)
                print(f"⏳ Searching for satellites{dots:<3} (Sats: {sats})", end='', flush=True)
                
                if no_fix_count % 10 == 0 and no_fix_count > 0:
                    print(f"\n💡 Tip: Make sure GNSS antenna has clear view of the sky")
                    print(f"   Current satellites: {sats}")
                    if sats == 0:
                        print(f"   ⚠️  No satellites detected - check antenna connection")
            
            last_fix = fix
            time.sleep(1)
            
    except KeyboardInterrupt:
        print("\n\n🛑 Stopping GNSS monitoring...")
        gnss.close()
        print("✅ GNSS module closed")
        return 0
    except Exception as e:
        print(f"\n❌ Error: {e}")
        gnss.close()
        return 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
