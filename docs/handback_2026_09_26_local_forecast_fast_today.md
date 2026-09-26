**STOP-E was triggered by §2.5 (D-09-25-30).** One existing assertion had to change: `test_T4_daily_rows_at_most_ten_and_fhr_range_printed` pinned Vancouver's `daily[0].date == "2026-09-26"  # today began before the run`. The ruling adds a Today row wherever the local day began before the run, and that happens west of UTC too, not only east of it (see "What the spec got wrong", item 1). §2.5 is isolated in its own commit, `d376220`. It can be dropped without touching §2.1–§2.4 or §2.6. The architect decides.

# Handback — d091491 — `/api/local/forecast`: a cold read under two seconds, the rest of today, and every `unknown` names why

**Spec:** pantry `docs/cc_spec_2026_09_26_local_forecast_fast_today.md` (pantry main `d61f4da`).
**Repo / branch:** `energylake-api`, `claude/local-forecast-fast-today-d091491-flcuqz`, cut from `origin/main` `8677fff` (#80). Branch only: no PR, no merge, no Railway change.
**Rulings built:** D-09-25-27 (§2.1, built first), D-09-25-28, D-09-25-29, D-09-25-30, D-09-25-31.
**Suite:** 1899 passed at the base. **1931 passed** on the branch: 32 new tests, 1 pin amended (the STOP-E above), 1 setup retargeted, 0 skipped.

| commit | § |
| --- | --- |
| `3a22772` | §2.1 Server-Timing (the "before" code: timing added, paths still serial) |
| `9eea858` | §2.2–§2.4 NWS legs concurrent, ladders concurrent, one US model read, run discovery off the path |
| `cd18c9b` | §2.6 `unknown` says why |
| `d376220` | §2.5 rest of today (**STOP-E**) |
| (this commit) | handback, reds, README |

## Before / after, on the fake transports (per leg)

`python scripts/bench_local_forecast_timing.py --n 5`. The "before" is commit `3a22772` in a worktree. It has the timing header, but the paths are still serial. The "after" is the branch head. Each cell is the median of 5 runs, in ms, read back from the route's own `Server-Timing`. `wall` is the client-side time.

The injected latencies are listed below. They are assumptions for comparing the two code paths, not a model of production.
- **NWS, per call:** §0's direct Wichita read: points 308, forecastHourly 318, forecast 173, observationStations 108, observations/latest 113, alerts 140.
- **Bank:** 100 ms per sidecar GET (header or 4-byte range). This was chosen so the serial model read lands near §0's ~2.5 s.
- **Run ledger query:** 150 ms.

**Before (serial):**

| case | nws_points | nws_forecast | nws_obs | nws_alerts | model_run | model_ladders | build | total | wall |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| us_cold | 309.0 | 493.3 | 222.5 | 140.9 | 251.4 | 2830.0 | 3.2 | **4277.2** | 4283.5 |
| us_warm | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 204.6 | 2.4 | 225.1 | 230.1 |
| model_cold | — | — | — | — | 251.3 | 2831.0 | 1.1 | **3100.6** | 3105.0 |
| model_warm | — | — | — | — | 0.0 | 204.1 | 1.2 | 225.7 | 230.9 |

**After:**

| case | nws_points | nws_forecast | nws_obs | nws_alerts | model_run | model_ladders | build | total | wall |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| us_cold | 309.3 | 320.2 | 223.3 | 141.4 | 251.3 | 816.5 | 20.6 | **1091.8** | 1108.0 |
| us_warm | 0.0 | 1.8 | 0.0 | 0.0 | 0.0 | 204.3 | 21.0 | 223.7 | 228.7 |
| model_cold | — | — | — | — | 251.4 | 814.5 | 17.1 | **1082.2** | 1087.0 |
| model_warm | — | — | — | — | 0.0 | 203.5 | 18.2 | 221.6 | 226.0 |

**How to read the tables:**
- **US cold: 4.28 s → 1.09 s.**
  - NWS is now points (309) plus the slowest leg (forecast, 320), about 630 ms, against §0's 626 ms.
  - The model read runs alongside NWS. At 251 + 816 ms it is now the long pole on the US arm.
- **Model cold: 3.10 s → 1.08 s.**
  - `model_ladders` is 8 round trips, not 6. dswrf has no f000 header by design, so its ladder spends 2 header round trips before its 6 value waves. The t2m header is already cached by the run probe.
- **`build` before and after are not comparable.**
  - Before, the model build ran inside `answer` and was not marked, so the before `build` is `nws_arm.build` + `build_payload` + ETag only.
  - After, `build` also covers `model_arm.build` (~17 ms).
- **Warm (unchanged, ~225 ms here): the header names where it goes.**
  - `model_ladders` is 204 ms on every warm read.
  - A warm `read` still makes **two uncached GETs**: `dswrf_f000.json` (the header, 404 by design) and then the `dswrf_f000.f32` range read. Nothing caches an absent rung.
  - That is 2 R2 round trips on every warm model read, US arm included. It is out of this lane: `weather_point.ladder` and the store's caches are unchanged. It is the obvious next cut at the warm floor.

## What changed

| § | file | change |
| --- | --- | --- |
| 2.1 | `local_forecast.py` | `Timings`: monotonic start, `add`, a `mark(name)` context manager, `durations()` and `header()`. Names are `TIMING_NAMES` in the ruled order. A name marked twice accumulates. `total` is taken when the header is read. An unknown name raises. |
| 2.1 | `main.py` | `_local_timing_headers(request, timings)` sets `Server-Timing`, `Access-Control-Expose-Headers: Server-Timing, ETag`, and `Timing-Allow-Origin` = the request's Origin when it is in `ALLOWED_ORIGINS` or fullmatches `VERCEL_PREVIEW_ORIGIN_REGEX`. It is on 200, 304 and 503. 400 is unchanged. `build` covers the NWS build, the model builds, `build_payload` and `content_etag`. The body is untouched. |
| 2.1 | `nws_arm.py`, `model_arm.py` | `NwsClient.fetch(lat, lon, timings=None)`, `model_arm.read(…, timings=None)` and `answer(…, timings=None)`. The default is a throwaway recorder. |
| 2.2 | `nws_arm.py` | `points` is awaited alone. Its URLs are parsed in the same try as today. Then `asyncio.gather(forecast_leg, obs_leg, alerts_leg, return_exceptions=True)`, where `forecast_leg` itself gathers hourly ∥ forecast (timed together as `nws_forecast`). `obs_leg` is stations → latest. A forecast-critical `NwsError` is re-raised in the old order (hourly, then forecast), with the points tz on it. Garnish errors map to `obs_error` / `alerts_error` exactly as before. A non-NWS exception in a leg is re-raised, not swallowed. The bundle's keys, the memo and `fetches`/`hits` are unchanged. |
| 2.3 | `model_arm.py` | `read(store, candidates, lat, lon, timings) -> (run_dt, ladders)` gathers the four `read_ladder` calls (`return_exceptions=True`, then the first failing param in `PARAMS` order raises, as the serial read did). `answer` = `read` then `build`. |
| 2.3 | `weather_point.py` | Pool is `max_connections = LADDER_CONCURRENCY * 4` (32) and `max_keepalive_connections = LADDER_CONCURRENCY * 2` (16). `LADDER_CONCURRENCY` stays 8. |
| 2.3 | `main.py` | **US arm:**<ul><li>`asyncio.create_task(model_arm.read(…))` starts before `fetch`.</li><li>Fewer than 10 NWS rows: `_local_extend_days` awaits the task and builds with NWS's tz. The notes are unchanged: `days 8–10 from model · GFS HHZ` and the `unavailable: …` lines.</li><li>NWS falls through: the fallback is built from the **same** task.</li><li>10 rows, or any exception on the NWS path: `_local_discard` cancels the task and retrieves its outcome in a done-callback.</li></ul>**Model arm and `?arm=model`:** `read`, then `build`. |
| 2.4 | `model_arm.py` | `discover_run` serves a memo up to `RUN_MEMO_MAX_AGE_S` (7 h). From 300 s it calls `_start_refresh`, which is single-flight per model and keeps a reference in `_refresh_tasks`. A task stranded on a closed loop does not count as running. `_refresh` runs the old candidates + probe (now `_discover`). On failure it logs `[[LOCAL_RUN_REFRESH_FAILED]] <reason>` and leaves the memo. With no memo, or a memo past 7 h, the request awaits `_discover`. `ModelArmError` is unchanged. |
| 2.5 | `model_arm.py` | `_today_row(series, run_dt, generated_at, tz, lat, lon, label)` (see below). `build` calls it only when the local date of `generated_at` has no whole-day row. It inserts the row at index 0, keeps `DAILY_ROWS` = 10, and notes `today: fAAA–fBBB only — the rest of the local day (the run began after the day did)`. When there is no window it notes `today: no hours left in the run for the local date` instead. `_daily` is unchanged. |
| 2.6 | `local_forecast.py` | `_check_reasons` raises `…condition is unknown with no reason in absent[]` for `now`, `hourly` and `daily`. |
| 2.6 | `model_arm.py` | Hourly (and so `now`): `condition: from sky, which is null (<sky_why>)`. Whole-day row: `(no daylight window with dswrf)`. |
| 2.6 | `nws_arm.py` | `_cond` writes `condition: nws gave no icon` or `condition: nws icon token '<t>' is not in the house table`. `raw=False` on daily, which has no `condition_raw`. `_daily_row` passes its real `absent`. `now_unavailable` adds `condition: obs unavailable (<reason>)`. |
| — | `scripts/bench_local_forecast_timing.py` | The bench above. |
| — | `README.md` | The route row names D-09-25-27…-31. |
| — | `tests/test_local_forecast.py` | T1f–T9f. The T4 pin (STOP-E) is amended. T15's monkeypatch target changed from `answer` to `read`: setup only, its assertions are unchanged. |

**`_today_row` (D-09-25-30):**
- **Window:** from `max(floor-hour(generated_at), run_dt)` up to local midnight, keeping the hours present in the series.
- **`lo`:** the minimum `t` over the window.
- **`hi`:** the maximum `t` over the hours with `solar_elevation > 0`. With none, it is null with `hi: day period elapsed`.
- **Missing temperatures:** a null `t` inside the window nulls `hi`/`lo` with `<why> inside the day`, the whole-day rule.
- **Wind:** the maximum over the window.
- **`sky`:** the mean of the non-null values. When it is null, the reason is `night` when no daylight hour remains and `no daylight window with dswrf` otherwise. The same reason is used in the `condition:` line.
- **`source`:** `model · GFS HHZ fAAA–fBBB`.
- **Other fields:** `pop` and `precip_amt` are `not banked`, and sunrise/sunset are the date's.

## §3 — Tests

| T | test(s) | result |
| --- | --- | --- |
| T1 | `test_T1f_server_timing_on_200_304_and_503_in_the_ruled_order`, `test_T1f_expose_headers_and_timing_allow_origin[allowed, preview, stranger, no Origin]`, `test_T1f_body_and_etag_are_byte_identical_with_and_without_the_recorder` | pass.<ul><li>US 200 has all 8 names, in order.</li><li>Model 200 is `model_run, model_ladders, build, total` (no `nws_*`).</li><li>The 503 has no `nws_*` and no `build`.</li><li>The 304 ends in `total`.</li><li>Each part matches `name;dur=\d+\.\d`.</li><li>TAO echoes an allowed or preview origin, and is absent for a stranger or with no Origin. Expose-Headers is always there.</li><li>Cold LAX and cold Vancouver, with a recorder whose clock never moves against one where each clock read is +0.25 s: body bytes and ETag are identical, and `Server-Timing` differs.</li></ul> |
| T2 | `test_T2f_nws_legs_run_together_once_points_answers` | pass.<ul><li>50 ms per call: in-flight peak ≥ 4, and the first call is `points`.</li><li>A cold `fetch` is < 200 ms (3 × 50 + slack; serial is ≥ 300).</li><li>A second `fetch` makes 0 calls, with memo `hit/hit/hit/hit`.</li></ul> |
| T3 | `test_T3f_hourly_503_raises_with_the_points_tz_then_falls_back`, `test_T3f_forecast_malformed_is_an_nws_error_by_class_name`, `test_T3f_garnish_failures_are_stated_in_place`, `test_T3f_a_failed_leg_is_not_memoised` | pass.<ul><li>Hourly 503 → `NwsError("HTTP 503")` with `tz` from `points.json`. The route falls back with `tz_source "nws"`.</li><li>`forecast {"properties": {}}` → fallback `KeyError`.</li><li>Stations 503 → `now` `no recent observation` with `obs unavailable (HTTP 503)`.</li><li>Alerts 503 → `[]` with its note.</li><li>A failed hourly is not memoised and is fetched again.</li><li>**Every pre-existing NWS test passes unedited.**</li></ul> |
| T4 | `test_T4f_ladders_are_read_together`, `test_T4f_answer_is_build_of_read_byte_for_byte`, `test_T4f_the_store_pool_allows_32_and_keeps_16` | pass.<ul><li>20 ms per range GET: peak in flight is ≥ 24 and ≤ 32.</li><li>A cold `read` is < 160 ms.</li><li>`answer` and `build(read(…))` give identical JSON.</li><li>The httpx pool is (32, 16).</li></ul> |
| T5 | `test_T5f_nws_seven_days_is_one_model_read`, `test_T5f_nws_falling_through_is_still_one_read_and_todays_payload`, `test_T5f_nws_ten_days_cancels_the_model_read`, `test_T5f_nws_ten_days_and_a_failed_model_read_leaks_nothing` | pass.<ul><li>7 NWS days: `read_ladder` is called once per param.</li><li>Points 503: once per param, and the body equals `build_payload(model_arm.answer(…))` with the same inputs.</li><li>10 NWS days: the pending read is cancelled, and the response is 200 with no `days …` note.</li><li>A read that raised: no "never retrieved" after `gc.collect()`.</li></ul> |
| T6 | `test_T6f_memo_at_299s_is_served_with_no_refresh`, `test_T6f_memo_at_301s_is_served_at_once_and_one_refresh_starts`, `test_T6f_a_failed_refresh_leaves_the_memo_and_logs`, `test_T6f_no_memo_blocks`, `test_T6f_memo_at_seven_hours_and_a_second_blocks` | pass (injected clock, gated ledger).<ul><li>299 s: 0 ledger calls, no task.</li><li>301 s: the old run comes back within 0.5 s while the ledger is held. Two requests → 1 ledger call. The memo becomes the new run once released.</li><li>Failure: the memo stands, and the log line is exactly `[[LOCAL_RUN_REFRESH_FAILED]] run ledger unavailable: RuntimeError`.</li><li>No memo, and 7 h + 1 s: the request is still pending while the ledger is held.</li></ul> |
| T7 | `test_T7f_tokyo_at_night_rest_of_today_has_no_high`, `test_T7f_tokyo_by_day_the_high_is_the_daylight_high_only`, `test_T7f_no_hours_left_is_a_note_not_a_row`, `test_T7f_the_us_arm_extra_days_carry_no_today_row`, `test_T7f_the_run_divider_keeps_today_with_the_next_day` | pass.<ul><li>Tokyo 13:57Z: `2026-09-26`, `model · GFS 00Z f013–f014`, `hi` null (`day period elapsed`), `lo` = min(13Z, 14Z), `sky: night`, the note.</li><li>Tokyo 02:00Z: `f002–f014`. Elevation at 08Z is > 0 and at 09Z is < 0. With the night 10 K warmer, `hi` = max(02Z–08Z) < max(window).</li><li>Past f240: the note and no row.</li><li>LAX: model rows are exactly 10-02…10-04, with no `today:` note.</li><li>The `sourceRun` regex strips both Today and the next row to `model · GFS 00Z`.</li></ul> |
| T8 | `test_T8f_every_unknown_on_both_arms_says_why`, `test_T8f_f000_by_day_names_the_six_hour_mean`, `test_T8f_build_payload_refuses_an_unexplained_unknown_and_not_a_mapped_one` | pass.<ul><li>NWS: `smoke` (hourly), no icon (hourly), `haze` (daily), no icon (daily) and obs 503 (now) each carry their exact line.</li><li>Model: the night hours carry `(night)`. f000 by day (40N 10W, 12Z) carries `(not banked at f000 (dswrf is a 6 h mean))`.</li><li>`build_payload` raises for `now`, `hourly` and `daily` without the line, and passes with it.</li><li>A mapped word needs no line.</li></ul> |
| T9 | `test_T9f_key_sets_are_byte_identical_to_main` plus the existing key-order tests | pass. `git diff origin/main -- local_forecast.py` touches no `*_KEYS` line. The T2 key-order tests pass unedited. |

**Amended pin (STOP-E):** `test_T4_daily_rows_at_most_ten_and_fhr_range_printed`.
- **Was:** `d0["date"] == "2026-09-26"  # today began before the run`.
- **Now:** `d0["date"] == "2026-09-25"`, with `daily[1]["date"] == "2026-09-26"`.
- The other assertions in the test (`pop not banked`, `hi > lo`, `fhr_range`, `run`, `tz`) are unchanged and pass on the Today row.

## §4 — Reds

The script is `docs/receipts/local-forecast-fast-today-d091491/reds.py`, and the full output is in `reds.txt` beside it. Each red is one edit, run against the whole suite, then restored and checked by sha-256. The reds were applied to `d376220`.

| red | edit | must go red | went red (whole suite) |
| --- | --- | --- | --- |
| R1 | `legs = [await forecast_leg(), await obs_leg(), await alerts_leg()]` | T2 | **T2f** (only). 1 failed / 1930 passed |
| R2 | `got = list({p: await read_ladder(…) for p in PARAMS}.values())` | T4 | **T4f ladders** (only). 1 / 1930 |
| R3 | the fallback does `await _model_arm.read(…)` instead of the task | T5 | **T5f fallback** (only). 1 / 1930 |
| R4 | `await _refresh(…)` instead of `_start_refresh(…)` | T6 | **T6f 301 s** and **T6f refresh failure**. 2 / 1929 |
| R5 | `day = list(hours)` | T7 | **T7f by day** and **T7f at night** (the night row gets a `hi`). 2 / 1929 |
| R6 | the `condition` rule short-circuited (`if False and …`) | T8 | **T8f build_payload refuses** (only). 1 / 1930 |
| R7 | `notes.append(f"total {…} ms")` before `build_payload` | T1 | **T1f byte-identical** and **T1f 200/304/503**. Also T19 and T8 304, T8 lon-normalised (it pins the notes), and T5f fallback. 6 / 1925 |

R6 turns only the direct `build_payload` test red, as expected: the arms still write their lines, so the sweep test stays green.

## §5 — STOPs

- **STOP-N: no live smoke was possible, so nothing could trigger it.**
  - `curl -H 'User-Agent: energylake.io (ops@energylake.io)' https://api.weather.gov/points/37.687,-97.33` from this box got `CONNECT tunnel failed, response 403` from the session's egress proxy. That is the same block as d091488. NWS was never reached.
  - No NWS policy document was consulted that states a concurrency limit.
  - No retries or delays were added. Concurrency peaks at 4 calls per request, after `points`.
  - Acceptance item 1 is the first real test of STOP-N. The existing `[[LOCAL_NWS_BLOCKED]]` log line would carry NWS's body verbatim.
- **STOP-R: not measurable from this lane.** There are no R2 credentials in this box, and none were sought. The pool is at 32 as ruled. If production shows 429/503 at 32 in flight, drop the pool to 16. The wave count per ladder is still 6, but the four ladders then share 16 connections: about 12 value waves instead of 6.
- **STOP-E: triggered by §2.5.** See the top of this document. No other existing assertion changed. T15's edit is setup only: the monkeypatch now targets `read`, because the route no longer calls `answer`.

## §6 — Fence check

The diff against `origin/main` touches:
- `local_forecast.py`, `nws_arm.py`, `model_arm.py`, `main.py` (the route and its helpers only);
- `weather_point.py` (the two pool numbers only);
- `README.md`, `tests/test_local_forecast.py`;
- the new `scripts/bench_local_forecast_timing.py`, and `docs/`.

These are unchanged:
- `ETAG_EXCLUDED_RECEIPTS` and `content_etag`;
- every NWS `TTL_S`, and `RUN_MEMO_TTL_S` (300 s, which now means "refresh after");
- `select_arm` and the outline;
- `LADDER_CONCURRENCY` (8);
- every `*_KEYS` tuple;
- `enso_catalog.py`, CORS config, `railway.json`.

`tests/test_weather_point.py` is untouched, and T10's sha pin passes. There is no new dependency.

## What the spec got wrong

1. **The Today row is not only east of UTC** (§1 D-09-25-30, §0's framing, the source of STOP-E).
   - `_daily` skips any local day whose midnight falls before `run_dt`.
   - West of UTC, local midnight also falls before the run for almost every cycle a user sees. Vancouver on the 12Z run: midnight is 08Z. Any Americas point on the 12Z or 18Z run is the same.
   - So as ruled, the Today row appears on most of the model arm, not just Tokyo and Moscow. In production, today's `no daily row for …` gap was probably on every such point too.
   - I built it as ruled; the pin change is the evidence.
2. **"< 8 waves" in T4 holds only because the fake sleeps on range GETs alone.**
   - With every GET costing a round trip, a cold `read` is 8 round trips (bench: 816 ms at 100 ms each). dswrf has no f000 header by design, so its ladder spends 2 header trips first.
   - The cold-model budget in acceptance (p50 ≤ 1.5 s) should be judged with that in mind.
3. **The warm floor is partly this arm's own 404s.**
   - Every warm model read re-requests `dswrf_f000.json` (404) and then a `dswrf_f000.f32` range read, because nothing caches an absent rung.
   - That is 2 R2 round trips on every warm US and model read, about 200 ms here. §0 put the warm floor out of scope, and I left it there, but the header will show it.
4. **The note's parenthetical is half the story.**
   - "(the run began after the day did)" is true of why there is no whole-day row.
   - But when `generated_at` is later than the run, the window starts at the current hour, not at the run. The `fAAA` in the note then reflects the clock, not the run start.
   - I used the ruled text verbatim.
5. **§2.2's order of failures moved slightly.**
   - `points`' `forecast` / `forecastHourly` / `observationStations` URLs are now all read before any leg starts. A points body missing `forecast` now raises before `forecastHourly` is called; before, it was called first.
   - Separately, a forecast-critical failure no longer stops the garnish calls: they are already in flight, so a failing hourly still costs the stations/obs/alerts calls, and their successful answers are memoised.
   - Neither changes a failure class or `receipts.memo`. Both are visible in call counts only.
6. **`build` is not one leg.** It runs twice on the US arm: the NWS build before the model rows are awaited, and the model build after. Its `Server-Timing` value is a sum.

## Appendix A — `CLAUDE.md` (api, proposed)

The api repo has no `CLAUDE.md` today. This is the proposed text, not committed:

- **D-09-25-27:** `/api/local/forecast` states its own timing in `Server-Timing` (`nws_points, nws_forecast, nws_obs, nws_alerts, model_run, model_ladders, build, total`) on 200/304/503, never in the body. `Timing-Allow-Origin` echoes only an allowed or preview Origin. `Access-Control-Expose-Headers: Server-Timing, ETag`.
- **D-09-25-28:** only `points` waits. The NWS legs, the four model ladders and the US arm's one model read run concurrently. The store pool is 32/16, and `LADDER_CONCURRENCY` stays 8.
- **D-09-25-29:** run discovery is off the request path. The memo is served up to 7 h old, with a single-flight background refresh after 300 s. A failed refresh logs `[[LOCAL_RUN_REFRESH_FAILED]]` and the memo stands.
- **D-09-25-30:** the model arm adds a rest-of-today row when today has no whole-day row. `hi` is over daylight hours only; with none left it is `hi: day period elapsed`.
- **D-09-25-31:** `unknown` is a value that must say why. `build_payload` refuses it without a `condition:` line.
- **Trap:** a per-request duration must never enter a content-hashed body. It goes in `Server-Timing`, or it breaks every 304.
- **Trap:** `{k: await f(k) for k in ks}` is serial. Four independent reads want `asyncio.gather`.
