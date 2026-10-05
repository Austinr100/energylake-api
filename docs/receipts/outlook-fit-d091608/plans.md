# d091608 — EXPLAIN (ANALYZE, BUFFERS), production, 2026-10-05 ~03:30Z

Read-only, through the Neon connector. Each statement is the module's own text, with the
parameters of the newest issuance written in. Buffers were warm (`shared hit`) throughout.

## As they stood (main)

| read | access path | rows | buffers | time |
| --- | --- | --- | --- | --- |
| wind `CALIBRATION_SQL`, HUBSUM, ids 673/674/695/696 (the LATERAL) | Index Scan `implied_gen_calibration_pkey` (4 ids) ⋈ 4 × Index Scan `implied_gen_area_hourly_pkey` on (tech, area_kind, area, model, init_ts range, target_ts range), filter on band / method / `scored_registry_mw > 0` / `written_at <= fitted_at` | 4 (462 rows read per line, 1,373 removed by filter) | 431 | 3.27 ms (exec 3.32) |
| solar `CALIBRATION_SQL`, HUBSUM, ids 713–716 + 403–405 | Index Scan `implied_gen_calibration_pkey` | 7 | 6 | 0.051 ms |

The LATERAL was one PK range per line. As d091590 §plans note 2 forecast, it grows with the
bank (462 rows per line today, against 4 rows per line for the stored-column read).

## The replacement reads (this branch)

| read | access path | rows | buffers | time |
| --- | --- | --- | --- | --- |
| `CALIBRATION_SQL` (one statement, both techs), wind HUBSUM 673/674/695/696 | Index Scan `implied_gen_calibration_pkey`, filter `tech`, `area` | 4 | 3 | 0.039 ms |
| `SCORES_SQL['hub_sum']` (wind, + `lead_min`, `lead_max`, `to_jsonb(s) -> 'hours_rule'`) | 13 × Index Scan Backward `implied_gen_scores_pkey`, LIMIT 1 | 13 | 39 | 0.302 ms |
| solar `HOURS_SQL` (+ `prev_calibration_id`, `prev_lead_h`), HUBSUM 18Z beside 12Z | Merge Join of two Index Scans `implied_gen_area_hourly_pkey` | 240 | 24 | 0.321 ms |

Notes:

1. **The score read's `to_jsonb(s) -> 'hours_rule'`** costs one row-to-jsonb per score row
   (13 rows). The buffers match d091590's 39, and the time is 0.30 ms against 0.14 ms. It keeps
   the statement valid before pantry d091607's column exists and passes the column through
   once it does (`test_PG_score_read_before_and_after_the_hours_rule_column`).
2. **Solar's `HOURS_SQL` is now a merge join.** d091590 recorded a nested loop with a 15,762-row
   join filter for wind's. The table's statistics have caught up, so no `ANALYZE` was needed.
3. **The wind statement is solar's** (`wind_outlook.CALIBRATION_SQL is solar_outlook.CALIBRATION_SQL`).
   It names `tech` and `area`, so an id of another tech or area cannot be read as this area's line.
