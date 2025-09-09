"""
FastAPI endpoints exposing the green score API.

This module defines a single `/green-score` route that aggregates data
from various utility modules to compute an overall environmental score
for a given input ZIP code.  Expensive calls are executed concurrently and
repetitive queries are rate limited.
"""

from fastapi import APIRouter, Request, HTTPException, Response
from typing import Dict, Any
from concurrent.futures import ThreadPoolExecutor
import os
import time
import math
import hashlib
import json

from utils.geocode import get_coordinates_from_zip
from utils.airnow import get_aqi_by_zip
from utils.landcover import get_canopy_and_pavement
from utils.trees import normalize_canopy
from utils.pavement import normalize_pavement
from utils.sea_level import get_sea_level_rise_score
from utils.transit import get_transit_access_score
from utils.water import get_water_score
from utils.flood_risk import get_flood_risk as get_rtfi_flood_risk
from utils.traffic import get_traffic_score
from utils.greenspace import get_green_space
from utils.toxics import get_toxic_sites
from utils.demographics import get_demographics
from utils.kv import cache_get_zip, cache_set_zip

router = APIRouter()

# Simple rate limiter per client IP
RATE_LIMIT_PER_MIN = int(os.getenv("RATE_LIMIT_PER_MIN", "30"))
_request_log: Dict[str, list] = {}


async def rate_limiter(request: Request):
    """
    Limit the number of requests from a single client per minute.

    Raise HTTPException with status 429 if the client has exceeded
    RATE_LIMIT_PER_MIN requests within the last 60 seconds.
    """
    client_ip = request.client.host if request.client else "unknown"
    now = time.time()

    calls = _request_log.get(client_ip, [])
    calls = [t for t in calls if now - t < 60]
    if len(calls) >= RATE_LIMIT_PER_MIN:
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Try again later.")
    calls.append(now)
    _request_log[client_ip] = calls
    return

METRIC_WEIGHTS = {
    "air_quality":        1.2,
    "tree_canopy":        0.8,
    "pavement":           0.8,
    "traffic":            1.0,
    "toxic_sites":        1.1,
    "green_space":        0.9,
    "sea_level_rise":     0.9,
    "transit_access":     0.6,
    "water_availability": 0.6,
    "riverine_flood_risk":0.9,
}

def _extract_score(v):
    """Return a clamped numeric score in 0..100 or None if missing/invalid."""
    if not isinstance(v, dict):
        return None
    s = v.get("score")
    if isinstance(s, (int, float)):
        return max(0.0, min(100.0, float(s)))
    return None

def _geometric_mean_over_scores(scores: dict) -> int | None:
    """
    Weighted geometric mean over available component scores (0..100).
    Missing metrics are ignored with weights renormalized.
    """

    triples = []
    for k, v in scores.items():
        s = _extract_score(v)
        w = METRIC_WEIGHTS.get(k, 0.0)
        if s is not None and w > 0:
            s = max(1.0, s)
            triples.append((k, s, w))

    if not triples:
        return None

    total_w = sum(w for _, _, w in triples)
    if total_w <= 0:
        return None

    acc = 0.0
    for _, s, w in triples:
        acc += (w / total_w) * math.log(s / 100.0)
    gm = 100.0 * math.exp(acc)
    return int(round(max(0.0, min(100.0, gm))))


def compute_green_score(zip: str) -> Dict[str, Any]:
    """
    Compute green scores for a ZIP code by aggregating multiple environmental metrics.

    This helper is called by the `/green-score` endpoint.  It converts the
    provided ZIP code into geographic coordinates, validates them and then
    orchestrates concurrent calls to both internal utility modules and
    externally defined environmental functions.  Each result is placed
    into a dictionary keyed by the metric name and includes error
    handling.  Finally, the overall score is computed as the average of
    available numeric scores (0–100 range).  If no scores are available,
    the overall score will be ``None``.
    """
    coords = get_coordinates_from_zip(zip)
    if not coords:
        return {"error": "Invalid ZIP code"}

    scores: Dict[str, Any] = {}

    # Use a thread pool to parallelize I/O bound operations.  Increase the
    # worker count to accommodate additional metrics.
    with ThreadPoolExecutor(max_workers=10) as executor:
        # Existing metrics
        fut_air = executor.submit(get_aqi_by_zip, zip)
        fut_land = executor.submit(get_canopy_and_pavement, coords[0], coords[1], 0.01)
        fut_traffic = executor.submit(get_traffic_score, zip, coords[0], coords[1])
        fut_toxic = executor.submit(get_toxic_sites, coords[0], coords[1])
        fut_green = executor.submit(get_green_space, coords[0], coords[1])
        fut_dem = executor.submit(get_demographics, zip)

        # New environmental metrics
        fut_sea_level = executor.submit(get_sea_level_rise_score, coords[0], coords[1])
        fut_transit = executor.submit(get_transit_access_score, coords[0], coords[1])
        fut_water = executor.submit(get_water_score, coords[0], coords[1])
        fut_flood_rtfi = executor.submit(get_rtfi_flood_risk, coords[0], coords[1])

        # Wait for results
        airnow_result = fut_air.result()
        land_result = fut_land.result()
        traffic_result = fut_traffic.result()
        toxic_result = fut_toxic.result()
        green_space_result = fut_green.result()
        demographics_result = fut_dem.result()

        sea_level_result = fut_sea_level.result()
        transit_result = fut_transit.result()
        water_result = fut_water.result()
        flood_rtfi_result = fut_flood_rtfi.result()

    # Air quality
    if isinstance(airnow_result, dict) and "error" not in airnow_result:
        scores["air_quality"] = {
            "score": airnow_result.get("score"),
            "max_aqi": airnow_result.get("max_aqi"),
            "primary_pollutant": airnow_result.get("primary_pollutant"),
            "observations": airnow_result.get("observations"),
            "source": "AirNow"
        }
    else:
        scores["air_quality"] = {"error": airnow_result.get("error", "Unknown error"), "source": "AirNow"}

    # Land cover (canopy & pavement)
    if isinstance(land_result, dict) and "error" not in land_result:
        scores["tree_canopy"] = {
            "score": normalize_canopy(land_result.get("canopy")),
            "percentage": land_result.get("canopy"),
            "source": land_result.get("source"),
            "acquired": land_result.get("acquired")
        }
        scores["pavement"] = {
            "score": normalize_pavement(land_result.get("pavement")),
            "percentage": land_result.get("pavement"),
            "source": land_result.get("source"),
            "acquired": land_result.get("acquired")
        }
    else:
        err_msg = land_result.get("error", "Unable to retrieve land cover data") if isinstance(land_result, dict) else "Unable to retrieve land cover data"
        scores["tree_canopy"] = {"error": err_msg}
        scores["pavement"] = {"error": err_msg}

    # Traffic
    if isinstance(traffic_result, dict) and "error" not in traffic_result:
        scores["traffic"] = {
            "score": traffic_result.get("score"),
            "weighted_road_length": round(traffic_result.get("weighted_length", 0.0), 2)
        }
    else:
        err = traffic_result.get("error", "Unable to compute traffic score") if isinstance(traffic_result, dict) else "Unable to compute traffic score"
        scores["traffic"] = {"error": err}

    # Toxic sites
    if isinstance(toxic_result, dict) and "error" not in toxic_result:
        scores["toxic_sites"] = toxic_result
    else:
        err = toxic_result.get("error", "Unable to retrieve toxic sites") if isinstance(toxic_result, dict) else "Unable to retrieve toxic sites"
        scores["toxic_sites"] = {"error": err}

    # Green space
    if isinstance(green_space_result, dict) and "error" not in green_space_result:
        scores["green_space"] = green_space_result
    else:
        err = green_space_result.get("error", "Unable to retrieve green space") if isinstance(green_space_result, dict) else "Unable to retrieve green space"
        scores["green_space"] = {"error": err}

    # Demographics
    if isinstance(demographics_result, dict) and "error" not in demographics_result:
        scores["demographics"] = demographics_result
    else:
        err = demographics_result.get("error", "Unable to retrieve demographics") if isinstance(demographics_result, dict) else "Unable to retrieve demographics"
        scores["demographics"] = {"error": err}

    # New metrics: sea level rise exposure
    if isinstance(sea_level_result, dict) and "error" not in sea_level_result:
        scores["sea_level_rise"] = sea_level_result
    else:
        err = sea_level_result.get("error", "Unable to compute sea level rise exposure") if isinstance(sea_level_result, dict) else "Unable to compute sea level rise exposure"
        scores["sea_level_rise"] = {"error": err}

    # Transit access
    if isinstance(transit_result, dict) and "error" not in transit_result:
        scores["transit_access"] = transit_result
    else:
        err = transit_result.get("error", "Unable to compute transit access") if isinstance(transit_result, dict) else "Unable to compute transit access"
        scores["transit_access"] = {"error": err}

    # Water availability
    if isinstance(water_result, dict) and "error" not in water_result:
        scores["water_availability"] = water_result
    else:
        err = water_result.get("error", "Unable to compute water availability") if isinstance(water_result, dict) else "Unable to compute water availability"
        scores["water_availability"] = {"error": err}

    # Real‑time flood risk (USGS RT‑FI)
    if isinstance(flood_rtfi_result, dict) and "error" not in flood_rtfi_result:
        scores["riverine_flood_risk"] = flood_rtfi_result
    else:
        err = flood_rtfi_result.get("error", "Unable to compute real‑time flood risk") if isinstance(flood_rtfi_result, dict) else "Unable to compute real‑time flood risk"
        scores["riverine_flood_risk"] = {"error": err}

    # Compute overall score: average of available numeric scores (0–100)
    score_values = [
        v.get("score")
        for v in scores.values()
        if isinstance(v, dict) and isinstance(v.get("score"), (int, float))
    ]
    # overall_score = round(sum(score_values) / len(score_values)) if score_values else None
    overall_score = _geometric_mean_over_scores(scores)

    return {
        "zip": zip,
        "coordinates": coords,
        "scores": scores,
        "overall_score": overall_score
    }

@router.get("/green-score")
def green_score(zip: str, request: Request):
    """
    Compute or retrieve a green score for the supplied ZIP code.

    This endpoint enforces a simple per-IP rate limit, serves cached responses
    where available, and returns 304 Not Modified when the client presents
    a matching ETag.  Cached responses are annotated with `X-Cache` headers.
    """
    # simple per-IP rate limiting (sync, uses X-Forwarded-For if present)
    client_ip = (request.headers.get("x-forwarded-for", "").split(",")[0].strip()
                 or (request.client.host if request.client else "unknown"))
    now = time.time()
    calls = _request_log.get(client_ip, [])
    calls = [t for t in calls if now - t < 60]
    if len(calls) >= RATE_LIMIT_PER_MIN:
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Try again later.")
    calls.append(now)
    _request_log[client_ip] = calls

    # Attempt to serve from cache.
    cached = cache_get_zip(zip)
    if cached:
        etag = hashlib.sha256(json.dumps(cached, sort_keys=True).encode('utf-8')).hexdigest()
        inm = request.headers.get('if-none-match')
        if inm == etag:
            # Data unchanged since last fetch.
            return Response(status_code=304, headers={
                "ETag": etag,
                "Cache-Control": "public, max-age=600",
                "X-Cache": "HIT",
            })
        return Response(
            content=json.dumps(cached),
            media_type="application/json",
            headers={
                "ETag": etag,
                "Cache-Control": "public, max-age=600",
                "X-Cache": "HIT",
            }
        )

    result = compute_green_score(zip)
    if isinstance(result, dict) and not result.get("error"):
        cache_set_zip(zip, result)
        etag = hashlib.sha256(json.dumps(result, sort_keys=True).encode('utf-8')).hexdigest()
        return Response(
            content=json.dumps(result),
            media_type="application/json",
            headers={
                "ETag": etag,
                "Cache-Control": "public, max-age=600",
                "X-Cache": "MISS",
            }
        )
    # error: return as-is (dict with error)
    return result

@router.get("/sea-level")
def sea_level(zip: str):
    """
    Assess sea level rise exposure for a ZIP code.

    Queries the NOAA Sea Level Rise viewer for 1–6 ft inundation
    scenarios around the ZIP centroid and returns a score along with
    which scenarios, if any, intersect the area.  A score of 100
    indicates no detected inundation within 5 km, while 0 indicates
    all scenarios affect the location.
    """
    coords = get_coordinates_from_zip(zip)
    if not coords:
        return {"error": "Invalid ZIP code"}
    lat, lon = coords
    result = get_sea_level_rise_score(lat, lon)
    return {"zip": zip, "coordinates": coords, **result}


@router.get("/transit-access")
def transit_access(zip: str):
    """
    Evaluate public transit accessibility for a ZIP code.

    Counts bus, rail and tram stops within a 1.5 km radius around the
    ZIP centroid via the Overpass API and converts the count into a
    0–100 score.  Five or more stops saturate the score at 100.
    """
    coords = get_coordinates_from_zip(zip)
    if not coords:
        return {"error": "Invalid ZIP code"}
    lat, lon = coords
    result = get_transit_access_score(lat, lon)
    return {"zip": zip, "coordinates": coords, **result}


@router.get("/water")
def water(zip: str):
    """
    Compute a local water availability score for a ZIP code.

    Uses OpenStreetMap data via Overpass to count natural water bodies
    and riverbank features within a 1 km radius and maps the total
    into a 0–100 score.  More water features translate into a higher
    score.
    """
    coords = get_coordinates_from_zip(zip)
    if not coords:
        return {"error": "Invalid ZIP code"}
    lat, lon = coords
    result = get_water_score(lat, lon)
    return {"zip": zip, "coordinates": coords, **result}


@router.get("/flood-risk")
def flood_risk_endpoint(zip: str):
    """
    Estimate riverine flood risk for a ZIP code based on real‑time
    flooding data.

    Retrieves currently flooding reference points from USGS and
    computes the nearest distance to the ZIP centroid.  The response
    includes a 0–100 score, where higher scores denote greater
    distance (and thus lower immediate risk).  In the absence of any
    flooding points, the score defaults to 100.
    """
    coords = get_coordinates_from_zip(zip)
    if not coords:
        return {"error": "Invalid ZIP code"}
    lat, lon = coords
    result = get_rtfi_flood_risk(lat, lon)
    return {"zip": zip, "coordinates": coords, **result}