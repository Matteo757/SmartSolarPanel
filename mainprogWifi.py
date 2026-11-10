from machine import I2C, Pin, ADC, PWM
from time import sleep, ticks_ms, ticks_diff
import network, socket, json
from ads1115 import ADS1115

# ===== COMMUNICATIE =====
# WIFI_AAN = True : thuis, metingen via wifi (HTTP POST) naar het dashboard
# WIFI_AAN = False: school (netwerk blokkeert wifi), alleen USB-serial
WIFI_AAN = True
SERVER_POORT = 5000
STUUR_INTERVAL = 500           # ms tussen twee verzendingen

# Wifi-gegevens staan in wifi_secrets.py (niet op GitHub, zie wifi_secrets_example.py)
try:
    from wifi_secrets import WIFI_SSID, WIFI_WACHTWOORD, SERVER_IP
except ImportError:
    WIFI_AAN = False
    print("wifi_secrets.py niet gevonden: wifi staat uit, alleen USB-serial")

# ===== TRACKER-INSTELLINGEN =====
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


# ===== WIFI =====
wlan = network.WLAN(network.STA_IF)


def verbind_wifi(max_seconden=15):
    wlan.active(True)
    if wlan.isconnected():
        return True
    wlan.connect(WIFI_SSID, WIFI_WACHTWOORD)
    for _ in range(max_seconden * 2):
        if wlan.isconnected():
            print("WiFi verbonden. IP van de Pico:", wlan.ifconfig()[0])
            return True
        sleep(0.5)
    print("WiFi niet verbonden (controleer SSID, wachtwoord en 2,4 GHz)")
    return False


def stuur_data(data):
    """Stuurt de metingen als JSON via een HTTP POST naar het dashboard."""
    if not WIFI_AAN or not wlan.isconnected():
        return False
    s = None
    try:
        body = json.dumps(data)
        kop = ("POST /ingest HTTP/1.0\r\n"
               "Host: %s\r\n"
               "Content-Type: application/json\r\n"
               "Content-Length: %d\r\n\r\n" % (SERVER_IP, len(body)))
        s = socket.socket()
        s.settimeout(0.5)          # korte timeout, zodat de tracker niet blijft hangen
        s.connect((SERVER_IP, SERVER_POORT))
        s.send((kop + body).encode())
        s.recv(64)
        return True
    except Exception as e:
        print("Versturen mislukt:", e)
        return False
    finally:
        if s:
            s.close()


sleep(1)
if WIFI_AAN:
    verbind_wifi()

laatste = ticks_ms()
laatste_wifi_poging = ticks_ms()
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

    if ticks_diff(ticks_ms(), laatste) >= STUUR_INTERVAL:
        raw = panel_adc.read_u16()

        # Regel voor het dashboard via USB-serial (begint met "D,")
        print("D,%d,%d,%d,%d,%.3f,%.3f,%.1f,%.1f,%d" % (
            ldr1, ldr2, ldr3, ldr4, h, v, pan.huidig, tilt.huidig, raw))
        # Leesbare regel voor in Thonny
        print("LDR:", ldr1, ldr2, ldr3, ldr4, "| H: %.3f V: %.3f" % (h, v),
              "| Pan: %.1f Tilt: %.1f" % (pan.huidig, tilt.huidig),
              "| Ronde:", ronde, "ms", "| Paneel RAW:", raw)

        stuur_data({
            "ldr": [ldr1, ldr2, ldr3, ldr4],
            "h": round(h, 3),
            "v": round(v, 3),
            "pan": round(pan.huidig, 1),
            "tilt": round(tilt.huidig, 1),
            "raw": raw,
        })
        laatste = ticks_ms()

    # Als de wifi wegvalt: elke 10 s opnieuw proberen, zonder de tracker te blokkeren
    if WIFI_AAN and not wlan.isconnected() and ticks_diff(ticks_ms(), laatste_wifi_poging) > 10000:
        wlan.connect(WIFI_SSID, WIFI_WACHTWOORD)
        laatste_wifi_poging = ticks_ms()

    sleep(VERTRAGING)
    nu = ticks_ms()
    ronde = ticks_diff(nu, vorige)
    vorige = nu
