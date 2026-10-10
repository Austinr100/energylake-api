-- Migration 294's schema change, verbatim (sections 1 and 2: the revision in both
-- keys, its comments, and the three views of the rows in force), for the d091682
-- tests' local Postgres. Cut from energylake-pantry
-- migrations/294_tropical_correction_revisions.sql at pantry c7779f2
-- (file sha-256 42b610d1ccc85bd2c58f372d8cd52d63c6b1052f9bf8f4a04a70b875de32a4af); its pre-checks, post-checks
-- and ledger row are pantry's and are not repeated. Applied after the bank's rows
-- are loaded, as on Neon (2026-10-10 00:39Z): every existing row takes revision 0.
-- Neon's view definitions were read back on 2026-10-10 10:37Z and match these.

-- 1. the revision, in the key -----------------------------------------------
ALTER TABLE tropical_track_points
    ADD COLUMN revision smallint NOT NULL DEFAULT 0 CONSTRAINT ttp_revision_nonneg CHECK (revision >= 0),
    DROP CONSTRAINT uniq_tropical_track_points,
    ADD CONSTRAINT uniq_tropical_track_points UNIQUE (storm_id, source, init_ts, advisory, tau, revision);
COMMENT ON COLUMN tropical_track_points.revision IS
'How many NHC corrections the parsed files carry (ruling D-09-25-174, lane d091675): 0 for the '
'original, n beside it for the n-th correction. Rows are never updated: a correction''s rows are '
'inserted at their revision and the original''s stay as history. Readers take '
'tropical_track_points_in_force. Migration 294.';

ALTER TABLE tropical_place_odds
    ADD COLUMN revision smallint NOT NULL DEFAULT 0 CONSTRAINT tpo_revision_nonneg CHECK (revision >= 0),
    DROP CONSTRAINT uniq_tropical_place_odds,
    ADD CONSTRAINT uniq_tropical_place_odds UNIQUE NULLS NOT DISTINCT
        (storm_id, source, issued_ts, place_id, threshold_kt, radius_km, window_h, kind, revision);
COMMENT ON COLUMN tropical_place_odds.revision IS
'How many NHC corrections the PWS it was parsed from carries (D-09-25-174, d091675): 0 for the '
'original. Never updated; readers take tropical_place_odds_in_force. Migration 294.';

-- 2. the reads in force ------------------------------------------------------
CREATE VIEW tropical_track_points_in_force AS
SELECT p.*
  FROM tropical_track_points p
 WHERE NOT EXISTS (SELECT 1 FROM tropical_track_points q
                    WHERE q.storm_id = p.storm_id AND q.source = p.source AND q.init_ts = p.init_ts
                      AND q.advisory = p.advisory AND q.revision > p.revision);
COMMENT ON VIEW tropical_track_points_in_force IS
'The track points readers use (D-09-25-174, d091675): of each (storm_id, source, init_ts, '
'advisory), the rows of its highest revision only, so a corrected advisory is read whole. '
'revision > 0 means NHC corrected the advisory''s files. Migration 294.';

CREATE VIEW tropical_place_odds_in_force AS
SELECT o.*
  FROM tropical_place_odds o
 WHERE NOT EXISTS (SELECT 1 FROM tropical_place_odds q
                    WHERE q.storm_id = o.storm_id AND q.source = o.source AND q.issued_ts = o.issued_ts
                      AND q.advisory = o.advisory AND q.revision > o.revision);
COMMENT ON VIEW tropical_place_odds_in_force IS
'The place odds readers use (D-09-25-174, d091675): of each (storm_id, source, issued_ts, '
'advisory), the rows of its highest revision only. Migration 294.';

CREATE VIEW tropical_file_vintage_in_force AS
SELECT DISTINCT ON (v.source, v.product, v.storm_id, v.vintage_key)
       v.vintage_id, v.dataset, v.source, v.product, v.storm_id, v.vintage_key, v.fetch_ts,
       v.issued_ts, v.r2_key, v.byte_size, v.sha256, v.source_url, v.source_last_modified,
       v.source_etag, v.meta,
       COALESCE((v.meta -> 'correction' ->> 'revision')::int, 0) AS revision,
       (v.meta ? 'correction') AS corrected,
       (SELECT count(*) FROM tropical_file_vintage a
         WHERE a.source = v.source AND a.product = v.product AND a.storm_id = v.storm_id
           AND a.vintage_key = v.vintage_key AND a.status = 'banked'
           AND a.meta ? 'anomaly_prior_sha') AS anomalies_held
  FROM tropical_file_vintage v
 WHERE v.status = 'banked' AND NOT (v.meta ? 'anomaly_prior_sha')
 ORDER BY v.source, v.product, v.storm_id, v.vintage_key, v.fetch_ts DESC, v.vintage_id DESC;
COMMENT ON VIEW tropical_file_vintage_in_force IS
'Per banked file identity, the copy readers use (D-09-25-174, d091675): the newest that is not '
'an unexplained change (meta.anomaly_prior_sha; those wait for a human and are counted in '
'anomalies_held). corrected = NHC re-published it and said so (meta.correction names the prior '
'shas and the signal). Migration 294.';
