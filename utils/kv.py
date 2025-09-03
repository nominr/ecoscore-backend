import os, json, redis
from typing import Optional, Dict, Any

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
ZIP_CACHE_TTL_SECONDS = int(os.getenv("ZIP_CACHE_TTL_SECONDS", str(30*24*3600)))  # 30 days
ZIP_CACHE_PREFIX = os.getenv("ZIP_CACHE_PREFIX", "greenscore:")

r = redis.Redis.from_url(REDIS_URL, decode_responses=True)

def _key(zip_code: str) -> str:
    return f"{ZIP_CACHE_PREFIX}{zip_code}"

def cache_get_zip(zip_code: str) -> Optional[Dict[str, Any]]:
    v = r.get(_key(zip_code))
    return json.loads(v) if v else None

def cache_set_zip(zip_code: str, payload: Dict[str, Any], ttl: int = ZIP_CACHE_TTL_SECONDS):
    r.setex(_key(zip_code), ttl, json.dumps(payload))

def cache_ttl(zip_code: str) -> int:
    return r.ttl(_key(zip_code))
