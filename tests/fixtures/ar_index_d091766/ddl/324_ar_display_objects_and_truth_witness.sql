-- 324_ar_display_objects_and_truth_witness.sql
-- 2026-10-16 — lane d091765 (brief issued 2026-10-16 by the architect): the AR
-- bank gets a reader-safe publish ledger and a truth witness. Ruling
-- D-09-25-245 (the page is built now; no probability reaches a reader until its
-- (region, lead bucket, threshold) cell passes show_v1), the AR shadow contract
-- and show_v1 (migration 292), D-09-25-194/-197/-200/-205 (truth holes and
-- frontiers), D-09-05-T (the banked-object vector), migration 182's attestation
-- rule. Handback: docs/handback_2026_10_16_ar_publish_score_d091765.md.
--
-- *** NOT APPLIED BY THE LANE. Surfaced for explicit captain approval.     ***
-- *** Rehearsed on an ephemeral Postgres 16 with 292's DDL loaded verbatim ***
-- *** (scripts/rehearse_ar_publish_d091765.py --migration; test M1).       ***
--
-- DECLARED NUMBER: 324, as the brief issued it. Sweeps 2026-10-16:
--   (a) disk   — migrations/ on origin/main (b3ec6a3) tops out at
--                319_nwm_reach_forecast_clock.sql
--   (b) ledger — NOT read by the lane (no Neon credential). Literals: `git grep`
--                over origin/main finds no migration numbered 320..324 named in
--                any file (header, pre-check or ledger row)
--   (c) refs   — git ls-tree migrations/ over all 341 origin heads: nothing
--                numbered 320..399 on any head
--   318..323 are claimed by other lanes (the brief; 318/319 are on disk, 320..323
--   are booked, not yet visible). Claims are not jumped. If anything claims 324
--   at apply time, RENUMBER THIS FILE (three places: this header, the pre-check,
--   the ledger row); the ledger beats git.
--
-- WHY. The API and the page read Neon, never R2 per request (tropical.publish's
-- rule). Two facts were nowhere in Neon:
--   * WHICH display objects exist for a cycle. scripts/ar_publish.py now writes
--     them to the display bucket, immutable and content-checked; this ledger is
--     the index the API serves (ar_display_objects).
--   * HOW FAR THE TRUTH HAS REACHED. ar-truth-era5 writes R2 only and carries no
--     Neon secret; ingestion_freshness reads ar_ivt_truth_era5 MISSING. The
--     witness (ar_truth_days) is one row per truth day banked, or held open by a
--     hole, written by the same run after its R2 write (ar/witness.py).
--
-- WHAT THIS DOES
--   1. ar_display_objects — PK (level_set, kind, display_key). kind is
--      coast_ivt | odds | registry. A cycle object names its cycle (init_ts, FK
--      to ar_runs: only a banked cycle publishes) and its key IS its cycle and
--      level set (CHECK, the bank's rule 1); the registry has no cycle and its
--      key IS its sha (content-addressed: points_vN.{first 8 hex of sha256}.json).
--   2. ar_truth_days — PK (level_set, valid_date). status banked | hole_open |
--      hole_closed; a banked row's r2_key IS the day's truth parquet key, a
--      hole row's IS the day's hole record key (CHECK).
--   3. NO datasets row changes, no freshness arm, no catalog row (the witness
--      is not yet a clock: a clock on ar_truth_days is a promotion in 279's
--      shape, its own migration, after the witness has written).
--   4. The ledger row. A second apply is refused by the pre-check.
--
-- THE SHADOW CONTRACT HOLDS (292's COMMENTs). ar_display_objects indexes the
-- odds object too; that object is a BANK ARTIFACT the API gates per cell on
-- ar_scores (show = true under show_v1, k_bin 'all', newest scored_at) and never
-- draws as-is. coast_ivt carries member IVT only (no count, no probability).
--
-- ORDER. After 292 (refused otherwise); independent of 293..323. Apply BEFORE the
-- proposed ar-gefs-points.yml (publish step) and ar-truth-era5.yml (--witness)
-- are installed: either step's first write into an absent table fails its run.
--
-- REVERSAL (only while both tables are empty; scripts/rehearse_ar_publish_d091765.py
-- runs this block verbatim — it refuses to drop a table that holds rows):
-- BEGIN REVERSAL
-- BEGIN;
-- DO $$
-- BEGIN
--     IF EXISTS (SELECT 1 FROM ar_display_objects) OR EXISTS (SELECT 1 FROM ar_truth_days) THEN
--         RAISE EXCEPTION 'REVERSAL REFUSED: ar_display_objects or ar_truth_days holds rows — read them first';
--     END IF;
-- END $$;
-- DROP TABLE ar_display_objects;
-- DROP TABLE ar_truth_days;
-- DELETE FROM schema_migrations WHERE version = 324;
-- COMMIT;
-- END REVERSAL
--
-- Attestation (scripts/migration_attest.py, the extended rule of migration 182):
-- the DO blocks, COMMENT ONs and the ledger INSERT are excluded, so the attested
-- payload is three statements: the temp snapshot the post-check compares against,
-- and the two CREATE TABLEs.

BEGIN;

-- 0. pre-checks -------------------------------------------------------------------
DO $pre$
BEGIN
    PERFORM 1 FROM schema_migrations WHERE version = 324;
    IF FOUND THEN RAISE EXCEPTION 'PRECONDITION: schema_migrations already holds 324 — double apply, or renumber'; END IF;
    PERFORM 1 FROM schema_migrations WHERE version = 292;
    IF NOT FOUND THEN RAISE EXCEPTION 'PRECONDITION: schema_migrations lacks 292 — the AR bank (ar_runs, ar_scores) was never born'; END IF;
    IF to_regclass('ar_runs') IS NULL OR to_regclass('ar_scores') IS NULL THEN
        RAISE EXCEPTION 'PRECONDITION: 292 is recorded but ar_runs / ar_scores is absent — read before applying';
    END IF;
    IF to_regclass('ar_display_objects') IS NOT NULL OR to_regclass('ar_truth_days') IS NOT NULL THEN
        RAISE EXCEPTION 'PRECONDITION: ar_display_objects or ar_truth_days already exists — read it before applying';
    END IF;
    PERFORM 1 FROM pg_constraint
     WHERE conrelid = 'ar_runs'::regclass AND contype = 'p'
       AND pg_get_constraintdef(oid) = 'PRIMARY KEY (init_ts, level_set)';
    IF NOT FOUND THEN RAISE EXCEPTION 'PRECONDITION: ar_runs is not keyed (init_ts, level_set) — the FK would not hold'; END IF;
    RAISE NOTICE 'PRECONDITION OK: 292 recorded, ar_runs keyed (init_ts, level_set), 324 not recorded, both tables absent';
END $pre$;

CREATE TEMP TABLE _m324_snap ON COMMIT DROP AS
    SELECT (SELECT md5(coalesce(string_agg(to_jsonb(d)::text, '|' ORDER BY d.dataset_code), ''))
              FROM datasets d) AS datasets_md5,
           (SELECT count(*) FROM ar_runs) AS n_runs,
           (SELECT count(*) FROM ar_landfall_latest) AS n_latest,
           (SELECT count(*) FROM ar_scores) AS n_scores;

-- 1. ar_display_objects ------------------------------------------------------------
CREATE TABLE ar_display_objects (
    init_ts       timestamptz,
    level_set     text        NOT NULL CHECK (level_set ~ '^[a-z0-9]+_v[0-9]+$'),
    kind          text        NOT NULL CHECK (kind IN ('coast_ivt', 'odds', 'registry')),
    display_key   text        NOT NULL,
    sha256        text        NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    bytes         bigint      NOT NULL CHECK (bytes > 0),
    published_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (level_set, kind, display_key),
    FOREIGN KEY (init_ts, level_set) REFERENCES ar_runs (init_ts, level_set),
    CONSTRAINT ar_display_cycle_or_registry CHECK ((kind = 'registry') = (init_ts IS NULL)),
    CONSTRAINT ar_display_cycle_key CHECK (
        kind = 'registry'
        OR display_key = 'ar/gefs/' || to_char(init_ts AT TIME ZONE 'UTC', 'YYYYMMDD') || '/'
                         || to_char(init_ts AT TIME ZONE 'UTC', 'HH24') || 'Z/' || kind || '.'
                         || level_set || '.json'),
    CONSTRAINT ar_display_registry_key CHECK (
        kind <> 'registry'
        OR (display_key ~ '^ar/registry/points_v[0-9]+\.[0-9a-f]{8}\.json$'
            AND substr(display_key, length(display_key) - 12, 8) = left(sha256, 8)))
);
COMMENT ON TABLE ar_display_objects IS
    'd091765 (migration 324): the AR publish ledger the API reads. One row per object scripts/ar_publish.py '
    'put to the display bucket (object first, then this row; immutable, idempotent by sha). coast_ivt = member '
    'IVT at the 71 coast points (no count, no probability); odds = the cycle''s odds rows as banked, a BANK '
    'ARTIFACT the API gates per (region, lead bucket, threshold) on ar_scores show_v1 and never draws as-is '
    '(ruling D-09-25-245); registry = the point registry, content-addressed.';
COMMENT ON COLUMN ar_display_objects.init_ts IS
    'The banked cycle (FK ar_runs); NULL exactly for the registry.';

-- 2. ar_truth_days -----------------------------------------------------------------
CREATE TABLE ar_truth_days (
    valid_date  date        NOT NULL,
    level_set   text        NOT NULL CHECK (level_set ~ '^[a-z0-9]+_v[0-9]+$'),
    r2_key      text        NOT NULL,
    sha256      text        NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    status      text        NOT NULL CHECK (status IN ('banked', 'hole_open', 'hole_closed')),
    written_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (level_set, valid_date),
    CONSTRAINT ar_truth_days_key CHECK (
        r2_key = CASE WHEN status = 'banked'
                      THEN 'weather/truth/era5/ar/' || to_char(valid_date, 'YYYYMMDD') || '.' || level_set || '.parquet'
                      ELSE 'weather/truth/era5/ar/_holes/' || to_char(valid_date, 'YYYYMMDD') || '.' || level_set || '.json'
                 END)
);
COMMENT ON TABLE ar_truth_days IS
    'd091765 (migration 324): the ERA5 truth witness. One row per (level_set, valid_date) that '
    'scripts/ar_truth_era5.py --witness banked (r2_key = the day''s parquet, its sha) or holds open by a '
    'hole (r2_key = the hole record), written after the R2 write it witnesses; hole_closed = ruled absent. '
    'Not a freshness clock yet: promoting one is its own migration.';

-- 3. post-checks -------------------------------------------------------------------
DO $post$
DECLARE
    v_n   bigint;
    v_bit int := 0;
    v_pl  text := repeat('a', 64);
    v_sha text := repeat('b', 64);
BEGIN
    -- (1) both tables exist and are empty
    SELECT (SELECT count(*) FROM ar_display_objects) + (SELECT count(*) FROM ar_truth_days) INTO v_n;
    IF v_n <> 0 THEN RAISE EXCEPTION 'POST-CHECK (1): % row(s) in the new tables at birth', v_n; END IF;

    -- (2) BITES: each guard refuses what it exists to refuse; the probes roll back
    BEGIN
        INSERT INTO ar_runs (init_ts, level_set, members_present, steps_expected, steps_present,
                             steps_with_odds, bytes_pulled, status, points_key, odds_key, manifest_key,
                             point_list_sha, mask_sha, method_hash)
        VALUES ('1900-01-01 12:00Z', 'sub7_v1', 31, 93, 93, 93, 0, 'complete',
                'x/points_ivt.sub7_v1.parquet', 'x/odds.sub7_v1.parquet', 'x/_manifest.sub7_v1.json',
                v_pl, v_pl, 'probe324');
        -- the legitimate rows land
        INSERT INTO ar_display_objects (init_ts, level_set, kind, display_key, sha256, bytes)
        VALUES ('1900-01-01 12:00Z', 'sub7_v1', 'coast_ivt', 'ar/gefs/19000101/12Z/coast_ivt.sub7_v1.json', v_sha, 1),
               ('1900-01-01 12:00Z', 'sub7_v1', 'odds', 'ar/gefs/19000101/12Z/odds.sub7_v1.json', v_sha, 1),
               (NULL, 'sub7_v1', 'registry', 'ar/registry/points_v1.bbbbbbbb.json', v_sha, 1);
        INSERT INTO ar_truth_days (valid_date, level_set, r2_key, sha256, status)
        VALUES ('1900-01-01', 'sub7_v1', 'weather/truth/era5/ar/19000101.sub7_v1.parquet', v_sha, 'banked'),
               ('1900-01-02', 'sub7_v1', 'weather/truth/era5/ar/_holes/19000102.sub7_v1.json', v_sha, 'hole_open'),
               ('1900-01-03', 'sub7_v1', 'weather/truth/era5/ar/_holes/19000103.sub7_v1.json', v_sha, 'hole_closed');
        BEGIN  -- a cycle object for a cycle the bank never banked (FK)
            INSERT INTO ar_display_objects (init_ts, level_set, kind, display_key, sha256, bytes)
            VALUES ('1900-01-02 00:00Z', 'sub7_v1', 'odds', 'ar/gefs/19000102/00Z/odds.sub7_v1.json', v_sha, 1);
        EXCEPTION WHEN foreign_key_violation THEN v_bit := v_bit + 1; END;
        BEGIN  -- a cycle object whose key is another cycle's
            INSERT INTO ar_display_objects (init_ts, level_set, kind, display_key, sha256, bytes)
            VALUES ('1900-01-01 12:00Z', 'sub7_v1', 'odds', 'ar/gefs/19000101/00Z/odds.sub7_v1.json', v_sha, 1);
        EXCEPTION WHEN check_violation THEN v_bit := v_bit + 1; END;
        BEGIN  -- a cycle object whose key lacks its level set (292's rule 1)
            INSERT INTO ar_display_objects (init_ts, level_set, kind, display_key, sha256, bytes)
            VALUES ('1900-01-01 12:00Z', 'sub7_v1', 'coast_ivt', 'ar/gefs/19000101/12Z/coast_ivt.json', v_sha, 1);
        EXCEPTION WHEN check_violation THEN v_bit := v_bit + 1; END;
        BEGIN  -- a registry whose key is not its sha
            INSERT INTO ar_display_objects (init_ts, level_set, kind, display_key, sha256, bytes)
            VALUES (NULL, 'sub7_v1', 'registry', 'ar/registry/points_v1.cccccccc.json', v_sha, 1);
        EXCEPTION WHEN check_violation THEN v_bit := v_bit + 1; END;
        BEGIN  -- a registry that names a cycle
            INSERT INTO ar_display_objects (init_ts, level_set, kind, display_key, sha256, bytes)
            VALUES ('1900-01-01 12:00Z', 'sub7_v1', 'registry', 'ar/registry/points_v1.bbbbbbbb.json', v_sha, 1);
        EXCEPTION WHEN check_violation THEN v_bit := v_bit + 1; END;
        BEGIN  -- a kind the API does not know
            INSERT INTO ar_display_objects (init_ts, level_set, kind, display_key, sha256, bytes)
            VALUES ('1900-01-01 12:00Z', 'sub7_v1', 'chart', 'ar/gefs/19000101/12Z/chart.sub7_v1.json', v_sha, 1);
        EXCEPTION WHEN check_violation THEN v_bit := v_bit + 1; END;
        BEGIN  -- a sha that is not sha-256 hex
            INSERT INTO ar_display_objects (init_ts, level_set, kind, display_key, sha256, bytes)
            VALUES (NULL, 'sub7_v2', 'registry', 'ar/registry/points_v1.bbbbbbbb.json', 'bbbbbbbb', 1);
        EXCEPTION WHEN check_violation THEN v_bit := v_bit + 1; END;
        BEGIN  -- a status the witness does not write
            INSERT INTO ar_truth_days (valid_date, level_set, r2_key, sha256, status)
            VALUES ('1900-01-04', 'sub7_v1', 'weather/truth/era5/ar/19000104.sub7_v1.parquet', v_sha, 'preliminary');
        EXCEPTION WHEN check_violation THEN v_bit := v_bit + 1; END;
        BEGIN  -- banked, pointing at a hole record
            INSERT INTO ar_truth_days (valid_date, level_set, r2_key, sha256, status)
            VALUES ('1900-01-04', 'sub7_v1', 'weather/truth/era5/ar/_holes/19000104.sub7_v1.json', v_sha, 'banked');
        EXCEPTION WHEN check_violation THEN v_bit := v_bit + 1; END;
        BEGIN  -- a hole row pointing at another day's record
            INSERT INTO ar_truth_days (valid_date, level_set, r2_key, sha256, status)
            VALUES ('1900-01-04', 'sub7_v1', 'weather/truth/era5/ar/_holes/19000105.sub7_v1.json', v_sha, 'hole_open');
        EXCEPTION WHEN check_violation THEN v_bit := v_bit + 1; END;
        IF v_bit <> 10 THEN RAISE EXCEPTION 'POST-CHECK (2): only % of 10 bites', v_bit; END IF;
        RAISE EXCEPTION USING ERRCODE = 'P0324', MESSAGE = 'probe rows rolled back';
    EXCEPTION WHEN SQLSTATE 'P0324' THEN
        NULL;   -- every probe row above, the ar_runs probe included, is rolled back here
    END;

    -- (3) the probes are gone, and nothing else moved
    SELECT (SELECT count(*) FROM ar_display_objects) + (SELECT count(*) FROM ar_truth_days) INTO v_n;
    IF v_n <> 0 THEN RAISE EXCEPTION 'POST-CHECK (3): % probe row(s) survived', v_n; END IF;
    IF (SELECT count(*) FROM ar_runs) <> (SELECT n_runs FROM _m324_snap)
       OR (SELECT count(*) FROM ar_landfall_latest) <> (SELECT n_latest FROM _m324_snap)
       OR (SELECT count(*) FROM ar_scores) <> (SELECT n_scores FROM _m324_snap) THEN
        RAISE EXCEPTION 'POST-CHECK (3): an ar_runs / ar_landfall_latest / ar_scores row count moved';
    END IF;
    IF (SELECT md5(coalesce(string_agg(to_jsonb(d)::text, '|' ORDER BY d.dataset_code), '')) FROM datasets d)
       IS DISTINCT FROM (SELECT datasets_md5 FROM _m324_snap) THEN
        RAISE EXCEPTION 'POST-CHECK (3): a datasets row changed — 324 owns none';
    END IF;
    RAISE NOTICE 'POST-CHECK OK: ar_display_objects and ar_truth_days born empty, 10 of 10 bites, probes rolled back, ar_* counts and datasets unchanged';
END $post$;

-- 4. the ledger ----------------------------------------------------------------------
INSERT INTO schema_migrations (version, filename, status, applied_at, attestation, note)
VALUES (324, '324_ar_display_objects_and_truth_witness.sql', 'committed', now(),
        '6db58cda4d05d89b772e2fe6a0966587',
        'd091765: the AR bank gets a reader-safe publish ledger and a truth witness (ruling D-09-25-245). ar_display_objects (PK level_set, kind, display_key; kind coast_ivt | odds | registry; a cycle object FK ar_runs and its key IS its cycle and level set; the registry key IS its sha8) — the index the API serves, written by scripts/ar_publish.py after each display-bucket put. ar_truth_days (PK level_set, valid_date; banked | hole_open | hole_closed; r2_key IS the day''s parquet or hole record) — written by scripts/ar_truth_era5.py --witness after each R2 write. No datasets row, no freshness arm, no catalog row. Pre-check refuses: 292 absent, ar_runs/ar_scores absent or ar_runs not keyed (init_ts, level_set), 324 recorded, either table present. Post-check: born empty, 10 of 10 CHECK/FK bites with probe rows rolled back, ar_* counts and datasets byte-equal.');

COMMIT;
