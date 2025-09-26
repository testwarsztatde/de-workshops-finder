# app.py — DE Workshops/Parts Finder (clean, verified)
# Szuka warsztatów i sklepów w DE na podstawie OSM (Overpass).
# Szerokie tagi: car_repair, amenity=car_repair, shop=repair (auta), craft=mechanic,
# salony z serwisem (shop=car + service:*), wulkanizacje (shop=tyres), sklepy części (shop=car_parts).
# Funkcje: geokodowanie (Nominatim + Photon fallback), dystans (km), tabela + mapa, eksport CSV/XLSX.

import time
import math
import requests
import pandas as pd
import streamlit as st
from io import BytesIO

# --- Konfiguracja ---
CONTACT_EMAIL = "wisniewskia579@gmail.com"  # <= podaj swój email (wymóg Nominatim)
USER_AGENT = f"de-workshop-finder/1.4 (mailto:{CONTACT_EMAIL})"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
PHOTON_URL = "https://photon.komoot.io/api/"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"

st.set_page_config(page_title="DE Workshops/Parts Finder", page_icon="🔧", layout="wide")
st.title("🔧 DE Workshops/Parts Finder — szerokie wykrywanie (OSM)")
st.caption("OpenStreetMap (Overpass) • CSV/XLSX • Dystans (km) • Geokodowanie bez kluczy (z podanym mailem)")

# --- Sidebar ---
with st.sidebar:
    st.header("Parametry wyszukiwania")
    place = st.text_input(
        "Lokalizacja (Niemcy)",
        value="10585 Berlin, DE",
        help="Miasto/adres/kod, np. '10585 Berlin, DE' albo 'Dresden, DE'."
    )
    radius_km = st.slider("Promień (km)", 1, 30, 20)
    st.markdown("**Źródła w OSM:**")
    filt_workshops = st.checkbox("Warsztaty (szerokie tagi)", True)
    filt_parts = st.checkbox("Sklepy z częściami (shop=car_parts)", True)
    include_tyres = st.checkbox("Wulkanizacje (shop=tyres)", True)
    st.markdown("---")
    deduplicate = st.checkbox("Deduplikuj podobne rekordy", True)
    debug = st.checkbox("Pokaż liczbę elementów z Overpass (debug)", False)
    run_btn = st.button("🔍 Szukaj", use_container_width=True)

# --- Helpery ---
def haversine_km(lat1, lon1, lat2, lon2):
    if None in (lat1, lon1, lat2, lon2):
        return None
    R = 6371.0088
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlmb/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return round(R * c, 2)

def pick(d, *keys):
    for k in keys:
        v = d.get(k)
        if v:
            return v
    return ""

def norm(s):
    return (s or "").strip()

def full_address(tags):
    parts = [tags.get("addr:street", ""), tags.get("addr:housenumber", "")]
    return " ".join([p for p in parts if p])

# --- Geokodowanie (Nominatim + Photon fallback) ---
@st.cache_data(show_spinner=False, ttl=3600)
def geocode_place(q: str):
    # Nominatim
    try:
        r = requests.get(
            NOMINATIM_URL,
            params={
                "q": q, "format": "json", "limit": 1, "countrycodes": "de", "email": CONTACT_EMAIL
            },
            headers={"User-Agent": USER_AGENT, "Referer": "https://localhost"},
            timeout=30,
        )
        if r.status_code == 200 and r.json():
            it = r.json()[0]
            return float(it["lat"]), float(it["lon"]), it.get("display_name", q)
    except Exception:
        pass
    # Photon fallback
    try:
        r2 = requests.get(
            PHOTON_URL,
            params={"q": q, "limit": 1, "lang": "de"},
            headers={"User-Agent": USER_AGENT},
            timeout=30,
        )
        if r2.status_code == 200:
            data = r2.json()
            feats = data.get("features", [])
            for f in feats:
                props = f.get("properties", {}) or {}
                if props.get("countrycode", "").lower() != "de":
                    continue
                coords = (f.get("geometry", {}) or {}).get("coordinates", [])
                if len(coords) >= 2:
                    lon, lat = coords[0], coords[1]
                    return float(lat), float(lon), props.get("name") or q
    except Exception:
        pass
    return None, None, None

# --- Budowa zapytania Overpass ---
def build_overpass_query(lat: float, lon: float, radius_m: int,
                         want_workshops: bool, want_parts: bool, want_tyres: bool) -> str:
    blocks = []
    if want_workshops:
        # Główne i legacy tagi + szerokie heurystyki
        blocks += [
            f'node["shop"="car_repair"](around:{radius_m},{lat},{lon});',
            f'way["shop"="car_repair"](around:{radius_m},{lat},{lon});',
            f'relation["shop"="car_repair"](around:{radius_m},{lat},{lon});',
            f'node["amenity"="car_repair"](around:{radius_m},{lat},{lon});',
            f'way["amenity"="car_repair"](around:{radius_m},{lat},{lon});',
            f'relation["amenity"="car_repair"](around:{radius_m},{lat},{lon});',
            f'node["shop"="repair"]["repair"~"^car$|^auto$|^vehicle$|^automobile$|cars?"](around:{radius_m},{lat},{lon});',
            f'way["shop"="repair"]["repair"~"^car$|^auto$|^vehicle$|^automobile$|cars?"](around:{radius_m},{lat},{lon});',
            f'relation["shop"="repair"]["repair"~"^car$|^auto$|^vehicle$|^automobile$|cars?"](around:{radius_m},{lat},{lon});',
            f'node["craft"="mechanic"](around:{radius_m},{lat},{lon});',
            f'way["craft"="mechanic"](around:{radius_m},{lat},{lon});',
            f'relation["craft"="mechanic"](around:{radius_m},{lat},{lon});',
            f'node["shop"="car"]["service"~"vehicle|repair|service"](around:{radius_m},{lat},{lon});',
            f'way["shop"="car"]["service"~"vehicle|repair|service"](around:{radius_m},{lat},{lon});',
            f'relation["shop"="car"]["service"~"vehicle|repair|service"](around:{radius_m},{lat},{lon});',
            f'node["shop"="car"]["service:vehicle"~"yes|repair|service"](around:{radius_m},{lat},{lon});',
            f'way["shop"="car"]["service:vehicle"~"yes|repair|service"](around:{radius_m},{lat},{lon});',
            f'relation["shop"="car"]["service:vehicle"~"yes|repair|service"](around:{radius_m},{lat},{lon});',
            f'node["shop"="car"]["service:vehicle:car_repair"~"yes"](around:{radius_m},{lat},{lon});',
            f'way["shop"="car"]["service:vehicle:car_repair"~"yes"](around:{radius_m},{lat},{lon});',
            f'relation["shop"="car"]["service:vehicle:car_repair"~"yes"](around:{radius_m},{lat},{lon});',
        ]
    if want_parts:
        blocks += [
            f'node["shop"="car_parts"](around:{radius_m},{lat},{lon});',
            f'way["shop"="car_parts"](around:{radius_m},{lat},{lon});',
            f'relation["shop"="car_parts"](around:{radius_m},{lat},{lon});',
        ]
    if want_tyres:
        blocks += [
            f'node["shop"="tyres"](around:{radius_m},{lat},{lon});',
            f'way["shop"="tyres"](around:{radius_m},{lat},{lon});',
            f'relation["shop"="tyres"](around:{radius_m},{lat},{lon});',
        ]

    if not blocks:
        return ""

    query = f"""
[out:json][timeout:90];
(
  {''.join(blocks)}
);
out tags center;
"""
    return query

# --- Overpass ---
@st.cache_data(show_spinner=False, ttl=1800)
def overpass_search(query: str):
    headers = {"User-Agent": USER_AGENT}
    r = requests.post(OVERPASS_URL, data=query.encode("utf-8"), headers=headers, timeout=180)
    if r.status_code == 429:
        time.sleep(2)
        r = requests.post(OVERPASS_URL, data=query.encode("utf-8"), headers=headers, timeout=180)
    r.raise_for_status()
    return r.json().get("elements", [])

# --- Transformacja wyników ---
def rows_from_elements(elements: list, lat0: float, lon0: float, do_dedupe: bool = True) -> pd.DataFrame:
    rows = []
    seen = set()
    for el in elements:
        tags = el.get("tags", {})
        name = norm(tags.get("name", "")) or "(brak nazwy)"
        street = norm(tags.get("addr:street", ""))
        nr = norm(tags.get("addr:housenumber", ""))
        kod = norm(tags.get("addr:postcode", ""))
        miasto = norm(pick(tags, "addr:city", "addr:town", "addr:village"))
        lat = el.get("lat") or (el.get("center", {}) or {}).get("lat")
        lon = el.get("lon") or (el.get("center", {}) or {}).get("lon")
        phone = norm(pick(tags, "contact:phone", "phone"))
        www = norm(pick(tags, "contact:website", "website"))
        typ = "Inne"
        if tags.get("shop") == "car_repair" or tags.get("amenity") == "car_repair":
            typ = "Warsztat"
        elif tags.get("shop") == "repair" and any(k in (tags.get("repair","")) for k in ["car","vehicle","auto","automobile","cars"]):
            typ = "Naprawy (shop=repair)"
        elif tags.get("shop") == "car":
            s = (tags.get("service","") + " " + tags.get("service:vehicle","") + " " + tags.get("service:vehicle:car_repair",""))
            if any(x in s for x in ["vehicle","repair","service","yes"]):
                typ = "Salon z serwisem"
        elif tags.get("shop") == "tyres":
            typ = "Wulkanizacja"
        elif tags.get("shop") == "car_parts":
            typ = "Sklep z częściami"

        if do_dedupe:
            key = (name.lower(), street.lower(), nr.lower(), kod.lower(), miasto.lower())
            if key in seen:
                continue
            seen.add(key)

        dist = haversine_km(lat0, lon0, lat, lon) if lat and lon else None
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
            "Typ": typ,
            "Dystans (km)": dist,
        })

    df = pd.DataFrame(rows, columns=[
        "Nazwa","Ulica","Nr","Kod","Miasto","Adres (łączny)",
        "Telefon","WWW","Lat","Lon","Typ","Dystans (km)"
    ])
    if not df.empty:
        df = df.sort_values(["Dystans (km)", "Miasto", "Nazwa"], na_position="last").reset_index(drop=True)
    return df

# --- UI logika ---
if run_btn:
    with st.spinner("Geokodowanie…"):
        lat0, lon0, disp = geocode_place(place)
    if not lat0:
        st.error("Nie udało się zgeokodować lokalizacji. Zmień zapis (np. '10585 Berlin, DE').")
        st.stop()

    st.success(f"Szukam w promieniu {radius_km} km od: {disp} ({lat0:.5f}, {lon0:.5f})")

    q = build_overpass_query(lat0, lon0, int(radius_km*1000), filt_workshops, filt_parts, include_tyres)
    if not q:
        st.warning("Zaznacz przynajmniej jedną kategorię.")
        st.stop()

    try:
        with st.spinner("Pobieranie danych z Overpass…"):
            elements = overpass_search(q)
    except requests.HTTPError as e:
        st.error(f"Błąd Overpass: {e}")
        st.stop()
    except Exception as e:
        st.error(f"Nieoczekiwany błąd: {e}")
        st.stop()

    if debug:
        st.caption(f"Surowych elementów z Overpass: {len(elements)}")

    df = rows_from_elements(elements, lat0, lon0, do_dedupe=deduplicate)
    st.subheader(f"Wyniki: {len(df)} rekordów")

    if df.empty:
        st.info("Brak wyników. Zwiększ promień lub zmień lokalizację.")
    else:
        st.dataframe(
            df[["Nazwa","Adres (łączny)","Kod","Miasto","Telefon","WWW","Typ","Dystans (km)"]],
            use_container_width=True, hide_index=True
        )

        df_map = df.dropna(subset=["Lat","Lon"]).rename(columns={"Lat":"lat","Lon":"lon"})
        if not df_map.empty:
            st.map(df_map[["lat","lon"]], use_container_width=True)

        # Eksporty
        csv_bytes = df.to_csv(index=False).encode("utf-8")
        st.download_button("⬇️ Pobierz CSV", data=csv_bytes, file_name="de_workshops.csv", mime="text/csv", use_container_width=True)

        xlsx_buf = BytesIO()
        with pd.ExcelWriter(xlsx_buf, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Wyniki")
        st.download_button(
            "⬇️ Pobierz XLSX",
            data=xlsx_buf.getvalue(),
            file_name="de_workshops.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True
        )

with st.expander("ℹ️ Notatki"):
    st.markdown(
        """
- Tagi OSM obejmują m.in.: `shop=car_repair`, `amenity=car_repair`, `shop=repair` (+ `repair=car|vehicle|auto|automobile|cars`),
  `craft=mechanic`, `shop=car` z `service:*`, `shop=tyres`, `shop=car_parts`.
- Dane: **OpenStreetMap** (Overpass API). Dodaj atrybucję ODbL przy publicznym deployu.
- Geokoder: Nominatim wymaga kontaktu (email). Jest fallback na Photon.
- Gdy Overpass zwróci 429, jest prosty retry. Nie odświeżaj zbyt często.
        """
    )
