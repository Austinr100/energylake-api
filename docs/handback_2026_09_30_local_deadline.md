# Handback — d091513 — Local answers in seconds, and "Today" is one row

**Spec:** pantry `docs/cc_spec_2026_09_30_local_deadline.md` (pantry main `4036774`).
**Repo / branch:** `energylake-api`, `claude/local-deadline-d091513`, cut from `origin/main` `3fb18a2` (#84).
**Discipline:** branch only. No PR, no merge, no migration, nothing under `.github/workflows/`. **Neon was not touched at all**: no query was run, and nothing in this lane needs the database.
**Suite:** **1989 passed** at the base, **2010 passed** on the branch. That is 21 new cells, 2 pins amended (T9f, N6), 1 fixture line added (`world` clears the new cell memo), 0 skipped.

**Headline, cold, the same US request (all memos empty, the store taking 10 s per GET):** main **10.03 s**, branch **2.53 s**.

## STOP-D — not triggered, with one finding for the architect

Every row of §0's table matches main `3fb18a2`:

| §0 claim | on main |
|---|---|
| `_LOCAL_DAILY_ROWS = 10`; `local_forecast()` runs `await _local_extend_days(...)`, which does `run_dt, ladders = await model_read`, with no `wait_for` | yes: `main.py:18913`, `:18933`, `:19037–19039` |
| `discover_run`: ledger SQL plus up to `RUN_PROBE_DEPTH = 4` header GETs; `RUN_MEMO_TTL_S = 300` | yes: `model_arm.py:70–71`, `:113–133`, `:160–177` |
| four ladders of 41 range GETs each at `LADDER_CONCURRENCY = 8` | yes: `weather_point.py:117` and `ladder_fhrs()` f000..f240 by 6 = 41; `model_arm.read` gathers the four |
| `SidecarStore(timeout = 10.0)`, no retry | yes: `weather_point.py:811`; `_fetch` makes one `client.get` with no retry |
| an httpx `ReadTimeout` is neither `PointError` nor `ModelArmError`, so it falls to the bare `except Exception` and prints its class | yes: `get_values` catches only `PointError` per rung; `read_ladder` and `_discover` catch only `PointError`; the result is `main.py:18941–18944` |
| NWS `CONNECT_TIMEOUT_S = 4.0`, `READ_TIMEOUT_S = 8.0`; points first, then three legs under `gather`; memo in-process | yes: `nws_arm.py:52–53`, `:183`, `:212`; `NwsClient._memo` is an instance dict |

**Finding (not a §0 table row, so not STOP-D):** §2's premise is out of date. It says the first reader after every 5 minutes pays discovery. On main that is no longer true: **D-09-25-29 (d091491) already made `discover_run` stale-while-revalidate and single-flight.** A memo up to `RUN_MEMO_MAX_AGE_S = 7 h` is served at once. Past `RUN_MEMO_TTL_S` one background refresh starts through `_start_refresh`/`_refresh_tasks`. A request waits only when there is no memo, or when the memo is older than 7 h (quoted below). The one §2 item missing on main was **the startup warm**, and that is the only §2 code on this branch. I did not rebuild the stale-while-revalidate logic. L6 was written as specified (ten concurrent readers) against the existing code, and it passes.

## Gate 0

### `local_forecast()` and `_local_extend_days`, in full

`main.py:18913–18954` (main `3fb18a2`):

```python
_LOCAL_DAILY_ROWS = 10


async def _local_extend_days(parts: dict, lat: float, lon: float, generated_at,
                             model_read, timings: "_lf.Timings") -> None:
    """Days 8–10 on the US arm (d091485 §2.3). NWS's forecast stops at 7 local
    days; the model arm's rows for the SAME place (built in NWS's tz, so the
    local dates line up) for the dates NWS does not cover are appended, up to
    10 in all. Each keeps its own `source: model · GFS <HH>Z …` (D-09-24-09
    per row) and `receipts.notes[]` names the range. If the model arm cannot
    answer, the NWS rows stand alone and the note says why — the US page never
    fails because of the model arm.

    `model_read` is the `model_arm.read` task the route started alongside NWS
    (D-09-25-28): the ladders were being read while NWS answered, and only the
    build waits for NWS's tz."""
    daily = parts["daily"]
    notes = parts["receipts"]["notes"]
    a = len(daily) + 1
    try:
        run_dt, ladders = await model_read
        with timings.mark("build"):
            model = _model_arm.build(
                ladders, run_dt, lat, lon, tz=parts["place"]["tz"], tz_source="nws",
                country="US", generated_at=generated_at, fallback=None, notes=[])
    except _model_arm.ModelArmError:
        notes.append(f"days {a}–{_LOCAL_DAILY_ROWS} unavailable: model arm 503")
        return
    except Exception as e:                      # the US page never fails on this
        notes.append(f"days {a}–{_LOCAL_DAILY_ROWS} unavailable: model arm "
                     f"{type(e).__name__}")
        return
    last = max((d["date"] for d in daily), default="")
    extra = [d for d in model["daily"] if d["date"] > last]
    extra = extra[:_LOCAL_DAILY_ROWS - len(daily)]
    if not extra:
        notes.append(f"days {a}–{_LOCAL_DAILY_ROWS} unavailable: model arm "
                     f"covers no date after {last}")
        return
    daily.extend(extra)
    label = _model_arm.run_label(run_dt)
    notes.append(f"days {a}–{len(daily)} from model · {label}")
```

`main.py:18984–19073` (main `3fb18a2`):

```python
@app.get("/api/local/forecast")
async def local_forecast(request: Request,
                         lat: Optional[str] = Query(None),
                         lon: Optional[str] = Query(None),
                         arm: Optional[str] = Query(None)):
    """The Local Weather forecast for one point, in one shape whichever arm
    answered. `?arm=model` forces the model arm anywhere; `?arm=nws` outside
    the US outline is a 400. Bad or missing lat/lon → 400 with the bounds."""
    bounds = {"lat": [-90, 90], "lon": [-180, 180], "lon_also_accepted": "(180, 360]"}
    try:
        flat = float(lat) if lat is not None else None
        flon = float(lon) if lon is not None else None
    except ValueError:
        return _local_400({"error": "lat/lon must be numbers", "bounds": bounds})
    if flat is None or flon is None:
        return _local_400({"error": "lat and lon are required", "bounds": bounds})
    if not (_math.isfinite(flat) and -90 <= flat <= 90):
        return _local_400({"error": "lat out of range", "lat": flat, "bounds": bounds})
    if not (_math.isfinite(flon) and -180 <= flon <= 360):
        return _local_400({"error": "lon out of range", "lon": flon, "bounds": bounds})
    notes: list[str] = []
    if flon > 180:
        notes.append(f"lon {flon:g} normalised to {flon - 360:g}")
        flon -= 360
    if arm not in (None, _lf.ARM_MODEL, _lf.ARM_NWS):
        return _local_400({"error": "arm must be 'model' or 'nws'", "arm": arm})

    in_us = _lf.in_outline(flat, flon)
    if arm == _lf.ARM_NWS and not in_us:
        return _local_400({"error": "arm=nws outside the US outline",
                           "lat": flat, "lon": flon, "outline_sha": _lf.outline_sha()})
    chosen = arm or (_lf.ARM_NWS if in_us else _lf.ARM_MODEL)
    generated_at = _lf.utcnow()
    timings = _lf.Timings()

    parts = None
    fallback = None
    tz_hint = None
    model_task = None
    if chosen == _lf.ARM_NWS:
        # D-09-25-28: the model read (days 8–10, or the whole answer if NWS
        # falls through) starts with the request, alongside NWS. ONE read.
        model_task = asyncio.create_task(_model_arm.read(
            _get_weather_store(), _local_gfs_run_candidates, flat, flon, timings))
        try:
            try:
                bundle = await _get_local_nws_client().fetch(flat, flon, timings)
                with timings.mark("build"):
                    parts = _nws_arm.build(bundle, flat, flon, generated_at, notes)
            except _nws_arm.NwsError as e:
                fallback = {"from": "nws", "reason": e.reason}
                tz_hint = e.tz
            if parts is not None:
                if len(parts["daily"]) < _LOCAL_DAILY_ROWS:
                    await _local_extend_days(parts, flat, flon, generated_at,
                                             model_task, timings)
                else:
                    _local_discard(model_task)
        except BaseException:
            _local_discard(model_task)
            raise
    if parts is None:
        tz, tz_source = (tz_hint, "nws") if tz_hint else (_lf.nominal_tz(flon), "nominal")
        try:
            if model_task is not None:
                run_dt, ladders = await model_task
            else:
                run_dt, ladders = await _model_arm.read(
                    _get_weather_store(), _local_gfs_run_candidates, flat, flon, timings)
            with timings.mark("build"):
                parts = _model_arm.build(
                    ladders, run_dt, flat, flon, tz=tz, tz_source=tz_source,
                    country="US" if in_us else None, generated_at=generated_at,
                    fallback=fallback, notes=notes)
        except _model_arm.ModelArmError as e:
            return JSONResponse(status_code=503, content={"detail": {
                "error": f"model arm unavailable: {e.reason}", **e.detail,
                "fallback": fallback}}, headers=_local_timing_headers(request, timings))

    with timings.mark("build"):
        payload = _lf.build_payload(**parts)
        # D-09-25-15: the tag is the content (minus generated_at/memo), so a new
        # observation, alert, hourly trim or model run is a 200, never a 304.
        tag = _lf.content_etag(payload)
    rc = payload["receipts"]
    headers = {"Cache-Control": _LOCAL_CACHE_CONTROL[rc["arm"]], "ETag": tag,
               **_local_timing_headers(request, timings)}
    if _enso.etag_matches(request.headers.get("if-none-match"), tag):
        return Response(status_code=304, headers=headers)
    return JSONResponse(content=payload, headers=headers)
```

### `model_arm.read`, `discover_run`, the ladder loop, the memos

`model_arm.py:94–104` (main `3fb18a2`):

```python
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
```

`model_arm.py:113–177` (main `3fb18a2`):

```python
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
```

`model_arm.py:180–210` (main `3fb18a2`):

```python
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
```

`model_arm.py:510–530` (main `3fb18a2`):

```python
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
```

The ladder loop sits under `read_ladder`, in `weather_point.ladder` (`:1037`): one header, then `store.get_values`, which gives one range GET per rung, at most 8 in flight:

`weather_point.py:980–998` (main `3fb18a2`):

```python
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
```

**Per-cell memo: none in `model_arm` on main.** Its only memo is `_run_memo` ("the one memo this arm keeps; the store keeps the rest"). The store does keep a value LRU (`VALUE_CACHE_MAX = 4096` values, keyed by `(value key, offset)`). A repeat read of a cell whose 164 values have not been evicted costs no range GETs. But nothing in the model arm names or bounds that, and 4096 values is about 25 cells. So §1.3's condition holds, and a per-cell memo was added in the house idiom (see below).

### `SidecarStore`'s timeout and its exception types

`weather_point.py:808–812` (main `3fb18a2`):

```python

    def __init__(self, *, base_url: str = "", endpoint: str = "",
                 bucket: str = "", access_key: str = "", secret_key: str = "",
                 region: str = "auto", timeout: float = 10.0,
                 transport=None):
```

`weather_point.py:842–853` (main `3fb18a2`):

```python
        import httpx  # local: the rest of the API must not need it at import

        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                # d091491 D-09-25-28: the model arm reads its four ladders at
                # once, each 8 in flight — 32 range GETs, 16 kept alive.
                limits=httpx.Limits(
                    max_connections=LADDER_CONCURRENCY * 4,
                    max_keepalive_connections=LADDER_CONCURRENCY * 2,
                ),
            )
```

`weather_point.py:885–914` (main `3fb18a2`):

```python
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
```

The store raises only `PointError(status, detail)` itself: 404 for a missing header or values, 502 for a bad status, bad JSON, or an unhonoured range. **Anything httpx raises passes through untouched**: `ReadTimeout`, `ConnectTimeout`, `ConnectError`, `PoolTimeout`, and the rest of `httpx.TransportError`. No retry.

### `nws_arm.daily_rows` and `_daily_row`

`nws_arm.py:321–367` (main `3fb18a2`):

```python
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
```

### The cold reproduction

`scripts/repro_local_cold_store.py` (on the branch). It sends one US request (the LAX fixture point) through the app with every memo empty. NWS is answered instantly by the test fixtures. The sidecar store's every GET sleeps 10 s. **By default the store then raises `httpx.ReadTimeout`**, which is what production's `SidecarStore(timeout=10.0)` does against a store slower than 10 s. With `REPRO_STORE=answer` the store answers after the sleep instead, as the spec's wording has it. Main was run from a worktree at `3fb18a2`, and the branch from its head. "Local server" here means the ASGI app in-process (Starlette `TestClient`). A uvicorn process would need `lifespan`, which requires `NEON_DATABASE_URL` and opens a pool, and this lane does not touch Neon.

| store | build | wall | daily | note | `Server-Timing` (model legs, total) |
|---|---|---|---|---|---|
| 10 s then `ReadTimeout` | **main** | **10.03 s** | 7 | `days 8–10 unavailable: model arm ReadTimeout` | `model_run 10010.9`, `total 10012.9` |
| 10 s then `ReadTimeout` | **branch** | **2.53 s** | 7 | `days 8–10 pending: model arm still reading (deadline 2.5 s)` | `model_wait 2502.9`, `total 2507.0` |
| 10 s then answers | main | 90.15 s | 10 | `days 8–10 from model · GFS 12Z` | `model_run 10013.1`, `model_ladders 80084.3`, `total 90118.8` |
| 10 s then answers | branch | 2.52 s | 7 | `days 8–10 pending: …` | `model_wait 2502.9`, `total 2507.1` |

The first row reproduces the captain's footer exactly: `model arm ReadTimeout`, with the response held for the store's 10 s.

## What was built

**§1 — the deadline** (`main.py`, `model_arm.py`, `local_forecast.py`)
- `MODEL_EXTEND_DEADLINE_S = 2.5`. `_local_extend_days` waits on `asyncio.wait_for(asyncio.shield(model_read), MODEL_EXTEND_DEADLINE_S)` inside `timings.mark("model_wait")`. The clock starts at that call, which is after NWS's answer is built.
- When the deadline passes, the note reads `days {a}–10 pending: model arm still reading (deadline 2.5 s)`. The task goes into `_local_background`, a strong reference so the loop's weak one is not the only one. A done-callback retrieves its outcome, so a late failure is never "never retrieved".
- **The per-cell memo** is `model_arm._cell_memo`, keyed by `(model, run_dt, round(lat,4), round(lon,4))` and holding `(monotonic, run-memo stamp, ladders)`. The TTL is `CELL_MEMO_TTL_S = RUN_MEMO_TTL_S`. An entry is also dead once the run memo it was read under has been replaced, so a ladder never outlives the discovery that chose its run. Up to 256 entries are kept, and the oldest is evicted first. Only a completed read is memoised.
- **Named timeouts.** `model_arm._named_store_error` maps an `httpx.TimeoutException` to `ModelArmError("store_timeout")` and any other `httpx.TransportError` to `ModelArmError("store_unreachable")`. It covers both `_discover` (the header probe) and `read_ladder`, with `{"transport": "<class>"}` in `detail`. US note: `days a–10 unavailable: model arm store timeout` (or `store unreachable`). Outside the US the route's existing `ModelArmError` path answers 503 `model arm unavailable: store_timeout`. On main that case raised through the route as a 500. Other `ModelArmError`s still say `model arm 503`. The bare `except Exception` still prints the class.
- `Server-Timing` gains `model_wait`, between `model_ladders` and `build` in `TIMING_NAMES`. It appears only when the US route actually waited on the model arm.

**§2 — run discovery**: `_local_warm_run()` is started from `lifespan` as a detached task and cancelled on shutdown. It does nothing if the store is unconfigured. It never raises: a failure logs `[[LOCAL_RUN_WARM_FAILED]] <reason>` and boot carries on as before. The stale-while-revalidate logic was already on main (see STOP-D above).

**§3 — one row per local date** (`nws_arm.py`): if the first period of a pair is a night and the next period is a day period **with the same local date**, the night joins the day's row. `hi` comes from the day. `lo` is `min(early night, following night)` and `lo_period` holds that period's `name`. On a tie the earlier period ("Overnight") wins. Nothing is averaged. An evening read (`Tonight`, then tomorrow) is unchanged. `_local_extend_days` already indexes from `len(daily)` and appends only dates after the last NWS date, so it extends from the merged length with no change.

## Decisions the architect should check

1. **`reason: "deadline"` is not in the body.** `receipts`' keys are pinned (`RECEIPT_KEYS`, T9f, D-09-25-15), and nothing in the spec adds a field for it. So `_local_extend_days` returns the absence it stated, `{"days": [a, 10], "reason": "deadline" | "store_timeout" | "store_unreachable" | "model_arm" | "error" | "no_dates"}`, and the cells assert on that. The body carries the reason in words, in the note. If the page needs it machine-readable, that is a receipts contract change to rule on.
2. **`lo_period` is a contract change**: `DAILY_KEYS` gains `lo_period` after `lo`, on every row, because `build_payload` refuses keys off the contract. NWS rows carry the NWS name of the period that gave `lo` (`"Tonight"`, `"Friday Night"`, `"Overnight"`). Model rows carry `null` with `lo_period: model rows are not built from NWS periods`, per the null-has-a-reason rule. T9f's pin is amended. N6 now proves the LAX body is **main's exact bytes** once `lo_period` and its reason lines are stripped. Every point's ETag changes once, because the content changed. For the dashboard it is an additive key.
3. **`model_wait` can read a few ms over 2500.** The measured wait is the deadline plus event-loop wake-up: 2502.9 ms in the reproduction above, and 2500–2503 in the cells. §5 accepts `model_wait ≤ 2500`. I did not clamp the number, because that would print a wait that did not happen. The architect decides: read §5 as ≤ 2500 + ε, or rule a clamp.
4. **The merged row's `pop` is the max over all three periods** (Overnight, Today, Tonight). The overnight chance is today's too. The spec names only `hi` and `lo` for the merge.
5. **No in-flight join.** A second request that arrives *while* a background read is still running starts its own read. The memo serves only completed reads, which is what L2 specifies. A join would need a shared task, reference counting, or giving up the cancel `_local_discard` does when NWS has 10 rows. I did not build one.
6. **L1 runs on a 0.2 s deadline** with the app on one event loop (httpx over ASGI). `TestClient` closes its loop after each request, which would kill the background task. A second L1 cell holds the real 2.5 s constant on a fake `wait_for` and checks the note says `2.5 s` and the task is left running.

## Cells and reds

All in `tests/test_local_forecast.py`, section "L1..L9 are d091513's".

| cell | test(s) | asserts |
|---|---|---|
| L0 | `test_L0_the_deadline_is_two_and_a_half_seconds` | the constant is 2.5; `model_wait` is a ruled timing name |
| L1 | `test_L1_a_slow_model_arm_misses_the_deadline_and_says_so`, `test_L1_the_note_names_the_real_deadline` | 7 NWS rows only; `pending … (deadline …)` note; returned absence `{"days": [8, 10], "reason": "deadline"}`; `model_wait` is at least the deadline and at most the deadline + 60 ms; the task is not cancelled |
| L2 | `test_L2_the_read_finishes_behind_the_response_and_serves_the_next` | the background task is held, then finishes; the second request has days 8–10 and the store's GET count is **unchanged** |
| L3 | `test_L3_a_store_timeout_is_named_not_a_class` ×6 (ReadTimeout / ConnectTimeout / ConnectError × discovery / ladders), `test_L3_anything_else_still_prints_its_class` | the note says `model arm store timeout` / `store unreachable`; the reason is `store_timeout` / `store_unreachable`; no note matches `Timeout\|Error\|Exception`; Vancouver gets a 503 `model arm unavailable: <reason>`; a `KeyError` still prints `KeyError` |
| L4 | `test_L4_a_model_arm_inside_the_deadline_is_todays_answer` | with the real 2.5 s and a 5 ms store: days 8–10 as in T15; no `pending`; returns `None` |
| L5 | `test_L5_outside_the_us_there_is_no_deadline` | with a 0.01 s deadline and a 20 ms store, Vancouver still answers in full; no `model_wait`; `_local_extend_days` is never called |
| L6 | `test_L6_ten_readers_of_a_stale_memo_start_one_refresh`, `test_L6_boot_warms_the_run_and_never_fails`, `test_L6_boot_warm_is_in_the_lifespan` | ten concurrent readers at TTL+1 s all get the memoised run at once, with **exactly one** ledger call; the warm fills the memo, and a timing-out store only logs |
| L7 | `test_L7_overnight_and_today_are_one_row`, `test_L7_the_lower_night_wins_either_way`, `test_L7_the_route_serves_one_today_and_extends_from_the_merged_length` | Overnight 62 / Today 93 / Tonight 64 gives one row: `hi` 93 °F, `lo` 62 °F, `lo_period` `"Overnight"`, 7 unique dates; lows reversed gives `"Tonight"`; through the route, 10 unique dates with days 8–10 from the model |
| L8 | `test_L8_an_evening_read_keeps_its_night_only_row` | `Tonight, Friday, …`: the night-only row stands, `hi` null with `hi: day period elapsed`, `lo_period` `"Tonight"` |
| L9 | `test_L9_a_daytime_read_is_mains_rows_plus_lo_period` | `This Afternoon, Tonight, …`: identical to `tests/fixtures/nws/daily_rows_daytime.main.json` (main's own output, captured from `3fb18a2`) once `lo_period` is removed, and `lo_period` is each night's name |

| red | one edit | cell that went red |
|---|---|---|
| R1 | `run_dt, ladders = await model_read` (no deadline) | **L1**: both cells (`TimeoutError` at the 5 s bound on each request; the store never answers) |
| R2 | `wait_for(model_read, …)` without `shield` | **L2**: `CancelledError`; the task was cancelled |
| R3 | merge condition short-circuited to `False` (the overnight row kept separate) | **L7**: 8 rows, `2026-09-25` twice; the lower-night cell also red |
| R4 | merge whenever a night is followed by a day, with no date check | **L8**: `Tonight` folded into Friday, so row 0 is dated `2026-09-26` |

Each red was applied to a clean tree, the named cell run, and the file restored with `git checkout`. The full suite was green after each one.

## Files

`main.py` (deadline, background set, boot warm), `model_arm.py` (named store errors, cell memo, `lo_period` on model rows), `nws_arm.py` (§3), `local_forecast.py` (`model_wait`, `lo_period` in `DAILY_KEYS`), `tests/test_local_forecast.py`, `tests/fixtures/nws/daily_rows_daytime.main.json` and its README entry, `scripts/repro_local_cold_store.py`.

## For §5 (after merge and deploy)

The three cold US points should show `total` of about 2.5 s plus NWS (well under 5 s) and `model_wait` of about 2500–2503 (see decision 3). Read again within 5 minutes, they should show days 8–10 and `total` of about 1 s or less, because the cell memo answers with `model_ladders` near 0. A West Coast point between 07Z and 12Z should have unique dates and today's row carrying `hi` and `lo_period`.
