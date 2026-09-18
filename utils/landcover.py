"""
Compute canopy and pavement percentages from NAIP imagery.

This module leverages the raster helpers to crop and read a small
window of NAIP data and compute NDVI.  It then applies thresholds to
estimate canopy cover and impervious surface (pavement) percentages.
Results are cached via TTL to avoid repeated expensive raster reads
for the same location.
"""

from __future__ import annotations

import io
import requests
import rasterio
from typing import Dict, Any
from .raster import read_rgbn_window, compute_ndvi
from .cache import ttl_cache


def _read_nlcd_impervious_percent(lat: float, lon: float, radius_deg: float, max_size: int) -> float:
    """Read NLCD imperviousness percentages for the requested window."""
    bbox = f"{lon - radius_deg},{lat - radius_deg},{lon + radius_deg},{lat + radius_deg}"
    params = {
        "service": "WMS",
        "version": "1.1.1",
        "request": "GetMap",
        "layers": "mrlc_display:NLCD_2021_Impervious_L48",
        "styles": "",
        "srs": "EPSG:4326",
        "bbox": bbox,
        "width": max_size,
        "height": max_size,
        "format": "image/tiff",
    }
    response = requests.get(
        "https://www.mrlc.gov/geoserver/NLCD_Impervious/wms",
        params=params,
        timeout=30,
    )
    response.raise_for_status()
    with rasterio.MemoryFile(io.BytesIO(response.content)).open() as dataset:
        values = dataset.read(1).astype("float32")
    valid = values[(values >= 0) & (values <= 100)]
    if valid.size == 0:
        raise ValueError("NLCD returned no valid imperviousness pixels")
    return float(valid.mean())

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
    try:
        pavement_pct = _read_nlcd_impervious_percent(lat, lon, radius_deg, max_size)
        pavement_source = "NLCD 2021 Imperviousness"
    except Exception:
        # Keep the endpoint usable if MRLC is temporarily unavailable.
        pavement_pct = float((ndvi < 0.1).mean() * 100.0)
        pavement_source = "NAIP NDVI fallback"

    return {
        "canopy": round(canopy_pct, 2),
        "pavement": round(pavement_pct, 2),
        "source": f"{item.collection_id} + {pavement_source}",
        "acquired": item.datetime.isoformat()
    }