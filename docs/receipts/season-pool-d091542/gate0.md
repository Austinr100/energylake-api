# Gate 0 — who held the pool (d091542 §2.1)

**Read:** 2026-10-01, 12:20–12:35Z, by the lane, read-only. **Verdict: `/api/market-clock`.** Five of its requests held all five shared connections from 10:36:59 to 10:40:11Z. The degree-day board did not; it was one of the requests left waiting. STOP-H does not apply: the holder is one of the two §0 named.

## How it was read, and how that differs from §2.1

§2.1 asked for a reproduction on a local or staging run against production Neon, logging `_pool.get_stats()` once a second. This container cannot open a socket to Neon. The egress proxy refuses both TCP 5432 and Neon's HTTPS SQL endpoint (`CONNECT tunnel failed, response 403`). So no process here could hold a real pool. The holder was read from production's own record of the morning instead:

1. **Railway's HTTP log** for service `web`, 10:35–10:42Z. Every request that ran 5 s or longer is in `railway_http_2026-10-01T1035-1042Z.txt`, with its start (the log stamps a request's end; start = end − duration).
2. **The deploy log** for the same window, which carries the query strings: `area=reservoir%3Aca_major8&var=storage` and `area=snow%3Aca_state&var=swe_in` 503 at 10:38:24Z.
3. **Neon, read-only:** the market-clock statement's plan, and the statement itself, run once.

## The timeline (UTC; start → end)

| start | end | s | status | route |
| --- | --- | --- | --- | --- |
| 10:36:42.9 | 10:36:57.4 | 14.5 | **499** | /api/market-clock |
| 10:36:42.9 | 10:37:39.8 | 56.9 | 200 | /api/weather/temp-matrix |
| 10:36:54.4 | 10:36:57.4 | 3.0 | **499** | /api/market-clock |
| 10:36:57.4 | 10:36:59.8 | 2.3 | **499** | /api/market-clock |
| 10:36:59.8 | **10:40:11.9** | **192.1** | 200 | /api/market-clock |
| 10:36:59.8 | 10:37:29.8 | 30.0 | 500 | /api/timeseries/caiso-hub-lmp (checkout timeout) |
| 10:37:00.4 | 10:37:40.3 | 39.8 | 200 | /api/almanac/daily |
| 10:37:54.7 | **10:40:11.9** | **137.1** | 200 | /api/market-clock |
| 10:37:54.7 | 10:38:24.7 | 30.0 | **503** | /api/weather/season (ca_major8 · storage) |
| 10:37:54.7 | 10:38:24.7 | 30.0 | **503** | /api/weather/season (ca_state · swe_in) |
| 10:37:54.7 | 10:38:54.7 | 60.0 | 503 | /api/weather/dd/forecast |
| 10:37:54.7 | 10:38:55.4 | 60.7 | 503 | /api/weather/dd/daily |
| 10:38:49.5, 10:39:19.6 | +30.0 | 30.0 | 503 | /api/weather/season (two more) |
| 10:39:40–49 | 10:40:12–14 | 25–32 | 200 | dd/cumulative, dd/forecast, dd/daily, five /season reads |

**Reading it:**

- From 10:36:59 nothing could get a connection. `caiso-hub-lmp` started then and gave up at exactly 30.0 s, the pool's checkout timeout.
- temp-matrix and almanac/daily released theirs by 10:37:40. At 10:37:54 five requests arrived; one market-clock got a connection and the other four, both season reads included, waited the full 30 s. **So at 10:37:54 four connections were already held, and none of their holders appears as a running request in the HTTP log apart from the 192 s market-clock.**
- What fills that gap is the three market-clock **499s**. Railway records 499 when the browser goes away, and it logs the request then. Uvicorn does not cancel the handler, so the handler keeps its connection until its query returns. Five market-clock requests started between 10:36:42 and 10:37:54 (three abandoned, two that finished in 192 s and 137 s). That is five connections: the whole pool.
- The two surviving market-clock requests end within 4 ms of each other at 10:40:11.85, and every waiter is served within 3 s of that (the season reads at 10:40:12–14, the dd board at 10:40:12–13). The pool came free all at once, when the market-clock statements finished.
- The degree-day board was a **victim**. Its requests (10:37:54 and 10:39:40) are among the waiters, and its 32 s at 10:39:40 is 30 s of waiting plus about 2 s of build. Nothing in the log shows a cold 52 s cumulative build in this window. A container started at 10:27:58Z; whether its boot warm finished is not in the log, and the timeline does not need it.

## Why market-clock takes minutes before the day-ahead market publishes

The route's one statement carries five scalar subqueries. Neon's plan for one of them, today's (target trade date 2026-10-02 PT):

```
(SELECT max(ingested_ts) FROM timeseries_values
   WHERE dataset = 'caiso_lmp_da_hourly'
     AND ts >= '2026-10-02 07:00+00' AND ts < '2026-10-03 07:00+00')
→ Limit
    → Index Scan Backward using idx_tsv_dataset_ingested_ts on timeseries_values
        Index Cond: (dataset = 'caiso_lmp_da_hourly')
        Filter: ts >= … AND ts < …            (estimated total cost 21,873,243)
```

It has no `series` predicate, so the planner walks the whole dataset backwards by `ingested_ts` until it finds a row inside tomorrow's window. Before CAISO publishes the day-ahead market (about 13:00 PT), **tomorrow has no rows**, so the scan reads every hourly row of every node in `caiso_lmp_da_hourly` and finds nothing. At 10:38Z it was 03:38 PT. Run once by hand at 12:30Z (05:30 PT, still before publication), the whole statement **did not finish in 60 s**: the console's runner timed out. The other four subqueries are single-series index lookups (estimated costs 4.7 and 8.7).

The ticker polls market-clock from every open page, and every page load adds another request. Each request holds a shared connection for minutes, whether or not its browser is still there. Five of them fill the pool.

## What this lane does about it, and what it does not

- **Does (D-09-25-73):** the season routes stop using the shared pool (§2.2). The morning's failure cannot reach a season read again, whatever market-clock does.
- **Does not:** fix market-clock. That fix is one predicate (`AND series = %(hub)s` on the `max(ingested_ts)` subquery, or the hub's own ingest time), and it belongs to its own lane with its own test. It should come soon: until it ships, every other route on the shared pool (`caiso-hub-lmp`, which 500s rather than 503s on a checkout timeout, and the dd ledger among them) is still exposed every morning before the day-ahead market publishes.

## Neon's ceiling (§2.2)

Read 2026-10-01 12:29Z: `max_connections` = **901** (behind pgbouncer). Open: **14** backends, 7 of them client backends, 2 active. The season pool adds at most 3 per process.
