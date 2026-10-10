-- d091691 P1/P2: cpc_curves_v2 beside the banked v1 rows, as pantry's v2 writer
-- (cpc_curves_writer_v2, method b201e09ff97d) and its first verdict run would
-- leave the two tables. Constructed, not read from Neon (no v2 row existed there
-- at 2026-10-10 17:32Z). Every row passes migration 298's CHECKs.
--
--  (a) a v2 verdict cell for every v1 cell: a new backtest_version, scored after
--      v1's, floor 22 (D-09-25-177). A cell under 30 base years keeps its count
--      and its sentence says so; a pooled band under 30 is not allowed by
--      ccv_base_ck, so those cells are scored at 30 here.
--  (b) a v2 window row and its days for every banked v1 issuance and place,
--      every tavg value moved by +1.0 F so a v2 value can never pass for v1's.
--  (c) KDEN, which v1 does not write (24 base years), written by v2 on each
--      product's newest v1 issuance: the curve v2 adds.

INSERT INTO cpc_curve_verdicts (backtest_version, scored_at, truth_frontier, method_version,
    method_hash, rules_version, rules_hash, product, place_kind, place, weighting, season,
    strength, min_n_eff, n, n_eff, skill, t, strength_verdict, share_p5_p95, share_p25_p75,
    season_n, season_n_eff, season_skill, season_t, verdict, history_years_in_base,
    band_basis, band_basis_n, band_basis_n_eff, band_share, band_claim, band_sentence,
    drawable, min_base_years, base_fewest_stations, base_by_station, threaded_base, base_reason)
SELECT 'cpcv_2026-10-10_d091691v2', TIMESTAMPTZ '2026-10-11 06:11:00+00', DATE '2026-10-10',
       'cpc_curves_v2', 'b201e09ff97d', 'cpc_curve_verdicts_v2', '0d091691aaaa', product,
       place_kind, place, weighting, season, strength, min_n_eff, n, n_eff, skill, t,
       strength_verdict, share_p5_p95, share_p25_p75, season_n, season_n_eff, season_skill,
       season_t, verdict, h, band_basis, band_basis_n, band_basis_n_eff, band_share,
       band_claim,
       CASE WHEN h < 30 AND band_sentence IS NOT NULL
            THEN band_sentence || ' Its normal rests on ' || h || ' base years, not CPC''s 30.'
            ELSE band_sentence END,
       verdict = 'beats' AND h >= 22 AND band_claim IS NOT NULL,
       22, ARRAY[member], jsonb_build_array(jsonb_build_object('station', member, 'base_years', h)),
       '[]'::jsonb, NULL
  FROM (SELECT v.*,
               CASE WHEN band_basis = 'pooled_stations' THEN 30
                    ELSE history_years_in_base END AS h,
               CASE WHEN place_kind = 'station' THEN place ELSE 'USW00000001' END AS member
          FROM cpc_curve_verdicts v
         WHERE method_version = 'cpc_curves_v1') s;

INSERT INTO cpc_outlook_curves (product, issued_date, valid_start, valid_end, place_kind, place,
    weighting, day_index, target_date, tavg_p05, tavg_p25, tavg_p50, tavg_p75, tavg_p95,
    hdd_p05, hdd_p25, hdd_p50, hdd_p75, hdd_p95, cdd_p05, cdd_p25, cdd_p50, cdd_p75, cdd_p95,
    eq_tavg_p05, eq_tavg_p50, eq_tavg_p95, eq_hdd_p05, eq_hdd_p50, eq_hdd_p95, eq_cdd_p05,
    eq_cdd_p50, eq_cdd_p95, n_history_years, history_first, history_last, empty_class,
    member_odds, season, strength, reading, history, method_version, method_hash,
    writer_version, source_content_sha256, source_r2_key, source_format_epoch, notes,
    written_at, base_years, base_fewest_stations, base_by_station, threaded_base)
SELECT product, issued_date, valid_start, valid_end, place_kind, place, weighting, day_index,
       target_date, tavg_p05 + 1, tavg_p25 + 1, tavg_p50 + 1, tavg_p75 + 1, tavg_p95 + 1,
       hdd_p05, hdd_p25, hdd_p50, hdd_p75, hdd_p95, cdd_p05, cdd_p25, cdd_p50, cdd_p75,
       cdd_p95, eq_tavg_p05 + 1, eq_tavg_p50 + 1, eq_tavg_p95 + 1, eq_hdd_p05, eq_hdd_p50,
       eq_hdd_p95, eq_cdd_p05, eq_cdd_p50, eq_cdd_p95, n_history_years, history_first,
       history_last, empty_class, member_odds, season, strength, reading, history,
       'cpc_curves_v2', 'b201e09ff97d', 'cpc_curves_writer_v2', source_content_sha256,
       source_r2_key, source_format_epoch, notes, TIMESTAMPTZ '2026-10-10 22:50:00+00', 30,
       ARRAY[member], jsonb_build_array(jsonb_build_object('station', member, 'base_years', 30)),
       '[]'::jsonb
  FROM (SELECT c.*, CASE WHEN place_kind = 'station' THEN place ELSE 'USW00000001' END AS member
          FROM cpc_outlook_curves c
         WHERE method_version = 'cpc_curves_v1') s;

-- (c) KDEN (USW00003017) on each product's newest v1 issuance: KSAN's v2 rows
-- for that issuance, restated at KDEN on 24 base years.
INSERT INTO cpc_outlook_curves (product, issued_date, valid_start, valid_end, place_kind, place,
    weighting, day_index, target_date, tavg_p05, tavg_p25, tavg_p50, tavg_p75, tavg_p95,
    hdd_p05, hdd_p25, hdd_p50, hdd_p75, hdd_p95, cdd_p05, cdd_p25, cdd_p50, cdd_p75, cdd_p95,
    eq_tavg_p05, eq_tavg_p50, eq_tavg_p95, eq_hdd_p05, eq_hdd_p50, eq_hdd_p95, eq_cdd_p05,
    eq_cdd_p50, eq_cdd_p95, n_history_years, history_first, history_last, empty_class,
    member_odds, season, strength, reading, history, method_version, method_hash,
    writer_version, source_content_sha256, source_r2_key, source_format_epoch, notes,
    written_at, base_years, base_fewest_stations, base_by_station, threaded_base)
SELECT product, issued_date, valid_start, valid_end, place_kind, 'USW00003017', weighting,
       day_index, target_date, tavg_p05, tavg_p25, tavg_p50, tavg_p75, tavg_p95, hdd_p05,
       hdd_p25, hdd_p50, hdd_p75, hdd_p95, cdd_p05, cdd_p25, cdd_p50, cdd_p75, cdd_p95,
       eq_tavg_p05, eq_tavg_p50, eq_tavg_p95, eq_hdd_p05, eq_hdd_p50, eq_hdd_p95, eq_cdd_p05,
       eq_cdd_p50, eq_cdd_p95, n_history_years, history_first, history_last, empty_class,
       member_odds, season, strength, reading, history, method_version, method_hash,
       writer_version, source_content_sha256, source_r2_key, source_format_epoch,
       ARRAY['24 complete base windows 1991-2020'], written_at, 24, ARRAY['USW00003017'],
       '[{"station": "USW00003017", "base_years": 24}]'::jsonb, '[]'::jsonb
  FROM cpc_outlook_curves c
 WHERE method_version = 'cpc_curves_v2' AND place = 'USW00023188'
   AND issued_date = (SELECT max(issued_date) FROM cpc_outlook_curves x
                       WHERE x.product = c.product AND x.method_version = 'cpc_curves_v1');
