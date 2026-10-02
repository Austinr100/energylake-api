# d091551 — plans, before and after (Neon, production, read-only)

**Read:** 2026-10-01 ~20:15Z, project `fancy-block-96153928` (Energylake), default branch, through the Neon MCP connector. Every statement is a SELECT or an `EXPLAIN (ANALYZE, BUFFERS)` of one. Nothing was written.

## `/api/weather/regime` drivers

### What the five datasets hold

| dataset | series | rows | newest `ts` |
| --- | --- | ---: | --- |
| climate_iod_dmi_monthly | iod_dmi | 1,877 | 2026-05-01 |
| climate_pdo_monthly | pdo | 2,071 | 2026-07-01 |
| climate_qbo_monthly | qbo | 938 | 2026-02-01 |
| cpc_oni_monthly | oni | 919 | 2026-07-01 |
| cpc_roni_monthly | roni | 919 | 2026-07-01 |

One series per dataset, 6,724 rows in all. The spec's 78,935 is the planner's estimate from an `EXPLAIN`; the executed plan below reads 6,724.

### Before — as shipped

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT DISTINCT ON (dataset) dataset, series, ts, value
FROM timeseries_values
WHERE dataset = ANY(ARRAY['cpc_oni_monthly','cpc_roni_monthly','climate_pdo_monthly',
                          'climate_qbo_monthly','climate_iod_dmi_monthly'])
ORDER BY dataset, ts DESC;
```

```
Unique  (cost=237526.04..237846.13 rows=51) (actual time=90.906..91.760 rows=5 loops=1)
  Buffers: shared hit=13 read=520 dirtied=497
  ->  Sort  (actual rows=6724)  Sort Key: dataset, ts DESC  Sort Method: quicksort  Memory: 575kB
        ->  Bitmap Heap Scan on timeseries_values  (actual time=16.076..84.664 rows=6724 loops=1)
              Recheck Cond: (dataset = ANY (...))
              Heap Blocks: exact=514
              ->  Bitmap Index Scan on idx_tsv_dataset_ingested_ts  (actual rows=6724)
Planning Time: 0.113 ms
Execution Time: 91.814 ms
```

533 buffers, 92 ms, every row of five datasets read and sorted to keep five.

### After — this branch (`main._REGIME_DRIVERS_SQL`)

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT d.dataset, l.series, l.ts, l.value
FROM (VALUES ('cpc_oni_monthly', 'oni'), ('cpc_roni_monthly', 'roni'),
             ('climate_pdo_monthly', 'pdo'), ('climate_qbo_monthly', 'qbo'),
             ('climate_iod_dmi_monthly', 'iod_dmi')) AS d(dataset, series)
CROSS JOIN LATERAL (
    SELECT t.series, t.ts, t.value FROM timeseries_values t
    WHERE t.dataset = d.dataset AND t.series = d.series
    ORDER BY t.ts DESC LIMIT 1) AS l;
```

```
Nested Loop  (cost=0.69..23.87 rows=5) (actual time=0.045..0.091 rows=5 loops=1)
  Buffers: shared hit=30
  ->  Values Scan on "*VALUES*"  (actual rows=5 loops=1)
  ->  Limit  (actual time=0.017..0.017 rows=1 loops=5)
        Buffers: shared hit=30
        ->  Index Scan using idx_tsv_series_ts on timeseries_values t  (actual rows=1 loops=5)
              Index Cond: ((dataset = "*VALUES*".column1) AND (series = "*VALUES*".column2))
Planning Time: 0.148 ms
Execution Time: 0.116 ms
```

**30 buffers, 0.116 ms: five index lookups. STOP-Q (> 100 buffers) does not fire.**

### Same rows

Both statements, `UNION ALL`ed and sorted, return the same five rows:

| dataset | series | ts | value |
| --- | --- | --- | --- |
| climate_iod_dmi_monthly | iod_dmi | 2026-05-01 | 0.146000 |
| climate_pdo_monthly | pdo | 2026-07-01 | -2.030000 |
| climate_qbo_monthly | qbo | 2026-02-01 | -23.130000 |
| cpc_oni_monthly | oni | 2026-07-01 | 1.800000 |
| cpc_roni_monthly | roni | 2026-07-01 | 1.360000 |

These are the driver rows banked in `tests/fixtures/polled_routes_d091551/regime_rows.json`.

## `/api/timeseries/caiso-peak-demand`

### What the route returns from the dataset

| | value |
| --- | --- |
| rows (`value IS NOT NULL`) | 65,664 (no nulls) |
| series | 36 |
| `ts` | 2026-07-23 07:00Z … 2026-10-07 06:00Z |
| PT operating dates | 76 |
| distinct `meta.publish_time` | 76 (2026-07-16 … 2026-09-29, 16:10Z): one vintage per date, the diagonal |

The body's `operating_dates` is every one of those 76 dates, and each area carries a day for each. A `ts` floor or a newest-issuance key would drop dates from the body, which STOP-B forbids. So the read stays the same and is memoised (300 s, single-flight, stale behind one refresh), under `SET LOCAL statement_timeout = '5s'` in an explicit transaction.

### The statement (unchanged)

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT ts, series, value, meta FROM timeseries_values
WHERE dataset = 'caiso_load_fcst_7day' AND value IS NOT NULL
ORDER BY series ASC, ts ASC;
```

```
Incremental Sort  (cost=78.49..278913.57 rows=68942) (actual time=3.019..70.747 rows=65664 loops=1)
  Sort Key: series, ts   Presorted Key: series
  Buffers: shared hit=57338
  ->  Index Scan using idx_tsv_series_ts on timeseries_values  (actual time=0.030..53.217 rows=65664 loops=1)
        Index Cond: (dataset = 'caiso_load_fcst_7day'::text)
        Filter: (value IS NOT NULL)
        Buffers: shared hit=57338
Planning Time: 3.023 ms
Execution Time: 75.875 ms
```

76 ms and 57,338 buffers (all cache hits) on the server, plus 65,664 rows with their `meta` over the wire. Before: on every request. After: once per 300 s per process, however many tabs, and never more than one at a time.

## `/api/timeseries/caiso-fuel-mix` (default mode)

Not changed and not re-run: it is bounded by `LIMIT`, and it has no empty-window case because the newest rows always exist (d091546 §6, cost 3,621).
