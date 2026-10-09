"""d091673: the tropical bank as Neon stored it, for the Tropics routes' tests.

tests/fixtures/tropics_d091673/bank/*.jsonl.gz hold every row of the four
tables the routes read, as Postgres's own row_to_json text, cut at CUT
(manifest.json). `rows(kind)` checks the sha-256 Neon computed over the same
text before it returns anything, so a hand edit to the bank is a red test.

`cluster()` starts a throwaway local Postgres, creates migration 291's tables
(ddl_291_tables.sql, with every CHECK and index), and loads the bank with
json_populate_recordset, so each value is parsed by Postgres from Postgres's
own text. The routes' statements then run on it unchanged through PgPool.

Not a test module (no test_ prefix): the tests and the receipts' scripts
both import it.
"""

from __future__ import annotations

import contextlib
import gzip
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone

FIX = pathlib.Path(__file__).resolve().parent / "fixtures" / "tropics_d091673"
MANIFEST = json.loads((FIX / "manifest.json").read_text())
CUT = datetime.fromisoformat(MANIFEST["cut"])
# The instant the bodies are banked at: the cut plus the first poll interval,
# so the newest heartbeat (21:54:46Z) is 5 min 13 s old and FRESH.
NOW = datetime(2026, 10, 9, 22, 0, 0, tzinfo=timezone.utc)

TABLES = {"storm": "tropical_storms", "point": "tropical_track_points",
          "odds": "tropical_place_odds", "heartbeat": "tropical_file_vintage",
          "dataset": "datasets"}
LOAD_ORDER = ("dataset", "storm", "point", "odds", "heartbeat")


def text(kind: str) -> str:
    f = MANIFEST["files"][kind]
    raw = gzip.decompress((FIX / f["path"]).read_bytes()).decode()
    got = hashlib.sha256(raw.encode()).hexdigest()
    if got != f["sha256"]:
        raise AssertionError(f"bank/{kind}: sha256 {got} is not the one Neon computed ({f['sha256']})")
    return raw


def lines(kind: str) -> list[str]:
    return text(kind).split("\n")


def rows(kind: str) -> list[dict]:
    """The rows as stored, parsed from JSON (timestamps stay strings)."""
    return [json.loads(line) for line in lines(kind)]


def initdb_path() -> str | None:
    p = shutil.which("initdb") or "/usr/lib/postgresql/16/bin/initdb"
    return p if os.path.exists(p) else None


def load(conn, kinds=LOAD_ORDER) -> None:
    conn.execute((FIX / "ddl_291_tables.sql").read_text())
    for kind in kinds:
        conn.execute(f"INSERT INTO {TABLES[kind]} SELECT * FROM json_populate_recordset("
                     f"NULL::{TABLES[kind]}, %s::json)", ("[" + ",".join(lines(kind)) + "]",))
    for table, col in (("tropical_track_points", "point_id"), ("tropical_place_odds", "odds_id"),
                       ("tropical_file_vintage", "vintage_id")):
        # The rows carry Neon's ids; a later insert must not collide with them.
        conn.execute(f"SELECT setval(pg_get_serial_sequence('{table}', '{col}'), "
                     f"(SELECT max({col}) FROM {table}))")
    conn.execute("ANALYZE")


@contextlib.contextmanager
def cluster(load_bank: bool = True):
    """A throwaway Postgres on a unix socket, the bank loaded. Caller skips
    when initdb_path() is None."""
    import psycopg
    from psycopg.rows import dict_row
    initdb = initdb_path()
    bindir = os.path.dirname(initdb)
    d = tempfile.mkdtemp(prefix="pg_tropics_")
    as_pg = ["runuser", "-u", "postgres", "--"] if os.geteuid() == 0 else []
    if as_pg:
        shutil.chown(d, "postgres")
    data = os.path.join(d, "data")
    subprocess.run(as_pg + [initdb, "-D", data, "-A", "trust", "-U", "postgres", "-E", "UTF8",
                            "--locale=C.UTF-8"], check=True, capture_output=True)
    subprocess.run(as_pg + [os.path.join(bindir, "pg_ctl"), "-D", data, "-w",
                            "-l", os.path.join(d, "pg.log"), "-o",
                            f"-k {d} -c listen_addresses='' -c timezone=UTC", "start"],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
    conn = psycopg.connect(f"host={d} user=postgres dbname=postgres", autocommit=True,
                           row_factory=dict_row)
    try:
        if load_bank:
            load(conn)
        yield conn
    finally:
        conn.close()
        subprocess.run(as_pg + [os.path.join(bindir, "pg_ctl"), "-D", data, "-m", "immediate",
                                "stop"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=60)
        shutil.rmtree(d, ignore_errors=True)


class PgPool:
    """main._pool over a sync psycopg connection, recording (sql, params).
    The connection is autocommit, so SET LOCAL only warns; the statements are
    the routes' own."""

    def __init__(self, conn):
        self.conn, self.calls = conn, []

    def connection(self):
        pool = self

        class _Cur:
            async def __aenter__(self):
                self.c = pool.conn.cursor()
                return self

            async def __aexit__(self, *exc):
                self.c.close()
                return False

            async def execute(self, query, params=None):
                pool.calls.append((query, params))
                self.c.execute(query, params)

            async def fetchall(self):
                return self.c.fetchall()

            async def fetchone(self):
                return self.c.fetchone()

        class _Ctx:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            def cursor(self):
                return _Cur()

            def transaction(self):
                return _Ctx()

        return _Ctx()
