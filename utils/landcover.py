"""
Compute canopy and pavement percentages from NAIP imagery.

This module leverages the raster helpers to crop and read a small
window of NAIP data and compute NDVI.  It then applies thresholds to
estimate canopy cover and impervious surface (pavement) percentages.
Results are cached via TTL to avoid repeated expensive raster reads
for the same location.
"""

from __future__ import annotations

from typing import Dict, Any
from .raster import read_rgbn_window, compute_ndvi
from .cache import ttl_cache

@ttl_cache(seconds=3600 * 24)
def get_canopy_and_pavement(lat: float, lon: float, radius_deg: float = 0.01, max_size: int = 512) -> Dict[str, Any]:
    """
    Compute both canopy and pavement coverage percentages for a geographic point.

    A single NAIP scene is read and NDVI is computed.  Pixels with NDVI
    > 0.4 are classified as canopy; pixels with NDVI < 0.1 are classified
    as pavement.  Percentages are returned along with metadata (source
    collection and acquisition date).

    Parameters
    ----------
    lat : float
        Center latitude of the area of interest.
    lon : float
        Center longitude of the area of interest.
    radius_deg : float, optional
        Half‑width of the bounding box in degrees.  Defaults to 0.01 (~1 km).
    max_size : int, optional
        Maximum output dimension to downsample the window.  Defaults to 512.

    Returns
    -------
    dict
        A dictionary with keys ``canopy`` (float), ``pavement`` (float),
        ``source`` (str) and ``acquired`` (ISO date).  If imagery is not
        available, returns ``{"error": "..."}``.
    """
    arr, item, err = read_rgbn_window(lat, lon, radius_deg=radius_deg, max_size=max_size)
    if err:
        return {"error": err["error"]}

    ndvi = compute_ndvi(arr)
    canopy_pct = float((ndvi > 0.4).mean() * 100.0)
    pavement_pct = float((ndvi < 0.1).mean() * 100.0)

    return {
        "canopy": round(canopy_pct, 2),
        "pavement": round(pavement_pct, 2),
        "source": item.collection_id,
        "acquired": item.datetime.isoformat()
    }