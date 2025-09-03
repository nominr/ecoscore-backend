"""
Estimate tree canopy cover percentage using NAIP imagery.

This module uses the raster helpers to crop a small window around a
point and compute NDVI.  Pixels with NDVI > 0.4 are considered
canopy.  Results are cached for one day.
"""

from __future__ import annotations

from typing import Dict, Any
from .landcover import get_canopy_and_pavement

def get_canopy_percentage(lat: float, lon: float, radius_deg: float = 0.01) -> Dict[str, Any]:
    """
    Retrieve the canopy cover percentage for a location.

    Calls the shared landcover function which caches the NAIP read, then
    extracts only the canopy portion of the result.
    """
    res = get_canopy_and_pavement(lat, lon, radius_deg=radius_deg)
    if "error" in res:
        return {"error": res["error"]}
    return {
        "percentage": res["canopy"],
        "source": res["source"],
        "acquired": res["acquired"]
    }

def normalize_canopy(percentage: float) -> int:
    try:
        p = float(percentage)
    except (TypeError, ValueError):
        return 0

    p = max(0.0, min(100.0, p))
    k = 20.0  
    n = 1.2   

    if p == 0.0:
        return 0
    score = 100.0 * (p**n) / (p**n + k**n)
    return int(round(score))
