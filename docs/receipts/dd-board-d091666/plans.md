# d091666 receipts: reads, plans, bytes

**Measured:** Neon project `fancy-block-96153928`, production branch, 2026-10-08. Each statement is an `EXPLAIN (ANALYZE, BUFFERS)` of a pinned read (`dd_board.SCORES_SQL`, `dd_board.BLEND_SQL`), with literals in place of its parameters.

- The SQL as run is in `explains.sql`. Each statement is stamped `-- <tag> <NAME> <params>`; test S6 rebuilds it from the pinned SQL and those params.
- Every plan line is copied verbatim into `plans_raw.txt`, from one read-only transaction.
- Nothing was written to Neon. The `dirtied=1` on a first read is Postgres setting hint bits on pages it reads, not a write by this lane.
- Statement timeout on every read: 2 s (`_DD_REGION_FC_STATEMENT_TIMEOUT`, the region board's). The slowest read below is 123 ms, on a first touch.

## Scores: `v_dd_member_scores_current`, one read per place_kind

| tag | read | rows | buffers | time |
|---|---|---:|---|---:|
| S01 | place_kind station, first | 2,016 | 3 hit / 79 read | 14.1 ms |
| S02 | the same, warm | 2,016 | 79 hit | **5.8 ms** |
| S03 | place_kind region | 864 | 79 hit | **3.1 ms** |
| S04 | the same, warm | 864 | 79 hit | 3.0 ms |

**The plan:** `Seq Scan on dd_member_scores`, filtered on `scorer_version = 'dd_member_scores_v2' AND place_kind = ...`. That feeds `Sort`, then `Unique` (the view's DISTINCT ON), then `Sort`.

- The `place_kind` predicate is a DISTINCT ON column, so it reaches the scan ("Rows Removed by Filter: 864" for station).
- The whole table is 79 pages today.

**The cost grows with the table, not with the answer.** The view is `DISTINCT ON (member, lead_day, place_kind, place, weighting)` over every v2 row ever written. The scorer writes a new vintage of 2,880 cells per day and never updates in place (pantry's comment).

- At that rate the table holds ~1 M rows in a year.
- Every read then sorts all of one place_kind's rows to keep 2,016.
- This is the CLAUDE.md trap in a pantry view (the `DISTINCT ON` form reads every row to keep one).
- Linear projection, not measured: 79 pages/day → ~29,000 pages a year. Expect hundreds of ms, and the 2 s line within the year.
- The fix belongs to pantry: a current-cell table the scorer replaces, or an index on the DISTINCT ON key + order (`member, lead_day, place_kind, place, weighting, as_of_date DESC, ghcn_frontier DESC, created_at DESC`) with the view rewritten to one `LATERAL ... LIMIT 1` per cell (d091551).

## Blend: `v_dd_blend_drawable`

| tag | read | rows | buffers | time |
|---|---|---:|---|---:|
| B01 | 2026-10-08 + 16 days, all stations, first | 154 | 430 read | 5.8 ms |
| B02 | the same, warm | 154 | 430 hit | **1.0 ms** |
| B03 | the same, USW00024233 only, first | 10 | 29 hit / 3 read | 1.9 ms |
| B04 | the same, warm | 10 | 32 hit | **0.1 ms** |
| B05 | 2026-09-08 + 30 days (every drawable target before today), first | 264 | 425 hit / 1,734 read | 123.4 ms |
| B06 | the same, warm | 264 | 2,159 hit | **4.1 ms** |

**The plans:**
- B01/B02: `Bitmap Index Scan on idx_dbf_target` (target_date, lead_day), then `Bitmap Heap Scan on dd_blend_forecast`, filtered on the view's `drawable AND method_version = 'el_blend_v2'`.
- B03/B04: `Index Scan using dd_blend_forecast_pkey` (station_id, target_date, ..., method_version).
- B05/B06: at 30 days the planner takes a `Seq Scan on dd_blend_forecast` (2,159 pages, all of it). It is 13,146 rows at ~6 rows a page, because `members` jsonb is wide.

**Growth.** 315 rows/day ≈ 52 pages/day.
- The default window (today + 16) stays on the index.
- A month-wide window seq-scans the table: about 4 ms warm today, roughly 100 ms warm in a year (projection).
- The read carries the 2 s timeout either way.

## Bytes (the banked bodies, served by the routes over the fixtures)

See `bytes.md`. `/scores` is the large one: 767 KB raw, 80 KB gzipped (the app's GZipMiddleware); `?place_kind=region` serves the 864 region cells alone.
