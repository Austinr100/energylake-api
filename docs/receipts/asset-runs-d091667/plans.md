# d091667 plan receipts: one plant across its held runs

Neon project `fancy-block-96153928` (Postgres 17), through the connector, read-only, 2026-10-08 20:33–21:00Z.
Every statement was a `SELECT`, or an `EXPLAIN (ANALYZE, BUFFERS)` of one, or a `PREPARE`/`EXECUTE` of one inside a transaction. The statements are in `explains.sql`.

The history held 3 runs per tech (10-08 00/06/12Z) when these were taken. Every buffer below is `shared hit` unless a `read` is shown.

## What the table offers

| relation | key |
|---|---|
| `implied_gen_site_history` | partitioned `RANGE (tech, model, init_ts)`, one partition per run; PK `(tech, plant_code, model, init_ts, target_ts)` on every partition |
| `implied_gen_site_history_runs` | PK `(tech, model, init_ts)`; never analyzed (`reltuples = -1`), so the planner sizes it at 1 row |
| a partition | solar 394,800 rows / 78.8 MB; wind 77,520 rows / 16.1 MB (`pg_total_relation_size`) |

## 1. The obvious join does not prune (rejected)

`r` = the ledger's newest 8 for `(wind, hrrr_gfs)`; `LEFT JOIN implied_gen_site_history h ON h.tech = 'wind' AND h.plant_code = 66923 AND h.model = 'hrrr_gfs' AND h.init_ts = r.init_ts`:

```
Nested Loop Left Join (actual rows=720 loops=1)
  Join Filter: (h.init_ts = implied_gen_site_history_runs.init_ts)
  Rows Removed by Join Filter: 1440
  ->  Limit (rows=3)  ->  Index Scan Backward using implied_gen_site_history_runs_pkey
  ->  Append (actual rows=720 loops=3)
        ->  Index Scan using ..._wind_hrrr_gfs_2026100800_pkey h_1 (actual rows=240 loops=3)
              Index Cond: ((tech = 'wind') AND (plant_code = 66923) AND (model = 'hrrr_gfs'))
        ->  ... _2026100806 (loops=3)  ->  ... _2026100812 (loops=3)
Buffers: shared hit=85 read=8      Execution Time: 3.955 ms
```

**Static pruning on `(tech, model)` works:** only the 3 wind partitions are in the Append.

**`init_ts` does not prune.** It stays a join filter, so every run walks every held partition: 3 × 3 scans today, 8 × 8 = 64 when the history is full.
- A plain `LEFT JOIN LATERAL` is pulled up into the same plan (93 buffers).
- `init_ts = ANY(ARRAY(SELECT init_ts FROM r))` puts `init_ts` into each index condition, but each partition is still probed: there is no pruning on a non-constant array.

## 2. The route's statement: the LATERAL fenced with `OFFSET 0`

`asset_page.runs_sql` (pinned by `test_H9_the_runs_sql_is_pinned`). Its parameters here: wind, 66923, n = 8.

```
Sort (actual rows=720 loops=1)
  CTE r
    ->  Limit  ->  WindowAgg  ->  Index Scan Backward using implied_gen_site_history_runs_pkey (rows=3)
          Index Cond: ((tech = 'wind') AND (model = 'hrrr_gfs'))              Buffers: shared hit=2 read=1
  ->  Nested Loop Left Join (actual rows=720 loops=1)
        ->  CTE Scan on r (rows=3)
        ->  Append (actual rows=240 loops=3)                                    Buffers: shared hit=30
              ->  Result (rows=240 loops=1)  One-Time Filter: (r.k <= 8)
                    ->  Index Scan using ..._wind_hrrr_gfs_2026100800_pkey x_1 (actual rows=240 loops=1)
                          Index Cond: ((tech = 'wind') AND (plant_code = 66923) AND (model = 'hrrr_gfs') AND (init_ts = r.init_ts))
                          Buffers: shared hit=10
              ->  ... _2026100806 (rows=240 loops=1, 10 buffers)
              ->  ... _2026100812 (rows=240 loops=1, 10 buffers)
Buffers: shared hit=32 read=1      Planning Time: 0.470 ms      Execution Time: 1.056 ms
```

**Each partition runs once while the Append runs three times.** That is run-time pruning: each run's loop executes its own partition only.

| read | rows | buffers | planning | execution |
|---|---:|---:|---:|---:|
| wind, SunZia Wind South 66923, n = 8 (3 held) | 720 | 33 (1 read) | 0.47 ms | **1.06 ms** |
| solar, Solar Star 1 58388, n = 8 (3 held) | 720 | 34 | 0.47 ms | **1.08 ms** |
| wind 66923, n = 1 | 242 | 13 | 0.54 ms | 0.41 ms: the two older runs' scans are `(never executed)` |
| wind 66923, n = 8, **generic plan** (`PREPARE`, `plan_cache_mode = force_generic_plan`) | 720 | 33 | 0.74 ms | **0.79 ms**: `Subplans Removed: 3` (initial pruning drops the solar partitions), then one partition per run |

**Why the generic plan matters:** psycopg binds the route's parameters server-side, so after five executions Postgres may switch to a generic plan. Pruning holds under it.

**Extrapolated to a full history** (not measured: there are not yet 8 runs): ~10 buffers per run, so ~85 buffers and ~2–3 ms warm at 8 runs. A partition read from storage costs ~1–7 blocks per run (seen above as `read=1`).

## 3. The other reads

- **The registry lookup** (`RUNS_PLANT_SQL`):
  - wind: a PK lookup on `implied_gen_wind_sites (plant_code)`, the same as the asset route's site read;
  - solar: a PK-prefix lookup on `implied_gen_sites (tech, plant_code, generator_id)`.
  - Both are d091635's measured shapes (`docs/receipts/asset-page-api-d091635/plans.md`).
- **The asset route's site read with `equipment_source`:** the same PK lookup with one more text column. Its plan is unchanged.

## 4. TTLs, against the writers' cadence

| memo | TTL | stale | key | why |
|---|---|---|---|---|
| `generation/asset/runs` (new) | 300 s | ≤ 900 s more (D-09-25-138) | `(tech, plant_code, n)` | see below |
| `generation/asset` (unchanged) | 300 s | ≤ 900 s more | `(tech, plant_code)` | the site table refreshes once per wind issuance (`refreshed_at` 14:57:55Z, alongside the 12Z run) |

**Why 300 s for the runs memo:** a run lands per tech every 6 h. The ledger's `landed_at − init_ts`: wind 2.96–3.14 h, solar 5.43–5.50 h. So 300 s puts a landed run on the page within 5 minutes. It is also the asset route's TTL, so the card's newest cycle and the panel's newest run disagree for at most one TTL.

**`max_entries` = 64:** an 8-run body is ~90–113 KB, so the memo holds at most ~7 MB.
