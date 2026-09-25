"""
Build `data/us_outline_20km.geojson` — the US arm's outline for
`local_forecast.select_arm` (d091477, spec §2.2).

SOURCE. Natural Earth 1:10m Cultural Vectors, admin-0 countries (public domain,
https://www.naturalearthdata.com/about/terms-of-use/), as mirrored in the
Natural Earth GitHub repository:

    https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_admin_0_countries.geojson

Two features are kept: `ADM0_A3 == "USA"` (the 50 states + DC — Natural Earth
carries Alaska and Hawaii in the one feature) and `ADM0_A3 == "PRI"` (Puerto
Rico, its own admin-0 feature). Nothing else: the spec names 50 + DC + PR, and
Guam / USVI / American Samoa / the Marianas are left for a ruling (NWS serves
several of them, and D-09-25-04 catches the rest).

THE BUFFER IS THE RULING (§2.2), so it is done honestly in METRES, not degrees:
every polygon part is projected into its own azimuthal-equidistant frame
centred on the part, buffered 20 000 m, and projected back. Parts near the
antimeridian (the western Aleutians) are unwrapped to a continuous longitude
before the union and split back at ±180 afterwards, so the file is plain
RFC 7946 lon/lat.

BUILD-TIME ONLY. This script needs `shapely` and `pyproj`; neither is in
requirements.txt and neither is imported by the API. The runtime reads the
output with `json` and does ray casting in pure Python. Regenerate with:

    pip install --target /tmp/buildlib shapely pyproj
    PYTHONPATH=/tmp/buildlib python scripts/build_us_outline.py ne_10m_admin_0_countries.geojson

    PYTHONPATH=/tmp/buildlib python scripts/build_us_outline.py ne_10m_admin_0_countries.geojson \\
        0 tests/fixtures/us_outline_raw.geojson      # the unbuffered twin (tests only)

Prints the input sha-256, the output bytes and the output sha-256 — the
numbers the handback records.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

BUFFER_M = 20_000.0
SIMPLIFY_DEG = 0.02          # ~2 km: far inside the 20 km buffer's slack
DECIMALS = 3                 # ~110 m
MAX_BYTES = 200 * 1024
KEEP = ("USA", "PRI")

OUT = Path(__file__).resolve().parent.parent / "data" / "us_outline_20km.geojson"


def main(src: str, buffer_m: float = BUFFER_M, out_path: Path = OUT) -> None:
    from pyproj import Transformer
    from shapely.geometry import box, mapping, shape, MultiPolygon, Polygon
    from shapely.ops import transform, unary_union
    from shapely import affinity

    raw = Path(src).read_bytes()
    src_sha = hashlib.sha256(raw).hexdigest()
    doc = json.loads(raw)

    parts = []
    for f in doc["features"]:
        if f["properties"].get("ADM0_A3") in KEEP:
            g = shape(f["geometry"])
            parts.extend(g.geoms if isinstance(g, MultiPolygon) else [g])

    buffered = []
    for p in parts:
        c = p.representative_point()
        lon0, lat0 = c.x, c.y
        aeqd = f"+proj=aeqd +lat_0={lat0} +lon_0={lon0} +datum=WGS84 +units=m"
        fwd = Transformer.from_crs("EPSG:4326", aeqd, always_xy=True).transform
        inv = Transformer.from_crs(aeqd, "EPSG:4326", always_xy=True).transform

        def unwrap(x, y, _lon0=lon0):
            # pyproj hands back [-180, 180]; keep the ring continuous with the
            # part's own centre so a buffer across 180 does not span the globe.
            import numpy as np
            x = np.asarray(x, dtype=float)
            x = np.where(x - _lon0 > 180, x - 360, x)
            x = np.where(x - _lon0 < -180, x + 360, x)
            return x, y

        b = transform(fwd, p).buffer(buffer_m, quad_segs=8) if buffer_m else transform(fwd, p)
        g = transform(inv, b)
        g = transform(unwrap, g)
        # Shift every part into [-180+, 180+] so parts on either side of 180
        # union in ONE continuous frame (Aleutians west of 180 carry lon > 0).
        if g.centroid.x > 0:
            g = affinity.translate(g, xoff=-360)
        buffered.append(g)

    merged = unary_union(buffered)
    # Split back at the antimeridian: anything west of -180 moves to the east.
    west = merged.intersection(box(-540, -90, -180, 90))
    main_ = merged.intersection(box(-180, -90, 180, 90))
    east = affinity.translate(west, xoff=360) if not west.is_empty else west
    merged = unary_union([main_, east]) if not east.is_empty else main_
    merged = merged.simplify(SIMPLIFY_DEG, preserve_topology=True)

    polys = list(merged.geoms) if isinstance(merged, MultiPolygon) else [merged]
    polys = [p for p in polys if isinstance(p, Polygon) and not p.is_empty]

    def ring(coords):
        return [[round(x, DECIMALS), round(y, DECIMALS)] for x, y in coords]

    feature = {
        "type": "Feature",
        "properties": {
            "name": out_path.stem,
            "country": "US",
            "members": "USA (50 states + DC) + PRI",
            "buffer_m": buffer_m,
            "simplify_deg": SIMPLIFY_DEG,
            "source": "Natural Earth 1:10m admin-0 countries (public domain)",
            "source_url": "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/"
                          "master/geojson/ne_10m_admin_0_countries.geojson",
            "source_sha256": src_sha,
            "builder": "scripts/build_us_outline.py",
        },
        "geometry": {
            "type": "MultiPolygon",
            "coordinates": [[ring(p.exterior.coords)] + [ring(h.coords) for h in p.interiors]
                            for p in polys],
        },
    }
    out = json.dumps({"type": "FeatureCollection", "features": [feature]},
                     separators=(",", ":")).encode()
    if len(out) > MAX_BYTES:
        raise SystemExit(f"outline is {len(out)} bytes > {MAX_BYTES}: raise SIMPLIFY_DEG")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(out)
    print(f"source sha256 {src_sha}")
    print(f"polygons {len(polys)}  vertices "
          f"{sum(len(p.exterior.coords) + sum(len(h.coords) for h in p.interiors) for p in polys)}")
    print(f"wrote {out_path} {len(out)} bytes sha256 {hashlib.sha256(out).hexdigest()}")


if __name__ == "__main__":
    # `build_us_outline.py SRC` writes the served file. `build_us_outline.py SRC
    # 0 tests/fixtures/us_outline_raw.geojson` writes the UNBUFFERED outline the
    # tests keep beside it (T1 proves the buffer is what admits the offshore
    # point, and R1 swaps it in).
    args = sys.argv[1:]
    main(args[0],
         float(args[1]) if len(args) > 1 else BUFFER_M,
         Path(args[2]) if len(args) > 2 else OUT)
