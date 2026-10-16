-- =========================================================================
-- Migration 316: the outlook keyed by its stamp (d091756 follow-up, ruling D-09-25-234)
-- =========================================================================
-- Date drafted: 2026-10-15. Author: Claude (the d091756 follow-up lane), under ruling
-- D-09-25-234 (and D-09-25-226 §2/§5, D-09-25-174, D-09-05-T). A FILE ONLY: NOT APPLIED,
-- never run against Neon by the lane. The architect applies it on the captain's word,
-- BEFORE this branch merges. Merged first, the publisher refuses every TWO source by
-- name ("migration 316 is not applied"), exits 2 on every run until 316 lands, and keeps
-- publishing every other kind. d091756's own branch (hour-slot keys) must NOT merge: this
-- branch carries its commit and supersedes it. Were its hour-keyed rows ever written,
-- pre-check (c) refuses this file.
-- Rehearsal: scripts/rehearse_migration_316.py, an ephemeral Postgres 16 with 314's DDL,
-- the database SET idle_in_transaction_session_timeout = '2s' (Neon's is 5min, read
-- 20:18Z). Receipt docs/receipts/outlook-stamp-key-316/rehearse_316.txt; test C1 applies
-- this file too.
--
-- NUMBER: 316. Three sweeps (2026-10-15 ~20:20Z): (a) disk — migrations/ on main @ a09affb
-- tops out at 315; (b) ledger — schema_migrations on Neon: 312..315 committed,
-- max(version) = 315 (read-only SELECT); (c) refs — git ls-tree migrations/ over all 340
-- remote heads: nothing claims 316 or above. If anything claims 316 at apply time,
-- RENUMBER (header, the pre-check, the ledger row); the ledger beats git.
--
-- WHY. D-09-25-234: r<n> on a display row means a correction NHC issued, and nothing else;
-- an outlook's identity is its 12-digit issuance stamp (YYYYMMDDHHMM). 314's
-- tfv_display_vintage_key admits NNN[A] or YYYYMMDDHH only, so d091756 keyed the TWO rows
-- by the hour plus an r<n> slot for the re-issues inside it. The ruling refuses that. This
-- file lets the CHECK and the in-force view read a 12-digit base, so the publisher keys
-- each TWO row by its stamp, and r<n> keeps its one meaning.
--
-- MEASURED on Neon at 20:18Z (read-only SELECTs, PG 17.11), the live text this file is
-- written against (tests/fixtures/outlook_stamp_key_316/neon_catalog_2026_10_15_2018Z.json):
--   tfv_display_vintage_key   CHECK (((source <> 'display'::text) OR (vintage_key ~
--                             '^([0-9]{3}[A-Z]?|[0-9]{10})(r[1-9][0-9]*)?$'::text)))
--   tropical_display_in_force md5(pg_get_viewdef) = cc73e4d9b88b74494b19029b1a0aacd0
--                             (314's view, unmoved; a local Postgres 16 prints the
--                             same text for 314's DDL, md5 equal)
--   463 display rows, none 12-digit, none display:two_*; 7,957 ledger rows (6.2 MB).
--
-- WHAT IT DOES
--   1. tfv_display_vintage_key re-cut (same name): the base admits [0-9]{12} beside
--      [0-9]{3}[A-Z]? and [0-9]{10}; the r<n> suffix as before.
--   2. CREATE OR REPLACE VIEW tropical_display_in_force: the base pattern widened the same
--      way, in its three places (DISTINCT ON, advisory_or_cycle, ORDER BY), and NOTHING
--      else. The view text after = the text before with exactly that substitution (the
--      post-check diffs it). Same columns, same order, same types; the comment, the grants
--      and the oid are kept (CREATE OR REPLACE).
--      WHY THE VIEW MUST MOVE WITH THE CHECK: Postgres takes the LONGEST alternative of
--      a branch-level |, so under 314's base pattern '202610140517' reads as
--      '2026101405' (measured) and two outlooks of one hour would fold into one
--      advisory_or_cycle. The widened pattern reads it as itself; every key 314 admits
--      reads exactly as before (3- and 10-digit bases have no longer match).
--   NOTHING ELSE. No row written or moved (post-check: count and md5 over every ledger
--   row and over every display row, before = after); tropical_file_vintage_in_force, the
--   other twelve constraints and tfv_display_immutable untouched (their text md5,
--   before = after). The probe rows are rolled back; the identity sequence they draw
--   from is not (vintage_id jumps, as in 314).
--   LOCK: ALTER TABLE takes ACCESS EXCLUSIVE on tropical_file_vintage while it validates
--   7,957 rows (milliseconds); the poll and the publisher wait, they do not fail.
--
-- THE ROW CONTRACT after 316 (d091757 reads it): product display:two_areas, two_points,
-- two_text_at, two_text_ep; vintage_key = the 12-digit stamp, advisory_or_cycle = the
-- stamp; r<n> only for a correction NHC signalled on a copy of the same stamp.
--
-- REVERSAL (one transaction; only while no display row carries a 12-digit key, or with
-- those rows deleted first by the captain's word: the published objects stay in the
-- display bucket, immutable):
--   ALTER TABLE tropical_file_vintage DROP CONSTRAINT tfv_display_vintage_key,
--       ADD CONSTRAINT tfv_display_vintage_key CHECK (source <> 'display'
--       OR vintage_key ~ '^([0-9]{3}[A-Z]?|[0-9]{10})(r[1-9][0-9]*)?$');
--   CREATE OR REPLACE VIEW tropical_display_in_force AS
--   SELECT DISTINCT ON (v.product, v.storm_id, substring(v.vintage_key FROM '^([0-9]{3}[A-Z]?|[0-9]{10})'))
--          v.vintage_id, v.dataset, v.product, v.storm_id,
--          substring(v.vintage_key FROM '^([0-9]{3}[A-Z]?|[0-9]{10})') AS advisory_or_cycle,
--          COALESCE(substring(v.vintage_key FROM 'r([1-9][0-9]*)$')::int, 0) AS revision,
--          v.vintage_key, v.fetch_ts AS published_ts, v.issued_ts, v.r2_key, v.byte_size, v.sha256, v.meta
--     FROM tropical_file_vintage v
--    WHERE v.source = 'display' AND v.status = 'banked'
--    ORDER BY v.product, v.storm_id, substring(v.vintage_key FROM '^([0-9]{3}[A-Z]?|[0-9]{10})'),
--             COALESCE(substring(v.vintage_key FROM 'r([1-9][0-9]*)$')::int, 0) DESC,
--             v.fetch_ts DESC, v.vintage_id DESC;
--   DELETE FROM schema_migrations WHERE version = 316;
-- =========================================================================

BEGIN;

-- 0. pre-checks: the live shape this file was written against; refuse a re-apply
DO $$
BEGIN
    -- (a) the ledger
    IF EXISTS (SELECT 1 FROM schema_migrations WHERE version = 316) THEN
        RAISE EXCEPTION 'PRECONDITION: schema_migrations already holds 316 — applied?';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM schema_migrations WHERE version = 315)
       OR NOT EXISTS (SELECT 1 FROM schema_migrations WHERE version = 314) THEN
        RAISE EXCEPTION 'PRECONDITION: 314 and 315 are not both on the ledger — apply in order';
    END IF;
    -- (b) no display row is keyed by a 12-digit stamp yet
    IF EXISTS (SELECT 1 FROM tropical_file_vintage WHERE source = 'display' AND vintage_key ~ '^[0-9]{12}') THEN
        RAISE EXCEPTION 'PRECONDITION: a display row already carries a 12-digit vintage_key — 316 applied?';
    END IF;
    -- (c) no outlook row under d091756's hour-slot keys (its branch did not merge and run)
    IF EXISTS (SELECT 1 FROM tropical_file_vintage WHERE source = 'display' AND product LIKE 'display:two\_%') THEN
        RAISE EXCEPTION 'PRECONDITION: display:two_* rows exist under hour keys (d091756''s slots) — they would sit beside the stamp keys; the captain decides first';
    END IF;
    -- (d) the CHECK, exactly as 314 left it (measured 2026-10-15 20:18Z)
    IF (SELECT pg_get_constraintdef(oid) FROM pg_constraint
         WHERE conrelid = 'tropical_file_vintage'::regclass AND conname = 'tfv_display_vintage_key')
       IS DISTINCT FROM 'CHECK (((source <> ''display''::text) OR (vintage_key ~ ''^([0-9]{3}[A-Z]?|[0-9]{10})(r[1-9][0-9]*)?$''::text)))' THEN
        RAISE EXCEPTION 'PRECONDITION: tfv_display_vintage_key is not 314''s (measured 2026-10-15)';
    END IF;
    -- (e) the view text, exactly as 314 left it (md5 of pg_get_viewdef, measured on Neon)
    IF to_regclass('tropical_display_in_force') IS NULL
       OR md5(pg_get_viewdef('tropical_display_in_force'::regclass)) IS DISTINCT FROM 'cc73e4d9b88b74494b19029b1a0aacd0' THEN
        RAISE EXCEPTION 'PRECONDITION: tropical_display_in_force is not 314''s text (md5 cc73e4d9…, measured 2026-10-15)';
    END IF;
    RAISE NOTICE 'PRECONDITION OK: 314 and 315 on the ledger, 316 not; no 12-digit or two_* display row; tfv_display_vintage_key and tropical_display_in_force are 314''s, measured';
END $$;

-- Everything that must not move, before anything moves.
CREATE TEMP TABLE _316_before ON COMMIT DROP AS
SELECT (SELECT count(*) FROM tropical_file_vintage) AS n_rows,
       (SELECT md5(coalesce(string_agg(t::text, ',' ORDER BY t.vintage_id), '')) FROM tropical_file_vintage t) AS rows_md5,
       (SELECT count(*) FROM tropical_file_vintage WHERE source = 'display') AS n_display,
       (SELECT md5(coalesce(string_agg(t::text, ',' ORDER BY t.vintage_id), '')) FROM tropical_file_vintage t
         WHERE t.source = 'display') AS display_md5,
       pg_get_viewdef('tropical_display_in_force'::regclass) AS view_def,
       obj_description('tropical_display_in_force'::regclass, 'pg_class') AS view_comment,
       (SELECT string_agg(column_name || ' ' || data_type, ',' ORDER BY ordinal_position)
          FROM information_schema.columns WHERE table_name = 'tropical_display_in_force') AS view_cols,
       md5(pg_get_viewdef('tropical_file_vintage_in_force'::regclass)) AS in_force_md5,
       (SELECT md5(string_agg(conname || ' ' || pg_get_constraintdef(oid), ',' ORDER BY conname)) FROM pg_constraint
         WHERE conrelid = 'tropical_file_vintage'::regclass AND conname <> 'tfv_display_vintage_key') AS other_cons_md5,
       md5(pg_get_indexdef('tfv_display_immutable'::regclass)) AS immutable_md5;

-- 1. the CHECK -----------------------------------------------------------------
ALTER TABLE tropical_file_vintage
    DROP CONSTRAINT tfv_display_vintage_key,
    ADD CONSTRAINT tfv_display_vintage_key CHECK (source <> 'display'
        OR vintage_key ~ '^([0-9]{3}[A-Z]?|[0-9]{10}|[0-9]{12})(r[1-9][0-9]*)?$');

-- 2. the in-force read: the base pattern widened, nothing else -----------------
CREATE OR REPLACE VIEW tropical_display_in_force AS
SELECT DISTINCT ON (v.product, v.storm_id, substring(v.vintage_key FROM '^([0-9]{3}[A-Z]?|[0-9]{10}|[0-9]{12})'))
       v.vintage_id, v.dataset, v.product, v.storm_id,
       substring(v.vintage_key FROM '^([0-9]{3}[A-Z]?|[0-9]{10}|[0-9]{12})') AS advisory_or_cycle,
       COALESCE(substring(v.vintage_key FROM 'r([1-9][0-9]*)$')::int, 0) AS revision,
       v.vintage_key, v.fetch_ts AS published_ts, v.issued_ts, v.r2_key, v.byte_size, v.sha256, v.meta
  FROM tropical_file_vintage v
 WHERE v.source = 'display' AND v.status = 'banked'
 ORDER BY v.product, v.storm_id, substring(v.vintage_key FROM '^([0-9]{3}[A-Z]?|[0-9]{10}|[0-9]{12})'),
          COALESCE(substring(v.vintage_key FROM 'r([1-9][0-9]*)$')::int, 0) DESC,
          v.fetch_ts DESC, v.vintage_id DESC;

-- 3. post-checks: the shape, the diff, nothing moved, and the CHECK bites -------
DO $$
DECLARE b record; v_n bigint; v_s text; bites int := 0; lands int := 0;
BEGIN
    SELECT * INTO b FROM _316_before;

    -- (1) the CHECK as written, validated
    IF (SELECT pg_get_constraintdef(oid) FROM pg_constraint
         WHERE conrelid = 'tropical_file_vintage'::regclass AND conname = 'tfv_display_vintage_key')
       IS DISTINCT FROM 'CHECK (((source <> ''display''::text) OR (vintage_key ~ ''^([0-9]{3}[A-Z]?|[0-9]{10}|[0-9]{12})(r[1-9][0-9]*)?$''::text)))'
       OR NOT (SELECT convalidated FROM pg_constraint
                WHERE conrelid = 'tropical_file_vintage'::regclass AND conname = 'tfv_display_vintage_key') THEN
        RAISE EXCEPTION 'POST-CHECK FAILED (1): tfv_display_vintage_key is not 316''s, validated';
    END IF;
    -- every existing row passes it (the ADD validated them; counted here as well)
    SELECT count(*) INTO v_n FROM tropical_file_vintage
     WHERE source = 'display' AND vintage_key !~ '^([0-9]{3}[A-Z]?|[0-9]{10}|[0-9]{12})(r[1-9][0-9]*)?$';
    IF v_n <> 0 THEN RAISE EXCEPTION 'POST-CHECK FAILED (1): % display rows fail the new CHECK', v_n; END IF;

    -- (2) the view: 314's text with the base pattern widened in its three places, and
    --     nothing else; the expected text's md5 (measured on Postgres 16); same columns
    --     and comment
    IF (length(b.view_def) - length(replace(b.view_def, '''^([0-9]{3}[A-Z]?|[0-9]{10})''::text', ''))) /
        length('''^([0-9]{3}[A-Z]?|[0-9]{10})''::text') <> 3 THEN
        RAISE EXCEPTION 'POST-CHECK FAILED (2): 314''s view does not hold the base pattern three times';
    END IF;
    IF pg_get_viewdef('tropical_display_in_force'::regclass) IS DISTINCT FROM
       replace(b.view_def, '''^([0-9]{3}[A-Z]?|[0-9]{10})''::text', '''^([0-9]{3}[A-Z]?|[0-9]{10}|[0-9]{12})''::text') THEN
        RAISE EXCEPTION 'POST-CHECK FAILED (2): the view text is not 314''s with the base pattern widened';
    END IF;
    IF md5(pg_get_viewdef('tropical_display_in_force'::regclass)) IS DISTINCT FROM '977c97d66d70ff8a8ed7e6992545f6c7' THEN
        RAISE EXCEPTION 'POST-CHECK FAILED (2): the view text md5 is not the expected 977c97d6…';
    END IF;
    IF (SELECT string_agg(column_name || ' ' || data_type, ',' ORDER BY ordinal_position)
          FROM information_schema.columns WHERE table_name = 'tropical_display_in_force') IS DISTINCT FROM b.view_cols
       OR obj_description('tropical_display_in_force'::regclass, 'pg_class') IS DISTINCT FROM b.view_comment THEN
        RAISE EXCEPTION 'POST-CHECK FAILED (2): the view''s columns or comment moved';
    END IF;

    -- (3) zero collateral: every row, every display row, the other view, the other
    --     constraints and the immutable index are what they were
    SELECT count(*), md5(coalesce(string_agg(t::text, ',' ORDER BY t.vintage_id), ''))
      INTO v_n, v_s FROM tropical_file_vintage t;
    IF v_n <> b.n_rows OR v_s IS DISTINCT FROM b.rows_md5 THEN
        RAISE EXCEPTION 'POST-CHECK FAILED (3): a ledger row moved (% -> % rows)', b.n_rows, v_n;
    END IF;
    SELECT count(*), md5(coalesce(string_agg(t::text, ',' ORDER BY t.vintage_id), ''))
      INTO v_n, v_s FROM tropical_file_vintage t WHERE t.source = 'display';
    IF v_n <> b.n_display OR v_s IS DISTINCT FROM b.display_md5 THEN
        RAISE EXCEPTION 'POST-CHECK FAILED (3): a display row moved (% -> % rows)', b.n_display, v_n;
    END IF;
    IF md5(pg_get_viewdef('tropical_file_vintage_in_force'::regclass)) IS DISTINCT FROM b.in_force_md5
       OR (SELECT md5(string_agg(conname || ' ' || pg_get_constraintdef(oid), ',' ORDER BY conname)) FROM pg_constraint
            WHERE conrelid = 'tropical_file_vintage'::regclass AND conname <> 'tfv_display_vintage_key')
          IS DISTINCT FROM b.other_cons_md5
       OR md5(pg_get_indexdef('tfv_display_immutable'::regclass)) IS DISTINCT FROM b.immutable_md5 THEN
        RAISE EXCEPTION 'POST-CHECK FAILED (3): collateral: tropical_file_vintage_in_force, another constraint or tfv_display_immutable moved';
    END IF;

    -- (4) the stamp rows land, the view reads each stamp as its own issuance and a
    --     correction over it, the old keys still land, and every wrong shape bites;
    --     inside a block that is rolled back. Probe identities no real row can hold: storm
    --     ep992026 (a test number), WSP cycle 2099123118, and stamps of 2026-10-14 05Z, an
    --     hour no display:two_* row can hold (pre-check (c): there are none)
    BEGIN
        INSERT INTO tropical_file_vintage (dataset, source, product, storm_id, vintage_key, status, fetch_ts,
                                           r2_key, byte_size, sha256, meta)
        VALUES ('nhc_two_vintage', 'display', 'display:two_areas', '', '202610140517', 'banked',
                '2026-10-15 14:00+00', 'tropics/two/202610140517/areas.geojson', 1, repeat('a', 64), '{}'),
               ('nhc_two_vintage', 'display', 'display:two_areas', '', '202610140522', 'banked',
                '2026-10-15 14:00+00', 'tropics/two/202610140522/areas.geojson', 1, repeat('b', 64), '{}'),
               ('nhc_two_vintage', 'display', 'display:two_areas', '', '202610140517r1', 'banked',
                '2026-10-15 13:00+00', 'tropics/two/202610140517r1/areas.geojson', 1, repeat('c', 64), '{}'),
               ('nhc_advisory_vintage', 'display', 'display:cone', 'ep992026', '057Ar1', 'banked',
                '2026-10-15 14:00+00', 'tropics/ep992026/adv057Ar1/cone.geojson', 1, repeat('d', 64), '{}'),
               ('nhc_wsp_vintage', 'display', 'display:wsp34_bands', '', '2099123118', 'banked',
                '2026-10-15 14:00+00', 'tropics/wsp/2099123118/wsp34_bands.geojson', 1, repeat('e', 64), '{}');
        lands := 5;
        IF (SELECT string_agg(advisory_or_cycle || '=' || vintage_key, ',' ORDER BY advisory_or_cycle)
              FROM tropical_display_in_force WHERE product = 'display:two_areas')
           IS DISTINCT FROM '202610140517=202610140517r1,202610140522=202610140522' THEN
            RAISE EXCEPTION 'POST-CHECK FAILED (4): the view does not read two stamps of one hour as two issuances, the correction in force';
        END IF;
        IF (SELECT advisory_or_cycle || '=' || vintage_key FROM tropical_display_in_force
             WHERE product = 'display:cone' AND storm_id = 'ep992026') IS DISTINCT FROM '057A=057Ar1'
           OR (SELECT advisory_or_cycle FROM tropical_display_in_force WHERE product = 'display:wsp34_bands'
                AND vintage_key = '2099123118') IS DISTINCT FROM '2099123118' THEN
            RAISE EXCEPTION 'POST-CHECK FAILED (4): an advisory or a WSP cycle no longer reads as 314 read it';
        END IF;

        BEGIN INSERT INTO tropical_file_vintage (dataset, source, product, vintage_key, status, r2_key, byte_size, sha256)
              VALUES ('nhc_two_vintage', 'display', 'display:two_areas', '20261014051', 'banked', 'k', 1, repeat('f', 64));
        EXCEPTION WHEN check_violation THEN bites := bites + 1; END;   -- 11 digits
        BEGIN INSERT INTO tropical_file_vintage (dataset, source, product, vintage_key, status, r2_key, byte_size, sha256)
              VALUES ('nhc_two_vintage', 'display', 'display:two_areas', '2026101405170', 'banked', 'k', 1, repeat('f', 64));
        EXCEPTION WHEN check_violation THEN bites := bites + 1; END;   -- 13 digits
        BEGIN INSERT INTO tropical_file_vintage (dataset, source, product, vintage_key, status, r2_key, byte_size, sha256)
              VALUES ('nhc_two_vintage', 'display', 'display:two_areas', '202610140517r0', 'banked', 'k', 1, repeat('f', 64));
        EXCEPTION WHEN check_violation THEN bites := bites + 1; END;   -- revision 0 written as r0
        BEGIN INSERT INTO tropical_file_vintage (dataset, source, product, vintage_key, status, r2_key, byte_size, sha256)
              VALUES ('nhc_two_vintage', 'display', 'display:two_areas', 'ABNT20_KNHC_140517', 'banked', 'k', 1, repeat('f', 64));
        EXCEPTION WHEN check_violation THEN bites := bites + 1; END;   -- the bank's WMO key, not a stamp
        BEGIN INSERT INTO tropical_file_vintage (dataset, source, product, vintage_key, status, r2_key, byte_size, sha256)
              VALUES ('nhc_advisory_vintage', 'display', 'display:cone', 'adv058', 'banked', 'k', 1, repeat('f', 64));
        EXCEPTION WHEN check_violation THEN bites := bites + 1; END;   -- 314's bite, still a bite
        BEGIN INSERT INTO tropical_file_vintage (dataset, source, product, storm_id, vintage_key, status, r2_key,
                                                 byte_size, sha256)
              VALUES ('nhc_two_vintage', 'display', 'display:two_areas', '', '202610140522', 'banked',
                      'tropics/two/202610140522/areas.geojson', 1, repeat('0', 64))
              ON CONFLICT ON CONSTRAINT uniq_tropical_file_vintage DO NOTHING;
        EXCEPTION WHEN unique_violation THEN bites := bites + 1; END;  -- a second sha under a published stamp
        RAISE EXCEPTION 'ROLLBACK_PROBE' USING ERRCODE = 'P0316', DETAIL = bites::text || ':' || lands::text;
    EXCEPTION WHEN SQLSTATE 'P0316' THEN
        GET STACKED DIAGNOSTICS v_s = PG_EXCEPTION_DETAIL;
        bites := split_part(v_s, ':', 1)::int;
        lands := split_part(v_s, ':', 2)::int;
    END;
    IF bites <> 6 THEN RAISE EXCEPTION 'POST-CHECK FAILED (4): % of 6 bites', bites; END IF;
    IF lands <> 5 THEN RAISE EXCEPTION 'POST-CHECK FAILED (4): % of 5 legitimate rows landed', lands; END IF;
    IF (SELECT count(*) FROM tropical_file_vintage) <> b.n_rows THEN
        RAISE EXCEPTION 'POST-CHECK FAILED (4): a probe row survived';
    END IF;

    RAISE NOTICE 'POST-CHECK OK: tfv_display_vintage_key admits a 12-digit stamp, validated; the view is 314''s with the base pattern widened (3 places, md5 977c97d6…), columns and comment kept; % ledger rows and % display rows unchanged (count and md5); tropical_file_vintage_in_force, 12 other constraints and tfv_display_immutable unmoved; % legitimate rows landed, two stamps of one hour read as two issuances; 6 of 6 bites; probe rolled back',
        b.n_rows, b.n_display, lands;
END $$;

-- 4. the ledger -------------------------------------------------------------
INSERT INTO schema_migrations (version, filename, status, applied_at, attestation, note)
VALUES (316, '316_tropical_display_stamp_key.sql', 'committed', now(),
        '2bef5c451e0738c2a50a3620bcc49a72',
        'd091756 follow-up (D-09-25-234): an outlook''s identity is its 12-digit issuance stamp; r<n> means a correction NHC issued and nothing else. tfv_display_vintage_key re-cut: base ^([0-9]{3}[A-Z]?|[0-9]{10}|[0-9]{12}), then r<n>. tropical_display_in_force: the same base pattern in its three places, nothing else (view text diffed). No row written or moved; no other constraint, index or view touched.')
ON CONFLICT (version) DO NOTHING;

COMMIT;
