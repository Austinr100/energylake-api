-- timeseries_values, datasets and sources, from NEON'S OWN LIVE DEFINITION
-- (information_schema + pg_constraint, read read-only 2026-10-17). These three
-- predate the pantry's numbered migrations (the earliest is 003), so there is no
-- migration file to copy them from. The routes read timeseries_values; datasets and
-- sources exist here only to carry its foreign key.

CREATE TABLE sources (
    source_code text NOT NULL,
    source_name text NOT NULL,
    source_type text NOT NULL,
    website text,
    api_base_url text,
    notes text,
    PRIMARY KEY (source_code)
);

CREATE TABLE datasets (
    dataset_code text NOT NULL,
    dataset_name text NOT NULL,
    source_code text NOT NULL,
    description text,
    granularity text,
    value_type text,
    unit text,
    ingester text,
    refresh_cron text,
    backfill_start date,
    series_list text[],
    config jsonb DEFAULT '{}'::jsonb,
    enabled boolean DEFAULT true,
    created_ts timestamptz DEFAULT now(),
    updated_ts timestamptz DEFAULT now(),
    freshness_mode text NOT NULL DEFAULT 'data'::text,
    lifecycle_status text NOT NULL DEFAULT 'active'::text,
    stale_after_override interval,
    meta jsonb,
    PRIMARY KEY (dataset_code),
    CONSTRAINT chk_datasets_freshness_mode CHECK (freshness_mode = ANY (ARRAY['data'::text, 'ingest_success'::text, 'manual'::text])),
    CONSTRAINT chk_datasets_lifecycle_status CHECK (lifecycle_status = ANY (ARRAY['active'::text, 'planned'::text, 'retired'::text])),
    CONSTRAINT datasets_source_code_fkey FOREIGN KEY (source_code) REFERENCES sources(source_code)
);

CREATE TABLE timeseries_values (
    ts timestamptz NOT NULL,
    dataset text NOT NULL,
    series text NOT NULL,
    value numeric,
    meta jsonb DEFAULT '{}'::jsonb,
    ingested_ts timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (ts, dataset, series),
    CONSTRAINT timeseries_values_dataset_fkey FOREIGN KEY (dataset) REFERENCES datasets(dataset_code)
);
CREATE INDEX idx_tsv_series_ts ON timeseries_values (dataset, series, ts DESC);
CREATE INDEX idx_tsv_dataset_ts ON timeseries_values (dataset, ts DESC);
CREATE INDEX idx_tsv_dataset_ingested_ts ON timeseries_values (dataset, ingested_ts DESC);
