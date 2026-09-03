"""
Weather Atlas B — the point API, the pure half + the four-byte reader.

WHAT THIS LANE IS. The tiles carry the hover; the API carries the click. A
click is a latitude and a longitude, and the answer is ONE grid cell's value.
Spec A writes, beside every rendered frame, a **value sidecar**: a raw
little-endian float32 array `{param}_f{fhr}.f32` and a `.json` header that
describes its geometry. This module turns (lat, lon) into a byte offset in that
array and reads **exactly four bytes** out of it over HTTP `Range`.

THE ONE RULE THIS MODULE EXISTS TO ENFORCE
──────────────────────────────────────────

    THE ARRAY NEVER ENTERS THE CONTAINER.

The na3 crop is 222 x 583 float32 = 517,704 bytes per frame; a 41-frame ladder
is 21 MB. Downloading either to answer a click would make the API a proxy for a
file it has no business holding, and would put the cost of a hover on the
render budget. So every read here is a `Range: bytes=N-N+3` GET, four bytes
wide, and `bytes_read` rides on every response as the assertion. A ladder is 41
of those (one per forecast hour), never one full-object read. If you ever find
yourself wanting the whole array, you want Spec A's renderer, not this lane.

THE HEADER CONTRACT (measured, not assumed). Pinned against the live proof
object the captain dispatched — `weather/values/gfs/na3/20260903/06Z/
mslp_f006.json`:

    shape            [222, 583]                  # [ny, nx] — rows, then columns
    lat0             14.75                       # the latitude of row j=0
    lon0             -186.75                     # the longitude of column i=0
    dlat / dlon      0.25 / 0.25
    lat_order        "ascending"                 # row 0 is the SOUTHERNMOST
    lon_convention   "west_negative_monotonic"
    dtype            float32, little-endian
    sha256           the full-object digest (see VERIFIED, below)

which puts the na3 crop at 14.75N..70.00N and -186.75..-41.25 longitude
(583 columns is 582 steps of 0.25 degrees = 145.5, not 145.75 — the fencepost
that makes the eastern edge -41.25 and not -41.00).

WHY lon0 IS -186.75 AND WHY THAT IS NOT A BUG. `west_negative_monotonic` means
the longitude axis is a single monotonically increasing run of degrees-west-are-
negative values that is allowed to walk PAST -180 rather than wrapping to +173.
That is what makes the axis usable as a flat array index at all: a wrapping axis
has a seam in the middle of the row and `i = (lon - lon0)/dlon` stops being
true. The cost is that an incoming longitude has to be moved onto that run
before it can be indexed — a point in the western Aleutians arrives as +176.5
and must be read as -183.5. `normalize_lon` is the whole of that translation and
it is the only place in this lane that is allowed to touch a longitude.

VERIFIED IS ALWAYS FALSE HERE, AND SAYS SO. The header carries a sha256 of the
WHOLE `.f32` object. A four-byte range read cannot check it — you would have to
read the 517 KB this module exists not to read. So the digest is echoed and
`verified: false` is stated outright on every response. The chain is proven in
Spec A's manifest, at write time, over the whole object; it is not re-proven per
click, and pretending otherwise on the wire would be the lie this field exists
to prevent.

NaN IS AN ANSWER, NOT AN ERROR. The crop's corners run off the model domain and
the antimeridian strip is unwritten; those cells hold NaN. A NaN read comes back
`{"value": null, "reason": "nodata"}` with a 200, because "this cell is outside
what the model wrote" is a fact about the grid, not a failure of the request.
A point outside the crop BOX, by contrast, is a 404 that states the bounds —
never the nearest edge cell, which would answer a question about Kansas with a
number from Texas.

NO NEW DEPENDENCIES. `httpx` (already a dev/runtime dep of the framework) and
the standard library. The R2 request signer below is ~40 lines of `hmac` and
`hashlib` rather than a boto3 import, because SigV4 over a GET with an empty
body is genuinely that small and this lane refuses to grow a dependency to make
one range request.

WHAT LIVES HERE vs IN main.py. This module owns the vocabulary, the geometry,
the decode, the key scheme, the unit display map, and the store that does the
range GET (with an injectable transport, so every test runs against a synthetic
sidecar and no test touches the network). main.py owns the routes, the query
validation, and the response envelope.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json as _json
import math
import re
import struct
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

UTC = timezone.utc


# ═══════════════════════════════════════════════════════════════════════════
# Vocabulary
# ═══════════════════════════════════════════════════════════════════════════

#: The sidecar prefix Spec A writes under. Everything this lane reads lives
#: below it; nothing else is reachable through these routes.
VALUES_ROOT = "weather/values/"

#: Slugs that may appear in a key. Deliberately tighter than the S3 key charset:
#: a request string becomes part of an object key, so it is matched against
#: these BEFORE it is interpolated, and a miss is a 400 that never touches R2.
MODEL_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
CROP_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,15}$")
PARAM_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,31}$")

#: The forecast-hour ladder for the GFS synoptic crop: f000..f240 every 6 h.
#: 240/6 + 1 = 41 frames, which is the "41 range GETs" the spec counts. Both
#: knobs are query-overridable; the fan-out cap is not.
LADDER_FHR_STEP = 6
LADDER_FHR_MAX = 240
LADDER_MAX_FRAMES = 64          # hard ceiling on the fan-out, whatever is asked
LADDER_CONCURRENCY = 8          # simultaneous range GETs — spec §2.4

#: One value is one float32.
BYTES_PER_POINT = 4

#: Grid geometry we can read. `dtype`/`endianness` are checked when the header
#: states them and defaulted to these when it does not.
DTYPE = "float32"
ENDIANNESS = "little"
_STRUCT_FMT = "<f"

#: Cache sizes. The header cache is effectively permanent (a frame's header is
#: immutable once written); the value cache is small because a click is a click.
#: Neither holds an array — the largest thing in either is a ~1 KB dict.
HEADER_CACHE_MAX = 512
VALUE_CACHE_MAX = 4096

EARTH_RADIUS_KM = 6371.0088


class PointError(Exception):
    """A fail-loud refusal carrying the HTTP status and the body the route
    should hand back. Every one of these bodies names the thing the reader
    needs in order to learn WHICH of the two failures they have — a key that
    was never written, or a request that was never inside the box."""

    def __init__(self, status: int, detail: dict[str, Any]):
        self.status = status
        self.detail = detail
        super().__init__(_json.dumps(detail, default=str))


# ═══════════════════════════════════════════════════════════════════════════
# The key scheme
# ═══════════════════════════════════════════════════════════════════════════

_RUN_RE = re.compile(
    r"^(?P<y>\d{4})-?(?P<m>\d{2})-?(?P<d>\d{2})[T ]?(?P<h>\d{2})Z?$"
)


def parse_run(run: str) -> datetime:
    """`20260903T06Z`, `2026-09-03T06Z`, `2026090306` → an aware UTC datetime.

    The wire spelling of a run is not settled across the estate (the spec's own
    example response says `2026-09-02T00Z` while its query example says
    `YYYYMMDDTHHZ`), so all three spellings are accepted on the way in and
    exactly one is emitted on the way out — see `format_run`.
    """
    m = _RUN_RE.match((run or "").strip())
    if not m:
        raise PointError(400, {
            "error": "invalid run",
            "run": run,
            "expected": "YYYYMMDDTHHZ (e.g. 20260903T06Z)",
        })
    try:
        dt = datetime(int(m["y"]), int(m["m"]), int(m["d"]), int(m["h"]),
                      tzinfo=UTC)
    except ValueError as e:
        raise PointError(400, {"error": f"invalid run: {e}", "run": run})
    if dt.hour % 6 != 0:
        # Not fatal to the arithmetic, but a non-synoptic cycle will never have
        # a sidecar, and saying so here beats a 404 three calls later.
        raise PointError(400, {
            "error": "run cycle is not synoptic",
            "run": run,
            "cycles": ["00Z", "06Z", "12Z", "18Z"],
        })
    return dt


def format_run(run_dt: datetime) -> str:
    """The ONE wire spelling of a run: `2026-09-03T06Z`."""
    return run_dt.strftime("%Y-%m-%dT%HZ")


def format_valid(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def valid_time(run_dt: datetime, fhr: int) -> datetime:
    return run_dt + timedelta(hours=int(fhr))


def sidecar_stem(model: str, crop: str, run_dt: datetime, param: str,
                 fhr: int) -> str:
    """`weather/values/gfs/na3/20260903/06Z/mslp_f006` — the two sidecar legs
    (`.f32`, `.json`) differ only in their suffix, so the stem is built once.

    Callers MUST have validated model/crop/param against the slug regexes
    first; `validate_slugs` is the single place that happens.
    """
    return (f"{VALUES_ROOT}{model}/{crop}/{run_dt:%Y%m%d}/{run_dt:%H}Z/"
            f"{param}_f{int(fhr):03d}")


def value_key(model: str, crop: str, run_dt: datetime, param: str,
              fhr: int) -> str:
    return sidecar_stem(model, crop, run_dt, param, fhr) + ".f32"


def header_key(model: str, crop: str, run_dt: datetime, param: str,
               fhr: int) -> str:
    return sidecar_stem(model, crop, run_dt, param, fhr) + ".json"


def render_key(model: str, crop: str, run_dt: datetime, param: str,
               fhr: int) -> str:
    """The PNG the renderer writes beside the sidecar. Probed ONLY on the
    absent-sidecar path, to tell 'not written' from 'not rendered' — and the
    404 body always states the key that was probed, so a reader who finds this
    scheme wrong can see that rather than infer it."""
    return sidecar_stem(model, crop, run_dt, param, fhr) + ".png"


def validate_slugs(model: str, crop: str, param: str) -> None:
    for name, value, rx in (("model", model, MODEL_RE),
                            ("crop", crop, CROP_RE),
                            ("param", param, PARAM_RE)):
        if not rx.match(value or ""):
            raise PointError(400, {
                "error": f"invalid {name}",
                name: value,
                "pattern": rx.pattern,
            })


def ladder_fhrs(step: int = LADDER_FHR_STEP,
                fhr_max: int = LADDER_FHR_MAX) -> list[int]:
    """f000..f240 by 6 → 41 frames. The count is the spec's fan-out assertion,
    so it is derived here once and asserted in the tests rather than typed as
    a literal in two places."""
    if step < 1 or fhr_max < 0:
        raise PointError(400, {"error": "invalid ladder range",
                               "fhr_step": step, "fhr_max": fhr_max})
    fhrs = list(range(0, fhr_max + 1, step))
    if len(fhrs) > LADDER_MAX_FRAMES:
        raise PointError(400, {
            "error": "ladder too wide",
            "frames": len(fhrs),
            "max_frames": LADDER_MAX_FRAMES,
        })
    return fhrs


# ═══════════════════════════════════════════════════════════════════════════
# The header
# ═══════════════════════════════════════════════════════════════════════════

def _first(doc: dict, names: Iterable[str]) -> Any:
    for n in names:
        if n in doc and doc[n] is not None:
            return doc[n]
    return None


class SidecarHeader:
    """The parsed `.json` header — the grid, and nothing derived from the array.

    STRICT WHERE GUESSING WOULD BE WRONG, TOLERANT WHERE IT WOULD NOT.
    `shape`, `lat0`, `lon0`, `dlat`, `dlon` are unguessable: absent one of them
    there is no index arithmetic to do, so a header missing any is a 502 that
    names the missing field and lists the fields it DID carry. `lat_order` and
    `lon_convention`, by contrast, are recoverable from the numbers themselves
    (a negative `dlat` is a descending axis; a negative `lon0` is a west-negative
    axis), so when the header omits them they are inferred — and every inferred
    field is listed in `inferred`, which rides on the response. An inference the
    reader can see is not a silent default.
    """

    __slots__ = ("ny", "nx", "lat0", "lon0", "dlat", "dlon", "lat_order",
                 "lon_convention", "dtype", "endianness", "sha256", "units",
                 "param", "model", "crop", "inferred", "raw")

    def __init__(self, doc: dict[str, Any], *, key: str = ""):
        if not isinstance(doc, dict):
            raise PointError(502, {"error": "sidecar header is not an object",
                                   "key": key})
        self.raw = doc
        self.inferred: list[str] = []

        shape = _first(doc, ("shape", "grid_shape"))
        ny = nx = None
        if isinstance(shape, (list, tuple)) and len(shape) == 2:
            ny, nx = int(shape[0]), int(shape[1])
        else:
            ny = _first(doc, ("ny", "nlat", "n_lat"))
            nx = _first(doc, ("nx", "nlon", "n_lon"))
            ny = int(ny) if ny is not None else None
            nx = int(nx) if nx is not None else None

        lat0 = _first(doc, ("lat0", "lat_0", "lat_origin"))
        lon0 = _first(doc, ("lon0", "lon_0", "lon_origin"))
        dlat = _first(doc, ("dlat", "d_lat", "lat_step"))
        dlon = _first(doc, ("dlon", "d_lon", "lon_step"))

        missing = [n for n, v in (("shape", shape if shape is not None else ny),
                                  ("lat0", lat0), ("lon0", lon0),
                                  ("dlat", dlat), ("dlon", dlon))
                   if v is None]
        if missing or not ny or not nx:
            raise PointError(502, {
                "error": "sidecar header is incomplete",
                "key": key,
                "missing": missing or ["shape"],
                "present": sorted(doc.keys()),
            })

        self.ny, self.nx = ny, nx
        self.lat0, self.lon0 = float(lat0), float(lon0)
        self.dlat, self.dlon = float(dlat), float(dlon)
        if self.dlat == 0 or self.dlon == 0:
            raise PointError(502, {"error": "sidecar header has a zero step",
                                   "key": key,
                                   "dlat": self.dlat, "dlon": self.dlon})
        if self.ny < 1 or self.nx < 1:
            raise PointError(502, {"error": "sidecar header has an empty shape",
                                   "key": key, "shape": [self.ny, self.nx]})

        lat_order = _first(doc, ("lat_order", "latitude_order", "lat_dir"))
        if lat_order is None:
            lat_order = "descending" if self.dlat < 0 else "ascending"
            self.inferred.append("lat_order")
        lat_order = str(lat_order).lower()
        if lat_order not in ("ascending", "descending"):
            raise PointError(502, {"error": "unknown lat_order", "key": key,
                                   "lat_order": lat_order})
        self.lat_order = lat_order

        lon_conv = _first(doc, ("lon_convention", "longitude_convention",
                                "lon_conv"))
        if lon_conv is None:
            lon_conv = ("west_negative_monotonic" if self.lon0 < 0
                        else "east_0_360")
            self.inferred.append("lon_convention")
        lon_conv = str(lon_conv).lower()
        if lon_conv not in ("west_negative_monotonic", "east_0_360"):
            raise PointError(502, {"error": "unknown lon_convention",
                                   "key": key, "lon_convention": lon_conv})
        self.lon_convention = lon_conv

        dtype = str(_first(doc, ("dtype", "data_type")) or DTYPE).lower()
        if dtype not in (DTYPE, "f4", "<f4", "float32_le"):
            raise PointError(502, {
                "error": "unsupported sidecar dtype",
                "key": key, "dtype": dtype, "supported": [DTYPE],
            })
        self.dtype = DTYPE
        endian = str(_first(doc, ("endianness", "endian", "byte_order"))
                     or ENDIANNESS).lower()
        if endian not in (ENDIANNESS, "le", "<"):
            raise PointError(502, {
                "error": "unsupported sidecar endianness",
                "key": key, "endianness": endian, "supported": [ENDIANNESS],
            })
        self.endianness = ENDIANNESS

        self.sha256 = _first(doc, ("sha256", "sha_256", "checksum_sha256"))
        self.units = _first(doc, ("units", "unit"))
        self.param = _first(doc, ("param", "parameter"))
        self.model = _first(doc, ("model",))
        self.crop = _first(doc, ("crop", "domain"))

    # ── the axes ────────────────────────────────────────────────────────────

    @property
    def lat_step(self) -> float:
        """The SIGNED latitude step per row index. `lat_order` is the authority;
        a header that says "ascending" while carrying a negative `dlat` means
        row j=0 is the southernmost and the magnitude is the spacing."""
        mag = abs(self.dlat)
        return mag if self.lat_order == "ascending" else -mag

    @property
    def lon_step(self) -> float:
        return abs(self.dlon)

    def lat_at(self, j: int) -> float:
        return self.lat0 + j * self.lat_step

    def lon_at(self, i: int) -> float:
        return self.lon0 + i * self.lon_step

    @property
    def lat_bounds(self) -> tuple[float, float]:
        """(min, max) of the CELL CENTRES, whichever way the axis runs."""
        a, b = self.lat_at(0), self.lat_at(self.ny - 1)
        return (a, b) if a <= b else (b, a)

    @property
    def lon_bounds(self) -> tuple[float, float]:
        return (self.lon_at(0), self.lon_at(self.nx - 1))

    @property
    def expected_bytes(self) -> int:
        return self.ny * self.nx * BYTES_PER_POINT

    def axes(self) -> dict[str, Any]:
        """The geometry, echoed on the response so a client can do its own
        arithmetic against the same numbers we did."""
        lat_min, lat_max = self.lat_bounds
        lon_min, lon_max = self.lon_bounds
        return {
            "shape": [self.ny, self.nx],
            "lat0": self.lat0, "lon0": self.lon0,
            "dlat": self.dlat, "dlon": self.dlon,
            "lat_order": self.lat_order,
            "lon_convention": self.lon_convention,
            "dtype": self.dtype, "endianness": self.endianness,
            "bounds": {"lat_min": lat_min, "lat_max": lat_max,
                       "lon_min": lon_min, "lon_max": lon_max},
            "expected_bytes": self.expected_bytes,
            "inferred": list(self.inferred),
        }


# ═══════════════════════════════════════════════════════════════════════════
# The geometry — (lat, lon) → (j, i) → byte offset
# ═══════════════════════════════════════════════════════════════════════════

def wrap180(lon: float) -> float:
    """Any real longitude → (-180, 180]. The front door for a click."""
    lon = float(lon)
    if not math.isfinite(lon):
        raise PointError(400, {"error": "lon is not finite", "lon": lon})
    lon = math.fmod(lon + 180.0, 360.0)
    if lon <= 0:
        lon += 360.0
    return lon - 180.0


def normalize_lon(lon: float, hdr: SidecarHeader) -> float:
    """Move a longitude onto the sidecar's own axis run.

    THE ANTIMERIDIAN, IN ONE FUNCTION. na3's axis runs -186.75 -> -41.25: a
    monotonic run that walks 6.75 degrees PAST -180 to pick up the western
    Aleutians without a seam. A click there arrives as +176.5 (nobody's map
    hands you -183.5), and +176.5 is not in [-186.75, -41.25], so indexing it
    directly would call Alaska out-of-box. The fix is a single 360-degree shift
    toward the axis: try the wrapped value, then that value ±360, and take the
    first that lands inside the run. Nothing else is tried, so a point that is
    genuinely outside the crop stays outside it — this widens the axis's reach
    to its true 360-degree extent, it does not wrap a miss into a hit.

    `east_0_360` axes get the mirror of the same treatment.
    """
    base = wrap180(lon)
    lo, hi = hdr.lon_bounds
    half = hdr.lon_step / 2.0
    for candidate in (base, base - 360.0, base + 360.0):
        if (lo - half) <= candidate <= (hi + half):
            return candidate
    # Outside the run every way round: hand back the convention-shaped value so
    # the 404 body shows the reader the number that was actually compared.
    return base if hdr.lon_convention == "west_negative_monotonic" else (
        base + 360.0 if base < 0 else base)


def _nearest_index(value: float, origin: float, step: float,
                   n: int) -> Optional[int]:
    """Nearest index along one axis, or None when the point falls outside the
    axis's FOOTPRINT — the centres extended by half a cell at each end, which
    is the area the grid actually covers. Half a cell of tolerance is the
    difference between 'the edge cell is the nearest cell' and 'the edge cell
    is a cell 40 km away that happens to be last'."""
    frac = (value - origin) / step
    idx = int(math.floor(frac + 0.5))
    if idx < 0 or idx > n - 1:
        return None
    return idx


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance. Used for one thing only: telling the reader how
    far the cell they got is from the point they asked for. At 0.25 degrees a
    worst-case nearest-cell miss is ~19 km at the equator, so a distance that
    reads much larger than that is a sign the click was near a crop edge."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = (math.sin(dp / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def locate(lat: float, lon: float, hdr: SidecarHeader,
           *, crop: str = "") -> dict[str, Any]:
    """(lat, lon) → the nearest cell, or a 404 that states the bounds.

    NEVER A NEAREST-EDGE VALUE. A point outside the crop box raises rather than
    clamping to the edge row/column, because an edge value returned without
    complaint is indistinguishable from a real one and would quietly answer
    questions about places the crop does not cover.
    """
    lat = float(lat)
    if not math.isfinite(lat) or not -90.0 <= lat <= 90.0:
        raise PointError(400, {"error": "lat out of range", "lat": lat,
                               "range": [-90, 90]})
    nlon = normalize_lon(lon, hdr)

    j = _nearest_index(lat, hdr.lat0, hdr.lat_step, hdr.ny)
    i = _nearest_index(nlon, hdr.lon0, hdr.lon_step, hdr.nx)
    if j is None or i is None:
        lat_min, lat_max = hdr.lat_bounds
        lon_min, lon_max = hdr.lon_bounds
        raise PointError(404, {
            "error": "point is outside the crop",
            "crop": crop or hdr.crop,
            "point": {"lat": lat, "lon": wrap180(lon)},
            "normalized_lon": nlon,
            "lon_convention": hdr.lon_convention,
            "bounds": {"lat_min": lat_min, "lat_max": lat_max,
                       "lon_min": lon_min, "lon_max": lon_max,
                       "note": ("cell-centre bounds; the covered footprint "
                                "extends half a cell beyond each")},
            "outside": ([] if j is not None else ["lat"])
                       + ([] if i is not None else ["lon"]),
        })

    clat, clon = hdr.lat_at(j), hdr.lon_at(i)
    return {
        "lat": clat,
        "lon": wrap180(clon),
        "lon_grid": clon,
        "j": j,
        "i": i,
        "distance_km": round(haversine_km(lat, nlon, clat, clon), 3),
    }


def byte_offset(j: int, i: int, hdr: SidecarHeader) -> int:
    """Row-major, `4 * (j*nx + i)`. The array is a flat C-order dump; this is
    the whole of the addressing."""
    return BYTES_PER_POINT * (j * hdr.nx + i)


def range_header(offset: int) -> str:
    """`bytes=N-N+3`. Inclusive at both ends, which is why the tail is +3 and
    not +4 — an off-by-one here reads three bytes of one float and one of the
    next, and `struct` would happily decode the result into a plausible
    number."""
    return f"bytes={offset}-{offset + BYTES_PER_POINT - 1}"


def decode_value(raw: bytes, *, key: str = "", offset: int = 0) -> Optional[float]:
    """Four little-endian bytes → a float, or None for NaN.

    NaN is the sidecar's spelling of 'the model wrote nothing here' (the crop's
    off-domain corners and the antimeridian strip). It comes back as None with
    a `nodata` reason at the route, not as an error and never as 0.0.
    Infinities are treated the same way: they are not values a client can plot.
    """
    if len(raw) != BYTES_PER_POINT:
        raise PointError(502, {
            "error": "short range read",
            "key": key, "offset": offset,
            "expected_bytes": BYTES_PER_POINT, "got_bytes": len(raw),
        })
    (value,) = struct.unpack(_STRUCT_FMT, raw)
    if not math.isfinite(value):
        return None
    return float(value)


# ═══════════════════════════════════════════════════════════════════════════
# Units — what the number is, and what a human should see
# ═══════════════════════════════════════════════════════════════════════════

def display_value(value: Optional[float], units: Optional[str],
                  param: Optional[str]) -> dict[str, Any]:
    """The presentation leg. `value`/`units` on the response are ALWAYS the
    sidecar's own number in the sidecar's own units — this adds a second,
    clearly-labelled pair for the panel, and states the conversion it used so
    nobody has to reverse-engineer it from the magnitude.

    An ANOMALY in kelvin is a DIFFERENCE, so it converts to °C by identity and
    not by subtracting 273.15 — the one conversion in this table that is a trap.
    """
    u = (units or "").strip()
    p = (param or "")
    is_anom = p.endswith("_anom") or p.endswith("_anomaly")

    if u in ("K", "kelvin"):
        if is_anom:
            return {"value": value, "units": "°C", "method": "delta_identity"}
        return {"value": None if value is None else round(value - 273.15, 3),
                "units": "°C", "method": "kelvin_to_celsius"}
    if u in ("Pa", "pascal"):
        return {"value": None if value is None else round(value / 100.0, 3),
                "units": "hPa", "method": "pa_to_hpa"}
    if u in ("m s-1", "m/s", "m s**-1"):
        return {"value": None if value is None else round(value * 2.2369362921, 3),
                "units": "mph", "method": "ms_to_mph"}
    if u in ("m",):
        return {"value": value, "units": "m", "method": "identity"}
    return {"value": value, "units": u or None, "method": "identity"}


# ═══════════════════════════════════════════════════════════════════════════
# The chain hook (charter §7) — the SHAPE on day one, the rungs as they light
# ═══════════════════════════════════════════════════════════════════════════

#: The four rungs a click must eventually be able to climb. v0 fills the first
#: from the sidecar read itself and states, per rung, WHY the rest are dark.
CHAIN_RUNGS = ["temperature", "degree_day", "load", "lmp"]


def nearest_station(lat: float, lon: float,
                    stations: list[dict]) -> Optional[dict]:
    """Nearest station in the ruled 17-station WECC basket. Pure — the basket
    is passed in (main.py already loads it once at import for the temp matrix),
    so this needs neither the file nor a database."""
    best = None
    for s in stations:
        slat, slon = s.get("lat"), s.get("lon")
        if slat is None or slon is None:
            continue
        d = haversine_km(lat, lon, float(slat), float(slon))
        if best is None or d < best["distance_km"]:
            best = {
                "station_id": s.get("station_id"),
                "icao": s.get("icao"),
                "name": s.get("display_name") or s.get("name"),
                "lat": float(slat), "lon": float(slon),
                "distance_km": round(d, 3),
            }
    return best


def chain_stub(lat: float, lon: float, param: Optional[str],
               value: Optional[float], units: Optional[str],
               stations: list[dict]) -> dict[str, Any]:
    """`?chain=1` — the rung ladder, v0.

    The point of this is the SHAPE, not the numbers: the Atlas panel binds to
    four rungs on day one and each lights up as its lane lands. A dark rung
    carries a `reason`, never an empty object, so the panel can say what is
    missing instead of rendering a blank row.
    """
    p = param or ""
    temp_like = p.startswith("t2m") or p.startswith("tmp") or p.startswith("t_")
    station = nearest_station(lat, lon, stations)

    rungs: dict[str, Any] = {
        "temperature": (
            {"available": True, "value": value, "units": units,
             "source": "sidecar", "param": param}
            if temp_like else
            {"available": False,
             "reason": f"param {param!r} is not a temperature rung"}
        ),
        "degree_day": {
            "available": False,
            "reason": ("v0 returns the nearest station identifier only; the "
                       "HDD/CDD join against /api/weather/dd/* is not built"),
            "station": station,
        },
        "load": {
            "available": False,
            "reason": "no load rung is built for this lane yet",
        },
        "lmp": {
            "available": False,
            "reason": ("v0 returns no pnode; nearest-pnode needs the nodal "
                       "Atlas geo join, which is not wired into this route"),
            "pnode_id": None,
        },
    }
    return {"rungs": CHAIN_RUNGS, "filled": ["temperature"] if temp_like else [],
            "chain": rungs}


# ═══════════════════════════════════════════════════════════════════════════
# The store — the only thing here that touches the network
# ═══════════════════════════════════════════════════════════════════════════

class _LRU(OrderedDict):
    """Smallest honest LRU. Holds headers (~1 KB dicts) and single floats;
    never an array — see the module rule."""

    def __init__(self, maxsize: int):
        super().__init__()
        self.maxsize = maxsize

    def get_(self, key):
        if key in self:
            self.move_to_end(key)
            return self[key]
        return None

    def put(self, key, value):
        self[key] = value
        self.move_to_end(key)
        while len(self) > self.maxsize:
            self.popitem(last=False)


def sigv4_headers(*, method: str, host: str, path: str, region: str,
                  service: str, access_key: str, secret_key: str,
                  now: Optional[datetime] = None) -> dict[str, str]:
    """AWS SigV4 for a GET with an empty body, in stdlib `hmac`/`hashlib`.

    WHY THIS IS HERE AND NOT boto3. The Model Room already carries boto3 for its
    list/get surface, and this lane could have reached for it. It does not,
    because a signed empty-body GET is forty lines and pulling a 15 MB SDK into
    the click path to make one four-byte request is the wrong trade. `Range` is
    deliberately NOT in SignedHeaders: SigV4 requires every `x-amz-*` header to
    be signed and permits any other to be omitted, and leaving it out means the
    signature does not have to be recomputed per forecast hour on a ladder.
    """
    now = now or datetime.now(UTC)
    amzdate = now.strftime("%Y%m%dT%H%M%SZ")
    datestamp = now.strftime("%Y%m%d")
    empty_hash = hashlib.sha256(b"").hexdigest()

    signed = "host;x-amz-content-sha256;x-amz-date"
    canonical = "\n".join([
        method,
        path,
        "",                                   # no query string on these GETs
        f"host:{host}",
        f"x-amz-content-sha256:{empty_hash}",
        f"x-amz-date:{amzdate}",
        "",
        signed,
        empty_hash,
    ])
    scope = f"{datestamp}/{region}/{service}/aws4_request"
    to_sign = "\n".join([
        "AWS4-HMAC-SHA256", amzdate, scope,
        hashlib.sha256(canonical.encode()).hexdigest(),
    ])

    def _sign(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode(), hashlib.sha256).digest()

    k = _sign(f"AWS4{secret_key}".encode(), datestamp)
    k = _sign(k, region)
    k = _sign(k, service)
    k = _sign(k, "aws4_request")
    signature = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()

    return {
        "x-amz-date": amzdate,
        "x-amz-content-sha256": empty_hash,
        "Authorization": (
            f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
            f"SignedHeaders={signed}, Signature={signature}"
        ),
    }


class SidecarStore:
    """Reads sidecar headers and four-byte value slices.

    TWO TRANSPORTS, ONE SHAPE.
      * `base_url` — a public/CDN base under which the `weather/values/...`
        keys resolve directly. Plain GET, no signing.
      * R2 credentials — a path-style, SigV4-signed GET against the archive
        bucket, reusing the SAME read-only token the Model Room already has.
    Whichever is configured, the caller sees `get_header` / `get_value`.

    Tests never construct either: they pass a `transport` callable and the
    network is not reachable from the test suite at all.
    """

    def __init__(self, *, base_url: str = "", endpoint: str = "",
                 bucket: str = "", access_key: str = "", secret_key: str = "",
                 region: str = "auto", timeout: float = 10.0,
                 transport=None):
        self.base_url = (base_url or "").rstrip("/")
        self.endpoint = (endpoint or "").rstrip("/")
        self.bucket = bucket
        self.access_key = access_key
        self.secret_key = secret_key
        self.region = region
        self.timeout = timeout
        self._transport = transport
        self._client = None
        self._headers = _LRU(HEADER_CACHE_MAX)
        self._values = _LRU(VALUE_CACHE_MAX)

    # ── the connection pool ─────────────────────────────────────────────────

    def _get_client(self):
        """ONE `httpx.AsyncClient` for the process, not one per read.

        MEASURED, AND THE REASON THIS EXISTS. A 41-frame ladder against a
        local origin took 2.39 s when each range GET built its own client — 41
        fresh connections for 164 bytes of payload. Against R2 each of those is
        also a TLS handshake. Sharing one pooled client took the same ladder to
        68 ms cold and 3 ms warm. The four-byte read is the cheap part; the
        connection was the whole cost.

        No lock: there is no `await` between the emptiness check and the
        assignment, so within one event loop this cannot race. `is_closed` is
        checked so a client stranded by a closed loop is rebuilt rather than
        raising on every subsequent read.
        """
        import httpx  # local: the rest of the API must not need it at import

        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                limits=httpx.Limits(
                    max_connections=LADDER_CONCURRENCY * 2,
                    max_keepalive_connections=LADDER_CONCURRENCY,
                ),
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    # ── configuration ───────────────────────────────────────────────────────

    def configured(self) -> bool:
        if self._transport is not None:
            return True
        if self.base_url:
            return True
        return bool(self.endpoint and self.bucket
                    and self.access_key and self.secret_key)

    def mode(self) -> str:
        if self._transport is not None:
            return "injected"
        if self.base_url:
            return "public_base_url"
        return "r2_sigv4"

    def url_for(self, key: str) -> str:
        if self.base_url:
            return f"{self.base_url}/{key}"
        return f"{self.endpoint}/{self.bucket}/{key}"

    # ── the one network call ────────────────────────────────────────────────

    async def _fetch(self, key: str,
                     byte_range: Optional[str]) -> tuple[int, bytes]:
        """(status, body). A 404/403 is handed back for the caller to shape into
        a fail-loud body; anything else that is not 200/206 raises."""
        if self._transport is not None:
            return await self._transport(key, byte_range)

        url = self.url_for(key)
        headers: dict[str, str] = {}
        if byte_range:
            headers["Range"] = byte_range
        if not self.base_url:
            from urllib.parse import urlsplit, quote
            parts = urlsplit(url)
            headers.update(sigv4_headers(
                method="GET", host=parts.netloc,
                path=quote(parts.path, safe="/-_.~"),
                region=self.region, service="s3",
                access_key=self.access_key, secret_key=self.secret_key,
            ))
        resp = await self._get_client().get(url, headers=headers)
        if resp.status_code in (200, 206):
            return resp.status_code, resp.content
        if resp.status_code in (403, 404):
            return resp.status_code, b""
        raise PointError(502, {
            "error": "sidecar storage error",
            "key": key,
            "status": resp.status_code,
        })

    # ── headers ─────────────────────────────────────────────────────────────

    async def get_header(self, key: str) -> SidecarHeader:
        """Cached forever by key: a frame's header is immutable once written,
        so there is no TTL to get wrong. A ladder pays for exactly one of these."""
        hit = self._headers.get_(key)
        if hit is not None:
            return hit
        status, body = await self._fetch(key, None)
        if status in (403, 404):
            raise PointError(404, {
                "error": "sidecar header not found",
                "key": key,
                "status": status,
            })
        try:
            doc = _json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as e:
            raise PointError(502, {"error": f"sidecar header is not JSON: {e}",
                                   "key": key})
        hdr = SidecarHeader(doc, key=key)
        self._headers.put(key, hdr)
        return hdr

    async def exists(self, key: str) -> bool:
        """Presence probe for the render PNG on the absent-sidecar path. Costs
        one byte, not one object: a `bytes=0-0` range GET is a HEAD that works
        the same way through every CDN and signer in the path."""
        try:
            status, _ = await self._fetch(key, "bytes=0-0")
        except PointError:
            return False
        return status in (200, 206)

    # ── the four bytes ──────────────────────────────────────────────────────

    async def get_value(self, key: str, offset: int) -> Optional[float]:
        """ONE cell. Four bytes on the wire, always — the assertion the whole
        lane exists to keep."""
        cached = self._values.get_((key, offset))
        if cached is not None:
            return cached[0]
        status, raw = await self._fetch(key, range_header(offset))
        if status in (403, 404):
            raise PointError(404, {
                "error": "sidecar values not found",
                "key": key,
                "status": status,
            })
        if status == 200 and len(raw) != BYTES_PER_POINT:
            # A 200 to a Range request means the store ignored the range and
            # handed back the WHOLE array. Refusing it is the rule: honouring
            # it would put 517 KB in this process and make `bytes_read: 4` a
            # lie on the very response that reports it.
            raise PointError(502, {
                "error": "range read was not honoured",
                "key": key, "offset": offset,
                "expected_bytes": BYTES_PER_POINT, "got_bytes": len(raw),
                "hint": "storage returned 200 (full object) for a Range request",
            })
        value = decode_value(raw, key=key, offset=offset)
        self._values.put((key, offset), (value,))
        return value

    async def get_values(self, keys_offsets: list[tuple[str, int]],
                         *, concurrency: int = LADDER_CONCURRENCY
                         ) -> list[Any]:
        """The ladder fan-out: one range GET per forecast hour, at most
        `concurrency` in flight. Returns a value or the PointError raised for
        that rung, positionally — a ladder with one unwritten frame answers with
        the other forty rather than failing whole."""
        sem = asyncio.Semaphore(max(1, concurrency))

        async def one(key: str, offset: int):
            async with sem:
                try:
                    return await self.get_value(key, offset)
                except PointError as e:
                    return e

        return await asyncio.gather(*(one(k, o) for k, o in keys_offsets))
