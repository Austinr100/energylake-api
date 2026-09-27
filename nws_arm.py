"""
Local Weather lane A — the US arm: NWS, read live (d091477, §2.3).

D-09-25-03 — NWS IS READ, NEVER BANKED, AND THE MEMO IS THE ONLY CACHE. A point
forecast at arbitrary coordinates cannot be pre-banked, so `api.weather.gov` is
read on demand behind an in-process memo: `dict[key] -> (monotonic, payload)`
per kind, the `_delta_board_cache` shape. Keys and TTLs:

    points     (lat4, lon4)       10 min   → the gridpoint `{office}/{x},{y}`
    forecast   gridpoint          10 min   (the 12 h periods)
    hourly     gridpoint          10 min   (forecastHourly)
    stations   gridpoint          10 min   (the station list, for `now`)
    obs        gridpoint           5 min   (the latest observation)
    alerts     gridpoint           2 min

No table, no cron, no row in `datasets`. Every memo hit/miss rides in
`receipts.memo` (`forecast` is a hit only when BOTH forecast kinds hit).

D-09-25-04, AS AMENDED BY D-09-25-09 — A FORECAST FAILURE RAISES `NwsError`
(`points`, `forecast`, `forecastHourly`, or a malformed forecast body), and the
route falls through to the model arm for that request with `receipts.fallback`.
The observation and the alerts are garnish: their failure is stated in place —
`now` keeps its NWS nulls with `obs unavailable (<status>)` reasons, `alerts`
is `[]` with an `alerts unavailable (<status>)` note — and never falls through. Timeouts 4 s connect /
8 s read; one immediate retry on a 5xx and no other retry. A 403/429 is also
logged `[[LOCAL_NWS_BLOCKED]]` with NWS's body verbatim — that is STOP-N's
evidence, and nothing here tries to route around it.

UNITS (D-09-25-16). `units=us` is requested on both forecast calls: NWS
publishes integer °F, and asking for SI makes it round to integer °C first, so
the page's °F could land a degree off weather.gov. Every value is still
converted by the unit it CARRIES (`temperatureUnit`, `unitCode`), not by the
unit that was asked for: the payload is °C / m/s / hPa whatever NWS sent.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Any, Awaitable, Callable, Optional
from zoneinfo import ZoneInfo

import local_forecast as lf

log = logging.getLogger("energylake.local")

BASE = "https://api.weather.gov"
USER_AGENT = "energylake.io (ops@energylake.io)"
HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/geo+json"}
CONNECT_TIMEOUT_S = 4.0
READ_TIMEOUT_S = 8.0

TTL_S = {"points": 600.0, "forecast": 600.0, "hourly": 600.0, "stations": 600.0,
         "obs": 300.0, "alerts": 120.0}

HOURLY_ROWS = 48
DAILY_ROWS = 10

Transport = Callable[[str, Optional[dict]], Awaitable[tuple[int, Any]]]


class NwsError(Exception):
    """Any NWS failure. `reason` is what `receipts.fallback.reason` prints:
    `HTTP 503`, `HTTP 404`, or the exception class (`ReadTimeout`, `KeyError`)."""

    def __init__(self, reason: str, *, url: str = "", body: Any = None,
                 tz: Optional[str] = None):
        self.reason = reason
        self.url = url
        self.body = body
        self.tz = tz              # the points timeZone, when points had answered
        super().__init__(f"{reason} {url}".strip())


class NwsClient:
    """The NWS reader and its memo. `transport(url, params) -> (status, json)`
    is injectable; the real one is httpx with the required User-Agent."""

    def __init__(self, *, transport: Optional[Transport] = None,
                 clock: Callable[[], float] = time.monotonic, base: str = BASE):
        self._transport = transport
        self._clock = clock
        self.base = base.rstrip("/")
        self._client = None
        self._memo: dict[str, dict[Any, tuple[float, Any]]] = {k: {} for k in TTL_S}
        self.fetches: dict[str, int] = {k: 0 for k in TTL_S}
        self.hits: dict[str, int] = {k: 0 for k in TTL_S}

    # ── the one network call ────────────────────────────────────────────────

    async def _http(self, url: str, params: Optional[dict]) -> tuple[int, Any]:
        if self._transport is not None:
            return await self._transport(url, params)
        import httpx  # local: the rest of the API must not need it at import

        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers=HEADERS,
                timeout=httpx.Timeout(READ_TIMEOUT_S, connect=CONNECT_TIMEOUT_S))
        resp = await self._client.get(url, params=params)
        try:
            body = resp.json()
        except ValueError:
            body = resp.text
        return resp.status_code, body

    async def _get(self, url: str, params: Optional[dict] = None) -> Any:
        for attempt in (1, 2):
            try:
                status, body = await self._http(url, params)
            except Exception as e:           # timeout, connect, DNS — the class is the reason
                raise NwsError(type(e).__name__, url=url) from e
            if 500 <= status < 600 and attempt == 1:
                continue                     # the one immediate retry
            if status in (403, 429):
                log.warning("[[LOCAL_NWS_BLOCKED]] HTTP %s from %s body=%r", status, url, body)
            if status != 200:
                raise NwsError(f"HTTP {status}", url=url, body=body)
            if not isinstance(body, dict):
                raise NwsError("MalformedBody", url=url, body=body)
            return body
        raise AssertionError("unreachable")

    async def _memo_get(self, kind: str, key: Any, url: str,
                        params: Optional[dict] = None) -> tuple[Any, str]:
        now = self._clock()
        hit = self._memo[kind].get(key)
        if hit is not None and (now - hit[0]) < TTL_S[kind]:
            self.hits[kind] += 1
            return hit[1], "hit"
        self.fetches[kind] += 1
        body = await self._get(url, params)
        self._memo[kind][key] = (now, body)
        return body, "miss"

    # ── the five reads ──────────────────────────────────────────────────────

    async def fetch(self, lat: float, lon: float,
                    timings: Optional[lf.Timings] = None) -> dict:
        """Every read the US arm needs, memoised, in two classes (D-09-25-09).

        FORECAST-CRITICAL — `points`, `forecastHourly`, `forecast`: any failure
        raises NwsError and the route falls through to the model arm; a points
        answer's timeZone rides on the error so the fallback can still name the
        place's real tz.

        GARNISH — the observation (the station list, then its latest) and the
        alerts: a failure is caught HERE and handed to `build` as
        `obs_error` / `alerts_error` (the NwsError's reason), which states it in
        place. A failed call is not memoised (`_memo_get` stores only a body),
        so the next request retries it.

        ONLY `points` WAITS (D-09-25-28). Every other URL comes from its
        answer, so once it is in, the legs run at the same time: hourly ∥
        forecast ∥ (stations → latest observation) ∥ alerts. Each leg keeps its
        own failure class; the forecast-critical error re-raised is the first in
        the old serial order (hourly, then forecast), with the points tz on it.

        `timings` (D-09-25-27) records each leg for `Server-Timing`; the
        default is a throwaway recorder nobody reads."""
        timings = timings if timings is not None else lf.Timings()
        tz = None
        try:
            pkey = (round(lat, 4), round(lon, 4))
            with timings.mark("nws_points"):
                points, m_points = await self._memo_get(
                    "points", pkey, f"{self.base}/points/{pkey[0]},{pkey[1]}")
            p = points["properties"]
            tz = p.get("timeZone")
            grid = f"{p['gridId']}/{p['gridX']},{p['gridY']}"
            hourly_url, forecast_url = p["forecastHourly"], p["forecast"]
            stations_url = p["observationStations"]
        except NwsError as e:
            e.tz = e.tz or tz
            raise
        except (KeyError, IndexError, TypeError, ValueError, AttributeError) as e:
            raise NwsError(type(e).__name__, tz=tz) from e

        async def forecast_leg():
            with timings.mark("nws_forecast"):
                return await asyncio.gather(
                    self._memo_get("hourly", grid, hourly_url, {"units": "us"}),
                    self._memo_get("forecast", grid, forecast_url, {"units": "us"}),
                    return_exceptions=True)

        async def obs_leg():
            station = None
            try:
                with timings.mark("nws_obs"):
                    stations, _ = await self._memo_get("stations", grid, stations_url)
                    station = stations["features"][0]["properties"]["stationIdentifier"]
                    obs, m_obs = await self._memo_get(
                        "obs", grid, f"{self.base}/stations/{station}/observations/latest")
            except NwsError as e:
                return station, None, e.reason, "miss"
            except (KeyError, IndexError, TypeError, ValueError, AttributeError) as e:
                return station, None, type(e).__name__, "miss"
            return station, obs, None, m_obs

        async def alerts_leg():
            try:
                with timings.mark("nws_alerts"):
                    alerts, m_alerts = await self._memo_get(
                        "alerts", grid, f"{self.base}/alerts/active",
                        {"point": f"{pkey[0]},{pkey[1]}"})
            except NwsError as e:
                return None, e.reason, "miss"
            return alerts, None, m_alerts

        legs = await asyncio.gather(forecast_leg(), obs_leg(), alerts_leg(),
                                    return_exceptions=True)
        for leg in legs:                    # a bug in a leg is a bug, not garnish
            if isinstance(leg, BaseException):
                raise leg
        (hourly_r, forecast_r), obs_r, alerts_r = legs
        for res in (hourly_r, forecast_r):
            if isinstance(res, NwsError):
                res.tz = res.tz or tz
                raise res
            if isinstance(res, BaseException):
                raise res
        (hourly, m_hourly), (forecast, m_fc) = hourly_r, forecast_r
        station, obs, obs_error, m_obs = obs_r
        alerts, alerts_error, m_alerts = alerts_r

        return {
            "points": points, "grid": grid, "tz": tz, "station": station,
            "hourly": hourly, "forecast": forecast, "obs": obs, "alerts": alerts,
            "obs_error": obs_error, "alerts_error": alerts_error,
            "memo": {"points": m_points,
                     "forecast": "hit" if (m_hourly, m_fc) == ("hit", "hit") else "miss",
                     "obs": m_obs, "alerts": m_alerts},
        }


# ═══════════════════════════════════════════════════════════════════════════
# Rows — raw NWS documents → the one shape's blocks
# ═══════════════════════════════════════════════════════════════════════════

def _qv(v: Any) -> tuple[Optional[float], Optional[str]]:
    """A QuantitativeValue `{"value", "unitCode"}` or a bare number."""
    if isinstance(v, dict):
        return v.get("value"), v.get("unitCode")
    return v, None


def _temp(period: dict) -> Optional[float]:
    val, unit = _qv(period.get("temperature"))
    return lf.to_celsius(val, unit or period.get("temperatureUnit"))


def _cond(icon: Optional[str], absent: list[str], *, raw: bool = True
          ) -> tuple[str, Optional[str]]:
    """The house word and the raw token. An `unknown` says why in `absent`
    (D-09-25-31); `raw=False` for a block with no `condition_raw` (daily)."""
    token = lf.icon_token(icon)
    condition = lf.condition_from_token(token)
    if token is None:
        if raw:
            absent.append("condition_raw: nws gave no icon")
        absent.append("condition: nws gave no icon")
    elif condition == lf.UNKNOWN:
        absent.append(f"condition: nws icon token '{token}' is not in the house table")
    return condition, token


def _sky(token: Optional[str], absent: list[str]) -> Optional[float]:
    sky = lf.COVER_FRACTION.get(token or "")
    if sky is None:
        absent.append("sky: icon carries no cover class")
    return sky


def hourly_row(period: dict, source: str) -> dict:
    absent: list[str] = []
    t = _temp(period)
    if t is None:
        absent.append("t: not reported")
    dew = lf.to_celsius(*_qv(period.get("dewpoint")))
    if dew is None:
        absent.append("dewpoint: not reported")
    rh_v, _ = _qv(period.get("relativeHumidity"))
    rh = int(round(rh_v)) if rh_v is not None else None
    if rh is None:
        absent.append("rh: not reported")
    pop_v, _ = _qv(period.get("probabilityOfPrecipitation"))
    pop = int(round(pop_v)) if pop_v is not None else None
    if pop is None:
        absent.append("pop: not reported")
    wind = _period_wind(period, absent, gust_reason="not in nws hourly")
    condition, raw = _cond(period.get("icon"), absent)
    sky = _sky(raw, absent)
    absent += ["feels: not in nws hourly", "precip_amt: not in nws hourly",
               "mslp: not in nws hourly", "t_spread: gefs not banked"]
    return {"valid": lf.iso_z(lf.parse_iso(period["startTime"])), "t": t, "feels": None,
            "dewpoint": dew, "rh": rh, "wind": wind, "pop": pop, "precip_amt": None,
            "sky": sky, "mslp": None, "condition": condition, "condition_raw": raw,
            "t_spread": None, "interp": False, "source": source, "absent": absent}


def _period_wind(period: dict, absent: list[str], *, gust_reason: str) -> dict:
    txt = (period.get("windDirection") or "").strip() or None
    deg = lf.wind_deg(txt)
    speed = lf.parse_wind_speed(period.get("windSpeed"))
    gust = lf.parse_wind_speed(period.get("windGust")) if isinstance(
        period.get("windGust"), str) else None
    if deg is None:
        absent.append("wind.dir_deg: not reported")
        txt = None
    if txt is None:
        absent.append("wind.dir_txt: not reported")
    if speed is None:
        absent.append("wind.speed: not reported")
    if gust is None:
        absent.append(f"wind.gust: {gust_reason}")
    return {"dir_deg": deg, "dir_txt": txt, "speed": speed, "gust": gust}


def daily_rows(periods: list[dict], tz: str, lat: float, lon: float,
               source: str) -> list[dict]:
    """Day/night paired into one row: `hi` from the day period, `lo` from the
    night that follows it. A leading night-only period (the day has elapsed)
    fills `lo` and leaves `hi` null with its reason."""
    zone = ZoneInfo(tz)
    rows: list[dict] = []
    i = 0
    while i < len(periods) and len(rows) < DAILY_ROWS:
        p = periods[i]
        if p.get("isDaytime"):
            day, night = p, (periods[i + 1] if i + 1 < len(periods)
                             and not periods[i + 1].get("isDaytime") else None)
            i += 2 if night is not None else 1
        else:
            day, night = None, p
            i += 1
        rows.append(_daily_row(day, night, zone, tz, lat, lon, source))
    return rows


def _daily_row(day: Optional[dict], night: Optional[dict], zone: ZoneInfo, tz: str,
               lat: float, lon: float, source: str) -> dict:
    absent: list[str] = []
    lead = day or night
    d = datetime.fromisoformat(lead["startTime"]).astimezone(zone).date()
    hi = _temp(day) if day else None
    if hi is None:
        absent.append("hi: day period elapsed" if day is None else "hi: not reported")
    lo = _temp(night) if night else None
    if lo is None:
        absent.append("lo: night period beyond the forecast" if night is None
                      else "lo: not reported")
    pops = [v for v in (_qv((x or {}).get("probabilityOfPrecipitation"))[0]
                        for x in (day, night)) if v is not None]
    pop = int(round(max(pops))) if pops else None
    if pop is None:
        absent.append("pop: not reported")
    wind = _period_wind(lead, absent, gust_reason="not in nws forecast")
    condition, raw = _cond(lead.get("icon"), absent, raw=False)
    sky = _sky(raw, absent)
    sun = lf.sun_times(lat, lon, d, tz)
    absent += sun["absent"]
    absent.append("precip_amt: not in nws forecast")
    return {"date": d.isoformat(), "hi": hi, "lo": lo, "pop": pop, "precip_amt": None,
            "wind": wind, "sky": sky, "condition": condition, "sunrise": sun["sunrise"],
            "sunset": sun["sunset"], "source": source, "absent": absent}


def now_block(obs: dict, station: str, generated_at: datetime) -> dict:
    p = obs["properties"]
    absent: list[str] = []
    valid = lf.parse_iso(p.get("timestamp"))
    t = lf.to_celsius(*_qv(p.get("temperature")))
    if t is None:
        absent.append("t: nws qc")
    dew = lf.to_celsius(*_qv(p.get("dewpoint")))
    if dew is None:
        absent.append("dewpoint: nws qc")
    rh_v, _ = _qv(p.get("relativeHumidity"))
    rh = int(round(rh_v)) if rh_v is not None else None
    if rh is None:
        absent.append("rh: nws qc")
    feels = None
    for k in ("heatIndex", "windChill"):
        feels = lf.to_celsius(*_qv(p.get(k)))
        if feels is not None:
            break
    if feels is None:
        absent.append("feels: station reported neither heat index nor wind chill")
    deg, _ = _qv(p.get("windDirection"))
    speed = lf.to_ms(*_qv(p.get("windSpeed")))
    gust = lf.to_ms(*_qv(p.get("windGust")))
    wind = {"dir_deg": deg, "dir_txt": lf.wind_txt(deg), "speed": speed, "gust": gust}
    for f in ("dir_deg", "dir_txt", "speed"):
        if wind[f] is None:
            absent.append(f"wind.{f}: nws qc")
    if gust is None:
        absent.append("wind.gust: no gust reported")
    mslp = lf.to_hpa(*_qv(p.get("seaLevelPressure")))
    if mslp is None:
        absent.append("mslp: nws qc")
    amounts = [(l.get("amount") or "").lower() for l in (p.get("cloudLayers") or [])]
    covers = [lf.COVER_FRACTION[a] for a in amounts if a in lf.COVER_FRACTION]
    sky = max(covers) if covers else None
    if sky is None:
        absent.append("sky: no cloud layers reported")
    condition, raw = _cond(p.get("icon"), absent)
    age = (int((generated_at - valid).total_seconds() // 60) if valid else None)
    if age is None:
        absent.append("age_min: observation carries no timestamp")
    stamp = valid.strftime("%H:%MZ") if valid else "time unknown"
    return {"t": t, "feels": feels, "dewpoint": dew, "rh": rh, "wind": wind, "sky": sky,
            "mslp": mslp, "condition": condition, "condition_raw": raw,
            "valid": lf.iso_z(valid), "source": f"nws · {station} · observed {stamp}",
            "age_min": age, "absent": absent}


def now_unavailable(station: Optional[str], reason: str) -> dict:
    """D-09-25-09 — the observation call failed: `now` keeps the NWS arm and
    carries its nulls, each with `"<field>: obs unavailable (<reason>)"`, and
    its source says there is no recent observation. Never a model value, never
    the word "observed"."""
    why = f"obs unavailable ({reason})"
    absent = [f"{f}: {why}" for f in ("t", "feels", "dewpoint", "rh", "wind.dir_deg",
                                      "wind.dir_txt", "wind.speed", "wind.gust", "sky",
                                      "mslp", "condition_raw", "condition", "valid",
                                      "age_min")]
    return {"t": None, "feels": None, "dewpoint": None, "rh": None,
            "wind": {"dir_deg": None, "dir_txt": None, "speed": None, "gust": None},
            "sky": None, "mslp": None, "condition": lf.UNKNOWN, "condition_raw": None,
            "valid": None,
            "source": f"nws · {station or 'station unknown'} · no recent observation",
            "age_min": None, "absent": absent}


def alert_rows(alerts: dict) -> list[dict]:
    out = []
    for f in alerts.get("features") or []:
        p = f.get("properties") or {}
        out.append({"id": p.get("id") or f.get("id"), "event": p.get("event"),
                    "severity": p.get("severity"), "headline": p.get("headline"),
                    "onset": lf.iso_z(lf.parse_iso(p.get("onset"))),
                    "ends": lf.iso_z(lf.parse_iso(p.get("ends")))})
    return out


def build(bundle: dict, lat: float, lon: float, generated_at: datetime,
          notes: list[str]) -> dict:
    """The NWS bundle → `build_payload`'s keyword arguments."""
    tz = bundle["tz"]
    grid = bundle["grid"]
    hp = bundle["hourly"]["properties"]
    issued = lf.parse_iso(hp.get("updateTime") or hp.get("generatedAt"))
    source = f"nws · gridpoint {grid}"
    try:
        hourly = [hourly_row(p, source) for p in hp["periods"]]
        daily = daily_rows(bundle["forecast"]["properties"]["periods"], tz, lat, lon, source)
    except (KeyError, IndexError, TypeError, ValueError, AttributeError) as e:
        # A malformed forecast body is an NWS failure too (D-09-25-04), found late.
        raise NwsError(type(e).__name__, tz=tz) from e
    # D-09-25-10: from the hour containing `generated_at`, at most 48 rows.
    hourly, hourly_note = lf.trim_to_now(hourly, generated_at, HOURLY_ROWS)
    notes.append(hourly_note)

    # D-09-25-09: the garnish is stated in place, never a fallback. A malformed
    # observation or alerts body is the same garnish failure, found late.
    obs_error = bundle.get("obs_error")
    if obs_error is None:
        try:
            now = now_block(bundle["obs"], bundle["station"], generated_at)
        except (KeyError, IndexError, TypeError, ValueError, AttributeError) as e:
            obs_error = type(e).__name__
    if obs_error is not None:
        now = now_unavailable(bundle.get("station"), obs_error)
    alerts_error = bundle.get("alerts_error")
    if alerts_error is None:
        try:
            alerts = alert_rows(bundle["alerts"])
        except (KeyError, IndexError, TypeError, ValueError, AttributeError) as e:
            alerts_error = type(e).__name__
    if alerts_error is not None:
        alerts = []
        notes.append(f"alerts unavailable ({alerts_error})")
    local_today = generated_at.astimezone(ZoneInfo(tz)).date()
    return {
        "place": {"lat": lat, "lon": lon, "tz": tz, "tz_source": "nws", "country": "US"},
        "now": now,
        "hourly": hourly,
        "daily": daily,
        "alerts": alerts,
        "sun": lf.sun_times(lat, lon, local_today, tz),
        "receipts": {
            "arm": lf.ARM_NWS,
            "source": f"{source} · issued {issued.strftime('%Y-%m-%dT%H:%MZ') if issued else 'unknown'}",
            "issued_at": lf.iso_z(issued),
            "run": None,
            "fhr_range": None,
            "station": bundle["station"],
            "memo": bundle["memo"],
            "fallback": None,
            "outline_sha": lf.outline_sha(),
            "generated_at": lf.iso_z(generated_at),
            "notes": notes,
        },
    }
