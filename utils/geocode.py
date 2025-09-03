"""
Geocode ZIP codes to latitude and longitude coordinates using OpenStreetMap Nominatim.

Requests to the Nominatim API are cached for one day to avoid repeated
lookups.  Nominatim usage policies discourage heavy usage, so please
respect the rate limits and cache results appropriately.
"""

from __future__ import annotations

import requests
from typing import Optional, Tuple
from .cache import ttl_cache

@ttl_cache(seconds=86400)
def get_coordinates_from_zip(zip_code: str) -> Optional[Tuple[float, float]]:
    """
    Convert a five‑digit ZIP code into (latitude, longitude) coordinates.
    """
    url = "https://nominatim.openstreetmap.org/search"
    params = {
        "postalcode": zip_code,
        "country": "USA",
        "format": "json"
    }
    headers = {"User-Agent": "green-score-app"}
    try:
        res = requests.get(url, params=params, headers=headers, timeout=30)
    except Exception:
        return None
    try:
        data = res.json()
    except Exception:
        return None
    if data:
        try:
            lat = float(data[0]["lat"])
            lon = float(data[0]["lon"])
            return lat, lon
        except (KeyError, ValueError, TypeError):
            return None
    return None