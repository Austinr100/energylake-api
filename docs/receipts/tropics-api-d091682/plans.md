# d091682 plan receipts: the Tropics reads moved onto the rows in force

**Two firings.**
- **The first (below) stopped at STOP-V.** Through 294's views, /tracks and /odds were seq scans of whole tables.
- **Pantry applied the fix proposed at the end of this file as migration 297** (2026-10-10 11:55:17Z).
- **The re-fire** took the six statements again through 297's views, 11:58–12:00Z (`neon_plans_297.json`, pinned by `test_R8_the_neon_receipt_*`):

| statement | d091673 receipt | 294 views (first firing) | **297 views** | ratio |
|---|---:|---:|---:|---:|
| STORMS_SQL | 0.135 ms | 0.608 ms | **0.139 ms**, 31 buf | 1.0× |
| OFFICIAL_NEWEST_SQL | 0.150 ms | 0.912 ms | **0.516 ms**, 155 buf | 3.4× |
| OBSERVED_SQL | 1.02 ms | 3.19 ms | **0.242 ms**, 97 buf | 0.2× |
| CYCLES_SQL | 10.3 ms | 86.4 ms | **15.9 ms**, 1,204 buf | 1.5× |
| TRACKS_SQL n = 8 | 3.26 ms | 29–61 ms, seq scan | **6.6 ms**, 2,363 buf | 2.0× |
| ODDS_SQL | 0.357 ms | 3.36 ms, seq scan | **0.294 ms**, 19 buf | 0.8× |

- **The plans:** every anti-join's inner side is `ttp_revised` or `tpo_revised`, and every ledger lookup is `tfv_identity`. No plan scans a whole table.
- **STOP-V does not fire on the re-fire.**
- **The bank at that read** held the first real correction with different rows: Simon 012A at revision 1, 6 points. `OFFICIAL_NEWEST_SQL` returned 24 of its 30 candidate rows.

---

# The first firing — STOP-V

**How (Neon).** EXPLAIN (ANALYZE, BUFFERS) of each statement in `explains_before.sql` (d091673's, the bare tables, git `c40c231:tropics.py`) and `explains_after.sql` (this lane's draft, the 294 views, `draft_rows_in_force.patch`). Both were rendered by psycopg's `ClientCursor.mogrify` (`render_explains.py`).
- **Where:** the Neon connector, production database (Energylake, Postgres 17), 2026-10-10 10:38–10:49Z. Every statement was a SELECT; nothing was written.
- **Parameters:** the same as d091673's receipts: Rachel `ep182026` for /storm and /tracks, Isaias `al092026` for /odds, the three storms for the official.

**Data size.** `tropical_track_points` 36,523 rows (869 pages; Rachel 22,671), `tropical_place_odds` 4,046 (84 pages), `tropical_file_vintage` 3,348 (171 pages). No row has revision > 0.

**How (locally).** `rehearse_view_plans.py` → `view_plans_local.json` / `.txt`. A throwaway Postgres 16 is loaded with the d091673 bank, then migration 294 (`tests/fixtures/tropics_d091682/ddl_294.sql`). One constructed correction is added: Simon 010's official with one tau moved and one dropped, and Simon's newest PWS issuance with one value changed and one place dropped.
- Each statement runs three ways: **before** / **after** / **fixed** (after + `proposed_pantry_view_fix.sql`).
- Sizes: at the bank's size and at **31×** (1.07 M points, 89,838 odds).
- Timing: the median of 5 runs after a warming run.
- **The fixed views return exactly the rows 294's views return**, at both sizes. The script asserts it.

## STOP-V fired

> *If any read through a view is a seq scan of a whole table or more than ten times its d091673 timing, stop and show both plans and what pantry's view would need.*

| statement | route | d091673 receipt (10-09) | before, re-taken 10-10 (warm) | after, through the views | plan change | STOP-V |
|---|---|---:|---:|---:|---|---|
| `POLL_SQL` | /storms | 0.056 ms | — | statement unchanged (heartbeat index, no view) | none | no |
| `STORMS_SQL` | /storms | 0.135 ms, 25 buf | 0.140 ms, 25 buf | **0.608 ms, 95 buf** (9.3 ms cold) | per storm, the LIMIT 1 now materialises **all** of the storm's best-track / tcvitals keys for the anti-join | no (4.3×) |
| `OFFICIAL_NEWEST_SQL` | /storms, /storm | 0.150 ms, 28 buf | 0.249 ms, 34 buf | **0.912 ms, 163 buf** (4.9 ms cold) | the same materialise for the newest init, one anti-join probe per point, and a `tfv_identity` probe per advisory | no (3.7×) |
| `STORM_SQL` | /storm, /tracks, /odds | 0.030 ms | — | statement unchanged | none | no |
| `OBSERVED_SQL` | /storm | 1.02 ms, 21 buf (cold) | 0.139 ms, 24 buf | **3.19 ms, 319 buf** | one index-only anti-join probe per point (73), ~4 buffers each | **yes by the same-moment ratio (23×)**; 3.1× the d091673 receipt, which was taken cold |
| `CYCLES_SQL` | /storm | 10.3 ms, 1,053 buf | 11.5 ms, 1,088 buf | **86.4 ms, 2,223 buf** (219 ms cold) | each of the 58 cycles is read twice: a Hash Anti Join on (source, advisory) whose inner is the cycle again; model rows all have advisory `''`, so every tau of a source collides in one bucket (9,637 rows removed by join filter per cycle) | 7.5–8.4×: under the line, but closer than anything else that stays indexed |
| `TRACKS_SQL` n = 8 | /tracks | 3.26 ms, 115 buf | 2.75 ms, 110 buf | **29.2–61.5 ms, 1,089–1,092 buf** | **Hash Anti Join over a Seq Scan of the whole `tropical_track_points`** (919 buffers, 22,671 rows kept of 36,523) | **yes: seq scan, and 9–22×** |
| `ODDS_SQL` | /odds | 0.357 ms, 22 buf | 0.944 ms, 17 buf | **3.36 ms, 103 buf** (draft as banked); 5.6–12.6 ms in the first draft | **Hash Anti Join over a Seq Scan of the whole `tropical_place_odds`** (84 pages, 1,568 rows kept of 4,046) | **yes: seq scan** |

The "after" times vary run to run because Neon's compute had evicted pages: both TRACKS runs read 300–940 buffers from storage. The plan shape did not vary: every after run of `TRACKS_SQL` and `ODDS_SQL` was the hash anti-join over a whole-table seq scan.

### Why the views do this

294's view keeps a row unless `NOT EXISTS (a row of the same advisory with q.revision > p.revision)`.
- **Where it is only an anti-join on the bare table:** the planner can restrict the inner side only by what it can carry across, which is `storm_id` and sometimes `source`.
- **For a statement returning many rows** (2,214 points for /tracks n = 8, 154 odds rows), it judges one hash of the storm's whole history cheaper than 2,214 probes.
- **On Neon** Rachel is 62 % of the points table, so that hash is a seq scan of the whole table.
- **At 31× (locally)** the hash becomes an index range over the storm's whole history: 7,906 buffers for /tracks n = 8, against 152. Either way the cost now grows with the storm's age, which d091673's loose index scan was built to avoid ("Rachel's 48 older cycles are never touched").

### The plans, as Neon printed them (abridged to the plan nodes)

```
-- TRACKS_SQL n = 8, BEFORE (bare table)                Execution Time: 2.754 ms, Buffers: shared hit=110
Sort (rows=2214)
  CTE cyc -> Recursive Union (rows=8): Limit -> Index Scan using ttp_storm_init (loops=1, 7)
  InitPlan 3 -> Limit (rows=8) -> CTE Scan on cyc
  ->  Bitmap Heap Scan on tropical_track_points p (rows=2214)  Heap Blocks: exact=76
        ->  Bitmap Index Scan on ttp_storm_init  Index Cond: (storm_id = 'ep182026' AND init_ts = ANY (InitPlan 3))

-- TRACKS_SQL n = 8, AFTER (tropical_track_points_in_force)   Execution Time: 29.171 ms, Buffers: shared hit=146 read=943
Sort (rows=2214)
  CTE cyc -> Recursive Union (rows=8)
        -> Limit -> Nested Loop Anti Join
              -> Index Scan using ttp_storm_init on tropical_track_points p_1
              -> Index Only Scan using uniq_tropical_track_points on tropical_track_points q_1
                   Index Cond: (storm_id, source = p_1.source, init_ts = p_1.init_ts, advisory = p_1.advisory, revision > p_1.revision)
  CTE pts
    ->  Hash Anti Join (rows=2214)
          Hash Cond: (p_2.source = q_2.source AND p_2.init_ts = q_2.init_ts AND p_2.advisory = q_2.advisory)
          Join Filter: (q_2.revision > p_2.revision)   Rows Removed by Join Filter: 34822
          ->  Bitmap Heap Scan on tropical_track_points p_2 (rows=2214) -> Bitmap Index Scan on ttp_storm_init
          ->  Hash (rows=22671)
                ->  Seq Scan on tropical_track_points q_2 (rows=22671)          <-- the whole table
                      Filter: (storm_id = 'ep182026')  Rows Removed by Filter: 13852
                      Buffers: shared hit=9 read=910
  ->  Hash Left Join (pts.advisory = official_files.advisory)
        ->  Nested Loop -> Unique (8 advisories) -> Aggregate -> Unique -> Incremental Sort
              -> Index Scan using tfv_identity on tropical_file_vintage v (rows=2 loops=8)

-- ODDS_SQL, BEFORE (bare table)                         Execution Time: 0.944 ms, Buffers: shared hit=16 read=1
Sort (rows=154)
  ->  Nested Loop
        ->  Limit -> Index Only Scan Backward using uniq_tropical_place_odds
        ->  Bitmap Heap Scan on tropical_place_odds o (rows=154) -> Bitmap Index Scan on uniq_tropical_place_odds

-- ODDS_SQL, AFTER (tropical_place_odds_in_force; draft as banked)   Execution Time: 3.356 ms, Buffers: shared hit=33 read=70
Sort (rows=154)
  ->  Hash Anti Join  Hash Cond: (o.issued_ts = q.issued_ts AND o.advisory = q.advisory)
        Join Filter: (q.revision > o.revision)   Rows Removed by Join Filter: 23716
        ->  Nested Loop
              ->  Nested Loop Left Join
                    ->  Limit -> Nested Loop Anti Join
                          -> Index Scan Backward using uniq_tropical_place_odds on tropical_place_odds o_1
                          -> Index Scan using uniq_tropical_place_odds on tropical_place_odds q_1
                    ->  Aggregate -> Limit -> Sort -> Index Scan using tfv_identity on tropical_file_vintage v (rows=1)
              ->  Bitmap Heap Scan on tropical_place_odds o (rows=154)
        ->  Hash (rows=1568)
              ->  Seq Scan on tropical_place_odds q (rows=1568)                 <-- the whole table
                    Filter: (storm_id = 'al092026' AND source = 'nhc_pws')  Rows Removed by Filter: 2478

-- OBSERVED_SQL, BEFORE                                  Execution Time: 0.139 ms, Buffers: shared hit=24
Incremental Sort -> Index Scan using uniq_tropical_track_points (rows=73)
-- OBSERVED_SQL, AFTER                                   Execution Time: 3.193 ms, Buffers: shared hit=319
Incremental Sort
  ->  Nested Loop Anti Join (rows=73)
        ->  Index Scan using uniq_tropical_track_points on tropical_track_points p (rows=73)
        ->  Index Only Scan using uniq_tropical_track_points on tropical_track_points q (rows=0 loops=73)  Buffers: shared hit=292

-- CYCLES_SQL, BEFORE                                    Execution Time: 11.539 ms, Buffers: shared hit=1088
Nested Loop -> CTE Scan on cyc (58) -> HashAggregate (loops=58) -> Bitmap Heap Scan (rows=389 loops=58)
-- CYCLES_SQL, AFTER (warm)                              Execution Time: 86.393 ms, Buffers: shared hit=2223
Nested Loop -> CTE Scan on cyc (58)
  ->  GroupAggregate -> Sort
        ->  Hash Anti Join  Hash Cond: (p.source = q.source AND p.advisory = q.advisory)
              Join Filter: (q.revision > p.revision)   Rows Removed by Join Filter: 9637   (per cycle)
              ->  Bitmap Heap Scan on tropical_track_points p (rows=389 loops=58)
              ->  Hash -> Bitmap Heap Scan on tropical_track_points q (rows=391 loops=58)   <-- each cycle read twice
```

`STORMS_SQL` and `OFFICIAL_NEWEST_SQL` after: every scan is an index scan. The per-storm `LIMIT 1` is now a Nested Loop Anti Join, which materialises the storm's whole (storm, source) key range of `uniq_tropical_track_points` (36 best-track keys, 48 official keys) to test one row.

## What pantry's view would need (`proposed_pantry_view_fix.sql`)

The only rows that can supersede anything are rows at revision > 0. There are none today, and there will be few ever: one set per corrected advisory whose numbers changed.

**The fix:**
1. Add `q.revision > 0` to each view's NOT EXISTS. It is implied by `q.revision > p.revision` and the CHECK `revision >= 0`, so no row in force changes.
2. Add a partial index of exactly those rows:
   - `ttp_revised ON tropical_track_points (storm_id, source, init_ts, advisory, revision) WHERE revision > 0`;
   - `tpo_revised` likewise on `(storm_id, source, issued_ts, advisory, revision)`.
3. Keep the view names and columns: it is a `CREATE OR REPLACE VIEW`, and the API needs no change for it.

**Locally, the same statements on the fixed views** (median ms / shared buffers; `view_plans_local.txt`):

| statement | bank: before | bank: 294 views | bank: **fixed** | 31×: before | 31×: 294 views | 31×: **fixed** |
|---|---|---|---|---|---|---|
| STORMS | 0.061 / 25 | 0.194 / 81 | **0.082 / 31** | 0.088 / 31 | 0.221 / 87 | **0.168 / 37** |
| OFFICIAL_NEWEST | 0.072 / 27 | 0.483 / 170 | **0.292 / 100** | 0.073 / 27 | 0.377 / 171 | **0.498 / 100** |
| OBSERVED | 0.071 / 20 | 0.283 / 227 | **0.112 / 21** | 0.061 / 20 | 0.243 / 227 | **0.216 / 21** |
| CYCLES | 9.87 / 1,048 | 99.5 / 2,077 | **17.2 / 1,106** | 14.1 / 1,161 | 78.7 / 2,247 | **21.4 / 1,219** |
| TRACKS n = 8 | 2.98 / 119 | 20.8 / 1,021 **seq** | **6.42 / 130** | 3.34 / 152 | 17.8 / 7,906 | **5.75 / 163** |
| TRACKS n = 4 | 1.21 / 59 | 21.3 / 948 **seq** | **2.24 / 70** | 1.19 / 72 | 6.53 / 3,189 | **2.26 / 83** |
| ODDS | 0.259 / 19 | 12.5 / 88 **seq** | **0.415 / 23** | 0.281 / 21 | 16.2 / 3,055 | **1.47 / 781** |

- **With the fix, nothing seq-scans the points or odds table, at either size.** The anti-join's inner side is `ttp_revised` / `tpo_revised`.
- **What it still costs:** one probe of an index holding only corrected rows, per row served. The 31× ODDS buffers (781) are 378 such probes, 2 buffers each, against an index of 6,944 constructed revised rows (224 copied 31×). That is far more corrected rows than the bank will hold.
- **Local `tropical_file_vintage` seq scans** are the fixture's: its ledger holds only the 31 heartbeat rows, one page. On Neon the same lookups use `tfv_identity` (above).
- **Local Postgres is 16, Neon's is 17.** The local "after" plans reproduce Neon's: the same hash anti-join over a whole-table seq scan for TRACKS and ODDS. The fix has not been planned on Neon, because this lane writes nothing there. **Pantry's lane should re-take these plans on Neon after applying it.**

## Why the API does not dodge this itself

The draft could be bent until the planner probes instead of hashing:
- a per-cycle LATERAL for /tracks, as CYCLES already does;
- the issuance as a scalar InitPlan for /odds.

It was not done, for three reasons:
1. **CYCLES shows the ceiling of that approach:** per-cycle and fully indexed, it is still 7.5× slower and reads twice the buffers.
2. **Every page route, and every future reader of the views, would have to know the trick.**
3. **STOP-V asks what the view needs,** not how to route around it.
