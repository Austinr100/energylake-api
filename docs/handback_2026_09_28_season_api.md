# Handback — 2026-09-28 — d091503: the season-to-date API

**Spec:** pantry `docs/cc_spec_2026_09_28_season_api.md`. **Branch:** `claude/lane-d091503-energylake-api-xtagam`. No PR, no merge, no migration, nothing under `.github/workflows/`. Read-only against Neon.

**Shipped:**
- `season.py`: the pure module (no DB handle, no clock, no FastAPI).
- Two routes in `main.py`: `GET /api/weather/season/areas` and `GET /api/weather/season`.
- `tests/test_season.py`: 21 tests.

## STOP-D: clear

Re-measured on Neon `fancy-block-96153928` / `br-dark-morning-ajosxafs` at build time. Every table and column in §0 is as the spec states.

| check | result |
|---|---|
| `ghcnd_weather_daily.prcp_mm` | numeric (mm) |
| `station_degree_days_daily.hdd` / `cdd` | integer, with `basis_complete` boolean |
| `timeseries_values` `lwt_degree_days_daily` | 17 BAs × `{HDD, CDD, HDD_ANOM, CDD_ANOM}`, all 2011-01-01 → 2026-09-24 |
| station starts | Spokane 1900-01-01 |
| the four 2011 starts | USW00003145, USW00023234, USW00023293, USW00093138 |
| frontier, all 21 stations | 2026-09-24 |
| Sacramento complete water years before WY2026 | **74** |
| Sacramento WY2026 | 358 valued days; 2026-02-19 has **no row** |
| Seattle complete Nov–Mar seasons | **76** |
| Sacramento WY1998 | 365 of 365 days |
| cpc_oni `nino/strong` ENSO years | 1957, 1965, 1972, 1982, 1987, 1991, **1997** (EP), 2009, 2015, 2023 |
| `enso_catalog_runs.developing` | cpc_oni `{kind: nino, first_year: 2026, first_season: MAM, latest_season: JJA, n_seasons: 4, latest_oni: 1.8}`; roni 3 seasons, latest 1.4 (AMJ → JJA) |
| 2026 bin | `neutral` under cpc_oni and under roni |

## Gate 0: read and quoted

**`main.py`: DB helper, memo, cache headers.**
- `/api/enso/catalog` makes one `_pool.connection()`, so the pre-ping on checkout still runs. It keeps an in-process memo, `_enso_catalog_cache[classifier] = (now_mono, payload, version)`, with `_ENSO_MEMO_TTL = 60.0`, and sends `"Cache-Control": "max-age=3600"`. Its failure path is `except Exception as e: raise HTTPException(status_code=503, detail=f"db unavailable: {e}")`. It returns `JSONResponse` (plain json.dumps, not jsonable_encoder) so that "a stray Decimal must raise, not be coerced".
- `/api/weather/dd/*` uses `_DDCache`: single-flight, stale-while-revalidate, `Cache-Control: max-age={ttl}`, plus `X-Cache` and `Age`. That machinery exists for the 52 s views.
- **The season routes follow the ENSO idiom:** one connection, a monotonic memo, `Cache-Control`, `JSONResponse`. The areas read is ~1.2 s (EXPLAIN ANALYZE, below) and the season read is one indexed station scan, so neither needs the dd cache.

**`degree_days.py`: the absence shape.** `_absence(reason, message, **counts)` gives `{"reason", "message", ...counts}`. Its docstring rule: "A NULL total is not a zero, and it is never filled … `_f` … The `or 0` that is not here." `season.py` keeps the idea: a `reason` plus the counts that explain it (`{reason: "gap", first_missing, mode, days_complete}`, `{reason: "short_record", n}`, `{reason: "small_n", n}`, `{reason: "short_window", n}`, `{reason: "between_seasons", frontier}`).

**`enso_catalog.py`: its SQL.** `season.py` reuses these two statements rather than restating them:
```
RUN_SQL:       SELECT classifier, developing, source, n_episodes, n_year_bins, catalog_version, computed_at
               FROM enso_catalog_runs WHERE classifier = %(c)s ORDER BY computed_at DESC LIMIT 1
YEAR_BINS_SQL: SELECT enso_year, label, kind, strength, peak_window_oni::float8 …, flavor, …
               FROM enso_year_bins WHERE classifier = %(c)s AND catalog_version = %(v)s ORDER BY enso_year
```

**How `developing` names the open tail.** The pantry's `enso/catalog.py` says "the 1-4-season qualifying run at the end is NOT an episode: it is reported as `developing`". Production carries `first_year` / `first_season` / `latest_*` / `n_seasons` / `latest_oni`. The pantry's open-year rule, from `enso/composites.py`, is quoted verbatim because §2.8 asks for it:

> `open_enso_years`: "ENSO years whose Aug(E) -> Apr(E+1) peak window is not closed in the ONI series: its last whole season, FMA(E+1), is centred on Mar(E+1)." → `{e for e in enso_years if (e + 1, 3) > oni_last_centre}`

**The dashboard's `stationPrecip.ts` absence contract** (`src/components/weather/stationPrecip.ts`):

> "A station-day is either BANKED or it is ABSENT. A headline total is stated only when EVERY calendar day in its window carries a precipitation value. There is no part-summed tier and there must never be one." … "THE WALK IS BY CALENDAR DATE, NEVER BY ROW." … "`MissingReason = "absent" | "incomplete"` — `absent` no row banked; `incomplete` a row whose `prcp_mm` is NULL." … "THERE IS NO `?? 0` ON ANY VALUE PATH IN THIS FILE."

The season API carries the same two modes in `absence.mode`. For station degree days, `basis_complete = false` counts as `incomplete`.

## Cells

The 21 tests are in `tests/test_season.py`; each test name carries its cell number.

| cell | test(s) |
|---|---|
| S1 | `test_s1_gap_excludes_base_season_and_stops_this_season`, `test_s1_a_null_value_is_incomplete_not_zero` |
| S2 | `test_s2_leap_day_folds_into_feb_28`, `test_s2_missing_feb_29_withdraws_the_feb_28_day` |
| S3 | `test_s3_cone_is_numpy_linear_per_day` (5-season toy), `test_s3_cone_gate_29_is_null_30_is_present` |
| S4 | `test_s4_five_year_skips_incomplete_and_lists_its_five`, `test_s4_fewer_than_five_is_null` |
| S5 | `test_s5_mapping` |
| S6 | `test_s6_categories`, `test_s6_open_year_in_payload_and_pantry_fallback` |
| S7 | `test_s7_small_n_category` |
| S8 | `test_s8_mid_rank_with_ties`, `test_s8_readout_zero_median_and_category_keys`, `test_s8_readout_numbers` |
| S9 | `test_s9_hdd_between_seasons` |
| S10 | `test_s10_refusals` |
| S11 | `test_s11_route_key_order_and_areas` (38 areas = 21 + 17), `test_s11_lwt_short_record_with_five_year`, `test_s11_station_dd_basis_incomplete_is_missing` |
| sizes | `test_response_sizes_for_the_handback` |

## Reds

One edit each to `season.py`; run, then restore.

| red | edit | goes red |
|---|---|---|
| R1 | sum present days, missing as `or 0` | **S1** (both), plus S2 withdraw and S4 |
| R2 | keep Feb 29 as its own day (skip it off the axis) | **S2** (both), plus 9 more (every leap-year season turns incomplete) |
| R3 | trust the bin's `neutral` for an open year | **S6** (both) |
| R4 | five-year band as a calendar trailing window | **S4** |

## Suite

- Before: **1968** passed (at #83, as the spec says).
- After: **1989** passed (+21).

## Response sizes (from the fixtures, uncompressed JSON; `GZipMiddleware` applies on the wire)

| read | fixture | bytes |
|---|---|---|
| Spokane precip | WY1901 → WY2026, 125 complete seasons, all in `curves` from WY1950 | **254,029** |
| Seattle hdd | 1948-49 → 2025-26, 78 seasons, between seasons | **109,844** |

## Cost

`/areas` runs three GROUP BYs over every row of the three sources. EXPLAIN ANALYZE on production: **1,190 ms** total (the parallel legs take 0.96 s, 0.88 s and 1.17 s). It is memoised 1 h and served with `max-age=3600`. `/season` is memoised 15 min per (area, var, classifier) and served with `max-age=900`.

## Where I had to choose (the architect may overrule any of these)

1. **Absence keys.**
   - Each `*_absence` key is **always present, right after its sibling**, and null when the sibling is present. So the key order is fixed (`season.RESPONSE_KEYS`), and S11 asserts that the non-absence keys equal §1's table in order.
   - I added **`last_season_absence`** (`no_prior_season`) for symmetry. §1 does not name it.
2. **`this_season.absence` carries `mode`** (`absent` / `incomplete`), in addition to `first_missing` and `days_complete`: the dashboard's two words.
3. **Open year: both rules, not either one.** A year is open if it is ≥ `developing.first_year` **or** the pantry's peak-window rule says so, given the newest ONI/RONI centre month, which is read from `timeseries_values`. On today's data they agree (only 2026 is open). The union also covers a `developing` tail that starts in DJF, where `first_year` is a calendar year one ahead of the ENSO year.
4. **`category_absence` has two values beyond §2.8.**
   - `not_binned`: an ENSO year after the catalog's first year that has no bin row.
   - `unclassified`: a bin whose `kind` is NULL.

   Neither occurs on production today.
5. **The normal window uses the label's year.** That means WY1991–WY2020, heating 1991-92 → 2020-21, and cooling 1991 → 2020.
6. **The readout's `percentile` is gated on the cone's n ≥ 30.** Below that, `percentile` and `median` are null (short_record), so no rank is printed against a record the cone refuses. `vs_five_year` and `vs_category` are still printed when they have their own n.
7. **"Current season" means the frontier is inside a window *and before its last day*.**
   - A frontier exactly on Mar 31 (hdd) or Sep 30 (precip, cdd) reads as between seasons, with that season as `last_season`.
   - Between seasons, `last_season` is in the base (that is how Seattle's 76 includes 2025-26).
8. **`years[].to_date` when the readout is withheld.**
   - When a gap stops `this_season` (Sacramento), `to_date` is still taken on `this_season.through`'s day (Feb 18), so the table stays like-for-like.
   - It is null only when `this_season` has no through day at all.
9. **The area vocabulary is pinned in `season.py`.**
   - Stations: the 17 in `station_metadata.json`, plus the four it lacks.
   - LWT: the 17 BAs.
   - The routes do not discover new areas from the DB. A 22nd station would need one line here.
10. **The four unlabelled stations**, listed in id order after the metadata's 17, each labelled by its GHCN id with no `state`: **USW00003145, USW00023234, USW00023293, USW00093138**.

## What §4 should read after deploy

Everything here is predicted from the SQL counts above. It has not been read through the route on production, because this lane does not deploy.

| read | expect |
|---|---|
| Sacramento precip | `base.n` 74; `this_season` WY2026 `through` 2026-02-18, `absence.first_missing` 2026-02-19 (`mode: absent`); `readout: null` |
| Seattle hdd | `base.n` 76; `this_season: null` (`between_seasons`); `last_season` 2025-26; readout day 150 (`03-31`) |
| `lwt:BPAT` hdd | at most 15 complete seasons (2011-12 → 2025-26), so `percentiles: null` (`short_record`); `five_year` present |
| `enso.categories.nino_strong` on Sacramento precip | includes WY1998 |
| `/api/weather/season/areas` | 38 areas |
