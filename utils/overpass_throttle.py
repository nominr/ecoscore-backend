import os, time, threading, concurrent.futures as cf
import overpy
import requests

# Mirrors (first env var wins; otherwise hedge across these)
DEFAULT_MIRRORS = [
    "https://overpass.osm.ch/api/interpreter",
    "https://overpass-api.de/api/interpreter",
]

_MIN_INTERVAL_S = float(os.getenv("OVERPASS_MIN_INTERVAL_S", "1.2"))  # was 2.5; single-call faster
_MAX_RETRIES = int(os.getenv("OVERPASS_MAX_RETRIES", "2"))
_BACKOFF_START = float(os.getenv("OVERPASS_BACKOFF_START_S", "1.5"))
_HEDGE_MIRRORS = int(os.getenv("OVERPASS_HEDGE_MIRRORS", "2"))  # race the first 2 mirrors
_REQUEST_TIMEOUT_S = float(os.getenv("OVERPASS_REQUEST_TIMEOUT_S", "15"))
_ATTEMPT_TIMEOUT_S = float(os.getenv("OVERPASS_ATTEMPT_TIMEOUT_S", "18"))

_lock = threading.Lock()
_last_call = 0.0

_OVERPASS_HEADERS = {
    "User-Agent": os.getenv(
        "OVERPASS_USER_AGENT",
        "green-score-backend/1.0 (environmental scoring service)",
    ),
}


def _query_with_user_agent(api: overpy.Overpass, query: str) -> overpy.Result:
    response = requests.post(
        api.url,
        data=query.encode("utf-8"),
        headers=_OVERPASS_HEADERS,
        timeout=_REQUEST_TIMEOUT_S,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Overpass returned HTTP {response.status_code}: {response.text[:200]}")
    return api.parse_json(response.content)

def _paced_call(fn):
    global _last_call
    with _lock:
        wait = _MIN_INTERVAL_S - (time.monotonic() - _last_call)
        if wait > 0:
            time.sleep(wait)
        res = fn()
        _last_call = time.monotonic()
        return res

def hedged_paced_query(q: str) -> overpy.Result:
    mirrors = [os.getenv("OVERPASS_URL")] if os.getenv("OVERPASS_URL") else DEFAULT_MIRRORS
    mirrors = [m for m in mirrors if m][: _HEDGE_MIRRORS]
    backoff = _BACKOFF_START

    for attempt in range(1, _MAX_RETRIES + 1):
        with cf.ThreadPoolExecutor(max_workers=len(mirrors)) as ex:
            futs = []
            for url in mirrors:
                api = overpy.Overpass(url=url)
                futs.append(ex.submit(lambda a=api: _paced_call(lambda: _query_with_user_agent(a, q))))
            try:
                completed = cf.as_completed(futs, timeout=_ATTEMPT_TIMEOUT_S)
                for fut in completed:
                    try:
                        return fut.result()
                    except Exception:
                        pass
            except TimeoutError:
                pass
        time.sleep(backoff)
        backoff = min(backoff * 1.8, 12.0)

    raise RuntimeError("Overpass mirrors failed after retries")
