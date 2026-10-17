-- 292_ar_ivt_bank_shadow.sql          lane d091665 · atmospheric rivers, in shadow
--
-- *** NOT APPLIED BY THE BUILD. Surfaced for explicit captain approval. ***
-- *** Prepared and rehearsed on an ephemeral Postgres 16 built from the  ***
-- *** LIVE definitions of every object it touches, dumped from Neon      ***
-- *** 2026-10-08 (scripts/rehearse_migration_292.sh). Both apply orders ***
-- *** (migration first, YAML sync first) and a second apply (refused).  ***
--
-- Spec: lane d091665 brief (2026-10-08, architect). Recon: d091658,
-- docs/recon_2026_10_08_atmospheric_rivers.md ("Draft for the first build").
-- Handback: docs/handback_2026_10_08_atmospheric_rivers_ivt_bank.md.
-- Writers: scripts/ar_gefs_points.py, scripts/ar_truth_era5.py,
--          scripts/ar_backtest.py, scripts/ar_score.py (package ar/).
--
-- DECLARED NUMBER: 292, reserved for this lane by the brief. Ledger read live
-- 2026-10-08: max(version) = 289; 288, 290 and 291 are not on it (other lanes).
-- This file requires 289 and nothing after it: it touches no object 290/291
-- are known to touch. It does NOT touch ingestion_freshness or
-- model_cycle_liveness (both read `datasets`); post-check (4) proves every
-- other dataset's row in both views is unchanged.
--
-- WHAT THIS DOES
--   1. ar_runs — the per-cycle ledger, PK (init_ts, level_set). CHECKs: the
--      level set is a versioned key and is IN every object key (rule 1); only
--      00Z/12Z cycles (rule 8: 06Z/18Z are not added by this lane); a failed
--      cycle names no object; shas are sha256 hex.
--   2. ar_landfall_latest — the newest cycle's odds, replaced per cycle.
--      The 25-of-31 floor is IN THE SCHEMA (n_present 25..31, rule 3), and the
--      absent members are named (cardinality = 31 - n_present). The
--      tropical-trace pair (tc_named_system, tc_rule_version) is nullable and
--      all-or-nothing (rule 7); nothing in this lane fills it.
--   3. ar_scores — one row per scored (region, lead bucket, threshold, k-bin)
--      plus an 'all' row carrying the show decision. The SHOW RULE is in the
--      schema: a row can say show = true only with n_eff >= 60, event_days_eff
--      >= 14, bss_lo90 > 0 and no failed clause (rule 6, ar/score.py
--      SHOW_RULE 'show_v1'); truth_level_set = level_set (like-for-like).
--   4. datasets += ar_ivt_gefs_points, ar_landfall_odds_gefs,
--      ar_ivt_truth_era5 — BORN PLANNED (rule 5), freshness_mode 'data', no
--      ruler, no `cycle_registry` (so model_cycle_liveness never grades them).
--      INSERT ... ON CONFLICT DO NOTHING, then an explicit UPDATE of the
--      migration-owned columns: registry-sync never writes lifecycle_status
--      and its column default is 'active', so if the YAML sync lands first
--      this repairs it (the 270 / 289 pattern; both orders rehearsed).
--      No freshness arm, no firing-catalog row: they arrive with promotion.
--   5. The ledger row. A SECOND APPLY IS REFUSED by the pre-check on 292.
--
-- REVERSAL (only while the tables are empty):
--   DROP TABLE ar_landfall_latest; DROP TABLE ar_scores; DROP TABLE ar_runs;
--   DELETE FROM datasets WHERE dataset_code IN
--     ('ar_ivt_gefs_points', 'ar_landfall_odds_gefs', 'ar_ivt_truth_era5');
--   (and remove the three datasets.yaml blocks, or sync re-births them ACTIVE)
--   DELETE FROM schema_migrations WHERE version = 292;

BEGIN;

-- 0. pre-checks ----------------------------------------------------------------
DO $pre$
BEGIN
    PERFORM 1 FROM schema_migrations WHERE version = 292;
    IF FOUND THEN RAISE EXCEPTION 'REFUSED: schema_migrations already holds 292 — 292 is applied; a second apply is refused'; END IF;
    PERFORM 1 FROM schema_migrations WHERE version = 289;
    IF NOT FOUND THEN RAISE EXCEPTION 'PRECONDITION: schema_migrations lacks 289 — apply in order'; END IF;
    IF to_regclass('public.ar_runs') IS NOT NULL OR to_regclass('public.ar_landfall_latest') IS NOT NULL
       OR to_regclass('public.ar_scores') IS NOT NULL THEN
        RAISE EXCEPTION 'PRECONDITION: an ar_* table already exists — read it before applying';
    END IF;
    PERFORM 1 FROM sources WHERE source_code = 'l4';
    IF NOT FOUND THEN RAISE EXCEPTION 'PRECONDITION: no sources row for l4'; END IF;
    PERFORM 1 FROM pg_class WHERE oid = 'ingestion_freshness'::regclass AND relkind = 'v';
    IF NOT FOUND THEN RAISE EXCEPTION 'PRECONDITION: ingestion_freshness is not a view'; END IF;
    PERFORM 1 FROM datasets
     WHERE dataset_code IN ('ar_ivt_gefs_points', 'ar_landfall_odds_gefs', 'ar_ivt_truth_era5')
       AND lifecycle_status = 'retired';
    IF FOUND THEN RAISE EXCEPTION 'PRECONDITION: an ar_* dataset is retired — a retirement is not undone by a migration'; END IF;
    RAISE NOTICE 'PRECONDITION OK';
END $pre$;

CREATE TEMP TABLE _m292_fresh ON COMMIT DROP AS
    SELECT dataset_code, refresh_cron, freshness_mode, lifecycle_status, frontier_source,
           frontier_ts, stale_after, status
      FROM ingestion_freshness
     WHERE dataset_code NOT IN ('ar_ivt_gefs_points', 'ar_landfall_odds_gefs', 'ar_ivt_truth_era5');
CREATE TEMP TABLE _m292_mcl ON COMMIT DROP AS SELECT * FROM model_cycle_liveness;
CREATE TEMP TABLE _m292_defs ON COMMIT DROP AS
    SELECT md5(pg_get_viewdef('ingestion_freshness'::regclass, true)) AS f,
           md5(pg_get_viewdef('model_cycle_liveness'::regclass, true)) AS m,
           (SELECT count(*) FROM datasets) AS n_datasets;

-- 1. ar_runs ---------------------------------------------------------------------
CREATE TABLE ar_runs (
    init_ts           timestamptz NOT NULL,
    level_set         text        NOT NULL CHECK (level_set ~ '^[a-z0-9]+_v[0-9]+$'),
    members_expected  smallint    NOT NULL DEFAULT 31 CHECK (members_expected = 31),
    members_present   smallint    NOT NULL,
    steps_expected    smallint    NOT NULL CHECK (steps_expected > 0),
    steps_present     smallint    NOT NULL,
    steps_with_odds   smallint    NOT NULL,
    missing           jsonb       NOT NULL DEFAULT '{}'::jsonb,
    bytes_pulled      bigint      NOT NULL CHECK (bytes_pulled >= 0),
    requests          integer,
    retries           integer,
    rechecks          integer,
    wall_s            numeric(8,1),
    status            text        NOT NULL CHECK (status IN ('complete', 'incomplete', 'failed')),
    points_key        text,
    odds_key          text,
    manifest_key      text,
    sha256            text        CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    odds_sha256       text        CHECK (odds_sha256 ~ '^[0-9a-f]{64}$'),
    point_list_sha    text        NOT NULL CHECK (point_list_sha ~ '^[0-9a-f]{64}$'),
    mask_sha          text        NOT NULL CHECK (mask_sha ~ '^[0-9a-f]{64}$'),
    method_hash       text        NOT NULL,
    written_at        timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (init_ts, level_set),
    CONSTRAINT ar_runs_cycle_00_12 CHECK (
        date_trunc('hour', init_ts) = init_ts
        AND extract(hour FROM init_ts AT TIME ZONE 'UTC') IN (0, 12)),
    CONSTRAINT ar_runs_counts CHECK (
        members_present BETWEEN 0 AND members_expected
        AND steps_present BETWEEN 0 AND steps_expected
        AND steps_with_odds BETWEEN 0 AND steps_present),
    CONSTRAINT ar_runs_level_set_in_keys CHECK (
        status = 'failed'
        OR (points_key LIKE '%.' || level_set || '.parquet'
            AND odds_key LIKE '%.' || level_set || '.parquet'
            AND manifest_key LIKE '%.' || level_set || '.json')),
    CONSTRAINT ar_runs_failed_writes_nothing CHECK (
        status <> 'failed' OR (points_key IS NULL AND odds_key IS NULL AND manifest_key IS NULL))
);
COMMENT ON TABLE ar_runs IS
    'd091665 (migration 292): one row per GEFS cycle x level set of the AR member-IVT point bank '
    '(scripts/ar_gefs_points.py). Shadow: born with ar_ivt_gefs_points / ar_landfall_odds_gefs planned.';

-- 2. ar_landfall_latest ------------------------------------------------------------
CREATE TABLE ar_landfall_latest (
    init_ts          timestamptz NOT NULL,
    level_set        text        NOT NULL,
    valid_ts         timestamptz NOT NULL,
    lead_h           smallint    NOT NULL CHECK (lead_h BETWEEN 0 AND 384),
    point_code       text        NOT NULL CHECK (point_code ~ '^coast_[0-9]{2}\.[05]N$'),
    lat              numeric(4,1) NOT NULL CHECK (lat BETWEEN 25.0 AND 60.0),
    lon              numeric(5,1) NOT NULL CHECK (lon BETWEEN -180.0 AND 180.0),
    n250             smallint    NOT NULL,
    n500             smallint    NOT NULL,
    n_present        smallint    NOT NULL,
    ens_mean         real        NOT NULL,
    ctrl             real,
    max_member       real        NOT NULL,
    absent_members   text[]      NOT NULL DEFAULT '{}'::text[],
    tc_named_system  text,
    tc_rule_version  text,
    PRIMARY KEY (level_set, point_code, valid_ts),
    FOREIGN KEY (init_ts, level_set) REFERENCES ar_runs (init_ts, level_set),
    CONSTRAINT ar_latest_floor CHECK (n_present BETWEEN 25 AND 31),
    CONSTRAINT ar_latest_counts CHECK (n500 >= 0 AND n500 <= n250 AND n250 <= n_present),
    CONSTRAINT ar_latest_absent_named CHECK (cardinality(absent_members) = 31 - n_present),
    CONSTRAINT ar_latest_valid CHECK (valid_ts = init_ts + make_interval(hours => lead_h::int)),
    CONSTRAINT ar_latest_tc_pair CHECK ((tc_named_system IS NULL) = (tc_rule_version IS NULL))
);
COMMENT ON TABLE ar_landfall_latest IS
    'd091665 (migration 292): the newest cycle''s landfall odds per coastal 0.5-deg latitude and step, '
    'replaced per cycle. Counts of members >= 250 / >= 500 of n_present (>= 25 by CHECK). Shadow: '
    'nothing reads this table until a cell passes ar_scores.';
COMMENT ON COLUMN ar_landfall_latest.tc_named_system IS
    'NULL until lane d091664''s tropical bank exists. Rule (proposed, ar/odds.py TC_TRACE_RULE): trace the '
    'mean-IVT >= 250 region containing the coast cell back 72 h; a named system within 500 km of its maximum names it.';

-- 3. ar_scores ---------------------------------------------------------------------
CREATE TABLE ar_scores (
    scored_at        timestamptz NOT NULL DEFAULT now(),
    scorer_hash      text        NOT NULL,
    show_rule        text        NOT NULL,
    level_set        text        NOT NULL,
    truth_level_set  text        NOT NULL,
    region           text        NOT NULL CHECK (region IN ('baja_socal', 'central_norcal', 'or_wa', 'bc', 'se_alaska')),
    lead_bucket      text        NOT NULL CHECK (lead_bucket IN ('d1-2', 'd3-5', 'd6-8', 'd9-12', 'd13-16')),
    threshold        smallint    NOT NULL CHECK (threshold IN (250, 500)),
    k_bin            text        NOT NULL CHECK (k_bin IN ('0', '1-3', '4-9', '10-15', '16-21', '22-27', '28-31', 'all')),
    n                integer     NOT NULL CHECK (n > 0),
    n_eff            numeric     NOT NULL CHECK (n_eff > 0),
    mean_fcst        numeric,
    obs_freq         numeric,
    wilson_lo        numeric,
    wilson_hi        numeric,
    brier            numeric,
    bss              numeric,
    bss_lo90         numeric,
    bss_hi90         numeric,
    n_days           integer,
    event_days       integer,
    event_days_eff   numeric,
    r1               numeric,
    show             boolean,
    show_reasons     text[],
    excluded         jsonb,
    PRIMARY KEY (scored_at, scorer_hash, level_set, region, lead_bucket, threshold, k_bin),
    CONSTRAINT ar_scores_like_for_like CHECK (truth_level_set = level_set),
    CONSTRAINT ar_scores_decision_on_all CHECK ((k_bin = 'all') = (show IS NOT NULL)),
    CONSTRAINT ar_scores_show_rule_v1 CHECK (
        show IS NOT TRUE
        OR (show_rule = 'show_v1' AND n_eff >= 60 AND event_days_eff >= 14
            AND bss_lo90 > 0 AND cardinality(show_reasons) = 0))
);
COMMENT ON TABLE ar_scores IS
    'd091665 (migration 292): reliability and Brier skill of the AR odds by region x lead bucket x threshold x '
    'k-bin, against like-for-like ERA5 (scripts/ar_score.py). show = true is possible only under show_v1 '
    '(stated before any score): n_eff >= 60 days, >= 14 effective event days, BSS 90% lower bound > 0, calibrated bins.';

-- 4. the datasets, born planned ----------------------------------------------------
INSERT INTO datasets
    (dataset_code, dataset_name, source_code, description, granularity,
     value_type, unit, ingester, refresh_cron, backfill_start, series_list,
     config, enabled, freshness_mode, lifecycle_status, stale_after_override)
VALUES
    ('ar_ivt_gefs_points',
     'L4 — GEFS member IVT at the AR points (sub7_v1)', 'l4',
     'Born planned (migration 292, d091665). Canonical prose lives in datasets.yaml; registry-sync supersedes this text.',
     'irregular', 'ivt', 'kg m-1 s-1', 'ar.gefs_points', 'manual', DATE '2026-10-08',
     ARRAY[]::text[],
     '{"model": "gefs", "cycles": [0, 12], "level_set": "sub7_v1", "members_expected": 31, "steps": 93, "ledger": "ar_runs"}'::jsonb,
     TRUE, 'data', 'planned', NULL),
    ('ar_landfall_odds_gefs',
     'L4 — GEFS AR landfall odds (members >= 250 / 500 per coastal latitude)', 'l4',
     'Born planned (migration 292, d091665). Canonical prose lives in datasets.yaml; registry-sync supersedes this text.',
     'irregular', 'count', 'members', 'ar.landfall_odds', 'manual', DATE '2026-10-08',
     ARRAY[]::text[],
     '{"model": "gefs", "cycles": [0, 12], "level_set": "sub7_v1", "min_members": 25, "latest_table": "ar_landfall_latest"}'::jsonb,
     TRUE, 'data', 'planned', NULL),
    ('ar_ivt_truth_era5',
     'L4 — ERA5 IVT at the AR points, like-for-like (sub7_v1) + column integral', 'l4',
     'Born planned (migration 292, d091665). Canonical prose lives in datasets.yaml; registry-sync supersedes this text.',
     'daily', 'ivt', 'kg m-1 s-1 | mm', 'ar.truth_era5', 'manual', DATE '2020-10-02',
     ARRAY[]::text[],
     '{"source": "arco-era5", "level_set": "sub7_v1", "hours": [0, 6, 12, 18], "lag_days": 6}'::jsonb,
     TRUE, 'data', 'planned', NULL)
ON CONFLICT (dataset_code) DO NOTHING;

UPDATE datasets
   SET lifecycle_status = 'planned', freshness_mode = 'data', stale_after_override = NULL
 WHERE dataset_code IN ('ar_ivt_gefs_points', 'ar_landfall_odds_gefs', 'ar_ivt_truth_era5');

-- 5. post-checks --------------------------------------------------------------------
DO $post$
DECLARE
    v_n bigint;
    v_bit int := 0;
    v_pl text := repeat('a', 64);
BEGIN
    -- (1) three planned datasets, none graded by model_cycle_liveness
    SELECT count(*) INTO v_n FROM datasets
     WHERE dataset_code IN ('ar_ivt_gefs_points', 'ar_landfall_odds_gefs', 'ar_ivt_truth_era5')
       AND lifecycle_status = 'planned' AND freshness_mode = 'data'
       AND stale_after_override IS NULL AND source_code = 'l4' AND enabled
       AND (config ->> 'cycle_registry') IS NULL;
    IF v_n <> 3 THEN RAISE EXCEPTION 'POST-CHECK (1): % of 3 ar datasets planned/data/l4/no registry', v_n; END IF;
    SELECT count(*) INTO v_n FROM model_cycle_liveness WHERE dataset_code LIKE 'ar\_%';
    IF v_n <> 0 THEN RAISE EXCEPTION 'POST-CHECK (1): model_cycle_liveness grades % ar row(s)', v_n; END IF;

    -- (2) the three read as planned with no frontier on the board
    SELECT count(*) INTO v_n FROM ingestion_freshness
     WHERE dataset_code IN ('ar_ivt_gefs_points', 'ar_landfall_odds_gefs', 'ar_ivt_truth_era5')
       AND lifecycle_status = 'planned' AND frontier_ts IS NULL;
    IF v_n <> 3 THEN RAISE EXCEPTION 'POST-CHECK (2): % of 3 ar datasets on the board as planned/no frontier', v_n; END IF;

    -- (3) the two views' text is untouched; one row per new dataset was added
    IF (SELECT f FROM _m292_defs) <> md5(pg_get_viewdef('ingestion_freshness'::regclass, true))
       OR (SELECT m FROM _m292_defs) <> md5(pg_get_viewdef('model_cycle_liveness'::regclass, true)) THEN
        RAISE EXCEPTION 'POST-CHECK (3): a view definition changed';
    END IF;

    -- (4) every other dataset's board row and every liveness row unchanged
    SELECT count(*) INTO v_n FROM (
        (SELECT * FROM _m292_fresh
         EXCEPT
         SELECT dataset_code, refresh_cron, freshness_mode, lifecycle_status, frontier_source,
                frontier_ts, stale_after, status FROM ingestion_freshness)
        UNION ALL
        (SELECT dataset_code, refresh_cron, freshness_mode, lifecycle_status, frontier_source,
                frontier_ts, stale_after, status FROM ingestion_freshness
          WHERE dataset_code NOT IN ('ar_ivt_gefs_points', 'ar_landfall_odds_gefs', 'ar_ivt_truth_era5')
         EXCEPT
         SELECT * FROM _m292_fresh)) d;
    IF v_n <> 0 THEN RAISE EXCEPTION 'POST-CHECK (4): % other board row(s) changed', v_n; END IF;
    SELECT count(*) INTO v_n FROM (
        (SELECT * FROM _m292_mcl EXCEPT SELECT * FROM model_cycle_liveness)
        UNION ALL (SELECT * FROM model_cycle_liveness EXCEPT SELECT * FROM _m292_mcl)) d;
    IF v_n <> 0 THEN RAISE EXCEPTION 'POST-CHECK (4): % model_cycle_liveness row(s) changed', v_n; END IF;

    -- (5) BITES: each guard refuses what it exists to refuse (probes rolled back)
    INSERT INTO ar_runs (init_ts, level_set, members_present, steps_expected, steps_present,
                         steps_with_odds, bytes_pulled, status, points_key, odds_key, manifest_key,
                         point_list_sha, mask_sha, method_hash)
    VALUES ('1900-01-01 00:00Z', 'sub7_v1', 31, 93, 93, 93, 0, 'complete',
            'x/points_ivt.sub7_v1.parquet', 'x/odds.sub7_v1.parquet', 'x/_manifest.sub7_v1.json',
            v_pl, v_pl, 'bite');
    BEGIN  -- 06Z (rule 8)
        INSERT INTO ar_runs (init_ts, level_set, members_present, steps_expected, steps_present,
                             steps_with_odds, bytes_pulled, status, point_list_sha, mask_sha, method_hash)
        VALUES ('1900-01-01 06:00Z', 'sub7_v1', 0, 93, 0, 0, 0, 'failed', v_pl, v_pl, 'bite');
    EXCEPTION WHEN check_violation THEN v_bit := v_bit + 1; END;
    BEGIN  -- a key without its level set (rule 1)
        INSERT INTO ar_runs (init_ts, level_set, members_present, steps_expected, steps_present,
                             steps_with_odds, bytes_pulled, status, points_key, odds_key, manifest_key,
                             point_list_sha, mask_sha, method_hash)
        VALUES ('1900-01-01 12:00Z', 'sub7_v1', 31, 93, 93, 93, 0, 'complete',
                'x/points_ivt.parquet', 'x/odds.sub7_v1.parquet', 'x/_manifest.sub7_v1.json',
                v_pl, v_pl, 'bite');
    EXCEPTION WHEN check_violation THEN v_bit := v_bit + 1; END;
    BEGIN  -- 24 members (rule 3)
        INSERT INTO ar_landfall_latest (init_ts, level_set, valid_ts, lead_h, point_code, lat, lon,
                                        n250, n500, n_present, ens_mean, max_member, absent_members)
        VALUES ('1900-01-01 00:00Z', 'sub7_v1', '1900-01-01 03:00Z', 3, 'coast_47.0N', 47.0, -124.5,
                1, 0, 24, 1, 1, ARRAY['gep25','gep26','gep27','gep28','gep29','gep30','gec00']);
    EXCEPTION WHEN check_violation THEN v_bit := v_bit + 1; END;
    BEGIN  -- 30 present, absent member not named (A2 in the schema)
        INSERT INTO ar_landfall_latest (init_ts, level_set, valid_ts, lead_h, point_code, lat, lon,
                                        n250, n500, n_present, ens_mean, max_member)
        VALUES ('1900-01-01 00:00Z', 'sub7_v1', '1900-01-01 03:00Z', 3, 'coast_47.0N', 47.0, -124.5,
                1, 0, 30, 1, 1);
    EXCEPTION WHEN check_violation THEN v_bit := v_bit + 1; END;
    BEGIN  -- a tc name without its rule (rule 7)
        INSERT INTO ar_landfall_latest (init_ts, level_set, valid_ts, lead_h, point_code, lat, lon,
                                        n250, n500, n_present, ens_mean, max_member, tc_named_system)
        VALUES ('1900-01-01 00:00Z', 'sub7_v1', '1900-01-01 03:00Z', 3, 'coast_47.0N', 47.0, -124.5,
                1, 0, 31, 1, 1, 'EP142026');
    EXCEPTION WHEN check_violation THEN v_bit := v_bit + 1; END;
    BEGIN  -- shown on 13 effective event days (rule 6)
        INSERT INTO ar_scores (scorer_hash, show_rule, level_set, truth_level_set, region, lead_bucket,
                               threshold, k_bin, n, n_eff, bss_lo90, event_days_eff, show, show_reasons)
        VALUES ('bite', 'show_v1', 'sub7_v1', 'sub7_v1', 'or_wa', 'd1-2', 250, 'all', 500, 120,
                0.2, 13, TRUE, ARRAY[]::text[]);
    EXCEPTION WHEN check_violation THEN v_bit := v_bit + 1; END;
    BEGIN  -- scored against another method (S4)
        INSERT INTO ar_scores (scorer_hash, show_rule, level_set, truth_level_set, region, lead_bucket,
                               threshold, k_bin, n, n_eff)
        VALUES ('bite', 'show_v1', 'sub7_v1', 'full17_v1', 'or_wa', 'd1-2', 250, '0', 500, 120);
    EXCEPTION WHEN check_violation THEN v_bit := v_bit + 1; END;
    IF v_bit <> 7 THEN RAISE EXCEPTION 'POST-CHECK (5): only % of 7 bites', v_bit; END IF;
    -- ... and the legitimate rows land
    INSERT INTO ar_landfall_latest (init_ts, level_set, valid_ts, lead_h, point_code, lat, lon,
                                    n250, n500, n_present, ens_mean, ctrl, max_member, absent_members)
    VALUES ('1900-01-01 00:00Z', 'sub7_v1', '1900-01-01 03:00Z', 3, 'coast_47.0N', 47.0, -124.5,
            30, 19, 30, 508, 558, 607, ARRAY['gep07']);
    INSERT INTO ar_scores (scorer_hash, show_rule, level_set, truth_level_set, region, lead_bucket,
                           threshold, k_bin, n, n_eff, bss_lo90, event_days_eff, show, show_reasons)
    VALUES ('bite', 'show_v1', 'sub7_v1', 'sub7_v1', 'or_wa', 'd1-2', 250, 'all', 500, 120,
            0.2, 20, TRUE, ARRAY[]::text[]);
    DELETE FROM ar_scores WHERE scorer_hash = 'bite';
    DELETE FROM ar_landfall_latest WHERE init_ts = '1900-01-01 00:00Z';
    DELETE FROM ar_runs WHERE method_hash = 'bite';
    SELECT (SELECT count(*) FROM ar_runs) + (SELECT count(*) FROM ar_landfall_latest)
         + (SELECT count(*) FROM ar_scores) INTO v_n;
    IF v_n <> 0 THEN RAISE EXCEPTION 'POST-CHECK: % probe row(s) survived', v_n; END IF;
    RAISE NOTICE 'POST-CHECK OK: 3 tables, 3 planned datasets, board and liveness unchanged for every other row, 7 of 7 bites';
END $post$;

-- 6. the ledger ----------------------------------------------------------------------
INSERT INTO schema_migrations (version, filename, status, applied_at, attestation, note)
VALUES (292, '292_ar_ivt_bank_shadow.sql', 'committed', now(),
        'captain-applied, architect-recorded',
        'd091665: atmospheric rivers in shadow. ar_runs (per-cycle ledger; level set in every key; 00Z/12Z only), ar_landfall_latest (odds, 25-of-31 floor and named absent members in the schema; tc_named_system/tc_rule_version NULL until d091664), ar_scores (show_v1 in the schema; like-for-like). datasets += ar_ivt_gefs_points, ar_landfall_odds_gefs, ar_ivt_truth_era5, born planned, no freshness arm, no firing-catalog row. ingestion_freshness and model_cycle_liveness untouched.');

COMMIT;
