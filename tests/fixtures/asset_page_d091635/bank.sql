SELECT json_build_object('read_at', now(), 'columns', (SELECT coalesce(json_agg(t), '[]'::json) FROM (
    SELECT table_name, column_name
      FROM information_schema.columns
     WHERE table_schema = current_schema()
       AND table_name = ANY(ARRAY['implied_gen_site_latest','implied_gen_wind_sites']::text[])
       AND column_name = ANY(ARRAY['ghi_wm2','clearsky_ghi_wm2','clearsky_mw','tcc_pct','precip_mm','hub_ws_ms','gust_ms','cut_in_ms','rated_ms','cut_out_ms']::text[])
) t),
'units_solar_58388', (SELECT coalesce(json_agg(t), '[]'::json) FROM (
    SELECT plant_code, generator_id, plant_name, latitude, longitude,
           ac_mw::float8 AS ac_mw, dc_mw::float8 AS dc_mw, dc_basis,
           mount, mount_basis, tilt_deg::float8 AS tilt_deg, tilt_basis,
           azimuth_deg::float8 AS azimuth_deg, azimuth_basis, bifacial,
           hub, hub_method, ba_code, state, county, outage_resource_id,
           equipment_vintage, registry_vintage, method_version
      FROM implied_gen_sites
     WHERE tech = 'solar_pv' AND plant_code = 58388
     ORDER BY generator_id
) t),
'site_wind_57514', (SELECT coalesce(json_agg(t), '[]'::json) FROM (
    SELECT s.plant_code, s.plant_name, s.latitude, s.longitude,
           s.nameplate_mw::float8 AS nameplate_mw, s.hub, s.ba_code, s.state, s.county,
           s.turbine_model, s.turbine_model_basis, s.n_turbines, s.n_turbines_basis,
           s.rotor_m::float8 AS rotor_m, s.rotor_basis,
           s.hub_height_m::float8 AS hub_height_m, s.hub_height_basis,
           s.curve_turbine_type, s.curve_hub_height_m::float8 AS curve_hub_height_m, s.curve_basis,
           s.counts_in_hub_actual, s.counts_in_hub_actual_basis,
           s.export_cap_group, s.export_cap_mw::float8 AS export_cap_mw, s.export_cap_basis,
           s.hrrr_dist_km,
           s.hub_method, s.op_year_mw_wtd, s.outage_resource_id, s.method_version
      FROM implied_gen_wind_sites s
     WHERE s.plant_code = 57514
) t),
'hours_solar_58388', (SELECT coalesce(json_agg(t), '[]'::json) FROM (
    SELECT l.target_ts, l.init_ts, l.lead_h, l.weather_source, l.implied_mw,
           l.outage_mw_subtracted, l.cap_mw_subtracted, l.method_version
      FROM implied_gen_site_latest l
     WHERE l.tech = 'solar_pv' AND l.plant_code = 58388
       AND l.model = 'gfs'
     ORDER BY l.target_ts
) t),
'hours_wind_57514', (SELECT coalesce(json_agg(t), '[]'::json) FROM (
    SELECT l.target_ts, l.init_ts, l.lead_h, l.weather_source, l.implied_mw,
           l.outage_mw_subtracted, l.cap_mw_subtracted, l.method_version
      FROM implied_gen_site_latest l
     WHERE l.tech = 'wind' AND l.plant_code = 57514
       AND l.model = 'hrrr_gfs'
     ORDER BY l.target_ts
) t),
'scores_solar_SP15', (SELECT coalesce(json_agg(t), '[]'::json) FROM (
    SELECT v.lead_band, v.who, s.area_kind, s.window_start, s.window_end,
           s.n_hours, s.n_days, s.scored, s.bias_mw, s.mae_mw,
           s.mae_pct_installed, s.rmse_mw, s.r, s.scored_at,
           s.lead_min, s.lead_max, s.hours_rule
      FROM (VALUES
            ('h01_06', 'registry'),
            ('h01_06', 'calibrated'),
            ('h07_24', 'registry'),
            ('h07_24', 'calibrated'),
            ('h25_48', 'registry'),
            ('h25_48', 'calibrated'),
            ('h49_120', 'registry'),
            ('h49_120', 'calibrated'),
            ('h121_240', 'registry'),
            ('h121_240', 'calibrated'),
            ('dam_comparable', 'registry'),
            ('dam_comparable', 'calibrated'),
            ('dam_comparable', 'caiso_dam')
           ) AS v(lead_band, who)
     CROSS JOIN LATERAL (
        SELECT s.area_kind, s.window_start, s.window_end, s.n_hours, s.n_days,
               s.scored, s.bias_mw, s.mae_mw, s.mae_pct_installed, s.rmse_mw,
               s.r, s.scored_at, s.lead_min, s.lead_max,
               to_jsonb(s) -> 'hours_rule' AS hours_rule
          FROM implied_gen_scores s
         WHERE s.tech = 'solar_pv' AND s.area = 'SP15'
           AND s.lead_band = v.lead_band AND s.who = v.who
           AND s.area_kind = 'hub'
           AND s.method_version = 'solar_pv_v1'
         ORDER BY s.window_end DESC
         LIMIT 1
     ) AS s
) t),
'scores_wind_SP15', (SELECT coalesce(json_agg(t), '[]'::json) FROM (
    SELECT v.lead_band, v.who, s.area_kind, s.window_start, s.window_end,
           s.n_hours, s.n_days, s.scored, s.bias_mw, s.mae_mw,
           s.mae_pct_installed, s.rmse_mw, s.r, s.scored_at,
           s.lead_min, s.lead_max, s.hours_rule, s.actual_source, s.mw_yes, s.mw_unknown, s.mw_no
      FROM (VALUES
            ('h01_06', 'registry'),
            ('h01_06', 'calibrated'),
            ('h07_24', 'registry'),
            ('h07_24', 'calibrated'),
            ('h25_48', 'registry'),
            ('h25_48', 'calibrated'),
            ('h49_120', 'registry'),
            ('h49_120', 'calibrated'),
            ('h121_240', 'registry'),
            ('h121_240', 'calibrated'),
            ('dam_comparable', 'registry'),
            ('dam_comparable', 'calibrated'),
            ('dam_comparable', 'caiso_dam')
           ) AS v(lead_band, who)
     CROSS JOIN LATERAL (
        SELECT s.area_kind, s.window_start, s.window_end, s.n_hours, s.n_days,
               s.scored, s.bias_mw, s.mae_mw, s.mae_pct_installed, s.rmse_mw,
               s.r, s.scored_at, s.lead_min, s.lead_max,
               to_jsonb(s) -> 'hours_rule' AS hours_rule, s.actual_source, s.mw_yes, s.mw_unknown, s.mw_no
          FROM implied_gen_scores s
         WHERE s.tech = 'wind' AND s.area = 'SP15'
           AND s.lead_band = v.lead_band AND s.who = v.who
           AND s.area_kind = 'hub'
           AND s.method_version = 'wind_v1'
         ORDER BY s.window_end DESC
         LIMIT 1
     ) AS s
) t),
'search_solar_solar_star', (SELECT coalesce(json_agg(t), '[]'::json) FROM (
    SELECT plant_code, min(plant_name) AS plant_name, min(county) AS county,
           min(state) AS state, min(hub) AS hub, min(ba_code) AS ba_code,
           sum(ac_mw)::float8 AS mw, min(latitude) AS latitude,
           min(longitude) AS longitude,
           min(CASE WHEN NULL::int IS NOT NULL AND plant_code = NULL::int THEN 0
                WHEN plant_name ILIKE 'solar star%' ESCAPE '\' THEN 1
                WHEN plant_name ILIKE '%solar star%' ESCAPE '\' THEN 2
                ELSE 3 END) AS rank
      FROM implied_gen_sites
     WHERE tech = 'solar_pv'
       AND (plant_name ILIKE '%solar star%' ESCAPE '\' OR county ILIKE '%solar star%' ESCAPE '\'
            OR plant_code = NULL::int)
     GROUP BY plant_code
     ORDER BY rank, mw DESC NULLS LAST, plant_code
     LIMIT 20
) t),
'search_wind_solar_star', (SELECT coalesce(json_agg(t), '[]'::json) FROM (
    SELECT plant_code, plant_name, county, state, hub, ba_code,
           nameplate_mw::float8 AS mw, latitude, longitude,
           CASE WHEN NULL::int IS NOT NULL AND plant_code = NULL::int THEN 0
                WHEN plant_name ILIKE 'solar star%' ESCAPE '\' THEN 1
                WHEN plant_name ILIKE '%solar star%' ESCAPE '\' THEN 2
                ELSE 3 END AS rank
      FROM implied_gen_wind_sites
     WHERE plant_name ILIKE '%solar star%' ESCAPE '\' OR county ILIKE '%solar star%' ESCAPE '\'
           OR plant_code = NULL::int
     ORDER BY rank, mw DESC NULLS LAST, plant_code
     LIMIT 20
) t),
'search_solar_kern', (SELECT coalesce(json_agg(t), '[]'::json) FROM (
    SELECT plant_code, min(plant_name) AS plant_name, min(county) AS county,
           min(state) AS state, min(hub) AS hub, min(ba_code) AS ba_code,
           sum(ac_mw)::float8 AS mw, min(latitude) AS latitude,
           min(longitude) AS longitude,
           min(CASE WHEN NULL::int IS NOT NULL AND plant_code = NULL::int THEN 0
                WHEN plant_name ILIKE 'kern%' ESCAPE '\' THEN 1
                WHEN plant_name ILIKE '%kern%' ESCAPE '\' THEN 2
                ELSE 3 END) AS rank
      FROM implied_gen_sites
     WHERE tech = 'solar_pv'
       AND (plant_name ILIKE '%kern%' ESCAPE '\' OR county ILIKE '%kern%' ESCAPE '\'
            OR plant_code = NULL::int)
     GROUP BY plant_code
     ORDER BY rank, mw DESC NULLS LAST, plant_code
     LIMIT 20
) t),
'search_wind_kern', (SELECT coalesce(json_agg(t), '[]'::json) FROM (
    SELECT plant_code, plant_name, county, state, hub, ba_code,
           nameplate_mw::float8 AS mw, latitude, longitude,
           CASE WHEN NULL::int IS NOT NULL AND plant_code = NULL::int THEN 0
                WHEN plant_name ILIKE 'kern%' ESCAPE '\' THEN 1
                WHEN plant_name ILIKE '%kern%' ESCAPE '\' THEN 2
                ELSE 3 END AS rank
      FROM implied_gen_wind_sites
     WHERE plant_name ILIKE '%kern%' ESCAPE '\' OR county ILIKE '%kern%' ESCAPE '\'
           OR plant_code = NULL::int
     ORDER BY rank, mw DESC NULLS LAST, plant_code
     LIMIT 20
) t),
'search_solar_57514', (SELECT coalesce(json_agg(t), '[]'::json) FROM (
    SELECT plant_code, min(plant_name) AS plant_name, min(county) AS county,
           min(state) AS state, min(hub) AS hub, min(ba_code) AS ba_code,
           sum(ac_mw)::float8 AS mw, min(latitude) AS latitude,
           min(longitude) AS longitude,
           min(CASE WHEN 57514::int IS NOT NULL AND plant_code = 57514::int THEN 0
                WHEN plant_name ILIKE '57514%' ESCAPE '\' THEN 1
                WHEN plant_name ILIKE '%57514%' ESCAPE '\' THEN 2
                ELSE 3 END) AS rank
      FROM implied_gen_sites
     WHERE tech = 'solar_pv'
       AND (plant_name ILIKE '%57514%' ESCAPE '\' OR county ILIKE '%57514%' ESCAPE '\'
            OR plant_code = 57514::int)
     GROUP BY plant_code
     ORDER BY rank, mw DESC NULLS LAST, plant_code
     LIMIT 20
) t),
'search_wind_57514', (SELECT coalesce(json_agg(t), '[]'::json) FROM (
    SELECT plant_code, plant_name, county, state, hub, ba_code,
           nameplate_mw::float8 AS mw, latitude, longitude,
           CASE WHEN 57514::int IS NOT NULL AND plant_code = 57514::int THEN 0
                WHEN plant_name ILIKE '57514%' ESCAPE '\' THEN 1
                WHEN plant_name ILIKE '%57514%' ESCAPE '\' THEN 2
                ELSE 3 END AS rank
      FROM implied_gen_wind_sites
     WHERE plant_name ILIKE '%57514%' ESCAPE '\' OR county ILIKE '%57514%' ESCAPE '\'
           OR plant_code = 57514::int
     ORDER BY rank, mw DESC NULLS LAST, plant_code
     LIMIT 20
) t),
'search_solar_ocotillo', (SELECT coalesce(json_agg(t), '[]'::json) FROM (
    SELECT plant_code, min(plant_name) AS plant_name, min(county) AS county,
           min(state) AS state, min(hub) AS hub, min(ba_code) AS ba_code,
           sum(ac_mw)::float8 AS mw, min(latitude) AS latitude,
           min(longitude) AS longitude,
           min(CASE WHEN NULL::int IS NOT NULL AND plant_code = NULL::int THEN 0
                WHEN plant_name ILIKE 'ocotillo%' ESCAPE '\' THEN 1
                WHEN plant_name ILIKE '%ocotillo%' ESCAPE '\' THEN 2
                ELSE 3 END) AS rank
      FROM implied_gen_sites
     WHERE tech = 'solar_pv'
       AND (plant_name ILIKE '%ocotillo%' ESCAPE '\' OR county ILIKE '%ocotillo%' ESCAPE '\'
            OR plant_code = NULL::int)
     GROUP BY plant_code
     ORDER BY rank, mw DESC NULLS LAST, plant_code
     LIMIT 20
) t),
'search_wind_ocotillo', (SELECT coalesce(json_agg(t), '[]'::json) FROM (
    SELECT plant_code, plant_name, county, state, hub, ba_code,
           nameplate_mw::float8 AS mw, latitude, longitude,
           CASE WHEN NULL::int IS NOT NULL AND plant_code = NULL::int THEN 0
                WHEN plant_name ILIKE 'ocotillo%' ESCAPE '\' THEN 1
                WHEN plant_name ILIKE '%ocotillo%' ESCAPE '\' THEN 2
                ELSE 3 END AS rank
      FROM implied_gen_wind_sites
     WHERE plant_name ILIKE '%ocotillo%' ESCAPE '\' OR county ILIKE '%ocotillo%' ESCAPE '\'
           OR plant_code = NULL::int
     ORDER BY rank, mw DESC NULLS LAST, plant_code
     LIMIT 20
) t))::text AS bank