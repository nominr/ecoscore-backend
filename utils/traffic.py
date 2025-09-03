from __future__ import annotations
import os
import math
from typing import Dict, Any
from utils.overpass_throttle import hedged_paced_query as paced_query
import overpy
import math


# ---- Throttle helper (fallback if not present) -------------------------------
try:
    from .overpass_throttle import paced_query  # preferred (shared across modules)
except Exception:
    import time, threading
    _LOCK = threading.Lock()
    _LAST = 0.0
    _MIN_INTERVAL_S = float(os.getenv("OVERPASS_MIN_INTERVAL_S", "2.5"))
    _MAX_RETRIES = int(os.getenv("OVERPASS_MAX_RETRIES", "6"))

    def paced_query(api: overpy.Overpass, q: str):
        """Serialize Overpass calls with a minimal delay; simple retry loop."""
        global _LAST
        backoff = 2.0
        for _ in range(_MAX_RETRIES):
            with _LOCK:
                wait = _MIN_INTERVAL_S - (time.monotonic() - _LAST)
                if wait > 0:
                    time.sleep(wait)
                try:
                    res = api.query(q)
                    _LAST = time.monotonic()
                    return res
                except Exception:
                    pass
            time.sleep(backoff)
            backoff = min(backoff * 1.8, 30.0)
        raise overpy.exception.OverpassTooManyRequests("Overpass failed after retries")

# ---- TTL cache (fallback if not present) -------------------------------------
try:
    from .cache import ttl_cache
except Exception:
    def ttl_cache(seconds: int = 3600):
        def deco(fn):
            return fn
        return deco

# ---- Overpass client ---------------------------------------------------------
OVERPASS_URL = os.getenv("OVERPASS_URL")  # e.g. https://overpass.kumi.systems/api/interpreter
if OVERPASS_URL:
    API = overpy.Overpass(url=OVERPASS_URL)
else:
    API = overpy.Overpass()

# ---- Heuristics --------------------------------------------------------------
def get_population_estimate(zip_code: str) -> int:
    # keep deterministic/cheap; adjust if you wire a real source
    return 10000

def get_radius_from_population(zip_code: str) -> int:
    pop = get_population_estimate(zip_code)
    if pop > 10000:
        return 1000  # urban
    elif pop > 5000:
        return 2000
    return 3000  # rural

ROAD_WEIGHTS = {
    "motorway": 1.0,
    "trunk": 0.9,
    "primary": 0.8,
    "secondary": 0.6,
    "tertiary": 0.5,
    "residential": 0.2,
    "service": 0.1,
}

# ---- Core calc ---------------------------------------------------------------
def _compute_total_road_length(lat: float, lon: float, radius_m: int = 1000) -> Dict[str, Any]:
    # 'out geom' returns per-way geometry points in the same response (no extra calls)
    q = f"""
    [out:json][timeout:180];
    way(around:{radius_m},{lat},{lon})["highway"];
    out geom tags;
    """
    result = paced_query(API, q)

    total_weighted_length = 0.0
    raw_lengths: Dict[str, float] = {}

    for way in result.ways:
        road_type = way.tags.get("highway", "unknown")
        geom = getattr(way, "geometry", None)
        if road_type == "unknown" or not geom or len(geom) < 2:
            continue

        # geometry can be dicts or objects with lat/lon attrs depending on overpy version
        def _latlon(pt):
            if isinstance(pt, dict):
                return float(pt["lat"]), float(pt["lon"])
            return float(getattr(pt, "lat")), float(getattr(pt, "lon"))

        length_m = 0.0
        for i in range(1, len(geom)):
            y1, x1 = _latlon(geom[i - 1])
            y2, x2 = _latlon(geom[i])
            dy, dx = (y2 - y1), (x2 - x1)
            length_m += ((dy * dy + dx * dx) ** 0.5) * 111000.0  # rough meters

        weight = ROAD_WEIGHTS.get(road_type, 0.1)
        total_weighted_length += length_m * weight
        raw_lengths[road_type] = raw_lengths.get(road_type, 0.0) + length_m

    return {"weighted_length": total_weighted_length, "raw_road_lengths": raw_lengths}

def normalize_traffic_score(weighted_length_m: float, radius_m: int) -> int:
    wl = max(0.0, float(weighted_length_m))
    r = max(1.0, float(radius_m))

    area_km2 = math.pi * (r / 1000.0) ** 2
    density_km_per_km2 = (wl / 1000.0) / area_km2

    midpoint = 15.0  
    steepness = 0.12 

    score = 100.0 / (1.0 + math.exp(+steepness * (density_km_per_km2 - midpoint)))
    return max(0, min(100, int(round(score))))

@ttl_cache(seconds=int(os.getenv("TRAFFIC_TTL_SECONDS", str(30 * 24 * 3600))))
def get_traffic_score(zip_code: str, lat: float, lon: float) -> Dict[str, Any]:
    radius = get_radius_from_population(zip_code)
    result = _compute_total_road_length(lat, lon, radius_m=radius)
    return {
        "score": normalize_traffic_score(result["weighted_length"], radius),
        "weighted_length": result["weighted_length"],
    }
