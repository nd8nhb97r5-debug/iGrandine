import math
import os
import time
import requests

from datetime import datetime, timezone


# ============================================================
# CONFIGURAZIONE
# ============================================================

DEFAULT_LAT = 45.5009
DEFAULT_LON = 10.3554

FIREBASE_BASE = (
    "https://igrandine-default-rtdb.europe-west1.firebasedatabase.app"
)

POSITION_URL = f"{FIREBASE_BASE}/posizione.json"
STATE_URL = f"{FIREBASE_BASE}/stato_allerta.json"

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")

# ============================================================
# SOGLIE METEO
# ============================================================

PIOGGIA_FORTE = 10
PIOGGIA_ALLAGAMENTO = 20
PIOGGIA_ESTREMA = 30

ACCUMULO_ALLAGAMENTO_1H = 20

VENTO_ALLERTA = 60
VENTO_FORTE = 80
VENTO_SEVERO = 100

CAPE_TEMPORALE = 1000
CAPE_GRANDINE = 1500
CAPE_GRANDINE_ALTA = 2500

LPI_TEMPORALE = 1
LPI_GRANDINE = 3

# Radar
RADAR_DEBOLE = 20
RADAR_FORTE = 40
RADAR_SEVERO = 50
RADAR_GRANDINE = 55

RADAR_RAGGIO_KM = 120

# Ultimi 8 frame = circa 80 minuti
RADAR_FRAME_COUNT = 8

# Per considerare una cella realmente in avvicinamento
MIN_MOVIMENTO_KM = 2

# Parcheggi/rifugi
RAGGIO_RICERCA_METRI = 3000


# ============================================================
# POSIZIONE
# ============================================================

def get_location():

    try:
        r = requests.get(
            POSITION_URL,
            timeout=10
        )

        r.raise_for_status()

        data = r.json()

        if data and "lat" in data and "lon" in data:

            return (
                float(data["lat"]),
                float(data["lon"])
            )

    except Exception as e:

        print("Errore lettura posizione:", e)

    return DEFAULT_LAT, DEFAULT_LON


# ============================================================
# STATO PRECEDENTE
# ============================================================

def get_previous_state():

    try:

        r = requests.get(
            STATE_URL,
            timeout=10
        )

        r.raise_for_status()

        data = r.json()

        if isinstance(data, dict):

            return data

    except Exception as e:

        print("Stato precedente non disponibile:", e)

    return {
        "level": "VERDE",
        "score": 0,
        "eta_stage": "none",
        "last_alert_type": "",
        "timestamp": 0
    }


def save_state(state):

    try:

        r = requests.put(
            STATE_URL,
            json=state,
            timeout=10
        )

        r.raise_for_status()

        return True

    except Exception as e:

        print("Impossibile salvare stato:", e)

        return False


# ============================================================
# OPEN-METEO
# ============================================================

def get_weather(lat, lon):

    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}"
        f"&longitude={lon}"
        "&minutely_15="
        "precipitation,"
        "rain,"
        "showers,"
        "wind_gusts_10m,"
        "cape,"
        "lightning_potential"
        "&hourly="
        "precipitation,"
        "rain,"
        "showers,"
        "precipitation_probability,"
        "cape,"
        "wind_gusts_10m,"
        "temperature_2m,"
        "freezing_level_height,"
        "weather_code"
        "&forecast_days=1"
        "&timezone=auto"
    )

    r = requests.get(
        url,
        timeout=20
    )

    r.raise_for_status()

    return r.json()


# ============================================================
# GEOGRAFIA
# ============================================================

def haversine_km(
    lat1,
    lon1,
    lat2,
    lon2
):

    R = 6371.0

    p1 = math.radians(lat1)
    p2 = math.radians(lat2)

    dphi = math.radians(
        lat2 - lat1
    )

    dlambda = math.radians(
        lon2 - lon1
    )

    a = (
        math.sin(dphi / 2) ** 2
        +
        math.cos(p1)
        *
        math.cos(p2)
        *
        math.sin(dlambda / 2) ** 2
    )

    return (
        2
        * R
        * math.asin(
            math.sqrt(a)
        )
    )


def bearing_deg(
    lat1,
    lon1,
    lat2,
    lon2
):

    p1 = math.radians(lat1)
    p2 = math.radians(lat2)

    dlambda = math.radians(
        lon2 - lon1
    )

    y = (
        math.sin(dlambda)
        * math.cos(p2)
    )

    x = (
        math.cos(p1)
        * math.sin(p2)
        -
        math.sin(p1)
        * math.cos(p2)
        * math.cos(dlambda)
    )

    return (
        math.degrees(
            math.atan2(y, x)
        )
        + 360
    ) % 360


def direction_name(deg):

    directions = [
        "N",
        "NE",
        "E",
        "SE",
        "S",
        "SO",
        "O",
        "NO"
    ]

    return directions[
        int(
            (deg + 22.5) / 45
        ) % 8
    ]


# ============================================================
# ANALISI PREVISIONE
# ============================================================

def analyze_forecast(data):

    result = {
        "score": 0,
        "alerts": [],
        "details": [],
        "hail_risk": False,
        "max_cape": 0,
        "max_lpi": 0,
        "max_gust": 0,
        "max_rain_rate": 0,
        "rain_accumulation_1h": 0
    }

    minute = data.get(
        "minutely_15",
        {}
    )

    precip = minute.get(
        "precipitation",
        []
    )

    showers = minute.get(
        "showers",
        []
    )

    gusts = minute.get(
        "wind_gusts_10m",
        []
    )

    cape = minute.get(
        "cape",
        []
    )

    lpi = minute.get(
        "lightning_potential",
        []
    )

    # --------------------------------------------------------
    # PIOGGIA
    # --------------------------------------------------------

    p1h = precip[:4]

    if p1h:

        max_15min = max(p1h)

        max_mm_h = max_15min * 4

        accumulo = sum(p1h)

        result["max_rain_rate"] = max_mm_h
        result["rain_accumulation_1h"] = accumulo

        if max_mm_h >= PIOGGIA_ESTREMA:

            result["score"] += 6

            result["alerts"].append(
                f"🚨 PIOGGIA ESTREMAMENTE INTENSA "
                f"fino a {max_mm_h:.1f} mm/h"
            )

            result["details"].append(
                "Elevato potenziale di allagamenti localizzati"
            )

        elif (
            max_mm_h >= PIOGGIA_ALLAGAMENTO
            or accumulo >= ACCUMULO_ALLAGAMENTO_1H
        ):

            result["score"] += 4

            result["alerts"].append(
                f"🌊 PIOGGIA MOLTO INTENSA "
                f"fino a {max_mm_h:.1f} mm/h"
            )

            result["details"].append(
                f"Accumulo previsto nella prossima ora: "
                f"{accumulo:.1f} mm"
            )

            result["details"].append(
                "Possibile rischio di allagamenti"
            )

        elif max_mm_h >= PIOGGIA_FORTE:

            result["score"] += 2

            result["alerts"].append(
                f"🌧️ PIOGGIA INTENSA "
                f"fino a {max_mm_h:.1f} mm/h"
            )

    # --------------------------------------------------------
    # VENTO
    # --------------------------------------------------------

    g1h = gusts[:4]

    if g1h:

        max_gust = max(g1h)

        result["max_gust"] = max_gust

        if max_gust >= VENTO_SEVERO:

            result["score"] += 5

            result["alerts"].append(
                f"🚨 RAFFICHE MOLTO FORTI "
                f"fino a {max_gust:.0f} km/h"
            )

        elif max_gust >= VENTO_FORTE:

            result["score"] += 3

            result["alerts"].append(
                f"💨 VENTO FORTE "
                f"fino a {max_gust:.0f} km/h"
            )

        elif max_gust >= VENTO_ALLERTA:

            result["score"] += 2

            result["alerts"].append(
                f"💨 VENTO: raffiche oltre 60 km/h "
                f"(fino a {max_gust:.0f} km/h)"
            )

    # --------------------------------------------------------
    # CAPE / LPI
    # --------------------------------------------------------

    if cape:

        result["max_cape"] = max(
            cape[:24]
        )

    if lpi:

        result["max_lpi"] = max(
            lpi[:24]
        )

    max_showers = (
        max(showers[:24])
        if showers
        else 0
    )

    cape_value = result["max_cape"]
    lpi_value = result["max_lpi"]

    # --------------------------------------------------------
    # GRANDINE
    # --------------------------------------------------------

    if (
        cape_value >= CAPE_GRANDINE_ALTA
        and
        lpi_value >= LPI_GRANDINE
        and
        max_showers >= 2
    ):

        result["score"] += 6
        result["hail_risk"] = True

        result["alerts"].append(
            f"🧊 ALTO RISCHIO DI GRANDINE "
            f"(CAPE {cape_value:.0f}, "
            f"LPI {lpi_value:.1f})"
        )

    elif (
        cape_value >= CAPE_GRANDINE
        and
        (
            lpi_value >= LPI_GRANDINE
            or max_showers >= 2
        )
    ):

        result["score"] += 4
        result["hail_risk"] = True

        result["alerts"].append(
            f"🧊 POSSIBILE GRANDINE "
            f"(CAPE {cape_value:.0f}, "
            f"LPI {lpi_value:.1f})"
        )

    elif (
        cape_value >= CAPE_TEMPORALE
        and
        (
            lpi_value >= LPI_TEMPORALE
            or max_showers >= 2
        )
    ):

        result["score"] += 2

        result["alerts"].append(
            f"⛈️ POSSIBILE TEMPORALE "
            f"(CAPE {cape_value:.0f})"
        )

    # --------------------------------------------------------
    # COMBINAZIONI
    # --------------------------------------------------------

    if (
        result["max_rain_rate"]
        >= PIOGGIA_ALLAGAMENTO
        and
        result["max_gust"]
        >= VENTO_ALLERTA
    ):

        result["score"] += 3

        result["details"].append(
            "Combinazione potenzialmente severa: "
            "pioggia intensa + vento forte"
        )

    if (
        result["hail_risk"]
        and
        result["max_gust"]
        >= VENTO_ALLERTA
    ):

        result["score"] += 3

        result["details"].append(
            "Temporale potenzialmente severo: "
            "grandine possibile + vento forte"
        )

    return result


# ============================================================
# RAINVIEWER
# ============================================================

def get_radar_metadata():

    url = (
        "https://api.rainviewer.com/"
        "public/weather-maps.json"
    )

    r = requests.get(
        url,
        timeout=15
    )

    r.raise_for_status()

    return r.json()


def latlon_to_pixel(
    lat,
    lon,
    zoom
):

    n = 2 ** zoom

    x = (
        (lon + 180)
        / 360
        * n
    )

    lat_rad = math.radians(lat)

    y = (
        1
        -
        math.asinh(
            math.tan(lat_rad)
        )
        / math.pi
    ) / 2 * n

    return x, y


# ============================================================
# COLORE UNIVERSAL BLUE -> DBZ
# ============================================================

# RainViewer Universal Blue:
# 20 dBZ = azzurro
# 35 dBZ = giallo
# 40+ = arancio
# 45+ = rosso
# 55+ = magenta
#
# Usiamo una classificazione robusta per fasce.

def color_to_dbz(r, g, b, a):

    if a < 40:
        return None

    # 65+ dBZ = bianco
    if r > 240 and g > 240 and b > 240:
        return 65

    # 55-64 = magenta
    if r > 180 and b > 150:
        return 57

    # 45-54 = rosso
    if r > 120 and g < 100 and b < 100:
        return 50

    # 35-44 = arancione/giallo
    if r > 180 and g > 100 and b < 80:

        if g > 180:
            return 36

        return 42

    # 20-34 = blu/ciano
    if b > 100:

        if g > 120:
            return 25

        return 20

    return None


# ============================================================
# SCARICA TILE RADAR
# ============================================================

def get_radar_tile(
    host,
    path,
    lat,
    lon
):

    try:

        from PIL import Image
        from io import BytesIO

    except ImportError:

        print(
            "Pillow non installato"
        )

        return None

    zoom = 7

    x_float, y_float = latlon_to_pixel(
        lat,
        lon,
        zoom
    )

    tile_x = int(x_float)
    tile_y = int(y_float)

    # Universal Blue = color 2
    # 1_0 = smooth + no snow
    url = (
        f"{host}{path}/512/"
        f"{zoom}/{tile_x}/{tile_y}/"
        "2/1_0.png"
    )

    try:

        r = requests.get(
            url,
            timeout=15
        )

        r.raise_for_status()

        image = Image.open(
            BytesIO(r.content)
        ).convert("RGBA")

        return (
            image,
            tile_x,
            tile_y,
            zoom
        )

    except Exception as e:

        print(
            "Errore download radar:",
            e
        )

        return None


# ============================================================
# ANALISI FRAME RADAR
# ============================================================

def analyze_radar_frame(
    host,
    frame,
    lat,
    lon
):

    result = get_radar_tile(
        host,
        frame["path"],
        lat,
        lon
    )

    if result is None:
        return None

    image, tile_x, tile_y, zoom = result

    x_float, y_float = latlon_to_pixel(
        lat,
        lon,
        zoom
    )

    px = int(
        (x_float - tile_x)
        * image.width
    )

    py = int(
        (y_float - tile_y)
        * image.height
    )

    # Circa 120 km
    radius_px = 180

    pixels = []

    for y in range(
        max(0, py - radius_px),
        min(
            image.height,
            py + radius_px
        ),
        4
    ):

        for x in range(
            max(0, px - radius_px),
            min(
                image.width,
                px + radius_px
            ),
            4
        ):

            r, g, b, a = image.getpixel(
                (x, y)
            )

            dbz = color_to_dbz(
                r,
                g,
                b,
                a
            )

            if dbz is None:
                continue

            global_x = (
                tile_x
                +
                x / image.width
            )

            global_y = (
                tile_y
                +
                y / image.height
            )

            n = 2 ** zoom

            lon_p = (
                global_x
                / n
                * 360
                - 180
            )

            lat_p = math.degrees(
                math.atan(
                    math.sinh(
                        math.pi
                        *
                        (
                            1
                            -
                            2
                            * global_y
                            / n
                        )
                    )
                )
            )

            distance = haversine_km(
                lat,
                lon,
                lat_p,
                lon_p
            )

            if distance <= RADAR_RAGGIO_KM:

                pixels.append(
                    (
                        lat_p,
                        lon_p,
                        dbz,
                        distance
                    )
                )

    if not pixels:
        return None

    strong = [
        p
        for p in pixels
        if p[2] >= RADAR_FORTE
    ]

    if not strong:

        strong = [
            p
            for p in pixels
            if p[2] >= RADAR_DEBOLE
        ]

    if not strong:
        return None

    weights = [
        max(
            1,
            p[2] - 15
        )
        for p in strong
    ]

    total = sum(weights)

    cell_lat = sum(
        p[0] * w
        for p, w in zip(
            strong,
            weights
        )
    ) / total

    cell_lon = sum(
        p[1] * w
        for p, w in zip(
            strong,
            weights
        )
    ) / total

    max_dbz = max(
        p[2]
        for p in pixels
    )

    distance = haversine_km(
        lat,
        lon,
        cell_lat,
        cell_lon
    )

    return {
        "lat": cell_lat,
        "lon": cell_lon,
        "distance_km": distance,
        "dbz": max_dbz,
        "time": frame["time"]
    }


# ============================================================
# ANALISI MOVIMENTO RADAR
# ============================================================

def analyze_radar(
    lat,
    lon
):

    try:

        metadata = get_radar_metadata()

    except Exception as e:

        print(
            "Radar non disponibile:",
            e
        )

        return None

    host = metadata.get(
        "host"
    )

    frames = (
        metadata
        .get("radar", {})
        .get("past", [])
    )

    if not host or not frames:
        return None

    frames = frames[
        -RADAR_FRAME_COUNT:
    ]

    observations = []

    for frame in frames:

        obs = analyze_radar_frame(
            host,
            frame,
            lat,
            lon
        )

        if obs:

            observations.append(
                obs
            )

        time.sleep(0.1)

    if not observations:
        return None

    observations.sort(
        key=lambda x: x["time"]
    )

    latest = observations[-1]

    if len(observations) < 2:

        return {
            "latest": latest,
            "approaching": False,
            "eta": None,
            "speed": None,
            "direction": None
        }

    first = observations[0]
    last = observations[-1]

    dt = (
        last["time"]
        - first["time"]
    )

    if dt <= 0:

        return {
            "latest": latest,
            "approaching": False,
            "eta": None,
            "speed": None,
            "direction": None
        }

    movement_distance = haversine_km(
        first["lat"],
        first["lon"],
        last["lat"],
        last["lon"]
    )

    speed = (
        movement_distance
        /
        (dt / 3600)
    )

    approaching = (
        last["distance_km"]
        <
        first["distance_km"]
        - MIN_MOVIMENTO_KM
    )

    eta = None

    if approaching and speed > 3:

        eta = (
            last["distance_km"]
            / speed
            * 60
        )

    direction = bearing_deg(
        first["lat"],
        first["lon"],
        last["lat"],
        last["lon"]
    )

    return {
        "latest": latest,
        "approaching": approaching,
        "eta": eta,
        "speed": speed,
        "direction": direction
    }


# ============================================================
# INTERPRETAZIONE RADAR
# ============================================================

def interpret_radar(
    radar
):

    result = {
        "score": 0,
        "alerts": [],
        "details": [],
        "approaching": False,
        "eta": None,
        "dbz": 0
    }

    if not radar:
        return result

    latest = radar["latest"]

    dbz = latest["dbz"]

    result["dbz"] = dbz

    if dbz >= RADAR_SEVERO:

        result["score"] += 5

        result["alerts"].append(
            f"📡 NUCLEO RADAR MOLTO INTENSO "
            f"({dbz:.0f} dBZ)"
        )

    elif dbz >= RADAR_FORTE:

        result["score"] += 3

        result["alerts"].append(
            f"📡 PRECIPITAZIONE RADAR FORTE "
            f"({dbz:.0f} dBZ)"
        )

    elif dbz >= RADAR_DEBOLE:

        result["score"] += 1

        result["alerts"].append(
            f"📡 PRECIPITAZIONE RADAR "
            f"({dbz:.0f} dBZ)"
        )

    if radar["approaching"]:

        result["approaching"] = True
        result["score"] += 3

        eta = radar["eta"]

        if eta is not None:

            result["eta"] = eta

            if eta <= 15:

                result["alerts"].append(
                    f"🚨 CELLULA IN ARRIVO: "
                    f"ETA ~{eta:.0f} minuti"
                )

            elif eta <= 30:

                result["alerts"].append(
                    f"⚠️ CELLULA IN AVVICINAMENTO: "
                    f"ETA ~{eta:.0f} minuti"
                )

            else:

                result["alerts"].append(
                    f"📡 CELLULA IN AVVICINAMENTO: "
                    f"ETA ~{eta:.0f} minuti"
                )

    if radar["speed"] is not None:

        direction = direction_name(
            radar["direction"]
        )

        result["details"].append(
            f"Movimento stimato: "
            f"{radar['speed']:.0f} km/h verso {direction}"
        )

    return result


# ============================================================
# LIVELLO RISCHIO
# ============================================================

def get_level(
    forecast_score,
    radar_score
):

    score = (
        forecast_score
        + radar_score
    )

    if score >= 12:
        return "ROSSO", score

    if score >= 7:
        return "ARANCIONE", score

    if score >= 3:
        return "GIALLO", score

    return "VERDE", score


# ============================================================
# STADIO ETA
# ============================================================

def eta_stage(eta):

    if eta is None:
        return "none"

    if eta <= 15:
        return "15"

    if eta <= 30:
        return "30"

    if eta <= 60:
        return "60"

    return "later"


# ============================================================
# DECIDE SE NOTIFICARE
# ============================================================

def should_notify(
    previous,
    level,
    score,
    eta,
    alerts
):

    old_level = previous.get(
        "level",
        "VERDE"
    )

    old_score = previous.get(
        "score",
        0
    )

    old_eta_stage = previous.get(
        "eta_stage",
        "none"
    )

    new_eta_stage = eta_stage(
        eta
    )

    # Prima allerta
    if level != "VERDE" and old_level == "VERDE":
        return True

    # Peggioramento del livello
    levels = {
        "VERDE": 0,
        "GIALLO": 1,
        "ARANCIONE": 2,
        "ROSSO": 3
    }

    if (
        levels[level]
        >
        levels.get(old_level, 0)
    ):
        return True

    # Score peggiorato molto
    if score >= old_score + 3:
        return True

    # Passaggio ETA importante
    eta_priority = {
        "none": 0,
        "later": 1,
        "60": 2,
        "30": 3,
        "15": 4
    }

    if (
        eta_priority[new_eta_stage]
        >
        eta_priority.get(
            old_eta_stage,
            0
        )
    ):

        return True

    # Non ripetere continuamente
    return False


# ============================================================
# PARCHEGGI
# ============================================================

def cerca_parcheggi(
    lat,
    lon
):

    query = f"""
    [out:json][timeout:25];

    (
      nwr["amenity"="parking"]
         ["parking"~"multi-storey|underground"]
         (around:{RAGGIO_RICERCA_METRI},{lat},{lon});

      nwr["amenity"="parking"]
         ["covered"="yes"]
         (around:{RAGGIO_RICERCA_METRI},{lat},{lon});
    );

    out center tags;
    """

    try:

        r = requests.post(
            "https://overpass-api.de/api/interpreter",
            data={
                "data": query
            },
            timeout=25
        )

        r.raise_for_status()

        elements = r.json().get(
            "elements",
            []
        )

    except Exception as e:

        print(
            "Errore parcheggi:",
            e
        )

        return []

    result = []

    for el in elements:

        lat_el = (
            el.get("lat")
            or
            el.get(
                "center",
                {}
            ).get("lat")
        )

        lon_el = (
            el.get("lon")
            or
            el.get(
                "center",
                {}
            ).get("lon")
        )

        if (
            lat_el is None
            or lon_el is None
        ):
            continue

        tags = el.get(
            "tags",
            {}
        )

        nome = tags.get(
            "name",
            "Parcheggio coperto"
        )

        dist = haversine_km(
            lat,
            lon,
            lat_el,
            lon_el
        )

        result.append({
            "nome": nome,
            "dist": round(
                dist,
                1
            ),
            "lat": lat_el,
            "lon": lon_el
        })

    result.sort(
        key=lambda x: x["dist"]
    )

    return result[:3]


def format_parcheggi(
    parcheggi
):

    if not parcheggi:

        return (
            "🅿️ Nessun parcheggio coperto "
            "trovato entro 3 km."
        )

    lines = [
        "🅿️ PARCHEGGI COPERTI VICINI:"
    ]

    for p in parcheggi:

        maps = (
            "https://www.google.com/maps/search/"
            "?api=1"
            f"&query={p['lat']},{p['lon']}"
        )

        lines.append(
            f"- {p['nome']} "
            f"({p['dist']} km)\n"
            f"  {maps}"
        )

    return "\n".join(lines)


# ============================================================
# NOTIFICA NTFY
# ============================================================

def send_notification(
    message,
    level,
    hail_risk=False
):

    if not NTFY_TOPIC:
        print("NTFY_TOPIC non impostato.")
        return False

    if hail_risk:
        title = "🧊 ALLERTA GRANDINE"
        priority = "max"

    elif level == "ROSSO":
        title = "🚨 ALLERTA METEO ROSSA"
        priority = "max"

    elif level == "ARANCIONE":
        title = "🟠 ALLERTA METEO"
        priority = "urgent"

    else:
        title = "🟡 AVVISO METEO"
        priority = "high"

    try:
        r = requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers={
                "Title": title,
                "Priority": priority,
                "Tags": "warning"
            },
            timeout=15
        )

        r.raise_for_status()
        return True

    except Exception as e:
        print("Errore ntfy:", e)
        return False


# ============================================================
# MAIN
# ============================================================

def main():

    lat, lon = get_location()

    print(
        f"Posizione: {lat}, {lon}"
    )

    previous = get_previous_state()

    # --------------------------------------------------------
    # METEO
    # --------------------------------------------------------

    try:

        weather = get_weather(
            lat,
            lon
        )

        forecast = analyze_forecast(
            weather
        )

    except Exception as e:

        print(
            "Errore Open-Meteo:",
            e
        )

        return

    # --------------------------------------------------------
    # RADAR
    # --------------------------------------------------------

    radar = analyze_radar(
        lat,
        lon
    )

    radar_result = interpret_radar(
        radar
    )

    # --------------------------------------------------------
    # LIVELLO
    # --------------------------------------------------------

    level, score = get_level(
        forecast["score"],
        radar_result["score"]
    )

    eta = radar_result[
        "eta"
    ]

    # --------------------------------------------------------
    # DECIDI NOTIFICA
    # --------------------------------------------------------

    notify = True

    # --------------------------------------------------------
    # AGGIORNA STATO ANCHE SENZA NOTIFICA
    # --------------------------------------------------------

    state = {
        "level": level,
        "score": score,
        "eta_stage": eta_stage(eta),
        "last_alert_type": (
            "|".join(
                forecast["alerts"]
                +
                radar_result["alerts"]
            )
        )[:500],
        "timestamp": int(
            datetime.now(
                timezone.utc
            ).timestamp()
        )
    }

    # Verde: se prima c'era allerta,
    # resettiamo lo stato ma non mandiamo
    # una notifica ogni volta.
    if level == "VERDE":

        if previous.get(
            "level"
        ) != "VERDE":

            state["last_alert_type"] = ""

        save_state(state)

        print(
            "🟢 Nessun rischio significativo."
        )

        return

    # --------------------------------------------------------
    # SE NON SERVE NOTIFICA
    # --------------------------------------------------------

    if not notify:

        save_state(state)

        print(
            "Nessuna nuova notifica. "
            f"Livello attuale: {level}, "
            f"score: {score}"
        )

        return

    # --------------------------------------------------------
    # PARCHEGGI
    # --------------------------------------------------------

    auto_risk = (
        forecast["hail_risk"]
        or
        radar_result["dbz"]
        >= RADAR_GRANDINE
        or
        forecast["max_gust"]
        >= VENTO_FORTE
        or
        level == "ROSSO"
    )

    parcheggi = []

    if auto_risk:

        parcheggi = cerca_parcheggi(
            lat,
            lon
        )

    # --------------------------------------------------------
    # MESSAGGIO
    # --------------------------------------------------------

    message = (
        f"{'🚨' if level == 'ROSSO' else '⚠️'} "
        f"ALLERTA METEO {level}\n\n"
    )

    if forecast["alerts"]:

        message += (
            "🌦️ PREVISIONE:\n"
        )

        message += "\n".join(
            forecast["alerts"]
        )

        message += "\n"

    if radar_result["alerts"]:

        message += (
            "\n📡 RADAR:\n"
        )

        message += "\n".join(
            radar_result["alerts"]
        )

        message += "\n"

    details = (
        forecast["details"]
        +
        radar_result["details"]
    )

    if details:

        message += (
            "\n📋 DETTAGLI:\n"
        )

        message += "\n".join(
            f"- {d}"
            for d in details
        )

    # --------------------------------------------------------
    # PROTEZIONE AUTO
    # --------------------------------------------------------

    if auto_risk:

        message += (
            "\n\n🚗 PROTEZIONE AUTO\n"
            "Condizioni potenzialmente "
            "pericolose per il veicolo."
        )

        if forecast["hail_risk"]:

            message += (
                "\n🧊 RISCHIO GRANDINE: "
                "PROTEGGERE L'AUTO"
            )

        if (
            radar_result["approaching"]
            and eta is not None
        ):

            if eta <= 15:

                message += (
                    f"\n🚨 CELLULA IN ARRIVO: "
                    f"circa {eta:.0f} minuti"
                    "\n➡️ PROTEGGERE L'AUTO ORA"
                )

            elif eta <= 30:

                message += (
                    f"\n⏱️ ARRIVO STIMATO: "
                    f"circa {eta:.0f} minuti"
                    "\n➡️ PREPARARE L'AUTO"
                )

            else:

                message += (
                    f"\n⏱️ ARRIVO STIMATO: "
                    f"circa {eta:.0f} minuti"
                )

        message += (
            "\n\n"
            +
            format_parcheggi(
                parcheggi
            )
        )

    # --------------------------------------------------------
    # INFO
    # --------------------------------------------------------

    message += (
        "\n\n📊 Indice rischio: "
        f"{score}"
    )

    message += (
        "\n📍 Posizione: "
        f"{lat}, {lon}"
    )

    message += (
        "\n\n📡 Dati radar: RainViewer"
    )

    message += (
        "\n🌦️ Previsioni: Open-Meteo"
    )

    # --------------------------------------------------------
    # INVIA
    # --------------------------------------------------------

if send_notification(
    message,
    level,
    True
):
):
    save_state(state)

    print(
        "Notifica inviata."
    )

    else:

        print(
            "Notifica non inviata."
        )


if __name__ == "__main__":
    main()
