-- press_editions as spec d091689 §7 states it (pantry lane d091688 is building
-- the migration from this text). Verbatim; nothing added. The routes' tests
-- and plans run on this table in a throwaway local Postgres.
CREATE TABLE press_editions (
    edition_id bigserial PRIMARY KEY,
    kind text NOT NULL CHECK (kind IN ('daily','weekly','monthly','article')),
    edition_date date NOT NULL, slug text NOT NULL DEFAULT '',
    revision smallint NOT NULL DEFAULT 0 CHECK (revision >= 0),
    schema text NOT NULL CHECK (schema = 'el.edition.v1'),
    headline text NOT NULL, body jsonb NOT NULL, body_text text NOT NULL,
    sha256 text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    writer text NOT NULL, submitted_at timestamptz NOT NULL DEFAULT now(),
    withdrawn_at timestamptz, withdrawn_reason text,
    CONSTRAINT press_editions_identity UNIQUE (kind, edition_date, slug, revision));
