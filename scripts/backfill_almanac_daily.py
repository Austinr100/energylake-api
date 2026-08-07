#!/usr/bin/env python3
"""
backfill_almanac_daily.py — bank Kelvin's daily briefs as Almanac publications.

    joule_briefs (brief_type='weather')  ->  publications (series='daily')

THE ONLY WRITE PATH IN THIS REPOSITORY, AND IT IS NOT AN ENDPOINT. `main.py`
serves; this script is a one-shot adapter run by hand, by a person who has
decided to run it. It is not imported by the app, not wired to a route, and not
scheduled. Nothing in the API can invoke it.

    # look, change nothing (THE DEFAULT — a bare run writes nothing)
    python scripts/backfill_almanac_daily.py

    # having read the dry-run receipt, write
    python scripts/backfill_almanac_daily.py --apply

    # narrow the window
    python scripts/backfill_almanac_daily.py --since 2026-08-01 --apply

DRY-RUN IS THE DEFAULT AND `--apply` IS THE ONLY WAY PAST IT. There is no
`--yes`, no env var, and no config file that flips this. A run with no flags
opens a transaction, does every INSERT ... ON CONFLICT the real run would do,
prints the identical receipt, and ROLLS BACK. So the dry-run receipt is not a
simulation of the write — it IS the write, executed against the real table and
then discarded. A dry run that "would have worked" and an apply that fails on a
constraint is the failure mode this design removes.

IDEMPOTENT, BY THE TABLE'S OWN IDENTITY. The write is
`INSERT ... ON CONFLICT (series, issue_key) DO UPDATE`, riding migration 154's
UNIQUE constraint. Run it once or ten times and the shelf is the same. The
receipt distinguishes the three outcomes so a re-run is legible:

    inserted   no row existed for (daily, <date>)
    updated    a row existed and this run CHANGED at least one column
    unchanged  a row existed and every column already matched

The three are told apart by READING THE ROW'S PRE-IMAGE inside the same
transaction as the write and comparing it column by column — NOT by trusting a
row count. `ON CONFLICT DO UPDATE` reports every conflicting row as affected
whether or not it changed a single byte, so a count-based receipt reads a no-op
re-run as "14 updated" and reports work it did not do.

WHAT IT WILL NOT DO. It never DELETEs, never TRUNCATEs, and never touches a row
whose `series` is not 'daily' or whose `issue_key` is not a date it read from
`joule_briefs` on this run. A publications row that a human wrote by hand under
a daily issue_key WILL be overwritten by a re-run — that is the cost of an
idempotent backfill, it is stated here rather than discovered, and
`--skip-existing` is the flag that avoids it.

STATUS. Backfilled rows are written `status='published'`, because these 14
briefs are already live on `/api/weather/brief/*`. Backfilling a published run
as drafts would take it off a publication surface on the way to putting it on
one.

THE FIELD MAPPING, AND THE THREE NULLS THAT ARE FINDINGS (dek,
verifier_version, data_cutoff_ts), are documented once — in
`almanac.publication_from_daily_brief`, which is the pure transform this script
runs. They are not restated here so there is exactly one place to correct them.

REQUIRES migration 154 to have been applied. Without it the script stops on the
missing table with a one-line message naming the migration, rather than a
psycopg traceback.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date as _date
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import almanac as alm  # noqa: E402  (after sys.path fix-up)


# Every banked Kelvin daily, oldest first — so the backfill's `issued_ts` order
# and the shelf's newest-first order are the same run read from either end, and
# a partial run leaves a contiguous prefix of history rather than a scatter.
READ_BRIEFS_SQL = """
    SELECT brief_date, headline, content_md, created_at
    FROM joule_briefs
    WHERE brief_type = %(brief_type)s
      AND (%(since)s::date IS NULL OR brief_date >= %(since)s)
    ORDER BY brief_date ASC
"""

# The pre-image, read inside the same transaction as the write so "unchanged"
# is decided against what the row actually held a moment before, not against a
# separate snapshot.
READ_EXISTING_SQL = """
    SELECT headline, dek, body, author, issued_ts, verifier_version,
           verified, data_cutoff_ts, status
    FROM publications
    WHERE series = %(series)s AND issue_key = %(issue_key)s
"""

UPSERT_SQL = """
    INSERT INTO publications
        (series, issue_key, headline, dek, body, author, issued_ts,
         verifier_version, verified, data_cutoff_ts, status)
    VALUES
        (%(series)s, %(issue_key)s, %(headline)s, %(dek)s, %(body)s,
         %(author)s, %(issued_ts)s, %(verifier_version)s, %(verified)s,
         %(data_cutoff_ts)s, %(status)s)
    ON CONFLICT (series, issue_key) DO UPDATE SET
        headline         = EXCLUDED.headline,
        dek              = EXCLUDED.dek,
        body             = EXCLUDED.body,
        author           = EXCLUDED.author,
        issued_ts        = EXCLUDED.issued_ts,
        verifier_version = EXCLUDED.verifier_version,
        verified         = EXCLUDED.verified,
        data_cutoff_ts   = EXCLUDED.data_cutoff_ts,
        status           = EXCLUDED.status
    RETURNING publication_id
"""

# The columns compared to decide inserted / updated / unchanged. `body` is
# compared as parsed JSON, not as text: two jsonb renderings of the same blocks
# differ by key order and whitespace, and calling that an update would make
# every re-run report work it did not do.
COMPARED_COLUMNS = (
    "headline", "dek", "body", "author", "issued_ts", "verifier_version",
    "verified", "data_cutoff_ts", "status",
)


def _same(existing: dict, incoming: dict) -> bool:
    """True when the banked row already equals what this run would write."""
    for col in COMPARED_COLUMNS:
        if existing.get(col) != incoming.get(col):
            return False
    return True


def _database_url() -> str:
    url = os.environ.get("NEON_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        sys.exit(
            "NEON_DATABASE_URL is not set. This script writes to the pantry; "
            "it will not guess a connection string.")
    return url


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=("Backfill Kelvin's banked daily briefs into the Almanac's "
                     "`publications` table. Dry-run unless --apply."),
    )
    p.add_argument(
        "--apply", action="store_true",
        help=("COMMIT the writes. Without this the whole run is executed and "
              "then ROLLED BACK, and the receipt is identical."))
    p.add_argument(
        "--since", type=_date.fromisoformat, default=None, metavar="YYYY-MM-DD",
        help="Only briefs with brief_date >= this. Default: every banked brief.")
    p.add_argument(
        "--skip-existing", action="store_true",
        help=("Leave rows that already exist exactly as they are, instead of "
              "upserting over them. Use when a publications row under a daily "
              "issue_key has been edited by hand and must survive a re-run."))
    return p.parse_args(argv)


def run(conn, args) -> dict:
    """Execute the whole backfill inside `conn`'s open transaction.

    Returns the receipt. The CALLER decides commit vs rollback — this function
    is deliberately identical on a dry run and a real one.
    """
    receipt = {
        "brief_type": alm.BACKFILL_BRIEF_TYPE,
        "series": alm.SERIES_DAILY,
        "since": args.since.isoformat() if args.since else None,
        "briefs_read": 0,
        "inserted": 0,
        "updated": 0,
        "unchanged": 0,
        "skipped_existing": 0,
        "issue_keys": [],
        "changed_issue_keys": [],
    }

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(READ_BRIEFS_SQL, {
            "brief_type": alm.BACKFILL_BRIEF_TYPE,
            "since": args.since,
        })
        briefs = cur.fetchall()
        receipt["briefs_read"] = len(briefs)

        for brief in briefs:
            row = alm.publication_from_daily_brief(brief)
            issue_key = row["issue_key"]
            receipt["issue_keys"].append(issue_key)

            cur.execute(READ_EXISTING_SQL, {
                "series": row["series"], "issue_key": issue_key})
            existing = cur.fetchone()

            if existing is not None and args.skip_existing:
                receipt["skipped_existing"] += 1
                continue

            if existing is None:
                outcome = "inserted"
            elif _same(existing, row):
                outcome = "unchanged"
            else:
                outcome = "updated"

            # `body` crosses as Jsonb so psycopg adapts the block list to jsonb
            # rather than to a text literal.
            cur.execute(UPSERT_SQL, {**row, "body": Jsonb(row["body"])})
            cur.fetchone()  # drain RETURNING

            receipt[outcome] += 1
            if outcome != "unchanged":
                receipt["changed_issue_keys"].append(issue_key)

    return receipt


def main(argv=None) -> int:
    args = parse_args(argv)
    mode = "APPLY" if args.apply else "DRY RUN"

    try:
        conn = psycopg.connect(_database_url())
    except psycopg.OperationalError as e:
        sys.exit(f"could not connect to the pantry: {e}")

    with conn:
        conn.autocommit = False
        try:
            receipt = run(conn, args)
        except psycopg.errors.UndefinedTable:
            conn.rollback()
            sys.exit(
                "table `publications` does not exist. Migration 154 "
                "(migrations/154_publications.sql) has not been applied yet — "
                "the architect applies it. Nothing was written.")
        except Exception:
            conn.rollback()
            raise

        if args.apply:
            conn.commit()
        else:
            # The whole run, executed against the real table and discarded.
            conn.rollback()

    receipt["mode"] = mode
    receipt["committed"] = bool(args.apply)

    print(f"── ALMANAC DAILY BACKFILL — {mode} ──")
    print(json.dumps(receipt, indent=2, sort_keys=False))
    if not args.apply:
        print("\nROLLED BACK. Nothing was written. Re-run with --apply to commit.")
    else:
        print(f"\nCOMMITTED. {receipt['inserted']} inserted, "
              f"{receipt['updated']} updated, {receipt['unchanged']} unchanged, "
              f"{receipt['skipped_existing']} skipped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
