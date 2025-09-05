"""
FastAPI app with:
- Router include
- Startup prewarm for Houston ZIPs (gentle pacing to avoid Overpass 429s)
- Redis expiration listener that auto-rewarms ZIPs when their 30-day cache expires
"""

from __future__ import annotations
import os
import asyncio
import time
from fastapi import FastAPI
import redis
import logging
from api.cors import add_cors 

try: 
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from api.endpoints import router, compute_green_score  # compute_green_score must be SYNC
from utils.kv import r, cache_set_zip, ZIP_CACHE_PREFIX

# Configure logging at the application level
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# load_dotenv()

# Try to import Houston ZIPs helper; fallback to a static list if missing
try:
    from utils.houston_zips import fetch_houston_zips
except Exception:
    def fetch_houston_zips(include_po_boxes: bool = False):
        fallback = [
            "77002","77003","77004","77005","77006","77007","77008","77009","77010",
            "77011","77012","77013","77014","77015","77016","77017","77018","77019","77020",
            "77021","77022","77023","77024","77025","77026","77027","77028","77029","77030",
            "77031","77032","77033","77034","77035","77036","77037","77038","77039","77040",
            "77041","77042","77043","77044","77045","77046","77047","77048","77049","77050",
            "77051","77053","77054","77055","77056","77057","77058","77059","77060","77061",
            "77062","77063","77064","77065","77066","77067","77068","77069","77070","77071",
            "77072","77073","77074","77075","77076","77077","77078","77079","77080","77081",
            "77082","77083","77084","77085","77086","77087","77088","77089","77090","77091",
            "77092","77093","77094","77095","77096","77098","77099",
        ]
        return fallback

app = FastAPI(title="Green Score API")
add_cors(app, origins=["https://ecoscore-kappa.vercel.app"]) 
app.include_router(router)

# Controls
ENABLE_REDIS_EXPIRE_LISTENER = os.getenv("ENABLE_REDIS_EXPIRE_LISTENER", "1") == "1"
PREWARM_HOUSTON = os.getenv("PREWARM_HOUSTON", "1") == "1"
PREWARM_CONCURRENCY = int(os.getenv("PREWARM_CONCURRENCY", "3"))
PREWARM_SPACING_S = float(os.getenv("PREWARM_SPACING_S", "2.5"))  # lines up with OVERPASS_MIN_INTERVAL_S

def _expiration_worker_blocking():
    """
    Runs in a thread. Subscribes to Redis key expiration events and re-warms
    any key that matches our greenscore prefix.
    """
    while True:
        try:
            # Try to enable keyevent notifications (may fail on managed redis—OK to ignore)
            try:
                r.config_set("notify-keyspace-events", "Ex")
            except Exception:
                pass

            db = r.connection_pool.connection_kwargs.get("db", 0)
            channel = f"__keyevent@{db}__:expired"
            pubsub = r.pubsub(ignore_subscribe_messages=True)
            pubsub.subscribe(channel)

            for msg in pubsub.listen():
                key = msg.get("data")
                if isinstance(key, bytes):
                    key = key.decode()
                if not isinstance(key, str):
                    continue
                if not key.startswith(ZIP_CACHE_PREFIX):
                    continue
                zip_code = key[len(ZIP_CACHE_PREFIX):]
                # Recompute synchronously in a thread
                data = compute_green_score(zip_code)
                if isinstance(data, dict) and not data.get("error"):
                    cache_set_zip(zip_code, data)

        except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError) as e:
            # Redis not ready; back off and retry
            print(f"[redis-expire] Redis unavailable ({e}); retrying in 30s...")
            time.sleep(30)
        except Exception as e:
            print(f"[redis-expire] Listener error: {e}; retrying in 30s...")
            time.sleep(30)

async def _prewarm_houston():
    zips = await asyncio.to_thread(fetch_houston_zips, False)
    sem = asyncio.Semaphore(PREWARM_CONCURRENCY)

    async def one(z):
        print(f"\n[ZIP {z}] Starting computation...")  # Added print
        async with sem:
            data = await asyncio.to_thread(compute_green_score, z)
            if isinstance(data, dict) and not data.get("error"):
                print(f"[ZIP {z}] Success: {data}")  # Added print
                await asyncio.to_thread(cache_set_zip, z, data)
            else:
                print(f"[ZIP {z}] Error: {data}")  # Added print
        await asyncio.sleep(PREWARM_SPACING_S)

    await asyncio.gather(*(asyncio.create_task(one(z)) for z in zips))

@app.on_event("startup")
async def startup():
    # if PREWARM_HOUSTON:
        # asyncio.create_task(_prewarm_houston())
    if ENABLE_REDIS_EXPIRE_LISTENER:
        asyncio.create_task(asyncio.to_thread(_expiration_worker_blocking))
