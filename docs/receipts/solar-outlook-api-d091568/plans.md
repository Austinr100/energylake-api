# d091568 — EXPLAIN (ANALYZE, BUFFERS), every read the two routes make

Production (Neon `fancy-block-96153928`, Postgres 17), 2026-10-03 ~17:15 UTC, read-only, through
the Neon connector. Each statement is `solar_outlook.py`'s text with the parameters written in as
literals (area `hub_sum` / `HUBSUM`, model `gfs`, method `solar_pv_v1`). First run on each, so
most are **cold** (`read=` > 0: pages fetched from Neon storage). `dirtied=` on a SELECT is hint
bits being set on freshly written rows, not a write.

What production holds today: `implied_gen_area_hourly` 61,776 rows (36 backfill issuances,
2026-08-27 12Z → 10-01 12Z, `hub` / `hub_sum` / `ba` only, leads 1–66); `implied_gen_scores` 2,084
rows; `implied_gen_calibration` 389 rows; **`implied_gen_sites` and `implied_gen_site_latest` empty**.
So the two site reads are also planned at full size on a local Postgres 16 — see the end.

| # | read | plan | rows | buffers | time |
| --- | --- | --- | --- | --- | --- |
| 1 | `ISSUANCE_NEWEST_SQL` | Index Only Scan **Backward** on `implied_gen_area_hourly_pkey`, Limit 1; SubPlan the same with `init_ts < cur.init_ts`, Limit 1 | 1 | hit 5 read 7 | 20.2 ms cold |
| 2 | `ISSUANCE_AT_SQL` (`init` = 2026-09-20 12Z) | Index Only Scan, `init_ts =` in the Index Cond; SubPlan backward, Limit 1 | 1 | hit 9 read 1 | 1.0 ms |
| 3 | `HOURS_SQL` (init 10-01 12Z, prev 09-30 06Z) | Merge Left Join of two Index Scans on the pkey, each pinned to one `init_ts` | 66 | hit 9 read 2 | 1.3 ms |
| 4 | `SCORES_SQL["hub_sum"]` | Values Scan (13) × Limit 1 ← Index Scan **Backward** on `implied_gen_scores_pkey`; Index Cond (tech, area, lead_band, who, method_version), Filter area_kind | 13 | hit 32 read 7 | 4.1 ms |
| 5 | `CALIBRATION_SQL` (`ANY('{1,2,3}')`) | Index Scan on `implied_gen_calibration_pkey` | 3 | read 2 | 1.2 ms |
| 6 | `ACTUALS_SQL[hub_sum]` (6 pairs, 66 h) | Values Scan (6) × Index Scan on `idx_tsv_series_ts`; Index Cond (dataset, series, ts range) | 315 | hit 291 read 55 | 19.6 ms cold |
| 6b | `ACTUALS_SQL[ba CISO]` (fuel mix, 59,196-row series) | Index Scan on `idx_tsv_series_ts`, ts range | 53 | hit 83 read 51 | 30.4 ms cold |
| 7 | `FLEET_SQL["hub_sum"]` | Index Scan on `implied_gen_sites_pkey` (tech), Filter, GroupAggregate | 0 | hit 2 | 0.05 ms (empty table) |
| 8 | `SITE_LATEST_SQL` (a Pacific day) | Index Scan on `implied_gen_site_latest_pkey` | 0 | hit 2 | 0.03 ms (empty table) |
| 9 | `SITE_UNITS_SQL` | Index Scan on `implied_gen_sites_pkey` | 0 | hit 2 | 0.03 ms (empty table) |

No read is a `max()` over a table, a `DISTINCT ON`, or a scan of a dataset with no series named
(CLAUDE.md's trap): every "newest" is a LIMIT 1 down a primary key, and every `timeseries_values`
read names its `(dataset, series)` and a ts range taken from the issuance's own hours.

**Growth.** `implied_gen_area_hourly` keeps every issuance (spec §2.6): at four cycles a day × 240
hours × ~35 areas it adds ~34 k rows a day. Reads 1–3 do not grow with it (PK prefix + LIMIT 1, or
PK prefix + one `init_ts`). Read 4 walks back from the newest `window_end` and stops at the first
row. Read 6 is bounded by the issuance's ≤ 240 hours.

## Plans, verbatim

### 1. ISSUANCE_NEWEST_SQL
```
Subquery Scan on cur  (cost=0.41..1.65 rows=1 width=16) (actual time=20.195..20.197 rows=1 loops=1)
  Buffers: shared hit=5 read=7 dirtied=2
  ->  Limit  (cost=0.41..0.76 rows=1 width=8) (actual time=18.830..18.831 rows=1 loops=1)
        Buffers: shared hit=1 read=5 dirtied=1
        ->  Index Only Scan Backward using implied_gen_area_hourly_pkey on implied_gen_area_hourly i  (cost=0.41..30.36 rows=88 width=8) (actual time=18.828..18.829 rows=1 loops=1)
              Index Cond: ((tech = 'solar_pv'::text) AND (area_kind = 'hub_sum'::text) AND (area = 'HUBSUM'::text) AND (model = 'gfs'::text))
              Heap Fetches: 1
              Buffers: shared hit=1 read=5 dirtied=1
  SubPlan 1
    ->  Limit  (cost=0.41..0.90 rows=1 width=8) (actual time=1.354..1.355 rows=1 loops=1)
          Buffers: shared hit=4 read=2 dirtied=1
          ->  Index Only Scan Backward using implied_gen_area_hourly_pkey on implied_gen_area_hourly p  (cost=0.41..14.46 rows=29 width=8) (actual time=1.352..1.353 rows=1 loops=1)
                Index Cond: ((tech = 'solar_pv'::text) AND (area_kind = 'hub_sum'::text) AND (area = 'HUBSUM'::text) AND (model = 'gfs'::text) AND (init_ts < cur.init_ts))
                Heap Fetches: 1
                Buffers: shared hit=4 read=2 dirtied=1
Planning Time: 2.184 ms
Execution Time: 20.235 ms
```

### 2. ISSUANCE_AT_SQL
```
Subquery Scan on cur  (cost=0.41..5.34 rows=1 width=16) (actual time=0.974..0.975 rows=1 loops=1)
  Buffers: shared hit=9 read=1
  ->  Limit  (cost=0.41..4.44 rows=1 width=8) (actual time=0.949..0.949 rows=1 loops=1)
        ->  Index Only Scan using implied_gen_area_hourly_pkey on implied_gen_area_hourly i  (cost=0.41..8.47 rows=2 width=8) (actual time=0.948..0.948 rows=1 loops=1)
              Index Cond: ((tech = 'solar_pv'::text) AND (area_kind = 'hub_sum'::text) AND (area = 'HUBSUM'::text) AND (model = 'gfs'::text) AND (init_ts = '2026-09-20 12:00:00+00'::timestamp with time zone))
              Heap Fetches: 0
  SubPlan 1
    ->  Limit  (cost=0.41..0.90 rows=1 width=8) (actual time=0.020..0.020 rows=1 loops=1)
          Buffers: shared hit=5
          ->  Index Only Scan Backward using implied_gen_area_hourly_pkey on implied_gen_area_hourly p  (cost=0.41..14.46 rows=29 width=8) (actual time=0.019..0.019 rows=1 loops=1)
                Index Cond: ((tech = 'solar_pv'::text) AND (area_kind = 'hub_sum'::text) AND (area = 'HUBSUM'::text) AND (model = 'gfs'::text) AND (init_ts < cur.init_ts))
Execution Time: 1.000 ms
```

### 3. HOURS_SQL
```
Merge Left Join  (cost=0.83..20.46 rows=2 width=111) (actual time=1.171..1.224 rows=66 loops=1)
  Merge Cond: (c.target_ts = p.target_ts)
  Buffers: shared hit=9 read=2 dirtied=2
  ->  Index Scan using implied_gen_area_hourly_pkey on implied_gen_area_hourly c  (cost=0.41..10.22 rows=2 width=103) (actual time=0.551..0.570 rows=66 loops=1)
        Index Cond: ((tech = 'solar_pv'::text) AND (area_kind = 'hub_sum'::text) AND (area = 'HUBSUM'::text) AND (model = 'gfs'::text) AND (init_ts = '2026-10-01 12:00:00+00'::timestamp with time zone))
  ->  Index Scan using implied_gen_area_hourly_pkey on implied_gen_area_hourly p  (cost=0.41..10.22 rows=2 width=56) (actual time=0.601..0.620 rows=66 loops=1)
        Index Cond: ((tech = 'solar_pv'::text) AND (area_kind = 'hub_sum'::text) AND (area = 'HUBSUM'::text) AND (model = 'gfs'::text) AND (init_ts = '2026-09-30 06:00:00+00'::timestamp with time zone))
Planning Time: 2.714 ms
Execution Time: 1.263 ms
```

### 4. SCORES_SQL["hub_sum"]
```
Nested Loop  (cost=0.28..103.88 rows=13 width=133) (actual time=1.855..4.076 rows=13 loops=1)
  Buffers: shared hit=32 read=7 dirtied=1
  ->  Values Scan on "*VALUES*"  (cost=0.00..0.16 rows=13 width=64) (actual time=0.002..0.015 rows=13 loops=1)
  ->  Limit  (cost=0.28..7.97 rows=1 width=69) (actual time=0.311..0.311 rows=1 loops=13)
        ->  Index Scan Backward using implied_gen_scores_pkey on implied_gen_scores s  (cost=0.28..38.72 rows=5 width=69) (actual time=0.310..0.310 rows=1 loops=13)
              Index Cond: ((tech = 'solar_pv'::text) AND (area = 'HUBSUM'::text) AND (lead_band = "*VALUES*".column1) AND (who = "*VALUES*".column2) AND (method_version = 'solar_pv_v1'::text))
              Filter: (area_kind = 'hub_sum'::text)
Planning Time: 22.249 ms
Execution Time: 4.126 ms
```

### 5. CALIBRATION_SQL
```
Index Scan using implied_gen_calibration_pkey on implied_gen_calibration  (cost=0.15..8.20 rows=3 width=72) (actual time=1.123..1.127 rows=3 loops=1)
  Index Cond: (calibration_id = ANY ('{1,2,3}'::bigint[]))
  Buffers: shared read=2 dirtied=1
Execution Time: 1.162 ms
```
(Not run by the route today: no production row carries a `calibration_id`.)

### 6. ACTUALS_SQL[("hub_sum", "HUBSUM")]
```
Nested Loop  (cost=0.69..52.46 rows=6 width=80) (actual time=4.328..19.578 rows=315 loops=1)
  Buffers: shared hit=291 read=55 dirtied=33
  ->  Values Scan on "*VALUES*"  (cost=0.00..0.08 rows=6 width=64) (actual time=0.001..0.014 rows=6 loops=1)
  ->  Index Scan Backward using idx_tsv_series_ts on timeseries_values t  (cost=0.69..8.72 rows=1 width=16) (actual time=1.504..3.247 rows=52 loops=6)
        Index Cond: ((dataset = "*VALUES*".column1) AND (series = "*VALUES*".column2) AND (ts >= '2026-10-01 12:00:00+00'::timestamp with time zone) AND (ts <= '2026-10-04 05:00:00+00'::timestamp with time zone))
Execution Time: 19.625 ms
```

### 6b. ACTUALS_SQL[("ba", "CISO")]
```
Subquery Scan on l  (cost=0.69..32.33 rows=7 width=80) (actual time=5.342..30.394 rows=53 loops=1)
  Buffers: shared hit=83 read=51 dirtied=29
  ->  Index Scan Backward using idx_tsv_series_ts on timeseries_values t  (cost=0.69..32.33 rows=7 width=16) (actual time=5.340..30.372 rows=53 loops=1)
        Index Cond: ((dataset = 'caiso_fuel_mix_hourly'::text) AND (series = 'solar'::text) AND (ts >= '2026-10-01 12:00:00+00'::timestamp with time zone) AND (ts <= '2026-10-04 05:00:00+00'::timestamp with time zone))
Execution Time: 30.431 ms
```
(Cold: 51 of 134 pages read from storage. The fuel-mix series runs to 2020; the range keeps it to
the issuance's hours.)

### 7–9. FLEET_SQL, SITE_LATEST_SQL, SITE_UNITS_SQL — production (empty tables)
```
GroupAggregate ... (actual rows=0)  Buffers: shared hit=2
  ->  Index Scan using implied_gen_sites_pkey on implied_gen_sites  Index Cond: (tech = 'solar_pv')  Filter: (hub IS NOT NULL)
Execution Time: 0.050 ms

Index Scan using implied_gen_site_latest_pkey on implied_gen_site_latest l (actual rows=0)
  Index Cond: ((tech = 'solar_pv') AND (model = 'gfs') AND (target_ts >= '2026-10-02 07:00+00') AND (target_ts < '2026-10-03 07:00+00'))
  Buffers: shared hit=2
Execution Time: 0.031 ms

Index Scan using implied_gen_sites_pkey on implied_gen_sites (actual rows=0)  Buffers: shared hit=2
Execution Time: 0.029 ms
```

## The site reads at full size (local Postgres 16)

`plans_local_scale.py` → `plans_local_scale.txt`: migration 264's DDL, 1,914 units at 1,645 plants,
394,800 `site_latest` rows (one cycle, f001–f240, 67 MB).

| read | plan | buffers | time (warm) |
| --- | --- | --- | --- |
| `SITE_LATEST_SQL`, a Pacific day | **Seq Scan**, 39,480 kept, 355,320 removed | 5,264 | 26.8 ms |
| `SITE_LATEST_SQL`, one hour | Parallel Seq Scan, 1,645 kept | 5,264 | 14.9 ms |
| `SITE_UNITS_SQL` | Seq Scan + Sort | 37 | 1.4 ms |
| `FLEET_SQL["hub_sum"]` | Seq Scan + HashAggregate | 37 | 0.5 ms |

**`SITE_LATEST_SQL` reads the whole table.** Its key is `(tech, plant_code, model, target_ts)`, so a
`target_ts` window cannot use it, and the planner picks a seq scan. It is bounded — the writer
replaces the table in full each cycle, so it never holds more than one cycle (≈ 395 k rows) — and it
is behind the 300 s memo and the 5 s timeout. A plant-driven lateral (one PK range per plant) would
touch about as many pages (1,645 × ~3). The real fix is the writer's: an index on
`implied_gen_site_latest (tech, model, target_ts)` would make the one-hour read ~1,645 index
entries. This lane is read-only and does not add it (handback §6).
