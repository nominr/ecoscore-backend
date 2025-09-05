"""
Wrapper around the U.S. EPA AirNow API with TTL caching.

This module fetches real‑time air quality observations for a given ZIP
code and computes a green‑score component based on the maximum AQI
value.  Responses are cached for one hour to reduce repeated API
calls for the same ZIP code.
"""

from __future__ import annotations

import os
import requests
from typing import Dict, Any, Optional, List
from .cache import ttl_cache
from dotenv import load_dotenv

load_dotenv()

API_KEY = None  

def _compute_score_from_aqi(aqi: float) -> int:
    """
    Convert AQI to a 0–100 green-score.
    """
    try:
        x = float(aqi)
    except (TypeError, ValueError):
        return 0

    if x <= 0:
        return 100
    if x <= 50:
        # 0..50 → 100..85
        return int(round(100 - (x/50.0) * 15))
    if x <= 100:
        # 51..100 → 85..70
        return int(round(85 - ((x-50)/50.0) * 15))
    if x <= 150:
        # 101..150 → 70..50
        return int(round(70 - ((x-100)/50.0) * 20))
    if x <= 200:
        # 151..200 → 50..30
        return int(round(50 - ((x-150)/50.0) * 20))
    if x <= 300:
        # 201..300 → 30..10
        return int(round(30 - ((x-200)/100.0) * 20))
    if x <= 500:
        # 301..500 → 10..0
        return int(round(10 - ((x-300)/200.0) * 10))
    return 0


@ttl_cache(seconds=3600)
def get_aqi_by_zip(zip_code: str, distance: int = 25) -> Dict[str, Any]:
    """
    Retrieve current AQI observations for a ZIP code using the AirNow API.
    """
    api_key = os.getenv("AIRNOW_API_KEY")
    if not api_key:
        return {"error": "AIRNOW_API_KEY environment variable is not set."}
    base_url = "https://www.airnowapi.org/aq/observation/zipCode/current/"
    params = {
        "format": "application/json",
        "zipCode": zip_code,
        "distance": distance,
        "API_KEY": api_key,
    }
    try:
        resp = requests.get(base_url, params=params, timeout=30)
    except Exception as e:
        return {"error": f"Request failed: {e}"}
    if resp.status_code != 200:
        return {"error": f"AirNow API returned {resp.status_code}: {resp.text}"}
    try:
        data = resp.json()
    except Exception as e:
        return {"error": f"Failed to parse JSON: {e}"}
    if not isinstance(data, list) or not data:
        return {"error": "No air quality data found for this ZIP code"}
    max_aqi: Optional[float] = None
    primary_pollutant: Optional[str] = None
    observations: List[Dict[str, Any]] = []
    for rec in data:
        try:
            aqi_val = float(rec.get("AQI")) if rec.get("AQI") is not None else None
        except (TypeError, ValueError):
            aqi_val = None
        pollutant = rec.get("ParameterName") or rec.get("Parameter")
        category = None
        cat_obj = rec.get("Category")
        if isinstance(cat_obj, dict):
            category = cat_obj.get("Name")
        elif isinstance(cat_obj, list) and cat_obj:
            category = cat_obj[0].get("Name")
        else:
            category = rec.get("Category.Name")
        observations.append({
            "parameter": pollutant,
            "aqi": aqi_val,
            "category": category,
            "reporting_area": rec.get("ReportingArea"),
            "state_code": rec.get("StateCode"),
            "latitude": rec.get("Latitude"),
            "longitude": rec.get("Longitude"),
            "date_observed": rec.get("DateObserved"),
            "hour_observed": rec.get("HourObserved"),
        })
        if aqi_val is not None and (max_aqi is None or aqi_val > max_aqi):
            max_aqi = aqi_val
            primary_pollutant = pollutant
    if max_aqi is None:
        return {"error": "No valid AQI values returned by AirNow API"}
    score = _compute_score_from_aqi(max_aqi)
    return {
        "score": score,
        "max_aqi": max_aqi,
        "primary_pollutant": primary_pollutant,
        "observations": observations,
    }