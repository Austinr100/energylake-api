# Handback — d091485 — Local Weather lane A follow-ups

**STOP-N, first line as written:** `api.weather.gov` is unreachable from this lane's box. `curl https://api.weather.gov/points/33.94,-118.41` fails with `CONNECT tunnel failed, response 403` from the sandbox's egress proxy, so NWS never saw the request. The whole lane is built and proven on #78's fixtures (`tests/fixtures/nws/`, gridpoint LOX/154,44) and the `FakeGlobalBank`. Nothing here was checked against live NWS.

**Repo:** `energylake-api`. **Branch:** `claude/pantry-cc-spec-local-forecast-ddkvzn`, cut from `origin/main` 7a4e7c6 (contains #78). This is the branch this session was bound to; the spec's `claude/local-forecast-followups-d091485` could not be used, see §5. Branch only: no PR, no merge, no Railway changes.
**Spec:** pantry `docs/cc_spec_2026_09_26_local_forecast_followups.md`. **Rulings built:** D-09-25-09 and D-09-25-10.
**Suite:** `pytest` gives **1874 passed** (main had 1837; +37 collected cases from this lane, parametrised cases counted).

---

## 1. What was built

| § | change | where |
| --- | --- | --- |
| 2.1 | **D-09-25-09, a narrower fallback.** `NwsClient.fetch` now sorts its calls into two classes. **Forecast-critical** calls are `points`, `forecastHourly` and `forecast`. They raise `NwsError` → the route falls back to the model arm with `receipts.fallback` (shape unchanged). **Garnish** calls are the station list with its latest observation, plus alerts. Their failures are caught inside the arm and passed to `build` as `obs_error` / `alerts_error`. When the observation fails, `now` comes from `now_unavailable()`: every NWS field is null with `"<field>: obs unavailable (<status>)"`, and the source reads `nws · <station> · no recent observation`. When alerts fail, `alerts` is `[]` and the notes gain `"alerts unavailable (<status>)"`. A malformed observation or alerts body is the same garnish failure, discovered later. A failed call is not memoised (`_memo_get` only stores a body), so the next request retries it. | `nws_arm.py` |
| 2.2 | **D-09-25-10, hourly from now.** One helper, `lf.trim_to_now(rows, now) -> (rows, note)`. It drops rows before the UTC-floored hour containing `now`, keeps at most 48, and returns `"hourly from <HH>Z, <n> rows"`. The caller passes `now` in: it is the arms' `generated_at`, which comes from `lf.utcnow`. Both arms apply it before `build_payload`. The model arm first cuts its series at the last f-hour that banked a `t2m` value (`fhr_range[1]`), so it invents nothing past that point. `now` on the model arm is still f000, as the acceptance check expects. | `local_forecast.py`, `nws_arm.py`, `model_arm.py` |
| 2.3 | **Days 8–10 on the US arm.** When the NWS arm answers with fewer than 10 daily rows, `_local_extend_days` asks the model arm about the same place, using NWS's tz so the local dates line up. It appends model rows for dates after NWS's last date, up to 10 rows in total. Each appended row keeps its own `source: model · GFS <HH>Z f…–f…` and the note reads `"days 8–10 from model · GFS 12Z"`. If the model arm raises `ModelArmError`, the note is `"days 8–10 unavailable: model arm 503"`. Any other exception gives `… model arm <ExceptionClass>`. Either way the US arm serves its ≤ 7 rows with a 200. | `main.py` |
| 2.4.1 | **`weather_point.ladder(store, model, crop, run_dt, param, lat, lon, fhrs) -> dict`**. This is the body of `/api/weather/point/ladder`, lifted into the module. The route now calls it and adds only its own keys (`fhr_step`, `fhr_max`, `elapsed_ms`, `chain`) in the original order. `model_arm.read_ladder` calls it too. Its rebuilt copy (its own `locate`, `byte_offset` and `get_values` calls) is deleted. **Byte-identical**: see §3. | `weather_point.py`, `main.py`, `model_arm.py` |
| 2.4.2 | **A full-circle header wraps.** New `wp.is_full_circle(hdr)` checks `nx × dlon == 360` on the declared grid. When it holds, `locate` takes the longitude index modulo `nx` and only latitude can miss, mirroring pantry `d2/values.locate_cell`. Regional crops such as na3 still go through `normalize_lon` and still 404 outside the box. | `weather_point.py` |

## 2. Tests: T table

All tests are in `tests/test_local_forecast.py` and use fixtures only, with no network.

| T | test(s) | result |
| --- | --- | --- |
| T11 | `test_T11_observation_404_stays_on_the_nws_arm`, `…_a_failed_observation_is_not_memoised`, `…_station_list_failure_and_malformed_obs_are_garnish_too` | green |
| T12 | `test_T12_alerts_503_is_an_empty_list_with_the_note`, `…_a_real_no_alerts_answer_carries_no_note` (a genuine empty list carries no note) | green |
| T13 | `test_T13_forecast_critical_failure_falls_back`: {points, forecast, hourly} × {503, 404, TimeoutError}, the same assertions as #78's T6 | green (9) |
| T14 | `test_T14_trim_to_now_floors_the_hour_and_names_it` (14:37Z → 14:00Z), `…_nws_hourly_starts_this_hour` (43 rows from 14Z), `…_model_hourly_starts_this_hour` (14Z = `f000–f006 interp`), `…_model_arm_invents_nothing_past_its_last_f_hour` (t2m banked to f024 → 23 rows ending at f024; at f230 → 11 rows ending at f240) | green |
| T15 | `test_T15_days_8_to_10_from_the_model_arm` (10 rows; 10-02..10-04 each `model · GFS 12Z f…`; note), `…_model_arm_raising_leaves_seven_rows_and_the_note`, `…_production_today_no_global_sidecar_still_200` (no run at all → 7 rows, 200) | green |
| T16 | `test_T16_model_arm_reads_the_placed_cell_on_pm180`: Vancouver, Fiji (−17.8, 178.0), Samoa (−13.8, −172.1), (0, 179.9) and (0, −179.9) on the pm180 fake. Each reads the +7 K the fake placed at its cell, with −20 K on both seam-side neighbours, and every range read is that cell's offset. Also `…_both_sides_of_the_seam_are_one_cell`. | green (6) |
| T6′ | `test_T6p_ladder_route_is_byte_identical_to_main` compares six sha-256 bodies taken on main 7a4e7c6: default, step24, chain=1, f000 missing, outside the crop (404), run absent (404). It uses test_weather_point's `FakeSidecar`, the ladder_fake's class. Also `test_T6p_model_arm_reads_through_weather_point_ladder` (a spy sees one `wp.ladder` call per param; the copy is gone from `model_arm.py`). | green (7) |
| T7 | `test_T7_locator_wraps_a_full_circle_header`, `…_matches_pantry_locate_cell_on_both_axis_origins` (a sweep of 7201 longitudes on lon0 −180 and 0), `…_latitude_still_refuses_and_regional_crops_still_do_not_wrap` | green |
| T10′ | `tests/test_weather_point.py`, `test_enso_catalog.py`, `test_cors.py` are **unchanged**, and #78's T10 sha pins still hold. Whole suite: 1874 passed. | green |

**#78 pins amended by the rulings** (T2, T4 and T8 no longer hold as written under D-09-25-10 and §2.3):
- **T2:** `daily` is now 10 rows (7 NWS + 3 model). The test asserts the NWS seven come first.
- **T4:** the two tests that index `hourly[h]` as f00h now pin the clock at the run hour (`world["now"] = RUN + 10 min`). What they assert is unchanged.
- **T8** (lon > 180): `notes` gains `"hourly from 21Z, 48 rows"` and `"days 8–10 from model · GFS 12Z"`.

## 3. Reds

Each red was applied by a script, the named tests were run, and the file was restored. The same tests went green again after every restore.

| red | edit | went red |
| --- | --- | --- |
| R6 | the observation `except NwsError` re-raises | T11: 3 of 3 |
| R7 | the `alerts unavailable (…)` note removed (still `[]`) | T12: `…alerts_503…` (its "no note on a real empty list" partner stays green, correctly) |
| R8 | `trim_to_now` floors to the hour **before** `now` | T14: 4 of 4 |
| R9 | appended model days get NWS's `source` | T15: `…days_8_to_10…` |
| R10 | the full-circle branch clamps to `[0, nx−1]` instead of `% nx` | T7: 2 of 3 (the latitude/regional test stays green, correctly). T16: the (0, 179.9) case, the only probe past the seam's midpoint, reads column 1439 (−20 K). |
| T13 red | a `forecast` failure swallowed as an empty period list | T13: 3 (all `forecast` cases) |
| T6′ red | `wp.ladder` adds one key to `source` | T6′: 4 of 6 (the four 200 bodies; the 404 bodies never reach `source`) |
| T10′ red | a comment added to `tests/test_weather_point.py` | T10 sha pin |

## 4. The `ladder()` diff and the locator finding

**`ladder()`: STOP-L not triggered.** The route's body is byte-identical on all six cases. `elapsed_ms` is wall-clock, so both shas were taken with `time.perf_counter` pinned. Route before and after, in `main.py`:

```diff
     started = time.perf_counter()
-    # THE HEADER, ONCE. … (the 3-rung header probe, locate, byte_offset,
-    #  get_values fan-out, the per-rung rows, and the 15-key `out` dict —
-    #  ~85 lines, moved verbatim into weather_point.ladder)
+    try:
+        body = await _wp.ladder(store, model, crop, run_dt, param, lat, lon, fhrs)
+    except _wp.PointError as e:
+        _wp_raise(e)
+    out = {k: body[k] for k in ("param", "model", "crop", "run")}
+    out["fhr_step"] = fhr_step
+    out["fhr_max"] = fhr_max
+    for k in ("count", "point", "cell", "units", "values", "source", "header",
+              "verified"):
+        out[k] = body[k]
+    out["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
+    values, units = out["values"], out["units"]
     if chain: …                                   # unchanged
```

The lifted body needs two things that lived in `main.py`: the header echo and the teaching 404, which probes the sibling PNG. Both moved into `weather_point.py` as `header_block(hdr, hkey)` and `sidecar_not_found(store, hkey, vkey, pngkey)`. `main.py`'s `_wp_header_block` and `_wp_header` now delegate to them, so there is one copy of each. The single-point route's bodies are unchanged (all of `test_weather_point.py` passes, untouched). `model_arm.read_ladder` maps `wp.ladder`'s outcomes back to its old contract:
- the teaching 404 → `missing_header` (STOP-B, unchanged)
- the bounds 404 → `ModelArmError("point outside the global crop")`
- any other `PointError` → `"sidecar storage error"`

**Locator finding.** Before this lane, `weather_point`'s locator did **not** treat a full-circle header as periodic. It *looked* as if it did at ±179.9: `normalize_lon` retries a longitude ±360, and on a pm180 axis that half-cell reach covers most of the seam, so on main both 179.9 and −179.9 already read column 0. The hole was the seam's exact midpoint: `locate(0, 179.875)` on the pm180 header **raised a 404 ("point is outside the crop")**. That is the Greenwich-slit shape the pantry's `locate_cell` docstring describes. The modulo rule closes it, and T7's 7201-point sweep pins parity with `locate_cell` on both −180 and 0 origins.

## 5. What the spec got wrong

1. **T7's "adjacent columns".** On a nearest-centre pm180 grid (lon0 −180, 0.25°), 179.9 E and −179.9 are **the same cell**, column 0, which is the ±180° meridian. They are 0.1° either side of its centre, and the seam between columns 1439 and 0 sits at ±179.875. T7 pins what is true: both points are inside and share column 0, 179.8 → 1439 is adjacent to it modulo `nx`, −179.8 → 1, and 179.875 → 0 (the case that used to 404). T16 likewise places one cell for both (0, ±179.9).
2. **"If it already does, the lane says so."** It half did: ±179.9 worked through the ±360 retry, and only the seam midpoint failed. So the rule was added; this is not a no-op pin.
3. **"`weather_point.py` — exactly two changes … nothing else moves."** The route body can't be lifted without its header echo and the teaching 404, which lived in `main.py`. They moved with it (§4). The alternative was a second copy of each in `weather_point.py`.
4. **T6′ "sha of the JSON body".** The body contains `elapsed_ms` (wall-clock), so no raw sha repeats between two runs even on main. The shas are taken with `perf_counter` pinned.
5. **D-09-25-10 and §2.3 change #78's pinned tests** (T2's 7 daily rows, T4's f000-indexed rows, T8's exact `notes`). The spec doesn't list them; they are amended as described in §2.
6. **The station-list call is unclassified.** D-09-25-09 names `observation` and `alerts` as garnish. `observationStations`, which is how the arm finds the station to observe, is treated as part of the observation. When it fails, `now.source` is `nws · station unknown · no recent observation` and `receipts.station` is null.
7. **The US ETag no longer covers the whole body.** `W/"nws:<issued>:<station>"` was kept, because T8 pins it. Days 8–10 now come from the model run, so a new GFS run under an unchanged NWS issuance can return a 304 with stale days 8–10, bounded by `max-age=300`. Fixing it means putting the model run in the US anchor, which changes T8's pinned tag. That's the architect's call.
8. **Cost of days 8–10.** Every US request with < 10 NWS rows now also asks the model arm: 4 params × (≤ 3 header probes + 41 four-byte reads), with the store's header cache and value LRU absorbing repeats. While production has no global sidecar, each US request also runs the render-ledger query, because `discover_run` memoises only successes. That's harmless, but it is one DB read per US request until d091484 lands.
9. **Appendix A's `CLAUDE.md`.** `energylake-api` has no `CLAUDE.md`, and the pantry's carries no D-09-25-* rulings. The pantry is also outside this lane's repo. The two rulings are recorded here verbatim for the architect to place:
   > **D-09-25-09** — only the forecast calls (`points`, `forecast`, `forecastHourly`) send a US request to the model arm; observation and alerts failures are stated in place. **D-09-25-10** — Local Weather's hourly starts at the current hour.
10. **Branch name.** The spec names `claude/local-forecast-followups-d091485` ("suffix acceptable"). This session is bound to `claude/pantry-cc-spec-local-forecast-ddkvzn` and may not push elsewhere, so the work is there.
11. **The "unavailable" note's `<status>`.** `ModelArmError` becomes `503`, which is what the route would answer. Any other exception is printed as its class name. Only the first case is pinned (T15).

## 6. Fence check

These are untouched: `enso_catalog.py`, `sky/`, `degree_days.py`, CORS, `railway.json`, secrets, the outline and `select_arm`, and the icon table. `weather_point.py` changes are §2.4's two, plus the two helpers from §5.3. No Railway changes, no PR, no merge.

## 7. Acceptance on production (not run: needs merge, deploy and d091484's first global render)

What the fixtures predict:
- **LAX:** `arm: nws`, `now.source` naming the station, `hourly[0].valid` = this UTC hour, 10 daily rows with days 8–10 `model · GFS <HH>Z f…`.
- **Before d091484 renders:** 7 rows and `days 8–10 unavailable: model arm 503`.
- **Vancouver / Tokyo / Fiji:** `arm: model`, `now.source` = `model · GFS <HH>Z f000`.
- **304:** unchanged (T8).
