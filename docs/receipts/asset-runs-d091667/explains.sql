-- d091667: every statement run on Neon (fancy-block-96153928) through the
-- connector, 2026-10-08 20:32-21:00Z. Read-only: SELECT, EXPLAIN of a SELECT,
-- PREPARE/EXECUTE of a SELECT. Nothing was written.

-- ── Measurements ────────────────────────────────────────────────────────────

-- partitions and their bounds
SELECT now(), c.relname, c.relkind, pg_get_expr(c.relpartbound, c.oid) AS bound,
       pg_get_partkeydef(c.oid) AS partkey, c.reltuples::bigint, pg_total_relation_size(c.oid)
  FROM pg_class c WHERE c.relname LIKE 'implied_gen_site_history%' AND c.relkind IN ('p','r') ORDER BY 1;

-- the ledger, whole (also read at 20:43:13Z for the bank, and at 21:00:45Z with lag)
SELECT now(), tech, model, init_ts, landed_at,
       round(extract(epoch FROM landed_at - init_ts) / 3600, 2) AS lag_h, n_plants
  FROM implied_gen_site_history_runs ORDER BY tech, init_ts;

-- per run x weather_source: rows, plants, leads, drivers present
SELECT tech, model, init_ts, weather_source, count(*), count(DISTINCT plant_code), min(lead_h), max(lead_h),
       min(target_ts), max(target_ts), count(*) FILTER (WHERE ghi_wm2 IS NOT NULL),
       count(*) FILTER (WHERE hub_ws_ms IS NOT NULL), count(*) FILTER (WHERE gust_ms IS NOT NULL),
       count(*) FILTER (WHERE cap_mw_subtracted IS NULL), count(DISTINCT method_version)
  FROM implied_gen_site_history GROUP BY 1,2,3,4 ORDER BY 1,2,3,4;

-- every distinct value of each basis column and equipment_source, with counts
SELECT col, val, n FROM (
  SELECT 'turbine_model_basis' col, turbine_model_basis val, count(*) n FROM implied_gen_wind_sites GROUP BY 2
  UNION ALL SELECT 'n_turbines_basis', n_turbines_basis, count(*) FROM implied_gen_wind_sites GROUP BY 2
  UNION ALL SELECT 'rotor_basis', rotor_basis, count(*) FROM implied_gen_wind_sites GROUP BY 2
  UNION ALL SELECT 'hub_height_basis', hub_height_basis, count(*) FROM implied_gen_wind_sites GROUP BY 2
  UNION ALL SELECT 'curve_basis', curve_basis, count(*) FROM implied_gen_wind_sites GROUP BY 2
  UNION ALL SELECT 'equipment_source', equipment_source, count(*) FROM implied_gen_wind_sites GROUP BY 2) x
 ORDER BY col, n DESC, val;

-- what each plant has
SELECT count(*), count(*) FILTER (WHERE rotor_m IS NOT NULL), count(*) FILTER (WHERE hub_height_m IS NOT NULL),
       count(*) FILTER (WHERE n_turbines IS NOT NULL), count(*) FILTER (WHERE turbine_model IS NOT NULL),
       count(*) FILTER (WHERE curve_turbine_type IS NOT NULL),
       count(*) FILTER (WHERE cut_in_ms IS NOT NULL AND rated_ms IS NOT NULL AND cut_out_ms IS NOT NULL),
       count(*) FILTER (WHERE rotor_m IS NOT NULL AND cut_in_ms IS NOT NULL AND rated_ms IS NOT NULL AND cut_out_ms IS NOT NULL),
       count(*) FILTER (WHERE turbine_model ~ '[;|+,&]' OR turbine_model ILIKE '% and %' OR turbine_model ~ '/.*/')
  FROM implied_gen_wind_sites;

-- USWTDB rotors that are blends (more than one t_rd at the plant's EIA id)
WITH t AS (SELECT eia_id, count(DISTINCT t_rd) AS n_rd, count(DISTINCT t_model) AS n_model
             FROM atlas_wind_turbines WHERE eia_id IS NOT NULL GROUP BY eia_id)
SELECT count(*) FILTER (WHERE s.rotor_basis = 'uswtdb'),
       count(*) FILTER (WHERE s.rotor_basis = 'uswtdb' AND t.n_rd > 1),
       count(*) FILTER (WHERE s.rotor_basis = 'uswtdb' AND t.n_model > 1)
  FROM implied_gen_wind_sites s LEFT JOIN t ON t.eia_id::text = s.plant_code::text;

-- ── The bank (bank_from_neon.py) ────────────────────────────────────────────

-- history, one row per run, as arrays, with Neon's md5 of the canonical text
-- (wind 66923 shown; solar 58388 the same with the five solar drivers)
SELECT init_ts, count(*) AS n,
       count(*) FILTER (WHERE target_ts <> init_ts + (lead_h - 1) * interval '1 hour') AS off_ladder,
       min(method_version) AS mv, max(method_version) AS mv2,
       array_agg(lead_h ORDER BY target_ts) AS lead_h, array_agg(weather_source ORDER BY target_ts) AS weather_source,
       array_agg(implied_mw ORDER BY target_ts) AS implied_mw, array_agg(outage_mw_subtracted ORDER BY target_ts) AS outage,
       array_agg(cap_mw_subtracted ORDER BY target_ts) AS cap, array_agg(hub_ws_ms ORDER BY target_ts) AS hub_ws_ms,
       array_agg(gust_ms ORDER BY target_ts) AS gust_ms,
       md5(string_agg(concat_ws('|', lead_h::text, coalesce(weather_source,''), implied_mw::text,
                                outage_mw_subtracted::text, coalesce(cap_mw_subtracted::text,''), method_version,
                                coalesce(hub_ws_ms::text,''), coalesce(gust_ms::text,'')), E'\n' ORDER BY target_ts)) AS md5
  FROM implied_gen_site_history WHERE tech = 'wind' AND plant_code = 66923 AND model = 'hrrr_gfs'
 GROUP BY init_ts ORDER BY init_ts;

-- the site table: json_agg of the asset route's columns + equipment_source, all 323 rows;
-- and SITES_MD5_SQL:
SELECT count(*), md5(string_agg(concat_ws('|', s.plant_code::text, coalesce(s.plant_name,''), coalesce(s.turbine_model,''),
       coalesce(s.turbine_model_basis,''), coalesce(s.n_turbines::text,''), coalesce(s.n_turbines_basis,''),
       coalesce(s.rotor_m::float8::text,''), coalesce(s.rotor_basis,''), coalesce(s.hub_height_m::float8::text,''),
       coalesce(s.hub_height_basis,''), coalesce(s.curve_turbine_type,''), coalesce(s.curve_hub_height_m::float8::text,''),
       coalesce(s.curve_basis,''), coalesce(s.cut_in_ms::float8::text,''), coalesce(s.rated_ms::float8::text,''),
       coalesce(s.cut_out_ms::float8::text,''), coalesce(s.equipment_source,''), coalesce(s.nameplate_mw::float8::text,''),
       coalesce(s.hub,'')), E'\n' ORDER BY s.plant_code))
  FROM implied_gen_wind_sites s;

-- ── Plans (plans.md) ────────────────────────────────────────────────────────
-- 1. the obvious LEFT JOIN on init_ts; the plain LATERAL; = ANY(ARRAY(SELECT ...)) (all rejected)
-- 2. asset_page.runs_sql('wind') and ('solar') with literals (n = 8; and n = 1);
--    and under a generic plan:
--      SET LOCAL plan_cache_mode = force_generic_plan;
--      PREPARE d091667_runs(text, text, int, int, int) AS <runs_sql with $1..$5>;
--      EXPLAIN (ANALYZE, BUFFERS, COSTS OFF) EXECUTE d091667_runs('wind', 'hrrr_gfs', 66923, 8, 8);
--      DEALLOCATE d091667_runs;
