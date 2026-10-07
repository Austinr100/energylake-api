-- G01 | solar_pv ba/CISO n=4 first
EXPLAIN (ANALYZE, BUFFERS)
WITH RECURSIVE inits AS (
        (SELECT i.init_ts, 1 AS k
           FROM implied_gen_area_hourly i
          WHERE i.tech = 'solar_pv' AND i.area_kind = 'ba'
           AND i.area = 'CISO' AND i.model = 'gfs'
          ORDER BY i.init_ts DESC
          LIMIT 1)
        UNION ALL
        SELECT (SELECT p.init_ts
                  FROM implied_gen_area_hourly p
                 WHERE p.tech = 'solar_pv' AND p.area_kind = 'ba'
           AND p.area = 'CISO' AND p.model = 'gfs'
                   AND p.init_ts < w.init_ts
                 ORDER BY p.init_ts DESC
                 LIMIT 1), w.k + 1
          FROM inits w
         WHERE w.k < 4 AND w.init_ts IS NOT NULL
    )
    SELECT h.init_ts, h.target_ts, h.lead_h, h.registry_mw, h.calibrated_mw,
           h.calibration_id, h.method_version
      FROM inits w
      JOIN implied_gen_area_hourly h
        ON h.tech = 'solar_pv' AND h.area_kind = 'ba'
           AND h.area = 'CISO' AND h.model = 'gfs'
       AND h.init_ts = w.init_ts
     ORDER BY h.init_ts, h.target_ts;

-- G02 | solar_pv ba/CISO n=4 warm
EXPLAIN (ANALYZE, BUFFERS)
WITH RECURSIVE inits AS (
        (SELECT i.init_ts, 1 AS k
           FROM implied_gen_area_hourly i
          WHERE i.tech = 'solar_pv' AND i.area_kind = 'ba'
           AND i.area = 'CISO' AND i.model = 'gfs'
          ORDER BY i.init_ts DESC
          LIMIT 1)
        UNION ALL
        SELECT (SELECT p.init_ts
                  FROM implied_gen_area_hourly p
                 WHERE p.tech = 'solar_pv' AND p.area_kind = 'ba'
           AND p.area = 'CISO' AND p.model = 'gfs'
                   AND p.init_ts < w.init_ts
                 ORDER BY p.init_ts DESC
                 LIMIT 1), w.k + 1
          FROM inits w
         WHERE w.k < 4 AND w.init_ts IS NOT NULL
    )
    SELECT h.init_ts, h.target_ts, h.lead_h, h.registry_mw, h.calibrated_mw,
           h.calibration_id, h.method_version
      FROM inits w
      JOIN implied_gen_area_hourly h
        ON h.tech = 'solar_pv' AND h.area_kind = 'ba'
           AND h.area = 'CISO' AND h.model = 'gfs'
       AND h.init_ts = w.init_ts
     ORDER BY h.init_ts, h.target_ts;

-- G03 | solar_pv ba/CISO n=12 first
EXPLAIN (ANALYZE, BUFFERS)
WITH RECURSIVE inits AS (
        (SELECT i.init_ts, 1 AS k
           FROM implied_gen_area_hourly i
          WHERE i.tech = 'solar_pv' AND i.area_kind = 'ba'
           AND i.area = 'CISO' AND i.model = 'gfs'
          ORDER BY i.init_ts DESC
          LIMIT 1)
        UNION ALL
        SELECT (SELECT p.init_ts
                  FROM implied_gen_area_hourly p
                 WHERE p.tech = 'solar_pv' AND p.area_kind = 'ba'
           AND p.area = 'CISO' AND p.model = 'gfs'
                   AND p.init_ts < w.init_ts
                 ORDER BY p.init_ts DESC
                 LIMIT 1), w.k + 1
          FROM inits w
         WHERE w.k < 12 AND w.init_ts IS NOT NULL
    )
    SELECT h.init_ts, h.target_ts, h.lead_h, h.registry_mw, h.calibrated_mw,
           h.calibration_id, h.method_version
      FROM inits w
      JOIN implied_gen_area_hourly h
        ON h.tech = 'solar_pv' AND h.area_kind = 'ba'
           AND h.area = 'CISO' AND h.model = 'gfs'
       AND h.init_ts = w.init_ts
     ORDER BY h.init_ts, h.target_ts;

-- G04 | solar_pv ba/CISO n=12 warm
EXPLAIN (ANALYZE, BUFFERS)
WITH RECURSIVE inits AS (
        (SELECT i.init_ts, 1 AS k
           FROM implied_gen_area_hourly i
          WHERE i.tech = 'solar_pv' AND i.area_kind = 'ba'
           AND i.area = 'CISO' AND i.model = 'gfs'
          ORDER BY i.init_ts DESC
          LIMIT 1)
        UNION ALL
        SELECT (SELECT p.init_ts
                  FROM implied_gen_area_hourly p
                 WHERE p.tech = 'solar_pv' AND p.area_kind = 'ba'
           AND p.area = 'CISO' AND p.model = 'gfs'
                   AND p.init_ts < w.init_ts
                 ORDER BY p.init_ts DESC
                 LIMIT 1), w.k + 1
          FROM inits w
         WHERE w.k < 12 AND w.init_ts IS NOT NULL
    )
    SELECT h.init_ts, h.target_ts, h.lead_h, h.registry_mw, h.calibrated_mw,
           h.calibration_id, h.method_version
      FROM inits w
      JOIN implied_gen_area_hourly h
        ON h.tech = 'solar_pv' AND h.area_kind = 'ba'
           AND h.area = 'CISO' AND h.model = 'gfs'
       AND h.init_ts = w.init_ts
     ORDER BY h.init_ts, h.target_ts;

-- G05 | solar_pv ba/CISO n=28 first
EXPLAIN (ANALYZE, BUFFERS)
WITH RECURSIVE inits AS (
        (SELECT i.init_ts, 1 AS k
           FROM implied_gen_area_hourly i
          WHERE i.tech = 'solar_pv' AND i.area_kind = 'ba'
           AND i.area = 'CISO' AND i.model = 'gfs'
          ORDER BY i.init_ts DESC
          LIMIT 1)
        UNION ALL
        SELECT (SELECT p.init_ts
                  FROM implied_gen_area_hourly p
                 WHERE p.tech = 'solar_pv' AND p.area_kind = 'ba'
           AND p.area = 'CISO' AND p.model = 'gfs'
                   AND p.init_ts < w.init_ts
                 ORDER BY p.init_ts DESC
                 LIMIT 1), w.k + 1
          FROM inits w
         WHERE w.k < 28 AND w.init_ts IS NOT NULL
    )
    SELECT h.init_ts, h.target_ts, h.lead_h, h.registry_mw, h.calibrated_mw,
           h.calibration_id, h.method_version
      FROM inits w
      JOIN implied_gen_area_hourly h
        ON h.tech = 'solar_pv' AND h.area_kind = 'ba'
           AND h.area = 'CISO' AND h.model = 'gfs'
       AND h.init_ts = w.init_ts
     ORDER BY h.init_ts, h.target_ts;

-- G06 | solar_pv ba/CISO n=28 warm
EXPLAIN (ANALYZE, BUFFERS)
WITH RECURSIVE inits AS (
        (SELECT i.init_ts, 1 AS k
           FROM implied_gen_area_hourly i
          WHERE i.tech = 'solar_pv' AND i.area_kind = 'ba'
           AND i.area = 'CISO' AND i.model = 'gfs'
          ORDER BY i.init_ts DESC
          LIMIT 1)
        UNION ALL
        SELECT (SELECT p.init_ts
                  FROM implied_gen_area_hourly p
                 WHERE p.tech = 'solar_pv' AND p.area_kind = 'ba'
           AND p.area = 'CISO' AND p.model = 'gfs'
                   AND p.init_ts < w.init_ts
                 ORDER BY p.init_ts DESC
                 LIMIT 1), w.k + 1
          FROM inits w
         WHERE w.k < 28 AND w.init_ts IS NOT NULL
    )
    SELECT h.init_ts, h.target_ts, h.lead_h, h.registry_mw, h.calibrated_mw,
           h.calibration_id, h.method_version
      FROM inits w
      JOIN implied_gen_area_hourly h
        ON h.tech = 'solar_pv' AND h.area_kind = 'ba'
           AND h.area = 'CISO' AND h.model = 'gfs'
       AND h.init_ts = w.init_ts
     ORDER BY h.init_ts, h.target_ts;

-- G07 | wind ba/CISO n=4 first
EXPLAIN (ANALYZE, BUFFERS)
WITH RECURSIVE inits AS (
        (SELECT i.init_ts, 1 AS k
           FROM implied_gen_area_hourly i
          WHERE i.tech = 'wind' AND i.area_kind = 'ba'
           AND i.area = 'CISO' AND i.model = 'hrrr_gfs'
          ORDER BY i.init_ts DESC
          LIMIT 1)
        UNION ALL
        SELECT (SELECT p.init_ts
                  FROM implied_gen_area_hourly p
                 WHERE p.tech = 'wind' AND p.area_kind = 'ba'
           AND p.area = 'CISO' AND p.model = 'hrrr_gfs'
                   AND p.init_ts < w.init_ts
                 ORDER BY p.init_ts DESC
                 LIMIT 1), w.k + 1
          FROM inits w
         WHERE w.k < 4 AND w.init_ts IS NOT NULL
    )
    SELECT h.init_ts, h.target_ts, h.lead_h, h.registry_mw, h.calibrated_mw,
           h.calibration_id, h.method_version
      FROM inits w
      JOIN implied_gen_area_hourly h
        ON h.tech = 'wind' AND h.area_kind = 'ba'
           AND h.area = 'CISO' AND h.model = 'hrrr_gfs'
       AND h.init_ts = w.init_ts
     ORDER BY h.init_ts, h.target_ts;

-- G08 | wind ba/CISO n=4 warm
EXPLAIN (ANALYZE, BUFFERS)
WITH RECURSIVE inits AS (
        (SELECT i.init_ts, 1 AS k
           FROM implied_gen_area_hourly i
          WHERE i.tech = 'wind' AND i.area_kind = 'ba'
           AND i.area = 'CISO' AND i.model = 'hrrr_gfs'
          ORDER BY i.init_ts DESC
          LIMIT 1)
        UNION ALL
        SELECT (SELECT p.init_ts
                  FROM implied_gen_area_hourly p
                 WHERE p.tech = 'wind' AND p.area_kind = 'ba'
           AND p.area = 'CISO' AND p.model = 'hrrr_gfs'
                   AND p.init_ts < w.init_ts
                 ORDER BY p.init_ts DESC
                 LIMIT 1), w.k + 1
          FROM inits w
         WHERE w.k < 4 AND w.init_ts IS NOT NULL
    )
    SELECT h.init_ts, h.target_ts, h.lead_h, h.registry_mw, h.calibrated_mw,
           h.calibration_id, h.method_version
      FROM inits w
      JOIN implied_gen_area_hourly h
        ON h.tech = 'wind' AND h.area_kind = 'ba'
           AND h.area = 'CISO' AND h.model = 'hrrr_gfs'
       AND h.init_ts = w.init_ts
     ORDER BY h.init_ts, h.target_ts;

-- G09 | wind ba/CISO n=12 first
EXPLAIN (ANALYZE, BUFFERS)
WITH RECURSIVE inits AS (
        (SELECT i.init_ts, 1 AS k
           FROM implied_gen_area_hourly i
          WHERE i.tech = 'wind' AND i.area_kind = 'ba'
           AND i.area = 'CISO' AND i.model = 'hrrr_gfs'
          ORDER BY i.init_ts DESC
          LIMIT 1)
        UNION ALL
        SELECT (SELECT p.init_ts
                  FROM implied_gen_area_hourly p
                 WHERE p.tech = 'wind' AND p.area_kind = 'ba'
           AND p.area = 'CISO' AND p.model = 'hrrr_gfs'
                   AND p.init_ts < w.init_ts
                 ORDER BY p.init_ts DESC
                 LIMIT 1), w.k + 1
          FROM inits w
         WHERE w.k < 12 AND w.init_ts IS NOT NULL
    )
    SELECT h.init_ts, h.target_ts, h.lead_h, h.registry_mw, h.calibrated_mw,
           h.calibration_id, h.method_version
      FROM inits w
      JOIN implied_gen_area_hourly h
        ON h.tech = 'wind' AND h.area_kind = 'ba'
           AND h.area = 'CISO' AND h.model = 'hrrr_gfs'
       AND h.init_ts = w.init_ts
     ORDER BY h.init_ts, h.target_ts;

-- G10 | wind ba/CISO n=12 warm
EXPLAIN (ANALYZE, BUFFERS)
WITH RECURSIVE inits AS (
        (SELECT i.init_ts, 1 AS k
           FROM implied_gen_area_hourly i
          WHERE i.tech = 'wind' AND i.area_kind = 'ba'
           AND i.area = 'CISO' AND i.model = 'hrrr_gfs'
          ORDER BY i.init_ts DESC
          LIMIT 1)
        UNION ALL
        SELECT (SELECT p.init_ts
                  FROM implied_gen_area_hourly p
                 WHERE p.tech = 'wind' AND p.area_kind = 'ba'
           AND p.area = 'CISO' AND p.model = 'hrrr_gfs'
                   AND p.init_ts < w.init_ts
                 ORDER BY p.init_ts DESC
                 LIMIT 1), w.k + 1
          FROM inits w
         WHERE w.k < 12 AND w.init_ts IS NOT NULL
    )
    SELECT h.init_ts, h.target_ts, h.lead_h, h.registry_mw, h.calibrated_mw,
           h.calibration_id, h.method_version
      FROM inits w
      JOIN implied_gen_area_hourly h
        ON h.tech = 'wind' AND h.area_kind = 'ba'
           AND h.area = 'CISO' AND h.model = 'hrrr_gfs'
       AND h.init_ts = w.init_ts
     ORDER BY h.init_ts, h.target_ts;

-- G11 | wind ba/CISO n=28 first
EXPLAIN (ANALYZE, BUFFERS)
WITH RECURSIVE inits AS (
        (SELECT i.init_ts, 1 AS k
           FROM implied_gen_area_hourly i
          WHERE i.tech = 'wind' AND i.area_kind = 'ba'
           AND i.area = 'CISO' AND i.model = 'hrrr_gfs'
          ORDER BY i.init_ts DESC
          LIMIT 1)
        UNION ALL
        SELECT (SELECT p.init_ts
                  FROM implied_gen_area_hourly p
                 WHERE p.tech = 'wind' AND p.area_kind = 'ba'
           AND p.area = 'CISO' AND p.model = 'hrrr_gfs'
                   AND p.init_ts < w.init_ts
                 ORDER BY p.init_ts DESC
                 LIMIT 1), w.k + 1
          FROM inits w
         WHERE w.k < 28 AND w.init_ts IS NOT NULL
    )
    SELECT h.init_ts, h.target_ts, h.lead_h, h.registry_mw, h.calibrated_mw,
           h.calibration_id, h.method_version
      FROM inits w
      JOIN implied_gen_area_hourly h
        ON h.tech = 'wind' AND h.area_kind = 'ba'
           AND h.area = 'CISO' AND h.model = 'hrrr_gfs'
       AND h.init_ts = w.init_ts
     ORDER BY h.init_ts, h.target_ts;

-- G12 | wind ba/CISO n=28 warm
EXPLAIN (ANALYZE, BUFFERS)
WITH RECURSIVE inits AS (
        (SELECT i.init_ts, 1 AS k
           FROM implied_gen_area_hourly i
          WHERE i.tech = 'wind' AND i.area_kind = 'ba'
           AND i.area = 'CISO' AND i.model = 'hrrr_gfs'
          ORDER BY i.init_ts DESC
          LIMIT 1)
        UNION ALL
        SELECT (SELECT p.init_ts
                  FROM implied_gen_area_hourly p
                 WHERE p.tech = 'wind' AND p.area_kind = 'ba'
           AND p.area = 'CISO' AND p.model = 'hrrr_gfs'
                   AND p.init_ts < w.init_ts
                 ORDER BY p.init_ts DESC
                 LIMIT 1), w.k + 1
          FROM inits w
         WHERE w.k < 28 AND w.init_ts IS NOT NULL
    )
    SELECT h.init_ts, h.target_ts, h.lead_h, h.registry_mw, h.calibrated_mw,
           h.calibration_id, h.method_version
      FROM inits w
      JOIN implied_gen_area_hourly h
        ON h.tech = 'wind' AND h.area_kind = 'ba'
           AND h.area = 'CISO' AND h.model = 'hrrr_gfs'
       AND h.init_ts = w.init_ts
     ORDER BY h.init_ts, h.target_ts;

-- G13 | solar_pv ba/AZPS n=28 first (cold proxy)
EXPLAIN (ANALYZE, BUFFERS)
WITH RECURSIVE inits AS (
        (SELECT i.init_ts, 1 AS k
           FROM implied_gen_area_hourly i
          WHERE i.tech = 'solar_pv' AND i.area_kind = 'ba'
           AND i.area = 'AZPS' AND i.model = 'gfs'
          ORDER BY i.init_ts DESC
          LIMIT 1)
        UNION ALL
        SELECT (SELECT p.init_ts
                  FROM implied_gen_area_hourly p
                 WHERE p.tech = 'solar_pv' AND p.area_kind = 'ba'
           AND p.area = 'AZPS' AND p.model = 'gfs'
                   AND p.init_ts < w.init_ts
                 ORDER BY p.init_ts DESC
                 LIMIT 1), w.k + 1
          FROM inits w
         WHERE w.k < 28 AND w.init_ts IS NOT NULL
    )
    SELECT h.init_ts, h.target_ts, h.lead_h, h.registry_mw, h.calibrated_mw,
           h.calibration_id, h.method_version
      FROM inits w
      JOIN implied_gen_area_hourly h
        ON h.tech = 'solar_pv' AND h.area_kind = 'ba'
           AND h.area = 'AZPS' AND h.model = 'gfs'
       AND h.init_ts = w.init_ts
     ORDER BY h.init_ts, h.target_ts;

-- G14 | solar_pv ba/AZPS n=28 warm (cold proxy)
EXPLAIN (ANALYZE, BUFFERS)
WITH RECURSIVE inits AS (
        (SELECT i.init_ts, 1 AS k
           FROM implied_gen_area_hourly i
          WHERE i.tech = 'solar_pv' AND i.area_kind = 'ba'
           AND i.area = 'AZPS' AND i.model = 'gfs'
          ORDER BY i.init_ts DESC
          LIMIT 1)
        UNION ALL
        SELECT (SELECT p.init_ts
                  FROM implied_gen_area_hourly p
                 WHERE p.tech = 'solar_pv' AND p.area_kind = 'ba'
           AND p.area = 'AZPS' AND p.model = 'gfs'
                   AND p.init_ts < w.init_ts
                 ORDER BY p.init_ts DESC
                 LIMIT 1), w.k + 1
          FROM inits w
         WHERE w.k < 28 AND w.init_ts IS NOT NULL
    )
    SELECT h.init_ts, h.target_ts, h.lead_h, h.registry_mw, h.calibrated_mw,
           h.calibration_id, h.method_version
      FROM inits w
      JOIN implied_gen_area_hourly h
        ON h.tech = 'solar_pv' AND h.area_kind = 'ba'
           AND h.area = 'AZPS' AND h.model = 'gfs'
       AND h.init_ts = w.init_ts
     ORDER BY h.init_ts, h.target_ts;

-- G15 | solar_pv CISO lines for the n=28 ids first (CALIBRATION_SQL, the gate's lines)
EXPLAIN (ANALYZE, BUFFERS)
SELECT calibration_id, area, lead_band, intercept_mw, slope,
           fit_start, fit_end, n_hours, n_days, fitted_at, method_version,
           fit_lead_min, fit_lead_max, fit_rows
      FROM implied_gen_calibration
     WHERE calibration_id = ANY(ARRAY[386,387,388,389,407,408,409,717,718,719,720,753,754,755,773,774,775,776]::bigint[])
       AND tech = 'solar_pv' AND area = 'CISO';

-- G16 | solar_pv CISO lines for the n=28 ids warm (CALIBRATION_SQL, the gate's lines)
EXPLAIN (ANALYZE, BUFFERS)
SELECT calibration_id, area, lead_band, intercept_mw, slope,
           fit_start, fit_end, n_hours, n_days, fitted_at, method_version,
           fit_lead_min, fit_lead_max, fit_rows
      FROM implied_gen_calibration
     WHERE calibration_id = ANY(ARRAY[386,387,388,389,407,408,409,717,718,719,720,753,754,755,773,774,775,776]::bigint[])
       AND tech = 'solar_pv' AND area = 'CISO';

-- G17 | wind CISO lines for the n=28 ids first (CALIBRATION_SQL, the gate's lines)
EXPLAIN (ANALYZE, BUFFERS)
SELECT calibration_id, area, lead_band, intercept_mw, slope,
           fit_start, fit_end, n_hours, n_days, fitted_at, method_version,
           fit_lead_min, fit_lead_max, fit_rows
      FROM implied_gen_calibration
     WHERE calibration_id = ANY(ARRAY[677,678,683,684,733,734,736,789,790,791]::bigint[])
       AND tech = 'wind' AND area = 'CISO';

-- G18 | wind CISO lines for the n=28 ids warm (CALIBRATION_SQL, the gate's lines)
EXPLAIN (ANALYZE, BUFFERS)
SELECT calibration_id, area, lead_band, intercept_mw, slope,
           fit_start, fit_end, n_hours, n_days, fitted_at, method_version,
           fit_lead_min, fit_lead_max, fit_rows
      FROM implied_gen_calibration
     WHERE calibration_id = ANY(ARRAY[677,678,683,684,733,734,736,789,790,791]::bigint[])
       AND tech = 'wind' AND area = 'CISO';

-- D19 | pnw/population GFS first (every held issuance)
EXPLAIN (ANALYZE, BUFFERS)
SELECT issued_ts, target_date, hdd_wtd AS hdd, cdd_wtd AS cdd,
           basis_complete, sample_spacing_hours
      FROM v_degree_days_region_forecast
     WHERE region = 'pnw' AND weighting = 'population'
       AND source_product = 'GFS'
     ORDER BY issued_ts, target_date;

-- D20 | pnw/population GFS warm (every held issuance)
EXPLAIN (ANALYZE, BUFFERS)
SELECT issued_ts, target_date, hdd_wtd AS hdd, cdd_wtd AS cdd,
           basis_complete, sample_spacing_hours
      FROM v_degree_days_region_forecast
     WHERE region = 'pnw' AND weighting = 'population'
       AND source_product = 'GFS'
     ORDER BY issued_ts, target_date;

-- D21 | pnw/population IFS first (every held issuance)
EXPLAIN (ANALYZE, BUFFERS)
SELECT issued_ts, target_date, hdd_wtd AS hdd, cdd_wtd AS cdd,
           basis_complete, sample_spacing_hours
      FROM v_degree_days_region_forecast
     WHERE region = 'pnw' AND weighting = 'population'
       AND source_product = 'IFS'
     ORDER BY issued_ts, target_date;

-- D22 | pnw/population IFS warm (every held issuance)
EXPLAIN (ANALYZE, BUFFERS)
SELECT issued_ts, target_date, hdd_wtd AS hdd, cdd_wtd AS cdd,
           basis_complete, sample_spacing_hours
      FROM v_degree_days_region_forecast
     WHERE region = 'pnw' AND weighting = 'population'
       AND source_product = 'IFS'
     ORDER BY issued_ts, target_date;

-- D23 | pnw/population AIFS first (every held issuance)
EXPLAIN (ANALYZE, BUFFERS)
SELECT issued_ts, target_date, hdd_wtd AS hdd, cdd_wtd AS cdd,
           basis_complete, sample_spacing_hours
      FROM v_degree_days_region_forecast
     WHERE region = 'pnw' AND weighting = 'population'
       AND source_product = 'AIFS'
     ORDER BY issued_ts, target_date;

-- D24 | pnw/population AIFS warm (every held issuance)
EXPLAIN (ANALYZE, BUFFERS)
SELECT issued_ts, target_date, hdd_wtd AS hdd, cdd_wtd AS cdd,
           basis_complete, sample_spacing_hours
      FROM v_degree_days_region_forecast
     WHERE region = 'pnw' AND weighting = 'population'
       AND source_product = 'AIFS'
     ORDER BY issued_ts, target_date;

-- D25 | pnw/population gridpoints_raw first (every held issuance)
EXPLAIN (ANALYZE, BUFFERS)
SELECT issued_ts, target_date, hdd_wtd AS hdd, cdd_wtd AS cdd,
           basis_complete, sample_spacing_hours
      FROM v_degree_days_region_forecast
     WHERE region = 'pnw' AND weighting = 'population'
       AND source_product = 'gridpoints_raw'
     ORDER BY issued_ts, target_date;

-- D26 | pnw/population gridpoints_raw warm (every held issuance)
EXPLAIN (ANALYZE, BUFFERS)
SELECT issued_ts, target_date, hdd_wtd AS hdd, cdd_wtd AS cdd,
           basis_complete, sample_spacing_hours
      FROM v_degree_days_region_forecast
     WHERE region = 'pnw' AND weighting = 'population'
       AND source_product = 'gridpoints_raw'
     ORDER BY issued_ts, target_date;
