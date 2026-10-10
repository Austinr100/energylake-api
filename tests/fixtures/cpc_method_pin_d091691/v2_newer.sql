-- d091691 P1/P3: a cpc_curves_v2-only issuance, 2026-10-10, newer than every v1
-- issuance of either product (v1: 610temp 10-09, 814temp 10-08). It is what
-- the installed cpc-curves.yml writes tonight: pantry's writer writes v2 only.
-- Each product's newest v2 rows, moved to the new issuance (the window and
-- every target date by the same days; still SON). Applied after v2_beside.sql.
INSERT INTO cpc_outlook_curves (product, issued_date, valid_start, valid_end, place_kind, place,
    weighting, day_index, target_date, tavg_p05, tavg_p25, tavg_p50, tavg_p75, tavg_p95,
    hdd_p05, hdd_p25, hdd_p50, hdd_p75, hdd_p95, cdd_p05, cdd_p25, cdd_p50, cdd_p75, cdd_p95,
    eq_tavg_p05, eq_tavg_p50, eq_tavg_p95, eq_hdd_p05, eq_hdd_p50, eq_hdd_p95, eq_cdd_p05,
    eq_cdd_p50, eq_cdd_p95, n_history_years, history_first, history_last, empty_class,
    member_odds, season, strength, reading, history, method_version, method_hash,
    writer_version, source_content_sha256, source_r2_key, source_format_epoch, notes,
    written_at, base_years, base_fewest_stations, base_by_station, threaded_base)
SELECT product, DATE '2026-10-10', valid_start + shift, valid_end + shift, place_kind, place,
       weighting, day_index, target_date + shift, tavg_p05, tavg_p25, tavg_p50, tavg_p75,
       tavg_p95, hdd_p05, hdd_p25, hdd_p50, hdd_p75, hdd_p95, cdd_p05, cdd_p25, cdd_p50,
       cdd_p75, cdd_p95, eq_tavg_p05, eq_tavg_p50, eq_tavg_p95, eq_hdd_p05, eq_hdd_p50,
       eq_hdd_p95, eq_cdd_p05, eq_cdd_p50, eq_cdd_p95, n_history_years, history_first,
       history_last, empty_class, member_odds, season, strength, reading, history,
       method_version, method_hash, writer_version, source_content_sha256, source_r2_key,
       source_format_epoch, notes, TIMESTAMPTZ '2026-10-10 22:50:00+00', base_years,
       base_fewest_stations, base_by_station, threaded_base
  FROM (SELECT c.*, DATE '2026-10-10' - c.issued_date AS shift
          FROM cpc_outlook_curves c
         WHERE method_version = 'cpc_curves_v2'
           AND issued_date = (SELECT max(issued_date) FROM cpc_outlook_curves x
                               WHERE x.product = c.product
                                 AND x.method_version = 'cpc_curves_v2')) s;
