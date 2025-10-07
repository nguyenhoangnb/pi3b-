import time

import RPi.GPIO as GPIO

class ReedSwitch:
    def __init__(self, pin,callback=None, bouncetime=0.05):
        self.pin = pin
        self.bouncetime = bouncetime
        self.callback = callback
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(self.pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        GPIO.add_event_callback(self.pin, GPIO.FALLING, callback=self._trigged, bouncetime=bouncetime)


    def is_closed(self):
        state = GPIO.input(self.pin)
        time.sleep(self.bouncetime)
        return GPIO.input(self.pin) == state and state == GPIO.LOW

    def cleanup(self):
        GPIO.remove_event_detect(self.pin)
        GPIO.cleanup(self.pin)

    def _trigged(self, channel):
        if self.callback:
            self.callback()
    
    def read(self):
        return GPIO.input(self.pin) == GPIO.LOW

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