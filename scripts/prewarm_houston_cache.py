# scripts/prewarm_houston_cache.py
"""
Prewarm /green-score cache for all Houston ZIP codes.

Usage:
  python scripts/prewarm_houston_cache.py \
    --base-url http://localhost:8000 \
    --rpm 6 \
    --concurrency 2

Notes
- By default we exclude PO-box ZIPs (772xx). Use --include-po-boxes to include them.
- Requests are sent in per-minute batches of size --rpm; each batch is executed
  concurrently up to --concurrency workers, then the script sleeps until a minute
  has passed before sending the next batch.
- This warms the in-memory TTL caches on your API server since the server itself
  executes the underlying utils calls.
"""

import argparse
import concurrent.futures as cf
import csv
import time
import datetime as dt
from typing import List, Set
import requests
import string 
import os

HOUSTON_ZIPS_URL = "https://api.zippopotam.us/us/tx/houston"

# Reasonable fallback if the discovery API is down
FALLBACK_HOUSTON_ZIPS: List[str] = [
    # Core 770xx ZIPs commonly used in Houston addresses
    "77002","77003","77004","77005","77006","77007","77008","77009","77010",
    "77011","77012","77013","77014","77015","77016","77017","77018","77019","77020",
    "77021","77022","77023","77024","77025","77026","77027","77028","77029","77030",
    "77031","77032","77033","77034","77035","77036","77037","77038","77039","77040",
    "77041","77042","77043","77044","77045","77046","77047","77048","77049","77050",
    "77051","77053","77054","77055","77056","77057","77058","77059","77060","77061",
    "77062","77063","77064","77065","77066","77067","77068","77069","77070","77071",
    "77072","77073","77074","77075","77076","77077","77078","77079","77080","77081",
    "77082","77083","77084","77085","77086","77087","77088","77089","77090","77091",
    "77092","77093","77094","77095","77096","77098","77099"
    # (Extend as needed; this already covers the vast majority of Houston addresses.)
]

def fetch_houston_zips(include_po_boxes: bool) -> List[str]:
    try:
        r = requests.get(HOUSTON_ZIPS_URL, timeout=20)
        r.raise_for_status()
        data = r.json()
        zips: Set[str] = set()
        for place in data.get("places", []):
            z = place.get("post code") or place.get("post_code") or place.get("post-code")
            if not z:
                continue
            z = z.strip()
            if len(z) == 5 and z.isdigit():
                zips.add(z)
        if not include_po_boxes:
            zips = {z for z in zips if not z.startswith("772")}
        if zips:
            return sorted(zips)
    except Exception:
        pass
    # Fallback
    return sorted([z for z in FALLBACK_HOUSTON_ZIPS if include_po_boxes or not z.startswith("772")])

def prewarm_one(base_url: str, zip_code: string) -> dict:
    t0 = time.time()
    url = f"{base_url.rstrip('/')}/green-score"
    try:
        resp = requests.get(url, params={"zip": zip_code}, timeout=120)
        elapsed = time.time() - t0
        ok = resp.status_code == 200
        payload = resp.json() if ok else {"error": resp.text}
        return {
            "zip": zip_code,
            "status": resp.status_code,
            "ok": ok,
            "elapsed_s": round(elapsed, 3),
            "error": None if ok else str(payload)[:300]
        }
    except Exception as e:
        elapsed = time.time() - t0
        return {
            "zip": zip_code,
            "status": 0,
            "ok": False,
            "elapsed_s": round(elapsed, 3),
            "error": str(e)[:300]
        }

def run_batches(base_url: str, zips: List[str], rpm: int, concurrency: int, dry_run: bool):
    assert rpm >= 1, "rpm must be >= 1"
    assert concurrency >= 1, "concurrency must be >= 1"
    results = []
    batch_size = rpm  # requests per minute
    started = dt.datetime.utcnow()

    print(f"Discovered {len(zips)} Houston ZIPs")
    print(f"Prewarming against {base_url} with rpm={rpm}, concurrency={concurrency}, dry_run={dry_run}")
    for i in range(0, len(zips), batch_size):
        batch = zips[i:i+batch_size]
        print(f"\nBatch {i//batch_size + 1}: {batch}")
        t_batch_start = time.time()
        if dry_run:
            for z in batch:
                results.append({"zip": z, "status": -1, "ok": True, "elapsed_s": 0.0, "error": None})
        else:
            with cf.ThreadPoolExecutor(max_workers=concurrency) as ex:
                futs = [ex.submit(prewarm_one, base_url, z) for z in batch]
                for fut in cf.as_completed(futs):
                    res = fut.result()
                    results.append(res)
                    status = "OK" if res["ok"] else f"ERR({res['status']})"
                    print(f"  {res['zip']}: {status} in {res['elapsed_s']}s" + (f" – {res['error']}" if res['error'] else ""))

        # Rate limit: ensure at least 60s between batches
        elapsed = time.time() - t_batch_start
        if i + batch_size < len(zips):  # if more to run
            sleep_s = max(0.0, 60.0 - elapsed)
            if sleep_s > 0:
                print(f"Sleeping {sleep_s:.1f}s to respect rpm")
                time.sleep(sleep_s)

    finished = dt.datetime.utcnow()
    # Write CSV log
    stamp = finished.strftime("%Y%m%d_%H%M%S")
    out_path = f"prewarm_houston_log_{stamp}.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["zip","status","ok","elapsed_s","error"])
        w.writeheader()
        w.writerows(sorted(results, key=lambda r: r["zip"]))
    ok_count = sum(1 for r in results if r["ok"])
    print(f"\nDone. {ok_count}/{len(results)} successful. Log: {out_path}")
    print(f"Started: {started.isoformat()}Z  Finished: {finished.isoformat()}Z")

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", default="http://localhost:8000", help="Your API base URL")
    p.add_argument("--rpm", type=int, default=6, help="Requests per minute (total, across all workers)")
    p.add_argument("--concurrency", type=int, default=2, help="Concurrent workers within each minute")
    p.add_argument("--include-po-boxes", action="store_true", help="Include 772xx PO-box ZIPs")
    p.add_argument("--limit", type=int, default=0, help="Limit number of ZIPs (for testing)")
    p.add_argument("--dry-run", action="store_true", help="Discover and print, but do not call the API")
    args = p.parse_args()

    zips = fetch_houston_zips(include_po_boxes=args.include_po_boxes)
    if args.limit and args.limit > 0:
        zips = zips[:args.limit]
    run_batches(args.base_url, zips, rpm=args.rpm, concurrency=args.concurrency, dry_run=args.dry_run)

if __name__ == "__main__":
    main()
