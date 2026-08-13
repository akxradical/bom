"""
geo_cost.py — Freight, geocoding, rate database, fuel reference (offline-first)
═══════════════════════════════════════════════════════════════════════════════
All free, no API keys:
  • geocode(place)        — OpenStreetMap Nominatim (name → lat/lon)
  • road_distance_km(...) — OSRM driving distance, haversine×1.3 fallback
  • haversine(...)        — exact great-circle distance (verified)
  • rate database         — persists every raw-material rate the buyer enters,
                            suggests the running average next time
  • fuel reference        — editable defaults (a guaranteed-free *live* India fuel
                            feed doesn't exist; buyer can override)

Freight = road_distance_km × per_km_cost  (buyer sets per-km).
"""

import json, math, os, urllib.request, urllib.parse

# ───────────────────────────────────────────────────────────────────
# DISTANCE
# ───────────────────────────────────────────────────────────────────

def haversine(lat1, lon1, lat2, lon2):
    """Great-circle distance in km (exact)."""
    R = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return round(2 * R * math.asin(math.sqrt(a)), 2)


def geocode(place, timeout=10):
    """Resolve a place name to coordinates via OpenStreetMap Nominatim (free).
    Returns {lat, lon, display} or None."""
    if not place or not str(place).strip():
        return None
    q = urllib.parse.urlencode({"q": str(place).strip(), "format": "json", "limit": 1})
    url = f"https://nominatim.openstreetmap.org/search?{q}"
    req = urllib.request.Request(url, headers={"User-Agent": "AgenticBOM/1.0 (procurement tool)"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
        if not data:
            return None
        d = data[0]
        return {"lat": float(d["lat"]), "lon": float(d["lon"]), "display": d.get("display_name", place)}
    except Exception:
        return None


def road_distance_km(lat1, lon1, lat2, lon2, timeout=12):
    """Driving distance in km via OSRM (free). Falls back to haversine×1.3
    (typical road-circuity factor) if OSRM is unreachable.
    Returns (km, mode) where mode is 'road' or 'estimated'."""
    url = (f"https://router.project-osrm.org/route/v1/driving/"
           f"{lon1},{lat1};{lon2},{lat2}?overview=false")
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            data = json.loads(r.read())
        routes = data.get("routes") or []
        if routes:
            return round(routes[0]["distance"] / 1000.0, 2), "road"
    except Exception:
        pass
    return round(haversine(lat1, lon1, lat2, lon2) * 1.3, 2), "estimated"


def freight_cost(distance_km, per_km_cost, trips=1):
    """Freight = distance × ₹/km × trips. Deterministic."""
    try:
        return int(round(float(distance_km) * float(per_km_cost) * max(int(trips), 1)))
    except Exception:
        return 0


# Distance-slab freight: freight is a % of package VALUE, where the % itself
# scales with distance — the middle ground between pure ₹/km (ignores value)
# and a flat % (ignores distance). Buyer can override the suggested %.
FREIGHT_SLABS = [(250, 1.0), (750, 2.0), (1500, 3.0), (2500, 4.0)]
FREIGHT_PCT_MAX = 5.0


def freight_pct(distance_km):
    """Suggested freight % of package value for a given road distance.
    (Legacy %-of-value method — kept for reference / fallback.)"""
    try:
        km = float(distance_km)
    except Exception:
        return FREIGHT_SLABS[0][1]
    for limit, pct in FREIGHT_SLABS:
        if km < limit:
            return pct
    return FREIGHT_PCT_MAX


# Road-freight volumetric factor: kg charged per CBM (m³). Indian road
# convention is ~250–333 kg/m³; 250 is the common conservative value.
VOLUMETRIC_KG_PER_CBM = 250.0
FREIGHT_RATE_PER_TONNE_KM = 3.5   # ₹ per chargeable-tonne per km (buyer-overridable)


def chargeable_weight_kg(actual_kg, volume_cbm=0.0, vol_factor=VOLUMETRIC_KG_PER_CBM):
    """Freight is billed on the greater of actual weight and volumetric weight.
    volumetric_kg = volume(m³) × vol_factor."""
    try:
        actual = max(float(actual_kg or 0), 0.0)
    except Exception:
        actual = 0.0
    try:
        volumetric = max(float(volume_cbm or 0), 0.0) * float(vol_factor)
    except Exception:
        volumetric = 0.0
    return max(actual, volumetric)


def freight_cost(actual_kg, distance_km, volume_cbm=0.0,
                 rate_per_tonne_km=FREIGHT_RATE_PER_TONNE_KM,
                 vol_factor=VOLUMETRIC_KG_PER_CBM):
    """Weight-&-distance freight:
        chargeable_wt = max(actual_kg, volume_cbm × vol_factor)
        freight ₹     = (chargeable_wt / 1000) × rate_per_tonne_km × distance_km
    Returns (freight_inr:int, detail:dict)."""
    try:
        km = max(float(distance_km or 0), 0.0)
    except Exception:
        km = 0.0
    ch_kg = chargeable_weight_kg(actual_kg, volume_cbm, vol_factor)
    tonnes = ch_kg / 1000.0
    cost = int(round(tonnes * float(rate_per_tonne_km) * km))
    return cost, {
        "actual_kg": round(float(actual_kg or 0), 1),
        "volume_cbm": round(float(volume_cbm or 0), 3),
        "volumetric_kg": round(float(volume_cbm or 0) * float(vol_factor), 1),
        "chargeable_kg": round(ch_kg, 1),
        "chargeable_tonnes": round(tonnes, 3),
        "rate_per_tonne_km": float(rate_per_tonne_km),
        "distance_km": round(km, 1),
        "formula": f"{round(tonnes,3)} t × ₹{rate_per_tonne_km}/t·km × {round(km,1)} km",
    }


# ───────────────────────────────────────────────────────────────────
# RATE DATABASE (persists buyer-entered raw-material rates)
# ───────────────────────────────────────────────────────────────────
# NOTE: on Streamlit Cloud the filesystem resets on redeploy/reboot, so this is
# durable within a deployment but not forever. For permanent storage, point
# RATE_DB_PATH at a mounted volume / commit the file / use a DB or Google Sheet.

RATE_DB_PATH = os.environ.get("RATE_DB_PATH", "rate_db.json")


def load_rate_db():
    try:
        with open(RATE_DB_PATH, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_rate_db(db):
    try:
        with open(RATE_DB_PATH, "w") as f:
            json.dump(db, f, indent=1)
    except Exception:
        pass


def _norm_mat(material):
    return " ".join(str(material or "").lower().split())[:60] or "unspecified"


def record_rate(material, rate, component="", source="user"):
    """Store one entered rate against its material. Builds history for averaging."""
    rate = float(rate or 0)
    if rate <= 0:
        return
    db = load_rate_db()
    key = _norm_mat(material)
    entry = db.setdefault(key, {"rates": [], "count": 0, "last": 0, "avg": 0,
                                "samples": []})
    entry["rates"].append(rate)
    entry["rates"] = entry["rates"][-50:]          # cap history
    entry["count"] = len(entry["rates"])
    entry["last"] = rate
    entry["avg"] = round(sum(entry["rates"]) / len(entry["rates"]), 2)
    if component:
        entry["samples"] = (entry.get("samples", []) + [component])[-10:]
    _save_rate_db(db)


def suggested_rate(material):
    """Best default for a material from history (average), else 0."""
    db = load_rate_db()
    e = db.get(_norm_mat(material))
    return float(e["avg"]) if e and e.get("avg") else 0.0


def rate_db_table():
    """Flat list for display: [{material, avg, last, count}]."""
    db = load_rate_db()
    rows = [{"Material": k, "Avg ₹/kg": v.get("avg", 0), "Last ₹/kg": v.get("last", 0),
             "Samples": v.get("count", 0)} for k, v in db.items()]
    return sorted(rows, key=lambda r: -r["Samples"])


# ───────────────────────────────────────────────────────────────────
# FUEL REFERENCE (editable defaults; no guaranteed-free live India feed)
# ───────────────────────────────────────────────────────────────────

DEFAULT_FUEL = {"diesel": 90.0, "petrol": 105.0}   # ₹/litre, India approx — buyer can edit


def get_fuel_prices():
    """Returns reference fuel prices. There is no reliable free live India fuel
    API, so these are sensible editable defaults (override in the UI / via
    DIESEL_PRICE, PETROL_PRICE env vars)."""
    return {
        "diesel": float(os.environ.get("DIESEL_PRICE", DEFAULT_FUEL["diesel"])),
        "petrol": float(os.environ.get("PETROL_PRICE", DEFAULT_FUEL["petrol"])),
    }
