# d091590 — EXPLAIN (ANALYZE, BUFFERS), production, 2026-10-04 ~16:55Z

Every read the two wind routes make, run on Neon production (read-only, through the
Neon connector) with the live cycle's parameters: `tech='wind'`, `model='hrrr_gfs'`,
`init 2026-10-04 12Z`, previous `2026-10-02 06Z`, HUBSUM unless noted. The statements are
`wind_outlook.py`'s own text with the parameters substituted
(`/api/generation/wind/outlook?area_kind=hub_sum` and `/sites`). Warm buffers (`shared hit`)
throughout; nothing read from disk.

| read | access path | rows | buffers | time |
| --- | --- | --- | --- | --- |
| `ISSUANCE_NEWEST_SQL` (solar's, shared) | 2 × Index Only Scan Backward, `implied_gen_area_hourly_pkey`, LIMIT 1 | 1 | 11 | 0.075 ms |
| `HOURS_SQL` | Index Scan pkey (this issuance, 240 rows) ⋈ Index Scan pkey (previous issuance, 66 rows, re-scanned per row) | 240 | 1,443 | 12.2 ms |
| `SCORES_SQL['hub_sum']` (solar's, + 4 columns) | 13 × Index Scan Backward, `implied_gen_scores_pkey`, LIMIT 1 | 13 | 39 | 0.136 ms |
| `CALIBRATION_SQL` (the derived fit leads) | Index Scan `implied_gen_calibration_pkey` (4 ids) ⋈ 4 × Index Scan `implied_gen_area_hourly_pkey` on (tech, area_kind, area, model, init_ts range, target_ts range) | 4 (462 rows read per line) | 435 | 3.4 ms |
| `ACTUALS_SQL[hub_sum]` (solar's statement, wind's pairs) | 6 × Index Scan Backward, `idx_tsv_series_ts`, one `(dataset, series)` each | 57 | 56 | 0.130 ms |
| `ACTUALS_SQL[ba, CISO]` | 1 × Index Scan Backward, `idx_tsv_series_ts` | 5 | 10 | 0.060 ms |
| `FLEET_SQL` | Seq Scan `implied_gen_wind_sites` (323 rows, 12 pages) | 323 | 12 | 0.42 ms |
| `SITES_SQL` target 2026-10-04 20Z | `implied_gen_wind_sites_pkey` ⋈ 323 × Index Scan `implied_gen_site_latest_pkey` on (tech, plant_code, model, target_ts range) | 323 | 1,309 | 3.0 ms |
| `SITES_SQL` day 2026-10-05 | same, 24 rows per plant | 323 | 1,625 | 9.9 ms |

## Notes

1. **`HOURS_SQL` re-scans the previous issuance once per hour.** The planner estimated 1 row
   for each side (the table's statistics predate the live cycle) and chose a nested loop with
   a join filter, so the 66 previous rows are read 240 times (15,762 rows removed by the
   filter). That is solar's statement shape too. 12 ms, behind a 300 s memo and a 5 s
   timeout, so it is left as is; an `ANALYZE implied_gen_area_hourly` after the writer's
   backfill would likely give a merge join. Not run here: this lane writes nothing.
2. **`CALIBRATION_SQL` grows with the bank.** Each line's lateral is a PK range over the
   issuances that could have a target inside the fit window (`init_ts` from fit_start − 240 h
   to fit_end + 1 day). Today that is ~33 backfill issuances, 462 rows per line. With four
   live cycles a day of 240 leads it becomes ~38 × 4 × 240 ≈ 36k rows per line, five lines:
   still one PK range per line, estimated tens of ms. The fix is the writer storing
   `fit_lead_min` / `fit_lead_max` on `implied_gen_calibration` (handback §5.1), after which
   this read is the 4-row PK lookup and nothing else.
3. **`/sites` is one lateral per plant** (CLAUDE.md, d091551): 323 PK ranges of 1 or 24 rows.
   Solar's `/sites` reads `implied_gen_site_latest` by a target window over the whole table
   (solar handback §6.3, 5,264 buffers at full size); wind's would have walked 77,520 rows.
4. **`FLEET_SQL` reads the 323-plant registry whole** and filters in Python; 12 pages.
