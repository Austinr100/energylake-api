-- d091546 §2.1 — the market-clock statement, before and after, for the
-- architect to run on Neon (this lane's box cannot reach it: d091542 Gate 0).
--
-- Two windows each:
--   EMPTY  target trade date with no day-ahead rows yet. Run it any time
--          before ~13:00 PT for TOMORROW. The literals below are for target
--          2026-10-03 PT (UTC day 2026-10-03 07:00 → 2026-10-04 07:00), which
--          is empty until CAISO publishes on 10-02. Shift the dates if needed.
--   FULL   a published trade date: 2026-10-01 PT.
--
-- Every block is its own transaction and ends in ROLLBACK. Each carries a
-- statement_timeout so a bad plan cannot hold the console (or a connection).
--
-- STOP-Q: in every AFTER plan, each read of timeseries_values must be an
-- Index Scan / Index Only Scan whose Index Cond names `series` (expected:
-- idx_tsv_series_ts). Any Seq Scan, or any scan of
-- idx_tsv_dataset_ingested_ts, is STOP-Q: report the subquery, do not ship.


-- ── 1. BEFORE, EMPTY window ─────────────────────────────────────────────────
-- Plan only: run with ANALYZE it does not finish in 60 s (Gate 0, 12:30Z).
BEGIN;
SET LOCAL statement_timeout = '10s';
EXPLAIN (VERBOSE, COSTS)
SELECT
  (SELECT count(*) FROM timeseries_values
     WHERE dataset = 'caiso_lmp_da_hourly' AND series = 'SP15'
       AND ts >= '2026-10-03 07:00+00' AND ts < '2026-10-04 07:00+00'
       AND value IS NOT NULL)                                   AS da_hours,
  (SELECT max(ingested_ts) FROM timeseries_values
     WHERE dataset = 'caiso_lmp_da_hourly'
       AND ts >= '2026-10-03 07:00+00' AND ts < '2026-10-04 07:00+00')
                                                                AS da_published_at,
  (SELECT value FROM timeseries_values
     WHERE dataset = 'caiso_lmp_da_hourly' AND series = 'SP15'
       AND ts = '2026-10-03 23:00+00'
       AND value IS NOT NULL LIMIT 1)                           AS sp15_da_val,
  (SELECT ts FROM timeseries_values
     WHERE dataset = 'caiso_lmp_rt_15min' AND series = 'SP15' AND value IS NOT NULL
     ORDER BY ts DESC LIMIT 1)                                  AS fmm_ts,
  (SELECT value FROM timeseries_values
     WHERE dataset = 'caiso_lmp_rt_15min' AND series = 'SP15' AND value IS NOT NULL
     ORDER BY ts DESC LIMIT 1)                                  AS fmm_val;
ROLLBACK;


-- ── 2. BEFORE, FULL window ──────────────────────────────────────────────────
-- After publication the backward walk finds a row near the top, so this one
-- finishes; it is here for the comparison.
BEGIN;
SET LOCAL statement_timeout = '60s';
EXPLAIN (ANALYZE, BUFFERS, VERBOSE)
SELECT
  (SELECT count(*) FROM timeseries_values
     WHERE dataset = 'caiso_lmp_da_hourly' AND series = 'SP15'
       AND ts >= '2026-10-01 07:00+00' AND ts < '2026-10-02 07:00+00'
       AND value IS NOT NULL)                                   AS da_hours,
  (SELECT max(ingested_ts) FROM timeseries_values
     WHERE dataset = 'caiso_lmp_da_hourly'
       AND ts >= '2026-10-01 07:00+00' AND ts < '2026-10-02 07:00+00')
                                                                AS da_published_at,
  (SELECT value FROM timeseries_values
     WHERE dataset = 'caiso_lmp_da_hourly' AND series = 'SP15'
       AND ts = '2026-10-01 23:00+00'
       AND value IS NOT NULL LIMIT 1)                           AS sp15_da_val,
  (SELECT ts FROM timeseries_values
     WHERE dataset = 'caiso_lmp_rt_15min' AND series = 'SP15' AND value IS NOT NULL
     ORDER BY ts DESC LIMIT 1)                                  AS fmm_ts,
  (SELECT value FROM timeseries_values
     WHERE dataset = 'caiso_lmp_rt_15min' AND series = 'SP15' AND value IS NOT NULL
     ORDER BY ts DESC LIMIT 1)                                  AS fmm_val;
ROLLBACK;


-- ── 3. AFTER, EMPTY window ── the statement as shipped (main._MARKET_CLOCK_SQL)
BEGIN;
SET LOCAL statement_timeout = '3s';
EXPLAIN (ANALYZE, BUFFERS, VERBOSE)
SELECT da.da_hours, da.da_published_at, da.da_first_ingested_at,
       (SELECT value FROM timeseries_values
          WHERE dataset = 'caiso_lmp_da_hourly' AND series = 'SP15'
            AND ts = '2026-10-03 23:00+00'
            AND value IS NOT NULL LIMIT 1)                 AS sp15_da_val,
       fmm.ts AS fmm_ts, fmm.value AS fmm_val
FROM (SELECT count(value)     AS da_hours,
             max(ingested_ts) AS da_published_at,
             min(ingested_ts) AS da_first_ingested_at
        FROM timeseries_values
       WHERE dataset = 'caiso_lmp_da_hourly' AND series = 'SP15'
         AND ts >= '2026-10-03 07:00+00' AND ts < '2026-10-04 07:00+00') AS da
LEFT JOIN LATERAL
     (SELECT ts, value FROM timeseries_values
       WHERE dataset = 'caiso_lmp_rt_15min' AND series = 'SP15' AND value IS NOT NULL
       ORDER BY ts DESC LIMIT 1)                           AS fmm ON true;
ROLLBACK;


-- ── 4. AFTER, FULL window ───────────────────────────────────────────────────
BEGIN;
SET LOCAL statement_timeout = '3s';
EXPLAIN (ANALYZE, BUFFERS, VERBOSE)
SELECT da.da_hours, da.da_published_at, da.da_first_ingested_at,
       (SELECT value FROM timeseries_values
          WHERE dataset = 'caiso_lmp_da_hourly' AND series = 'SP15'
            AND ts = '2026-10-01 23:00+00'
            AND value IS NOT NULL LIMIT 1)                 AS sp15_da_val,
       fmm.ts AS fmm_ts, fmm.value AS fmm_val
FROM (SELECT count(value)     AS da_hours,
             max(ingested_ts) AS da_published_at,
             min(ingested_ts) AS da_first_ingested_at
        FROM timeseries_values
       WHERE dataset = 'caiso_lmp_da_hourly' AND series = 'SP15'
         AND ts >= '2026-10-01 07:00+00' AND ts < '2026-10-02 07:00+00') AS da
LEFT JOIN LATERAL
     (SELECT ts, value FROM timeseries_values
       WHERE dataset = 'caiso_lmp_rt_15min' AND series = 'SP15' AND value IS NOT NULL
       ORDER BY ts DESC LIMIT 1)                           AS fmm ON true;
ROLLBACK;


-- ── 5. The answer, FULL window: M5 on real rows ─────────────────────────────
-- Expect da_hours 24, da_first_ingested_at <= da_published_at, and (per §0)
-- 12 distinct ingest stamps with the newest 2026-10-01 00:49Z.
SELECT count(value) AS da_hours,
       min(ingested_ts) AS da_first_ingested_at,
       max(ingested_ts) AS da_published_at,
       count(DISTINCT ingested_ts) AS distinct_ingests
  FROM timeseries_values
 WHERE dataset = 'caiso_lmp_da_hourly' AND series = 'SP15'
   AND ts >= '2026-10-01 07:00+00' AND ts < '2026-10-02 07:00+00';
