# Handback — d091477 — Local Weather lane A: `GET /api/local/forecast`

**Date:** 2026-09-25. **Repo:** `energylake-api`. **Branch:** `claude/local-forecast-api-d091477-2qe6k1`
(cut from `origin/main` @ `536d61b`, which contains #77; the suffix is the session's designated branch, as the spec allows).
Branch only: no PR, no merge, no Railway changes. **Spec:** pantry `docs/cc_spec_2026_09_25_local_forecast_api.md`.

**Read this first.** The build is complete and green against fixtures. But from reading the pantry's code, the
model arm's data source (`weather/values/gfs/global/…`) is probably **not written by anything**. If that's true,
the Vancouver acceptance curl answers **503** and every NWS fallback does too. This is §"What the spec got wrong"
item 1, and it needs a ruling before the captain's deploy means anything.

---

## The route

`GET /api/local/forecast?lat=&lon=[&arm=model|nws]`

| case | answer |
| --- | --- |
| inside the 20 km-buffered US outline | NWS arm, `receipts.arm = "nws"`, `Cache-Control: max-age=300` |
| outside, or `arm=model` anywhere | model arm, `receipts.arm = "model"`, `max-age=900` |
| any NWS failure on the US arm | model arm for that request, `receipts.fallback = {"from": "nws", "reason": "HTTP 503"}` (D-09-25-04) |
| `lat` missing / non-numeric / outside [−90, 90]; `lon` missing / outside [−180, 360] | 400, bounds in `detail`, no cache header |
| `lon` in (180, 360] | normalised; `receipts.notes` says `"lon 241.59 normalised to -118.41"` |
| `arm=nws` outside the outline; `arm` not `model`/`nws` | 400 |
| model arm can't answer (no run proven, store unconfigured, ledger down) | 503, no cache header, `detail.probed` lists the keys it tried |
| `If-None-Match` matches `W/"<arm>:<issued_at>:<run or station>"` | 304, empty body |

The shape is §2.5's, with the key order enforced by `build_payload`. `build_payload` raises on any key off
contract, on a `Decimal`, and on **any null without a `"field: reason"` in that block's `absent[]`**. Three
keys were added because the spec asks for things its shape has no slot for (item 9 below): `receipts.notes[]`,
`sun.absent[]`, and the `"field: reason"` spelling of `absent[]`.

## The files

| file | what |
| --- | --- |
| `local_forecast.py` | `select_arm` (ray casting, pure Python), `nominal_tz`, the 12-word vocabulary, `NWS_ICON_TABLE`, `COVER_FRACTION`, the 16-point wind table, SI conversions, NOAA sun position and sunrise/sunset, Haurwitz clear-sky, `build_payload`, `etag` |
| `nws_arm.py` | `NwsClient` (injectable transport + clock; the per-kind memo; User-Agent/Accept; 4 s/8 s; one retry on 5xx; `[[LOCAL_NWS_BLOCKED]]` on 403/429), row builders, `build` |
| `model_arm.py` | run discovery (`RUNS_SQL` on `d2_render_runs`, then a global t2m f000 header probe), `read_ladder` (built from `weather_point`'s own functions), interpolation, sky from dswrf, daily windows, `build`, `answer` |
| `data/us_outline_20km.geojson` | the outline: 32,021 bytes, 30 polygons, 1,793 vertices |
| `scripts/build_us_outline.py` | the outline builder. Needs shapely + pyproj at build time only; neither is in `requirements.txt` or imported by the API |
| `tests/test_local_forecast.py` | T1–T10 plus supporting tests: 87 in all, no network |
| `tests/fixtures/nws/*.json` + `README.md` | the NWS fixtures for LOX/154,44. **Hand-built, not recorded** (item 13) |
| `tests/fixtures/us_outline_raw.geojson` | the unbuffered twin: 136,988 bytes. T1 proves the buffer is what admits the offshore point; R1 swaps it in |
| `main.py` | the route (~110 lines, after `/api/enso/catalog`) and one docstring row |
| `README.md` | one section |

No new dependencies. `httpx` is already in `requirements.txt` and is imported lazily inside `NwsClient`.
`.env.example` is unchanged. The fence held: `weather_point.py`, `enso_catalog.py`, `sky/`, `degree_days.py`,
CORS, `railway.json` and secrets are untouched. `enso_catalog.etag_matches` and `weather_point`'s reader are
called, not edited.

## The outline

- **Source:** Natural Earth 1:10m Cultural Vectors, admin-0 countries. Public domain, so it can be bundled under
  any licence. It comes from the Natural Earth GitHub mirror:
  `https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_admin_0_countries.geojson`
  (input sha-256 `239eec57ac17f100a11e2536cffc56752c318b50ae765b0918ff7aab4ce8f255`; naciscdn.org was refused by the session's egress).
- **Members:** `ADM0_A3 = USA` (the 50 states + DC; Natural Earth carries Alaska and Hawaii in that one feature) and `PRI`.
- **Buffer:** 20,000 m in metres. Each part is buffered in its own azimuthal-equidistant projection. Parts are
  unwrapped across the antimeridian before the union and split back at ±180 after it, so the Aleutians on both
  sides of 180 are covered (a test pins Attu and Adak). The result is then simplified at 0.02° and rounded to 3 dp.
- **Served file sha-256:** `b9d439007f23ab667391e088ec5060c9457acdfa92e7f4fa7fe25aba2996f3aa` (32,021 bytes, well under 200 KB). It is printed as `receipts.outline_sha`.
- STOP-O did not trigger.

## The icon table (`local_forecast.NWS_ICON_TABLE`)

| NWS token(s) | house word |
| --- | --- |
| `skc` | clear |
| `few` | mostly-clear |
| `sct` | partly-cloudy |
| `bkn`, `ovc` | cloudy |
| `wind_skc`, `wind_few`, `wind_sct`, `wind_bkn`, `wind_ovc`, `hurricane`, `tropical_storm` | wind |
| `fog` | fog |
| `rain`, `rain_showers`, `rain_showers_hi` | rain |
| `snow`, `blizzard` | snow |
| `sleet`, `rain_snow`, `rain_sleet`, `snow_sleet`, `fzra`, `rain_fzra`, `snow_fzra` | sleet |
| `tsra`, `tsra_sct`, `tsra_hi`, `tornado` | thunderstorm |
| **left out on purpose:** `smoke`, `dust`, `haze`, `hot`, `cold` | `unknown`, with the token kept and a `[[LOCAL_UNKNOWN_ICON]]` log line |

`drizzle` and `heavy-rain` are in the vocabulary, but no NWS icon token reaches them, because NWS icons carry no
intensity. `sky` on the NWS arm comes only from NWS's own cover class, using okta midpoints: FEW 0.1875,
SCT 0.4375, BKN 0.75, OVC 1.0. It is never inferred from a precipitation icon.

## Tests (T)

`pytest` whole suite: **1837 passed** (1750 already on main + 87 new), no network.

| T | tests | result |
| --- | --- | --- |
| T1 | `test_T1_select_arm` ×6 (LA, 15 km off Santa Monica, Vancouver, Honolulu, San Juan, 30 km into Mexico); `…_the_offshore_point_is_the_buffers_doing` (the raw outline does **not** contain it); `…_outline_is_bundled_small_and_its_sha_is_in_the_receipt`; `…_aleutians_across_the_antimeridian` | green |
| T2 | key order exact at every level; 48 hourly; daily paired day/night (**7 rows**, item 7); 77 °F→25.0 °C and 63 °F→17.2 °C; `now.source == "nws · KLAX · observed 20:53Z"`; leading-night row; `t: nws qc` still renders; alerts; every null has a reason (both arms); `build_payload` refuses an unexplained null and a `Decimal` | green |
| T3 | 12 words; every table token maps to one of them (29 parametrised); unknown `smoke` → `unknown`, `condition_raw` kept, `[[LOCAL_UNKNOWN_ICON]]` logged | green |
| T4 | 48 hourly, `interp` true exactly off the f-hours, linear value checked; `pop/precip_amt/dewpoint/rh` null with `not banked`; daily ≤ 10 and `fhr_range == [0, 240]`; 4-byte reads only and only `/global/` keys; sky formula and night nulls; Haurwitz formula pinned; STOP-B names the key; discovery skips a run without global t2m; no run → 503 with no cache header | green |
| T5 | model `now.source == "model · GFS 12Z f000"`; `observed` absent from the whole body; forced `arm=model` in the US is labelled and never calls NWS | green |
| T6 | `points` 503 / 404 / raised timeout → 200 from the model arm with `fallback.reason` naming it; exactly one retry on 5xx and none on 404; a late failure keeps the real tz; malformed body falls through; 403 logs `[[LOCAL_NWS_BLOCKED]]` | green |
| T7 | two reads inside 10 min → one `forecastHourly` fetch, `hit` on the second; alerts re-fetched after 121 s; forecast re-fetched after the 10 min TTL; memo keyed on the gridpoint, not the click; `units=si` on both forecasts; UA/Accept/timeouts pinned | green |
| T8 | `max-age=300` + ETag (NWS), `max-age=900` + ETag (model); matching `If-None-Match` → empty 304; 400 on `lat=91`, `arm=nws` outside, missing `lon`, non-numeric, `lon=-181`, bad `arm`, none of them carrying a cache header; `lon=241.59` normalised with a receipt | green |
| T9 | LAX 2026-09-25 sunrise/sunset within 2 min of an **independent** pin (astral 3.2: 13:44:11Z / 01:45:43Z; ours is 13:43:58Z / 01:45:55Z); 70°N in December → `sunrise: null`, `"sunrise: polar night"`, day length 0, on `sun` and every daily row; midnight sun is kept distinct | green |
| T10 | `test_weather_point.py`, `test_enso_catalog.py`, `test_cors.py` sha-256 pinned to main (and green in the suite); README row present once; route registered once | green |

## Reds (R)

Each red is one edit followed by the whole suite. The file is then restored from its saved bytes and the sha-256
is checked. The script used was `reds.py` in the session scratchpad; the edits are listed here so they can be re-run.

| red | edit | went red | restored sha-256 |
| --- | --- | --- | --- |
| R1 | `OUTLINE_PATH` → `tests/fixtures/us_outline_raw.geojson` (the buffer dropped) | `T1_select_arm[15 km off Santa Monica]`, `T1_select_arm[San Juan PR]`, `T1_outline_…sha_is_in_the_receipt` (3 failed) | `803f420d352e…` `local_forecast.py` |
| R2 | model arm `now["source"] = "observed"` | `T5_model_now_source…`, `T5_forced_model_arm…`, `T6_points_failure…` ×3 (5 failed) | `fb4a63a0b411…` `model_arm.py` |
| R3 | the `except NwsError` in the route re-raises | `T6_*` (7 failed: all three points failures, the retry, late failure, malformed, blocked) | `3546573a2828…` `main.py` |
| R4 | model hourly `"pop": 0` | `T4_model_arm_48_rows_interp_flags_and_absent` (1 failed) | `fb4a63a0b411…` `model_arm.py` |
| R5 | daily `hi = _temp(night)` | `T2_48_hourly_rows_and_daily_paired…`, `T2_si_units…`, `T2_leading_night…` (3 failed) | `16fe5b1dafdc…` `nws_arm.py` |

R1 also flips **San Juan**. The raw outline, simplified at 0.02°, clips San Juan's shoreline, so the buffer is
absorbing simplification error as well as reaching offshore. That is worth knowing if the buffer is ever made smaller.

## The memo's measured hit rate (fixture run)

Measured on the T7 sequence: four requests to LAX at t = 0, +60 s, +121 s and +721 s.

| kind | fetches | hits |
| --- | --- | --- |
| points | 2 | 2 |
| forecastHourly | 2 | 2 |
| forecast | 2 | 2 |
| stations | 2 | 2 |
| obs | 2 | 2 |
| alerts | 3 | 1 |
| **total** | **13** | **11 (45.8 %)** |

Within one 10 min window and after the first read, the rate is 100% except alerts (2 min TTL). This is fixture
arithmetic, not a production measurement.

---

## What the spec got wrong

1. **The `global` value sidecars probably don't exist, so the model arm would have nothing to read.**
   §0 says "the GFS sidecars exist for `global` … and `na3`". In the pantry, the `global` region is COG-only.
   `scripts/build_d2_sequence.py::_render_cog_frame` says outright that it writes "no sidecar and no tile"
   (around line 711). Sidecars are written only by the crop-native regional frame (`north_america` → `na3`).
   `model_fields/sources.py` also notes that nothing under `weather/models/{model}/global/` exists until a bank
   lane promotes it. I **could not measure** this: there is no R2 access from this session.
   If it holds on production:
   - run discovery finds no global t2m f000 header, so Vancouver answers **503** (`detail.probed` names the keys);
   - every NWS fallback also answers 503;
   - STOP-B ("a stop for the acceptance if it is true on production") fires for all four params, not one.

   Ruling needed; options:
   - (a) a pantry lane writes `weather/values/gfs/global/…` for `t2m`, `wind10m`, `mslp`, `dswrf` (the COG road already has the array in hand);
   - (b) the model arm range-reads the global COGs instead;
   - (c) lift the no-`na3` rule for points inside na3. The spec forbids this, and I did not do it.

2. **`weather_point.ladder(...)` does not exist.** The ladder is the body of main.py's
   `/api/weather/point/ladder` route. Since `weather_point.py` is fenced, `model_arm.read_ladder` rebuilds it
   from the same functions in the same order:
   - header from the first of three rungs that has one;
   - one `locate`, one offset;
   - `get_values` at 8 in flight.

   **Proposal:** add `async def ladder(store, model, crop, run_dt, param, lat, lon, fhrs) -> dict` to
   `weather_point.py` and have both the route and this arm call it.

3. **The bank's cadence is f000..f240 every 6 h (41 rungs), not "3-hourly to f120".** That is the pantry's
   `D2_LADDER` and `wp.ladder_fhrs()`. Hourly rows between rungs are therefore interpolated across 6 h gaps, not 3 h.

4. **There is no `latest.json`.** Nothing in the pantry's `d2/` or `build_d2_sequence.py` writes one. The run is
   taken from `d2_render_runs` (the newest 4 gfs cycles with a manifest). The first of those whose global t2m
   f000 header exists wins, and the choice is memoised for 5 min. `receipts.run` prints it.

5. **`sky/` has no solar arithmetic.** It holds the GLM lightning proxy only. The sun is the NOAA/Meeus
   solar-position equations (~60 lines of `math`, in `local_forecast.py`). They are pinned against astral
   within 13 s at LAX.

6. **GFS `dswrf` is a 6 h mean and is absent at f000 by design** (pantry `d2/params.py` DSWRF). An instantaneous
   `1 − dswrf/clearsky(valid)` compares a 6 h mean with a single moment. Instead, an hour's `sky` compares the
   mean flux of the window that holds it against the Haurwitz clear-sky mean over the same window. Two
   consequences:
   - `now.sky` on the model arm is **always null** (`night`, or `not banked at f000 (dswrf is a 6 h mean)`);
   - a window whose clear-sky mean is below 25 W m⁻² is null with `low sun`.

7. **NWS `/forecast` has 14 periods, which is 7 days, not 10.** No NWS product gives 10 days
   (`forecastHourly` is about 6.5). The US arm's `daily` is 7 rows (8 with a leading night), and T2 asserts that.
   Proposal: accept ≤ 7 on the US arm, or fill days 8–10 from the model arm with row-level `model · GFS …`
   sources. The row labels already make that legal under D-09-24-09, but it makes the US arm depend on the bank.

8. **`test_weather_point.py`'s `ladder_fake` can't stand in for weather.** It is an na3 mslp array whose cells
   hold `j*1024 + i`, which is useless as a temperature. The tests use `FakeGlobalBank`: the same (key, Range)
   transport surface, the same refusal of a value read without a Range header, but a 721×1440 global header and
   weather-shaped values.

9. **The §2.5 shape has no slot for four things the spec asks for:**
   - `fhr_lo/fhr_hi` (§2.4): carried in the row's `source` instead (`model · GFS 12Z f006–f012 interp`);
   - the lon-normalisation receipt (§2.1) and the STOP-B missing key (§5): both go in the added `receipts.notes[]`;
   - T9's `reason: "polar night"`: goes in the added `sun.absent[]`;
   - `absent[]` itself: the example shows bare field names while the text shows `"pop: not banked"`. I used the text's form.

   `tz_source: "outline"` is never produced, because the outline carries no tz. It is `nws` or `nominal`.

10. **"`condition` from `sky` + `t2m`" is really `sky` only.** Without precipitation, `t2m` cannot pick among
    clear / mostly-clear / partly-cloudy / cloudy. The boundaries are okta classes: 0.125, 0.375, 0.625.

11. **Units are converted in the arms, not in `build_payload`.** Conversion goes by the unit the body *carries*
    (`temperatureUnit`, `unitCode`), not the unit that was requested. T2's "a known °F arrives as °C" only means
    something if the body can still carry °F after `units=si` was asked for. Keeping `build_payload` pure
    composition (order + null policy) is the cleaner split.

12. **"Any NWS failure" makes the whole payload fall back when only the obs or alerts call fails.** A station
    with no recent observation returns 404 on `/observations/latest`. Under D-09-25-04 as written, that throws
    away a good NWS forecast. I built it as written. Proposal: an obs failure gives `now` nulls with a reason, and
    an alerts failure gives `alerts: []` plus a note; only `points` / `forecast` / `forecastHourly` fall back.

13. **The fixtures are hand-built, not recorded.** This session's egress policy refused `api.weather.gov` (the
    proxy's CONNECT 403, not NWS). Each fixture carries a `_provenance` key saying so, and the directory README
    explains. Swap in recorded bodies when a session can reach NWS. For the same reason, STOP-N is **untested**
    from Railway: the first live evidence will be the architect's LAX curl, and a 403/429 there shows up as
    `fallback.reason` plus a `[[LOCAL_NWS_BLOCKED]]` log line carrying NWS's body.

14. **Where the model arm's rows start.** Hourly rows run f000..f047 (48 rows, nothing past f048), so the first
    ~6–11 rows are already in the past when served. `daily` includes only local days the run covers end to end,
    so *today* is never a model-arm row (a max over half a day is not the day's high). Lane B should hide past
    hours. Or rule "start at now, end at f048" and accept fewer than 48.

15. **Smaller items:**
    - The alerts memo is keyed on the gridpoint (D-09-25-03) but queried by the point (§2.3). Two clicks in one
      2.5 km cell share alerts, which is fine but is a choice.
    - The route is ~110 lines, not ~50: validation, fallback and headers.
    - The NWS hourly has no `feels`, `precip_amt`, `mslp` or cover percentage. `precip_amt`, `mslp` and cover
      live in `forecastGridData`, which the spec does not read. They are null with `not in nws hourly`, except
      `sky`, which comes from the icon's cover class when it has one.

## Appendix A — proposed CLAUDE.md lines (not applied; the pantry is outside this lane)

- **D-09-25-03** — the Local Weather NWS arm is read live with an in-process memo, never banked.
- **D-09-25-04** — NWS failure falls through to the model arm with a first-class `fallback` receipt, never a blank page.
- (**D-09-24-09**, the labelled model arm, is still owed from the charter.)
