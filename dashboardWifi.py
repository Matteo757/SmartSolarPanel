import sqlite3, threading, time
from collections import deque
from contextlib import closing
from datetime import datetime
from flask import Flask, Response, jsonify, request

# ===== INSTELLINGEN =====
# Kies één invoer tegelijk:
#   thuis (wifi):  SERIAL_POORT = None            en op de Pico WIFI_AAN = True
#   school (USB):  SERIAL_POORT = "COM11" (of jouw poort)  en op de Pico WIFI_AAN = False
SERIAL_POORT = None

DB_BESTAND = "metingen.db"
OPSLAG_INTERVAL = 2          # seconden tussen twee opgeslagen metingen

# Paneelspanning: spanningsdeler 10 kOhm / 11 kOhm op GP26
R_BOVEN = 10000
R_ONDER = 11000
PANEEL_DELER = (R_BOVEN + R_ONDER) / R_ONDER
KALIBRATIE = 1.00            # correctiefactor na vergelijking met een multimeter

# LDR's: ADS1115 ruwe waarde -> volt. Dit hangt af van de gain in ads1115.py:
#   +-6.144 V: 0.0001875 | +-4.096 V: 0.000125 | +-2.048 V: 0.0000625
#   +-1.024 V: 0.00003125 | +-0.512 V: 0.000015625 | +-0.256 V: 0.0000078125
ADS_LSB_V = 0.000125

MAX_PUNTEN = 300             # punten in de live-grafieken
OFFLINE_NA = 5               # seconden zonder data = Pico offline

hist = {k: deque(maxlen=MAX_PUNTEN) for k in ("volt", "l1", "l2", "l3", "l4")}
laatste = {}
laatste_ontvangst = 0.0
laatste_opslag = 0.0
lock = threading.Lock()


# ===== OPSLAG (SQLite) =====
def db_init():
    with closing(sqlite3.connect(DB_BESTAND, timeout=5)) as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("""CREATE TABLE IF NOT EXISTS metingen (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tijd INTEGER NOT NULL,
            paneel_v REAL,
            ldr1_v REAL, ldr2_v REAL, ldr3_v REAL, ldr4_v REAL,
            h REAL, v REAL, pan REAL, tilt REAL)""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_tijd ON metingen(tijd)")
        c.commit()


def opslaan(t, volt, ldr_v, h, v, pan, tilt):
    with closing(sqlite3.connect(DB_BESTAND, timeout=5)) as c:
        c.execute(
            "INSERT INTO metingen (tijd, paneel_v, ldr1_v, ldr2_v, ldr3_v, ldr4_v, h, v, pan, tilt) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (int(t), round(volt, 3), *[round(x, 4) for x in ldr_v],
             round(h, 3), round(v, 3), round(pan, 1), round(tilt, 1)))
        c.commit()


_cache = {"t": 0.0, "v": 0.0}


def gemiddelde_24u():
    """Gemiddelde paneelspanning van de laatste 24 uur (uit de database, 10 s gecachet)."""
    if time.time() - _cache["t"] > 10:
        try:
            with closing(sqlite3.connect(DB_BESTAND, timeout=5)) as c:
                r = c.execute("SELECT AVG(paneel_v) FROM metingen WHERE tijd >= ?",
                              (int(time.time()) - 86400,)).fetchone()
            _cache["v"] = round(r[0] or 0, 2)
        except sqlite3.Error as e:
            print("Database-fout:", e)
        _cache["t"] = time.time()
    return _cache["v"]


# ===== ONTVANGEN EN VERWERKEN =====
def registreer(ldr_raw, h, v, pan, tilt, raw):
    """Verwerkt één meting (van wifi of USB): omrekenen, live bewaren en opslaan."""
    global laatste, laatste_ontvangst, laatste_opslag
    ldr_v = [x * ADS_LSB_V for x in ldr_raw]
    volt = raw / 65535 * 3.3 * PANEEL_DELER * KALIBRATIE
    t = time.time()
    with lock:
        laatste = dict(l1=ldr_v[0], l2=ldr_v[1], l3=ldr_v[2], l4=ldr_v[3],
                       h=h, v=v, pan=pan, tilt=tilt, volt=volt)
        laatste_ontvangst = t
        hist["volt"].append(volt)
        for k, w in zip(("l1", "l2", "l3", "l4"), ldr_v):
            hist[k].append(w)
    if t - laatste_opslag >= OPSLAG_INTERVAL:
        laatste_opslag = t
        try:
            opslaan(t, volt, ldr_v, h, v, pan, tilt)
        except sqlite3.Error as e:
            print("Opslaan mislukt:", e)


def lees_serial():
    """Optioneel: leest de 'D,...'-regels van de Pico via USB-serial."""
    try:
        import serial
    except ImportError:
        print("pyserial ontbreekt: installeer met 'pip install pyserial'")
        return
    while True:
        try:
            ser = serial.Serial(SERIAL_POORT, 115200, timeout=2)
            print("USB verbonden met", SERIAL_POORT)
            while True:
                regel = ser.readline().decode(errors="ignore").strip()
                if not regel.startswith("D,"):
                    continue
                try:
                    d = regel.split(",")[1:]
                    registreer([int(float(x)) for x in d[0:4]],
                               float(d[4]), float(d[5]), float(d[6]), float(d[7]), int(d[8]))
                except (ValueError, IndexError):
                    pass
        except serial.SerialException as e:
            print("USB-poort niet beschikbaar (staat Thonny nog open?):", e)
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
            font-size: 20px;
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

        .knoppen {
            margin-bottom: 15px;
        }

        .knoppen button {
            background: #0f172a;
            color: #94a3b8;
            border: 1px solid #334155;
            border-radius: 8px;
            padding: 8px 16px;
            margin: 0 4px;
            cursor: pointer;
        }

        .knoppen button.actief {
            background: #facc15;
            color: #101827;
            border-color: #facc15;
        }

        a.download {
            color: #38bdf8;
            font-size: 14px;
        }
    </style>
</head>

<body>

    <h1>☀️ Solar Tracker Dashboard</h1>
    <p>Live gegevens van mijn zonnevolgende zonnepaneel</p>
    <p id="status">⚪ Wachten op data...</p>

    <div class="container">

        <div class="card">
            <h2>Paneelspanning</h2>
            <div class="value" id="volt">0</div>
            <div class="unit">Volt (V)</div>
        </div>

        <div class="card">
            <h2>Gemiddelde</h2>
            <div class="value" id="avg">0</div>
            <div class="unit">Volt (V), laatste 24 u</div>
        </div>

        <div class="card">
            <h2>Servo's</h2>
            <div class="value" style="font-size:24px">
                Pan <span id="pan">0</span>°<br>Tilt <span id="tilt">0</span>°
            </div>
            <div class="unit">Hoek</div>
        </div>

        <div class="card">
            <h2>LDR's (V)</h2>
            <div class="ldr-grid">
                <div class="ldr"><small>LDR1</small><div class="value" id="l1">0</div></div>
                <div class="ldr"><small>LDR2</small><div class="value" id="l2">0</div></div>
                <div class="ldr"><small>LDR3</small><div class="value" id="l3">0</div></div>
                <div class="ldr"><small>LDR4</small><div class="value" id="l4">0</div></div>
            </div>
            <div class="unit" style="margin-top:10px">
                Volt (V) | H-fout <span id="h">0</span> | V-fout <span id="v">0</span>
            </div>
        </div>

    </div>

    <div class="chart-card">
        <h2>Paneelspanning (live)</h2>
        <canvas id="chartVolt"></canvas>
    </div>

    <div class="chart-card">
        <h2>LDR-waarden in volt (live)</h2>
        <div class="legend">
            <span style="color:#f87171">● LDR1</span>
            <span style="color:#4ade80">● LDR2</span>
            <span style="color:#38bdf8">● LDR3</span>
            <span style="color:#c084fc">● LDR4</span>
        </div>
        <canvas id="chartLdr"></canvas>
    </div>

    <div class="chart-card">
        <h2>Historie (uit de database)</h2>
        <div class="knoppen">
            <button id="k-uur" onclick="kies('uur')">Laatste uur</button>
            <button id="k-dag" class="actief" onclick="kies('dag')">Laatste dag</button>
            <button id="k-week" onclick="kies('week')">Laatste week</button>
            <button id="k-alles" onclick="kies('alles')">Alles</button>
        </div>
        <p class="unit" id="stats">-</p>
        <canvas id="histVolt"></canvas>
        <h3>LDR-waarden in volt</h3>
        <canvas id="histLdr"></canvas>
        <p><a class="download" href="/export.csv">⬇ Download alle metingen als CSV</a></p>
    </div>

    <script>
        const KLEUREN = ["#f87171", "#4ade80", "#38bdf8", "#c084fc"];

        function drawChart(id, series, colors, decimals, fill, labels) {
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

            if (labels) {
                x.fillStyle = "#94a3b8";
                x.textAlign = "left";
                x.fillText(labels[0], L, H - 8);
                x.textAlign = "right";
                x.fillText(labels[1], W - 10, H - 8);
                x.textAlign = "left";
            }
        }

        async function updateDashboard() {
            try {
                const response = await fetch("/data");
                const d = await response.json();

                for (const k of ["volt", "avg", "pan", "tilt", "l1", "l2", "l3", "l4", "h", "v"]) {
                    document.getElementById(k).textContent = d[k];
                }

                document.getElementById("status").textContent =
                    d.online ? "🟢 Pico verbonden" : "🔴 Geen data van de Pico";

                drawChart("chartVolt", [d.hist.volt], ["#facc15"], 2, true);
                drawChart("chartLdr",
                    [d.hist.l1, d.hist.l2, d.hist.l3, d.hist.l4], KLEUREN, 2, false);

            } catch (error) {
                console.log("Kan geen gegevens ophalen");
            }
        }

        let periode = "dag";

        function kies(p) {
            periode = p;
            for (const k of ["uur", "dag", "week", "alles"]) {
                document.getElementById("k-" + k).classList.toggle("actief", k === p);
            }
            updateHistory();
        }

        function tijdLabel(t) {
            return new Date(t * 1000).toLocaleString("nl-BE",
                { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
        }

        async function updateHistory() {
            try {
                const response = await fetch("/history?periode=" + periode);
                const d = await response.json();
                const labels = d.t.length ? [tijdLabel(d.t[0]), tijdLabel(d.t[d.t.length - 1])] : null;

                document.getElementById("stats").textContent = d.aantal
                    ? "Min " + d.min + " V | Max " + d.max + " V | Gemiddelde " + d.avg +
                      " V | " + d.aantal + " metingen"
                    : "Nog geen opgeslagen metingen in deze periode";

                drawChart("histVolt", [d.volt], ["#facc15"], 2, true, labels);
                drawChart("histLdr", [d.l1, d.l2, d.l3, d.l4], KLEUREN, 2, false, labels);
            } catch (error) {
                console.log("Kan historie niet ophalen");
            }
        }

        setInterval(updateDashboard, 500);
        setInterval(updateHistory, 10000);
        updateDashboard();
        updateHistory();
    </script>

</body>
</html>
"""

app = Flask(__name__)


@app.route("/")
def index():
    return HTML


@app.route("/ingest", methods=["POST"])
def ingest():
    """Ontvangt de metingen van de Pico (HTTP POST met JSON)."""
    d = request.get_json(silent=True)
    if not d:
        return "geen geldige JSON", 400
    try:
        ldr = [int(x) for x in d["ldr"]]
        if len(ldr) != 4:
            raise ValueError
        registreer(ldr, float(d["h"]), float(d["v"]),
                   float(d["pan"]), float(d["tilt"]), int(d["raw"]))
    except (KeyError, ValueError, TypeError):
        return "ongeldige data", 400
    return "ok"


@app.route("/data")
def get_data():
    avg = gemiddelde_24u()
    with lock:
        online = (time.time() - laatste_ontvangst) < OFFLINE_NA
        d = dict(laatste)
        live = {k: [round(x, 3) for x in w] for k, w in hist.items()}
    if not d:
        d = dict(volt=0, pan=0, tilt=0, l1=0, l2=0, l3=0, l4=0, h=0, v=0)
    return jsonify(
        volt=round(d["volt"], 2), avg=avg,
        pan=round(d["pan"], 1), tilt=round(d["tilt"], 1),
        l1=round(d["l1"], 3), l2=round(d["l2"], 3),
        l3=round(d["l3"], 3), l4=round(d["l4"], 3),
        h=round(d["h"], 3), v=round(d["v"], 3),
        online=online, hist=live,
    )


PERIODES = {"uur": 3600, "dag": 86400, "week": 604800}


@app.route("/history")
def history():
    """Gemiddelden per tijdsblok uit de database, max ongeveer 300 punten."""
    p = request.args.get("periode", "dag")
    nu_t = int(time.time())
    try:
        with closing(sqlite3.connect(DB_BESTAND, timeout=5)) as c:
            if p == "alles":
                r = c.execute("SELECT MIN(tijd) FROM metingen").fetchone()
                van = r[0] if r and r[0] is not None else nu_t
            else:
                van = nu_t - PERIODES.get(p, 86400)
            blok = max(1, (nu_t - van) // 300)
            rijen = c.execute(
                "SELECT (tijd / ?) * ? AS t, AVG(paneel_v), AVG(ldr1_v), AVG(ldr2_v), "
                "AVG(ldr3_v), AVG(ldr4_v) FROM metingen WHERE tijd >= ? "
                "GROUP BY t ORDER BY t", (blok, blok, van)).fetchall()
            stat = c.execute(
                "SELECT MIN(paneel_v), MAX(paneel_v), AVG(paneel_v), COUNT(*) "
                "FROM metingen WHERE tijd >= ?", (van,)).fetchone()
    except sqlite3.Error as e:
        print("Database-fout:", e)
        rijen, stat = [], (0, 0, 0, 0)
    return jsonify(
        t=[r[0] for r in rijen],
        volt=[round(r[1], 3) for r in rijen],
        l1=[round(r[2], 3) for r in rijen],
        l2=[round(r[3], 3) for r in rijen],
        l3=[round(r[4], 3) for r in rijen],
        l4=[round(r[5], 3) for r in rijen],
        min=round(stat[0] or 0, 2), max=round(stat[1] or 0, 2),
        avg=round(stat[2] or 0, 2), aantal=stat[3],
    )


@app.route("/export.csv")
def export_csv():
    def regels():
        yield "tijd,paneel_v,ldr1_v,ldr2_v,ldr3_v,ldr4_v,h_fout,v_fout,pan,tilt\n"
        with closing(sqlite3.connect(DB_BESTAND, timeout=5)) as c:
            for r in c.execute("SELECT tijd, paneel_v, ldr1_v, ldr2_v, ldr3_v, ldr4_v, "
                               "h, v, pan, tilt FROM metingen ORDER BY tijd"):
                yield (datetime.fromtimestamp(r[0]).strftime("%Y-%m-%d %H:%M:%S") + "," +
                       ",".join(str(x) for x in r[1:]) + "\n")
    return Response(regels(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=metingen.csv"})


if __name__ == "__main__":
    db_init()
    if SERIAL_POORT:
        threading.Thread(target=lees_serial, daemon=True).start()
    # 0.0.0.0 = bereikbaar voor andere apparaten op je netwerk (de Pico via wifi)
    app.run(host="0.0.0.0", port=5000)
