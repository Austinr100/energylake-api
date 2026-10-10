# d091673 plan receipts: the Tropics routes' reads on Neon

**How:** `EXPLAIN (ANALYZE, BUFFERS)` of each statement in `explains.sql` (tropics.py's own text, bound by psycopg's `ClientCursor.mogrify`), run through the Neon connector on the production database (project Energylake, Postgres 17.11), 2026-10-09 between 22:12Z and 22:30Z. Every statement is a `SELECT`; nothing was written.

**Data size at the time:** `tropical_track_points` 34,518+ rows (861 pages, 11 MB; Rachel `ep182026` 22,125 of them), `tropical_place_odds` 2,674 (55 pages), `tropical_file_vintage` 2,750 (137 pages), `tropical_storms` 3 (1 page). The live bank kept growing during the session; the plans read the live tables, the bank fixture is cut at 21:54:47Z.

**Locally, at 31× (1,070,058 points):** `test_T10_every_read_uses_an_index_at_31x` loads the bank and 30 copies under other seasons' ids, and asserts no statement seq-scans the points, odds or heartbeat table.

## Summary

| statement | route | plan | buffers | execution |
|---|---|---|---:|---:|
| `POLL_SQL` | /storms | Index Only Scan `tfv_heartbeat` (LIMIT 1, 0 heap fetches) + `datasets_pkey` | 5 | 0.056 ms |
| `STORMS_SQL` (active) | /storms | 1-page seq scan of `tropical_storms`; per storm, Index Scan Backward `uniq_tropical_track_points` LIMIT 1 for best_track and for tcvitals | 25 | 0.135 ms warm (4.66 ms cold: 7 reads) |
| `OFFICIAL_NEWEST_SQL` (3 storms) | /storms, /storm | per storm, Index Only Scan Backward `uniq_tropical_track_points` LIMIT 1, then an index range on it | 28 | 0.150 ms |
| `STORM_SQL` | /storm, /tracks, /odds | 1-page seq scan of `tropical_storms` | 1 | 0.030 ms |
| `OBSERVED_SQL` (Rachel, 69 rows) | /storm | Index Scan `uniq_tropical_track_points`, `source = ANY ('{best_track,tcvitals}')`, incremental sort | 21 | 1.02 ms (1 read) |
| `CYCLES_SQL` (Rachel, 56 cycles, 1,062 pairs) | /storm | loose index scan down `ttp_storm_init` (57 probes), then per cycle a bitmap range on `ttp_storm_init` | 1,053 | 10.3 ms |
| `TRACKS_SQL` n = 8 (Rachel, 2,493 rows) | /tracks | loose index scan (8 probes, loops = 7), then one bitmap range `init_ts = ANY(…)` on `ttp_storm_init` | 115 | 3.26 ms |
| `TRACKS_SQL` n = 4 (Rachel, 995 rows) | /tracks | as above, 4 probes | 49 | 1.26 ms |
| `ODDS_SQL` (Isaias, 378 rows) | /odds | Index Only Scan Backward `uniq_tropical_place_odds` LIMIT 1, then a bitmap range on it | 22 | 0.357 ms warm (17.5 ms cold: 8 reads) |
| *`ingestion_freshness` (not used)* | — | builds every dataset's arm, incl. `timeseries_values` | — | 280 ms |

`tropical_storms` is one 8 KB page; Postgres reads the page rather than the PK, and it stays a few pages at 31× (93 storms). Every read of the three big tables is indexed.

## Two findings that changed the SQL

**1. The census.** The obvious `SELECT init_ts, source, count(*), max(tau) … WHERE storm_id = 'ep182026' GROUP BY …` is a **seq scan of the whole table** on Neon (Rachel is 64 % of it), 10.8 ms, 868 buffers, with either GROUP BY order:

```
Sort  (actual time=10.706..10.752 rows=1062 loops=1)
  ->  HashAggregate  (actual time=10.222..10.402 rows=1062 loops=1)
        ->  Seq Scan on tropical_track_points  (actual time=0.351..5.105 rows=22065 loops=1)
              Filter: ((storm_id = 'ep182026'::text) AND (source <> ALL ('{best_track,tcvitals,nhc_official}'::text[])))
              Rows Removed by Filter: 12453
              Buffers: shared hit=868
Execution Time: 10.839 ms
```

It costs the same today, but it grows with every storm the d091671 backfill adds. The shipped statement drives the census from the same loose index scan as `/tracks`, so it grows with the storm alone:

```
Sort  (actual time=10.155..10.204 rows=1062 loops=1)
  CTE cyc
    ->  Recursive Union  (actual time=0.024..0.364 rows=57 loops=1)
          ->  Limit -> Index Scan using ttp_storm_init on tropical_track_points (rows=1 loops=1)
          ->  WorkTable Scan on cyc (loops=57)
                SubPlan 1 -> Limit -> Index Scan using ttp_storm_init on tropical_track_points t (rows=1 loops=56)
                      Index Cond: ((storm_id = 'ep182026'::text) AND (init_ts < cyc.init_ts))
  ->  Nested Loop  (actual time=0.082..9.882 rows=1062 loops=1)
        ->  CTE Scan on cyc c (rows=56)
        ->  HashAggregate (rows=19 loops=56)
              ->  Bitmap Heap Scan on tropical_track_points (rows=394 loops=56)
                    ->  Bitmap Index Scan on ttp_storm_init (rows=396 loops=56)
                          Index Cond: ((storm_id = 'ep182026'::text) AND (init_ts = c.init_ts))
Buffers: shared hit=1053
Execution Time: 10.332 ms
```

Rehearsal break 16 removes the inner index condition and `test_T10_every_read_uses_an_index_at_the_banks_size` goes red.

**2. The freshness view.** `ingestion_freshness` filtered to the two tropical arms takes **280 ms**: it builds every dataset's arm (7 `model_runs` scans, `timeseries_values` among them) before the filter. A polled route must not read it. `POLL_SQL` reads the same frontier off `tfv_heartbeat` (0.056 ms) and grades it with the view's own `CASE` in `tropics.grade_poll`. The witness (`tests/fixtures/tropics_d091673/freshness_witness.json`, view md5 `d3af42e8…`) shows the index's newest heartbeat equal to the view's frontier, and the two grades equal (T3).

## TRACKS_SQL, n = 8, as Neon printed it

```
Sort  (cost=1307.14..1315.72 rows=3429 width=55) (actual time=2.967..3.085 rows=2493 loops=1)
  Sort Key: p.init_ts, p.source, p.advisory, p.tau
  Buffers: shared hit=115
  CTE cyc
    ->  Recursive Union  (actual time=0.018..0.080 rows=8 loops=1)
          ->  Limit  (actual time=0.017..0.018 rows=1 loops=1)
                ->  Index Scan using ttp_storm_init on tropical_track_points (rows=1 loops=1)
                      Index Cond: (storm_id = 'ep182026'::text)
                      Filter: (source <> ALL ('{best_track,tcvitals,nhc_official}'::text[]))
          ->  WorkTable Scan on cyc  (actual time=0.008..0.008 rows=1 loops=7)
                SubPlan 1
                  ->  Limit  (rows=1 loops=7)
                        ->  Index Scan using ttp_storm_init on tropical_track_points t (rows=1 loops=7)
                              Index Cond: ((storm_id = 'ep182026'::text) AND (init_ts < cyc.init_ts))
  InitPlan 3
    ->  Limit  (rows=8 loops=1)
          ->  CTE Scan on cyc cyc_1  (rows=8 loops=1)
  ->  Bitmap Heap Scan on tropical_track_points p  (actual time=0.144..0.833 rows=2493 loops=1)
        Recheck Cond: ((storm_id = 'ep182026'::text) AND (init_ts = ANY ((InitPlan 3).col1)))
        Filter: (source <> ALL ('{best_track,tcvitals}'::text[]))
        Heap Blocks: exact=79
        ->  Bitmap Index Scan on ttp_storm_init  (rows=2509 loops=1)
              Index Cond: ((storm_id = 'ep182026'::text) AND (init_ts = ANY ((InitPlan 3).col1)))
Planning Time: 0.239 ms
Execution Time: 3.259 ms
```

The recursion stops at n: `LIMIT 8` pulls 8 rows from the CTE, and the WorkTable scan runs 7 times. Rachel has 56 cycles, and none of the older 48 is probed.

## The others, as Neon printed them (abridged to the plan nodes)

```
-- POLL_SQL                                   Execution Time: 0.056 ms, Buffers: shared hit=5
Nested Loop Left Join
  ->  Nested Loop Left Join
        ->  Result
        ->  Limit -> Index Only Scan using tfv_heartbeat on tropical_file_vintage (rows=1) Heap Fetches: 0
  ->  Index Scan using datasets_pkey on datasets d   Index Cond: (dataset_code = 'nhc_storms_current'::text)

-- STORMS_SQL scope=active (warm)             Execution Time: 0.135 ms, Buffers: shared hit=25
Sort
  ->  Nested Loop Left Join
        ->  Nested Loop Left Join
              ->  Seq Scan on tropical_storms s   Filter: (status <> 'inactive'::text)  Buffers: shared hit=1
              ->  Limit -> Index Scan Backward using uniq_tropical_track_points  (loops=3)
                    Index Cond: ((storm_id = s.storm_id) AND (source = 'best_track'::text))
        ->  Limit -> Index Scan Backward using uniq_tropical_track_points  (loops=3)
              Index Cond: ((storm_id = s.storm_id) AND (source = 'tcvitals'::text))

-- OFFICIAL_NEWEST_SQL (3 storms)             Execution Time: 0.150 ms, Buffers: shared hit=28
Sort
  ->  Nested Loop (rows=18)
        ->  Nested Loop
              ->  Function Scan on unnest u (rows=3)
              ->  Limit -> Index Only Scan Backward using uniq_tropical_track_points (loops=3)
                    Index Cond: ((storm_id = u.storm_id) AND (source = 'nhc_official'::text))
        ->  Index Scan using uniq_tropical_track_points on tropical_track_points p (rows=6 loops=3)
              Index Cond: ((storm_id = u.storm_id) AND (source = 'nhc_official'::text) AND (init_ts = tropical_track_points.init_ts))

-- STORM_SQL                                  Execution Time: 0.030 ms, Buffers: shared hit=1
Seq Scan on tropical_storms   Filter: (storm_id = 'ep182026'::text)   Rows Removed by Filter: 2

-- OBSERVED_SQL (Rachel)                      Execution Time: 1.017 ms, Buffers: shared hit=20 read=1
Incremental Sort   Presorted Key: source
  ->  Index Scan using uniq_tropical_track_points on tropical_track_points (rows=69)
        Index Cond: ((storm_id = 'ep182026'::text) AND (source = ANY ('{best_track,tcvitals}'::text[])))

-- ODDS_SQL (Isaias, warm)                    Execution Time: 0.357 ms, Buffers: shared hit=22
Sort
  ->  Nested Loop (rows=378)
        ->  Limit -> Index Only Scan Backward using uniq_tropical_place_odds (rows=1)
              Index Cond: ((storm_id = 'al092026'::text) AND (source = 'nhc_pws'::text))
        ->  Bitmap Heap Scan on tropical_place_odds o (rows=378)
              ->  Bitmap Index Scan on uniq_tropical_place_odds
                    Index Cond: ((storm_id = 'al092026'::text) AND (source = 'nhc_pws'::text) AND (issued_ts = tropical_place_odds.issued_ts))
```

## A whole route's reads

| route | statements | sum of Neon execution (warm) |
|---|---|---:|
| /storms | POLL + STORMS + OFFICIAL_NEWEST | ≈ 0.34 ms |
| /storm (Rachel) | STORM + OBSERVED + OFFICIAL_NEWEST + CYCLES | ≈ 11.5 ms |
| /tracks n = 8 (Rachel) | STORM + TRACKS | ≈ 3.3 ms |
| /odds (Isaias) | STORM + ODDS | ≈ 0.4 ms |

The 5 s statement timeout is about 400× the slowest read.
