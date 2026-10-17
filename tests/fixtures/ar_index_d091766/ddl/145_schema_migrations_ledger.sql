-- 145_schema_migrations_ledger.sql
--
-- Root-cause fix for the applied-state-with-no-committed-source class.
-- Prod had NO record of which migrations ran. This creates one.
--
-- APPLY ORDER: 145 before 146. 146 backfills the roster into this table
-- and will fail on a missing relation if run first.
--
-- Not a snapshot migration: no isolation-level line required.

BEGIN;

CREATE TABLE IF NOT EXISTS schema_migrations (
  version      integer PRIMARY KEY,
  filename     text,
  status       text NOT NULL,
  applied_at   timestamptz,
  attestation  text NOT NULL,
  note         text,
  recorded_at  timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT schema_migrations_status_ck
    CHECK (status IN ('committed','orphan','reserved','declared','gap'))
);

COMMENT ON TABLE schema_migrations IS
  'Migration ledger. applied_at IS NULL means apply state is UNVERIFIED, not unapplied — '
  'backfilled rows predate this table and cannot be attested. Every migration from 145 '
  'forward inserts its own row with applied_at = now().';

COMMENT ON COLUMN schema_migrations.status IS
  'committed = file present in repo. orphan = PROVEN live in prod with no committed source. '
  'reserved = number claimed, unwritten. declared = specced, unapplied. '
  'gap = number unaccounted for; apply state unknown.';

COMMENT ON COLUMN schema_migrations.attestation IS
  'How this row was established. Names the instrument, not the conclusion.';

-- Special cases only. The 123-file committed roster is generated from disk
-- by 146 rather than transcribed here — the repo is authoritative for
-- filenames and a hand-typed roster would be a second source of truth.
INSERT INTO schema_migrations (version, filename, status, applied_at, attestation, note) VALUES
  (84,  NULL, 'gap',      NULL, 'roster-diff-2026-08-05', 'absent from repo AND from the architect ledger; origin unknown'),
  (119, NULL, 'reserved', NULL, 'ledger-2026-08-05',      'sibling session: caiso_load_hourly fix'),
  (123, NULL, 'declared', NULL, 'ledger-2026-08-05',      'specced, unapplied'),
  (131, NULL, 'reserved', NULL, 'ledger-2026-08-05',      NULL),
  (133, NULL, 'gap',      NULL, 'roster-diff-2026-08-05', 'absent from repo. Prod-applied status UNPROVEN: the original orphan finding rested on alarm_semantics + FIRED_FAILING/ALARM_ACTIVE, and the 2026-08-05 sweep found alarm_semantics only inside 144''s own committed view and the other two tokens in zero views, zero enums, zero check constraints. Promote to orphan or committed only on the inventory diff.'),
  (134, NULL, 'gap',      NULL, 'roster-diff-2026-08-05', 'absent from repo. Prod-applied status UNPROVEN — see 133.'),
  (135, NULL, 'reserved', NULL, 'ledger-2026-08-05',      NULL),
  (137, NULL, 'gap',      NULL, 'ledger-2026-08-05',      'known open gap'),
  (145, '145_schema_migrations_ledger.sql', 'committed', now(), 'self-recorded', 'creates this table')
ON CONFLICT (version) DO NOTHING;

COMMIT;

-- POST-MIGRATION CHECKS
-- expect: 9 rows total; statuses gap=4, reserved=3, declared=1, committed=1
SELECT status, count(*) FROM schema_migrations GROUP BY 1 ORDER BY 1;

-- expect: exactly one row, version 145, applied_at NOT NULL
SELECT version, filename, applied_at IS NOT NULL AS attested
FROM schema_migrations WHERE applied_at IS NOT NULL;

-- expect: 133, 134, 84, 137 — the unresolved set the inventory diff must settle
SELECT version, note FROM schema_migrations WHERE status='gap' ORDER BY version;
