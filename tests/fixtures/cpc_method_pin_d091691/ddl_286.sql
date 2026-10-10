-- pantry migrations/286_cpc_outlook_curves_and_verdicts.sql at e71fa23, sections 1-3
-- verbatim (the two tables, their indexes and comments, the view). Not the
-- pre-checks, the datasets rows or the ledger row. Loaded by tests/load_bank_d091691.py.

-- 1. cpc_curve_verdicts ------------------------------------------------------
CREATE TABLE cpc_curve_verdicts (
    verdict_id            BIGSERIAL PRIMARY KEY,
    backtest_version      TEXT     NOT NULL,   -- cpcv_{truth_frontier}_{inputs digest}
    scored_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),   -- one per version (one transaction)
    truth_frontier        DATE     NOT NULL,
    method_version        TEXT     NOT NULL,   -- cpc_curves_v1
    method_hash           TEXT     NOT NULL,   -- e99c71234013
    rules_version         TEXT     NOT NULL,   -- the verdict rules (buckets, floors)
    rules_hash            TEXT     NOT NULL,
    product               TEXT     NOT NULL,
    place_kind            TEXT     NOT NULL,   -- 'station' | 'region'
    place                 TEXT     NOT NULL,   -- GHCN id, or region name
    weighting             TEXT     NOT NULL DEFAULT '',   -- '' on station rows
    season                TEXT     NOT NULL,   -- of valid_start's month
    strength              TEXT     NOT NULL,   -- 'weak' | 'moderate' | 'strong'
    min_n_eff             DOUBLE PRECISION NOT NULL,   -- the thin floor this version used
    -- the strength cell itself (product x place x season x strength)
    n                     INTEGER  NOT NULL,   -- scored issuances
    n_eff                 DOUBLE PRECISION,    -- of the CRPS difference, Bartlett 14 lags
    skill                 DOUBLE PRECISION,    -- CRPS skill % over equal odds, window-mean tavg
    t                     DOUBLE PRECISION,
    strength_verdict      TEXT     NOT NULL,   -- the cell's OWN skill test; reported, not gating
    share_p5_p95          DOUBLE PRECISION,
    share_p25_p75         DOUBLE PRECISION,
    -- clause 3: the product x place x season cell, every strength
    season_n              INTEGER  NOT NULL,
    season_n_eff          DOUBLE PRECISION,
    season_skill          DOUBLE PRECISION,
    season_t              DOUBLE PRECISION,
    verdict               TEXT     NOT NULL,   -- 'beats' | 'does_not_beat' | 'too_thin'
    -- clause 5
    history_years_in_base INTEGER  NOT NULL,   -- years 1991-2020 with >= 330 complete days (region: min over members)
    -- clause 4
    band_basis            TEXT     NOT NULL,   -- 'cell' | 'pooled_stations' | 'none'
    band_basis_n          INTEGER,
    band_basis_n_eff      DOUBLE PRECISION,
    band_share            DOUBLE PRECISION,
    band_claim            TEXT,                -- 'about_9_in_10' | 'measured'
    band_sentence         TEXT,
    drawable              BOOLEAN  NOT NULL,

    CONSTRAINT uniq_ccv_cell UNIQUE
        (product, place_kind, place, weighting, season, strength, backtest_version),
    CONSTRAINT ccv_domain_ck CHECK (
        product IN ('610temp', '814temp')
        AND season IN ('DJF', 'MAM', 'JJA', 'SON')
        AND strength IN ('weak', 'moderate', 'strong')
        AND verdict IN ('beats', 'does_not_beat', 'too_thin')
        AND strength_verdict IN ('beats', 'does_not_beat', 'too_thin')
        AND band_basis IN ('cell', 'pooled_stations', 'none')
        AND (band_claim IS NULL OR band_claim IN ('about_9_in_10', 'measured'))
        AND min_n_eff > 0 AND method_hash ~ '^[0-9a-f]{12}$'),
    CONSTRAINT ccv_place_ck CHECK (
        (place_kind = 'station' AND weighting = '')
        OR (place_kind = 'region' AND weighting <> '')),
    CONSTRAINT ccv_counts_ck CHECK (
        n >= 0 AND season_n >= n AND history_years_in_base BETWEEN 0 AND 30
        AND (n_eff IS NULL OR (n_eff > 0 AND n_eff <= n + 1e-9))
        AND (season_n_eff IS NULL OR (season_n_eff > 0 AND season_n_eff <= season_n + 1e-9))
        AND (n = 0) = (share_p5_p95 IS NULL) AND (n = 0) = (share_p25_p75 IS NULL)
        AND (share_p5_p95 IS NULL OR share_p5_p95 BETWEEN 0 AND 1)
        AND (share_p25_p75 IS NULL OR share_p25_p75 BETWEEN 0 AND 1)),
    -- CLAUSE 3 AS A TABLE PROPERTY. The verdict is the season cell's skill test,
    -- with its floor: too thin below min_n_eff, beats only at t >= 1.96.
    CONSTRAINT ccv_verdict_ck CHECK (
        (verdict = 'too_thin') = (season_n_eff IS NULL OR season_n_eff < min_n_eff)
        AND (verdict = 'beats') = (season_n_eff >= min_n_eff AND season_t >= 1.96)
        AND (strength_verdict = 'too_thin') = (n_eff IS NULL OR n_eff < min_n_eff)
        AND (strength_verdict = 'beats') = (n_eff >= min_n_eff AND t >= 1.96)),
    -- The band's basis: the cell itself when it is judgeable, else the pooled
    -- verdict stations' cell for the same product, season and strength, else
    -- nothing. Never "drawable" by default.
    CONSTRAINT ccv_band_basis_ck CHECK (
        (band_basis = 'none') = (band_share IS NULL)
        AND (band_basis = 'none') = (band_claim IS NULL)
        AND (band_basis = 'none') = (band_sentence IS NULL)
        AND (band_basis = 'none') = (band_basis_n_eff IS NULL)
        AND (band_basis = 'none') = (band_basis_n IS NULL)
        AND (band_basis = 'none' OR (band_basis_n_eff >= min_n_eff AND band_share BETWEEN 0 AND 1))
        AND (band_basis <> 'cell' OR (band_share = share_p5_p95 AND band_basis_n_eff = n_eff
                                      AND band_basis_n = n))
        AND (band_basis <> 'pooled_stations' OR strength_verdict = 'too_thin')),
    -- CLAUSE 4 AS A TABLE PROPERTY. "About 9 in 10" exactly where the band held
    -- 0.85..0.95, and 'measured' everywhere else; a measured sentence never
    -- says "9 in 10".
    CONSTRAINT ccv_claim_ck CHECK (
        band_claim IS NULL
        OR (band_claim = 'about_9_in_10') = (band_share BETWEEN 0.85 AND 0.95)),
    CONSTRAINT ccv_sentence_ck CHECK (
        band_claim IS NULL
        OR (band_claim = 'about_9_in_10' AND strpos(band_sentence, 'about 9 in 10 ') > 0)
        OR (band_claim = 'measured' AND strpos(band_sentence, ' 9 in 10') = 0)),
    -- CLAUSES 3 + 4 + 5: drawable exactly when the season cell beats equal odds,
    -- the place has 30 base years, and the band has a judged claim.
    CONSTRAINT ccv_drawable_ck CHECK (
        drawable = (verdict = 'beats' AND history_years_in_base >= 30
                    AND band_claim IS NOT NULL))
);

CREATE INDEX idx_ccv_version ON cpc_curve_verdicts (method_hash, scored_at DESC, backtest_version);

COMMENT ON TABLE cpc_curve_verdicts IS
    'D-09-25-172: whether a CPC curve may be drawn, per (product, place, season, strength of CPC''s odds) and backtest_version. verdict = clause 3, the product x place x season skill test over equal-odds history (all strengths; too_thin under min_n_eff). The strength cell''s own n / n_eff / skill / t / shares are reported beside it; its band share (or, where the cell is too thin, the pooled verdict stations'' cell) gives band_claim: about_9_in_10 only at 0.85..0.95, else measured with the share in words (band_sentence). drawable = verdict beats AND 30 base years AND a band claim, by CHECK. Written weekly by scripts/cpc_curves_writer.py verdicts as a NEW backtest_version; old versions are kept. Migration 286.';

-- 2. cpc_outlook_curves ------------------------------------------------------
CREATE TABLE cpc_outlook_curves (
    curve_row_id          BIGSERIAL PRIMARY KEY,
    product               TEXT     NOT NULL,
    issued_date           DATE     NOT NULL,
    valid_start           DATE     NOT NULL,
    valid_end             DATE     NOT NULL,
    place_kind            TEXT     NOT NULL,
    place                 TEXT     NOT NULL,
    weighting             TEXT     NOT NULL DEFAULT '',
    day_index             SMALLINT,            -- NULL on the window row
    target_date           DATE,                -- NULL on the window row
    -- reweighted; window row: tavg = window-mean tavg, hdd/cdd = window totals
    tavg_p05 DOUBLE PRECISION NOT NULL, tavg_p25 DOUBLE PRECISION NOT NULL,
    tavg_p50 DOUBLE PRECISION NOT NULL, tavg_p75 DOUBLE PRECISION NOT NULL,
    tavg_p95 DOUBLE PRECISION NOT NULL,
    hdd_p05  DOUBLE PRECISION NOT NULL, hdd_p25  DOUBLE PRECISION NOT NULL,
    hdd_p50  DOUBLE PRECISION NOT NULL, hdd_p75  DOUBLE PRECISION NOT NULL,
    hdd_p95  DOUBLE PRECISION NOT NULL,
    cdd_p05  DOUBLE PRECISION NOT NULL, cdd_p25  DOUBLE PRECISION NOT NULL,
    cdd_p50  DOUBLE PRECISION NOT NULL, cdd_p75  DOUBLE PRECISION NOT NULL,
    cdd_p95  DOUBLE PRECISION NOT NULL,
    -- equal-odds history, the same years at 1/N
    eq_tavg_p05 DOUBLE PRECISION NOT NULL, eq_tavg_p50 DOUBLE PRECISION NOT NULL,
    eq_tavg_p95 DOUBLE PRECISION NOT NULL,
    eq_hdd_p05  DOUBLE PRECISION NOT NULL, eq_hdd_p50  DOUBLE PRECISION NOT NULL,
    eq_hdd_p95  DOUBLE PRECISION NOT NULL,
    eq_cdd_p05  DOUBLE PRECISION NOT NULL, eq_cdd_p50  DOUBLE PRECISION NOT NULL,
    eq_cdd_p95  DOUBLE PRECISION NOT NULL,
    n_history_years       INTEGER  NOT NULL,
    history_first         INTEGER  NOT NULL,
    history_last          INTEGER  NOT NULL,
    empty_class           BOOLEAN  NOT NULL,
    member_odds           JSONB    NOT NULL,   -- [{station, weight, category, level, p_a, p_n, p_b, reading}]
    season                TEXT     NOT NULL,   -- the verdict cell this row is read from
    strength              TEXT     NOT NULL,
    reading               TEXT     NOT NULL,   -- 'midpoint' (the backtest's primary)
    history               TEXT     NOT NULL,   -- 'all' = 1991 .. Y-1
    method_version        TEXT     NOT NULL,
    method_hash           TEXT     NOT NULL,
    writer_version        TEXT     NOT NULL,
    source_content_sha256 TEXT     NOT NULL,   -- cpc_outlook_vintage.content_sha256, checked on read
    source_r2_key         TEXT     NOT NULL,
    source_format_epoch   TEXT,
    notes                 TEXT[]   NOT NULL DEFAULT '{}',
    written_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uniq_coc_row UNIQUE NULLS NOT DISTINCT
        (product, issued_date, place_kind, place, weighting, day_index, method_version),
    CONSTRAINT coc_domain_ck CHECK (
        product IN ('610temp', '814temp')
        AND strength IN ('weak', 'moderate', 'strong')
        AND method_hash ~ '^[0-9a-f]{12}$'
        AND source_content_sha256 ~ '^[0-9a-f]{64}$'
        AND jsonb_typeof(member_odds) = 'array' AND jsonb_array_length(member_odds) >= 1),
    CONSTRAINT coc_place_ck CHECK (
        (place_kind = 'station' AND weighting = '')
        OR (place_kind = 'region' AND weighting <> '')),
    CONSTRAINT coc_reading_ck CHECK (reading = 'midpoint' AND history = 'all'),
    CONSTRAINT coc_window_ck CHECK (
        valid_start > issued_date
        AND ((product = '610temp' AND valid_end = valid_start + 4)
             OR (product = '814temp' AND valid_end = valid_start + 6))),
    CONSTRAINT coc_day_ck CHECK (
        (day_index IS NULL) = (target_date IS NULL)
        AND (day_index IS NULL
             OR (day_index >= 0 AND target_date = valid_start + day_index
                 AND target_date <= valid_end))),
    CONSTRAINT coc_season_ck CHECK (season = CASE
        WHEN EXTRACT(MONTH FROM valid_start) IN (12, 1, 2) THEN 'DJF'
        WHEN EXTRACT(MONTH FROM valid_start) IN (3, 4, 5) THEN 'MAM'
        WHEN EXTRACT(MONTH FROM valid_start) IN (6, 7, 8) THEN 'JJA'
        ELSE 'SON' END),
    -- THE C2 WALL AS A TABLE PROPERTY: history is 1991 .. Y-1, never Y or later.
    CONSTRAINT coc_history_ck CHECK (
        history_first >= 1991
        AND history_last < EXTRACT(YEAR FROM valid_start)
        AND n_history_years BETWEEN 1 AND history_last - history_first + 1),
    CONSTRAINT coc_order_ck CHECK (
        tavg_p05 <= tavg_p25 AND tavg_p25 <= tavg_p50 AND tavg_p50 <= tavg_p75 AND tavg_p75 <= tavg_p95
        AND hdd_p05 <= hdd_p25 AND hdd_p25 <= hdd_p50 AND hdd_p50 <= hdd_p75 AND hdd_p75 <= hdd_p95
        AND cdd_p05 <= cdd_p25 AND cdd_p25 <= cdd_p50 AND cdd_p50 <= cdd_p75 AND cdd_p75 <= cdd_p95
        AND eq_tavg_p05 <= eq_tavg_p50 AND eq_tavg_p50 <= eq_tavg_p95
        AND eq_hdd_p05 <= eq_hdd_p50 AND eq_hdd_p50 <= eq_hdd_p95
        AND eq_cdd_p05 <= eq_cdd_p50 AND eq_cdd_p50 <= eq_cdd_p95
        AND hdd_p05 >= 0 AND cdd_p05 >= 0 AND eq_hdd_p05 >= 0 AND eq_cdd_p05 >= 0)
);

CREATE INDEX idx_coc_issued ON cpc_outlook_curves (product, issued_date DESC);

COMMENT ON TABLE cpc_outlook_curves IS
    'D-09-25-172 (1)(2): per CPC 6-10 / 8-14 day temperature issuance and place (a station or a degree_day_region_weights vector with 30 base years), the history at the place 1991..Y-1 reweighted by CPC''s published odds (scripts/cpc_curves.py, cpc_curves_v1): P5/P25/P50/P75/P95 of tavg, HDD and CDD per day (day_index 0..n-1) and over the window (day_index NULL: window-mean tavg, HDD and CDD totals), beside the equal-odds P5/P50/P95. A vintage: INSERT ... ON CONFLICT DO NOTHING, never updated. Read ONLY through v_cpc_curves_drawable. Never a model forecast: nothing here is written to dd_blend_forecast or station_degree_days_forecast. Migration 286.';

-- 3. the only sanctioned read ------------------------------------------------
CREATE VIEW v_cpc_curves_drawable AS
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
           || ' day odds' AS label,
       v.band_claim, v.band_share, v.band_sentence, v.band_basis,
       v.backtest_version, v.truth_frontier AS verdict_truth_frontier,
       v.season_n AS verdict_n, v.season_n_eff AS verdict_n_eff,
       v.season_skill AS verdict_skill, v.season_t AS verdict_t,
       v.n AS strength_n, v.n_eff AS strength_n_eff,
       v.history_years_in_base
  FROM cpc_outlook_curves c
  JOIN newest nw ON nw.method_hash = c.method_hash
  JOIN cpc_curve_verdicts v
    ON v.backtest_version = nw.backtest_version AND v.method_hash = c.method_hash
   AND v.product = c.product AND v.place_kind = c.place_kind AND v.place = c.place
   AND v.weighting = c.weighting AND v.season = c.season AND v.strength = c.strength
 WHERE v.drawable;

COMMENT ON VIEW v_cpc_curves_drawable IS
    'D-09-25-172: the CPC curve rows that may be drawn — the cell''s verdict in the NEWEST backtest_version of the curve''s method_hash says drawable (clause 3 beats, 30 base years, a judged band claim). Every row carries band_claim, band_share, band_sentence and the fixed label. A curve whose cell has no verdict in that version is not returned. Nothing downstream reads cpc_outlook_curves or cpc_curve_verdicts directly. Beside the blend (v_dd_blend_drawable), never in its place.';

