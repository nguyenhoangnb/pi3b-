import time
import threading
from pathlib import Path

try:
    import RPi.GPIO as GPIO
    GPIO_AVAILABLE = True
except ImportError:
    GPIO_AVAILABLE = False
    print("⚠ RPi.GPIO not available - Reed switch will be simulated")

class ReedSwitch:
    def __init__(self, pin=17, callback=None, debounce_time=0.5):
        self.pin = pin
        self.debounce_time = debounce_time
        self.callback = callback
        self.last_trigger = 0
        self.running = False
        self.monitor_thread = None
        
        if GPIO_AVAILABLE:
            self._setup_gpio()
        else:
            print(f"⚠ Reed switch on pin {pin} - GPIO simulation mode")
    
    def _setup_gpio(self):
        """Setup GPIO for reed switch"""
        try:
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(self.pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            GPIO.add_event_detect(self.pin, GPIO.FALLING, callback=self._gpio_callback, bouncetime=int(self.debounce_time * 1000))
            print(f"✓ Reed switch setup on GPIO {self.pin}")
        except Exception as e:
            print(f"⚠ Reed switch GPIO setup error: {e}")
    
    def _gpio_callback(self, channel):
        """GPIO interrupt callback"""
        self._trigger_event()


    def _trigger_event(self):
        """Handle reed switch trigger with debounce"""
        current_time = time.time()
        
        # Debounce check
        if current_time - self.last_trigger < self.debounce_time:
            return
        
        self.last_trigger = current_time
        
        print(f"🔔 Reed switch triggered (pin {self.pin})")
        
        # Call callback if provided
        if self.callback:
            try:
                self.callback()
            except Exception as e:
                print(f"⚠ Reed switch callback error: {e}")
    
    def _monitor_simulation(self):
        """Simulation mode - monitor for file trigger"""
        trigger_file = Path("/tmp/reed_trigger")
        
        while self.running:
            try:
                if trigger_file.exists():
                    trigger_file.unlink()  # Remove file
                    self._trigger_event()
                
                time.sleep(0.1)  # Check every 100ms
                
            except Exception as e:
                print(f"⚠ Reed switch simulation error: {e}")
                break
    
    def start(self):
        """Start monitoring reed switch"""
        if self.running:
            return
        
        self.running = True
        
        if not GPIO_AVAILABLE:
            # Start simulation monitoring
            self.monitor_thread = threading.Thread(target=self._monitor_simulation, daemon=True)
            self.monitor_thread.start()
            print(f"🚀 Reed switch monitoring started (simulation mode)")
            print("   Trigger with: touch /tmp/reed_trigger")
        else:
            print(f"🚀 Reed switch monitoring started (GPIO {self.pin})")
    
    def stop(self):
        """Stop monitoring reed switch"""
        self.running = False
        
        if self.monitor_thread:
            self.monitor_thread.join(timeout=2)
        
        # Cleanup GPIO if available
        if GPIO_AVAILABLE:
            try:
                GPIO.remove_event_detect(self.pin)
                GPIO.cleanup(self.pin)
            except Exception as e:
                print(f"⚠ GPIO cleanup error: {e}")
        
        print(f"🛑 Reed switch monitoring stopped (pin {self.pin})")

    def is_closed(self):
        """Check if reed switch is closed"""
        if GPIO_AVAILABLE:
            try:
                state = GPIO.input(self.pin)
                time.sleep(0.01)  # Small delay
                return GPIO.input(self.pin) == state and state == GPIO.LOW
            except:
                return False
        return False

    def cleanup(self):
        """Legacy cleanup method"""
        self.stop()

    def _trigged(self, channel):
        """Legacy callback method"""
        self._trigger_event()
    
    def read(self):
        """Legacy read method"""
        return self.is_closed()
    
    def trigger_manually(self):
        """Manually trigger reed switch (for testing)"""
        print(f"🔧 Manual reed switch trigger (pin {self.pin})")
        self._trigger_event()

def main():
    print("=== Reed Switch Simple Test ===")
    
    # Setup
    reed_pin = 18  # Change to your pin
    
    def on_trigger():
        print("Reed switch triggered!")
    
    try:
        # Initialize reed switch
        reed = ReedSwitch(pin=reed_pin, callback=on_trigger)
        print(f"Reed switch initialized on GPIO {reed_pin}")
        
        # Test for 10 seconds
        print("Testing for 10 seconds... Move magnet to test")
        
        for i in range(10):
            state = reed.read()
            closed = reed.is_closed()
            print(f"[{i+1}s] Raw: {state}, Closed: {closed}")
            time.sleep(1)
        
        print("Test completed!")
        
    except Exception as e:
        print(f"Error: {e}")
    finally:
        reed.cleanup()
        print("Cleanup done")

if __name__ == "__main__":
    main()