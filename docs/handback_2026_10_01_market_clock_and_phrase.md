# Handback — d091546 — the market clock stops holding the pool, and Local carries NWS's own words

**Date:** 2026-10-01. **Lane:** d091546, `energylake-api`. **Rulings:** D-09-25-75, D-09-25-76.
**Branch:** `claude/funny-davinci-njlrse`, not the spec's `claude/market-clock-and-phrase-d091546`. This session's harness allows pushes only to its own designated branch. The content is the lane; rename the branch on your side if the name matters. No PR, no merge, no deploy.

## Verdict

- **Market clock:** fixed and memoised. The statement now names `series` in every subquery. Neon's after-plan is 2.3 ms with the window empty and 6.1 ms with it full, and every subquery is an index lookup. **STOP-Q does not fire.**
- **`condition_text`:** shipped on `now`, `hourly[]` and `daily[]`.
- **STOP-T does not fire.** The forecast read is the endpoint the spec assumed: `points.forecast` / `points.forecastHourly` (`/gridpoints/{wfo}/{x},{y}/forecast[/hourly]`), and each period carries a `shortForecast` key. The banked LAX bodies carry it as `""`, because they are hand-built (`tests/fixtures/nws/README.md`), so on those bodies it reads null. See §4.
- **Tests:** 2123 pass on the full suite. Every new and amended test was red on HEAD source (`docs/receipts/market-clock-d091546/reds.txt`).

## 1. Files

| file | what |
| --- | --- |
| `main.py` | `/api/market-clock`: `MARKET_CLOCK_SQL` (all six subqueries name `(dataset, series)`; adds `da_first_ingested_at`), `_market_clock_build` (the read, with `SET LOCAL statement_timeout = '3s'`), `_market_clock_build_once` / `_market_clock_spawn_rebuild` (single-flight memo), and the route: 20 s fresh, stale up to 120 s, then 503. The docstring defines both ingest stamps. |
| `nws_arm.py` | `_phrase()`. `hourly_row` and `_daily_row` read `shortForecast`; `now_block` reads `textDescription`; `now_unavailable` gives null. |
| `model_arm.py` | `condition_text: None` on hour rows, daily rows, the rest-of-today row and `now`. Nothing is added to `absent[]`. |
| `local_forecast.py` | `condition_text` joins `NOW_KEYS`, `HOURLY_KEYS` and `DAILY_KEYS` (after `condition_raw` / `condition`). `CONDITION_TEXT_MAX = 120`. The validator takes null or a non-empty `str` of at most 120 characters: an empty or over-long string raises `ValueError`, a non-string raises `TypeError`. It is not in `_REASONED`. D-09-25-31 is untouched. |
| `tests/test_market_clock.py` | M1–M5. `_install` now clears the memo, because the older endpoint tests share module state. |
| `tests/test_local_forecast.py` | C1–C4, plus three pins amended (§3). |
| `scripts/build_nws_la_heat_fixture.py` | Generates the hand-built LA heat-wave NWS bodies (`--check` reports a stale one). |
| `tests/fixtures/nws/la_heat/*.json` | points, stations, latest, alerts, `forecast.us.json`, `forecastHourly.us.json`. Hand-built, each with `_provenance`. |
| `docs/receipts/local-phrase-d091546/bank.py`, `la_local_body.json` | The banked LA Local body (§4). |
| `docs/receipts/market-clock-d091546/plans.md`, `reds.txt` | The plans, the statements to re-run, the ingest-stamp table, and the reds. |

## 2. Tests, red → green

Reds were run with this branch's tests against HEAD `a7b85d2` source (`reds.txt`).

| test | pins | red on HEAD |
| --- | --- | --- |
| M1 (3 tests) | Every `(SELECT …) AS x` that reads `timeseries_values` has `dataset =` and `series =`; the names are `da_hours, da_published_at, da_first_ingested_at, sp15_da_val, fmm_ts, fmm_val`; the route executes exactly `MARKET_CLOCK_SQL`. **Rehearsed red:** stripping `series` from `da_published_at` (the shipped shape) is caught by name. | no `MARKET_CLOCK_SQL` |
| M2 | 20 concurrent requests on a cold memo (one event loop, httpx over ASGI, build held on a gate): 1 checkout, 1 statement, 20 × 200 with one `as_of`. | `assert 20 == 1` (20 checkouts) |
| M3 | Fresh at +10 s with no query. At +30 s, with the rebuild hung, the stale answer comes back at once, `as_of` unchanged, with exactly one rebuild. At +121 s a request awaits that rebuild, which then times out → 503 `db unavailable: …`. Still failing → 503 again, never the old answer. The read recovers → 200 with a new `as_of`. | `assert 2 == 1` (no memo) |
| M4 | `QueryCanceled` → 503 `db unavailable: canceling statement due to statement timeout`. Three requests make three checkouts (nothing memoised). Statement order is `SET LOCAL statement_timeout = '3s'`, then the read. | no `MARKET_CLOCK_SQL` |
| M5 | Before publication both stamps are null and the state is `DA_BIDDING`. After: `da_first_ingested_at ≤ da_published_at`, each equal to min/max of the ingests fed, and the label prints the **first** (`DA awards published 13:36 PT`). | fields absent from the body |
| C1, C1b, C1c | LA heat body: 9 rows, 6 `unknown` with `condition_text == "Hot"` and the D-09-25-31 reason; each row's text is its **day** period's `shortForecast` verbatim; the "Sunny then Slight Chance Showers And Thunderstorms" day keeps `condition = mostly-clear` from its icon. A night-only row carries the night's words. Strip only: `"  Mostly Sunny \n"` → `"Mostly Sunny"`; `""`, `"   "`, missing and non-string → null. | field absent |
| C2 | Hourly carries `Hot` / `Sunny` / `Mostly Clear` beside its class. `now` = `textDescription` (`"Clear"`), null when absent and null in `now_unavailable`. Model arm: null on `now`, hourly, daily and the past-the-run `now`, with no `condition_text` entry in `absent[]`. | field absent |
| C3 | For each of now / hourly / daily: `""` → ValueError, 121 characters → ValueError, `7` and `["Hot"]` → TypeError. 120 characters passes, null passes, and a missing key is refused. | key absent (validator ignores it) |
| C4 | The banked LA Local body is byte-identical to what `bank.py` makes today and re-validates through `build_payload`. The LAX NWS bodies validate through the route (N6). The model arm validates (C2). | key absent |

## 3. Fixtures and pins changed (C4)

- **No existing fixture file changed.** `tests/fixtures/nws/*.json` and `daily_rows_daytime.main.json` are untouched. The new files are all under `tests/fixtures/nws/la_heat/`.
- **Three pins amended, each saying why in place:**
  - `test_T9f_key_sets_are_byte_identical_to_main`: the three key tuples now include `condition_text`.
  - `test_N6_nws_arm_body_is_byte_identical_to_main`: `_without_lo_period` also strips `condition_text`, so the LAX body still hashes to `LAX_NWS_SHA_MAIN`. The test also asserts `now.condition_text == "Mostly Clear"` and that every hourly/daily value is null (the bodies' `""`).
  - `test_L9_a_daytime_read_is_mains_rows_plus_lo_period`: pops `condition_text` (asserted all null) before comparing to main's rows.

## 4. The banked LA body

`docs/receipts/local-phrase-d091546/la_local_body.json` is `nws_arm.build` + `local_forecast.build_payload` over `tests/fixtures/nws/la_heat/` at `generated_at` 2026-10-01T13:15Z. Its daily rows:

| date | condition | condition_text |
| --- | --- | --- |
| 10-01 | unknown | Hot |
| 10-02 | unknown | Hot |
| 10-03 | unknown | Hot |
| 10-04 | unknown | Hot |
| 10-05 | mostly-clear | Sunny then Slight Chance Showers And Thunderstorms |
| 10-06 | unknown | Hot |
| 10-07 | unknown | Hot |
| 10-08 | mostly-clear | Mostly Sunny |
| 10-09 | clear | Sunny |

**This is not a recording.** `api.weather.gov` is refused by this box's egress (`CONNECT tunnel failed, response 403`, 2026-10-01), as it was on 09-25. The NWS bodies are hand-built to the schema and shaped like d091537's morning: six `hot` days of nine. The gridpoint (LOX/155,45) and station (KCQT) are plausible values, not read ones. Two things should be done before the dashboard lane relies on exact strings:

- Bank a real LA `forecast` body when a session can reach NWS.
- Replace the LAX fixtures' `""` with recorded phrases at the same time.

## 5. The market clock: what changed, and what didn't

- **The query** gains `AND series = %(hub)s` on `da_published_at`, plus the `da_first_ingested_at` subquery with the same predicate. Plans: `docs/receipts/market-clock-d091546/plans.md`.
  - **Before:** `idx_tsv_dataset_ingested_ts` backward, cost 21,880,664 (EXPLAIN only; Gate 0 already showed > 60 s).
  - **After, empty window:** 2.302 ms, 31 buffers.
  - **After, full window:** 6.057 ms, 104 buffers.
  - The spec expected the after-plans to be the architect's read. This session has a Neon connector, so I ran them read-only. Re-run the statements in `plans.md` to verify.
- **The fields:** nothing renamed.
  - `da_published_at` = newest ingest of SP15's target-day rows.
  - `da_first_ingested_at` = first ingest.
  - Both are now in the response body (they were not before; `da_published_at` only fed the label), and both are null before publication. Neither gates a state.
- **The label now prints `da_first_ingested_at`.** The spec called it "the publication time", and the label reads "DA awards published HH:MM PT". Reverse that in one line (`da_published_at=` in `_market_clock_build`) if you prefer. With the real data (§7) the two stamps differ by seconds, so the label moves by at most a minute.
- **The memo:**
  - One key, 20 s fresh, single-flight (waiters `await asyncio.shield(task)`).
  - Between 20 s and 120 s the stale answer is served, with one background rebuild behind it.
  - From 120 s, a request awaits a build: success → fresh, failure → 503.
  - Failures are never stored. `as_of` is the build moment, because `compute_clock` stamps `now`.
  - It mirrors `_season_build_once` / `_season_memo`, with a ceiling on stale that the d091542 memo does not have. I didn't reuse `_DDCache` because it has no stale ceiling, and adding one there would change the dd routes.
- **The timeout:** `SET LOCAL statement_timeout = '3s'` runs as the first statement on the cursor. The pool's connections are not autocommit, so it opens the transaction the read runs in, and the pool ends that transaction on return. **If anyone turns on autocommit for `_pool`, `SET LOCAL` silently stops working**, and the comment in the code says so. A timeout raises `QueryCanceled` → 503 `db unavailable: canceling statement due to statement timeout`.
- **The abandoned request (499):** no disconnect handling was added. With the memo, a request whose browser has gone doesn't own a query. It awaits the one shared build, which is bounded by the 3 s statement timeout. Uvicorn still doesn't cancel the handler, but the handler no longer holds anything. At most one market-clock connection exists at a time per process (Procfile: one Uvicorn process).
- **Not bounded:** the wait for a connection. A build that queues for a checkout can still wait the shared pool's 30 s, but only one build waits, and no request holds a connection while waiting. The season pool's 5 s checkout pattern (`_season_connection`) would bound it if you want that. I didn't add it because the spec didn't ask.

## 6. The sweep — `timeseries_values` reads with no `series`, on polled or page-load routes

I scanned every `FROM timeseries_values` in `main.py` and read each hit in context. Plans are `EXPLAIN` (not executed) on production, 2026-10-01. Nothing here was fixed; this list is the next lane's input.

| route | statement | `series`? | plan | risk |
| --- | --- | --- | --- | --- |
| `/api/market-clock` | `MARKET_CLOCK_SQL` | **all six, fixed here** | index lookups, 2–6 ms | — |
| `/api/timeseries/caiso-hub-lmp` (main.py ~5494) | one read, 3 datasets × 3 hubs × 2 PT days | `series = ANY(hubs)` | Index Scan `idx_tsv_series_ts`, cost 70 | none: names its series as a list |
| `/api/watchboard` hub leg (`_COCKPIT_HUB_SQL`, ~8649) | datasets × series ≥ lo | `series = ANY(series)` | (same index shape) | none |
| `/api/atlas/constraints` | reads `caiso_binding_constraints_daily` / `constraint_geometry_current` / `constraint_fingerprints` | n/a | — | **does not read `timeseries_values`**: out of this rule's reach |
| `/api/timeseries/caiso-fuel-mix`, default mode (~744) | `SELECT DISTINCT ts … WHERE dataset = 'caiso_fuel_mix_5min' ORDER BY ts DESC LIMIT n`, then join | **no** | Index Only Scan `idx_tsv_dataset_ts` backward + Unique + Limit, cost 3,621 (scan total 403,314) | Low today. It stops after `limit` distinct ts, and the newest rows always exist. If the feed stops, it is still bounded by `limit`. Not the market-clock trap (no empty window), but it reads every fuel's row per ts. |
| `/api/timeseries/caiso-fuel-mix`, date/range mode (~725) | `dataset = … AND ts in PT day(s)` | **no** | (not explained; `idx_tsv_dataset_ts` range) | Bounded by the ts window: one dataset, a few days. |
| `/api/timeseries/caiso-peak-demand` (~5225) | `dataset = 'caiso_load_fcst_7day' AND value IS NOT NULL ORDER BY series, ts` | **no** | Index Scan `idx_tsv_series_ts` (dataset prefix only), 85,006 rows est., cost 341,920 | **Medium.** It reads the whole dataset on every call, and it grows with every issuance. A page-load candidate for a memo or a ts floor. |
| `/api/weather/regime` drivers (~2838) | `DISTINCT ON (dataset) … WHERE dataset = ANY(5 monthly indices) ORDER BY dataset, ts DESC` | **no** | Index Scan `idx_tsv_dataset_ts`, 78,935 rows est., Unique, cost 310,402 | **Medium.** It walks all five datasets end to end to keep one row each. A per-dataset `LIMIT 1` lateral (or naming the series) would make it 5 lookups. |
| `/api/structures` gas inventory / node universe (~9679, ~9695) | grouped scans | no / `LIKE` | (catalog route, `idx_tsv_dataset_ts`) | Catalog, not polled; listed for completeness. |

"The ticker's feeds": the API repo doesn't say which routes the dashboard ticker polls beyond `/api/market-clock` and the `latest` block of `/api/timeseries/caiso-hub-lmp` (both above). The dashboard repo isn't in this session; check its poll list against this table.

## 7. What the spec got wrong

1. **"`da_published_at` moves after publication … the feed re-ingests the day."** It doesn't, on the evidence I could find. For SP15, every trade date from 09-15 to 10-01 has 24 rows and 12 distinct `ingested_ts`, all within one 5–28 s burst (`plans.md`, the table). That is one ingest written in batches. The 12 stamps are real, but the newest is seconds after the first, not a later re-ingest. `da_first_ingested_at` is still the honest field and is shipped as specified, but it fixes a seconds-wide difference, not an hours-wide one.
2. **"The lane's box cannot reach Neon."** Sockets can't, but this session has the Neon MCP connector. The after-plans in `plans.md` are real `EXPLAIN (ANALYZE, BUFFERS)` runs, not just statements to run.
3. **"The D-08-02-L companion rule, already in `CLAUDE.md`."** `energylake-api` has no `CLAUDE.md`. The rule may live in the dashboard's. Appendix A's text is below, to land wherever `CLAUDE.md` is meant to be.
4. **The branch name.** See the top of this handback.
5. **C1 assumed "a banked NWS body whose icon token is `hot`".** None existed. The banked LAX bodies are hand-built with `shortForecast: ""` throughout. I built the LA heat body (§4); it is a shaped stand-in, not a capture.
6. **Not in the spec, but seen in the same table:** until 09-30 the DA feed landed each trade date's rows the **evening of that trade date or later**. Trade date 09-15 was first ingested 09-16 23:49Z (09-16 16:49 PT), and every date through 09-29 was ingested on the following UTC day. Only on 09-30 17:49 PT did it catch up and write both 09-30 and 10-01. For those weeks the market clock would have stayed in `DA_MARKET_RUNNING` with `da` degraded, and never reached `DA_PUBLISHED` for tomorrow. This is a feed-timeliness question for the ingest lane, not this one.

## 8. Appendix A, proposed for `CLAUDE.md`

- **D-09-25-75:** a polled route's reads each name one `(dataset, series)`, answer from a single-flight memo, and carry a statement timeout.
- **Trap:** `max(ingested_ts)` over a dataset with no `series` walks the whole dataset when the window is empty. The window is empty every morning.
- **D-09-25-76:** `condition_text` is the source's words verbatim, or null. Nothing is derived from it, and the page decides what to print.
