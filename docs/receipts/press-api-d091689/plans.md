# Plans — d091689, the press API

**Where:** a throwaway local Postgres (`tests/load_press_d091689.cluster`), `PostgreSQL 16.15 (Ubuntu 16.15-0ubuntu0.24.04.1) on x86_64-pc-linux-gnu`, holding `press_editions` exactly as spec d091689 §7 states it (`tests/fixtures/press_d091689/ddl_press_editions.sql`: the primary key and `press_editions_identity` are its only indexes) and **2,000 constructed rows** (`load_press_d091689.constructed`), every body a full el.edition.v1 document (the pantry vectors, re-dated), so the heap and TOAST are the real size.

**The Neon plans are still owed.** `press_editions` does not exist on Neon yet (pantry lane d091688 is building it); these are not Neon's plans. When the migration lands, the same statements (`pinned_sql.json`) are to be planned there with `EXPLAIN (ANALYZE, BUFFERS)` and this file's table filled for Neon.

Table: heap 432 kB, total with TOAST and indexes 42 MB, `press_editions_identity` 104 kB.

| kind | rows | identities | withdrawn | first | last |
|---|---:|---:|---:|---|---|
| article | 233 | 233 | 0 | 2022-10-14 | 2026-10-10 |
| daily | 1510 | 1461 | 17 | 2022-10-11 | 2026-10-10 |
| monthly | 48 | 48 | 0 | 2022-11-01 | 2026-10-01 |
| weekly | 209 | 209 | 0 | 2022-10-10 | 2026-10-05 |

## Summary

Every read of `press_editions` is an index scan on `press_editions_identity`; no plan has a sequential scan or a sort. `rows read` counts rows the scans returned plus rows their filter removed: the newest walks stop at their LIMIT. Times are medians of 25 warm runs: `exec` is EXPLAIN's execution time (the body is not detoasted), `fetch` the client's execute + fetch of the real statement (it is).

| statement | parameters | route | scans | rows read | rows out | buffers | exec ms | fetch ms |
|---|---|---|---|---:|---:|---:|---:|---:|
| `FRONT_DAILY_SQL` | - | /front: the newest daily in force, its first revision, the newest daily held | Index Scan on press_editions_identity | 3 | 1 | 9 | 0.066 | 0.69 |
| `FRONT_HEADERS_SQL` | - | /front: newest weekly, newest monthly, five newest articles | Index Scan on press_editions_identity | 7 | 7 | 9 | 0.055 | 0.27 |
| `HEADERS_SQL` | kind=daily, n=14 | /editions?kind=daily (default n) | Index Scan on press_editions_identity | 14 | 14 | 3 | 0.043 | 0.31 |
| `HEADERS_SQL` | kind=daily, n=60 | /editions?kind=daily&n=60 (the cap) | Index Scan on press_editions_identity | 62 | 60 | 4 | 0.093 | 0.52 |
| `HEADERS_SQL` | kind=article, n=60 | /editions?kind=article&n=60 | Index Scan on press_editions_identity | 60 | 60 | 7 | 0.092 | 0.55 |
| `EDITION_SQL` | kind=daily, date=2026-10-10, slug= | /edition?kind=daily&date=2026-10-10 | Index Scan on press_editions_identity | 3 | 1 | 9 | 0.058 | 0.79 |
| `EDITION_SQL` | kind=article, date=2026-10-10, slug=gas-carried-california-october-2026 | /edition?kind=article&...&slug= | Index Scan on press_editions_identity | 3 | 1 | 9 | 0.035 | 0.54 |
| `EDITION_SQL` | kind=daily, date=2022-10-11, slug= | /edition, an identity with a withdrawn revision 1 | Index Scan on press_editions_identity | 5 | 1 | 9 | 0.039 | 0.71 |
| `EDITION_AT_SQL` | kind=daily, date=2026-10-10, slug=, revision=0 | /edition?...&revision=0 | Index Scan on press_editions_identity | 2 | 1 | 6 | 0.026 | 0.77 |

## Raw plans

### `FRONT_DAILY_SQL` (-): /front: the newest daily in force, its first revision, the newest daily held

```
Nested Loop Left Join  (cost=0.83..9.26 rows=1 width=226) (actual time=0.050..0.052 rows=1 loops=1)
  Buffers: shared hit=9
  ->  Nested Loop Left Join  (cost=0.56..8.79 rows=1 width=189) (actual time=0.039..0.040 rows=1 loops=1)
        Buffers: shared hit=6
        ->  Nested Loop Left Join  (cost=0.28..0.48 rows=1 width=179) (actual time=0.034..0.034 rows=1 loops=1)
              Buffers: shared hit=3
              ->  Result  (cost=0.00..0.01 rows=1 width=0) (actual time=0.000..0.000 rows=1 loops=1)
              ->  Limit  (cost=0.28..0.46 rows=1 width=179) (actual time=0.032..0.032 rows=1 loops=1)
                    Buffers: shared hit=3
                    ->  Index Scan Backward using press_editions_identity on press_editions  (cost=0.28..268.70 rows=1497 width=179) (actual time=0.032..0.032 rows=1 loops=1)
                          Index Cond: (kind = 'daily'::text)
                          Filter: (withdrawn_at IS NULL)
                          Buffers: shared hit=3
        ->  Limit  (cost=0.28..8.30 rows=1 width=10) (actual time=0.004..0.005 rows=1 loops=1)
              Buffers: shared hit=3
              ->  Index Scan using press_editions_identity on press_editions press_editions_1  (cost=0.28..8.30 rows=1 width=10) (actual time=0.004..0.004 rows=1 loops=1)
                    Index Cond: ((kind = 'daily'::text) AND (edition_date = press_editions.edition_date) AND (slug = press_editions.slug))
                    Buffers: shared hit=3
  ->  Limit  (cost=0.28..0.46 rows=1 width=39) (actual time=0.010..0.011 rows=1 loops=1)
        Buffers: shared hit=3
        ->  Index Scan Backward using press_editions_identity on press_editions press_editions_2  (cost=0.28..268.70 rows=1510 width=39) (actual time=0.010..0.010 rows=1 loops=1)
              Index Cond: (kind = 'daily'::text)
              Buffers: shared hit=3
Planning Time: 0.235 ms
Execution Time: 0.078 ms
```

### `FRONT_HEADERS_SQL` (-): /front: newest weekly, newest monthly, five newest articles

```
Append  (cost=0.28..9.30 rows=7 width=41) (actual time=0.030..0.055 rows=7 loops=1)
  Buffers: shared hit=9
  ->  Limit  (cost=0.28..1.30 rows=1 width=41) (actual time=0.029..0.030 rows=1 loops=1)
        Buffers: shared hit=3
        ->  Index Scan Backward using press_editions_identity on press_editions  (cost=0.28..211.08 rows=207 width=41) (actual time=0.029..0.029 rows=1 loops=1)
              Index Cond: (kind = 'weekly'::text)
              Filter: (withdrawn_at IS NULL)
              Buffers: shared hit=3
  ->  Limit  (cost=0.28..2.99 rows=1 width=41) (actual time=0.008..0.008 rows=1 loops=1)
        Buffers: shared hit=3
        ->  Index Scan Backward using press_editions_identity on press_editions press_editions_1  (cost=0.28..130.45 rows=48 width=41) (actual time=0.008..0.008 rows=1 loops=1)
              Index Cond: (kind = 'monthly'::text)
              Filter: (withdrawn_at IS NULL)
              Buffers: shared hit=3
  ->  Limit  (cost=0.28..4.98 rows=5 width=41) (actual time=0.010..0.015 rows=5 loops=1)
        Buffers: shared hit=3
        ->  Result  (cost=0.28..212.73 rows=226 width=41) (actual time=0.010..0.015 rows=5 loops=1)
              Buffers: shared hit=3
              ->  Unique  (cost=0.28..212.73 rows=226 width=41) (actual time=0.009..0.012 rows=5 loops=1)
                    Buffers: shared hit=3
                    ->  Index Scan Backward using press_editions_identity on press_editions press_editions_2  (cost=0.28..211.58 rows=231 width=41) (actual time=0.008..0.010 rows=5 loops=1)
                          Index Cond: (kind = 'article'::text)
                          Filter: (withdrawn_at IS NULL)
                          Buffers: shared hit=3
Planning Time: 0.152 ms
Execution Time: 0.081 ms
```

### `HEADERS_SQL` (kind=daily, n=14): /editions?kind=daily (default n)

```
Limit  (cost=0.28..3.39 rows=14 width=41) (actual time=0.019..0.034 rows=14 loops=1)
  Buffers: shared hit=3
  ->  Result  (cost=0.28..276.18 rows=1241 width=41) (actual time=0.018..0.031 rows=14 loops=1)
        Buffers: shared hit=3
        ->  Unique  (cost=0.28..276.18 rows=1241 width=41) (actual time=0.017..0.027 rows=14 loops=1)
              Buffers: shared hit=3
              ->  Index Scan Backward using press_editions_identity on press_editions  (cost=0.28..268.70 rows=1497 width=41) (actual time=0.017..0.022 rows=14 loops=1)
                    Index Cond: (kind = 'daily'::text)
                    Filter: (withdrawn_at IS NULL)
                    Buffers: shared hit=3
Planning Time: 0.055 ms
Execution Time: 0.048 ms
```

### `HEADERS_SQL` (kind=daily, n=60): /editions?kind=daily&n=60 (the cap)

```
Limit  (cost=0.28..13.62 rows=60 width=41) (actual time=0.019..0.077 rows=60 loops=1)
  Buffers: shared hit=4
  ->  Result  (cost=0.28..276.18 rows=1241 width=41) (actual time=0.019..0.069 rows=60 loops=1)
        Buffers: shared hit=4
        ->  Unique  (cost=0.28..276.18 rows=1241 width=41) (actual time=0.018..0.053 rows=60 loops=1)
              Buffers: shared hit=4
              ->  Index Scan Backward using press_editions_identity on press_editions  (cost=0.28..268.70 rows=1497 width=41) (actual time=0.017..0.039 rows=61 loops=1)
                    Index Cond: (kind = 'daily'::text)
                    Filter: (withdrawn_at IS NULL)
                    Rows Removed by Filter: 1
                    Buffers: shared hit=4
Planning Time: 0.055 ms
Execution Time: 0.093 ms
```

### `HEADERS_SQL` (kind=article, n=60): /editions?kind=article&n=60

```
Limit  (cost=0.28..56.68 rows=60 width=41) (actual time=0.012..0.076 rows=60 loops=1)
  Buffers: shared hit=7
  ->  Result  (cost=0.28..212.73 rows=226 width=41) (actual time=0.011..0.068 rows=60 loops=1)
        Buffers: shared hit=7
        ->  Unique  (cost=0.28..212.73 rows=226 width=41) (actual time=0.010..0.052 rows=60 loops=1)
              Buffers: shared hit=7
              ->  Index Scan Backward using press_editions_identity on press_editions  (cost=0.28..211.58 rows=231 width=41) (actual time=0.010..0.038 rows=60 loops=1)
                    Index Cond: (kind = 'article'::text)
                    Filter: (withdrawn_at IS NULL)
                    Buffers: shared hit=7
Planning Time: 0.056 ms
Execution Time: 0.093 ms
```

### `EDITION_SQL` (kind=daily, date=2026-10-10, slug=): /edition?kind=daily&date=2026-10-10

```
Nested Loop Left Join  (cost=8.86..24.95 rows=1 width=228) (actual time=0.028..0.030 rows=1 loops=1)
  Buffers: shared hit=9
  ->  Nested Loop Left Join  (cost=8.58..16.64 rows=1 width=195) (actual time=0.014..0.014 rows=1 loops=1)
        Buffers: shared hit=6
        ->  Nested Loop Left Join  (cost=0.28..8.32 rows=1 width=179) (actual time=0.009..0.010 rows=1 loops=1)
              Buffers: shared hit=3
              ->  Result  (cost=0.00..0.01 rows=1 width=0) (actual time=0.000..0.000 rows=1 loops=1)
              ->  Limit  (cost=0.28..8.30 rows=1 width=179) (actual time=0.008..0.008 rows=1 loops=1)
                    Buffers: shared hit=3
                    ->  Index Scan Backward using press_editions_identity on press_editions  (cost=0.28..8.30 rows=1 width=179) (actual time=0.008..0.008 rows=1 loops=1)
                          Index Cond: ((kind = 'daily'::text) AND (edition_date = '2026-10-10'::date) AND (slug = ''::text))
                          Filter: (withdrawn_at IS NULL)
                          Buffers: shared hit=3
        ->  Aggregate  (cost=8.30..8.31 rows=1 width=16) (actual time=0.004..0.004 rows=1 loops=1)
              Buffers: shared hit=3
              ->  Index Scan using press_editions_identity on press_editions press_editions_1  (cost=0.28..8.30 rows=1 width=8) (actual time=0.002..0.002 rows=1 loops=1)
                    Index Cond: ((kind = 'daily'::text) AND (edition_date = '2026-10-10'::date) AND (slug = ''::text))
                    Buffers: shared hit=3
  ->  Limit  (cost=0.28..8.30 rows=1 width=33) (actual time=0.014..0.014 rows=1 loops=1)
        Buffers: shared hit=3
        ->  Index Scan Backward using press_editions_identity on press_editions press_editions_2  (cost=0.28..8.30 rows=1 width=33) (actual time=0.014..0.014 rows=1 loops=1)
              Index Cond: ((kind = 'daily'::text) AND (edition_date = '2026-10-10'::date) AND (slug = ''::text))
              Buffers: shared hit=3
Planning Time: 0.125 ms
Execution Time: 0.050 ms
```

### `EDITION_SQL` (kind=article, date=2026-10-10, slug=gas-carried-california-october-2026): /edition?kind=article&...&slug=

```
Nested Loop Left Join  (cost=8.86..24.95 rows=1 width=228) (actual time=0.021..0.023 rows=1 loops=1)
  Buffers: shared hit=9
  ->  Nested Loop Left Join  (cost=8.58..16.64 rows=1 width=195) (actual time=0.017..0.019 rows=1 loops=1)
        Buffers: shared hit=6
        ->  Nested Loop Left Join  (cost=0.28..8.32 rows=1 width=179) (actual time=0.011..0.012 rows=1 loops=1)
              Buffers: shared hit=3
              ->  Result  (cost=0.00..0.01 rows=1 width=0) (actual time=0.000..0.000 rows=1 loops=1)
              ->  Limit  (cost=0.28..8.30 rows=1 width=179) (actual time=0.010..0.010 rows=1 loops=1)
                    Buffers: shared hit=3
                    ->  Index Scan Backward using press_editions_identity on press_editions  (cost=0.28..8.30 rows=1 width=179) (actual time=0.010..0.010 rows=1 loops=1)
                          Index Cond: ((kind = 'article'::text) AND (edition_date = '2026-10-10'::date) AND (slug = 'gas-carried-california-october-2026'::text))
                          Filter: (withdrawn_at IS NULL)
                          Buffers: shared hit=3
        ->  Aggregate  (cost=8.30..8.31 rows=1 width=16) (actual time=0.005..0.005 rows=1 loops=1)
              Buffers: shared hit=3
              ->  Index Scan using press_editions_identity on press_editions press_editions_1  (cost=0.28..8.30 rows=1 width=8) (actual time=0.003..0.003 rows=1 loops=1)
                    Index Cond: ((kind = 'article'::text) AND (edition_date = '2026-10-10'::date) AND (slug = 'gas-carried-california-october-2026'::text))
                    Buffers: shared hit=3
  ->  Limit  (cost=0.28..8.30 rows=1 width=33) (actual time=0.003..0.003 rows=1 loops=1)
        Buffers: shared hit=3
        ->  Index Scan Backward using press_editions_identity on press_editions press_editions_2  (cost=0.28..8.30 rows=1 width=33) (actual time=0.003..0.003 rows=1 loops=1)
              Index Cond: ((kind = 'article'::text) AND (edition_date = '2026-10-10'::date) AND (slug = 'gas-carried-california-october-2026'::text))
              Buffers: shared hit=3
Planning Time: 0.203 ms
Execution Time: 0.052 ms
```

### `EDITION_SQL` (kind=daily, date=2022-10-11, slug=): /edition, an identity with a withdrawn revision 1

```
Nested Loop Left Join  (cost=8.86..24.95 rows=1 width=228) (actual time=0.019..0.020 rows=1 loops=1)
  Buffers: shared hit=9
  ->  Nested Loop Left Join  (cost=8.58..16.64 rows=1 width=195) (actual time=0.016..0.016 rows=1 loops=1)
        Buffers: shared hit=6
        ->  Nested Loop Left Join  (cost=0.28..8.32 rows=1 width=179) (actual time=0.010..0.011 rows=1 loops=1)
              Buffers: shared hit=3
              ->  Result  (cost=0.00..0.01 rows=1 width=0) (actual time=0.000..0.000 rows=1 loops=1)
              ->  Limit  (cost=0.28..8.30 rows=1 width=179) (actual time=0.009..0.009 rows=1 loops=1)
                    Buffers: shared hit=3
                    ->  Index Scan Backward using press_editions_identity on press_editions  (cost=0.28..8.30 rows=1 width=179) (actual time=0.009..0.009 rows=1 loops=1)
                          Index Cond: ((kind = 'daily'::text) AND (edition_date = '2022-10-11'::date) AND (slug = ''::text))
                          Filter: (withdrawn_at IS NULL)
                          Rows Removed by Filter: 1
                          Buffers: shared hit=3
        ->  Aggregate  (cost=8.30..8.31 rows=1 width=16) (actual time=0.005..0.005 rows=1 loops=1)
              Buffers: shared hit=3
              ->  Index Scan using press_editions_identity on press_editions press_editions_1  (cost=0.28..8.30 rows=1 width=8) (actual time=0.002..0.003 rows=2 loops=1)
                    Index Cond: ((kind = 'daily'::text) AND (edition_date = '2022-10-11'::date) AND (slug = ''::text))
                    Buffers: shared hit=3
  ->  Limit  (cost=0.28..8.30 rows=1 width=33) (actual time=0.002..0.003 rows=1 loops=1)
        Buffers: shared hit=3
        ->  Index Scan Backward using press_editions_identity on press_editions press_editions_2  (cost=0.28..8.30 rows=1 width=33) (actual time=0.002..0.002 rows=1 loops=1)
              Index Cond: ((kind = 'daily'::text) AND (edition_date = '2022-10-11'::date) AND (slug = ''::text))
              Buffers: shared hit=3
Planning Time: 0.128 ms
Execution Time: 0.040 ms
```

### `EDITION_AT_SQL` (kind=daily, date=2026-10-10, slug=, revision=0): /edition?...&revision=0

```
Nested Loop Left Join  (cost=0.56..16.63 rows=1 width=181) (actual time=0.011..0.013 rows=1 loops=1)
  Buffers: shared hit=6
  ->  Nested Loop Left Join  (cost=0.28..8.32 rows=1 width=179) (actual time=0.008..0.009 rows=1 loops=1)
        Buffers: shared hit=3
        ->  Result  (cost=0.00..0.01 rows=1 width=0) (actual time=0.000..0.000 rows=1 loops=1)
        ->  Index Scan using press_editions_identity on press_editions  (cost=0.28..8.30 rows=1 width=179) (actual time=0.006..0.007 rows=1 loops=1)
              Index Cond: ((kind = 'daily'::text) AND (edition_date = '2026-10-10'::date) AND (slug = ''::text) AND (revision = '0'::smallint))
              Buffers: shared hit=3
  ->  Limit  (cost=0.28..8.30 rows=1 width=2) (actual time=0.003..0.003 rows=1 loops=1)
        Buffers: shared hit=3
        ->  Index Scan Backward using press_editions_identity on press_editions press_editions_1  (cost=0.28..8.30 rows=1 width=2) (actual time=0.003..0.003 rows=1 loops=1)
              Index Cond: ((kind = 'daily'::text) AND (edition_date = '2026-10-10'::date) AND (slug = ''::text))
              Filter: (withdrawn_at IS NULL)
              Buffers: shared hit=3
Planning Time: 0.120 ms
Execution Time: 0.025 ms
```
