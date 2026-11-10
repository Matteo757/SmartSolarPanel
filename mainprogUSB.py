from machine import I2C, Pin, ADC, PWM
from time import sleep, ticks_ms, ticks_diff
from ads1115 import ADS1115

PAN_MIN, PAN_MAX, PAN_START = 0, 180, 90
TILT_MIN, TILT_MAX, TILT_START = 0, 160, 70
PAN_RICHTING = -1
TILT_RICHTING = -1
DREMPEL = 0.02
OFFSET_PAN = 0.0
OFFSET_TILT = 0.0
STAP_MIN = 0.25
STAP_MAX = 3.0
KP = 22
VERTRAGING = 0.03

i2c = I2C(0, sda=Pin(0), scl=Pin(1), freq=400000)
adc = ADS1115(i2c)
panel_adc = ADC(Pin(26))

class Servo:
    def __init__(self, pin, minimum, maximum, start):
        self.pwm = PWM(Pin(pin))
        self.pwm.freq(50)
        self.min = minimum
        self.max = maximum
        self.set(start)

    def set(self, hoek):
        self.huidig = max(self.min, min(self.max, hoek))
        self.pwm.duty_u16(int(1638 + (self.huidig / 180.0) * (8192 - 1638)))

pan = Servo(15, PAN_MIN, PAN_MAX, PAN_START)
tilt = Servo(14, TILT_MIN, TILT_MAX, TILT_START)

def fout(a, b):
    return (a - b) / ((a + b) or 1)

def stuur(servo, richting, e):
    if abs(e) > DREMPEL:
        stap = min(STAP_MAX, max(STAP_MIN, abs(e) * KP))
        servo.set(servo.huidig + richting * stap * (1 if e > 0 else -1))

sleep(1)
laatste = ticks_ms()
vorige = ticks_ms()
ronde = 0

while True:
    ldr1 = adc.read(0)
    ldr2 = adc.read(1)
    ldr3 = adc.read(2)
    ldr4 = adc.read(3)
    h = fout(ldr1 + ldr3, ldr2 + ldr4) - OFFSET_PAN
    v = fout(ldr1 + ldr2, ldr3 + ldr4) - OFFSET_TILT

    stuur(pan, PAN_RICHTING, h)
    stuur(tilt, TILT_RICHTING, v)

    if ticks_diff(ticks_ms(), laatste) >= 500:
        print("D,%d,%d,%d,%d,%.3f,%.3f,%.1f,%.1f,%d" % (ldr1, ldr2, ldr3, ldr4, h, v, pan.huidig, tilt.huidig, panel_adc.read_u16()))
        laatste = ticks_ms()

    sleep(VERTRAGING)
    nu = ticks_ms()
    ronde = ticks_diff(nu, vorige)
    vorige = nu
