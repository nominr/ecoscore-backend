"""
Public transit accessibility using OpenStreetMap data via Overpass API.

This module approximates how well a location is served by public
transport by counting transit stops within walking distance.  A 15‑minute
walk is roughly equivalent to a 1.5 km radius.  Using the Overpass API
through the ``hedged_paced_query`` helper, we query for OSM nodes
representing bus stops, rail stations and tram stops around the given
coordinate.  The number of stops is then scaled to a 0–100 score.

The Overpass API is open and provides a generous free tier.  Queries
are rate‑limited and retried automatically by the helper to avoid
abusing any particular mirror.  If the Overpass request fails, an
error message is returned.

References
----------
* Overpass API query language documentation for node and radius
  searches.
* ``hedged_paced_query`` helper from the project which serialises
  requests and races multiple mirrors.
"""

from __future__ import annotations

import math
from typing import Dict, Any

# Import overpy if available.  If not, we will fall back to calling
# the Overpass API directly via HTTP.  The traffic and other modules in
# this repository also depend on overpy, but we guard our use here to
# avoid crashing when the library is missing.
try:
    import overpy  # type: ignore
    _OVERPY_AVAILABLE = True
except Exception:
    overpy = None  # type: ignore
    _OVERPY_AVAILABLE = False

try:
    # hedged_paced_query is defined in overpass_throttle.py at project root
    from overpass_throttle import hedged_paced_query as paced_query  # type: ignore
except Exception:
    paced_query = None  # type: ignore


# ---- TTL cache (fallback if shared cache not available) --------------------
try:
    from .cache import ttl_cache  # type: ignore
except Exception:
    def ttl_cache(seconds: int = 3600):  # type: ignore
        def deco(fn):  # type: ignore
            return fn
        return deco


@ttl_cache(seconds=7 * 24 * 3600)  # one‑week cache
def get_transit_access_score(lat: float, lon: float, radius_m: int = 1500) -> Dict[str, Any]:
    """
    Compute a public transit accessibility score for a location.

    A 15‑minute walk is approximated by a 1.5 km radius.  The function
    queries the Overpass API for various OSM tags representing transit
    stops (bus stops, rail stations and tram stops) within this
    radius, counts the total number of stops, and converts the count
    into a 0–100 score.  Zero stops yields a score of 0, while five or
    more stops saturates at 100.

    Parameters
    ----------
    lat : float
        Latitude of the location.
    lon : float
        Longitude of the location.
    radius_m : int, optional
        Search radius in metres.  Defaults to 1 500 m.

    Returns
    -------
    dict
        Contains ``score`` (int), ``stops_count`` (int) and
        ``source`` (str).  If the Overpass query fails, returns
        ``error`` instead of a score.
    """
    # Build an Overpass query that searches for transit stop nodes.  We
    # include common tags: public_transport=platform/stop_position/stop,
    # railway=station/tram_stop/halt, and highway=bus_stop.  The
    # ``around`` filter performs a radial search in metres.
    query = f"""
    [out:json][timeout:60];
    (
      node(around:{radius_m},{lat},{lon})[public_transport~"platform|stop_position|stop"];  
      node(around:{radius_m},{lat},{lon})[railway~"station|tram_stop|halt"];  
      node(around:{radius_m},{lat},{lon})[highway=bus_stop];  
    );
    out ids;
    """
    # If overpy is available along with the paced_query helper, use it
    if _OVERPY_AVAILABLE and paced_query is not None:
        try:
            result = paced_query(query)  # type: ignore
        except Exception as exc:
            return {"error": f"Overpass query failed: {exc}"}
        # Overpy collects all returned nodes in result.nodes
        stops_count = len(getattr(result, "nodes", []))
    else:
        # Fall back to direct HTTP query to Overpass API.
        import requests
        # List of Overpass API endpoints to try sequentially
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
        # Count nodes representing stops.  Elements with type 'node'
        # correspond to the stops we requested.
        stops_count = 0
        for element in data.get("elements", []):
            if element.get("type") == "node":
                stops_count += 1

        if stops_count == 0:
            score = 0
        else:
            area_km2 = math.pi * (radius_m / 1000.0) ** 2
            density = stops_count / area_km2  # stops / km²
            alpha = 0.22
            score = int(round(100.0 * (1.0 - math.exp(-alpha * density))))

    return {
        "score": score,
        "stops_count": stops_count,
        "source": "OSM via Overpass API",
    }