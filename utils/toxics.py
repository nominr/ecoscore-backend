"""
Locate nearby Superfund (toxic) sites using the EPA FRS API.

This wrapper queries the EPA Facility Registry Service for Superfund
sites within a given search radius.  Results include the number of
sites, the distance to the nearest site and a normalized score.
Responses are cached for one day.
"""

from __future__ import annotations
import math
from math import radians, sin, cos, sqrt, atan2
import requests
from typing import Dict, Any, Optional
from .cache import ttl_cache

def _haversine_distance_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 3958.8  # Earth radius in miles
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * R * atan2(sqrt(a), sqrt(1 - a))

def normalize_toxic_sites(num_sites: int, nearest_distance: float) -> int:
    n = max(0, int(num_sites))
    d = 0.0 if nearest_distance is None else max(0.0, float(nearest_distance))
    midpoint = 2.5  
    steepness = 1.2 
    dist_score = 100.0 / (1.0 + math.exp(-steepness * (d - midpoint)))

    lam = 0.55
    count_score = 100.0 * math.exp(-lam * n)

    w_dist = 0.65
    w_count = 1.0 - w_dist
    score = w_dist * dist_score + w_count * count_score

    return max(0, min(100, int(round(score))))


@ttl_cache(seconds=86400)
def get_toxic_sites(lat: float, lon: float, radius_miles: float = 5.0) -> Dict[str, Any]:
    """
    Query the EPA FRS API for Superfund sites near a location.

    Parameters
    ----------
    lat : float
        Latitude.
    lon : float
        Longitude.
    radius_miles : float, optional
        Search radius in miles.  Defaults to 5.0.

    Returns
    -------
    dict
        Contains ``score``, ``num_sites`` and ``nearest_distance_miles`` or an ``error``.
    """
    url = "https://ofmpub.epa.gov/frs_public2/frs_rest_services.get_facilities"
    params = {
        "latitude83": lat,
        "longitude83": lon,
        "search_radius": radius_miles,
        "pgm_sys_acrnm": "SEMS",  # only Superfund
        "output": "JSON",
        "program_output": "N"
    }
    try:
        resp = requests.get(url, params=params, timeout=60)
    except Exception as e:
        return {"error": f"EPA FRS request failed: {e}"}
    if resp.status_code != 200:
        return {"error": f"EPA FRS API returned {resp.status_code}"}
    try:
        data = resp.json().get("Results", {}).get("FRSFacility", [])
    except Exception as e:
        return {"error": f"Failed to parse EPA FRS response: {e}"}
    if not data:
        return {"score": 100, "num_sites": 0, "nearest_distance_miles": None}
    count = 0
    nearest: Optional[float] = None
    for fac in data:
        lat2, lon2 = fac.get("Latitude83"), fac.get("Longitude83")
        if lat2 is None or lon2 is None:
            continue
        try:
            dist = _haversine_distance_miles(lat, lon, float(lat2), float(lon2))
        except Exception:
            continue
        count += 1
        if nearest is None or dist < nearest:
            nearest = dist
    if nearest is None:
        nearest = radius_miles
    return {
        "score": normalize_toxic_sites(count, nearest),
        "num_sites": count,
        "nearest_distance_miles": round(nearest, 2)
    }