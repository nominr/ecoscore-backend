"""
Water availability index using OpenStreetMap features.

This module approximates local water availability by counting natural
water bodies (lakes, ponds, reservoirs) and riverbank features within
a modest radius of a given coordinate.  The number of distinct
water features is converted into a green‑score component on a 0–100
scale.  More water features yield a higher score, reflecting better
local water abundance and recreational opportunities.

While water quantity and quality depend on many factors, this simple
metric serves as a proxy for the presence of accessible surface
water.  The Overpass API is open and supports generous free usage.
If the query fails, an error is returned.
"""

from __future__ import annotations

import math
from typing import Dict, Any

# Try to import overpy; if missing, we'll fall back to a direct HTTP
# request to the Overpass API.  This mirrors the approach used by the
# transit module.
try:
    import overpy  # type: ignore
    _OVERPY_AVAILABLE = True
except Exception:
    overpy = None  # type: ignore
    _OVERPY_AVAILABLE = False

try:
    from overpass_throttle import hedged_paced_query as paced_query  # type: ignore
except Exception:
    paced_query = None  # type: ignore

# ---- TTL cache (fallback if no shared cache) -------------------------------
try:
    from .cache import ttl_cache  # type: ignore
except Exception:
    def ttl_cache(seconds: int = 3600):  # type: ignore
        def deco(fn):  # type: ignore
            return fn
        return deco


@ttl_cache(seconds=7 * 24 * 3600)  # one week
def get_water_score(lat: float, lon: float, radius_m: int = 1000) -> Dict[str, Any]:
    """
    Compute a water availability score for a location.

    The score is based on the count of natural water features and
    riverbanks within a 1 km radius.  At least three distinct water
    features saturate the score at 100; fewer features reduce the
    score linearly.  If no features are found, the score is 0.

    Parameters
    ----------
    lat : float
        Latitude of the location.
    lon : float
        Longitude of the location.
    radius_m : int, optional
        Search radius in metres.  Defaults to 1 000 m.

    Returns
    -------
    dict
        Contains ``score`` (int), ``water_features`` (int) and
        ``source`` (str).  If the Overpass query fails, returns
        ``error`` instead of a score.
    """
    # Build an Overpass query that searches for natural water bodies and
    # riverbank features.  We query for ways (polygons/lines) because
    # water areas are usually represented as ways or relations.  We
    # also include multipolygon relations with natural=water tags.
    query = f"""
    [out:json][timeout:60];
    (
      way(around:{radius_m},{lat},{lon})[natural=water];
      relation(around:{radius_m},{lat},{lon})[type=multipolygon][natural=water];
      way(around:{radius_m},{lat},{lon})[waterway=riverbank];
    );
    out ids;
    """
    # If overpy is available along with the paced_query helper, use it
    if _OVERPY_AVAILABLE and paced_query is not None:
        try:
            result = paced_query(query)  # type: ignore
        except Exception as exc:
            return {"error": f"Overpass query failed: {exc}"}
        # Count unique IDs across ways and relations.  Overpy stores ways and
        # relations separately.  We ignore nodes because nodes cannot
        # represent water areas alone.
        water_count = 0
        water_count += len(getattr(result, "ways", []))
        water_count += len(getattr(result, "relations", []))
    else:
        # Fall back to direct HTTP query to Overpass API.
        import requests
        overpass_urls = [
            "https://overpass.kumi.systems/api/interpreter",
            "https://z.overpass-api.de/api/interpreter",
            "https://overpass-api.de/api/interpreter",
        ]
        response = None
        for url in overpass_urls:
            try:
                response = requests.post(url, data={"data": query}, timeout=60)
                if response.status_code == 200:
                    break
            except Exception:
                response = None
        if response is None or response.status_code != 200:
            return {"error": "Overpass query failed or no suitable endpoint responded"}
        try:
            data = response.json()
        except Exception as exc:
            return {"error": f"Failed to parse Overpass response: {exc}"}
        # Count ways and relations representing water features.  We
        # intentionally skip nodes since water bodies are not single
        # points.
        water_count = 0
        for element in data.get("elements", []):
            t = element.get("type")
            if t == "way" or t == "relation":
                water_count += 1

    # If no features, clearly 0.
    if water_count == 0:
        score = 0
    else:
        area_km2 = math.pi * (radius_m / 1000.0) ** 2
        density = water_count / area_km2  # features / km²
        alpha = 0.45
        score = int(round(100.0 * (1.0 - math.exp(-alpha * density))))

    return {
        "score": score,
        "water_features": water_count,
        "source": "OSM via Overpass API",
    }