"""
Estimate impervious surface (pavement) percentage using NAIP imagery.

This module delegates to the shared landcover helper to read NAIP data
and compute NDVI.  Pixels with NDVI < 0.1 are considered pavement.
Results are cached for one day.
"""

from __future__ import annotations
import math
from typing import Dict, Any
from .landcover import get_canopy_and_pavement

def get_pavement_percentage(lat: float, lon: float, radius_deg: float = 0.01) -> Dict[str, Any]:
    """
    Retrieve impervious surface percentage for a location.

    Calls the shared landcover function which caches the NAIP read, then
    extracts only the pavement portion of the result.
    """
    res = get_canopy_and_pavement(lat, lon, radius_deg=radius_deg)
    if "error" in res:
        return {"error": res["error"]}
    return {
        "percentage": res["pavement"],
        "source": res["source"],
        "acquired": res["acquired"]
    }

def normalize_pavement(percentage: float) -> int:
    try:
        p = float(percentage)
    except (TypeError, ValueError):
        return 0

    p = max(0.0, min(100.0, p))

    midpoint = 30.0  
    steepness = 0.12 
    x = 1.0 / (1.0 + math.exp(steepness * (p - midpoint)))  
    return int(round(100 * x))
