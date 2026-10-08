-- d091666 plan receipts: each statement is the pinned SQL (dd_board.<NAME>) with
-- the stated literals in place of its parameters, run as EXPLAIN (ANALYZE, BUFFERS)
-- on Neon fancy-block-96153928 (production), read-only. Plans: plans_raw.txt.

-- S01 SCORES_SQL {"place_kind": "'station'"}
EXPLAIN (ANALYZE, BUFFERS) SELECT as_of_date, ghcn_frontier, member, lead_day, place_kind, place, weighting, window_start, window_end, min_target_days, n_target_days, n_pairs, n_provisional_days, scored, bias_tavg_f, mae_tavg_f, rmse_tavg_f, mae_hdd, mae_cdd, excluded, scorer_version, scorer_hash, blend_method_version, created_at FROM v_dd_member_scores_current WHERE place_kind = 'station' ORDER BY place, weighting, member, lead_day;

-- S02 SCORES_SQL {"place_kind": "'station'"}
EXPLAIN (ANALYZE, BUFFERS) SELECT as_of_date, ghcn_frontier, member, lead_day, place_kind, place, weighting, window_start, window_end, min_target_days, n_target_days, n_pairs, n_provisional_days, scored, bias_tavg_f, mae_tavg_f, rmse_tavg_f, mae_hdd, mae_cdd, excluded, scorer_version, scorer_hash, blend_method_version, created_at FROM v_dd_member_scores_current WHERE place_kind = 'station' ORDER BY place, weighting, member, lead_day;

-- S03 SCORES_SQL {"place_kind": "'region'"}
EXPLAIN (ANALYZE, BUFFERS) SELECT as_of_date, ghcn_frontier, member, lead_day, place_kind, place, weighting, window_start, window_end, min_target_days, n_target_days, n_pairs, n_provisional_days, scored, bias_tavg_f, mae_tavg_f, rmse_tavg_f, mae_hdd, mae_cdd, excluded, scorer_version, scorer_hash, blend_method_version, created_at FROM v_dd_member_scores_current WHERE place_kind = 'region' ORDER BY place, weighting, member, lead_day;

-- S04 SCORES_SQL {"place_kind": "'region'"}
EXPLAIN (ANALYZE, BUFFERS) SELECT as_of_date, ghcn_frontier, member, lead_day, place_kind, place, weighting, window_start, window_end, min_target_days, n_target_days, n_pairs, n_provisional_days, scored, bias_tavg_f, mae_tavg_f, rmse_tavg_f, mae_hdd, mae_cdd, excluded, scorer_version, scorer_hash, blend_method_version, created_at FROM v_dd_member_scores_current WHERE place_kind = 'region' ORDER BY place, weighting, member, lead_day;

-- B01 BLEND_SQL {"from_date": "'2026-10-08'", "to_date": "'2026-10-24'", "station": "NULL::text"}
EXPLAIN (ANALYZE, BUFFERS) SELECT station_id, target_date, issue_date, lead_day, method_version, method_hash, tavg_f, hdd, cdd, n_members_used, blend_mae, best_member, best_member_mae, blend_n_target_days, computed_ts FROM v_dd_blend_drawable WHERE target_date >= '2026-10-08' AND target_date < '2026-10-24' AND (NULL::text::text IS NULL OR station_id = NULL::text) ORDER BY station_id, target_date, lead_day;

-- B02 BLEND_SQL {"from_date": "'2026-10-08'", "to_date": "'2026-10-24'", "station": "NULL::text"}
EXPLAIN (ANALYZE, BUFFERS) SELECT station_id, target_date, issue_date, lead_day, method_version, method_hash, tavg_f, hdd, cdd, n_members_used, blend_mae, best_member, best_member_mae, blend_n_target_days, computed_ts FROM v_dd_blend_drawable WHERE target_date >= '2026-10-08' AND target_date < '2026-10-24' AND (NULL::text::text IS NULL OR station_id = NULL::text) ORDER BY station_id, target_date, lead_day;

-- B03 BLEND_SQL {"from_date": "'2026-10-08'", "to_date": "'2026-10-24'", "station": "'USW00024233'"}
EXPLAIN (ANALYZE, BUFFERS) SELECT station_id, target_date, issue_date, lead_day, method_version, method_hash, tavg_f, hdd, cdd, n_members_used, blend_mae, best_member, best_member_mae, blend_n_target_days, computed_ts FROM v_dd_blend_drawable WHERE target_date >= '2026-10-08' AND target_date < '2026-10-24' AND ('USW00024233'::text IS NULL OR station_id = 'USW00024233') ORDER BY station_id, target_date, lead_day;

-- B04 BLEND_SQL {"from_date": "'2026-10-08'", "to_date": "'2026-10-24'", "station": "'USW00024233'"}
EXPLAIN (ANALYZE, BUFFERS) SELECT station_id, target_date, issue_date, lead_day, method_version, method_hash, tavg_f, hdd, cdd, n_members_used, blend_mae, best_member, best_member_mae, blend_n_target_days, computed_ts FROM v_dd_blend_drawable WHERE target_date >= '2026-10-08' AND target_date < '2026-10-24' AND ('USW00024233'::text IS NULL OR station_id = 'USW00024233') ORDER BY station_id, target_date, lead_day;

-- B05 BLEND_SQL {"from_date": "'2026-09-08'", "to_date": "'2026-10-08'", "station": "NULL::text"}
EXPLAIN (ANALYZE, BUFFERS) SELECT station_id, target_date, issue_date, lead_day, method_version, method_hash, tavg_f, hdd, cdd, n_members_used, blend_mae, best_member, best_member_mae, blend_n_target_days, computed_ts FROM v_dd_blend_drawable WHERE target_date >= '2026-09-08' AND target_date < '2026-10-08' AND (NULL::text::text IS NULL OR station_id = NULL::text) ORDER BY station_id, target_date, lead_day;

-- B06 BLEND_SQL {"from_date": "'2026-09-08'", "to_date": "'2026-10-08'", "station": "NULL::text"}
EXPLAIN (ANALYZE, BUFFERS) SELECT station_id, target_date, issue_date, lead_day, method_version, method_hash, tavg_f, hdd, cdd, n_members_used, blend_mae, best_member, best_member_mae, blend_n_target_days, computed_ts FROM v_dd_blend_drawable WHERE target_date >= '2026-09-08' AND target_date < '2026-10-08' AND (NULL::text::text IS NULL OR station_id = NULL::text) ORDER BY station_id, target_date, lead_day;
