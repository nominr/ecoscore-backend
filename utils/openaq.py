import os
import requests
from dotenv import load_dotenv
from math import radians, sin, cos, sqrt, atan2
import math

load_dotenv()

API_KEY = os.getenv("OPENAQ_API_KEY")
if not API_KEY:
    raise ValueError("OPENAQ_API_KEY environment variable is not set")

HEADERS = {"x-api-key": API_KEY}


def haversine_km(lat1, lon1, lat2, lon2):
    """
    Calculate the great-circle distance between two lat/lon points.
    """
    R = 6371  # Earth radius in kilometers
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return R * c


def get_measurements_by_coords(lat, lon, radius=10000, parameter="pm25"):
    """
    Compute the weihgted average and details of all contributing stations.
    """

    # Step 1: find nearby monitoring sites
    loc_resp = requests.get(
        "https://api.openaq.org/v3/locations",
        params={
            "coordinates": f"{lat},{lon}",
            "radius": radius,
            "limit": 20,
            "sort": "distance"
        },
        headers=HEADERS
    )
    if loc_resp.status_code != 200:
        return {"error": f"Location lookup failed: {loc_resp.text}"}

    locations = loc_resp.json().get("results", [])
    if not locations:
        return {"error": "No nearby locations found"}

    # Step 2: collect all PM2.5 sensor IDs at each site
    sensors_info = []
    for loc in locations:
        loc_coords = loc.get("coordinates")
        if not loc_coords:
            continue
        loc_lat = loc_coords["latitude"]
        loc_lon = loc_coords["longitude"]
        distance_km = haversine_km(lat, lon, loc_lat, loc_lon)

        for s in loc.get("sensors", []):
            if s["parameter"]["name"] == parameter:
                sensors_info.append({
                    "location_name": loc["name"],
                    "location_id": loc["id"],
                    "sensor_id": s["id"],
                    "distance_km": distance_km,
                    "loc_lat": loc_lat,
                    "loc_lon": loc_lon
                })

    if not sensors_info:
        return {"error": "No PM2.5 sensors found within radius"}

    # Step 3: fetch latest measurement for each sensor
    measurements = []
    for info in sensors_info:
        resp = requests.get(
            f"https://api.openaq.org/v3/sensors/{info['sensor_id']}",
            headers=HEADERS
        )
        if resp.status_code == 404 or resp.status_code != 200:
            continue

        result = resp.json().get("results", [])
        if not result:
            continue

        sensor_detail = result[0]
        latest = sensor_detail.get("latest")
        if latest and latest.get("value") is not None:
            val = latest["value"]
            if val < 0 or val > 500:
                # Filter out absurd outliers
                continue

            measurements.append({
                "station": info["location_name"],
                "station_id": info["location_id"],
                "sensor_id": info["sensor_id"],
                "value": latest["value"],
                "unit": sensor_detail["parameter"]["units"],
                "timestamp": latest["datetime"]["utc"],
                "distance_km": round(info["distance_km"], 2),
                "station_coordinates": {
                    "latitude": info["loc_lat"],
                    "longitude": info["loc_lon"]
                }
            })

    if not measurements:
        return {"error": "No valid PM2.5 measurements found from any nearby sensor"}

    # Step 4: Compute weighted average (inverse-distance weighting)
    numer = 0
    denom = 0
    for m in measurements:
        # Avoid division by zero: add 0.1 km
        weight = 1 / (m["distance_km"] + 0.1)
        numer += m["value"] * weight
        denom += weight

    avg_pm25 = numer / denom

    return {
        "pm25": round(avg_pm25, 2),
        "unit": measurements[0]["unit"],
        "stations_used": measurements,
        "num_stations": len(measurements)
    }

def normalize_pm25(pm25, scale=25):
    """
    Nonlinear scaling using square root:
    - 0 µg/m³ -> 100
    - 'scale' µg/m³ -> 0
    - curve in between
    """
    capped = min(max(pm25, 0), scale)
    ratio = capped / scale
    nonlinear = math.sqrt(ratio)  # 0 to 1, but faster rise
    return round(100 - nonlinear * 100)
