"""Rebuild src/frontend/src/landmarks.json: well-known Jakarta places for the map at city zoom.

The basemap tiles carry places only from zoom 14, so the frontend bundles this list to show
landmarks from zoom 11 (map-view.ts, addLandmarks). It is a snapshot of OpenStreetMap (ODbL):
places in the study area that have a Wikidata link, a stand-in for "famous". MAJOR_ORDER marks the
ones shown from zoom 11, most famous first; the rest appear from zoom 13.

Usage: python scripts/build_frontend_landmarks.py   (needs internet access to the Overpass API)
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "src/frontend/src/landmarks.json"
OVERPASS = "https://overpass-api.de/api/interpreter"

# Same as STUDY_AREA in src/frontend/src/config.ts, as south,west,north,east.
BBOX = "-6.38,106.68,-6.08,106.98"

MAJOR_ORDER = [
    "Monumen Nasional", "Masjid Istiqlal", "Stadion Utama Gelora Bung Karno", "Bundaran Hotel Indonesia",
    "Kota Tua Jakarta", "Taman Mini Indonesia Indah", "Taman Impian Jaya Ancol", "Kebun Binatang Ragunan",
    "Jakarta International Stadium", "Katedral Jakarta", "Grand Indonesia Shopping Town", "Museum Nasional Indonesia",
    "Istana Merdeka", "Plaza Indonesia", "Universitas Indonesia", "Dunia Fantasi", "Monumen Selamat Datang",
    "Museum Sejarah Jakarta", "Lapangan Banteng", "Indonesia Arena", "Senayan City", "Plaza Senayan", "Sarinah",
    "Pacific Place Mall", "Mall of Indonesia", "Summarecon Mall Kelapa Gading",
]
MAJOR = set(MAJOR_ORDER)

# Famous places that OpenStreetMap has without a Wikidata link (or as another kind of feature).
BY_NAME = ["Plaza Indonesia", "Istana Merdeka", "Kota Tua Jakarta", "Bundaran Hotel Indonesia"]

WIKIDATA_QUERY = f"""[out:json][timeout:120];
(
  nwr["wikidata"]["tourism"~"^(attraction|museum|zoo|theme_park|gallery)$"]({BBOX});
  nwr["wikidata"]["historic"~"^(monument|memorial|castle|fort)$"]({BBOX});
  nwr["wikidata"]["leisure"~"^(stadium|park)$"]({BBOX});
  nwr["wikidata"]["shop"="mall"]({BBOX});
  nwr["wikidata"]["amenity"~"^(place_of_worship|hospital|university|theatre)$"]({BBOX});
);
out center tags;"""

NAME_QUERY = f"""[out:json][timeout:60];
(
  wr["name"~"^({'|'.join(BY_NAME)})$"]({BBOX});
);
out center tags;"""


def overpass(query, attempts=4):
    """Runs one query; the public server is often busy (429/502/503/504), so it waits and retries."""
    body = urllib.parse.urlencode({"data": query}).encode()
    for attempt in range(1, attempts + 1):
        req = urllib.request.Request(OVERPASS, data=body, headers={"User-Agent": "sippolusi-landmark-extract"})
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                return json.load(resp)["elements"]
        except urllib.error.HTTPError as err:
            if err.code not in (429, 502, 503, 504) or attempt == attempts:
                raise
            wait = 10 * attempt
            print(f"Overpass answered {err.code}; retrying in {wait} s")
            time.sleep(wait)


def icon(t):
    """A sprite name of the OpenFreeMap style for the kind of place."""
    tourism, historic, leisure, amenity = t.get("tourism"), t.get("historic"), t.get("leisure"), t.get("amenity")
    if amenity == "place_of_worship":
        return {"muslim": "religious_muslim", "christian": "religious_christian"}.get(t.get("religion"), "place_of_worship")
    if amenity == "hospital":
        return "hospital"
    if amenity == "university":
        return "college"
    if amenity == "theatre":
        return "theatre"
    if t.get("shop") == "mall":
        return "shop"
    if leisure == "stadium":
        return "stadium"
    if leisure == "park":
        return "park"
    if historic in ("monument", "memorial"):
        return "monument"
    if historic in ("castle", "fort", "heritage"):
        return "castle"
    if tourism == "museum":
        return "museum"
    if tourism == "gallery":
        return "art_gallery"
    if tourism == "zoo":
        return "zoo"
    return "attraction"


def main():
    features, seen = [], set()

    def add(e):
        t = e["tags"]
        name = t.get("name")
        # TMII's provincial pavilions ("Anjungan …") would crowd out everything else there.
        if not name or name.startswith("Anjungan") or name == "Pura":
            return
        key = t.get("wikidata") or name
        if key in seen or name in seen:
            return
        seen.update({key, name})
        c = e.get("center") or {"lat": e["lat"], "lon": e["lon"]}
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(c["lon"], 5), round(c["lat"], 5)]},
            "properties": {
                "name": name,
                "icon": icon(t),
                "major": name in MAJOR,
                "rank": MAJOR_ORDER.index(name) + 1 if name in MAJOR else 100,
            },
        })

    for e in overpass(WIKIDATA_QUERY):
        add(e)
    time.sleep(2)  # be gentle with the public Overpass server
    for e in overpass(NAME_QUERY):
        if e["tags"].get("amenity") != "bus_station":
            add(e)

    features.sort(key=lambda f: (f["properties"]["rank"], f["properties"]["name"]))
    data = {
        "type": "FeatureCollection",
        "_source": "OpenStreetMap contributors (ODbL), places with a Wikidata link in the study area, via Overpass API",
        "features": features,
    }
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    missing = sorted(MAJOR - {f["properties"]["name"] for f in features})
    print(f"{len(features)} places, {sum(f['properties']['major'] for f in features)} major -> {OUT.relative_to(ROOT)}")
    if missing:
        print("not found in OpenStreetMap (check the names in MAJOR_ORDER):", ", ".join(missing))


if __name__ == "__main__":
    main()
