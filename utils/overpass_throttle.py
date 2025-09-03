import os, time, threading, concurrent.futures as cf
import overpy

# Mirrors (first env var wins; otherwise hedge across these)
DEFAULT_MIRRORS = [
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass-api.nextzen.org/api/interpreter", 
    "https://z.overpass-api.de/api/interpreter",
]

_MIN_INTERVAL_S = float(os.getenv("OVERPASS_MIN_INTERVAL_S", "1.2"))  # was 2.5; single-call faster
_MAX_RETRIES = int(os.getenv("OVERPASS_MAX_RETRIES", "4"))
_BACKOFF_START = float(os.getenv("OVERPASS_BACKOFF_START_S", "1.5"))
_HEDGE_MIRRORS = int(os.getenv("OVERPASS_HEDGE_MIRRORS", "2"))  # race the first 2 mirrors

_lock = threading.Lock()
_last_call = 0.0

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
                futs.append(ex.submit(lambda a=api: _paced_call(lambda: a.query(q))))
            for fut in cf.as_completed(futs, timeout=60):
                try:
                    return fut.result()
                except Exception:
                    pass
        time.sleep(backoff)
        backoff = min(backoff * 1.8, 12.0)

    raise overpy.exception.OverpassTooManyRequests("Overpass mirrors failed after retries")
