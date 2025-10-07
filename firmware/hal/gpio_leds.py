try:
    import RPi.GPIO as GPIO

except ModuleNotFoundError:
    import sys
    import fake_rpi
    original_output = fake_rpi.RPi._GPIO.output
    # Disable fake_rpi debug output
    # Redirect fake_rpi output to suppress debug messages
    fake_rpi.RPi._GPIO.output_original = fake_rpi.RPi._GPIO.output

    def silent_output(pin, value, *args, **kwargs):
        return original_output(pin, value, *args, **kwargs)

    fake_rpi.RPi._GPIO.output = silent_output

    sys.modules['RPi'] = fake_rpi.RPi
    sys.modules['RPi.GPIO'] = fake_rpi.RPi.GPIO
    import RPi.GPIO as GPIO
import time

class gpioLed:
    def __init__(self, pin):
        self.pin = pin
        self._setup()
    
    def _setup(self):
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        GPIO.setup(self.pin, GPIO.OUT)
        self.off()
    
    def on(self):
        GPIO.output(self.pin, GPIO.HIGH)
    def off(self):
        GPIO.output(self.pin, GPIO.LOW)
    
    def blink(self, interval):
        self.on()
        time.sleep(interval)
        self.off()
        time.sleep(interval)
    def cleanup(self):
        GPIO.cleanup(self.pin)

def main():
    print("=== LED Control Test ===")
    led = gpioLed(13)
    
    try:
        while True:
            print("\n===== MENU =====")
            print("1. Turn on LED")
            print("2. Turn off LED")
            print("3. Blink LED")
            print("4. Exit")
            print("================")
            choice = input("Select option (1–4): ").strip()

            if choice == "1":
                print("🔆 Turning on LED")
                led.on()
                
            elif choice == "2":
                print("🔅 Turning off LED")
                led.off()

            elif choice == "3":
                print("⚡ Start blinking LED (5 times)")
                for i in range(5):
                    led.blink(0.5)
                    print(f"  Blink {i+1}/5")

            elif choice == "4":
                print("👋 Exit")
                break

            else:
                print("⚠ Invalid selection. Please choose 1–4.")
    
    except KeyboardInterrupt:
        print("\nInterrupted by user")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        led.cleanup()
        print("✓ GPIO cleanup completed")

if __name__ == "__main__":
    main()
