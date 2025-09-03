from __future__ import annotations
from math import radians, sin, cos, sqrt, atan2, pi, exp
from typing import Dict, Any, Iterable, Tuple
import os

import overpy

# Throttle helper (normalize to (api, query) signature)
try:
    # If a 1-arg hedged helper exists, wrap it to look like (api, query)
    from utils.overpass_throttle import hedged_paced_query as _hedged_paced_query  # type: ignore

    def paced_query(api: overpy.Overpass, q: str):
        return _hedged_paced_query(q)

except Exception:
    try:
        # If a 2-arg shared helper exists, use it directly
        from .overpass_throttle import paced_query  # type: ignore
    except Exception:
        # Fallback: simple serialized query with retries
        import time
        import threading
        import random

        _LOCK = threading.Lock()
        _LAST = 0.0
        _MIN_INTERVAL_S = float(os.getenv("OVERPASS_MIN_INTERVAL_S", "2.5"))
        _MAX_RETRIES = int(os.getenv("OVERPASS_MAX_RETRIES", "6"))
        _BASE_BACKOFF = float(os.getenv("OVERPASS_BASE_BACKOFF_S", "1.5"))
        _MAX_BACKOFF = float(os.getenv("OVERPASS_MAX_BACKOFF_S", "30.0"))

        def paced_query(api: overpy.Overpass, q: str):
            """Simple paced query with coarse global lock, retry + jitter."""
            global _LAST
            backoff = _BASE_BACKOFF
            last_exc: Exception | None = None

            for _ in range(_MAX_RETRIES):
                with _LOCK:
                    now = time.monotonic()
                    wait = _MIN_INTERVAL_S - (now - _LAST)
                    if wait > 0:
                        time.sleep(wait + random.uniform(0, 0.5))
                    try:
                        res = api.query(q)
                        _LAST = time.monotonic()
                        return res
                    except Exception as e:
                        last_exc = e
                time.sleep(backoff + random.uniform(0, 0.5))
                backoff = min(backoff * 1.8, _MAX_BACKOFF)

            if last_exc:
                raise last_exc
            raise overpy.exception.OverpassTooManyRequests("Overpass failed after retries")

# TTL cache (fallback if not present)
try:
    from utils.cache import ttl_cache  # type: ignore
except Exception:
    def ttl_cache(seconds: int = 3600):
        def deco(fn):
            return fn
        return deco

# Overpass client
OVERPASS_URL = os.getenv("OVERPASS_URL")
API = overpy.Overpass(url=OVERPASS_URL) if OVERPASS_URL else overpy.Overpass()

# Configurable tag set
# Base tags considered as "green space". Adjust to your product requirements.
GREEN_TAGS = [
    '["leisure"="park"]',
    '["leisure"="garden"]',
    '["leisure"="common"]',
    '["leisure"="recreation_ground"]',
    '["leisure"="nature_reserve"]',
    '["boundary"="protected_area"]',
]
# Exclude private-access features by default (can be disabled via env var)
EXCLUDE_PRIVATE = os.getenv("GREENSPACE_EXCLUDE_PRIVATE", "1") not in ("0", "false", "False")
TAG_FILTER = "".join(GREEN_TAGS) + ('["access"!="private"]' if EXCLUDE_PRIVATE else "")

def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in meters."""
    R = 6371000.0
    dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dlat/2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2) ** 2
    return 2 * R * atan2(sqrt(a), sqrt(1 - a))


def _smooth_distance_score(distance_m: float | None, alpha_m: float) -> int:
    """
    0..100 score, decays smoothly with distance (higher is better).
    alpha_m controls the decay length (≈ distance where score ~= 37).
    """
    if distance_m is None:
        return 0
    v = 100.0 * exp(-max(0.0, distance_m) / max(1.0, alpha_m))
    return max(0, min(100, round(v)))


def _density_score(count: int, radius_m: int, k: float) -> int:
    """
    0..100 density subscore with diminishing returns.
    k scales how quickly density contributes.
    """
    if radius_m <= 0:
        return 0
    area_km2 = pi * (radius_m / 1000.0) ** 2
    dens = count / area_km2  # parks per km^2
    v = 100.0 * (1.0 - 1.0 / (1.0 + k * dens))
    return max(0, min(100, round(v)))


def _iter_osm_centroids(result: overpy.OverpassResult) -> Iterable[Tuple[str, int, float, float]]:
    """
    Yield unique (kind, osm_id, lat, lon) for nodes/ways/relations.
    De-duplicate by stable (kind, id) key.
    """
    seen: set[Tuple[str, int]] = set()

    # Nodes: use lat/lon
    for n in getattr(result, "nodes", []):
        try:
            k = ("n", int(n.id))
            if k in seen:
                continue
            seen.add(k)
            yield ("n", int(n.id), float(n.lat), float(n.lon))
        except Exception:
            continue

    # Ways: use center_* from 'out center'
    for w in getattr(result, "ways", []):
        try:
            lat2 = getattr(w, "center_lat", None)
            lon2 = getattr(w, "center_lon", None)
            if lat2 is None or lon2 is None:
                continue
            k = ("w", int(w.id))
            if k in seen:
                continue
            seen.add(k)
            yield ("w", int(w.id), float(lat2), float(lon2))
        except Exception:
            continue

    # Relations: use center_* from 'out center'
    for r in getattr(result, "relations", []):
        try:
            lat2 = getattr(r, "center_lat", None)
            lon2 = getattr(r, "center_lon", None)
            if lat2 is None or lon2 is None:
                continue
            k = ("r", int(r.id))
            if k in seen:
                continue
            seen.add(k)
            yield ("r", int(r.id), float(lat2), float(lon2))
        except Exception:
            continue


# Core
@ttl_cache(seconds=int(os.getenv("GREENSPACE_TTL_SECONDS", str(30 * 24 * 3600))))  # default 30 days
def get_green_space(
    lat: float,
    lon: float,
    radius_m: int = 5000,
    *,  # keyword-only tuning knobs
    alpha_m: float | None = None,           # distance decay length
    density_k: float | None = None,         # density contribution steepness
    blend_distance: float | None = None,    # weight for distance (0..1); density gets (1-w)
) -> Dict[str, Any]:
    """
    Compute a greenspace score near (lat, lon).

    Returns:
      {
        "score": int 0..100,
        "components": {"distance": int, "density": int},
        "nearest_distance_m": float | None,
        "num_parks": int,
        "query_radius_m": int,
        "tags": list[str],
      }
    """
    # Basic input validation
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        raise ValueError("lat/lon out of bounds")
    if radius_m <= 0:
        raise ValueError("radius_m must be positive")

    # Resolve tuning knobs (can be set via env or kwargs)
    alpha_m = float(alpha_m if alpha_m is not None else os.getenv("GREENSPACE_ALPHA_M", "600"))
    density_k = float(density_k if density_k is not None else os.getenv("GREENSPACE_DENSITY_K", "0.8"))
    w = blend_distance if blend_distance is not None else float(os.getenv("GREENSPACE_BLEND_DISTANCE", "0.7"))
    w = max(0.0, min(1.0, w))

    q = f"""
    [out:json][timeout:{int(os.getenv("OVERPASS_TIMEOUT_S", "120"))}];
    (
      node{TAG_FILTER}(around:{radius_m},{lat},{lon});
      way{TAG_FILTER}(around:{radius_m},{lat},{lon});
      relation{TAG_FILTER}(around:{radius_m},{lat},{lon});
    );
    out center tags;
    """

    result = paced_query(API, q)

    nearest: float | None = None
    count = 0

    for _kind, _oid, cx, cy in _iter_osm_centroids(result):
        d = _haversine_m(lat, lon, cx, cy)
        if nearest is None or d < nearest:
            nearest = d
        count += 1

    # Component scores
    distance_score = _smooth_distance_score(nearest, alpha_m=alpha_m)
    density_score = _density_score(count, radius_m=radius_m, k=density_k)
    score = round(w * distance_score + (1.0 - w) * density_score)

    return {
        "score": score,
        # "components": {"distance": distance_score, "density": density_score},
        "nearest_distance_m": round(nearest, 1) if nearest is not None else None,
        "num_parks": count,
        # "query_radius_m": radius_m,
        # "tags": [t.replace('"', "") for t in GREEN_TAGS],
        # "exclude_private": EXCLUDE_PRIVATE,
    }
