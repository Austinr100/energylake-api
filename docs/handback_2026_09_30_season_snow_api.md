# Handback: 2026-09-30, d091520, snowpack joins the season API

**Spec:** pantry `docs/cc_spec_2026_09_30_season_snow_api.md` (Season to date, lane 4, the API half). **Branch:** `claude/season-snow-api-d091520`, cut from api main `5fce60b` (the d091513 merge). No PR, no merge, no migration, nothing under `.github/workflows/`. Read-only against Neon.

**What's on the branch:**
- `season.py`:
  - a third kind of area, `snow:{basin}`, with its six basins and their labels pinned;
  - the variable `swe`, served in **level** mode;
  - `season.mode`, `years[].peak`/`peak_md`, and top-level `peak`, `peak_absence` and `source`;
  - `build_board`;
  - `SNOW_SQL` and `AREAS_SNOW_SQL`.
- `main.py`:
  - the season fetch moves into `_season_payload(area, var, classifier)`, one memo shared by `/season` and the board;
  - `/areas` gains one GROUP BY;
  - new route: **`GET /api/weather/snow/board`**.
- `tests/test_season_snow.py`: the new cells N1–N11, plus a size probe.
- `tests/test_season.py`: only N12's permitted edits (listed below).

## STOP-D: clear

I re-measured on Neon `fancy-block-96153928` / `br-dark-morning-ajosxafs` at 12:5xZ, read-only. Every figure in §0 reproduces.

| check | spec §0 | found |
|---|---|---|
| dataset | `snow_basin_index_daily` | ✓ six series, all `{basin}.SWE_PCT` |
| rows, total | 93,386 | 15,438 + 15,705 + 15,509 + 15,497 + 15,619 + 15,618 = **93,386** ✓ |
| `col_above_grand_coulee` | 15,438 · 1983-09-30 → 2026-09-28 · 41 (WY1985–2025) | ✓ · ✓ · 41, WY1985–2025 ✓ |
| `col_mid_tributaries` | 15,705 · 1983-09-30 → 2026-09-28 · 42 (WY1984–2025) | ✓ · ✓ · 42, WY1984–2025 ✓ |
| `snake_upper` | 15,509 · 1982-09-30 → 2026-09-28 · 40 (WY1986–2025) | ✓ · ✓ · 40, WY1986–2025 ✓ |
| `snake_lower` | 15,497 · 1983-09-13 → 2026-09-28 · 41 (WY1985–2025) | ✓ · ✓ · 41, WY1985–2025 ✓ |
| `snake` | 15,619 · 1982-09-30 → 2026-09-28 · 41 (WY1985–2025) | ✓ · ✓ · 41, WY1985–2025 ✓ |
| `columbia_above_the_dalles` | 15,618 · 1983-09-13 → 2026-09-28 · 41 (WY1985–2025) | ✓ · ✓ · 41, WY1985–2025 ✓ |
| incomplete water years since WY1990 | none | none, in all six ✓ |
| test vector (18 cells, 1997/2015/2026-04-01) | §0 table | all 18 exact (numeric(·,6): `153.980000` …) ✓ |
| WY2026 peaks | 78.1 Mar 17 · 46.4 Mar 13 · 61.3 Mar 14 · 69.3 Mar 16 · 63.9 Mar 14 · 63.8 Mar 16 | 78.12 03-17 · 46.44 03-13 · 61.30 03-14 · 69.29 03-16 · 63.89 03-14 · 63.82 03-16 ✓ |
| labels | pantry `snow/basins.py` `LABELS` | read at pantry main `cec4d49`; pinned verbatim ✓ |
| `timeseries_values.meta` | (jsonb) | `jsonb` ✓ |

**One banked row's `meta`, verbatim.** This is `columbia_above_the_dalles.SWE_PCT` at `2026-09-28T00:00:00Z`, the frontier, `value 0.070000`:
```json
{"unit": "pct_of_normal_peak", "basin": "columbia_above_the_dalles", "n_index": 163, "n_reporting": 155, "normals_sha256": "2f85c863926809b6611cfa70786cd168e1abb98bdba00e79093bf8cd5afa3040", "source_dataset": "snotel_columbia_daily", "normals_version": "v1"}
```
It carries a `basin` key that §0 doesn't list, alongside the five it does. The API reads only `n_index`, `n_reporting` and `normals_version`.

## Gate 0: read and quoted (api main `5fce60b`, before this lane)

**`season.py` `_walk`**
```python
def _walk(var: str, s: int, daily: Mapping[date, Optional[float]], limit: date,
          slots: Mapping[str, int]) -> _Walk:
    """Walk season `s` by calendar date from its first day through `limit`.

    Every day is looked up; nothing is iterated by row. The running sum is
    written to each day's slot until the first missing day, after which the
    slot stays None. Feb 29 folds into the Feb 28 slot; if Feb 29 is the first
    missing day, the Feb 28 slot it would have completed is withdrawn too."""
    start, end = bounds(var, s)
    w = _Walk()
    w.s = s
    w.values = [None] * len(slots)
    w.days_complete = 0
    w.days_in_window = (end - start).days + 1
    w.first_missing, w.mode, w.through = None, None, None
    total = 0.0
    d = start
    while d <= limit:
        v = daily.get(d, _NO_ROW)
        slot = slots["02-28" if (d.month, d.day) == (2, 29) else f"{d.month:02d}-{d.day:02d}"]
        if v is _NO_ROW or v is None:
            if w.first_missing is None:
                w.first_missing = d
                w.mode = ABSENT if v is _NO_ROW else INCOMPLETE
                if w.values[slot] is not None:          # Feb 29 after a written Feb 28
                    w.values[slot] = None
                    w.through = d - timedelta(days=2) if d - timedelta(days=2) >= start else None
        else:
            w.days_complete += 1
            if w.first_missing is None:
                total += float(v)
                w.values[slot] = total
                w.through = d
        d += timedelta(days=1)
    return w
```

**`season.py` `build_season`**
```python
def build_season(area: str, var: str, daily: Mapping[date, Optional[float]], *,
                 classifier: str, catalog_version: Optional[str],
                 developing: Optional[Mapping], bins: Sequence[Mapping],
                 oni_last_centre: Optional[tuple[int, int]] = None) -> dict:
    """The /api/weather/season body, keys in contract order.

    `daily` maps each calendar date that HAS A ROW to its value, None where the
    row carries no usable value (NULL, or basis_complete false). A date with no
    row is simply not a key. `bins` are enso_year_bins rows for `classifier`.
    """
    slots = _slot_index(var)
    ax = axis(var)
    ndays = len(ax)

    valued = [d for d, v in daily.items() if v is not None]
    frontier = max(valued) if valued else None

    # Seasons with any data (a row, valued or not), not starting after the frontier.
    seasons = sorted({s for d in daily for s in [season_of(var, d)]
                      if s is not None and (frontier is None or bounds(var, s)[0] <= frontier)})

    cur = None
    if frontier is not None:
        s = season_of(var, frontier)
        if s is not None and frontier < bounds(var, s)[1]:
            cur = s

    walks: dict[int, _Walk] = {}
    for s in seasons:
        limit = frontier if s == cur else bounds(var, s)[1]
        walks[s] = _walk(var, s, daily, limit, slots)
    past = [s for s in seasons if s != cur]

    # ── base ────────────────────────────────────────────────────────────────
    base = [s for s in past if _complete(walks[s])]
    excluded = [{"season": label(var, s), "days_complete": walks[s].days_complete,
                 "days_in_window": walks[s].days_in_window,
                 "first_missing": walks[s].first_missing.isoformat()
                 if walks[s].first_missing else None}
                for s in past if not _complete(walks[s])]
    B = np.array([walks[s].values for s in base], dtype=np.float64).reshape(len(base), ndays)
    n = len(base)

    if n >= CONE_MIN_N:
        pct = cone(B)
        percentiles = {"method": "linear (type 7)", **{k: _rv(v) for k, v in pct.items()}}
        percentiles_absence = None
        p50 = pct["p50"]
    else:
        percentiles, p50 = None, None
        percentiles_absence = {"reason": "short_record", "n": n}

    # ── normal ──────────────────────────────────────────────────────────────
    lo, hi = NORMAL_WINDOW
    normal_idx = [i for i, s in enumerate(base) if lo <= label_year(var, s) <= hi]
    if len(normal_idx) >= NORMAL_MIN_N:
        normal = {"window": f"{lo}-{hi}", "n": len(normal_idx),
                  "values": _rv(B[normal_idx].mean(axis=0))}
        normal_absence = None
    else:
        normal = None
        normal_absence = {"reason": "short_window", "n": len(normal_idx)}

    # ── five-year band: the five most recent COMPLETE seasons ───────────────
    five = sorted(base)[-FIVE_YEAR_N:]
    if len(five) == FIVE_YEAR_N:
        F = np.array([walks[s].values for s in five], dtype=np.float64)
        five_mean = F.mean(axis=0)
        five_year = {"seasons": [label(var, s) for s in five], "mean": _rv(five_mean),
                     "min": _rv(F.min(axis=0)), "max": _rv(F.max(axis=0))}
        five_year_absence = None
    else:
        five_mean, five_year = None, None
        five_year_absence = {"reason": "fewer_than_five", "n": len(five)}

    # ── this season / last season ───────────────────────────────────────────
    if cur is not None:
        this_season = _season_block(var, walks[cur], current=True)
        this_season_absence = None
        last_s = max(past) if past else None
    else:
        this_season = None
        this_season_absence = {"reason": "between_seasons",
                               "frontier": frontier.isoformat() if frontier else None}
        last_s = max(past) if past else None
    if last_s is not None:
        last_season = _season_block(var, walks[last_s], current=False)
        last_season_absence = None
    else:
        last_season, last_season_absence = None, {"reason": "no_prior_season"}

    # ── ENSO ────────────────────────────────────────────────────────────────
    bins_by_year = {int(b["enso_year"]): b for b in bins}
    opened = open_years(developing, oni_last_centre,
                        set(bins_by_year) | {enso_year(var, s) for s in seasons})
    klass = {s: classify(enso_year(var, s), bins_by_year, opened) for s in seasons}

    cat_medians: dict[str, Optional[np.ndarray]] = {}
    categories = {}
    for c in CATEGORIES:
        idx = [i for i, s in enumerate(base) if klass[s][0] and c in klass[s][0]]
        block = {"seasons": [label(var, base[i]) for i in idx], "n": len(idx)}
        if len(idx) >= CATEGORY_MIN_N:
            med = np.median(B[idx], axis=0)
            cat_medians[c] = med
            block["median"] = _rv(med)
        else:
            cat_medians[c] = None
            block["median"] = None
            block["absence"] = {"reason": "small_n", "n": len(idx)}
        categories[c] = block
    enso = {"classifier": classifier, "catalog_version": catalog_version,
            "mapping": MAPPING[var], "developing": developing, "categories": categories}

    # ── readout ─────────────────────────────────────────────────────────────
    day: Optional[int] = None          # the like-for-like day for years[].to_date
    readout, readout_absence = None, None
    if cur is not None:
        w = walks[cur]
        if w.through is not None:
            day = slots["02-28" if (w.through.month, w.through.day) == (2, 29)
                        else f"{w.through.month:02d}-{w.through.day:02d}"]
        if w.gap or day is None:
            readout_absence = _gap_absence(w) if w.gap else {"reason": "no_data"}
            value = None
        else:
            value = w.values[day]
    elif last_s is not None:
        w = walks[last_s]
        day = ndays - 1
        if _complete(w):
            value = w.values[day]
        else:
            readout_absence = _gap_absence(w) if w.gap else {"reason": "incomplete"}
            value = None
    else:
        value = None
        readout_absence = {"reason": "no_data"}

    if value is not None:
        median = float(p50[day]) if p50 is not None else None
        vs_cat = {c: (_r(value - float(cat_medians[c][day]))
                      if cat_medians[c] is not None else None) for c in CATEGORIES}
        readout = {
            "day": day,
            "value": _r(value),
            "median": _r(median),
            "pct_of_median": (_r(100.0 * value / median)
                              if median is not None and median != 0 else None),
            # The same n >= 30 gate as the cone: no rank against a short record.
            "percentile": _r(mid_rank(value, B[:, day].tolist())) if p50 is not None else None,
            "vs_five_year": _r(value - float(five_mean[day])) if five_mean is not None else None,
            "vs_category": vs_cat,
        }

    # ── years / curves ──────────────────────────────────────────────────────
    years = []
    for s in seasons:
        w = walks[s]
        cats, why = klass[s]
        years.append({
            "season": label(var, s), "enso_year": enso_year(var, s),
            "categories": cats, "category_absence": why,
            "complete": s != cur and _complete(w),
            "to_date": _r(w.values[day]) if day is not None else None,
            "final": _r(w.values[-1]) if s != cur and _complete(w) else None,
        })
    curves = {label(var, s): _rv(walks[s].values) for s in base
              if enso_year(var, s) in bins_by_year and enso_year(var, s) not in opened}

    return {
        "area": area, "var": var, "units": units(var),
        "season": season_meta(var),
        "frontier": frontier.isoformat() if frontier else None,
        "axis": ax,
        "base": {"rule": "every complete season, full record, excluding the current season",
                 "seasons": [label(var, s) for s in base], "n": n, "excluded": excluded},
        "percentiles": percentiles, "percentiles_absence": percentiles_absence,
        "normal": normal, "normal_absence": normal_absence,
        "five_year": five_year, "five_year_absence": five_year_absence,
        "this_season": this_season, "this_season_absence": this_season_absence,
        "last_season": last_season, "last_season_absence": last_season_absence,
        "enso": enso,
        "years": years,
        "curves": curves,
        "readout": readout, "readout_absence": readout_absence,
    }
```

**`season.py` `RESPONSE_KEYS`**
```python
# Contract order (§1's table; each `*_absence` rides right after its sibling).
RESPONSE_KEYS = ("area", "var", "units", "season", "frontier", "axis", "base",
                 "percentiles", "percentiles_absence", "normal", "normal_absence",
                 "five_year", "five_year_absence", "this_season", "this_season_absence",
                 "last_season", "last_season_absence", "enso", "years", "curves",
                 "readout", "readout_absence")
```

**`season.py` `area_vocabulary`, `vars_for`**
```python
def area_vocabulary(metadata_stations: Sequence[Mapping]) -> list[str]:
    return ([f"station:{sid}" for sid, _ in station_order(metadata_stations)]
            + [f"lwt:{ba}" for ba in LWT_BAS])


def vars_for(area: str) -> tuple[str, ...]:
    return VARS if area.startswith("station:") else ("hdd", "cdd")
```

**`main.py`: the memo and the two season routes**
```python
import season as _season

_SEASON_MEMO_TTL = 900.0
_SEASON_AREAS_MEMO_TTL = 3600.0
_SEASON_MEMO_MAX = 128
_season_cache: dict[tuple[str, str, str], tuple[float, dict]] = {}
_season_areas_cache: dict[str, tuple[float, dict]] = {}
# classifier -> the enso_indices series whose newest centre month feeds the
# pantry's open-year rule.
_SEASON_ONI_SERIES = {"cpc_oni": "oni", "roni": "roni"}


@app.get("/api/weather/season/areas")
async def weather_season_areas():
    """Every area the season API serves — 21 GHCNd stations (N→S by
    station_metadata.json, then the four it does not carry, labelled by id) and
    17 LWT load regions — with each var's season, first season and count of
    complete seasons. Memoised 1 h; DB unavailable → 503."""
    assert _pool is not None
    now_mono = time.monotonic()
    cached = _season_areas_cache.get("areas")
    if cached is not None and (now_mono - cached[0]) < _SEASON_AREAS_MEMO_TTL:
        payload = cached[1]
    else:
        counts: dict[tuple[str, str], dict[int, int]] = defaultdict(dict)
        try:
            async with _pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(_season.AREAS_PRECIP_SQL)
                    for r in await cur.fetchall():
                        counts[(f"station:{r['id']}", "precip")][int(r["s"])] = int(r["n"])
                    await cur.execute(_season.AREAS_STATION_DD_SQL)
                    for r in await cur.fetchall():
                        counts[(f"station:{r['id']}", r["var"])][int(r["s"])] = int(r["n"])
                    await cur.execute(_season.AREAS_LWT_SQL, {"d": _season.LWT_DATASET})
                    for r in await cur.fetchall():
                        counts[(f"lwt:{r['id']}", r["var"])][int(r["s"])] = int(r["n"])
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"db unavailable: {e}")
        payload = _season.build_areas(_WEATHER_STATIONS, counts)
        _season_areas_cache["areas"] = (now_mono, payload)
    return JSONResponse(content=payload,
                        headers={"Cache-Control": f"max-age={int(_SEASON_AREAS_MEMO_TTL)}"})


@app.get("/api/weather/season")
async def weather_season(area: Optional[str] = Query(None),
                         var: Optional[str] = Query(None),
                         classifier: Optional[str] = Query(None)):
    """Season to date for one area × var (`precip` = water year, stations only;
    `hdd` = Nov–Mar; `cdd` = May–Sep): the full-record cone, the 1991–2020
    normal, the five-year band, this and last season, ENSO-category medians and
    the readout. Every number the page draws is here. Unknown area / var /
    classifier, or precip on an lwt: area → 400 naming the vocabulary; no ENSO
    catalog banked → 503; DB unavailable → 503. Memoised 15 min."""
    try:
        area, var, classifier = _season.parse_request(area, var, classifier,
                                                      _WEATHER_STATIONS)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    assert _pool is not None

    key = (area, var, classifier)
    now_mono = time.monotonic()
    cached = _season_cache.get(key)
    if cached is not None and (now_mono - cached[0]) < _SEASON_MEMO_TTL:
        payload = cached[1]
    else:
        kind, ident = area.split(":", 1)
        oni_d, oni_s = _enso_idx.INDICES[_SEASON_ONI_SERIES[classifier]]
        try:
            async with _pool.connection() as conn:
                async with conn.cursor() as cur:
                    if kind == "lwt":
                        await cur.execute(_season.LWT_SQL, {"d": _season.LWT_DATASET,
                                                            "s": f"{ident}.{var.upper()}"})
                        daily = _season.daily_from_rows(var, await cur.fetchall())
                    elif var == "precip":
                        await cur.execute(_season.PRECIP_SQL, {"sid": ident})
                        daily = _season.daily_from_rows(var, await cur.fetchall())
                    else:
                        await cur.execute(_season.STATION_DD_SQL, {"sid": ident})
                        daily = _season.daily_from_rows(var, await cur.fetchall(),
                                                        station_dd=True)
                    await cur.execute(_enso.RUN_SQL, {"c": classifier})
                    run = await cur.fetchone()
                    bins = []
                    if run is not None:
                        await cur.execute(_enso.YEAR_BINS_SQL,
                                          {"c": classifier, "v": run["catalog_version"]})
                        bins = await cur.fetchall()
                    await cur.execute(_season.ONI_LAST_SQL, {"d": oni_d, "s": oni_s})
                    oni_row = await cur.fetchone()
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"db unavailable: {e}")
        if run is None or not bins:
            raise HTTPException(status_code=503,
                                detail=f"no ENSO catalog banked for {classifier}")
        oni_ts = oni_row["ts"] if oni_row else None
        payload = _season.build_season(
            area, var, daily, classifier=classifier,
            catalog_version=run["catalog_version"], developing=run["developing"],
            bins=bins,
            oni_last_centre=(oni_ts.year, oni_ts.month) if oni_ts is not None else None)
        _season_cache[key] = (now_mono, payload)
        while len(_season_cache) > _SEASON_MEMO_MAX:
            del _season_cache[min(_season_cache, key=lambda k: _season_cache[k][0])]
    # JSONResponse (plain json.dumps), not FastAPI's jsonable_encoder: the
    # encoder would quietly turn a stray Decimal into a float and hide a missing
    # ::float8 cast. enso_catalog.build_payload refuses one by name first.
    return JSONResponse(content=payload, headers=headers)
```

**What Gate 0 settled:**
- The walk is one loop that writes a running `total`.
- A level can't reuse that loop by flag without muddying both paths. It gets its own walk, `_walk_level`, called from `_walk` after the shared setup, so `_walk` stays the single entry point.
- `vars_for` split on `station:` versus everything else. It now keys on the area's kind.
- The route's fetch body moved verbatim into `_season_payload`, so the board can share the memo key `(area, var, classifier)`.

## What changed, rule by rule (§1)

| § rule | where |
|---|---|
| `snow:{basin}`, six, in the §1 order; labels pinned | `SNOW_BASINS`, `SNOW_LABELS` beside `LWT_BAS`. `build_areas` appends them after `lwt:`. They are never read from the database. |
| `swe` on a snow area only; a 400 names the vocabulary | `_KIND_VARS`. The refusal reads `var 'swe' is not served for lwt:BPAT; station: areas carry precip, hdd, cdd; lwt: areas carry hdd, cdd; snow: areas carry swe`. An unknown area's message gains `; snow:{basin} for columbia_above_the_dalles, …`. |
| `/areas` 44; the snow row | `{area, kind: "snow", label, vars: [{var: "swe", season: "water_year", units: "% of normal peak", first_season, complete_seasons}]}` |
| `season.mode` | Appended as the last key of `season`: `"cumulative"` or `"level"`. |
| 1. the day's own value | `_walk_level` |
| 2. a missing day is null on that day only | `_walk_level`. `days_missing` counts the missing days. |
| 3. Feb 29 on no slot, added into nothing, still counted | `_walk_level`, plus `LEAP_RULE_LEVEL` in `season.leap_rule` |
| 4. complete; unchanged statistics | `_complete`, the cone, normal, five-year band and ENSO medians. None of these changed. |
| 5. `this_season` | `through` is the frontier. The gap absence gains `days_missing` for a level only, so the cumulative absence shape is untouched. |
| 6. WY N → ENSO N−1 | `MAPPING["swe"]`. `label` and `label_year` key on `water_year`. |
| 7. readout | A level's value is the frontier's own value. For a Feb 29 frontier, the statistics are read on the Feb 28 slot. An earlier gap withholds nothing. Between seasons (a frontier on Sep 30), the readout is withheld only if that day is missing. |
| 8. `years[]` | `final` is null for a level. `peak` and `peak_md` are present for complete seasons and the current one; they are null otherwise and always null for cumulative variables. |
| 9. top-level `peak`, `peak_absence` | `this_season`; `last_season` (null unless that season is complete); `base {median, p10, p90, median_md, n}` over the base seasons' peaks. |
| 10. `source` | `{dataset, method}` for every area. Snow adds `n_index`, `n_reporting` and `normals_version` from the frontier row's `meta`. The snow `method` is §1's sentence verbatim. The other five are one factual sentence each: GHCNd precip; station degree days at base 65 °F on the GHCNd basis; LWT "as banked in lwt_degree_days_daily". |
| 11. rounding 0.1 at the edge | unchanged (`_r`) |

**Choices the spec left open. Each is stated in code and here; the architect may overrule.**
- **Peak ties and Feb 29.** A peak is taken over every valued calendar day, Feb 29 included, and the first occurrence wins a tie. `peak_md` can therefore read `02-29`. For `median_md`, a Feb 29 peak counts as the Feb 28 slot.
- **`base.median_md`.** This is the median of the peaks' axis slots. With an even n it takes the lower median, so the answer is a whole day.
- **`base.p10` and `p90`.** These are linear (type 7), matching the cone. They are not gated on n; `n` rides alongside so the page can gate.
- **Board `as_of` and a missing frontier.** `as_of` is the newest frontier among the six basins. A basin whose own frontier is older gets `absence: {reason: "frontier_missing", date: as_of, last_valued}` and nulls for everything tied to that day, `n_reporting` included. It keeps `n_index` and `peak_this_season`, which are true regardless of the day. A basin with a withheld readout gets its `readout_absence`.
- **Board `last_year`.** This is `years[]` WY(N−1)'s `to_date` on the readout's axis day. `five_year_mean` is `five_year.mean[day]`.
- **The board's classifier.** It uses `cpc_oni`, the default, so its memo entries are the same objects `/season?area=snow:…&var=swe` serves.

**SQL.**
- `SNOW_SQL` has the `LWT_SQL` shape plus `CASE WHEN ts = max(ts) FILTER (WHERE value IS NOT NULL) OVER () THEN meta END AS meta`. That is one read, with `meta` on the frontier row only. On Neon, for `snake`: 15,619 rows, 1 carrying meta, last 2026-09-28.
- `AREAS_SNOW_SQL` is one GROUP BY on `split_part(series,'.',1)` and the water-year shift used for precip (`- interval '9 months'`). On Neon its per-basin complete-season counts reproduce §0: 41, 42, 41, 41, 41, 40.

## Cells (§2)

`tests/test_season_snow.py` holds 14 tests, and `tests/test_season.py` holds 21. Together: **35 passed.**

| cell | test | asserts |
|---|---|---|
| N1 | `test_n1_level_values_are_the_days_own_and_a_gap_is_one_day`, `test_n1_this_season_gap_keeps_later_days_and_the_readout` | Values are each day's own. A deleted Jan 15 is null there and Jan 16 = 16.0. The season is excluded 364/365. For the current season: `complete_to_date` false, absence `{gap, 2026-02-10, absent, 181, days_missing 2}`, Feb 12 still valued, readout present. |
| N2 | `test_n2_leap_day_is_not_added_into_feb_28` | WY2024 axis has 365 slots. Feb 28 slot = 128.0, not 128 + 129. The season is complete at 366/366. With Feb 29 deleted: Feb 28 is still 128.0, days 365/366, excluded. `season.mode` is `level` and `leap_rule` names the rule. |
| N3 | `test_n3_statistics_equal_numpy_on_the_raw_values` | Over 41 gamma-noise seasons: p0…p100 equal `np.percentile(..., "linear")`; the normal (n 30) equals the mean; five-year mean, min and max equal NumPy. All compared through the module's one edge rounding. |
| N4 | `test_n4_a_lone_first_row_is_excluded_with_its_counts` | A single 1982-09-30 row gives `excluded [{WY1982, 1, 365, 1981-10-01}]`, base.n 42, and WY1982 `peak` null. |
| N5 | `test_n5_zero_median_has_no_pct_but_a_percentile` | Sep 28 value 0.3 against median 0.0: `pct_of_median` null, `percentile` 100.0. |
| N6 | `test_n6_peak_first_occurrence_and_base_median_day`, `test_n6_cumulative_variables_carry_no_peak` | A 70.0 tie on Mar 10 and Mar 20 gives `this_season {70.0, 2026-03-10}`. `base.median_md` = the median of 35 peak slots = `03-18`; median, p10 and p90 equal NumPy. Precip gives `peak` null with `peak_absence` `not_a_level`. |
| N7 | `test_n7_years_final_null_and_peak_for_swe_reverse_for_precip` | For swe: WY2025 `final` null, `peak` is the season maximum, `peak_md` `03-15`. For precip: `final` 365.0, `peak` and `peak_md` null. |
| N8 | `test_n8_refusals` | `swe` on `station:` and on `lwt:BPAT`, `precip` on `snow:snake`, `snow:yakima`, and var `snow` each return a 400 that names the vocabulary. |
| N9 | `test_n9_areas_and_swe_key_order` | `/areas` lists 44, with rows 38–43 being the six snow areas in §1's order and the pantry's labels verbatim (as string literals in the test). The `swe` payload's keys are `RESPONSE_KEYS` = the old 22 + `peak`, `peak_absence`, `source`. `source` carries §1's sentence verbatim and the frontier meta. The statement is `SNOW_SQL` with `{d: snow_basin_index_daily, s: columbia_above_the_dalles.SWE_PCT}`. |
| N10 | `test_n10_the_test_vector` | For each of the six series, rows on 1997-, 2015- and 2026-04-01 plus the WY2026 peak day. The readout day is Apr 1. `years[].to_date` equals §0 at 0.1, and the pure walk holds §0's values exactly (153.98 …). `peak.this_season` is §0's peak and day. |
| N11 | `test_n11_board_six_rows_and_a_missing_frontier` | Six rows in §1's order, with key order `BOARD_KEYS`. With `snake_lower`'s 2026-09-28 deleted, that basin is still listed with `frontier_missing` and nulls. The `columbia_above_the_dalles` row equals its memoised season payload's readout, last year, five-year mean and peak. A second board read and a `/season` read issue no statement, because they share the memo. `max-age=900`. |
| N12 | `tests/test_season.py` | All 21 pass. The edits are all within N12's permission; see the list below. |

**N12's edits to `tests/test_season.py`, all of them:**
- The `S11` table tuple gains `"peak", "source"`.
- `len(areas) == 38` becomes `44`.
- Two index lines follow from the count change: `areas[21:]` becomes `areas[21:38]`, and `areas[-1]` becomes `areas[37]`. Both still assert the same 17 LWT rows. Before this lane those rows ran to the end of the list; now the six snow rows come after them.
- The fake pool answers `AREAS_SNOW_SQL` with `[]`. This is harness only, not an assertion: the new statement would otherwise hit the pool's "unexpected statement" guard.

**Cumulative payloads are unchanged.** `build_season` on main and on this branch was run on three fixtures: Sacramento-shape precip with an absent and a NULL day, Seattle-shape hdd, and lwt cdd. After dropping `peak`, `peak_absence`, `source`, `season.mode` and `years[].peak`/`peak_md`, the `json.dumps` output is **byte-identical** in all three. This is §3's last row, checked in the pure module ahead of production.

## Reds

Each red is one edit to `season.py`. For each I ran the snow cells and the season cells, then restored the file (`cmp` against the saved copy confirmed it).

| red | edit | cells red |
|---|---|---|
| R1 | accumulate `swe` (a running `total` written to the slot in `_walk_level`) | **N1** (both), **N10**, N2, N3 |
| R2 | add Feb 29 into the Feb 28 slot for a level | **N2**, N3 |
| R3 | null everything after the first gap (write only while `first_missing is None`) | **N1** (both), N10 |
| R4 | `final` = Sep 30's value for `swe` | **N7** |
| R5 | discover snow areas from `counts` (sorted, labelled by key) instead of `SNOW_BASINS` | **N9**, S11 |

Every red turns the cell §2 names red. Cells in plain type went red as well.

## Suite

| | collected | result |
|---|---|---|
| before, api main `5fce60b` | **2010** | 2009 passed, **1 failed**: `tests/test_chart_brief.py::test_chart_brief_maps_contract` (`assert 'pending' == 'overdue'`) |
| after, this branch | **2024** (+14) | **2024 passed** |

**The chart_brief failure isn't this lane's.** It depends on the wall clock: the publication clock reads `pending` inside the edition's grace window and `overdue` after it. The same untouched main checkout failed it at ~12:55Z and passed it at 13:03Z. This lane doesn't touch `publication_clock.py` or chart brief. I've reported it and not fixed it.

## Size: the `swe` payload for `snow:columbia_above_the_dalles`

- **Real rows: 128,556 bytes.** The series was pulled read-only from Neon: 15,722 calendar days from 1983-09-13 to 2026-09-28, 15,618 valued, run through `build_season` with the frontier `meta`. The ENSO bins were a stand-in (all neutral), so `curves` carries every base year, the maximum; real bins can only drop open or unbinned years.
  - The same run gives: `base.n` 41; excluded WY1983 (14 of 365 days) and WY1984 (266 of 366, first missing 1983-12-17); percentiles present; normal n 30.
  - `this_season` WY2026 through 2026-09-28, `complete_to_date` true.
  - Readout: `{day 362, value 0.1, median 0.0, pct_of_median null, percentile 80.5, vs_five_year 0.0}`.
  - `peak.this_season` {63.8, 2026-03-16}; `last_season` {101.4, 2025-03-23}; `base` {median 94.7, p10 68.9, p90 119.2, median_md 04-05, n 41}.
  - WY1997 `peak` 149.4 on 04-14.
- **Synthetic shape** (`test_swe_payload_size_for_the_handback`, same span): 130,684 bytes.

## For §3 (the architect's production reads)

Read ahead of production against real rows, `columbia_above_the_dalles` only:
- The readout today is `value` 0.1 and `median` 0.0, with `pct_of_median` null. Nothing is invented.
- `base.n` for `col_above_grand_coulee` will be 41 (§0 and the areas GROUP BY agree).
- `peak.this_season` for `col_mid_tributaries` will be 46.4 on 2026-03-13, because the banked maximum is 46.44 on that day.
- The board's `n_reporting` / `n_index` values come from each 2026-09-28 frontier row's `meta`, read directly, in §1's order: columbia_above_the_dalles 155 / 163, col_above_grand_coulee 44 / 44, col_mid_tributaries 29 / 34, snake 82 / 85, snake_upper 58 / 61, snake_lower 24 / 24. Every `n_reporting` is ≤ its `n_index`, and every `normals_version` is `v1`.
