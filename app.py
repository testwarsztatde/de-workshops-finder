
# app_postcode.py — DE Workshops Finder (postal code only)
# Wejście: tylko niemiecki kod pocztowy (np. 10585). Reszta jak w wersji XL, ale uproszczona.

import os
import time
import math
import requests
import pandas as pd
import streamlit as st
from io import BytesIO

CONTACT_EMAIL = os.getenv("CONTACT_EMAIL", "wisniewskia579@gmai.com")
USER_AGENT = f"de-workshop-finder/zip/1.0 (mailto:{CONTACT_EMAIL})"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"

st.set_page_config(page_title="Wyszukiwanie warsztatów w DE - wersja test Przemo", page_icon="📮", layout="wide")
st.title("📮 Wyszukiwanie warsztatów w DE - wersja test Przemo")

# --- Helpery ---
def haversine_km(lat1, lon1, lat2, lon2):
    if None in (lat1, lon1, lat2, lon2):
        return None
    R = 6371.0088
    import math
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlmb/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return round(R * c, 2)

def pick(d, *keys):
    for k in keys:
        v = d.get(k)
        if v: return v
    return ""

def norm(s): return (s or "").strip()

def full_address(tags):
    parts = [tags.get("addr:street",""), tags.get("addr:housenumber","")]
    return " ".join([p for p in parts if p])

@st.cache_data(show_spinner=False, ttl=3600)
def geocode_postcode(plz: str):
    q = f"{plz} Germany"
    r = requests.get(NOMINATIM_URL, params={
        "q": q, "format": "json", "limit": 1, "countrycodes": "de", "email": CONTACT_EMAIL
    }, headers={"User-Agent": USER_AGENT}, timeout=30)
    r.raise_for_status()
    data = r.json()
    if not data:
        return None, None, None
    it = data[0]
    return float(it["lat"]), float(it["lon"]), it.get("display_name", q)

def build_query(lat, lon, radius_m):
    blocks = [
        f'node["shop"="car_repair"](around:{radius_m},{lat},{lon});',
        f'way["shop"="car_repair"](around:{radius_m},{lat},{lon});',
        f'node["amenity"="car_repair"](around:{radius_m},{lat},{lon});',
        f'way["amenity"="car_repair"](around:{radius_m},{lat},{lon});',
        f'node["shop"="car_parts"](around:{radius_m},{lat},{lon});',
        f'way["shop"="car_parts"](around:{radius_m},{lat},{lon});',
        f'node["shop"="tyres"](around:{radius_m},{lat},{lon});',
        f'way["shop"="tyres"](around:{radius_m},{lat},{lon});',
        f'node["craft"="mechanic"](around:{radius_m},{lat},{lon});',
        f'way["craft"="mechanic"](around:{radius_m},{lat},{lon});',
    ]
    return f"""
[out:json][timeout:60];
(
  {''.join(blocks)}
);
out tags center;
"""

@st.cache_data(show_spinner=False, ttl=1800)
def overpass_search(query: str):
    r = requests.post(OVERPASS_URL, data=query.encode("utf-8"),
                      headers={"User-Agent": USER_AGENT}, timeout=120)
    if r.status_code == 429:
        time.sleep(2)
        r = requests.post(OVERPASS_URL, data=query.encode("utf-8"),
                          headers={"User-Agent": USER_AGENT}, timeout=120)
    r.raise_for_status()
    return r.json().get("elements", [])

def rows_from_elements(elements, lat0, lon0):
    rows = []
    seen = set()
    for el in elements:
        tags = el.get("tags", {}) or {}
        name = norm(tags.get("name")) or "(brak nazwy)"
        street = norm(tags.get("addr:street"))
        nr = norm(tags.get("addr:housenumber"))
        kod = norm(tags.get("addr:postcode"))
        miasto = norm(pick(tags, "addr:city", "addr:town", "addr:village"))
        lat = el.get("lat") or (el.get("center", {}) or {}).get("lat")
        lon = el.get("lon") or (el.get("center", {}) or {}).get("lon")
        phone = norm(pick(tags, "contact:phone", "phone"))
        www = norm(pick(tags, "contact:website", "website"))
        dist = haversine_km(lat0, lon0, lat, lon) if lat and lon else None

        key = (name.lower(), street.lower(), nr.lower(), kod.lower(), miasto.lower())
        if key in seen:
            continue
        seen.add(key)

        rows.append({
            "Nazwa": name,
            "Ulica": street,
            "Nr": nr,
            "Kod": kod,
            "Miasto": miasto,
            "Adres (łączny)": full_address(tags),
            "Telefon": phone,
            "WWW": www,
            "Lat": lat,
            "Lon": lon,
            "Dystans (km)": dist,
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(["Dystans (km)", "Miasto", "Nazwa"], na_position="last").reset_index(drop=True)
    return df

# --- UI ---
plz = st.text_input("Kod pocztowy (DE)", value="10585", help="Wpisz 5 cyfr, np. 10585, 80331, 20095")
radius_km = st.slider("Promień (km)", 1, 50, 25)
if st.button("Szukaj", type="primary"):
    if not plz.isdigit() or len(plz) not in (4,5):
        st.error("Podaj prawidłowy kod pocztowy (4-5 cyfr).")
    else:
        try:
            with st.spinner("Geokodowanie…"):
                lat0, lon0, disp = geocode_postcode(plz)
            if not lat0:
                st.warning("Nie znaleziono lokalizacji dla tego kodu.")
            else:
                st.success(f"Szukam w promieniu {radius_km} km od: {disp}")
                q = build_query(lat0, lon0, int(radius_km*1000))
                with st.spinner("Pobieranie danych OSM…"):
                    elements = overpass_search(q)
                df = rows_from_elements(elements, lat0, lon0)
                st.subheader(f"Wyniki: {len(df)}")
                if df.empty:
                    st.info("Brak wyników. Zwiększ promień lub sprawdź kod.")
                else:
                    st.dataframe(df[
                        ["Nazwa","Adres (łączny)","Kod","Miasto","Telefon","WWW","Dystans (km)"]
                    ], use_container_width=True, hide_index=True)
                    m = df.dropna(subset=["Lat","Lon"]).rename(columns={"Lat":"lat","Lon":"lon"})
                    if not m.empty:
                        st.map(m[["lat","lon"]], use_container_width=True)
                    st.download_button("Pobierz CSV", df.to_csv(index=False).encode("utf-8"),
                                       "wyniki_plz.csv", "text/csv")
                    buf = BytesIO()
                    with pd.ExcelWriter(buf, engine="openpyxl") as w:
                        df.to_excel(w, index=False, sheet_name="Wyniki")
                    st.download_button("Pobierz XLSX", buf.getvalue(),
                                       "wyniki_plz.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        except requests.HTTPError as e:
            st.error(f"Błąd HTTP: {e}")
        except Exception as e:
            st.error(f"Nieoczekiwany błąd: {e}")
