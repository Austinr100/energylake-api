# dd_board_d091666 — production rows, read from Neon 2026-10-08 20:35–20:37Z

Every file is the `t` text of one read-only statement, written byte for byte
after checking the md5 and length Neon computed over that same text
(`manifest.psv`: file | rows | md5 | length; `verify.py` re-checks them).

| file | read |
|---|---|
| scores_region_population.json | `v_dd_member_scores_current`, place_kind region, weighting population |
| scores_region_load_share_365d.json | the same, weighting load_share_365d |
| scores_station.json | the same, place_kind station (weighting '') |
| blend_drawable.json | every row of `v_dd_blend_drawable` |
| board_fc_pnw_population.json | `_DD_REGION_FC_SQL`, pnw / population, 2026-10-08 + 16 days |
| board_delta_pnw_population.json | `_DD_REGION_DELTA_SQL`, the same window |
| board_spread_pnw_population.json | `_DD_REGION_SPREAD_SQL`, the same window |
| vectors.json | `_DD_REGION_FC_VECTORS_SQL` |

Score cells are arrays: member, lead_day, place, n_target_days, n_pairs,
n_provisional_days, scored, bias_tavg_f, mae_tavg_f, rmse_tavg_f, mae_hdd,
mae_cdd, excluded. The fields every cell shares on 2026-10-08 (one vintage,
measured: count(DISTINCT vintage fields) = 1 over all 2,880 cells) are
`vintage.json`. Blend rows are arrays in the view's column order.
