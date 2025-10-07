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
    print("Check function")
    led = gpioLed(13)
    while True:
        print("\n===== MENU =====")
        print("1. Turn on led")
        print("2. Turn off led")
        print("3. Blink led")
        print("4. Exit")
        print("================")
        choice = input("Select option (1–4): ").strip()

        if choice == "1":
            print("\n Turn on led")
            gpioLed.on()
        elif choice == "2":
            print("\n Turn off led")
            gpioLed.off()

        elif choice == "3":
            print("Start blink led")
            gpioLed.blink()

        elif choice == "4":
            print("Exit")
            gpioLed.cleanup()
            break

        

        else:
            print("⚠ Invalid selection. Please choose 1–5.")
