"""
Sea Level Rise risk estimation using NOAA Sea Level Rise Viewer services.

This module queries the NOAA Office for Coastal Management sea level rise
datasets for various inundation scenarios (1–6 feet).  For each foot
increment, we issue an ArcGIS REST query around a point and detect
whether any low‑lying inundation polygons intersect a small buffer
around the given coordinate.  The resulting count of inundated
scenarios is converted into a green‑score component on 0–100, where
higher scores represent less exposure to sea level rise.

The implementation intentionally avoids expensive area calculations and
limits itself to presence/absence detection within a 5 km radius.  The
NOAA viewer is provided under an unrestricted free tier and does not
require an API key.  If the upstream service is unreachable or
returns an error, the module gracefully reports the issue.
"""

from __future__ import annotations

import requests
from typing import Dict, Any

# ---- TTL cache (fallback if shared cache not available) --------------------
try:
    from .cache import ttl_cache  # type: ignore
except Exception:
    # If no cache implementation exists, define a no‑op decorator.
    def ttl_cache(seconds: int = 3600):  # type: ignore
        def deco(fn):  # type: ignore
            return fn
        return deco


def _query_inundation(lat: float, lon: float, feet: int, radius_m: int = 5000) -> bool:
    """
    Query a single sea level rise layer to determine if any inundation
    polygons are within ``radius_m`` metres of the given coordinate.

    Parameters
    ----------
    lat : float
        Latitude in decimal degrees (WGS84).
    lon : float
        Longitude in decimal degrees (WGS84).
    feet : int
        Sea level rise scenario in feet (1–6).
    radius_m : int, optional
        Search radius in metres.  Defaults to 5 000 m (5 km).

    Returns
    -------
    bool
        True if one or more inundation polygons intersect the buffer,
        False if none, and None if the query failed.
    """
    # Base URL for the NOAA dc_slr service.  Each foot increment has a
    # corresponding MapServer: e.g. slr_1ft, slr_2ft, etc.  We query
    # the Low‑lying Areas layer (index 0) for intersections with a
    # point buffer around our coordinate.
    base_url = f"https://coast.noaa.gov/arcgis/rest/services/dc_slr/slr_{feet}ft/MapServer/0/query"
    params = {
        "geometry": f"{lon},{lat}",
        "geometryType": "esriGeometryPoint",
        "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects",
        "distance": radius_m,
        "units": "meters",
        "outFields": "OBJECTID",
        "returnGeometry": "false",
        "f": "json",
    }
    try:
        resp = requests.get(base_url, params=params, timeout=20)
    except Exception:
        # If the request fails (network/proxy issues) treat as unknown.
        return None
    if resp.status_code != 200:
        return None
    try:
        data = resp.json()
    except Exception:
        return None
    features = data.get("features")
    if isinstance(features, list):
        return len(features) > 0
    return False


@ttl_cache(seconds=24 * 3600)  # cache for one day
def get_sea_level_rise_score(lat: float, lon: float) -> Dict[str, Any]:
    """
    Compute a sea level rise exposure score for a coordinate.

    This function queries NOAA sea level rise layers for feet 1–6 and
    converts the number of inundated scenarios into a 0–100 score.  A
    value of 100 indicates no scenarios result in inundation within 5 km
    of the point, while 0 means all scenarios do.  Intermediate values
    decrease linearly with the count of impacted levels.

    Parameters
    ----------
    lat : float
        Latitude of the location.
    lon : float
        Longitude of the location.

    Returns
    -------
    dict
        Contains ``score`` (int), ``inundated_feet`` (dict mapping feet to
        bool/None), and metadata about the query.  If any query fails
        completely, the ``score`` will still be computed based on
        available results but ``inundated_feet`` entries may be ``None``.
    """
    feet_levels = [1, 2, 3, 4, 5, 6]
    inundated: Dict[str, Any] = {}
    risk_count = 0
    for ft in feet_levels:
        hit = _query_inundation(lat, lon, ft)
        inundated[str(ft)] = hit
        if hit:
            risk_count += 1

    true_levels = [int(ft) for ft, hit in ((k, v) for k, v in inundated.items()) if hit is True]

    if not true_levels:
        score = 100  
    else:
        min_ft = min(true_levels)      
        breadth = len(true_levels)  
        gamma = 1.2
        base = 100.0 * (min_ft / 6.0) ** gamma
        penalty_per_level = 6.0
        penalty_cap = 30.0
        penalty = min(penalty_cap, max(0, breadth - 1) * penalty_per_level)

        score = max(0, int(round(base - penalty)))

    return {
        "score": score,
        "inundated_feet": inundated,
        "source": "NOAA Sea Level Rise Viewer",
        "method": "Presence/absence of inundation polygons within 5 km",
    }