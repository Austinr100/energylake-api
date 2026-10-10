-- pantry migrations/298_cpc_curves_short_base.sql at e71fa23, sections 1-4
-- verbatim (the helpers, both ALTERs with their CHECKs and comments, the view).
-- Not the pre-checks, the post-checks or the ledger row. Applied on Neon
-- 2026-10-10 13:40:22Z. Loaded by tests/load_bank_d091691.py.

-- 1. the helpers -------------------------------------------------------------
-- base_by_station is [{station, base_years}, ...]; threaded_base is
-- [{station, predecessor, years: [int, ...]}, ...]. Pure functions of their
-- arguments, so a CHECK may call them.
CREATE FUNCTION cpc_base_min(by_station jsonb) RETURNS integer
LANGUAGE sql IMMUTABLE STRICT AS $fn$
    SELECT min((e ->> 'base_years')::int) FROM jsonb_array_elements(by_station) e
$fn$;

CREATE FUNCTION cpc_base_fewest(by_station jsonb) RETURNS text[]
LANGUAGE sql IMMUTABLE STRICT AS $fn$
    SELECT array_agg(e ->> 'station' ORDER BY e ->> 'station' COLLATE "C")
      FROM jsonb_array_elements(by_station) e
     WHERE (e ->> 'base_years')::int = (SELECT min((x ->> 'base_years')::int)
                                         FROM jsonb_array_elements(by_station) x)
$fn$;

CREATE FUNCTION cpc_base_shape_ok(by_station jsonb, threaded jsonb) RETURNS boolean
LANGUAGE sql IMMUTABLE STRICT AS $fn$
    SELECT jsonb_typeof(by_station) = 'array' AND jsonb_array_length(by_station) >= 1
       AND jsonb_typeof(threaded) = 'array'
       -- every member: a station id (no blank), a count 0..30, each named once
       AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(by_station) e
                        WHERE jsonb_typeof(e) <> 'object'
                           OR coalesce(e ->> 'station', '') !~ '^\S+$'
                           OR jsonb_typeof(e -> 'base_years') <> 'number'
                           OR (e ->> 'base_years')::numeric NOT BETWEEN 0 AND 30
                           OR (e ->> 'base_years')::numeric <> trunc((e ->> 'base_years')::numeric))
       AND (SELECT count(DISTINCT e ->> 'station') FROM jsonb_array_elements(by_station) e)
           = jsonb_array_length(by_station)
       -- every thread: a member, a GHCN-shaped predecessor that is not itself,
       -- and 1..(its base years) distinct counted years inside 1991..2020
       AND NOT EXISTS (
           SELECT 1 FROM jsonb_array_elements(threaded) t
            WHERE jsonb_typeof(t) <> 'object'
               OR coalesce(t ->> 'predecessor', '') !~ '^[A-Z]{2}[A-Z0-9]{9}$'
               OR t ->> 'predecessor' = t ->> 'station'
               OR jsonb_typeof(t -> 'years') <> 'array'
               OR jsonb_array_length(t -> 'years') < 1
               OR NOT EXISTS (SELECT 1 FROM jsonb_array_elements(by_station) e
                               WHERE e ->> 'station' = t ->> 'station'
                                 AND (e ->> 'base_years')::int >= jsonb_array_length(t -> 'years'))
               OR EXISTS (SELECT 1 FROM jsonb_array_elements(t -> 'years') y
                           WHERE jsonb_typeof(y) <> 'number'
                              OR (y #>> '{}')::numeric NOT BETWEEN 1991 AND 2020)
               OR (SELECT count(DISTINCT y #>> '{}') FROM jsonb_array_elements(t -> 'years') y)
                  <> jsonb_array_length(t -> 'years'))
       AND (SELECT count(DISTINCT t ->> 'station') FROM jsonb_array_elements(threaded) t)
           = jsonb_array_length(threaded)
$fn$;

-- 2. cpc_curve_verdicts ------------------------------------------------------
ALTER TABLE cpc_curve_verdicts
    ADD COLUMN min_base_years       INTEGER NOT NULL DEFAULT 30,  -- the floor this row was judged at
    ADD COLUMN base_fewest_stations TEXT[],     -- the member(s) holding the place's count
    ADD COLUMN base_by_station      JSONB,      -- [{station, base_years}] every member
    ADD COLUMN threaded_base        JSONB,      -- [{station, predecessor, years}]; [] when none
    ADD COLUMN base_reason          TEXT;       -- why not drawn, with the number, under the floor

ALTER TABLE cpc_curve_verdicts DROP CONSTRAINT ccv_drawable_ck;
-- CLAUSES 3 + 4 + 5, D-09-25-177: drawable exactly when the season cell beats
-- equal odds, the place has the row's floor of base years, and the band has a
-- judged claim. Every v1 row's floor is 30, so its truth is 286's.
ALTER TABLE cpc_curve_verdicts ADD CONSTRAINT ccv_drawable_ck CHECK (
    drawable = (verdict = 'beats' AND history_years_in_base >= min_base_years
                AND band_claim IS NOT NULL));

ALTER TABLE cpc_curve_verdicts ADD CONSTRAINT ccv_base_ck CHECK (
    min_base_years BETWEEN 22 AND 30
    AND (method_version = 'cpc_curves_v1') = (base_by_station IS NULL)
    AND (base_by_station IS NULL) = (base_fewest_stations IS NULL)
    AND (base_by_station IS NULL) = (threaded_base IS NULL)
    AND (base_by_station IS NOT NULL OR (min_base_years = 30 AND base_reason IS NULL))
    AND (base_by_station IS NULL OR (
        cpc_base_shape_ok(base_by_station, threaded_base)
        AND history_years_in_base = cpc_base_min(base_by_station)
        AND base_fewest_stations = cpc_base_fewest(base_by_station)
        AND (place_kind <> 'station' OR (jsonb_array_length(base_by_station) = 1
                                         AND base_by_station -> 0 ->> 'station' = place))
        -- (5) under the floor: not drawn, and says why with its number
        AND (history_years_in_base < min_base_years) = (base_reason IS NOT NULL)
        AND (base_reason IS NULL
             OR strpos(base_reason, 'on ' || history_years_in_base || ' base years') > 0)
        -- nothing borrowed from a 30-year neighbour
        AND (band_basis <> 'pooled_stations' OR history_years_in_base >= 30)
        -- (2) the sentence states the count exactly when it is under 30
        AND (band_sentence IS NULL OR (
            (history_years_in_base < 30) = (strpos(band_sentence, ' base years, not CPC''s 30') > 0)
            AND (history_years_in_base >= 30
                 OR strpos(band_sentence, 'on ' || history_years_in_base
                                          || ' base years, not CPC''s 30') > 0))))));

COMMENT ON COLUMN cpc_curve_verdicts.min_base_years IS
    'D-09-25-177 (5): the base-year floor this row was judged at. 30 on every cpc_curves_v1 row (D-09-25-172); 22 from cpc_curves_v2: the shortest base measured, not a ruled floor. ccv_drawable_ck reads it.';
COMMENT ON COLUMN cpc_curve_verdicts.base_by_station IS
    'D-09-25-177 (2): [{station, base_years}] for every member (a station: itself). history_years_in_base is their minimum, by CHECK. NULL on v1 rows.';
COMMENT ON COLUMN cpc_curve_verdicts.base_fewest_stations IS
    'D-09-25-177 (2): the member(s) holding the fewest base years, sorted; every one of them when several tie. NULL on v1 rows.';
COMMENT ON COLUMN cpc_curve_verdicts.threaded_base IS
    'D-09-25-177 (4): [{station, predecessor, years}] — the counted base years holding a day threaded from a declared predecessor (station_degree_days_daily.threaded_from, migration 295); [] when none. NULL on v1 rows.';
COMMENT ON COLUMN cpc_curve_verdicts.base_reason IS
    'D-09-25-177 (5): why a place under min_base_years has no curve, carrying its number. NULL at or above the floor, and on v1 rows.';

-- 3. cpc_outlook_curves ------------------------------------------------------
ALTER TABLE cpc_outlook_curves
    ADD COLUMN base_years           INTEGER,    -- the place's count (a region: the fewest)
    ADD COLUMN base_fewest_stations TEXT[],
    ADD COLUMN base_by_station      JSONB,
    ADD COLUMN threaded_base        JSONB;

ALTER TABLE cpc_outlook_curves ADD CONSTRAINT coc_base_ck CHECK (
    (method_version = 'cpc_curves_v1') = (base_years IS NULL)
    AND (base_years IS NULL) = (base_by_station IS NULL)
    AND (base_years IS NULL) = (base_fewest_stations IS NULL)
    AND (base_years IS NULL) = (threaded_base IS NULL)
    AND (base_years IS NULL OR (
        base_years BETWEEN 22 AND 30          -- (5): no curve under the shortest base measured
        AND cpc_base_shape_ok(base_by_station, threaded_base)
        AND base_years = cpc_base_min(base_by_station)
        AND base_fewest_stations = cpc_base_fewest(base_by_station)
        AND (place_kind <> 'station' OR (jsonb_array_length(base_by_station) = 1
                                         AND base_by_station -> 0 ->> 'station' = place)))));

COMMENT ON COLUMN cpc_outlook_curves.base_years IS
    'D-09-25-177 (2): the base years (1991-2020, >= 330 complete days) behind this curve''s classes; a region: the fewest of its members. 22..30. NULL on cpc_curves_v1 rows (all at 30, D-09-25-172 (5)).';
COMMENT ON COLUMN cpc_outlook_curves.base_by_station IS
    'D-09-25-177 (2): [{station, base_years}] for every member. NULL on v1 rows.';
COMMENT ON COLUMN cpc_outlook_curves.base_fewest_stations IS
    'D-09-25-177 (2): the member(s) holding base_years. NULL on v1 rows.';
COMMENT ON COLUMN cpc_outlook_curves.threaded_base IS
    'D-09-25-177 (4): [{station, predecessor, years}] threaded base years; [] when none. NULL on v1 rows.';

-- 4. the only sanctioned read ------------------------------------------------
-- Same columns, same order, same join; the five count columns appended; the
-- label states the count under 30. A v1 row (base_years NULL) reads as before.
CREATE OR REPLACE VIEW v_cpc_curves_drawable AS
WITH newest AS (
    SELECT DISTINCT ON (method_hash) method_hash, backtest_version
      FROM cpc_curve_verdicts
     ORDER BY method_hash, scored_at DESC, backtest_version DESC
)
SELECT c.product, c.issued_date, c.valid_start, c.valid_end,
       c.place_kind, c.place, c.weighting, c.day_index, c.target_date,
       c.tavg_p05, c.tavg_p25, c.tavg_p50, c.tavg_p75, c.tavg_p95,
       c.hdd_p05, c.hdd_p25, c.hdd_p50, c.hdd_p75, c.hdd_p95,
       c.cdd_p05, c.cdd_p25, c.cdd_p50, c.cdd_p75, c.cdd_p95,
       c.eq_tavg_p05, c.eq_tavg_p50, c.eq_tavg_p95,
       c.eq_hdd_p05, c.eq_hdd_p50, c.eq_hdd_p95,
       c.eq_cdd_p05, c.eq_cdd_p50, c.eq_cdd_p95,
       c.n_history_years, c.history_first, c.history_last, c.empty_class,
       c.member_odds, c.season, c.strength, c.method_version, c.method_hash,
       c.source_content_sha256,
       'History at ' || c.place
           || CASE WHEN c.place_kind = 'region' THEN ' (' || c.weighting || ')' ELSE '' END
           || ', ' || c.history_first || '-' || c.history_last
           || ', reweighted by NOAA CPC''s published '
           || CASE c.product WHEN '610temp' THEN '6-10' ELSE '8-14' END
           || ' day odds'
           || CASE WHEN c.base_years < 30
                   THEN ', on ' || c.base_years || ' base years, not CPC''s 30' ELSE '' END
           AS label,
       v.band_claim, v.band_share, v.band_sentence, v.band_basis,
       v.backtest_version, v.truth_frontier AS verdict_truth_frontier,
       v.season_n AS verdict_n, v.season_n_eff AS verdict_n_eff,
       v.season_skill AS verdict_skill, v.season_t AS verdict_t,
       v.n AS strength_n, v.n_eff AS strength_n_eff,
       v.history_years_in_base,
       c.base_years, c.base_fewest_stations, c.base_by_station, c.threaded_base,
       v.min_base_years
  FROM cpc_outlook_curves c
  JOIN newest nw ON nw.method_hash = c.method_hash
  JOIN cpc_curve_verdicts v
    ON v.backtest_version = nw.backtest_version AND v.method_hash = c.method_hash
   AND v.product = c.product AND v.place_kind = c.place_kind AND v.place = c.place
   AND v.weighting = c.weighting AND v.season = c.season AND v.strength = c.strength
 WHERE v.drawable;

COMMENT ON VIEW v_cpc_curves_drawable IS
    'D-09-25-172 + D-09-25-177: the CPC curve rows that may be drawn — the cell''s verdict in the NEWEST backtest_version of the curve''s method_hash says drawable (clause 3 beats, the row''s floor of base years — 30 for cpc_curves_v1, 22 from v2 — and a judged band claim). Every row carries band_claim, band_share, band_sentence, the fixed label (which says "on N base years, not CPC''s 30" under 30) and, from v2, the base count: base_years, base_fewest_stations, base_by_station, threaded_base, min_base_years. A curve whose cell has no verdict in that version is not returned. Each method_version is its own vintage: a reader picks one (the handback names v2 once it is scored and written). Nothing downstream reads cpc_outlook_curves or cpc_curve_verdicts directly. Beside the blend (v_dd_blend_drawable), never in its place.';

