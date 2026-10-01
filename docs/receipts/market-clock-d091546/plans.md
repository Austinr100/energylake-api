# d091546: market-clock plans, before and after

This box can't reach Neon. The egress proxy refuses 5432 and Neon's HTTPS SQL endpoint (d091542 Gate 0). So the **before**-plan below is production's own reading, quoted from Gate 0. The **after**-plans are for the architect to run (§3.1). The statements are in `after_plans.sql`, each in a `BEGIN … ROLLBACK` block with a statement timeout.

## Before (as shipped, main a7b85d2): from d091542 Gate 0, Neon, 2026-10-01

Target trade date 2026-10-02 PT, window empty:

```
(SELECT max(ingested_ts) FROM timeseries_values
   WHERE dataset = 'caiso_lmp_da_hourly'
     AND ts >= '2026-10-02 07:00+00' AND ts < '2026-10-03 07:00+00')
→ Limit
    → Index Scan Backward using idx_tsv_dataset_ingested_ts on timeseries_values
        Index Cond: (dataset = 'caiso_lmp_da_hourly')
        Filter: ts >= … AND ts < …            (estimated total cost 21,873,243)
```

- **Whole statement, run by hand at 12:30Z:** did not finish in 60 s.
- **The other four subqueries:** single-series index lookups (estimated cost 4.7 and 8.7).
- **Spec §0, 13:15Z:** the same subquery with `AND series = 'SP15'` was an index scan on `idx_tsv_series_ts`, 0.078 ms, 5 buffers.

## After (this branch): what to run and what to look for

The statement is `main._MARKET_CLOCK_SQL`. It has three reads of `timeseries_values`, and each names `(dataset, series)`:

| read | shape | expected plan |
| --- | --- | --- |
| `da` | `count(value), max(ingested_ts), min(ingested_ts)` over SP15's rows in the target PT day | Aggregate over an Index Scan on `idx_tsv_series_ts`, with Index Cond `series = 'SP15' AND ts >= … AND ts < …` and Filter `dataset = …`. That is about 400 SP15 rows a day across datasets. |
| `sp15_da_val` | SP15 at HE17 | Index Scan on `idx_tsv_series_ts`, with Index Cond `series = 'SP15' AND ts = …` |
| `fmm` (LATERAL) | newest SP15 RTPD value | Index Scan Backward on `idx_tsv_series_ts`, with Index Cond `series = 'SP15'`, Limit 1 |

**Why one aggregate.** This is hardening, not a fix for a failure I reproduced. With `count()` in the same `SELECT`, Postgres can't use its min/max shortcut. That shortcut rewrites a lone `max()` or `min()` into an ordered walk of an index on the aggregated column, and here that index is `idx_tsv_dataset_ingested_ts`, the trap. With `series` named, the planner chose the series index both on Neon for `max` (spec §0) and locally for `min` (below). But that was the planner's choice by cost, and one aggregate takes the choice away.

**STOP-Q.** Any read in plans 3 or 4 that is a Seq Scan, or a scan of `idx_tsv_dataset_ingested_ts`, means: report the read, do not ship.

**Expectation for the architect's sign-off.** Plans 3 and 4 both finish in well under 3 s. Plan 3 (empty window) should cost the same as plan 4 apart from the aggregate's row count.

## Local synthetic run: what it shows and what it doesn't

Postgres 16 is installed on this box. So I ran `after_plans.sql` against a throwaway local instance holding a synthetic `timeseries_values` (2.18 M rows, the two named indexes, `VACUUM ANALYZE`). The output is in `local_synthetic_plans.txt`. It is **not** Neon: the plans depend on statistics. What it does show:

- **The statement runs, and it returns one row when every read is empty.** The `LEFT JOIN LATERAL` keeps the row when there is no FMM print. M5 holds on real rows: `da_hours` 24, first ingest earlier than the newest.
- **The `da` aggregate is an `idx_tsv_series_ts` lookup in both windows:**
  - empty window: Index Scan, 0.010 ms;
  - full window: Bitmap Index Scan on `idx_tsv_series_ts`, 0.048 ms.
- **Before, empty window:** the old `da_published_at` read planned as a Parallel Seq Scan over the whole table locally (48 ms on 2 M rows). On Neon it was the backward `idx_tsv_dataset_ingested_ts` walk. Either way it is the whole dataset.
- **A lone `min(ingested_ts)` with `series` named** also chose `idx_tsv_series_ts` locally (0.06 ms).
- **Watch this in STOP-Q:** locally the `fmm` read (newest SP15 RTPD) planned as a BitmapAnd of `idx_tsv_dataset_ingested_ts` (Index Cond `dataset` only) and `idx_tsv_series_ts`, then a top-N sort, taking about 2 ms. That shape is identical before and after: the old `fmm_ts` and `fmm_val` subqueries did the same thing twice. Gate 0 read these reads on Neon as single-series index lookups (estimated cost 4.7 and 8.7). If Neon's after-plan shows the BitmapAnd, the read is bounded by the size of `caiso_lmp_rt_15min`, not by the window. The robust form is a `ts` lower bound (for example `ts >= now() − 1 day`). That changes the answer when the feed is more than a day stale, so it is the architect's call and I didn't make it.
