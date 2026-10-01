# d091546 — the market-clock statement, before and after (Neon, production, read-only)

**Read:** 2026-10-01, ~13:50Z (06:50 PT, before the DAM published trade date 2026-10-02), project `fancy-block-96153928` (Energylake), default branch, through the Neon connector attached to this session. The spec assumed this box could not reach Neon (d091542 Gate 0 said the egress proxy refuses TCP 5432 and the HTTPS SQL endpoint). That is still true for sockets; the **Neon MCP connector** reached it. Every statement below is a SELECT or an EXPLAIN of one. Nothing was written.

Windows used (target trade date = tomorrow PT):

| window | tstart | tend | he17 |
| --- | --- | --- | --- |
| **empty** — trade date 2026-10-02 (not yet published) | `2026-10-02 07:00+00` | `2026-10-03 07:00+00` | `2026-10-02 23:00+00` |
| **full** — trade date 2026-10-01 (published 09-30 17:49 PT) | `2026-10-01 07:00+00` | `2026-10-02 07:00+00` | `2026-10-01 23:00+00` |

## Before (as shipped), empty window — `EXPLAIN` only

Not run with `ANALYZE`: d091542 Gate 0 ran it by hand at 12:30Z and it did not finish in 60 s, and doing that again on production to print a number already known costs a minute of the compute every other route shares.

```
Result  (cost=4554.71..4554.72 rows=1 width=64)
  InitPlan 1          -- da_hours
    ->  Aggregate  (cost=8.72..8.73)
          ->  Index Scan using idx_tsv_series_ts  (cost=0.69..8.72 rows=1)
                Index Cond: dataset = 'caiso_lmp_da_hourly' AND series = 'SP15' AND ts >= … AND ts < …
                Filter: value IS NOT NULL
  InitPlan 3          -- da_published_at   ← THE ONE
    ->  Result  (cost=4527.91..4527.92)
          InitPlan 2
            ->  Limit  (cost=0.57..4527.91 rows=1)
                  ->  Index Scan using idx_tsv_dataset_ingested_ts  (cost=0.57..21880664.00 rows=4833)
                        Index Cond: dataset = 'caiso_lmp_da_hourly'
                        Filter: ts >= '2026-10-02 07:00+00' AND ts < '2026-10-03 07:00+00'
  InitPlan 4          -- sp15_da_val: Index Scan using timeseries_values_pkey (cost 8.59)
  InitPlan 5          -- fmm_ts:      Index Scan using idx_tsv_series_ts (Limit cost 4.73)
  InitPlan 6          -- fmm_val:     Index Scan using idx_tsv_series_ts (Limit cost 4.73)
```

The `Limit` cost (4,528) is the planner betting it will meet a matching row early in the backward walk. With the window empty it never does, so the real cost is the scan's full 21,880,664: the whole of `caiso_lmp_da_hourly`.

## After, empty window — `EXPLAIN (ANALYZE, BUFFERS)`

```
Result  (actual time=2.251..2.253 rows=1)  Buffers: shared hit=27 read=4
  InitPlan 1 da_hours             Index Scan idx_tsv_series_ts  rows=0  1.932 ms  hit=2 read=3
  InitPlan 2 da_published_at      Index Scan idx_tsv_series_ts  rows=0  0.012 ms  hit=5
  InitPlan 3 da_first_ingested_at Index Scan idx_tsv_series_ts  rows=0  0.005 ms  hit=5
  InitPlan 4 sp15_da_val          Index Scan timeseries_values_pkey rows=0  0.248 ms  hit=3 read=1
  InitPlan 5 fmm_ts               Index Scan idx_tsv_series_ts  rows=1  0.027 ms  hit=6
  InitPlan 6 fmm_val              Index Scan idx_tsv_series_ts  rows=1  0.013 ms  hit=6
Planning Time: 0.433 ms
Execution Time: 2.302 ms
```

Index Cond on InitPlans 1–3 is `dataset = 'caiso_lmp_da_hourly' AND series = 'SP15' AND ts >= '2026-10-02 07:00+00' AND ts < '2026-10-03 07:00+00'`. The 1.9 ms on InitPlan 1 is three cold page reads; the subquery the spec measured (0.078 ms, 5 buffers) is InitPlan 2 here, at 0.012 ms and 5 buffers.

## After, full window — `EXPLAIN (ANALYZE, BUFFERS)`

```
Result  (actual time=5.999..6.002 rows=1)  Buffers: shared hit=78 read=26 dirtied=24
  InitPlan 1 da_hours             Index Scan idx_tsv_series_ts  rows=24  4.858 ms  hit=5 read=24 dirtied=24
  InitPlan 2 da_published_at      Index Scan idx_tsv_series_ts  rows=24  0.041 ms  hit=29
  InitPlan 3 da_first_ingested_at Index Scan idx_tsv_series_ts  rows=24  0.016 ms  hit=29
  InitPlan 4 sp15_da_val          Index Scan timeseries_values_pkey rows=1  1.021 ms  hit=3 read=2
  InitPlan 5 fmm_ts               Index Scan idx_tsv_series_ts  rows=1  0.020 ms  hit=6
  InitPlan 6 fmm_val              Index Scan idx_tsv_series_ts  rows=1  0.010 ms  hit=6
Planning Time: 0.440 ms
Execution Time: 6.057 ms
```

`dirtied=24` is hint-bit setting on the first read of freshly written heap pages, not a write by the statement.

**STOP-Q does not fire:** every subquery, in both windows, is an index lookup on `idx_tsv_series_ts` or the primary key.

## The statements, for the architect to re-run

Substitute the window. The route sends `SET LOCAL statement_timeout = '3s'` first, in the same transaction.

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT
  (SELECT count(*) FROM timeseries_values
     WHERE dataset = 'caiso_lmp_da_hourly' AND series = 'SP15'
       AND ts >= '2026-10-02 07:00+00' AND ts < '2026-10-03 07:00+00'
       AND value IS NOT NULL)                                   AS da_hours,
  (SELECT max(ingested_ts) FROM timeseries_values
     WHERE dataset = 'caiso_lmp_da_hourly' AND series = 'SP15'
       AND ts >= '2026-10-02 07:00+00' AND ts < '2026-10-03 07:00+00') AS da_published_at,
  (SELECT min(ingested_ts) FROM timeseries_values
     WHERE dataset = 'caiso_lmp_da_hourly' AND series = 'SP15'
       AND ts >= '2026-10-02 07:00+00' AND ts < '2026-10-03 07:00+00') AS da_first_ingested_at,
  (SELECT value FROM timeseries_values
     WHERE dataset = 'caiso_lmp_da_hourly' AND series = 'SP15'
       AND ts = '2026-10-02 23:00+00' AND value IS NOT NULL LIMIT 1) AS sp15_da_val,
  (SELECT ts FROM timeseries_values
     WHERE dataset = 'caiso_lmp_rt_15min' AND series = 'SP15' AND value IS NOT NULL
     ORDER BY ts DESC LIMIT 1)                                  AS fmm_ts,
  (SELECT value FROM timeseries_values
     WHERE dataset = 'caiso_lmp_rt_15min' AND series = 'SP15' AND value IS NOT NULL
     ORDER BY ts DESC LIMIT 1)                                  AS fmm_val;
```

For the full window, use `2026-10-01 07:00+00` / `2026-10-02 07:00+00` / `2026-10-01 23:00+00`.

## What `ingested_ts` actually holds (SP15, trade dates 2026-09-15 … 2026-10-01)

```sql
SELECT (ts AT TIME ZONE 'America/Los_Angeles')::date AS trade_date, count(*) AS rows,
       count(DISTINCT ingested_ts) AS stamps, min(ingested_ts), max(ingested_ts),
       max(ingested_ts) - min(ingested_ts) AS spread
FROM timeseries_values
WHERE dataset = 'caiso_lmp_da_hourly' AND series = 'SP15'
  AND ts >= '2026-09-15 07:00+00' AND ts < '2026-10-03 07:00+00'
GROUP BY 1 ORDER BY 1;
```

| trade date | rows | stamps | first ingest (Z) | newest ingest (Z) | spread |
| --- | --- | --- | --- | --- | --- |
| 09-15 | 24 | 12 | 09-16 23:49:18 | 09-16 23:49:29 | 11.7 s |
| 09-16 | 24 | 12 | 09-17 23:40:18 | 09-17 23:40:34 | 15.9 s |
| 09-17 | 24 | 12 | 09-18 23:34:39 | 09-18 23:34:56 | 16.8 s |
| 09-18 | 24 | 12 | 09-19 23:30:04 | 09-19 23:30:18 | 13.9 s |
| 09-19 | 24 | 12 | 09-20 23:30:05 | 09-20 23:30:19 | 13.9 s |
| 09-20 | 24 | 12 | 09-21 22:17:00 | 09-21 22:17:16 | 15.8 s |
| 09-21 | 24 | 12 | 09-22 23:48:46 | 09-22 23:49:02 | 15.8 s |
| 09-22 | 24 | 12 | 09-23 23:56:56 | 09-23 23:57:24 | 28.1 s |
| 09-23 | 24 | 12 | 09-25 00:00:02 | 09-25 00:00:27 | 25.0 s |
| 09-24 | 24 | 12 | 09-25 21:51:41 | 09-25 21:51:56 | 14.4 s |
| 09-25 | 24 | 12 | 09-26 23:52:00 | 09-26 23:52:12 | 11.1 s |
| 09-26 | 24 | 12 | 09-27 23:57:40 | 09-27 23:57:54 | 14.9 s |
| 09-27 | 24 | 12 | 09-28 23:32:14 | 09-28 23:32:24 | 10.0 s |
| 09-28 | 24 | 12 | 09-29 22:40:22 | 09-29 22:40:32 | 9.6 s |
| 09-29 | 24 | 12 | 09-30 22:57:12 | 09-30 22:57:25 | 13.2 s |
| 09-30 | 24 | 12 | 10-01 00:49:31 | 10-01 00:49:43 | 11.8 s |
| 10-01 | 24 | 12 | 10-01 00:49:43 | 10-01 00:49:48 | 5.4 s |

Each trade date has 12 distinct stamps, and they fall inside one write burst of 5–28 s. That is one ingest written in batches, not "the feed re-ingests the day". `da_first_ingested_at` and `da_published_at` differ by seconds, not hours. The fields still mean what the docstring says. See the handback, "what the spec got wrong".

## The sweep's plans

These are in the handback's sweep table, `EXPLAIN` only.
