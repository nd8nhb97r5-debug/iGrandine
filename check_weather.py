import math
import os
import requests

# ============ CONFIGURA QUI ============
DEFAULT_LAT = 45.56895     # <-- le coordinate che avevi gia' inserito (usate solo se non hai mai premuto "Aggiorna posizione")
DEFAULT_LON = 10.02529
FIREBASE_URL = "https://igrandine-default-rtdb.europe-west1.firebasedatabase.app/posizione.json"
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")   # letto dal Secret di GitHub (Fase E) - non scrivere qui il nome del topic
# ========================================

SOGLIA_PIOGGIA_MM_H = 10     # mm/ora di pioggia = temporale forte
SOGLIA_RAFFICHE_KMH = 60     # km/h di raffica di vento
SOGLIA_CAPE = 1500           # indice di energia atmosferica (temporali violenti/grandine)

RAGGIO_RICERCA_METRI = 3000  # raggio in cui cercare parcheggi coperti e rifugi


def get_location():
    """Legge l'ultima posizione salvata dal telefono. Se non c'e', usa quella di riserva."""
    try:
        r = requests.get(FIREBASE_URL, timeout=10)
        r.raise_for_status()
        dati = r.json()
        if dati and "lat" in dati and "lon" in dati:
            return dati["lat"], dati["lon"]
    except Exception:
        pass
    return DEFAULT_LAT, DEFAULT_LON


def get_weather(lat, lon):
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&minutely_15=precipitation,wind_gusts_10m"
        "&hourly=cape"
        "&forecast_days=1"
        "&timezone=auto"
    )
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    return r.json()


def check_risk(data):
    alerts = []
    precip = data.get("minutely_15", {}).get("precipitation", [])
    gusts = data.get("minutely_15", {}).get("wind_gusts_10m", [])
    cape = data.get("hourly", {}).get("cape", [])

    if precip and max(precip[:4]) >= SOGLIA_PIOGGIA_MM_H:
        alerts.append(f"pioggia intensa prevista ({max(precip[:4])} mm/h)")
    if gusts and max(gusts[:4]) >= SOGLIA_RAFFICHE_KMH:
        alerts.append(f"raffiche di vento forti ({max(gusts[:4])} km/h)")
    if cape and max(cape[:3]) >= SOGLIA_CAPE:
        alerts.append(f"alta energia da temporale/rischio grandine (CAPE {max(cape[:3])})")

    return alerts


def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def cerca_rifugi(lat, lon):
    """Cerca su OpenStreetMap parcheggi coperti (auto) e luoghi al chiuso (a piedi/bici) vicini."""
    query = f"""
    [out:json][timeout:25];
    (
      nwr["amenity"="parking"]["parking"~"multi-storey|underground"](around:{RAGGIO_RICERCA_METRI},{lat},{lon});
      nwr["amenity"="parking"]["covered"="yes"](around:{RAGGIO_RICERCA_METRI},{lat},{lon});
      nwr["shop"~"mall|department_store"](around:{RAGGIO_RICERCA_METRI},{lat},{lon});
      nwr["amenity"="shelter"](around:{RAGGIO_RICERCA_METRI},{lat},{lon});
    );
    out center tags;
    """
    try:
        r = requests.post("https://overpass-api.de/api/interpreter", data={"data": query}, timeout=25)
        r.raise_for_status()
        elementi = r.json().get("elements", [])
    except Exception:
        return [], []

    parcheggi, rifugi_pedoni = [], []
    for el in elementi:
        lat_el = el.get("lat") or el.get("center", {}).get("lat")
        lon_el = el.get("lon") or el.get("center", {}).get("lon")
        if lat_el is None or lon_el is None:
            continue
        tags = el.get("tags", {})
        nome = tags.get("name", "Senza nome")
        dist = haversine_km(lat, lon, lat_el, lon_el)
        voce = {"nome": nome, "dist": round(dist, 1), "lat": lat_el, "lon": lon_el}

        if tags.get("amenity") == "parking":
            parcheggi.append(voce)
        else:
            rifugi_pedoni.append(voce)

    parcheggi.sort(key=lambda x: x["dist"])
    rifugi_pedoni.sort(key=lambda x: x["dist"])
    return parcheggi[:3], rifugi_pedoni[:3]


def formatta_luoghi(lista, etichetta):
    if not lista:
        return f"{etichetta}: nessuno trovato entro {RAGGIO_RICERCA_METRI // 1000} km"
    righe = []
    for l in lista:
        link = f"https://www.google.com/maps/search/?api=1&query={l['lat']},{l['lon']}"
        righe.append(f"- {l['nome']} ({l['dist']} km): {link}")
    return f"{etichetta}:\n" + "\n".join(righe)


def send_notification(message):
    if not NTFY_TOPIC:
        print("ATTENZIONE: NTFY_TOPIC non impostato, notifica non inviata")
        return
    requests.post(
        f"https://ntfy.sh/{NTFY_TOPIC}",
        data=message.encode("utf-8"),
        headers={
            "Title": "Rischio maltempo imminente",
            "Priority": "urgent",
            "Tags": "warning",
        },
        timeout=15,
    )


if __name__ == "__main__":
    lat, lon = get_location()
    dati = get_weather(lat, lon)
    allarmi = check_risk(dati)
    if allarmi:
        parcheggi, rifugi = cerca_rifugi(lat, lon)
        messaggio = " | ".join(allarmi) + "\n\n"
        messaggio += formatta_luoghi(parcheggi, "Parcheggi coperti vicini (auto)") + "\n\n"
        messaggio += formatta_luoghi(rifugi, "Rifugi vicini (a piedi/bici)")
        send_notification(messaggio)
        print("Notifica inviata:", allarmi, "- posizione usata:", lat, lon)
    else:
        print("Nessun rischio rilevato. Posizione usata:", lat, lon)
