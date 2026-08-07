-- ═══════════════════════════════════════════════════════════════════════════
-- MIGRATION 154 — `publications`: the Almanac's issue table
--
--   Reserved in the ledger 2026-08-07 (schema_migrations version 154,
--   status='reserved', attestation='ledger-2026-08-07', note "almanac lane A:
--   publications table + daily-brief backfill").
--
--   DECLARED HERE, NOT APPLIED. This file has NOT been run against the pantry.
--   The architect applies it; the ledger row stays `reserved` until it does.
--   Nothing in the API repo can apply it — this service is read-only and holds
--   no DDL path.
--
--   WHY THE FILE LIVES IN THE API REPO. Migrations are the pantry's
--   (`energylake-pantry`) to own and apply. This one is declared here because
--   the lane that needs it — the Almanac serving layer and its backfill
--   adapter — is here, and shipping the endpoints without the DDL they read
--   would leave the shape they expect unstated. Move or mirror it into the
--   pantry's migration directory at apply time; the ledger's `filename` below
--   records the name it is applied under.
--
--   SELF-RECORD. The final statement UPDATEs the reserved ledger row in the
--   same transaction as the DDL, per the house convention set by 148 and 151
--   (attestation 'self-recorded'). Apply the whole file or none of it: a
--   committed table with a `reserved` ledger row is exactly the drift the
--   ledger exists to prevent.
--
-- ── WHAT THIS TABLE IS ──────────────────────────────────────────────────────
--   One row per ISSUE of a publication — a daily/weekly/monthly cadence piece
--   or a standalone article. `body` is an ORDERED list of render blocks, so a
--   piece can mix prose and live figures without the serving layer knowing
--   what any particular figure is.
--
--   Read by three endpoints in `energylake-api`, all published-only:
--       GET /api/almanac                    the shelf
--       GET /api/almanac/{series}           the series page
--       GET /api/almanac/{series}/{issue}   one issue
--
-- ── THE BLOCK VOCABULARY (body) ─────────────────────────────────────────────
--       {"type": "prose",  "md": "..."}
--       {"type": "figure", "component": "...", "params": {...},
--        "as_of": "<timestamptz>"}
--
--   Block STRUCTURE IS NOT CONSTRAINED IN SQL — only that `body` is a JSON
--   ARRAY. A CHECK enumerating block types would have to be migrated every
--   time the Almanac learns a new figure, and the serving layer already skips
--   block types it does not recognise rather than failing the page. The array
--   check earns its place because it is the one thing the reader genuinely
--   cannot work around: an object where a list belongs breaks ordering, which
--   is the whole point of the column.
--
-- ── DEVIATIONS FROM THE FILED SPEC, STATED ──────────────────────────────────
--   The spec named `verified boolean NOT NULL` and left the rest unqualified.
--   Four NOT NULLs are added here, each because a NULL in that column produces
--   a row that cannot be served or cannot be addressed:
--
--     series     NOT NULL — a CHECK does not reject NULL, and UNIQUE does not
--                collapse NULLs. Without this, rows with a null series are
--                un-routable AND can be inserted without limit.
--     issue_key  NOT NULL — same reasoning; it is half the identity.
--     status     NOT NULL — the publish gate. A NULL status matches neither
--                'draft' nor 'published', so the row would be invisible to the
--                API and invisible to any draft-review tool: silently lost.
--     body       NOT NULL DEFAULT '[]'::jsonb — the ordered block list. Empty
--                is a real state (a stub issue); NULL is not a different one.
--
--   If the architect wants the spec's literal nullability instead, drop the
--   four NOT NULLs — the serving layer tolerates every one of those NULLs
--   already (it coerces `verified`, serves a non-list body as `[]`, and filters
--   status in SQL). They are declared here as pantry-side guardrails, not as
--   something the API depends on.
--
-- ── INDEXES: NONE BEYOND THE CONSTRAINTS, DELIBERATELY ──────────────────────
--   `publications_series_issue_uniq` is doing more work than its name suggests,
--   and it is why nothing else is needed at v0. REHEARSED on an ephemeral Neon
--   branch on 2026-08-07 with this exact DDL applied and all 14 backfilled
--   dailies loaded (28,770 bytes of body across 14 rows, 72 kB table):
--
--     GET /api/almanac                (shelf, no ?series=)
--       Sort  (cost=13.26..13.27 rows=1 width=273) (actual time=0.038..0.039 rows=14)
--         Sort Key: issued_ts DESC NULLS LAST, issue_key DESC
--         Sort Method: quicksort  Memory: 44kB      Buffers: shared hit=3
--         ->  Seq Scan on publications  (actual time=0.016..0.023 rows=14)
--               Filter: (status = 'published'::text)
--       Planning Time: 0.066 ms   Execution Time: 0.060 ms
--
--     GET /api/almanac/daily          (series page)
--       Sort  (cost=8.18..8.18 rows=1 width=273) (actual time=0.045..0.046 rows=14)
--         Sort Key: issued_ts DESC NULLS LAST, issue_key DESC
--         Buffers: shared hit=8
--         ->  Index Scan using publications_series_issue_uniq (actual rows=14)
--               Index Cond: (series = 'daily'::text)
--               Filter: (status = 'published'::text)
--       Planning Time: 0.074 ms   Execution Time: 0.066 ms
--
--     GET /api/almanac/daily/2026-08-06   (one issue)
--       Limit  (actual time=0.013..0.014 rows=1)   Buffers: shared hit=2
--         ->  Index Scan using publications_series_issue_uniq (actual rows=1)
--               Index Cond: ((series = 'daily') AND (issue_key = '2026-08-06'))
--       Planning Time: 0.060 ms   Execution Time: 0.026 ms
--
--   THE SEQ SCAN ON THE SHELF IS THE RIGHT PLAN, not a finding: the whole table
--   is three pages, so an index would cost a read to save nothing. The series
--   page and the single issue already ride the UNIQUE index on its leading
--   column — the series page for free, without an index declared for it.
--
--   THE INDEX TO ADD WHEN IT GROWS — written out so nobody has to re-derive it,
--   and deliberately NOT created today. It only starts earning its keep once
--   the `status` filter is discarding a real fraction of the table, which needs
--   a standing draft population this table does not have yet:
--
--       CREATE INDEX idx_publications_series_issued
--           ON publications (series, issued_ts DESC)
--           WHERE status = 'published';
--
--   Add it when `publications` reaches the low thousands of rows, or when the
--   shelf read's Execution Time clears a millisecond, whichever comes first.
--
-- ── WHAT THE 2026-08-07 REHEARSAL PROVED, AND WHAT IT DID NOT ───────────────
--   PROVED, on the branch: this DDL applies clean; the self-record UPDATE below
--   flips the reserved row to committed; all five guards fire (an off-vocabulary
--   series, an off-vocabulary status, a jsonb object where an array belongs, a
--   duplicate (series, issue_key), and a NULL `verified`) and the table is
--   byte-identical after all five are rejected; the backfill is idempotent (a
--   second full pass leaves the table's hash and every publication_id
--   unchanged); and the transform's output is byte-identical to the Python in
--   `almanac.publication_from_daily_brief` (md5 9be8b81355865e9d85c8b3d7f3d7bdd1
--   on the 2026-08-06 issue, both sides).
--
--   NOT PROVED: nothing here has touched the pantry. This file has not been run
--   against it, and `scripts/backfill_almanac_daily.py` has not been executed
--   anywhere — the rehearsal transcribed its transform into SQL because the CI
--   container has no outbound Postgres route. Whoever applies this migration
--   runs the script's own dry-run first; that receipt is the one that counts.
-- ═══════════════════════════════════════════════════════════════════════════

BEGIN;

CREATE TABLE publications (
    publication_id      bigserial PRIMARY KEY,

    -- The cadence lane. Vocabulary mirrored in almanac.SERIES_VOCABULARY.
    series              text NOT NULL,

    -- The stable key of one issue within its series. For 'daily' this is the
    -- ISO covered date ('2026-08-06'); a weekly/monthly is free to key on its
    -- own period label, and an 'article' on a slug. Text, not date, precisely
    -- so those four cadences do not have to agree on a calendar.
    issue_key           text NOT NULL,

    headline            text,

    -- The standfirst under the headline. Nullable and, at v0, null on every
    -- backfilled row: the daily-brief writer banks no dek. See the backfill
    -- adapter's findings.
    dek                 text,

    -- ORDERED render blocks. See THE BLOCK VOCABULARY above.
    body                jsonb NOT NULL DEFAULT '[]'::jsonb,

    author              text,

    -- When the issue was published. Drives newest-first ordering everywhere.
    issued_ts           timestamptz,

    -- The version of the verifier that passed this piece. NULL means the
    -- writer banked no verifier version — NOT that the piece is unverified;
    -- `verified` is the field that answers that, and it is NOT NULL.
    verifier_version    text,
    verified            boolean NOT NULL,

    -- The instant beyond which no data in this piece was read. NULL means the
    -- writer stated no instant. It is never inferred from a writer clock: when
    -- the desk wrote is not what the desk had read.
    data_cutoff_ts      timestamptz,

    -- The publish gate. Only 'published' rows ever reach the API; a 'draft'
    -- row is served as an absence, never as a 403.
    status              text NOT NULL,

    CONSTRAINT publications_series_ck
        CHECK (series IN ('daily', 'weekly', 'monthly', 'article')),
    CONSTRAINT publications_status_ck
        CHECK (status IN ('draft', 'published')),
    CONSTRAINT publications_body_is_array_ck
        CHECK (jsonb_typeof(body) = 'array'),
    CONSTRAINT publications_series_issue_uniq
        UNIQUE (series, issue_key)
);

COMMENT ON TABLE publications IS
    'The Almanac: one row per published issue. series+issue_key is the '
    'identity; body is an ordered jsonb array of render blocks '
    '({type:prose,md} | {type:figure,component,params,as_of}). Only '
    'status=''published'' rows are served by energylake-api; drafts are served '
    'as absences. Migration 154, 2026-08-07.';

COMMENT ON COLUMN publications.issue_key IS
    'Stable key of one issue within its series. ISO covered date for '
    '''daily''; a period label or slug for the other cadences. Text so the '
    'four series need not share a calendar. UNIQUE(series, issue_key).';

COMMENT ON COLUMN publications.body IS
    'ORDERED render blocks. {"type":"prose","md":...} | '
    '{"type":"figure","component":...,"params":{...},"as_of":...}. Block '
    'structure is intentionally unconstrained in SQL beyond being an array; '
    'the serving layer skips block types it does not recognise.';

COMMENT ON COLUMN publications.verified IS
    'Whether this piece passed its verifier. NOT NULL: a publication surface '
    'must never render an unknown verification state as a verified one. The '
    '2026-08-07 daily backfill sets true from the source brief''s own '
    'verification state (the pantry weather writer UPSERTs only on verifier '
    'PASS, so the existence of the source row IS the stamp).';

COMMENT ON COLUMN publications.verifier_version IS
    'Version of the verifier that passed this piece. NULL = the writer banked '
    'no version stamp; it does NOT mean unverified (see publications.verified). '
    'Null on every row of the 2026-08-07 daily backfill: joule_briefs, its '
    'sources_used and the linked joule_calls.meta carry voice_version and '
    'bundle_version, neither of which is a verifier version.';

COMMENT ON COLUMN publications.data_cutoff_ts IS
    'Instant beyond which no data in this piece was read. NULL = the writer '
    'stated no instant. Never inferred from a writer clock: when the desk '
    'wrote is not what the desk had read.';

-- ── SELF-RECORD ─────────────────────────────────────────────────────────────
-- Flip the reserved ledger row to committed, in the same transaction as the
-- DDL. Guarded on status='reserved' so a re-run cannot silently re-stamp an
-- already-applied migration with a new timestamp.
UPDATE schema_migrations
   SET filename    = '154_publications.sql',
       status      = 'committed',
       applied_at  = now(),
       attestation = 'self-recorded',
       note        = 'almanac lane A: publications table (series/issue_key '
                     'identity, ordered jsonb body blocks, draft|published '
                     'gate). Declared by energylake-api; daily-brief backfill '
                     'ships as scripts/backfill_almanac_daily.py and is run '
                     'separately.'
 WHERE version = 154
   AND status = 'reserved';

COMMIT;
