"""d091741 receipt: record GIBS GeoColor z0 presence at 18 descending 10-minute slots.

READ-ONLY, stdlib only. Run: python3 -I docs/receipts/tropics-api-index-d091741/record_gibs_z0.py OUT.jsonl
Appends one JSON line per layer: per slot the status, content-type, bytes, sha12 and
GIBS's `layer-time-actual` header (the time it says it served). /ground's G1 test
replays these lines; the first line was taken before `layer-time-actual` was read.
"""
import hashlib
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

UA = "energylake-api-d091741 (energylake.io)"
BASE = "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best"
TMS = "GoogleMapsCompatible_Level7"


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.headers.get("content-type"), r.headers.get("layer-time-actual"), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("content-type"), e.headers.get("layer-time-actual"), e.read()


def main(out_path):
    now = datetime.now(timezone.utc)
    slot0 = now.replace(minute=now.minute - now.minute % 10, second=0, microsecond=0)
    with open(out_path, "a") as out:
        for layer in ("GOES-East_ABI_GeoColor", "GOES-West_ABI_GeoColor"):
            slots = []
            for k in range(18):
                t = slot0 - timedelta(minutes=10 * k)
                code, ct, actual, body = get(f"{BASE}/{layer}/default/{t:%Y-%m-%dT%H:%M:%SZ}/{TMS}/0/0/0.png")
                slots.append({"slot_utc": f"{t:%Y-%m-%dT%H:%M:%SZ}", "status": code, "content_type": ct,
                              "layer_time_actual": actual, "bytes": len(body),
                              "sha12": hashlib.sha256(body).hexdigest()[:12]})
            rec = {"recorded_by": "d091741", "probed_utc": now.isoformat(timespec="seconds"),
                   "layer": layer, "tile": "z0/0/0", "slots": slots}
            out.write(json.dumps(rec, sort_keys=True) + "\n")
            print(layer, now.isoformat(timespec="seconds"),
                  [(s["slot_utc"][11:16], s["status"], (s["layer_time_actual"] or "")[11:16]) for s in slots[:7]])


if __name__ == "__main__":
    main(sys.argv[1])
