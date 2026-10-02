# Handback — d091551 — the remaining series-less reads, and the market clock's hardening

**Date:** 2026-10-01. **Lane:** d091551, `energylake-api`. **Ruling applied:** D-09-25-75.
**Branch:** see the end of this file. No PR, no merge, no deploy.

## Verdict

- **`/api/weather/regime` drivers:** one `LIMIT 1` lateral per `(dataset, series)`, in one statement. Production plan: **533 buffers / 92 ms → 30 buffers / 0.116 ms**, five `idx_tsv_series_ts` lookups. **STOP-Q does not fire** (30 ≤ 100). Both statements return the same five rows.
- **`/api/timeseries/caiso-peak-demand`:** the body serves the whole history (all 76 operating dates), so the read keeps its shape. It is now memoised (`_DDCache`, 300 s, allow_stale, single-flight) under a 5 s `SET LOCAL statement_timeout` in an explicit transaction. **STOP-B does not fire:** the body banked from d08d602 is byte-identical on this branch, cold, fresh and stale.
- **`/api/timeseries/caiso-fuel-mix`:** not changed. It is bounded by `LIMIT`, and it has no empty-window case because the newest rows always exist.
- **Market clock:** all five §1.4 items are in.
- **`CLAUDE.md`:** created.
- **Tests:** 2137 pass on the full suite (2123 before, plus 14 new). Every new test was seen red (`docs/receipts/polled-routes-d091551/reds.txt`).
- **Found, not fixed:** `/api/weather/regime` should be returning 500 in production today because of NULL CPC dates (§6.7).

## 1. Files

| file | what |
| --- | --- |
| `main.py` | `_REGIME_DRIVERS` gains each driver's series. `_REGIME_DRIVERS_SQL` is the lateral, and the route runs it. Peak demand splits into the route (memo, 503s, `as_of` stamped per request) and `_peak_demand_build` (the read under `SET LOCAL statement_timeout = '5s'` inside `conn.transaction()`, plus the unchanged derivation). Adds `PEAK_DEMAND_SQL`, `PEAK_DEMAND_MEMO_TTL` / `_STATEMENT_TIMEOUT` / `_BUILD_TIMEOUT`, and `_peak_demand_cache` (defined after `_DDCache`). Market clock: `conn.transaction()` around `SET LOCAL` and the read; both stamps passed to `compute_clock` by name; `MARKET_CLOCK_RETRY_AFTER = 5` on the 503. |
| `market_clock.py` | `compute_clock(..., da_first_ingested_at=None)` keyword. The DA_PUBLISHED label prints it, and falls back to `da_published_at` when it is absent. The body is unchanged: the route still adds both stamps itself. |
| `CLAUDE.md` | New: d091546's Appendix A (three entries) and one line on the lateral. |
| `tests/test_polled_routes_d091551.py` | R1, R2, D1. |
| `tests/test_market_clock.py` | The autouse cold-memo fixture, `transaction()` on both fakes (the counting pool also records whether each statement ran inside one), and d091551 M1–M4. |
| `tests/test_peak_demand.py` | `transaction()` on the fake connection, and an autouse fixture that clears the new memo (the existing tests install different rows per test). No assertion changed. |
| `tests/fixtures/polled_routes_d091551/` | `regime_rows.json` / `regime_body.json`, `peak_demand_rows.json` / `peak_demand_body.json`. The bodies are the raw response bytes from d08d602. |
| `docs/receipts/polled-routes-d091551/bank.py` | Writes the inputs, and banks or `--check`s the bodies against any checkout. Its docstring says which rows are production's and which are generated. |
| `docs/receipts/polled-routes-d091551/rehearse.py`, `reds.txt` | The reds. |
| `docs/receipts/polled-routes-d091551/plans.md` | The production plans, before and after, plus what the two datasets hold. |
| `docs/receipts/polled-routes-d091551/tx_probe.py`, `tx_probe.txt` | §1.4.1 against a real Postgres 16 (§6.3). |

## 2. Tests, red → green

`reds.txt` has three parts: this branch's new tests on HEAD source (d08d602), then each test against this branch with one deliberate break, then all of them green.

| test | pins | red |
| --- | --- | --- |
| R1 (3) | The driver statement has a `VALUES` list equal to `_REGIME_DRIVERS`'s `(dataset, series)` pairs, and exactly one lateral. The lateral reads `timeseries_values` once, names `t.dataset = d.dataset AND t.series = d.series`, and ends `ORDER BY t.ts DESC LIMIT 1`. There is no `DISTINCT ON`, and the route executes exactly that statement. **Rehearsed red:** the shipped `DISTINCT ON` statement is caught, and so are dropping the series and dropping the `LIMIT 1`. | HEAD: no `_REGIME_DRIVERS_SQL`. Break: series dropped → 2 failed. |
| R2 | The regime body is byte-identical (1,207 bytes) to d08d602's on production's driver rows. | Break: chips keyed by series instead of dataset → failed. |
| D1 (4) | The peak-demand body is byte-identical (25,034 bytes) to d08d602's, cold, fresh (no second read) and stale. `as_of` is the request instant even when the answer is memoised. 20 concurrent cold requests make one read. A failed read is `503 db unavailable: …` and is not memoised. Statement order is `SET LOCAL statement_timeout = '5s'`, then the read. | HEAD: no `_peak_demand_cache`. Breaks: ties to the latest hour → byte test fails; memo keeps the build's `as_of` → `as_of` test fails. |
| M1 (2) | §1.4.1. `SET LOCAL` and the read both run inside `conn.transaction()`, which commits after the read, and a timeout rolls it back. | HEAD: no transaction. Break: `conn.transaction()` removed → 2 failed. |
| M2 (2) | §1.4.3. M2a leaves the memo warm on purpose, without the `cold` fixture, whose monkeypatch undo would reset the memo itself. M2b, next in file order, finds it cold. | Break: fixture not autouse → M2b fails. Passes on HEAD source, because the fixture is test code (§6.4). |
| M3 | §1.4.4. 60 s after a build, with the lake failing, the stale answer is served. The failed rebuild leaves the entry the same object and the in-flight slot empty. At 110 s the same answer is served again, behind another attempt (3 checkouts). | Breaks: a failed build memoised → fails; stale not served → fails. Passes on HEAD (§6.4). |
| M4 | §1.4.5. The 503 carries `Retry-After: 5`. | HEAD: no header. Break: header removed → fails. |

§1.4.2, `compute_clock`'s keyword, has no test of its own, as the spec's table says. d091546's M5 still pins the label printing the first ingest, now through that keyword.

## 3. The plans

These are in `plans.md`, all production, read-only, through the Neon connector.

| route | before | after |
| --- | --- | --- |
| regime drivers | Bitmap Heap Scan via `idx_tsv_dataset_ingested_ts`, 6,724 rows, Sort, Unique. **533 buffers, 91.8 ms** | Values Scan × Limit 1 over `idx_tsv_series_ts`, 5 loops. **30 buffers, 0.116 ms** |
| peak demand | `idx_tsv_series_ts` (dataset prefix), 65,664 rows, Incremental Sort. **57,338 buffers (all hits), 75.9 ms**, on every request | The same statement, at most once per 300 s per process and one at a time, under a 5 s timeout |

## 4. Why peak demand keeps the whole history

The dataset is the forecast's diagonal: one vintage per operating date (76 dates and 76 `publish_time`s, 2026-07-23 … 10-06 PT). The route puts every date into `operating_dates`, and a day for each into every area. The spec offered a `ts` floor for the case where the route "serves the newest issuance only". It doesn't: the newest issuance is one date. Any floor drops dates from the body, which STOP-B forbids. So the read stays as it is and only the memo is added.

The history grows by 36 × 24 rows a day, and with it the body (2,736 day cells today). Whether the page needs more than a week is a frontend question. Answering it changes the body, so it needs a ruling, not this lane.

## 5. Choices the spec left open

- **TTLs and timeouts.** Peak demand is 300 s fresh, has a 5 s statement timeout (measured at 76 ms) and a 15 s build backstop (`_DDCache.build_timeout`, which also covers the checkout wait). `Retry-After` on the market clock is 5 s: the next request can build at once, so a longer hint only delays recovery.
- **`_DDCache` has no stale ceiling.** If every refresh fails, peak demand keeps serving its last answer, with no 120 s cutoff like the market clock's. That is acceptable for a dataset that changes once a day. The route doesn't carry `_dd_envelope`'s `cache` block, because that would change the body.
- **The regime route got the lateral only**, not a memo or a timeout. It is a page-load route, the spec asked for neither, and its read is now 0.1 ms.

## 6. What the spec got wrong

1. **The row counts were the planner's estimates.** "85,006 rows" for peak demand is 65,664 when executed. "78,935 rows" for the regime drivers is 6,724, read by a Bitmap Heap Scan on `idx_tsv_dataset_ingested_ts`, not an index scan on `idx_tsv_dataset_ts`. The conclusions still hold: both routes read whole datasets.
2. **§1.2's first branch could not apply.** See §4: the route serves every issuance, not just the newest.
3. **§1.4.1's premise.** "So it cannot leak to the next checkout": it never could. `SET LOCAL` is transaction-scoped, and the pool's `connection()` commits on exit. What the explicit transaction actually fixes is autocommit: under autocommit a bare `SET LOCAL` is a no-op with a server WARNING, so the timeout would silently vanish. Under today's pool, `conn.transaction()` is a **SAVEPOINT**, because the pre-ping's `SELECT 1` has already opened a transaction. `tx_probe.txt` shows all four cases on a real Postgres 16: the shipped code's next checkout reads `0` every time, and only the explicit transaction holds `3s` under autocommit.
4. **§1.4.3 and §1.4.4 pin what HEAD already did.** d091546's `_install` and `cold` fixture already cleared the memo, and its memo already dropped failed builds while serving stale. The new tests pass on HEAD source, so their reds come from deliberate breaks (`reds.txt` part 2), not from HEAD.
5. **The names M1–M4 collide** with d091546's M1–M5 in the same file. The new ones are `test_d091551_M1…M4`.
6. **R2 on a banked fixture cannot see the SQL.** A fake pool answers by substring, so an `ORDER BY` break left R2 green. R2 pins the route's Python over the rows. The SQL's rows are pinned by the production comparison in `plans.md`: same five rows before and after.
7. **Not in the spec: `/api/weather/regime` should be a 500 in production today.** Three of the four live CPC vintages (`610temp`, `610prcp`, `814prcp`, issued 2026-09-30, format `gif`) have `valid_start` / `valid_end` NULL. The route calls `.isoformat()` on them. d08d602 with those rows returns `500 Internal Server Error` (checked here with a fake pool; I did not call the production route). This lane does not change that, before or after. The fix is one guard per field (`r["valid_start"].isoformat() if r and r["valid_start"] else None`), but it changes the route's output, so it needs its own lane. §2's "twenty reads of each of the two routes" will show it.
8. **"The `allow_stale` helper"** is the `_DDCache` class (`allow_stale=True`). It reads `time.monotonic` directly, so D1 ages an entry by editing `built_mono`.
9. **Left as found:** the market-clock docstring still says "the feed re-ingests a published day", which d091546 §7.1 disproved. I changed only the comment at the call site I was editing.

## Branch

`claude/polled-routes-hardening-d091551`, as the spec names it. This time the push was accepted under that name. The session's harness branch, `claude/polled-routes-hardening-d091551-lj9nz5`, carries the same commits. No PR, no merge, no deploy.
