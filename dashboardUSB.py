import threading, time, serial
from collections import deque
from flask import Flask, jsonify

POORT = "COM11"
R_BOVEN = 10000           # weerstand tussen paneel+ en GP26
R_ONDER = 11000           # weerstand tussen GP26 en GND
PANEEL_DELER = (R_BOVEN + R_ONDER) / R_ONDER   # = 1,909      # 1.0 = paneel direct op GP26; 2.0 bij spanningsdeler 1:1
KALIBRATIE = 1.00         # bijv. multimeter 4,80 V / dashboard 4,90 V = 0,98
MAX_PUNTEN = 300          # ongeveer 2,5 minuten aan data in de grafieken

hist = {k: deque(maxlen=MAX_PUNTEN) for k in ("volt", "l1", "l2", "l3", "l4")}
nu = {}
som = 0.0
aantal = 0

def verwerk(regel):
    global som, aantal, nu
    if not regel.startswith("D,"):
        return
    try:
        d = regel.split(",")[1:]
        l1, l2, l3, l4 = (int(float(x)) for x in d[0:4])
        h, v, pan, tilt = (float(x) for x in d[4:8])
        raw = int(d[8])
    except (ValueError, IndexError):
        return
    volt = raw / 65535 * 3.3 * PANEEL_DELER * KALIBRATIE
    som += volt
    aantal += 1
    nu = dict(l1=l1, l2=l2, l3=l3, l4=l4, h=h, v=v, pan=pan, tilt=tilt, volt=volt)
    for k, w in (("volt", volt), ("l1", l1), ("l2", l2), ("l3", l3), ("l4", l4)):
        hist[k].append(w)

def lees_serial():
    while True:
        try:
            ser = serial.Serial(POORT, 115200, timeout=2)
            print("Verbonden met", POORT)
            while True:
                verwerk(ser.readline().decode(errors="ignore").strip())
        except serial.SerialException as e:
            print("Poort niet beschikbaar (staat Thonny nog open?):", e)
            time.sleep(2)

HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Solar Tracker Dashboard</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">

    <style>
        body {
            font-family: Arial;
            background: #101827;
            color: white;
            text-align: center;
            padding: 30px;
        }

        h1 {
            color: #facc15;
        }

        .container {
            display: flex;
            justify-content: center;
            flex-wrap: wrap;
            gap: 20px;
            margin-bottom: 20px;
        }

        .card {
            background: #1e293b;
            padding: 30px;
            border-radius: 15px;
            width: 200px;
        }

        .value {
            font-size: 35px;
            color: #38bdf8;
        }

        .unit {
            color: #94a3b8;
        }

        .ldr-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 10px;
            margin-top: 10px;
        }

        .ldr {
            background: #0f172a;
            border-radius: 10px;
            padding: 10px;
        }

        .ldr .value {
            font-size: 22px;
        }

        .ldr small {
            color: #94a3b8;
        }

        .chart-card {
            background: #1e293b;
            border-radius: 15px;
            padding: 20px;
            max-width: 920px;
            margin: 20px auto 0;
        }

        .chart-card h2 {
            margin-top: 0;
        }

        canvas {
            width: 100%;
            height: 260px;
        }

        .legend span {
            margin: 0 10px;
            font-size: 14px;
        }
    </style>
</head>

<body>

    <h1>☀️ Solar Tracker Dashboard</h1>
    <p>Live gegevens van mijn zonnevolgende zonnepaneel</p>

    <div class="container">

        <div class="card">
            <h2>Paneelspanning</h2>
            <div class="value" id="volt">0</div>
            <div class="unit">Volt (V)</div>
        </div>

        <div class="card">
            <h2>Gemiddelde</h2>
            <div class="value" id="avg">0</div>
            <div class="unit">Volt (V), sinds start</div>
        </div>

        <div class="card">
            <h2>Servo's</h2>
            <div class="value" style="font-size:24px">
                Pan <span id="pan">0</span>°<br>Tilt <span id="tilt">0</span>°
            </div>
            <div class="unit">Hoek</div>
        </div>

        <div class="card">
            <h2>LDR's</h2>
            <div class="ldr-grid">
                <div class="ldr"><small>LDR1</small><div class="value" id="l1">0</div></div>
                <div class="ldr"><small>LDR2</small><div class="value" id="l2">0</div></div>
                <div class="ldr"><small>LDR3</small><div class="value" id="l3">0</div></div>
                <div class="ldr"><small>LDR4</small><div class="value" id="l4">0</div></div>
            </div>
            <div class="unit" style="margin-top:10px">
                H-fout <span id="h">0</span> | V-fout <span id="v">0</span>
            </div>
        </div>

    </div>

    <div class="chart-card">
        <h2>Paneelspanning</h2>
        <canvas id="chartVolt"></canvas>
    </div>

    <div class="chart-card">
        <h2>LDR-waarden</h2>
        <div class="legend">
            <span style="color:#f87171">● LDR1</span>
            <span style="color:#4ade80">● LDR2</span>
            <span style="color:#38bdf8">● LDR3</span>
            <span style="color:#c084fc">● LDR4</span>
        </div>
        <canvas id="chartLdr"></canvas>
    </div>

    <script>
        function drawChart(id, series, colors, decimals, fill) {
            const c = document.getElementById(id);
            const x = c.getContext("2d");
            const W = c.width = c.clientWidth;
            const H = c.height = 260;
            const L = 55, T = 10, B = H - 25;

            x.clearRect(0, 0, W, H);
            x.font = "12px Arial";

            let max = Math.max(...series.flat(), 0.001) * 1.1;

            for (let k = 0; k <= 4; k++) {
                const y = B - k / 4 * (B - T);
                x.strokeStyle = "#334155";
                x.lineWidth = 1;
                x.beginPath();
                x.moveTo(L, y);
                x.lineTo(W - 10, y);
                x.stroke();
                x.fillStyle = "#94a3b8";
                x.fillText((max * k / 4).toFixed(decimals), 5, y + 4);
            }

            series.forEach((s, idx) => {
                if (s.length < 2) return;
                const pts = s.map((val, i) => [
                    L + i / (s.length - 1) * (W - L - 10),
                    B - val / max * (B - T)
                ]);

                x.beginPath();
                pts.forEach(([px, py], i) => i ? x.lineTo(px, py) : x.moveTo(px, py));
                x.strokeStyle = colors[idx];
                x.lineWidth = 2;
                x.stroke();

                if (fill) {
                    x.lineTo(pts[pts.length - 1][0], B);
                    x.lineTo(L, B);
                    x.closePath();
                    x.fillStyle = "rgba(250, 204, 21, 0.15)";
                    x.fill();
                }
            });
        }

        async function updateDashboard() {
            try {
                const response = await fetch("/data");
                const d = await response.json();

                for (const k of ["volt", "avg", "pan", "tilt", "l1", "l2", "l3", "l4", "h", "v"]) {
                    document.getElementById(k).textContent = d[k];
                }

                drawChart("chartVolt", [d.hist.volt], ["#facc15"], 2, true);
                drawChart("chartLdr",
                    [d.hist.l1, d.hist.l2, d.hist.l3, d.hist.l4],
                    ["#f87171", "#4ade80", "#38bdf8", "#c084fc"], 0, false);

            } catch (error) {
                console.log("Kan geen gegevens ophalen");
            }
        }

        setInterval(updateDashboard, 500);
        updateDashboard();
    </script>

</body>
</html>
"""

app = Flask(__name__)

@app.route("/")
def index():
    return HTML

@app.route("/data")
def get_data():
    if not nu:
        return jsonify(volt=0, avg=0, pan=0, tilt=0, l1=0, l2=0, l3=0, l4=0,
                       h=0, v=0, hist={k: [] for k in hist})
    return jsonify(
        volt=round(nu["volt"], 2),
        avg=round(som / aantal, 2),
        pan=round(nu["pan"], 1),
        tilt=round(nu["tilt"], 1),
        l1=nu["l1"], l2=nu["l2"], l3=nu["l3"], l4=nu["l4"],
        h=round(nu["h"], 3),
        v=round(nu["v"], 3),
        hist={k: list(w) for k, w in hist.items()},
    )

if __name__ == "__main__":
    threading.Thread(target=lees_serial, daemon=True).start()
    app.run(host="127.0.0.1", port=5000)
