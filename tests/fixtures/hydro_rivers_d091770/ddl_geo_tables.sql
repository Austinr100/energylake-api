-- The geo bank's tables, VERBATIM from the pantry's own migrations.
-- Source: energylake-pantry @ f2a75d16dcc0761f358b4ee8a33790e3d02256ac
--   migrations/302_geo_geometry_bank.sql lines 92-199   (sha256 a149a16b0c60be69c9b2b9fe72283baeb2027ca01116d9fd2a3096d014a842ab)
--   migrations/322_geo_display_publish.sql lines 114-148 (sha256 dbc0287b859def80c74aa7b0cf40cb478807d1cda8140fbbc20bc963fa149415)
-- Only the CREATE TABLE / CREATE INDEX / COMMENT statements are extracted, byte for
-- byte; each migration's BEGIN/COMMIT, its DO $$ pre- and post-checks, its
-- schema_migrations row and its unrelated tables (gas_pipeline_derates) are left out.
-- migrations/308_geo_unit_receipts_refused_runs.sql adds NO DDL (it de-duplicates
-- cwms_catalog receipt rows), so geo_unit_receipts' shape is 302's.

-- ─────────── 302, lines 92-199 ───────────
CREATE TABLE geo_layer_objects (
    object_id          bigserial   PRIMARY KEY,
    layer_code         text        NOT NULL,          -- e.g. eia_ng_pipelines, nhdplus_hr_flowline_sacramento
    source_code        text        NOT NULL,          -- eia | usgs_nhdplus_hr | nid | usgs_wdfn | cdec | cwms | gnis
    source_url         text        NOT NULL,          -- the exact request
    source_vintage     text        NOT NULL,          -- as the publisher states it (e.g. '202001', 'refreshed October, 2022')
    source_sha256      text        NOT NULL,          -- of the publisher's bytes
    source_bytes       bigint      NOT NULL,
    r2_key             text        NOT NULL UNIQUE,
    r2_sha256          text        NOT NULL,
    crs_as_published   text        NOT NULL,          -- 'EPSG:4326', 'EPSG:3857 (wkid 102100)', ...
    simplification     text        NOT NULL DEFAULT 'none',
    tolerance_m        numeric,                        -- NULL iff simplification = 'none'
    derived_from       bigint      REFERENCES geo_layer_objects(object_id),   -- a derived object's first source
    role               text        NOT NULL,          -- A2: 'source' (publisher's bytes) | 'derived'
    derived_from_keys  text[],                         -- A2: every source r2_key, in order (derived only)
    feature_count      integer     NOT NULL,
    vertex_count       bigint      NOT NULL,
    fetched_at         timestamptz NOT NULL,
    terms_ref          text        NOT NULL,          -- recon § or URL of the quoted terms
    CONSTRAINT glo_tolerance_ck CHECK ((simplification = 'none') = (tolerance_m IS NULL)),
    CONSTRAINT glo_derived_ck   CHECK ((simplification = 'none') OR derived_from IS NOT NULL),
    CONSTRAINT glo_role_ck      CHECK (role IN ('source','derived')
                                       AND (role = 'derived') = (derived_from IS NOT NULL)
                                       AND (role = 'derived') = (coalesce(cardinality(derived_from_keys), 0) > 0))
);
CREATE INDEX ix_glo_layer ON geo_layer_objects (layer_code, role, fetched_at DESC);   -- A2
COMMENT ON TABLE geo_layer_objects IS
    'd091701 (302): registry of R2 geometry objects. Vertices are published vertices; a simplification '
    'records its tolerance and points at the object it was derived from (D-09-25-202).';

-- 2 ─ published records the rooms place ───────────────────────────────────
CREATE TABLE geo_records (
    record_id          bigserial   PRIMARY KEY,
    kind               text        NOT NULL,          -- dam | storage_field | gauge | reservoir_station | cwms_project | place | compressor | receipt_point
    source_code        text        NOT NULL,
    source_key         text        NOT NULL,          -- NID federalId, EIA ID, USGS-xxxxxxxx, CDEC station, CWMS location, GNIS feature_id
    name               text        NOT NULL,
    lon                double precision,              -- publisher's number; NULL when the record publishes none
    lat                double precision,
    position_basis     text        NOT NULL,          -- 'published lon/lat' | 'published, datum NAD27 per source' | 'none published'
    attrs              jsonb       NOT NULL DEFAULT '{}'::jsonb,   -- every published field, verbatim keys
    source_sha256      text        NOT NULL,          -- of the response the record was read from
    record_sha256      text        NOT NULL,          -- A3: of the record itself (kind, key, name, lon, lat, basis, attrs)
    fetched_at         timestamptz NOT NULL,          -- first banked
    CONSTRAINT gr_kind_ck CHECK (kind IN ('dam','storage_field','gauge','reservoir_station','cwms_project',
                                          'place','compressor','receipt_point')),
    CONSTRAINT gr_pos_ck  CHECK ((lon IS NULL) = (lat IS NULL)
                                 AND ((lon IS NULL) = (position_basis = 'none published'))),
    CONSTRAINT gr_key_uq  UNIQUE (kind, source_code, source_key, record_sha256)   -- A3
);
CREATE INDEX ix_gr_kind_key ON geo_records (kind, source_code, source_key, fetched_at DESC);
COMMENT ON TABLE geo_records IS
    'd091701 (302): published point records (dams, storage fields, gauges, stations, places). No row is '
    'positioned by us: a record with no published coordinate carries none (D-09-25-202).';

-- 3 ─ crosswalk decisions ──────────────────────────────────────────────────
CREATE TABLE geo_crosswalk (
    xw_id              bigserial   PRIMARY KEY,
    rule_code          text        NOT NULL,          -- C1 | C2 | C3 | ...
    rule_version       text        NOT NULL,          -- e.g. 'd091701.1' (the lib function's text hash in evidence)
    left_kind          text        NOT NULL,          -- 'pantry_series' | 'cdec_station' | ...
    left_key           text        NOT NULL,          -- e.g. 'socalgas_storage_daily/aliso_canyon'
    right_source       text,
    right_key          text,                           -- NULL for miss / non_spatial / aggregate
    outcome            text        NOT NULL,
    distance_m         numeric,
    evidence           text        NOT NULL,
    status             text        NOT NULL DEFAULT 'proposed',
    decided_at         timestamptz NOT NULL DEFAULT now(),
    confirmed_by       text,
    CONSTRAINT gx_outcome_ck CHECK (outcome IN ('match','key_uncorroborated','operator_only','ambiguous',
                                                'miss','non_spatial','aggregate')),
    CONSTRAINT gx_status_ck  CHECK (status IN ('proposed','confirmed','rejected')),
    CONSTRAINT gx_confirm_ck CHECK ((status = 'proposed') = (confirmed_by IS NULL)),
    CONSTRAINT gx_right_ck   CHECK ((outcome IN ('miss','non_spatial','aggregate')) = (right_key IS NULL))
);
CREATE UNIQUE INDEX uq_gx_rule_left ON geo_crosswalk (rule_code, rule_version, left_key);
COMMENT ON TABLE geo_crosswalk IS
    'd091701 (302): one row per rule decision. proposed until a person confirms; only outcome = match '
    'and status = confirmed may place a marker.';

-- 4 ─ unit receipts (D-08-02-R) ────────────────────────────────────────────
CREATE TABLE geo_unit_receipts (
    receipt_id         bigserial   PRIMARY KEY,
    subject            text        NOT NULL,          -- dataset/series or source field, e.g. 'cwms:TDA.Flow-Out.Ave.1Hour.1Hour.CBT-REV'
    unit_tag           text,                           -- what the source CLAIMS (recorded, never trusted)
    unit_settled       text        NOT NULL,          -- what the arbiter settles
    arbiter_kind       text        NOT NULL,          -- 'second_source' | 'lattice'
    arbiter_ref        text        NOT NULL,          -- the other observation or the lattice constant
    window_start       timestamptz,
    window_end         timestamptz,
    n_obs              integer     NOT NULL,
    statistic          numeric,                        -- the ratio (second_source) or lattice statistic; A4: NULL = nothing measured
    expected           numeric,                        -- e.g. 35.3147 for ft3/m3; 1.0 for same unit
    tolerance          numeric,
    verdict            text        NOT NULL,
    measured_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT gu_kind_ck    CHECK (arbiter_kind IN ('second_source','lattice')),
    CONSTRAINT gu_verdict_ck CHECK (verdict IN ('settled','contradicted','inconclusive')),
    CONSTRAINT gu_verdict_math_ck CHECK (verdict <> 'settled' OR abs(statistic - expected) <= tolerance),
    CONSTRAINT gu_numbers_ck CHECK (verdict = 'inconclusive'
                                    OR (statistic IS NOT NULL AND expected IS NOT NULL AND tolerance IS NOT NULL)),  -- A4
    CONSTRAINT gu_receipt_uq UNIQUE NULLS NOT DISTINCT (subject, arbiter_kind, arbiter_ref, window_start, window_end)  -- A4
);
COMMENT ON TABLE geo_unit_receipts IS
    'd091701 (302): D-08-02-R. A unit is settled only by a row here with verdict settled; a unit tag alone is not a receipt.';


-- ─────────── 322, lines 114-148 ───────────
CREATE TABLE geo_display_objects (
    object_id     bigint      PRIMARY KEY REFERENCES geo_layer_objects(object_id),
    display_key   text        UNIQUE NOT NULL,
    sha256        text        NOT NULL,
    bytes         bigint      NOT NULL,
    content_type  text        NOT NULL,
    published_at  timestamptz NOT NULL,
    CONSTRAINT gdo_sha_ck   CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT gdo_key_ck   CHECK (display_key ~ ('^geo/[a-z0-9_]+/' || sha256 || '\.(geojson|json)$')),
    CONSTRAINT gdo_bytes_ck CHECK (bytes > 0),
    CONSTRAINT gdo_ct_ck    CHECK (content_type = 'application/json; charset=utf-8')
);
COMMENT ON TABLE geo_display_objects IS
    'd091769 (322): one row per banked geo object (geo_layer_objects role derived) published to the display bucket '
    '(display.energylake.io/<display_key>), byte for byte, immutable. sha256 = geo_layer_objects.r2_sha256: asserted '
    'by scripts/geo_publish.py before every put (no CHECK can read the second table). Object first, then this row.';

-- 2. the rivers registry -----------------------------------------------------
CREATE TABLE geo_display_registry (
    registry_key  text        PRIMARY KEY,
    sha256        text        UNIQUE NOT NULL,
    bytes         bigint      NOT NULL,
    published_at  timestamptz NOT NULL,
    schema        text        NOT NULL,
    CONSTRAINT gdr_sha_ck    CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT gdr_key_ck    CHECK (registry_key = 'geo/rivers/registry.' || substr(sha256, 1, 8) || '.json'),
    CONSTRAINT gdr_bytes_ck  CHECK (bytes > 0),
    CONSTRAINT gdr_schema_ck CHECK (schema ~ '^geo\.rivers\.v[1-9][0-9]*$')
);
CREATE INDEX ix_gdr_published ON geo_display_registry (published_at DESC);
COMMENT ON TABLE geo_display_registry IS
    'd091769 (322): one row per published rivers registry (geo/rivers/registry.<sha8>.json on the display bucket), '
    'built by scripts/geo_publish.py from geo.nhd.REACHES / geo.cwms.BASINS and the published objects; published once '
    'per change (sha256 UNIQUE). The registry in force is the newest published_at.';

