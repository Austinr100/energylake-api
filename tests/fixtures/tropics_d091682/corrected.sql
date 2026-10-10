-- d091682: the constructed corrections, laid over the banked snapshot (the d091673
-- bank with migrations 294 and 297 and the ledger slice). CONSTRUCTED, not NHC's:
-- every ledger row below says so in meta.constructed, and every sha is a
-- recognisable pattern. Applied only by the tests that ask for it
-- (load_bank_d091682.construct), never by the bank load.
--
-- Each case is written the way pantry writes a real one (ingesters/tropical_bank.py
-- at 0393def): the corrected file's ledger row carries meta.correction
-- {prior_sha256, signal, revision}; rows that parse differently are inserted
-- beside the original's at the advisory's revision (the official's = the 5-day
-- zip's corrections + the radii zip's); rows that parse identically are not
-- written at all.
--
--   C1  Simon ep202026 advisory 010 (init 2026-10-09 18Z), corrected with different
--       rows: its 5-day zip at revision 1, the radii zip not, so the points are at
--       revision 1. Revision 1 moves tau 24 (17.5 -> 17.8 N, 125 -> 130 kt) and drops
--       tau 96, which exists only in revision 0. Every other tau is copied as is.
--   C2  Simon's newest PWS issuance (advisory 010, issued 21Z), corrected with
--       different rows: revision 1 raises one printed value by 1 and drops the
--       last place NHC printed (MANZANILLO); every below_1pct cell is copied as is.
--   C3  Isaias al092026 advisory 013 (init 18Z) and its PWS: corrected with
--       IDENTICAL rows (the case of Rachel 052A on Neon, 2026-10-10 07:10Z): the
--       ledger reads corrected, and no row is written.

-- C1 -------------------------------------------------------------------------
INSERT INTO tropical_file_vintage (dataset, source, product, storm_id, vintage_key, status, fetch_ts,
                                   issued_ts, r2_key, byte_size, sha256, source_url, meta)
VALUES ('nhc_advisory_vintage', 'nhc', 'fcst_5day_zip', 'ep202026', '010', 'banked',
        '2026-10-09 21:10:00+00', '2026-10-09 21:00:00+00',
        'constructed/d091682/ep202026/adv010/fcst_5day_zip__d0916821.zip', 27975,
        repeat('d0916821', 8), 'https://www.nhc.noaa.gov/gis/forecast/archive/ep202026_5day_010.zip',
        '{"constructed": "d091682 C1", "correction": {"revision": 1, "prior_sha256": ["41432a197b69a138"], "signal": {"by": ["constructed"]}}}');

INSERT INTO tropical_track_points (storm_id, source, init_ts, advisory, tau, valid_ts, lat, lon,
                                   vmax_kt, mslp_hpa, radii, stage, source_r2_key, revision)
SELECT storm_id, source, init_ts, advisory, tau, valid_ts,
       CASE WHEN tau = 24 THEN 17.8 ELSE lat END, lon,
       CASE WHEN tau = 24 THEN 130 ELSE vmax_kt END, mslp_hpa, radii, stage,
       'constructed/d091682/ep202026/adv010/fcst_5day_zip__d0916821.zip', 1
  FROM tropical_track_points
 WHERE storm_id = 'ep202026' AND source = 'nhc_official' AND advisory = '010' AND revision = 0
   AND tau <> 96;

-- C2 -------------------------------------------------------------------------
INSERT INTO tropical_file_vintage (dataset, source, product, storm_id, vintage_key, status, fetch_ts,
                                   issued_ts, r2_key, byte_size, sha256, source_url, meta)
VALUES ('nhc_advisory_vintage', 'nhc', 'pws', 'ep202026', '010', 'banked',
        '2026-10-09 21:11:00+00', '2026-10-09 21:00:00+00',
        'constructed/d091682/ep202026/adv010/pws__d0916822.txt', 4624,
        repeat('d0916822', 8), 'https://www.nhc.noaa.gov/text/MIAPWSEP5.shtml',
        '{"constructed": "d091682 C2", "correction": {"revision": 1, "prior_sha256": ["636b31ee134099af"], "signal": {"by": ["wmo_bbb"], "wmo_bbb": "CCA"}}}');

INSERT INTO tropical_place_odds (storm_id, issued_ts, source, advisory, place_id, threshold_kt,
                                 radius_km, window_h, kind, value, below_1pct, n_members, scored,
                                 source_r2_key, revision)
SELECT storm_id, issued_ts, source, advisory, place_id, threshold_kt, radius_km, window_h, kind,
       CASE WHEN odds_id = (SELECT min(odds_id) FROM tropical_place_odds
                             WHERE storm_id = 'ep202026' AND source = 'nhc_pws' AND advisory = '010'
                               AND revision = 0 AND NOT below_1pct AND value < 99)
            THEN value + 1 ELSE value END,
       below_1pct, n_members, scored, 'constructed/d091682/ep202026/adv010/pws__d0916822.txt', 1
  FROM tropical_place_odds
 WHERE storm_id = 'ep202026' AND source = 'nhc_pws' AND advisory = '010' AND revision = 0
   AND place_id <> 'MANZANILLO'
 ORDER BY odds_id;

-- C3 -------------------------------------------------------------------------
INSERT INTO tropical_file_vintage (dataset, source, product, storm_id, vintage_key, status, fetch_ts,
                                   issued_ts, r2_key, byte_size, sha256, source_url, meta)
VALUES ('nhc_advisory_vintage', 'nhc', 'fcst_5day_zip', 'al092026', '013', 'banked',
        '2026-10-09 21:12:00+00', '2026-10-09 21:00:00+00',
        'constructed/d091682/al092026/adv013/fcst_5day_zip__d0916823.zip', 29581,
        repeat('d0916823', 8), 'https://www.nhc.noaa.gov/gis/forecast/archive/al092026_5day_013.zip',
        '{"constructed": "d091682 C3", "correction": {"revision": 1, "prior_sha256": ["a175990861fcb802"], "signal": {"by": ["constructed"]}}}'),
       ('nhc_advisory_vintage', 'nhc', 'pws', 'al092026', '013', 'banked',
        '2026-10-09 21:13:00+00', '2026-10-09 21:00:00+00',
        'constructed/d091682/al092026/adv013/pws__d0916824.txt', 5256,
        repeat('d0916824', 8), 'https://www.nhc.noaa.gov/text/MIAPWSAT4.shtml',
        '{"constructed": "d091682 C3", "correction": {"revision": 1, "prior_sha256": ["5aab17f13f23deef"], "signal": {"by": ["wmo_bbb"], "wmo_bbb": "CCA"}}}');
