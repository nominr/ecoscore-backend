"""
Fetch demographic statistics for a ZIP Code Tabulation Area (ZCTA) using the U.S. Census API.

This module wraps the Census 5‑year American Community Survey (ACS)
profile tables and returns a set of common socioeconomic indicators.
Results are cached for 30 days as the ACS data does not change
frequently.
"""

from __future__ import annotations

import os
import requests
from typing import Dict, Any
from .cache import ttl_cache

CENSUS_API_KEY = os.getenv("CENSUS_API_KEY")

@ttl_cache(seconds=86400 * 30)
def get_demographics(zip_code: str) -> Dict[str, Any]:
    """
    Fetch demographic indicators for a ZIP Code Tabulation Area.
    """
    variables = [
        "DP05_0001E",  # total population
        "DP05_0002PE",  # percent male
        "DP05_0003PE",  # percent female
        "DP05_0018E",  # median age
        "DP05_0037PE",  # percent White
        "DP05_0038PE",  # percent Black
        "DP05_0073PE",  # percent Hispanic
        "DP03_0062E",   # median household income
        "DP03_0128PE"   # poverty rate
    ]
    base_url = "https://api.census.gov/data/2022/acs/acs5/profile"
    params = {
        "get": ",".join(variables),
        "for": f"zip code tabulation area:{zip_code}"
    }
    if CENSUS_API_KEY:
        params["key"] = CENSUS_API_KEY
    try:
        resp = requests.get(base_url, params=params, timeout=30)
    except Exception as e:
        return {"error": f"Request failed: {e}"}
    if resp.status_code != 200:
        return {"error": f"Census API returned {resp.status_code}: {resp.text}"}
    try:
        data = resp.json()
    except Exception as e:
        return {"error": f"Failed to parse JSON: {e}"}
    if not data or len(data) < 2:
        return {"error": "No demographic data found for this ZIP"}
    header, values = data[0], data[1]
    try:
        total_population = int(float(values[0])) if values[0] not in (None, '') else None
        percent_male = float(values[1]) if values[1] not in (None, '') else None
        percent_female = float(values[2]) if values[2] not in (None, '') else None
        median_age = float(values[3]) if values[3] not in (None, '') else None
        percent_white = float(values[4]) if values[4] not in (None, '') else None
        percent_black = float(values[5]) if values[5] not in (None, '') else None
        percent_hispanic = float(values[6]) if values[6] not in (None, '') else None
        median_income = float(values[7]) if values[7] not in (None, '') else None
        poverty_rate = float(values[8]) if values[8] not in (None, '') else None
    except (IndexError, ValueError) as e:
        return {"error": f"Missing or invalid data returned from Census API: {e}"}
    return {
        "total_population": total_population,
        "percent_male": percent_male,
        "percent_female": percent_female,
        "median_age": median_age,
        "percent_white": percent_white,
        "percent_black": percent_black,
        "percent_hispanic": percent_hispanic,
        "median_income": median_income,
        "poverty_rate": poverty_rate
    }