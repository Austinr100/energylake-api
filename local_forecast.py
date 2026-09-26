"""
Local Weather lane A — `GET /api/local/forecast`, the pure half (d091477).

TWO ARMS, ONE SHAPE. Inside the US outline the forecast is NWS, read live
(`nws_arm.py`, D-09-25-03: read, never banked, the memo is the only cache).
Outside it, or when NWS fails (D-09-25-04), it is the GFS bank read in-process
(`model_arm.py`). Either way the response is `build_payload`'s shape, and
`receipts.arm` says which arm answered. The route (query validation, headers,
304) lives in main.py.

WHAT LIVES HERE. Arm selection (`select_arm`, ray casting over the bundled
outline — no shapely), the 12-word condition vocabulary and the NWS icon table
that feeds it, the 16-point wind table, the unit conversions both arms share,
the sun arithmetic, and `build_payload`, which fixes the key order and REFUSES
a null that carries no reason.

THE SUN IS COMPUTED HERE, NOT CALLED. The spec points at "sky/'s solar
arithmetic"; `sky/` holds the GLM lightning proxy and no solar code at all. The
NOAA solar-position equations below (Meeus-based, the ones the NOAA solar
calculator spreadsheet uses: ~1 min accuracy between ±72° latitude) are ~60
lines of `math` and replace that pointer — no new dependency either way.

ABSENCE IS STATED, NEVER FILLED. Every null in `now`, `hourly[]`, `daily[]` and
`sun` must have a matching `"field: reason"` entry in that block's `absent[]`.
`build_payload` raises if one does not — the page prints the reason on hover,
and a null with no reason is a dash nobody can explain.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

UTC = timezone.utc
log = logging.getLogger("energylake.local")

OUTLINE_PATH = Path(__file__).resolve().parent / "data" / "us_outline_20km.geojson"

ARM_NWS = "nws"
ARM_MODEL = "model"


def utcnow() -> datetime:
    """The lane's clock seam (tests monkeypatch it)."""
    return datetime.now(UTC)


def iso_z(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        raise ValueError("naive datetime reached the payload")
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: Optional[str]) -> Optional[datetime]:
    """NWS stamps carry a local offset (`2026-09-25T13:00:00-07:00`) or `+00:00`."""
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(UTC)


# ═══════════════════════════════════════════════════════════════════════════
# Arm selection — the outline and ray casting
# ═══════════════════════════════════════════════════════════════════════════

@lru_cache(maxsize=4)
def load_outline(path: str = str(OUTLINE_PATH)) -> tuple[tuple, str]:
    """(polygons, sha256). Each polygon is (bbox, [ring, ...]) with ring 0 the
    exterior and the rest holes; a ring is a tuple of (lon, lat)."""
    raw = Path(path).read_bytes()
    doc = json.loads(raw)
    polys = []
    for feat in doc["features"]:
        geom = feat["geometry"]
        coords = (geom["coordinates"] if geom["type"] == "MultiPolygon"
                  else [geom["coordinates"]])
        for poly in coords:
            rings = tuple(tuple((float(x), float(y)) for x, y in ring) for ring in poly)
            xs = [p[0] for p in rings[0]]
            ys = [p[1] for p in rings[0]]
            polys.append(((min(xs), min(ys), max(xs), max(ys)), rings))
    return tuple(polys), hashlib.sha256(raw).hexdigest()


def outline_sha(path: str = str(OUTLINE_PATH)) -> str:
    return load_outline(path)[1]


def _in_ring(lon: float, lat: float, ring) -> bool:
    """Even-odd ray cast, ray pointing east."""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > lat) != (yj > lat):
            x_cross = xi + (lat - yi) * (xj - xi) / (yj - yi)
            if lon < x_cross:
                inside = not inside
        j = i
    return inside


def in_outline(lat: float, lon: float, path: str = str(OUTLINE_PATH)) -> bool:
    polys, _ = load_outline(path)
    for (x0, y0, x1, y1), rings in polys:
        if not (x0 <= lon <= x1 and y0 <= lat <= y1):
            continue
        if _in_ring(lon, lat, rings[0]) and not any(_in_ring(lon, lat, h) for h in rings[1:]):
            return True
    return False


def select_arm(lat: float, lon: float) -> str:
    """Inside the 20 km-buffered US outline → `nws`; outside → `model`. The
    buffer is the ruling (§2.2): a point at sea off LA still gets NWS's marine
    grid, and NWS's own `points` 404 beyond its grid is D-09-25-04's to catch."""
    return ARM_NWS if in_outline(lat, lon) else ARM_MODEL


def nominal_tz(lon: float) -> str:
    """The longitude's nominal offset zone. POSIX sign: `Etc/GMT+8` is UTC−8."""
    off = int(round(lon / 15.0))
    off = max(-12, min(14, off))
    if off == 0:
        return "Etc/GMT"
    return f"Etc/GMT{'+' if off < 0 else '-'}{abs(off)}"


# ═══════════════════════════════════════════════════════════════════════════
# The vocabulary
# ═══════════════════════════════════════════════════════════════════════════

VOCAB = ("clear", "mostly-clear", "partly-cloudy", "cloudy", "fog", "drizzle",
         "rain", "heavy-rain", "snow", "sleet", "thunderstorm", "wind")
UNKNOWN = "unknown"

#: NWS icon path token → house word. Tokens from api.weather.gov/icons. NWS
#: icons carry no intensity, so nothing maps to `drizzle` or `heavy-rain`;
#: freezing rain and the mixed tokens land on `sleet` (the vocabulary's one
#: frozen-mix word). Deliberately NOT here, so they surface as `unknown` with
#: the token kept and logged: `smoke`, `dust`, `haze`, `hot`, `cold` — they
#: describe an obscuration or a temperature, not a sky, and picking a word for
#: them is a ruling this lane does not make.
NWS_ICON_TABLE: dict[str, str] = {
    "skc": "clear",
    "few": "mostly-clear",
    "sct": "partly-cloudy",
    "bkn": "cloudy",
    "ovc": "cloudy",
    "wind_skc": "wind",
    "wind_few": "wind",
    "wind_sct": "wind",
    "wind_bkn": "wind",
    "wind_ovc": "wind",
    "fog": "fog",
    "rain": "rain",
    "rain_showers": "rain",
    "rain_showers_hi": "rain",
    "snow": "snow",
    "blizzard": "snow",
    "sleet": "sleet",
    "rain_snow": "sleet",
    "rain_sleet": "sleet",
    "snow_sleet": "sleet",
    "fzra": "sleet",
    "rain_fzra": "sleet",
    "snow_fzra": "sleet",
    "tsra": "thunderstorm",
    "tsra_sct": "thunderstorm",
    "tsra_hi": "thunderstorm",
    "tornado": "thunderstorm",
    "hurricane": "wind",
    "tropical_storm": "wind",
}

#: The okta-class midpoints for NWS's cover tokens (FEW 1–2/8, SCT 3–4/8,
#: BKN 5–7/8, OVC 8/8). This is the only way `sky` is filled on the NWS arm:
#: from NWS's own cover class, never from a precip icon.
COVER_FRACTION: dict[str, float] = {
    "skc": 0.0, "clr": 0.0, "few": 0.1875, "sct": 0.4375, "bkn": 0.75, "ovc": 1.0,
}


def icon_token(icon_url: Optional[str]) -> Optional[str]:
    """`https://api.weather.gov/icons/land/day/tsra_hi,40/sct,20?size=medium`
    → `tsra_hi` (the first half-period's token, PoP suffix dropped)."""
    if not icon_url:
        return None
    segs = [s for s in urlsplit(icon_url).path.split("/") if s]
    for k, s in enumerate(segs):
        if s in ("day", "night") and k + 1 < len(segs):
            return segs[k + 1].split(",")[0]
    return segs[-1].split(",")[0] if segs else None


def condition_from_token(token: Optional[str]) -> str:
    if token is None:
        return UNKNOWN
    word = NWS_ICON_TABLE.get(token)
    if word is None:
        log.warning("[[LOCAL_UNKNOWN_ICON]] nws icon token %r is not in the table; "
                    "condition=unknown, condition_raw kept", token)
        return UNKNOWN
    return word


def condition_from_sky(sky: Optional[float]) -> str:
    """The model arm's four words, from cover alone (okta-class boundaries)."""
    if sky is None:
        return UNKNOWN
    if sky < 0.125:
        return "clear"
    if sky < 0.375:
        return "mostly-clear"
    if sky < 0.625:
        return "partly-cloudy"
    return "cloudy"


WIND_POINTS = ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
               "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")
WIND_TABLE: dict[str, float] = {p: i * 22.5 for i, p in enumerate(WIND_POINTS)}


def wind_deg(txt: Optional[str]) -> Optional[float]:
    return WIND_TABLE.get((txt or "").strip().upper())


def wind_txt(deg: Optional[float]) -> Optional[str]:
    if deg is None:
        return None
    return WIND_POINTS[int((deg % 360) / 22.5 + 0.5) % 16]


# ═══════════════════════════════════════════════════════════════════════════
# Units — the payload is SI: °C, m/s, hPa, mm
# ═══════════════════════════════════════════════════════════════════════════

def to_celsius(value: Optional[float], unit: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    u = (unit or "").replace("wmoUnit:", "").strip()
    if u in ("F", "degF"):
        return round((float(value) - 32.0) * 5.0 / 9.0, 1)
    if u in ("K",):
        return round(float(value) - 273.15, 1)
    if u in ("C", "degC", ""):
        return round(float(value), 1)
    raise ValueError(f"unknown temperature unit {unit!r}")


def to_ms(value: Optional[float], unit: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    u = (unit or "").replace("wmoUnit:", "").strip()
    if u in ("km_h-1", "km/h"):
        return round(float(value) / 3.6, 1)
    if u in ("mph",):
        return round(float(value) * 0.44704, 1)
    if u in ("kt", "kn"):
        return round(float(value) * 0.514444, 1)
    if u in ("m_s-1", "m s-1", "m/s"):
        return round(float(value), 1)
    raise ValueError(f"unknown speed unit {unit!r}")


def to_hpa(value: Optional[float], unit: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    u = (unit or "").replace("wmoUnit:", "").strip()
    if u == "Pa":
        return round(float(value) / 100.0, 1)
    if u == "hPa":
        return round(float(value), 1)
    raise ValueError(f"unknown pressure unit {unit!r}")


def parse_wind_speed(s: Optional[str]) -> Optional[float]:
    """NWS's text speed (`"15 km/h"`, `"10 to 20 mph"`) → m/s; a range takes
    its upper end (the card shows the wind you should expect to meet)."""
    if not s:
        return None
    parts = s.replace("to", " ").split()
    nums = [float(p) for p in parts if p.replace(".", "", 1).isdigit()]
    unit = parts[-1] if parts else ""
    if not nums:
        return None
    return to_ms(max(nums), unit)


# ═══════════════════════════════════════════════════════════════════════════
# The sun — NOAA solar position (Meeus), and Haurwitz clear-sky
# ═══════════════════════════════════════════════════════════════════════════

SUNRISE_ZENITH = 90.833          # refraction + solar radius, the civil-almanac value


def _solar_terms(dt: datetime) -> tuple[float, float]:
    """(declination °, equation of time minutes) at instant `dt`."""
    jd = dt.astimezone(UTC).timestamp() / 86400.0 + 2440587.5
    t = (jd - 2451545.0) / 36525.0
    l0 = (280.46646 + t * (36000.76983 + t * 0.0003032)) % 360
    m = 357.52911 + t * (35999.05029 - 0.0001537 * t)
    e = 0.016708634 - t * (0.000042037 + 0.0000001267 * t)
    mr = math.radians(m)
    c = (math.sin(mr) * (1.914602 - t * (0.004817 + 0.000014 * t))
         + math.sin(2 * mr) * (0.019993 - 0.000101 * t)
         + math.sin(3 * mr) * 0.000289)
    true_long = l0 + c
    omega = 125.04 - 1934.136 * t
    app_long = true_long - 0.00569 - 0.00478 * math.sin(math.radians(omega))
    eps0 = 23 + (26 + (21.448 - t * (46.815 + t * (0.00059 - t * 0.001813))) / 60) / 60
    eps = eps0 + 0.00256 * math.cos(math.radians(omega))
    decl = math.degrees(math.asin(math.sin(math.radians(eps))
                                  * math.sin(math.radians(app_long))))
    y = math.tan(math.radians(eps / 2)) ** 2
    l0r = math.radians(l0)
    eot = 4 * math.degrees(y * math.sin(2 * l0r) - 2 * e * math.sin(mr)
                           + 4 * e * y * math.sin(mr) * math.cos(2 * l0r)
                           - 0.5 * y * y * math.sin(4 * l0r)
                           - 1.25 * e * e * math.sin(2 * mr))
    return decl, eot


def solar_elevation(lat: float, lon: float, dt: datetime) -> float:
    """Geometric solar elevation in degrees (no refraction)."""
    dt = dt.astimezone(UTC)
    decl, eot = _solar_terms(dt)
    minutes = dt.hour * 60 + dt.minute + dt.second / 60.0
    tst = (minutes + eot + 4 * lon) % 1440
    ha = tst / 4 - 180
    lr, dr = math.radians(lat), math.radians(decl)
    cos_z = (math.sin(lr) * math.sin(dr)
             + math.cos(lr) * math.cos(dr) * math.cos(math.radians(ha)))
    return 90.0 - math.degrees(math.acos(max(-1.0, min(1.0, cos_z))))


def clearsky_ghi(lat: float, lon: float, dt: datetime) -> float:
    """Haurwitz (1945) clear-sky global horizontal irradiance, W m-2:
    GHI = 1098 · cos z · exp(−0.057 / cos z), zero with the sun down."""
    cz = math.sin(math.radians(solar_elevation(lat, lon, dt)))
    if cz <= 0:
        return 0.0
    return 1098.0 * cz * math.exp(-0.057 / cz)


def clearsky_mean(lat: float, lon: float, start: datetime, end: datetime,
                  step_min: int = 10) -> float:
    """Mean Haurwitz GHI over (start, end] — the window a GFS 6 h mean covers."""
    n = max(1, int((end - start).total_seconds() // (step_min * 60)))
    acc = 0.0
    for k in range(n):
        t = start + timedelta(minutes=step_min * (k + 0.5))
        acc += clearsky_ghi(lat, lon, t)
    return acc / n


def sun_times(lat: float, lon: float, day: date, tz: str) -> dict[str, Any]:
    """Sunrise/sunset for the LOCAL date `day` in `tz`. Two refinement passes
    of the NOAA noon-anchored hour-angle solution. Polar night / midnight sun
    come back as nulls with the reason, and day length as the fact (0 / 1440)."""
    zone = ZoneInfo(tz)
    local_noon = datetime(day.year, day.month, day.day, 12, tzinfo=zone)

    def event(sign: int) -> tuple[Optional[datetime], Optional[str]]:
        guess = local_noon.astimezone(UTC)
        for _ in range(3):
            decl, eot = _solar_terms(guess)
            lr, dr = math.radians(lat), math.radians(decl)
            cos_h = (math.cos(math.radians(SUNRISE_ZENITH))
                     / (math.cos(lr) * math.cos(dr)) - math.tan(lr) * math.tan(dr))
            if cos_h > 1:
                return None, "polar night"
            if cos_h < -1:
                return None, "midnight sun"
            ha = math.degrees(math.acos(cos_h))
            noon_utc_min = 720 - 4 * lon - eot
            ev_min = noon_utc_min - sign * 4 * ha
            base = datetime(day.year, day.month, day.day, tzinfo=UTC)
            # Anchor on the UTC day that holds this LOCAL date's noon.
            base += timedelta(days=(local_noon.astimezone(UTC).date() - day).days)
            guess = base + timedelta(minutes=ev_min)
        return guess.replace(microsecond=0), None

    rise, r_reason = event(+1)
    sset, s_reason = event(-1)
    absent = []
    if rise is None:
        absent.append(f"sunrise: {r_reason}")
    if sset is None:
        absent.append(f"sunset: {s_reason}")
    if rise and sset:
        length = int(round((sset - rise).total_seconds() / 60))
    else:
        length = 0 if (r_reason or s_reason) == "polar night" else 1440
    return {"sunrise": iso_z(rise), "sunset": iso_z(sset),
            "day_length_min": length, "source": "computed", "absent": absent}


# ═══════════════════════════════════════════════════════════════════════════
# One shape
# ═══════════════════════════════════════════════════════════════════════════

PLACE_KEYS = ("lat", "lon", "tz", "tz_source", "country")
WIND_KEYS = ("dir_deg", "dir_txt", "speed", "gust")
NOW_KEYS = ("t", "feels", "dewpoint", "rh", "wind", "sky", "mslp", "condition",
            "condition_raw", "valid", "source", "age_min", "absent")
HOURLY_KEYS = ("valid", "t", "feels", "dewpoint", "rh", "wind", "pop", "precip_amt",
               "sky", "mslp", "condition", "condition_raw", "t_spread", "interp",
               "source", "absent")
DAILY_KEYS = ("date", "hi", "lo", "pop", "precip_amt", "wind", "sky", "condition",
              "sunrise", "sunset", "source", "absent")
ALERT_KEYS = ("id", "event", "severity", "headline", "onset", "ends")
SUN_KEYS = ("sunrise", "sunset", "day_length_min", "source", "absent")
MEMO_KEYS = ("points", "forecast", "obs", "alerts")
RECEIPT_KEYS = ("arm", "source", "issued_at", "run", "fhr_range", "station", "memo",
                "fallback", "outline_sha", "generated_at", "notes")
TOP_KEYS = ("place", "now", "hourly", "daily", "alerts", "sun", "receipts")

#: The fields whose null must be explained, per block. `interp`, `valid`,
#: `source`, `condition`, `date` are never null by construction.
_REASONED = {
    "now": ("t", "feels", "dewpoint", "rh", "sky", "mslp", "condition_raw", "age_min"),
    "hourly": ("t", "feels", "dewpoint", "rh", "pop", "precip_amt", "sky", "mslp",
               "condition_raw", "t_spread"),
    "daily": ("hi", "lo", "pop", "precip_amt", "sky", "sunrise", "sunset"),
    "sun": ("sunrise", "sunset"),
}


def _ordered(d: dict, keys: Iterable[str], where: str) -> dict:
    keys = tuple(keys)
    extra = set(d) - set(keys)
    missing = [k for k in keys if k not in d]
    if extra or missing:
        raise ValueError(f"{where}: keys off contract (extra={sorted(extra)}, "
                         f"missing={missing})")
    return {k: d[k] for k in keys}


def _check_numeric(v: Any, where: str) -> None:
    if isinstance(v, Decimal):
        raise TypeError(f"{where} is a Decimal — numerics are float/int")
    if isinstance(v, dict):
        for k, x in v.items():
            _check_numeric(x, f"{where}.{k}")
    elif isinstance(v, list):
        for i, x in enumerate(v):
            _check_numeric(x, f"{where}[{i}]")


def _check_reasons(row: dict, kind: str, where: str) -> None:
    absent = row.get("absent")
    if not isinstance(absent, list):
        raise ValueError(f"{where}: absent[] missing")
    named = {a.split(":", 1)[0] for a in absent}
    for f in _REASONED[kind]:
        if row.get(f) is None and f not in named:
            raise ValueError(f"{where}.{f} is null with no reason in absent[]")
    wind = row.get("wind")
    if isinstance(wind, dict):
        for f in WIND_KEYS:
            if wind.get(f) is None and f"wind.{f}" not in named:
                raise ValueError(f"{where}.wind.{f} is null with no reason in absent[]")


def _wind(w: dict, where: str) -> dict:
    return _ordered(w, WIND_KEYS, where)


def build_payload(*, place: dict, now: dict, hourly: list[dict], daily: list[dict],
                  alerts: list[dict], sun: dict, receipts: dict) -> dict:
    """The route's body. Pure: no clock, no I/O. Fixes key order everywhere,
    refuses a Decimal, refuses an unexplained null."""
    now = dict(now)
    now["wind"] = _wind(now["wind"], "now.wind")
    now = _ordered(now, NOW_KEYS, "now")
    _check_reasons(now, "now", "now")

    rows = []
    for i, r in enumerate(hourly):
        r = dict(r)
        r["wind"] = _wind(r["wind"], f"hourly[{i}].wind")
        r = _ordered(r, HOURLY_KEYS, f"hourly[{i}]")
        _check_reasons(r, "hourly", f"hourly[{i}]")
        rows.append(r)

    days = []
    for i, d in enumerate(daily):
        d = dict(d)
        d["wind"] = _wind(d["wind"], f"daily[{i}].wind")
        d = _ordered(d, DAILY_KEYS, f"daily[{i}]")
        _check_reasons(d, "daily", f"daily[{i}]")
        days.append(d)

    sun = _ordered(sun, SUN_KEYS, "sun")
    _check_reasons(sun, "sun", "sun")

    receipts = dict(receipts)
    if receipts.get("memo") is not None:
        receipts["memo"] = _ordered(receipts["memo"], MEMO_KEYS, "receipts.memo")
    receipts = _ordered(receipts, RECEIPT_KEYS, "receipts")

    out = {
        "place": _ordered(place, PLACE_KEYS, "place"),
        "now": now,
        "hourly": rows,
        "daily": days,
        "alerts": [_ordered(a, ALERT_KEYS, f"alerts[{i}]") for i, a in enumerate(alerts)],
        "sun": sun,
        "receipts": receipts,
    }
    _check_numeric(out, "payload")
    return out


HOURLY_ROWS = 48


def trim_to_now(rows: list[dict], now: datetime, limit: int = HOURLY_ROWS
                ) -> tuple[list[dict], str]:
    """D-09-25-10 — hourly starts at the current hour, on both arms. Drops every
    row before the hour containing `now` (UTC, floored), keeps at most `limit`
    from there, and returns the `receipts.notes[]` line saying where it started
    and how many rows it kept. Fewer than `limit` is correct: nothing is
    invented past what the arm had. `now` is injected — the arms' own clock,
    never read here."""
    hour = now.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    kept = [r for r in rows if parse_iso(r["valid"]) >= hour][:limit]
    return kept, f"hourly from {hour:%H}Z, {len(kept)} rows"


#: The only receipts fields that describe the REQUEST rather than the weather
#: (D-09-25-15): when it was built, and which memo entries it hit.
ETAG_EXCLUDED_RECEIPTS = ("generated_at", "memo")


def content_etag(payload: dict) -> str:
    """D-09-25-15 — the ETag is the content. `W/"<arm>:<sha256 hex[:32]>"` over
    the canonical JSON of the payload minus `receipts.generated_at` and
    `receipts.memo`. Everything else — `now` (age_min included), every hourly
    and daily row, every alert, the run — is in the hash, so a 304 means the
    weather in the body is byte-identical. Pure: no clock, no I/O."""
    receipts = payload.get("receipts")
    if not isinstance(receipts, dict) or "arm" not in receipts:
        raise ValueError("content_etag: payload.receipts has no arm")
    body = dict(payload)
    body["receipts"] = {k: v for k, v in receipts.items()
                        if k not in ETAG_EXCLUDED_RECEIPTS}
    canon = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.sha256(canon.encode("utf-8")).hexdigest()[:32]
    return f'W/"{receipts["arm"]}:{digest}"'
