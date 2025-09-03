# utils/houston_zips.py
import requests
from typing import List, Set

HOUSTON_ZIPS_URL = "https://api.zippopotam.us/us/tx/houston"
FALLBACK = [
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
]

def fetch_houston_zips(include_po_boxes: bool = False) -> List[str]:
    try:
        r = requests.get(HOUSTON_ZIPS_URL, timeout=20)
        r.raise_for_status()
        data = r.json()
        zips: Set[str] = set()
        for place in data.get("places", []):
            z = place.get("post code") or place.get("post_code") or place.get("post-code")
            if z and len(z) == 5 and z.isdigit():
                zips.add(z)
        if not include_po_boxes:
            zips = {z for z in zips if not z.startswith("772")}
        if zips:
            return sorted(zips)
    except Exception:
        pass
    return sorted([z for z in FALLBACK if include_po_boxes or not z.startswith("772")])
