from pystac_client import Client
from planetary_computer import sign
from rasterio.windows import from_bounds
from rasterio.enums import Resampling
from rasterio.warp import transform_bounds
import rasterio, numpy as np

def read_rgbn_window(lat, lon, radius_deg=0.0075, max_size=384):  # smaller than before
    lon_min, lat_min = lon - radius_deg, lat - radius_deg
    lon_max, lat_max = lon + radius_deg, lat + radius_deg

    catalog = Client.open("https://planetarycomputer.microsoft.com/api/stac/v1")
    search = catalog.search(
        collections=["naip"],
        bbox=[lon_min, lat_min, lon_max, lat_max],
        datetime="2015-01-01/2025-12-31",
        limit=1,
    )
    items = list(search.items())  # was get_items()

    if not items:
        return None, None, {"error": "No NAIP imagery found."}

    item = items[0]
    href = sign(item.assets["image"]).href

    with rasterio.open(href) as src:
        bbox_ds = transform_bounds("EPSG:4326", src.crs, lon_min, lat_min, lon_max, lat_max, densify_pts=21)
        win = from_bounds(*bbox_ds, transform=src.transform)

        w, h = int(win.width), int(win.height)
        scale = min(max_size / max(w, h), 1.0)
        out_w, out_h = max(1, int(w*scale)), max(1, int(h*scale))

        arr = src.read(
            indexes=[1,2,3,4],
            window=win,
            out_shape=(4, out_h, out_w),
            resampling=Resampling.bilinear,
            boundless=True,
        )
    return arr, item, None

def compute_ndvi(arr: np.ndarray) -> np.ndarray:
    """
    Compute the Normalized Difference Vegetation Index (NDVI) from an RGB+NIR array.
    """
    red = arr[0].astype(np.float32)
    nir = arr[3].astype(np.float32)
    return (nir - red) / (nir + red + 1e-5)