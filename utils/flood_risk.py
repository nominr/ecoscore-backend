"""
Riverine flood risk estimation using USGS Real‑Time Flood Impact API.

This module estimates flood risk based on the proximity to currently
flooding reference points reported by the USGS Real‑Time Flood Impact
API.  It does not incorporate FEMA flood zone polygons, which require
more complex geospatial analysis.  The API provides a list of flood
impact locations (e.g., embankments, bridges, roads) that are
currently experiencing flooding.  We compute the great‑circle
distance between the query location and each flooding reference
point, then map the minimum distance onto a 0–100 score.  A
location adjacent to an active flooding site yields a low score,
while distances beyond 200 km saturate the score at 100.

If the API request fails or returns no flooding sites, the module
returns a score of 100 and indicates the error in the response.
"""

from __future__ import annotations

import math
from typing import Dict, Any, List, Optional

import requests

# ---- TTL cache (fallback if shared cache not available) --------------------
try:
    from .cache import ttl_cache  # type: ignore
except Exception:
    def ttl_cache(seconds: int = 3600):  # type: ignore
        def deco(fn):  # type: ignore
            return fn
        return deco

def _score_from_distance_km(d_km: float, *, midpoint_km: float = 50.0, steepness: float = 0.08) -> int:
    if d_km <= 0:
        return 0
    # logistic in [0,1], then scale to [0,100]
    x = 1.0 / (1.0 + math.exp(-steepness * (d_km - midpoint_km)))
    return int(round(100 * x))


def _haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Compute great‑circle distance between two points on Earth.
    """
    R = 6371.0  # Earth radius in kilometres
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


@ttl_cache(seconds=1 * 3600)  # cache for 1 hour; flood conditions change quickly
def get_flood_risk(lat: float, lon: float) -> Dict[str, Any]:
    """
    Estimate flood risk based on proximity to current flooding points.
    """
    url = "https://api.waterdata.usgs.gov/rtfi-api/referencepoints/flooding"
    try:
        resp = requests.get(url, timeout=20)
    except Exception as exc:
        # treat as no flooding; score high
        return {
            "score": 100,
            "nearest_flood_distance_km": None,
            "error": f"Failed to fetch flood data: {exc}",
            "source": "USGS Real-Time Flood Impact API",
        }
    if resp.status_code != 200:
        return {
            "score": 100,
            "nearest_flood_distance_km": None,
            "error": f"Flood API returned {resp.status_code}: {resp.text}",
            "source": "USGS Real-Time Flood Impact API",
        }
    try:
        data = resp.json()
    except Exception as exc:
        return {
            "score": 100,
            "nearest_flood_distance_km": None,
            "error": f"Failed to parse flood data: {exc}",
            "source": "USGS Real-Time Flood Impact API",
        }
    
    # Expect a list of reference points with latitude and longitude fields
    points: List[Dict[str, Any]] = []
    if isinstance(data, list):
        points = data
    elif isinstance(data, dict) and "referencePoints" in data:
        points = data.get("referencePoints", [])  # alternative field name

    # If no flooding points are present, return a high score
    if not points:
        return {
            "score": 100,
            "nearest_flood_distance_km": None,
            "source": "USGS Real-Time Flood Impact API",
        }
    
    # Compute the minimum distance to any flooding reference point
    min_distance: Optional[float] = None
    for pt in points:
        try:
            pt_lat = float(pt.get("latitude"))
            pt_lon = float(pt.get("longitude"))
        except Exception:
            continue
        d = _haversine_distance(lat, lon, pt_lat, pt_lon)
        if (min_distance is None) or (d < min_distance):
            min_distance = d
    if min_distance is None:
        # no valid coordinates in data
        return {
            "score": 100,
            "nearest_flood_distance_km": None,
            "source": "USGS Real-Time Flood Impact API",
        }
    
    return {
        "score": _score_from_distance_km(min_distance),
        "nearest_flood_distance_km": round(min_distance, 2),
        "source": "USGS Real-Time Flood Impact API",
    }