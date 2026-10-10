-- d091682: the Tropics statements before the move to the rows in force (git c40c231:tropics.py),
-- bound by psycopg's ClientCursor.mogrify with the parameters their Neon plans were taken with.

-- POLL_SQL
SELECT hb.fetch_ts AS newest_heartbeat_ts,
           d.stale_after_override,
           d.lifecycle_status
      FROM (SELECT 1) AS one
      LEFT JOIN LATERAL (
            SELECT fetch_ts
              FROM tropical_file_vintage
             WHERE product = 'heartbeat'
             ORDER BY fetch_ts DESC
             LIMIT 1) AS hb ON true
      LEFT JOIN datasets AS d ON d.dataset_code = 'nhc_storms_current';

-- STORMS_SQL {'scope': 'active', 'recent_days': 7}
SELECT s.storm_id, s.basin, s.number, s.season, s.status, s.names, s.aliases,
           s.first_seen, s.last_seen, s.last_lat, s.last_lon, s.updated_ts,
           bt.init_ts AS bt_init_ts, bt.valid_ts AS bt_valid_ts, bt.lat AS bt_lat,
           bt.lon AS bt_lon, bt.vmax_kt AS bt_vmax_kt, bt.mslp_hpa AS bt_mslp_hpa,
           bt.stage AS bt_stage,
           tv.init_ts AS tv_init_ts, tv.valid_ts AS tv_valid_ts, tv.lat AS tv_lat,
           tv.lon AS tv_lon, tv.vmax_kt AS tv_vmax_kt, tv.mslp_hpa AS tv_mslp_hpa,
           tv.stage AS tv_stage
      FROM tropical_storms AS s
      LEFT JOIN LATERAL (
            SELECT init_ts, valid_ts, lat, lon, vmax_kt, mslp_hpa, stage
              FROM tropical_track_points
             WHERE storm_id = s.storm_id AND source = 'best_track'
             ORDER BY init_ts DESC, advisory DESC, tau DESC
             LIMIT 1) AS bt ON true
      LEFT JOIN LATERAL (
            SELECT init_ts, valid_ts, lat, lon, vmax_kt, mslp_hpa, stage
              FROM tropical_track_points
             WHERE storm_id = s.storm_id AND source = 'tcvitals'
             ORDER BY init_ts DESC, advisory DESC, tau DESC
             LIMIT 1) AS tv ON true
     WHERE ('active' = 'all'
            OR ('active' = 'active' AND s.status <> 'inactive')
            OR ('active' = 'recent' AND s.last_seen >= now() - make_interval(days => 7)))
     ORDER BY s.storm_id;

-- OFFICIAL_NEWEST_SQL {'storm_ids': ['al092026', 'ep182026', 'ep202026']}
SELECT p.storm_id, p.advisory, p.init_ts, p.tau, p.valid_ts, p.lat, p.lon,
           p.vmax_kt, p.mslp_hpa, p.radii, p.stage
      FROM unnest('{al092026,ep182026,ep202026}'::text[]) AS u(storm_id)
      CROSS JOIN LATERAL (
            SELECT init_ts
              FROM tropical_track_points
             WHERE storm_id = u.storm_id AND source = 'nhc_official'
             ORDER BY init_ts DESC
             LIMIT 1) AS m
      JOIN tropical_track_points AS p
        ON p.storm_id = u.storm_id AND p.source = 'nhc_official' AND p.init_ts = m.init_ts
     ORDER BY p.storm_id, p.advisory, p.tau;

-- STORM_SQL {'storm_id': 'ep182026'}
SELECT storm_id, basin, number, season, status, names, aliases,
           first_seen, last_seen, last_lat, last_lon, updated_ts
      FROM tropical_storms
     WHERE storm_id = 'ep182026';

-- OBSERVED_SQL {'storm_id': 'ep182026'}
SELECT source, init_ts, advisory, tau, valid_ts, lat, lon, vmax_kt, mslp_hpa, radii, stage
      FROM tropical_track_points
     WHERE storm_id = 'ep182026' AND source IN ('best_track', 'tcvitals')
     ORDER BY source, valid_ts, init_ts, tau;

-- CYCLES_SQL {'storm_id': 'ep182026'}
WITH RECURSIVE cyc AS (
        (SELECT init_ts
           FROM tropical_track_points
          WHERE storm_id = 'ep182026'
            AND source NOT IN ('best_track', 'tcvitals', 'nhc_official')
          ORDER BY init_ts DESC
          LIMIT 1)
        UNION ALL
        SELECT (SELECT t.init_ts
                  FROM tropical_track_points AS t
                 WHERE t.storm_id = 'ep182026'
                   AND t.source NOT IN ('best_track', 'tcvitals', 'nhc_official')
                   AND t.init_ts < cyc.init_ts
                 ORDER BY t.init_ts DESC
                 LIMIT 1)
          FROM cyc
         WHERE cyc.init_ts IS NOT NULL
    )
    SELECT c.init_ts, g.source, g.n_points, g.max_tau
      FROM cyc AS c
     CROSS JOIN LATERAL (
            SELECT source, count(*) AS n_points, max(tau) AS max_tau
              FROM tropical_track_points
             WHERE storm_id = 'ep182026' AND init_ts = c.init_ts
               AND source NOT IN ('best_track', 'tcvitals', 'nhc_official')
             GROUP BY source) AS g
     WHERE c.init_ts IS NOT NULL
     ORDER BY c.init_ts, g.source;

-- TRACKS_SQL n=8 {'storm_id': 'ep182026', 'n': 8}
WITH RECURSIVE cyc AS (
        (SELECT init_ts
           FROM tropical_track_points
          WHERE storm_id = 'ep182026'
            AND source NOT IN ('best_track', 'tcvitals', 'nhc_official')
          ORDER BY init_ts DESC
          LIMIT 1)
        UNION ALL
        SELECT (SELECT t.init_ts
                  FROM tropical_track_points AS t
                 WHERE t.storm_id = 'ep182026'
                   AND t.source NOT IN ('best_track', 'tcvitals', 'nhc_official')
                   AND t.init_ts < cyc.init_ts
                 ORDER BY t.init_ts DESC
                 LIMIT 1)
          FROM cyc
         WHERE cyc.init_ts IS NOT NULL
    ), newest AS (
        SELECT init_ts FROM cyc WHERE init_ts IS NOT NULL LIMIT 8
    )
    SELECT p.source, p.init_ts, p.advisory, p.tau, p.valid_ts, p.lat, p.lon,
           p.vmax_kt, p.mslp_hpa, p.stage
      FROM tropical_track_points AS p
     WHERE p.storm_id = 'ep182026'
       AND p.init_ts = ANY (ARRAY(SELECT init_ts FROM newest))
       AND p.source NOT IN ('best_track', 'tcvitals')
     ORDER BY p.init_ts, p.source, p.advisory, p.tau;

-- TRACKS_SQL n=4 {'storm_id': 'ep182026', 'n': 4}
WITH RECURSIVE cyc AS (
        (SELECT init_ts
           FROM tropical_track_points
          WHERE storm_id = 'ep182026'
            AND source NOT IN ('best_track', 'tcvitals', 'nhc_official')
          ORDER BY init_ts DESC
          LIMIT 1)
        UNION ALL
        SELECT (SELECT t.init_ts
                  FROM tropical_track_points AS t
                 WHERE t.storm_id = 'ep182026'
                   AND t.source NOT IN ('best_track', 'tcvitals', 'nhc_official')
                   AND t.init_ts < cyc.init_ts
                 ORDER BY t.init_ts DESC
                 LIMIT 1)
          FROM cyc
         WHERE cyc.init_ts IS NOT NULL
    ), newest AS (
        SELECT init_ts FROM cyc WHERE init_ts IS NOT NULL LIMIT 4
    )
    SELECT p.source, p.init_ts, p.advisory, p.tau, p.valid_ts, p.lat, p.lon,
           p.vmax_kt, p.mslp_hpa, p.stage
      FROM tropical_track_points AS p
     WHERE p.storm_id = 'ep182026'
       AND p.init_ts = ANY (ARRAY(SELECT init_ts FROM newest))
       AND p.source NOT IN ('best_track', 'tcvitals')
     ORDER BY p.init_ts, p.source, p.advisory, p.tau;

-- ODDS_SQL {'storm_id': 'al092026'}
SELECT o.issued_ts, o.advisory, o.place_id, o.threshold_kt, o.radius_km, o.window_h,
           o.kind, o.value, o.below_1pct, o.scored, o.odds_id
      FROM (SELECT issued_ts
              FROM tropical_place_odds
             WHERE storm_id = 'al092026' AND source = 'nhc_pws'
             ORDER BY issued_ts DESC
             LIMIT 1) AS m
      JOIN tropical_place_odds AS o
        ON o.storm_id = 'al092026' AND o.source = 'nhc_pws' AND o.issued_ts = m.issued_ts
     ORDER BY o.odds_id;
