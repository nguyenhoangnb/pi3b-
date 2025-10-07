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