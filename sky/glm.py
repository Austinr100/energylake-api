"""GLM L2 LCFA — key grammar, flash extraction, the rolling window, thinning,
and the GeoJSON + receipt-header contract.

Everything in this module is pure: no network, no database, no clock except
the one handed in. The reader that actually talks to NODD is
`sky/glm_reader.py`; the handler that serves the route is `sky/glm_route.py`.

────────────────────────────────────────────────────────────────────────────
WHAT THE WIRE SAYS — measured 2026-09-18 against `noaa-goes19`, not quoted
from a product description.

  prefix   GLM-L2-LCFA/{YYYY}/{DDD}/{HH}/
  object   OR_GLM-L2-LCFA_G19_s20262611400000_e20262611400200_c20262611400221.nc
  cadence  180 keys in one hour prefix -> exactly 20.00 s, 3 files/min
  bytes    min 218,503 / mean 250,365 / max 299,510 (one full hour, 180 keys)
  format   HDF5 magic `89 48 44 46 0d 0a 1a 0a` -> NetCDF-4, needs a real
           NetCDF-4/HDF5 reader; `netCDF4` 1.7.4 installs from a pip wheel
  content  210 flashes/file median over 45 consecutive files (14Z, 2026-261)

────────────────────────────────────────────────────────────────────────────
THREE THINGS THE FILE DOES THAT A READER WRITTEN FROM THE DOCS GETS WRONG.
All three were measured across three files spanning one hour before a line
of this module was written.

1. **The time epoch is PER FILE, not per hour.**
   `flash_time_offset_of_first_event.units` reads
   `seconds since 2026-09-18 14:00:00.000` in the 14:00:00 file and
   `seconds since 2026-09-18 14:13:20.000` in the 14:13:20 file. Anchoring
   the offsets on the hour — the obvious reading of a `{HH}/` prefix —
   misplaces every flash in the hour by up to 59 minutes. `read_flashes`
   parses the units string of the variable it is reading and never derives
   the epoch from the key.

2. **Offsets go NEGATIVE and run past the nominal 20 s span.**
   Measured range -1.488 .. +19.320 s against a file whose declared
   `time_coverage_start/end` is exactly 20 s wide. A flash's own time can
   therefore precede its file's start. Nothing clamps: the flash's time is
   the flash's time, and the window arithmetic uses it as-is.

3. **`flash_quality_flag` is not all zero.** `flag_values [0 1 3 5]`,
   `flag_meanings good_quality_qf degraded_due_to_*`. Five of 210 in the
   sample file carry flag 3. These are real detections that a constituent
   check downgraded, not fill rows — so **nothing is filtered on quality**.
   Silently dropping them would be an editorial choice hidden in a parser.
   The count is reported in `read_flashes`'s companion stats instead.

────────────────────────────────────────────────────────────────────────────
RULING S-5 — pass-through, never a bank. `FlashWindow` is a bounded deque
over the last `WINDOW_MAX_MINUTES`. It forgets. There is no write path in
this package at all, and `tests/test_sky_glm.py` asserts that by reading the
sources.
"""

from __future__ import annotations

import json
import re
import threading
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Iterable, Mapping, NamedTuple, Sequence

# ── The estate's two GOES birds, and their NODD buckets ────────────────────
GLM_SATELLITES = {
    "goes19": "noaa-goes19",   # GOES-East
    "goes18": "noaa-goes18",   # GOES-West
}

#: The satellite short code as it appears in the object name (`_G19_`).
_SAT_TOKEN = {"goes19": "G19", "goes18": "G18"}

#: The rolling window's depth. The route may ask for 1..15 minutes; the
#: reader retains exactly this much and drops the rest on every tick.
WINDOW_MAX_MINUTES = 15

#: The route thins to at most this many points, highest energy first.
THIN_CAP = 5_000

#: Nominal file span, used only to decide which files a window "drew on".
#: Never used to place a flash in time — see note 1 in the module docstring.
FILE_SPAN_SECONDS = 20.0

# ── netCDF4 IS NOT THREAD-SAFE, AND THIS SERVICE PARSES FROM TWO THREADS ───
# MEASURED, 2026-09-18, lane d091448m, on the first real boot of the mounted
# route: one reader thread per satellite, both calling `read_flashes` on the
# wire's 20-s cadence, and the uvicorn process died with SIGSEGV within the
# first tick. The traceback caught on the way down, from the goes18 thread:
#
#     File "sky/glm.py", line 223, in read_flashes
#       ds = netCDF4.Dataset("glm-inmemory", "r", memory=blob)
#     RuntimeError: NetCDF: Can't open HDF5 attribute
#
# Reduced to a deterministic reproduction against one real 412,164 B GOES-19
# object, netCDF4 1.7.4 / numpy 2.4.6 / Python 3.11:
#
#     80 parses, one at a time    ->  0 errors, exit 0
#     2 threads x 40 parses       ->  SIGSEGV (exit 139)
#
# The wheel's bundled HDF5 is not built `--enable-threadsafe`, so concurrent
# `Dataset` access corrupts library state. This is NOT a defect in the
# pantry's parser — `tests/test_sky_glm.py` parses single-threaded and is
# right to pass. It is a property of running TWO readers in ONE process,
# which is what mounting the route here does, so the lock lives here.
#
# THE COST, stated rather than waved at: parse is 11.2 ms/file (measured,
# price receipt §2) and both birds together publish 6 files/min, so this
# serialises ~67 ms of work per minute. Contention is effectively nil, and
# the GIL already serialises the surrounding Python anyway. A lock is the
# cheap, total fix; the alternatives (a parse subprocess, or one process per
# satellite) buy nothing here and cost a great deal.
#
# It is a plain `Lock`, not an `RLock`: `read_flashes` must never re-enter
# itself, and if it ever did, a deadlock that shows up in testing is a better
# outcome than a segfault that shows up in production.
_NETCDF4_LOCK = threading.Lock()

_KEY_RE = re.compile(
    r"^(?:.*/)?OR_GLM-L2-LCFA_(?P<sat>G\d{2})"
    r"_s(?P<start>\d{14})"
    r"_e(?P<end>\d{14})"
    r"_c(?P<created>\d{14})\.nc$"
)

_UNITS_RE = re.compile(
    r"^seconds\s+since\s+"
    r"(?P<y>\d{4})-(?P<mo>\d{2})-(?P<d>\d{2})"
    r"[ T](?P<h>\d{2}):(?P<mi>\d{2}):(?P<s>\d{2})(?:\.(?P<frac>\d+))?"
    r"\s*(?:Z|UTC)?$"
)


class GLMParseError(ValueError):
    """A GLM object or key could not be read.

    Raised loudly rather than returning an empty list (principle #27). The
    reader catches it per file, logs the key, and **never retries that key**
    — a file that is malformed now is malformed forever, and a tight retry
    loop against NODD is the failure this exception exists to prevent.
    """


class GLMKey(NamedTuple):
    """The five facts the object name carries, and nothing inferred."""
    key: str
    sat: str            # 'goes19' | 'goes18'
    start: datetime     # scan start, UTC
    end: datetime       # scan end, UTC
    created: datetime   # publish stamp, UTC


class Flash(NamedTuple):
    """One GLM flash. One point, not one event and not one group.

    Measured resident cost: **168 B per flash** as a `NamedTuple` of five
    floats (`tracemalloc`, 9,456 flashes). See the price table in
    `docs/receipts/sky-glm/price_2026_09_18.md` for why this shape was kept
    over a numpy column store that costs 24 B/flash.
    """
    lat: float          # degrees_north
    lon: float          # degrees_east
    t: datetime         # the flash's OWN time (first constituent event), UTC
    energy_j: float     # joules
    area_km2: float     # km^2


# ───────────────────────────────────────────────────────────────────────────
# Key grammar
# ───────────────────────────────────────────────────────────────────────────

def hour_prefix(sat: str, when: datetime) -> str:
    """The NODD prefix for `when`'s hour. `GLM-L2-LCFA/{YYYY}/{DDD}/{HH}/`.

    `sat` is validated because a typo would otherwise list a prefix that
    exists under the wrong bucket and read plausibly-shaped wrong data.
    """
    if sat not in GLM_SATELLITES:
        raise GLMParseError(
            f"unknown satellite {sat!r}; known: {sorted(GLM_SATELLITES)}")
    when = _as_utc(when)
    return (f"GLM-L2-LCFA/{when.year:04d}/"
            f"{when.timetuple().tm_yday:03d}/{when.hour:02d}/")


def parse_glm_key(key: str) -> GLMKey:
    """Parse an object key into its five stamps.

    The `s`/`e`/`c` stamps are `YYYYDDDHHMMSSt` — year, day-of-year, hour,
    minute, second, and one digit of TENTHS. The trailing tenths digit is
    the one that bites: read as `YYYYDDDHHMMSS` plus a stray character, a
    naive parse silently yields a time 10x wrong in the seconds place.
    """
    m = _KEY_RE.match(key)
    if not m:
        raise GLMParseError(f"not a GLM L2 LCFA object key: {key!r}")
    token = m.group("sat")
    sat = next((s for s, t in _SAT_TOKEN.items() if t == token), None)
    if sat is None:
        raise GLMParseError(f"unknown satellite token {token!r} in {key!r}")
    return GLMKey(
        key=key,
        sat=sat,
        start=_stamp_to_dt(m.group("start")),
        end=_stamp_to_dt(m.group("end")),
        created=_stamp_to_dt(m.group("created")),
    )


def _stamp_to_dt(stamp: str) -> datetime:
    """`20262611400000` -> 2026-09-18 14:00:00.0 UTC (day-of-year 261)."""
    year = int(stamp[0:4])
    doy = int(stamp[4:7])
    hour, minute, sec = int(stamp[7:9]), int(stamp[9:11]), int(stamp[11:13])
    tenths = int(stamp[13])
    if not 1 <= doy <= 366:
        raise GLMParseError(f"day-of-year {doy} out of range in stamp {stamp!r}")
    base = datetime(year, 1, 1, tzinfo=timezone.utc) + timedelta(days=doy - 1)
    return base + timedelta(hours=hour, minutes=minute,
                            seconds=sec, milliseconds=100 * tenths)


# ───────────────────────────────────────────────────────────────────────────
# Reading one file
# ───────────────────────────────────────────────────────────────────────────

def read_flashes(blob: bytes, key: str | None = None) -> list[Flash]:
    """Extract the flash table from one GLM L2 LCFA object.

    `blob` is the raw NetCDF-4 bytes; nothing is written to disk — netCDF4
    opens it from memory. Returns one `Flash` per row of
    `number_of_flashes`, in file order.

    Raises `GLMParseError` on anything that is not a readable GLM file:
    truncated bytes, a missing flash table, a units string this module does
    not understand. It never returns `[]` to mean "could not read" — an
    empty list means the file genuinely held no flashes, which is the normal
    state of a quiet 20 seconds over the Pacific.
    """
    # Lazy, matching the house idiom for heavy geo deps (`d2/synoptic.py`
    # imports xarray inside its functions): this module's window and
    # thinning arithmetic is importable and testable without netCDF4.
    try:
        import netCDF4  # noqa: WPS433
        import numpy as np
    except ImportError as exc:  # pragma: no cover - environment defect
        raise GLMParseError(
            "netCDF4/numpy are required to read GLM objects; both are in "
            "requirements.txt and netCDF4 1.7.4 installs from a pip wheel "
            f"(no conda, no system HDF5). Import failed: {exc}") from exc

    if not blob:
        raise GLMParseError(f"empty body for {key or '<unnamed>'}")
    if blob[:8] != b"\x89HDF\r\n\x1a\n":
        raise GLMParseError(
            f"{key or '<unnamed>'}: not HDF5 (first 8 bytes {blob[:8]!r}); "
            "a NetCDF-3 or an S3 error document would look like this")

    # The lock spans OPEN through CLOSE, not just the open: every variable
    # read below is an HDF5 call into the same unsafe library state.
    with _NETCDF4_LOCK:
        return _read_flashes_locked(netCDF4, np, blob, key)


def _read_flashes_locked(netCDF4, np, blob: bytes, key: str | None) -> list[Flash]:
    """The body of `read_flashes`, called only with `_NETCDF4_LOCK` held."""
    try:
        ds = netCDF4.Dataset("glm-inmemory", "r", memory=blob)
    except (OSError, RuntimeError) as exc:
        # RuntimeError joins OSError because netCDF4 raises its own library
        # errors as RuntimeError ("NetCDF: Can't open HDF5 attribute"), and
        # `read_flashes` promises GLMParseError for anything unreadable. A
        # RuntimeError escaping instead would skip the reader's per-file
        # handler, so the key would never reach `_failed` and would be
        # re-fetched every 20 s forever — the tight retry loop the spec
        # forbids, arrived at by way of an exception type.
        raise GLMParseError(f"{key or '<unnamed>'}: netCDF4 refused it: {exc}") from exc

    try:
        if "number_of_flashes" not in ds.dimensions:
            raise GLMParseError(
                f"{key or '<unnamed>'}: no `number_of_flashes` dimension — "
                "this is not an L2 LCFA product")
        n = len(ds.dimensions["number_of_flashes"])
        if n == 0:
            return []

        missing = [v for v in ("flash_lat", "flash_lon", "flash_energy",
                               "flash_area", "flash_time_offset_of_first_event")
                   if v not in ds.variables]
        if missing:
            raise GLMParseError(
                f"{key or '<unnamed>'}: flash table incomplete, missing {missing}")

        tvar = ds.variables["flash_time_offset_of_first_event"]
        epoch = _parse_units_epoch(getattr(tvar, "units", ""), key)

        # `[:]` applies scale_factor/add_offset and returns a masked array.
        # filled(nan) makes the fill rows non-finite so the one finite-check
        # below drops them, rather than each column inventing its own rule.
        lat = _col(ds.variables["flash_lat"], np)
        lon = _col(ds.variables["flash_lon"], np)
        energy = _col(ds.variables["flash_energy"], np)
        area = _col(ds.variables["flash_area"], np)
        offs = _col(tvar, np)
    except RuntimeError as exc:
        raise GLMParseError(
            f"{key or '<unnamed>'}: netCDF4 failed mid-read: {exc}") from exc
    finally:
        ds.close()

    good = (np.isfinite(lat) & np.isfinite(lon)
            & np.isfinite(energy) & np.isfinite(area) & np.isfinite(offs))

    out: list[Flash] = []
    for la, lo, en, ar, off in zip(lat[good], lon[good], energy[good],
                                   area[good], offs[good]):
        out.append(Flash(
            lat=round(float(la), 4),
            lon=round(float(lo), 4),
            t=epoch + timedelta(seconds=float(off)),
            # SIX significant figures, and that is MORE precision than the
            # wire carries, not less. `flash_energy` is stored int16 with
            # scale_factor 9.999959802209943e-16 — at most 65,536 distinct
            # values, about five significant figures. A float64 round-trip
            # prints seventeen (`1.4028458028472052e-13`), so eleven of those
            # digits are quantisation noise rendered as fact, and they cost
            # ~11 bytes on every one of up to 5,000 features per response.
            energy_j=_sig(float(en), 6),
            # `flash_area` is declared in m2; the wire carries a scale factor
            # of 152,601.859375 m2 per count, so a single flash is hundreds
            # of km2. Converted here so nothing downstream guesses the unit.
            area_km2=round(float(ar) / 1.0e6, 3),
        ))
    return out


def _col(var, np):
    """One variable as a float64 array with fills turned into NaN."""
    arr = var[:]
    if hasattr(arr, "filled"):
        arr = arr.filled(np.nan)
    return np.asarray(arr, dtype="float64")


def _parse_units_epoch(units: str, key: str | None) -> datetime:
    """`seconds since 2026-09-18 14:13:20.000` -> that datetime, UTC.

    Deliberately strict. A units string this module cannot parse raises
    rather than falling back to the key's `s` stamp — because the fallback
    would be right most of the time (the epoch usually EQUALS the scan
    start) and wrong silently the moment NOAA changes it. A guard that is
    almost always unnecessary is exactly the guard that must not guess.
    """
    m = _UNITS_RE.match((units or "").strip())
    if not m:
        raise GLMParseError(
            f"{key or '<unnamed>'}: cannot read time units {units!r}; "
            "expected `seconds since YYYY-MM-DD HH:MM:SS[.fff]`")
    frac = m.group("frac") or ""
    micro = int((frac + "000000")[:6]) if frac else 0
    return datetime(int(m.group("y")), int(m.group("mo")), int(m.group("d")),
                    int(m.group("h")), int(m.group("mi")), int(m.group("s")),
                    micro, tzinfo=timezone.utc)


# ───────────────────────────────────────────────────────────────────────────
# The rolling window
# ───────────────────────────────────────────────────────────────────────────

class FlashWindow:
    """The last `retain_minutes` of flashes, and the files they came from.

    Two ledgers, deliberately separate:

      * `_flashes` — one entry per flash, kept in arrival order.
      * `_files`   — one entry per file successfully parsed, kept whether or
        not it yielded a flash.

    They are separate because `X-GLM-Files` must answer *"how many 20-s
    files did this window draw on"*, and counting distinct keys among the
    flashes answers a different question — it would silently report a
    lightning-free window as `0 files` when the reader had in fact read
    fifteen of them cleanly. That is the difference between "nothing
    happened" and "nothing was read", and the receipt exists to tell them
    apart.

    Not thread-safe by itself. `sky/glm_reader.py` owns the lock.
    """

    def __init__(self, sat: str, retain_minutes: int = WINDOW_MAX_MINUTES):
        if sat not in GLM_SATELLITES:
            raise GLMParseError(f"unknown satellite {sat!r}")
        if retain_minutes < 1:
            raise ValueError("retain_minutes must be >= 1")
        self.sat = sat
        self.retain = timedelta(minutes=retain_minutes)
        self._flashes: deque[Flash] = deque()
        self._files: deque[tuple[datetime, datetime, str]] = deque()
        self._keys_seen: set[str] = set()

    # -- writing ------------------------------------------------------------
    def add_file(self, info: GLMKey, flashes: Sequence[Flash]) -> int:
        """Record one parsed file. Returns the number of flashes taken.

        Idempotent per key: re-offering a key already in the window is a
        no-op, so a listing that repeats (the common case — the reader lists
        the same prefix every 20 s) cannot double-count.
        """
        if info.sat != self.sat:
            raise GLMParseError(
                f"{info.key}: satellite {info.sat} offered to a {self.sat} window")
        if info.key in self._keys_seen:
            return 0
        self._keys_seen.add(info.key)
        self._files.append((info.start, info.end, info.key))
        self._flashes.extend(flashes)
        return len(flashes)

    def prune(self, now: datetime) -> int:
        """Forget everything older than the retention. Returns rows dropped.

        This is the whole of ruling S-5's "never a bank": the only thing
        standing between a proxy and an accidental archive is that this runs
        on every tick. It is called from `GLMReader.tick`, and a test asserts
        the window's length stops growing across a simulated hour.
        """
        cutoff = _as_utc(now) - self.retain
        dropped = 0
        # Flash times are near-sorted but NOT sorted: offsets run -1.5..+19.3 s
        # (measured), so a late file can carry a flash older than an earlier
        # file's newest. A left-popping deque would therefore strand rows.
        if self._flashes and any(f.t < cutoff for f in self._flashes):
            kept = [f for f in self._flashes if f.t >= cutoff]
            dropped = len(self._flashes) - len(kept)
            self._flashes = deque(kept)
        while self._files and self._files[0][1] < cutoff:
            _, _, key = self._files.popleft()
            self._keys_seen.discard(key)
        return dropped

    # -- reading ------------------------------------------------------------
    def select(self, now: datetime, minutes: int) -> list[Flash]:
        """Flashes whose OWN time falls in `[now - minutes, now]`.

        The upper bound is not decoration. Publish latency is +16..27 s
        (measured), so `now` is always ahead of the newest flash — but a
        clock skew or a bad units string could put a flash in the future,
        and a point drawn ahead of the wall clock is a receipt that lies.
        """
        now = _as_utc(now)
        lo = now - timedelta(minutes=minutes)
        return [f for f in self._flashes if lo <= f.t <= now]

    def files_in_window(self, now: datetime, minutes: int) -> int:
        """How many parsed files overlap `[now - minutes, now]`.

        The lower bound is STRICT (`end > lo`), and that is the boundary the
        first draft got wrong. Files land on exact 20-s marks, so at
        `minutes=5` there is always one whose `end` equals `lo` precisely.
        Counting it made `X-GLM-Files` report 16 where the flash selection
        had drawn on 15 — a receipt off by one against the payload beside
        it, every single request, forever. A file that touches the window
        only at its closing instant contributed nothing to it.
        """
        now = _as_utc(now)
        lo = now - timedelta(minutes=minutes)
        return sum(1 for fs, fe, _ in self._files if fe > lo and fs <= now)

    def newest(self) -> datetime | None:
        """The newest flash's own time, or None when the window is empty."""
        return max((f.t for f in self._flashes), default=None)

    def __len__(self) -> int:
        return len(self._flashes)

    @property
    def file_count(self) -> int:
        return len(self._files)


# ───────────────────────────────────────────────────────────────────────────
# The bounding box — filtered BEFORE thinning, and that order is the point
# ───────────────────────────────────────────────────────────────────────────

class BBox(NamedTuple):
    """A geographic filter, `w,s,e,n` in degrees.

    Added by lane d091448m when the route was mounted in `energylake-api`.
    The price receipt handed the mount lane a number, not a worry: at the
    convective rate measured on 2026-09-18 a `minutes=5` body is **840,307 B**
    and `minutes=15` costs the same, because both saturate the 5,000-point
    cap. A bbox is the one lever of the three offered (poll slower, lower the
    cap, filter by area) that makes the body smaller *without* making the
    layer less true: the West gets the West, at full density, instead of
    every point in the hemisphere thinned until the West is sparse.

    **`w > e` means the box crosses the antimeridian, and that is a real
    case here, not a defensive flourish.** GOES-18 is GOES-West; its field
    of view runs from roughly 180°W east to 85°W *and* across 180° into the
    eastern hemisphere. A Pacific box is therefore genuinely written
    `bbox=170,0,-150,30`, and reading that as an empty (or inverted) box
    would silently drop every flash in it.
    """
    w: float
    s: float
    e: float
    n: float

    @property
    def crosses_antimeridian(self) -> bool:
        return self.w > self.e

    def contains(self, lat: float, lon: float) -> bool:
        """Inclusive on every edge.

        Inclusive rather than half-open because a flash sitting exactly on a
        round-numbered boundary is the case a hand-typed bbox produces, and
        dropping it would look like the proxy losing points at the edge of
        the view.
        """
        if not (self.s <= lat <= self.n):
            return False
        if self.crosses_antimeridian:
            return lon >= self.w or lon <= self.e
        return self.w <= lon <= self.e


def parse_bbox(raw: str | None) -> BBox | None:
    """`"w,s,e,n"` -> `BBox`, or None when the parameter is absent.

    Refuses rather than repairs, for the same reason `minutes` refuses
    rather than clamps: a bbox quietly "fixed" into a different box than the
    one asked for produces a layer that does not match the map it is drawn
    on, and `X-GLM-Bbox` would stamp the repaired box on it as if it had
    been requested.
    """
    if raw is None or not raw.strip():
        return None
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) != 4:
        raise GLMParseError(
            f"bbox must be four comma-separated numbers `w,s,e,n`, "
            f"got {len(parts)} in {raw!r}")
    try:
        w, s, e, n = (float(p) for p in parts)
    except ValueError:
        raise GLMParseError(
            f"bbox must be four numbers `w,s,e,n`, got {raw!r}") from None

    for name, v in (("w", w), ("s", s), ("e", e), ("n", n)):
        if v != v or v in (float("inf"), float("-inf")):
            raise GLMParseError(f"bbox {name} is not a finite number: {raw!r}")
    for name, v in (("s", s), ("n", n)):
        if not -90.0 <= v <= 90.0:
            raise GLMParseError(f"bbox {name}={v} out of range -90..90")
    for name, v in (("w", w), ("e", e)):
        if not -180.0 <= v <= 180.0:
            raise GLMParseError(f"bbox {name}={v} out of range -180..180")
    if s >= n:
        raise GLMParseError(
            f"bbox south={s} must be below north={n} (order is w,s,e,n)")
    if w == e:
        # Ambiguous by construction: with the antimeridian rule above, `w == e`
        # could mean a zero-width sliver or the whole globe, and guessing
        # either way is how a layer silently goes blank. Refuse and say so.
        raise GLMParseError(
            f"bbox west equals east ({w}); that is either a zero-width box or "
            "the whole globe and this route will not guess which")
    return BBox(w, s, e, n)


def filter_by_bbox(flashes: Sequence[Flash], bbox: BBox | None) -> list[Flash]:
    """Flashes inside `bbox`. The identity when `bbox` is None.

    **This runs BEFORE `thin_by_energy`, and the order is the whole value of
    the parameter.** Thinning first and filtering second would pick the 5,000
    most energetic flashes in the hemisphere and *then* discard the ones
    outside the view — so a quiet West behind an active Midwest would come
    back nearly empty while the window held plenty of Western flashes. Filter
    first and the cap is spent entirely on the area that was asked for.
    """
    if bbox is None:
        return list(flashes)
    return [f for f in flashes if bbox.contains(f.lat, f.lon)]


def format_bbox(bbox: BBox | None) -> str:
    """The bbox as the receipt header states it back, or `-`."""
    if bbox is None:
        return "-"
    return ",".join(_trim(v) for v in bbox)


def _trim(v: float) -> str:
    """`-125.0` -> `-125`; keeps the echoed bbox readable beside the request."""
    return f"{v:g}"


# ───────────────────────────────────────────────────────────────────────────
# Thinning, GeoJSON, headers
# ───────────────────────────────────────────────────────────────────────────

def thin_by_energy(flashes: Sequence[Flash], cap: int = THIN_CAP) -> list[Flash]:
    """The `cap` most energetic flashes, newest-first within a tie.

    Under the cap this is the identity — the same list object's contents in
    the same order — so the common case pays nothing. Over it, the sort key
    is total: `(-energy, -timestamp, lat, lon)`. Totality matters because
    two requests one second apart against an unchanged window must return
    the SAME 5,000 points; a partial key would let a stable sort reorder ties
    with insertion order and make points flicker in and out of the layer.
    """
    if cap < 1:
        raise ValueError("cap must be >= 1")
    if len(flashes) <= cap:
        return list(flashes)
    ordered = sorted(
        flashes,
        key=lambda f: (-f.energy_j, -f.t.timestamp(), f.lat, f.lon),
    )
    return ordered[:cap]


def flashes_to_geojson(flashes: Iterable[Flash], sat: str) -> dict:
    """A GeoJSON `FeatureCollection` — one `Point` feature per flash.

    Coordinates are `[lon, lat]` (RFC 7946 order; the one that is backwards
    from how every other line in this file says it). `t` is RFC 3339 with a
    `Z`, from the flash's own time.
    """
    feats = []
    for f in flashes:
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [f.lon, f.lat]},
            "properties": {
                "t": _iso_z(f.t),
                "energy_j": f.energy_j,
                "area_km2": f.area_km2,
            },
        })
    return {
        "type": "FeatureCollection",
        "features": feats,
        # Attribution rides in the payload as well as the layer, because a
        # GeoJSON body gets saved and passed around without its page.
        "properties": {
            "source": "NOAA GOES GLM L2 LCFA",
            "satellite": sat,
            "attribution": "NOAA/NESDIS (public domain)",
        },
    }


#: The prefix every receipt header shares. It is the SOURCE of the exposed
#: list below, not a description of it — see `build_expose_headers`.
RECEIPT_HEADER_PREFIX = "X-GLM-"


def build_expose_headers(headers: Mapping[str, str]) -> str:
    """`Access-Control-Expose-Headers` for `headers`, READ OFF `headers`.

    Only the CORS-safelisted response headers reach a cross-origin `fetch`.
    Everything else is on the wire and unreadable by the client at the same
    time — lane d091448b measured it on chromium-1194 across two real
    origins, identical bodies, the header the only difference: **0 of 6
    readable as production sends it, 6 of 6 with it.** Both arms are
    `res.ok` and deliver every byte, so nothing about the read notices.
    `curl` has no CORS and cannot see this difference at all, which is why
    the wire table taken before it was correct and useless for the question.

    The value is derived from the dict being sent rather than typed out, so
    a seventh `X-GLM-*` receipt header is exposed by the act of adding it.
    A hand-written second list of six names would be a second contract that
    drifts from the first while every test still passes — the same shape of
    defect as the one this function exists to fix, one layer up.
    """
    return ", ".join(
        name for name in headers
        if name.upper().startswith(RECEIPT_HEADER_PREFIX.upper())
    )


def build_receipt_headers(
    *,
    sat: str,
    minutes: int,
    newest: datetime | None,
    files: int,
    returned: int,
    available: int,
    bbox: BBox | None = None,
) -> dict[str, str]:
    """The response headers. Three from the spec, two more argued for.

    From the spec:
      `Access-Control-Allow-Origin: *` — the whole reason this proxy exists.
      `Cache-Control: public, max-age=10` — half the 20-s cadence.
      `X-GLM-Window`  the window asked for, as `5m`.
      `X-GLM-Newest`  the NEWEST FLASH'S OWN TIME, RFC 3339 Z, or `-`.
                      Never `now`, never the file's stamp — ruling S-3.
      `X-GLM-Files`   how many 20-s files the window drew on.

    Two beyond the spec, each because its absence would make the receipt
    lie — flagged in the handback rather than slipped in:

      `X-GLM-Thinned` `<returned>/<available>`. Thinning silently discarding
                      4,456 of 9,456 flashes while the layer reads "9,456
                      flashes" is precisely the quiet-untruth the S-3 ruling
                      is about. The dashboard can now say `5,000 of 9,456`.
      `X-GLM-Sat`     which bird answered. `Cache-Control: public` plus a
                      satellite toggle means a shared cache can hand a
                      GOES-18 body to a GOES-19 request; without this header
                      nothing downstream could tell.

    One more, added with `?bbox=` by lane d091448m:

      `X-GLM-Bbox`    the box as applied, `w,s,e,n`, or `-` when none was
                      asked for. It is on the response for the same reason
                      `X-GLM-Sat` is: `Cache-Control: public` plus a
                      *second* varying parameter means a shared cache can
                      hand a CONUS body to a Pacific request, and without
                      this nothing downstream could tell.

    Two CORS headers ride along, added by lane d091453, and neither is a
    receipt — they are what makes the receipts above legible to a browser:

      `Access-Control-Expose-Headers`
                      the `X-GLM-*` names in this very dict, derived by
                      `build_expose_headers`. Without it the browser hides
                      all six from the page that asked for them.
      `Timing-Allow-Origin: *`
                      lets the page read its own Resource Timing entry for
                      this response — transferred bytes and phase timings,
                      which currently report 0. Safe HERE for a reason that
                      does not generalise: this route is unauthenticated,
                      public-domain NOAA data already served `ACAO: *`, so
                      the body is world-readable and its size and timing
                      disclose nothing the body does not. It belongs on
                      `/sky/*` only — NOT on `/api/*`, which is credentialed.

    **`X-GLM-Thinned` counts AFTER the bbox**, deliberately. It reports the
    decision thinning actually made — `returned` chosen out of the
    `available` that reached it — so with a bbox that cuts 10,765 window
    flashes to 3,201, the header reads `3201/3201` and the honest caption is
    "3,201 flashes", not "3,201 of 10,765". The window held more; the view
    did not, and the caption describes the view. `X-GLM-Bbox` beside it is
    what says a filter was applied at all.
    """
    headers = {
        "Content-Type": "application/geo+json",
        "Access-Control-Allow-Origin": "*",
        "Cache-Control": "public, max-age=10",
        "Timing-Allow-Origin": "*",
        "X-GLM-Window": f"{minutes}m",
        "X-GLM-Newest": _iso_z(newest) if newest else "-",
        "X-GLM-Files": str(files),
        "X-GLM-Thinned": f"{returned}/{available}",
        "X-GLM-Sat": sat,
        "X-GLM-Bbox": format_bbox(bbox),
    }
    # DERIVED FROM THE DICT ABOVE, one line after it and never a second list
    # of names. Add a receipt header to that literal and it is exposed; there
    # is no second place to remember.
    headers["Access-Control-Expose-Headers"] = build_expose_headers(headers)
    return headers


# ───────────────────────────────────────────────────────────────────────────
# Small helpers
# ───────────────────────────────────────────────────────────────────────────

def _sig(x: float, digits: int) -> float:
    """Round to `digits` significant figures. 0 and non-finite pass through."""
    if not x or not (x == x) or x in (float("inf"), float("-inf")):
        return x
    from math import floor, log10
    return round(x, -int(floor(log10(abs(x)))) + (digits - 1))


def _as_utc(when: datetime) -> datetime:
    """Reject naive datetimes rather than assuming they mean UTC.

    CLAUDE.md's standing trap: CAISO timestamps misalign by 7-8 h when a
    naive local datetime is read as UTC. The same mistake here would place
    every flash in the wrong window, so the assumption is refused.
    """
    if when.tzinfo is None:
        raise GLMParseError(
            "naive datetime; GLM arithmetic is UTC-only and will not guess "
            "a zone (pass datetime.now(timezone.utc))")
    return when.astimezone(timezone.utc)


def _iso_z(when: datetime) -> str:
    """RFC 3339 with a literal `Z` and milliseconds."""
    return (_as_utc(when)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"))


def geojson_bytes(doc: dict) -> bytes:
    """The body as it goes on the wire — compact, no ASCII escaping."""
    return json.dumps(doc, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")
