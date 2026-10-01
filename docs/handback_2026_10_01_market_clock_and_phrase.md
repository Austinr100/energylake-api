# Handback — d091546: the market clock stops holding the pool, and Local carries NWS's own words

**Date:** 2026-10-01. **Lane:** d091546, `energylake-api`. **Rulings:** D-09-25-75, D-09-25-76.
**Branch:** `claude/market-clock-condition-text-ef894b`. The spec named `claude/market-clock-and-phrase-d091546`, but this session's harness fixes the branch it may push to. No PR, no merge, no deploy.

**Suite:** 2116 passed (`pytest -q`). The 11 new market-clock tests and 10 of the 11 new C tests were red on main a7b85d2. The remaining C test is a regression guard and is green on both sides (see `docs/receipts/market-clock-d091546/reds.txt`).

---

## Files

| file | what |
| --- | --- |
| `main.py` ~5680–5935 | `/api/market-clock`. The statement is now `_MARKET_CLOCK_SQL`, and every read names `(dataset, series)`. It is one aggregate for the day-ahead count and both ingest stamps. `_market_clock_build` runs the read under `SET LOCAL statement_timeout = '3s'`. The route is behind a one-key memo: 20 s fresh, then stale for up to 120 s while one rebuild runs, with single-flight builds. Timeouts give 503 with `Retry-After: 5` and are never memoised. |
| `market_clock.py` | `compute_clock` takes `da_first_ingested_at`. The DA_PUBLISHED label prints it, and falls back to `da_published_at` when it is absent. `MarketClock.as_dict` gains `da_published_at` and `da_first_ingested_at` as ISO strings or null. No state depends on either. |
| `local_forecast.py` | `condition_text` joins `NOW_KEYS`, `HOURLY_KEYS` and `DAILY_KEYS`, placed right after `condition`. Adds `condition_text()`: the source's string, stripped, or None. The validator accepts null or a non-empty string of at most 120 characters. It is not in `_REASONED`, so a null needs no reason. |
| `nws_arm.py` | Hourly rows: `shortForecast`. Daily rows: the lead period's `shortForecast` (the day period when there is one). `now`: the observation's `textDescription`. `now_unavailable`: null. |
| `model_arm.py` | Hourly, daily, rest-of-today and `now`: `condition_text: null`. `absent` does not list it. |
| `tests/test_market_clock.py` | The fake pool now counts checkouts and queries and has `transaction()`. An autouse fixture gives every test a cold memo. Adds M1–M5. |
| `tests/test_local_forecast.py` | Adds C1–C4. Amends three pins in place (C4 below). |
| `tests/fixtures/nws/*.la_heat*.json` (6), `scripts/build_nws_la_heat_fixtures.py`, fixture `README.md` | A hand-built NWS response for downtown LA under the Extreme Heat Watch. The script regenerates it; `--check` reports staleness. |
| `docs/receipts/market-clock-d091546/` | `plans.md` (the before-plan, what to look for after, and the local run), `after_plans.sql` (the statements to run), `local_synthetic_plans.txt`, `reds.txt`, and `local_la_heat.json` (the banked Local body). |

## Tests: red → green

| test | pins | red on main |
| --- | --- | --- |
| M1 `every_read_names_dataset_and_series` | Parses `_MARKET_CLOCK_SQL`. Every parenthesised SELECT on `timeseries_values` names `dataset` and `series = %(hub)s`. Includes a self-check that the old shape gets caught. | yes. Also rehearsed on this branch: removing `AND series = %(hub)s` from the `da` read gives `assert ['da'] == []`. |
| M1 `statement_runs_under_a_3s_timeout` | `SET LOCAL statement_timeout = '3s'` runs first, then the statement, in one transaction | yes |
| M2 `twenty_concurrent_requests_on_a_cold_memo_run_one_query` | 20 concurrent `market_clock()` calls produce 1 checkout, 1 query and 1 `as_of` | yes |
| M2 `a_fresh_memo_is_served_without_a_checkout` | A second call 10 s later takes no checkout and returns the same `as_of` | yes |
| M3 `stale_answer_served_during_rebuild_with_its_own_as_of` | At age 30 s, five calls get the old payload (its own `as_of`) and trigger one rebuild. The next call gets the new answer. | yes |
| M3 `past_120s_the_stale_answer_is_not_served` | At age 121 s with a failing read: 503, and the old payload is not served | yes |
| M3 `between_20s_and_120s_a_failed_rebuild_keeps_the_stale_answer` | At age 60 s with a failing read: the stale answer is still served | yes |
| M4 `statement_timeout_is_a_503_and_not_memoised` | `QueryCanceled` gives 503 with "market clock read exceeded 3 s" and `Retry-After: 5`, and the memo stays empty. The next good read is memoised. | yes |
| M5 (×3) | Both stamps are in one aggregate over the same rows, so first ≤ newest and both are null together. Before publication both are null in the body. When published, the label prints the first ingest and the body carries both. | yes |
| C1 (×3) | On the banked LA body, day 1's icon token is `hot`: `condition` is `unknown` with its reason, and `condition_text` is `"Hot"`. Every row's phrase is its day period's `shortForecast`, verbatim. The compound "Sunny then Slight Chance Showers" keeps its words while its class comes from `skc`. Phrases are stripped; empty or missing gives null. A night-only row carries the night's words. | yes |
| C2 (×4) | Every hourly row matches its period's `shortForecast`. `now` is `"Clear"` (from `textDescription`) while its class is `unknown` (icon `hot`). Model-arm rows (Vancouver) are null with no `absent` entry. `now_unavailable` is null. | yes |
| C3 (×2) | The validator refuses `""`, 121 characters, `7` and `["Hot"]`, and accepts null, `"Hot"` and 120 characters, in now, hourly and daily. A missing key is "keys off contract". | yes |
| C4 `every_banked_body_still_builds_a_valid_payload` | LAX SI, LAX US, Vancouver (model) and the LA heat set all pass `build_payload` | no; this is the regression guard |
| C4 `the_banked_la_body_is_the_route_on_the_banked_nws_response` | The route's body for LA at 2026-10-01T20:10Z equals `local_la_heat.json` | yes |

**C4: what changed in existing pins.** No fixture file changed. Three pins were amended in place, each with a `d091546` comment:
- **`test_T9f_key_sets_are_byte_identical_to_main`:** the three key tuples now include `condition_text`.
- **`test_N6_nws_arm_body_is_byte_identical_to_main`:** `_without_lo_period` also strips `condition_text`, and the LAX sha `acc9de1b…` is unchanged. The test first asserts `now.condition_text == "Mostly Clear"` and that every hourly and daily value is null, because the LAX bodies' `shortForecast` is `""`.
- **`test_L9_a_daytime_read_is_mains_rows_plus_lo_period`:** pops `condition_text` (all null) before comparing to `daily_rows_daytime.main.json`.

There is no banked Local payload anywhere in this repo. If the dashboard repo banks bodies for its own tests, they now lack the key. They still validate there unless the dashboard checks keys strictly.

## The plans

**Before** (production, from d091542 Gate 0): a backward Index Scan of `idx_tsv_dataset_ingested_ts`, with Index Cond on `dataset` only, a Filter on `ts`, and estimated cost 21,873,243. The whole statement did not finish in 60 s. It is quoted in full in `docs/receipts/market-clock-d091546/plans.md`.

**Local, synthetic (not Neon):** this box has Postgres 16, so the statements ran against a throwaway instance with 2.18 M synthetic rows and the two named indexes (`local_synthetic_plans.txt`). The new `da` aggregate is an `idx_tsv_series_ts` lookup in both windows (0.010 / 0.048 ms). The whole after-statement takes about 2 ms. The old statement's `da_published_at` was a full scan in the empty window. One thing to watch: locally the `fmm` read is a BitmapAnd that touches `idx_tsv_dataset_ingested_ts`. It is the same before and after, and Gate 0 saw it as an index lookup on Neon. `plans.md` covers it.

**After, on Neon:** the architect runs these (§3.1). `after_plans.sql` has the before and after statements, each with the window empty and full, plus an M5 check on real rows. Every block is `BEGIN; SET LOCAL statement_timeout …; EXPLAIN …; ROLLBACK;`. `plans.md` lists the expected plan for each read and the STOP-Q criterion. I did not run anything on Neon.

## The sweep (§2.1)

These are the routes I classified as polled or page-load, and every statement they run against `timeseries_values`:

| route | line | statement | `series`? | likely plan |
| --- | --- | --- | --- | --- |
| `/api/market-clock` | ~5709 | `da` aggregate, `sp15_da_val`, `fmm` | equality on all three (this lane) | `idx_tsv_series_ts` lookups |
| `/api/market-clock` (main, before) | old 5734 | `da_published_at` | **none** | backward walk of `idx_tsv_dataset_ingested_ts` over the whole dataset when the window is empty. **Fixed here.** |
| `/api/timeseries/caiso-hub-lmp` | ~5492 | `dataset = ANY(3) AND series = ANY(HUB_LMP_HUBS: NP15, SP15, ZP26) AND ts in 2-day window` | `= ANY`, a bounded list of 3 | `(series, ts)` range per hub, `dataset` filtered |
| `POST /api/watchboard` (basis tiles only) | `_COCKPIT_HUB_SQL` ~8519 | `dataset = ANY(≤3) AND series = ANY(≤3, validated against COCKPIT_HUB_REF_SET) AND ts >= now−8d` | `= ANY`, bounded at 3 | `(series, ts)` range per hub |
| `/api/atlas/constraints` | ~6748 | reads `caiso_binding_constraints_daily`, landmarks, `constraint_geometry_current` | n/a: no `timeseries_values` read | in-process cache, 5 min |
| publication clock (brief routes) | — | pure code; the brief routes read `joule_briefs` | n/a | — |

**Result:** the market clock was the only polled or page-load read with no `series`. Nothing for the next lane on this list. Two caveats:
- I couldn't see the dashboard's ticker code from this repo. I found the ticker's feeds from the API's own comments ("banner ticker", "ticker chip", "ticker block").
- `constraint_geometry_current` is a view the repo does not define, so I couldn't see what it reads from.

## The abandoned request

With the memo, a 499 no longer owns a query. At most one market-clock build is in flight per process. A request that arrives during a build awaits that build through `asyncio.shield`, so the client leaving cancels nothing and starts nothing. The build itself holds one connection for at most 3 s (the statement timeout), plus the `SET LOCAL`. Three abandoned tabs plus two live ones now cost one connection for milliseconds, not five for minutes. I added no disconnect handling.

## The banked Local body for Los Angeles

`docs/receipts/market-clock-d091546/local_la_heat.json`: the route's body at 2026-10-01T20:10Z for (34.052, −118.244), built from the hand-built NWS response in `tests/fixtures/nws/*.la_heat*.json`. `receipts.memo` is nulled because it describes the request. C4 pins it, and `BANK_LA_BODY=1 pytest -k C4_the_banked` re-banks it.

| date | condition | condition_text |
| --- | --- | --- |
| 10-01 | unknown | Hot |
| 10-02 | unknown | Hot |
| 10-03 | unknown | Hot |
| 10-04 | unknown | Hot |
| 10-05 | unknown | Haze |
| 10-06 | unknown | Areas Of Smoke |
| 10-07 | clear | Sunny then Slight Chance Showers |

`now` is `unknown` / "Clear". The 48 hourly rows each carry "Hot", "Sunny" or "Mostly Clear". The alert is the Extreme Heat Watch.

## What the spec got wrong

1. **`da_published_at` was never a body field.** `MarketClock.as_dict` didn't emit it; it only fed the DA_PUBLISHED label. I added both stamps to the body. The label now prints `da_first_ingested_at`, so "DA awards published 13:05 PT" no longer drifts to the latest re-ingest. That is a visible change to the label after publication. If the ticker should keep the old wording, it's a one-line revert in `compute_clock`.
2. **Adding `AND series` to the `max()` fixes it; the single aggregate is hardening.** A lone `max()` or `min()` lets the planner use its min/max shortcut, which rewrites it as an ordered walk of an index on `ingested_ts`. That index is `idx_tsv_dataset_ingested_ts`, the trap. With `series` named, the planner chose `idx_tsv_series_ts` on Neon for `max` (spec §0, 0.078 ms) and locally for `min` (0.06 ms, `local_synthetic_plans.txt`). But that was a cost choice. So `da_hours`, `max` and `min` are now one aggregate: with `count()` present the shortcut can't apply. The statement no longer has "five scalar subqueries": it has three reads (`da`, `sp15_da_val`, and a LATERAL `fmm`, which replaces two identical walks for `fmm_ts` and `fmm_val`). M1 parses that shape.
3. **The memo pattern.** The spec pointed at `_DDCache` (`main.py` ~15798). That cache serialises builds behind one lock and has no cap on stale age. The season memo (`_season_build_once`, d091542) is the closer pattern: a task-based single flight, shielded, one rebuild behind a stale answer. The market clock's memo copies that pattern for one key and adds the 120 s cap.
4. **STOP-T didn't fire, but the banked bodies have no words.** The NWS arm reads the right endpoints: `/gridpoints/{g}/forecast`, `/forecast/hourly` and `/stations/{s}/observations/latest`. Every period in the banked bodies carries the `shortForecast` key, but its value is always `""`, because those bodies were hand-built, never recorded (fixture README). `latest.json`'s `textDescription` is "Mostly Clear". `api.weather.gov` is still refused by this box's egress proxy, so C1's `hot` body is also hand-built. Its gridpoint (LOX/155,45) and its values are illustrative. Replace it with recorded bodies when a session can reach NWS.
5. **"Six of nine daily rows."** NWS gives 14 periods, which makes 7 rows. On production, days 8–10 come from the model arm (`receipts.notes`: "days 8–10 …"), and those rows carry `condition_text: null` by D-09-25-76. So the page will never have more than 7 phrases. The 3 model days stay house words only.
6. **"The day period's phrase."** A night-only row (an evening read: "Tonight", then tomorrow) has no day period. It carries the night's phrase, because that's what "as `condition` is the lead period's class" implies. C1 pins it.
7. **A 121-character phrase fails the whole body.** The validator refuses it, as specified. `build_payload` runs after the arm fallback, so the refusal surfaces as a 500 for that point, not a fallback to the model arm. NWS's `shortForecast` strings are generated and short, so this is unlikely. But the source decides the length, not us. If the architect wants it, the safe rule is in the builder: null with no reason when the source's phrase is over 120 characters. I didn't do that, because the ruling says "verbatim or null when the source sent none".
8. **`CLAUDE.md`.** The spec says the D-08-02-L companion rule is "already in `CLAUDE.md`". This repo has no `CLAUDE.md`. Appendix A's text is proposed below for whichever file holds the API's rules.
9. **The pool's checkout wait.** The 3 s timeout bounds how long a market-clock read *holds* a connection, not how long a build *waits* for one (the shared pool's 30 s). That's fine for D-09-25-75 because waiting holds nothing. But with an empty or past-120 s memo during a pool outage, the route can take 30 s to return its 503.

## Proposed for `CLAUDE.md` (API), Appendix A

- **D-09-25-75:** every read on a polled route names one `(dataset, series)`. The route answers from a single-flight memo, and the read carries a statement timeout.
- **Trap:** `max(ingested_ts)` over a dataset with no `series` walks the whole dataset when the window is empty, and the window is empty every morning. A lone `max()` or `min()` with `series` named can still be planned as that walk (the min/max shortcut). Put it beside a `count()` in one aggregate, so the only access path is the `(series, ts)` range.

## For §3

- **3.1:** run `after_plans.sql` blocks 3 and 4. STOP-Q is in `plans.md`.
- **3.2:** with the memo, at most one request in 20 s per process pays the read. The rest answer from memory.
- **3.3:** the dashboard's `wordLabel` reads `condition_text` on every NWS row. It is null on model-arm rows, so a neutral class there keeps the house word.
