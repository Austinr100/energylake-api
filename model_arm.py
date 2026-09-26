"""
Local Weather lane A — the model arm: the GFS bank, in-process (d091477, §2.4).

Outside the US outline — or inside it when NWS fails (D-09-25-04), or on
`?arm=model` — the forecast is the newest banked GFS run read at ONE grid cell
of the `global` value sidecars, four bytes per forecast hour, through
`weather_point`'s own reader (`SidecarStore`, `locate`, `byte_offset`,
`get_values`). There is no HTTP hop to `/api/weather/point/ladder`.

THE LADDER IS `weather_point.ladder`. The body of `/api/weather/point/ladder`
was lifted into the module (d091485, #78 handback item 2) and `read_ladder`
below calls it — header from the first of three rungs that has one, one
`locate`, one offset, the bounded fan-out — so the route and this arm share one
reader. On a `pm180` global header the locator wraps (a full-circle axis has no
outside in longitude), so Fiji and Samoa read their own cells.

THE CADENCE IS THE BANK'S. f000..f240 every 6 h — 41 rungs (`wp.ladder_fhrs()`,
the pantry's D2_LADDER). Not 3-hourly to f120: the bank never wrote those.

D-09-24-09 — THE MODEL ARM IS ALWAYS LABELLED. `now.source` is
`model · GFS {HH}Z f000`, every row carries its f-hour(s) in `source`, and the
word "observed" is never written on this arm.

ABSENCE IS STATED. The bank has no sidecar for precipitation, dewpoint/RH,
gusts, cloud cover or wind direction (`wind10m` is hypot(u, v), a speed); those
fields are null with `not banked`. `t_spread` is null with `gefs not banked`.
STOP-B: a param with no `global` sidecar on the run is null with
`not banked on run` and the receipt's `notes` names the key; there is no
fallback to `na3`.

SKY FROM DSWRF. GFS `dswrf` is a 6 h MEAN flux over (f−6, f] and is absent at
f000 by design (pantry d2/params.py DSWRF). So an hour's `sky` compares the
mean of the window that holds it against the Haurwitz clear-sky mean over the
SAME window: `sky = clip(1 − dswrf / clearsky_mean, 0, 1)`. At f000 there is no
window (`sky: not banked at f000`); with the sun down at the row's valid time
`sky` is null with `night`; a window whose clear-sky mean is under
`LOW_SUN_WM2` is null with `low sun` (the ratio is noise there).
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional
from zoneinfo import ZoneInfo

import local_forecast as lf
import weather_point as wp

UTC = timezone.utc

MODEL = "gfs"
CROP = "global"
PARAMS = ("t2m", "wind10m", "mslp", "dswrf")
HOURLY_ROWS = 48
DAILY_ROWS = 10
LOW_SUN_WM2 = 25.0
RUN_MEMO_TTL_S = 300.0
RUN_PROBE_DEPTH = 4

# The bank writes no `latest.json` (nothing in the pantry's d2/ or
# build_d2_sequence.py emits one), so the run is discovered from the render
# ledger and then PROVEN by a header probe: the newest candidate whose global
# t2m f000 header exists wins.
RUNS_SQL = """
    SELECT DISTINCT run_date, cycle
    FROM d2_render_runs
    WHERE model = %(model)s
      AND manifest_sha IS NOT NULL
    ORDER BY run_date DESC, cycle DESC
    LIMIT %(n)s
"""


class ModelArmError(Exception):
    def __init__(self, reason: str, detail: Optional[dict] = None):
        self.reason = reason
        self.detail = detail or {}
        super().__init__(reason)


# (monotonic, run_dt) — the one memo this arm keeps; the store keeps the rest
# (headers forever, values LRU'd).
_run_memo: dict[str, tuple[float, datetime]] = {}
# The one background refresh in flight per model (D-09-25-29). Held here so the
# task is not garbage-collected mid-flight.
_refresh_tasks: dict[str, asyncio.Task] = {}

#: D-09-25-29 — past this age a request waits for discovery rather than serve
#: the memo: one missed 6-hourly cycle plus an hour, so a dead refresher cannot
#: serve a day-old run forever.
RUN_MEMO_MAX_AGE_S = 7 * 3600.0

log = logging.getLogger("energylake.local")


def run_label(run_dt: datetime) -> str:
    return f"GFS {run_dt:%H}Z"


async def _discover(store: wp.SidecarStore,
                    candidates: Callable[[], Awaitable[list[datetime]]]) -> datetime:
    """The ledger's candidates, newest first, each PROVEN by its global t2m
    f000 header; the first that has one is the run."""
    try:
        runs = await candidates()
    except Exception as e:
        raise ModelArmError(f"run ledger unavailable: {type(e).__name__}")
    probed = []
    for run_dt in runs[:RUN_PROBE_DEPTH]:
        key = wp.header_key(MODEL, CROP, run_dt, "t2m", 0)
        probed.append(key)
        try:
            await store.get_header(key)
        except wp.PointError as e:
            if e.status == 404:
                continue
            raise ModelArmError("sidecar storage error", e.detail)
        return run_dt
    raise ModelArmError("no banked gfs run carries a global t2m f000 sidecar",
                        {"probed": probed})


async def _refresh(store: wp.SidecarStore,
                   candidates: Callable[[], Awaitable[list[datetime]]],
                   clock: Callable[[], float]) -> None:
    try:
        run_dt = await _discover(store, candidates)
    except Exception as e:
        reason = e.reason if isinstance(e, ModelArmError) else type(e).__name__
        log.warning("[[LOCAL_RUN_REFRESH_FAILED]] %s", reason)
        return                                  # the memo stands
    _run_memo[MODEL] = (clock(), run_dt)


def _start_refresh(store: wp.SidecarStore,
                   candidates: Callable[[], Awaitable[list[datetime]]],
                   clock: Callable[[], float]) -> None:
    """Single-flight: at most one refresh per model. A task left behind by a
    closed event loop can never finish, so it does not count as running."""
    loop = asyncio.get_running_loop()
    task = _refresh_tasks.get(MODEL)
    if task is not None and not task.done() and task.get_loop() is loop:
        return
    _refresh_tasks[MODEL] = loop.create_task(_refresh(store, candidates, clock))


async def discover_run(store: wp.SidecarStore,
                       candidates: Callable[[], Awaitable[list[datetime]]],
                       clock: Callable[[], float] = time.monotonic) -> datetime:
    """D-09-25-29 — run discovery leaves the request path. A memo up to 7 h old
    is served at once; past RUN_MEMO_TTL_S the first request that sees it
    starts ONE background refresh and does not wait for it. A request waits
    only when there is no memo (the first model read after a deploy) or the
    memo is older than RUN_MEMO_MAX_AGE_S."""
    hit = _run_memo.get(MODEL)
    if hit is not None:
        age = clock() - hit[0]
        if age <= RUN_MEMO_MAX_AGE_S:
            if age >= RUN_MEMO_TTL_S:
                _start_refresh(store, candidates, clock)
            return hit[1]
    run_dt = await _discover(store, candidates)
    _run_memo[MODEL] = (clock(), run_dt)
    return run_dt


async def read_ladder(store: wp.SidecarStore, run_dt: datetime, param: str,
                      lat: float, lon: float) -> dict:
    """One cell across f000..f240 for one param, through `wp.ladder` — the
    same body `/api/weather/point/ladder` serves. `values[fhr]` is a float, or
    None with `reasons[fhr]` saying why; `missing_header` names the key when the
    param has no sidecar on the run at all (STOP-B)."""
    try:
        lad = await wp.ladder(store, MODEL, CROP, run_dt, param, lat, lon,
                              wp.ladder_fhrs())
    except wp.PointError as e:
        err = e.detail.get("error") if isinstance(e.detail, dict) else None
        if e.status == 404 and err == "sidecar not found":
            return {"param": param, "units": None, "values": {}, "reasons": {},
                    "missing_header": e.detail["expected"]["header"]}
        if e.status == 404 and err == "point is outside the crop":
            raise ModelArmError("point outside the global crop", e.detail)
        raise ModelArmError("sidecar storage error", e.detail)
    values: dict[int, Optional[float]] = {}
    reasons: dict[int, str] = {}
    for row in lad["values"]:
        f = row["fhr"]
        if not row["available"]:
            values[f] = None
            reasons[f] = "not banked on run"
        elif row["value"] is None:
            values[f] = None
            reasons[f] = "nodata"
        else:
            values[f] = _si(param, row["value"], lad["units"])
    return {"param": param, "units": lad["units"], "values": values,
            "reasons": reasons, "missing_header": None, "cell": lad["cell"]}


def _si(param: str, v: float, units: Optional[str]) -> float:
    u = (units or "").strip()
    if param == "t2m":
        return lf.to_celsius(v, "K" if u in ("K", "kelvin") else u)
    if param == "mslp":
        return lf.to_hpa(v, "Pa" if u in ("Pa", "pascal") else u)
    if param == "wind10m":
        return lf.to_ms(v, "m/s" if u in ("m s-1", "m s**-1", "m/s") else u)
    return round(float(v), 1)          # dswrf, W m-2


# ── the hourly series ───────────────────────────────────────────────────────

def _at(lad: dict, h: int) -> tuple[Optional[float], bool, Optional[str], int, int]:
    """(value, interp, reason, fhr_lo, fhr_hi) at hour h: exact at an f-hour,
    linear between the two rungs around it, None beyond what was banked."""
    step = wp.LADDER_FHR_STEP
    lo = (h // step) * step
    hi = lo if h % step == 0 else lo + step
    if lad["missing_header"]:
        return None, lo != hi, "not banked on run", lo, hi
    v_lo, v_hi = lad["values"].get(lo), lad["values"].get(hi)
    if v_lo is None or v_hi is None:
        f = lo if v_lo is None else hi
        return None, lo != hi, lad["reasons"].get(f, "not banked on run"), lo, hi
    if lo == hi:
        return v_lo, False, None, lo, hi
    w = (h - lo) / step
    return round(v_lo + (v_hi - v_lo) * w, 1), True, None, lo, hi


def _sky_at(dswrf: dict, run_dt: datetime, h: int, lat: float, lon: float
            ) -> tuple[Optional[float], Optional[str]]:
    valid = run_dt + timedelta(hours=h)
    if lf.solar_elevation(lat, lon, valid) <= 0:
        return None, "night"
    if h == 0:
        return None, "not banked at f000 (dswrf is a 6 h mean)"
    if dswrf["missing_header"]:
        return None, "not banked on run"
    step = wp.LADDER_FHR_STEP
    f = int(math.ceil(h / step)) * step
    flux = dswrf["values"].get(f)
    if flux is None:
        return None, dswrf["reasons"].get(f, "not banked on run")
    cs = lf.clearsky_mean(lat, lon, run_dt + timedelta(hours=f - step),
                          run_dt + timedelta(hours=f))
    if cs < LOW_SUN_WM2:
        return None, "low sun"
    return round(min(1.0, max(0.0, 1.0 - flux / cs)), 2), None


def _series(ladders: dict, run_dt: datetime, lat: float, lon: float,
            hours: range) -> list[dict]:
    out = []
    for h in hours:
        t, interp, t_why, lo, hi = _at(ladders["t2m"], h)
        ws, _, ws_why, _, _ = _at(ladders["wind10m"], h)
        p, _, p_why, _, _ = _at(ladders["mslp"], h)
        sky, sky_why = _sky_at(ladders["dswrf"], run_dt, h, lat, lon)
        out.append({"h": h, "valid": run_dt + timedelta(hours=h), "t": t, "t_why": t_why,
                    "wind": ws, "wind_why": ws_why, "mslp": p, "mslp_why": p_why,
                    "sky": sky, "sky_why": sky_why, "interp": interp,
                    "fhr_lo": lo, "fhr_hi": hi})
    return out


_NOT_BANKED_ROW = ["feels: not banked", "dewpoint: not banked", "rh: not banked",
                   "pop: not banked", "precip_amt: not banked",
                   "wind.dir_deg: not banked (wind10m is a speed)",
                   "wind.dir_txt: not banked (wind10m is a speed)",
                   "wind.gust: not banked", "condition_raw: the model arm has no icon",
                   "t_spread: gefs not banked"]


def _hour_row(s: dict, label: str) -> dict:
    absent = list(_NOT_BANKED_ROW)
    for f, why in (("t", s["t_why"]), ("wind.speed", s["wind_why"]),
                   ("mslp", s["mslp_why"]), ("sky", s["sky_why"])):
        if why:
            absent.append(f"{f}: {why}")
    if s["sky"] is None:                        # D-09-25-31: `unknown` says why
        absent.append(f"condition: from sky, which is null ({s['sky_why']})")
    fh = (f"f{s['fhr_lo']:03d}" if not s["interp"]
          else f"f{s['fhr_lo']:03d}–f{s['fhr_hi']:03d} interp")
    return {"valid": lf.iso_z(s["valid"]), "t": s["t"], "feels": None, "dewpoint": None,
            "rh": None,
            "wind": {"dir_deg": None, "dir_txt": None, "speed": s["wind"], "gust": None},
            "pop": None, "precip_amt": None, "sky": s["sky"], "mslp": s["mslp"],
            "condition": lf.condition_from_sky(s["sky"]), "condition_raw": None,
            "t_spread": None, "interp": s["interp"], "source": f"model · {label} {fh}",
            "absent": absent}


def _daily(series: list[dict], run_dt: datetime, tz: str, lat: float, lon: float,
           label: str) -> list[dict]:
    """Local-day windows wholly inside the banked span. A day the run does not
    cover end to end (today, already begun before the run; the day f240 cuts
    through) is not a row — a max over half a day is not the day's high."""
    zone = ZoneInfo(tz)
    by_valid = {s["valid"]: s for s in series}
    end_span = series[-1]["valid"]
    d = run_dt.astimezone(zone).date()
    rows: list[dict] = []
    while len(rows) < DAILY_ROWS:
        start = datetime(d.year, d.month, d.day, tzinfo=zone).astimezone(UTC)
        nxt = d + timedelta(days=1)
        end = datetime(nxt.year, nxt.month, nxt.day, tzinfo=zone).astimezone(UTC)
        if end - timedelta(hours=1) > end_span:
            break
        if start >= run_dt:
            hours = [by_valid.get(start + timedelta(hours=k))
                     for k in range(int((end - start).total_seconds() // 3600))]
            if all(hours):
                rows.append(_day_row(d, hours, tz, lat, lon, label))
        d = nxt
    return rows


def _day_row(d: date, hours: list[dict], tz: str, lat: float, lon: float,
             label: str) -> dict:
    absent = ["pop: not banked", "precip_amt: not banked",
              "wind.dir_deg: not banked (wind10m is a speed)",
              "wind.dir_txt: not banked (wind10m is a speed)", "wind.gust: not banked"]
    ts = [h["t"] for h in hours]
    hi = max(ts) if None not in ts else None
    lo = min(ts) if None not in ts else None
    if hi is None:
        why = next(h["t_why"] for h in hours if h["t"] is None)
        absent += [f"hi: {why} inside the day", f"lo: {why} inside the day"]
    ws = [h["wind"] for h in hours]
    wind = max(ws) if None not in ws else None
    if wind is None:
        absent.append("wind.speed: not banked on run inside the day")
    skies = [h["sky"] for h in hours if h["sky"] is not None]
    sky = round(sum(skies) / len(skies), 2) if skies else None
    if sky is None:
        absent.append("sky: no daylight window with dswrf")
        absent.append("condition: from sky, which is null (no daylight window with dswrf)")
    sun = lf.sun_times(lat, lon, d, tz)
    absent += sun["absent"]
    return {"date": d.isoformat(), "hi": hi, "lo": lo, "pop": None, "precip_amt": None,
            "wind": {"dir_deg": None, "dir_txt": None, "speed": wind, "gust": None},
            "sky": sky, "condition": lf.condition_from_sky(sky),
            "sunrise": sun["sunrise"], "sunset": sun["sunset"],
            "source": (f"model · {label} f{hours[0]['h']:03d}–f{hours[-1]['h']:03d}"),
            "absent": absent}


def build(ladders: dict, run_dt: datetime, lat: float, lon: float, *, tz: str,
          tz_source: str, country: Optional[str], generated_at: datetime,
          fallback: Optional[dict], notes: list[str]) -> dict:
    """Ladders → `build_payload`'s keyword arguments."""
    label = run_label(run_dt)
    span = range(0, wp.LADDER_FHR_MAX + 1)
    series = _series(ladders, run_dt, lat, lon, span)
    daily = _daily(series, run_dt, tz, lat, lon, label)

    for p, lad in ladders.items():
        if lad["missing_header"]:
            notes.append(f"not banked on run: {lad['missing_header']}")
    t_fhrs = [f for f, v in ladders["t2m"]["values"].items() if v is not None]
    fhr_range = [0, max(t_fhrs)] if t_fhrs else None

    # D-09-25-10: from the hour containing `generated_at`, and nothing past the
    # last f-hour that banked a temperature — the arm invents no hour it has
    # no rung for.
    last = fhr_range[1] if fhr_range else -1
    hourly, hourly_note = lf.trim_to_now(
        [_hour_row(s, label) for s in series if s["h"] <= last], generated_at,
        HOURLY_ROWS)
    notes.append(hourly_note)

    s0 = series[0]
    now_row = _hour_row(s0, label)
    now = {k: now_row[k] for k in ("t", "feels", "dewpoint", "rh", "wind", "sky",
                                   "mslp", "condition", "condition_raw")}
    now["absent"] = [a for a in now_row["absent"]
                     if not a.startswith(("pop:", "precip_amt:", "t_spread:"))]
    now["valid"] = lf.iso_z(run_dt)
    # D-09-24-09: the label, never "observed".
    now["source"] = f"model · {label} f000"
    now["age_min"] = int((generated_at - run_dt).total_seconds() // 60)

    local_today = generated_at.astimezone(ZoneInfo(tz)).date()
    span_txt = (f"f000–f{fhr_range[1]:03d}" if fhr_range else "no t2m rungs")
    return {
        "place": {"lat": lat, "lon": lon, "tz": tz, "tz_source": tz_source,
                  "country": country},
        "now": now,
        "hourly": hourly,
        "daily": daily,
        "alerts": [],
        "sun": lf.sun_times(lat, lon, local_today, tz),
        "receipts": {
            "arm": lf.ARM_MODEL,
            "source": f"model · GFS {run_dt:%Y-%m-%d %H}Z · {CROP} · {span_txt}",
            "issued_at": lf.iso_z(run_dt),
            "run": wp.format_run(run_dt),
            "fhr_range": fhr_range,
            "station": None,
            "memo": None,
            "fallback": fallback,
            "outline_sha": lf.outline_sha(),
            "generated_at": lf.iso_z(generated_at),
            "notes": notes,
        },
    }


async def read(store: wp.SidecarStore,
               candidates: Callable[[], Awaitable[list[datetime]]],
               lat: float, lon: float,
               timings: Optional[lf.Timings] = None) -> tuple[datetime, dict]:
    """The model arm's I/O: the run, then the four ladders AT THE SAME TIME
    (D-09-25-28). Each ladder keeps its own 8-in-flight bound, so at most
    4 × LADDER_CONCURRENCY range GETs are in flight — the store's pool allows
    that many. `{p: await read_ladder(...) for p in PARAMS}` would be serial.
    The first failing param (in PARAMS order) raises, as the serial read did."""
    timings = timings if timings is not None else lf.Timings()
    if not store.configured():
        raise ModelArmError("weather value sidecar storage not configured")
    with timings.mark("model_run"):
        run_dt = await discover_run(store, candidates)
    with timings.mark("model_ladders"):
        got = await asyncio.gather(*(read_ladder(store, run_dt, p, lat, lon)
                                     for p in PARAMS), return_exceptions=True)
    for g in got:
        if isinstance(g, BaseException):
            raise g
    return run_dt, dict(zip(PARAMS, got))


async def answer(store: wp.SidecarStore,
                 candidates: Callable[[], Awaitable[list[datetime]]],
                 lat: float, lon: float, *, tz: str, tz_source: str,
                 country: Optional[str], generated_at: datetime,
                 fallback: Optional[dict], notes: list[str],
                 timings: Optional[lf.Timings] = None) -> dict:
    """`read`, then `build`: every caller outside the route is unchanged."""
    run_dt, ladders = await read(store, candidates, lat, lon, timings)
    return build(ladders, run_dt, lat, lon, tz=tz, tz_source=tz_source,
                 country=country, generated_at=generated_at, fallback=fallback,
                 notes=notes)
