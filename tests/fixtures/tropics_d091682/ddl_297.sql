-- Migration 297's schema change, verbatim (the partial indexes ttp_revised and
-- tpo_revised, and the two in-force views re-stated with q.revision > 0), for the
-- d091682 tests' local Postgres. Cut from energylake-pantry
-- migrations/297_tropical_in_force_views_index_read.sql at pantry 0393def
-- (file sha-256 a405836f3a60d386dd21ac9b4b7ad3d840b5336e18da522f81401cf873e637e4); its pre-checks, post-check and
-- ledger row are pantry's and are not repeated. Applied after ddl_294.sql, as on
-- Neon (2026-10-10 11:55:17Z). Neon's view definitions were read back at 11:57Z and
-- match these.

CREATE INDEX ttp_revised ON tropical_track_points (storm_id, source, init_ts, advisory, revision)
    WHERE revision > 0;
CREATE INDEX tpo_revised ON tropical_place_odds (storm_id, source, issued_ts, advisory, revision)
    WHERE revision > 0;

CREATE OR REPLACE VIEW tropical_track_points_in_force AS
SELECT p.*
  FROM tropical_track_points p
 WHERE NOT EXISTS (SELECT 1 FROM tropical_track_points q
                    WHERE q.revision > 0
                      AND q.storm_id = p.storm_id AND q.source = p.source AND q.init_ts = p.init_ts
                      AND q.advisory = p.advisory AND q.revision > p.revision);
COMMENT ON VIEW tropical_track_points_in_force IS
'The track points readers use (D-09-25-174, d091675): of each (storm_id, source, init_ts, '
'advisory), the rows of its highest revision only, so a corrected advisory is read whole. '
'revision > 0 means NHC corrected the advisory''s files. Migration 294; made an index read '
'by 297 (the q.revision > 0 clause and the partial index ttp_revised).';

CREATE OR REPLACE VIEW tropical_place_odds_in_force AS
SELECT o.*
  FROM tropical_place_odds o
 WHERE NOT EXISTS (SELECT 1 FROM tropical_place_odds q
                    WHERE q.revision > 0
                      AND q.storm_id = o.storm_id AND q.source = o.source AND q.issued_ts = o.issued_ts
                      AND q.advisory = o.advisory AND q.revision > o.revision);
COMMENT ON VIEW tropical_place_odds_in_force IS
'The place odds readers use (D-09-25-174, d091675): of each (storm_id, source, issued_ts, '
'advisory), the rows of its highest revision only. Migration 294; made an index read by 297 '
'(the q.revision > 0 clause and the partial index tpo_revised).';
