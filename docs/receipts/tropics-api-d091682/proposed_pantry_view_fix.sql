-- d091682 STOP-V: what pantry's views of the rows in force would need so a read
-- through them stays an index read (a PROPOSAL for pantry; this lane writes
-- nothing to Neon). Same view names, same columns, same rows.
--
-- WHY. 294's views keep a row unless a row of the same advisory has a higher
-- revision (NOT EXISTS ... q.revision > p.revision). The planner reads that as
-- an anti-join against the WHOLE table restricted only by what it can carry
-- across (storm_id, sometimes source): for /tracks and /odds on Neon it chose a
-- Hash Anti Join over a seq scan of all of tropical_track_points (919 buffers,
-- against d091673's 110) and of tropical_place_odds; for /storm's census it
-- reads every cycle twice. Yet the only rows that can supersede anything are
-- rows at revision > 0, and there are none today and will be few ever (one per
-- corrected advisory whose numbers changed).
--
-- THE FIX. Say so in the view, and index exactly those rows:
--   q.revision > 0 is implied by q.revision > p.revision and the CHECK
--   revision >= 0, so the rows in force are unchanged; with it written out the
--   planner can answer the anti-join from a partial index of revised rows,
--   which is empty today and holds only corrections ever after.

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

CREATE OR REPLACE VIEW tropical_place_odds_in_force AS
SELECT o.*
  FROM tropical_place_odds o
 WHERE NOT EXISTS (SELECT 1 FROM tropical_place_odds q
                    WHERE q.revision > 0
                      AND q.storm_id = o.storm_id AND q.source = o.source AND q.issued_ts = o.issued_ts
                      AND q.advisory = o.advisory AND q.revision > o.revision);
