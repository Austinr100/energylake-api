# CC Spec — Weather Atlas B: the point API (energylake-api)

**Filed 2026-09-02.** Repo: `energylake-api` (FastAPI on Railway). Charter:
`energylake-pantry:docs/arc_charter_2026_09_01_two_weather_surfaces.md` §7 —
"the click must be able to hand its value to the degree-day, load, and LMP
rungs, which live server-side." Recon: `docs/recon_tile_price_2026_09_02.md`
§6 — build the point API first; tiles carry the hover, the API carries the
click.

**Depends on Spec A's sidecar** (`weather/values/.../{param}_f{fhr}.f32` +
`.json` header). This lane can build against a sidecar the captain has
dispatched once; it must not decode GRIB. **No new dependencies**: the API has
`boto3` already (the Model Room R2 proxy) and byte-range GET is all this needs.

## 1. The endpoint

```
GET /api/weather/point?lat=&lon=&param=&model=gfs&run=YYYYMMDDTHHZ&fhr=
GET /api/weather/point/ladder?lat=&lon=&param=&model=gfs&run=   (all fhrs)
```

Response (single):
```json
{"param":"t2m_anom","model":"gfs","run":"2026-09-02T00Z","fhr":24,
 "valid":"2026-09-03T00:00:00Z",
 "point":{"lat":36.74,"lon":-119.79},
 "cell":{"lat":36.75,"lon":-119.75,"j":88,"i":...,"distance_km":5.2},
 "value":3.12,"units":"K","display":{"value":3.12,"units":"°C"},
 "source":{"key":"weather/values/gfs/na3/20260902/00Z/t2m_anom_f024.f32",
           "sha256":"…","bytes_read":4},
 "crop":"na3"}
```

Ladder: the same shape with `values:[{fhr,valid,value}]` × 41, and the header
read once.

## 2. How it reads — the whole trick

1. Fetch the `.json` header (cache in-process by key, TTL = forever; it is
   immutable per frame).
2. Nearest cell from the header's `lat0/lon0/dlat/dlon/shape`, honouring
   `lat_order`. Longitude normalised to the sidecar's convention (the header
   says which). Outside the crop box → 404 with the crop's bounds in the
   body, never a nearest-edge value.
3. `GET` with `Range: bytes=4*(j*nx+i)-…+3` against the `.f32`. Four bytes.
   Decode little-endian float32. NaN → 204-class answer `{"value":null,
   "reason":"nodata"}` (the antimeridian strip), not an error.
4. Ladder: 41 range GETs in parallel (thread pool, bounded at 8), one header.
5. Cache: an LRU of (key, offset) → value, small; and the header cache. No
   array caching in the API — the array never enters the container.

Fail-loud: a missing sidecar is a **404 that says which key** was expected
and whether the frame exists as a PNG (so the reader learns "not written"
vs "not rendered"); a sha in the header that does not match a full-object
sha is never checked here (range reads cannot), and the response says
`"verified":false` — Spec A's manifest is where the chain is proven.

## 3. The chain hook (charter §7) — stub, not build

Add `?chain=1` which, for `param=t2m_anom` or a raw temperature, ALSO returns
the nearest station's degree-day context and the nearest pnode from the
existing tables (`station_metadata.json` is already in the repo; the nodal
Atlas already answers nearest-pnode). v0 returns the two identifiers and a
`chain: ["temperature","degree_day","load","lmp"]` list with the first rung
filled and the rest `null` with reasons. The point is that the shape exists
on day one so the Atlas panel can render the rungs as they light up.

## 4. Tests

`tests/test_weather_point.py` with a fake S3 client: header → index math
(four corners, centre, antimeridian cell, out-of-box), range string
correctness, little-endian decode, NaN handling, ladder fan-out count = 41,
404 body names the key. No network in tests.

## 5. Handback

Branch, route names, the test tail, and one real curl against a sidecar the
captain dispatched (Spec A proof), showing value + cell + bytes_read=4 +
latency. No PR, no merge, no Railway changes.
