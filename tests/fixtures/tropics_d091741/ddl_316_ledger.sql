-- The migration ledger pantry 316 reads and writes, as Neon holds it (information_schema.columns,
-- read 2026-10-15 21:5xZ by d091746): the test cluster has no other. 316's pre-check (a)
-- requires 314 and 315 on it and refuses 316; its last statement inserts 316 ON CONFLICT
-- (version). The two rows are banked verbatim (bank/migrations.jsonl.gz). Not a migration.
CREATE TABLE schema_migrations (
    version      integer PRIMARY KEY,
    filename     text,
    status       text NOT NULL,
    applied_at   timestamptz,
    attestation  text NOT NULL,
    note         text,
    recorded_at  timestamptz NOT NULL DEFAULT now()
);
