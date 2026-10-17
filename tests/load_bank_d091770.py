"""d091770: the geo slice the river routes read, for their tests.

`tests/fixtures/hydro_rivers_d091770/bank/*.jsonl.gz` hold, as Postgres's own
`row_to_json` text read through the Neon connector read-only (cut 2026-10-17T11:30Z,
manifest.json), a SLICE of each table the routes read — the rows the suite asserts on,
verbatim, never constructed:

  walks     geo_layer_objects: every row of the four reach walks the suite names and
            their three nwm_crosswalk layers, INCLUDING object 32 (the Columbia
            crosswalk banked 11:27Z with no publish row)
  display   geo_display_objects: the publish row of each of those objects that has one
  registry  geo_display_registry: the one geo.rivers.v1 row
  receipts  geo_unit_receipts: every row at the cut (44; none is an nwm_reach:*)
  records   geo_records: GCL, BON, MCN each with its superseded copy, PRD (the
            mid-Columbia relay, which banks its own receipt_scope) and HCR (the
            Willamette)
  values    timeseries_values: cwms_outflow_hourly GCL/PRD/MCN, 2026-10-17 00-03Z
            (MCN has no row — that is what R3's grey rests on)

`text(kind)` CHECKS NEON'S OWN sha256 over the same expression before it returns
anything, so a hand edit to a bank file is a red test.

`cluster()` starts a throwaway Postgres, creates the five geo tables from the pantry's
own migrations VERBATIM (`ddl_geo_tables.sql`: 302 lines 92-199, 322 lines 114-148) and
timeseries_values/datasets/sources from Neon's live definition (`ddl_series_tables.sql`;
all three predate the pantry's numbered migrations), then loads the banks with
`json_populate_recordset`, so every value is parsed by Postgres from Postgres's own text
and every row is held to the CHECKs Neon holds it to. The routes' statements then run on
it unchanged.

Not a test module (no test_ prefix): the tests and the rehearsal both import it.
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
from datetime import datetime

FIX = pathlib.Path(__file__).resolve().parent / "fixtures" / "hydro_rivers_d091770"
MANIFEST = json.loads((FIX / "manifest.json").read_text())
CUT = datetime.fromisoformat(MANIFEST["cut"])
# The window the values bank covers, and the instant the bodies are built at.
WIN_FROM = datetime.fromisoformat("2026-10-17T00:00:00+00:00")
WIN_TO = datetime.fromisoformat("2026-10-17T03:00:00+00:00")

TABLES = {k: v["table"] for k, v in MANIFEST["files"].items()}
# geo_layer_objects before geo_display_objects (its FK), datasets before timeseries_values.
LOAD_ORDER = ("walks", "display", "registry", "receipts", "records", "values")

# The one sources/datasets pair timeseries_values' FK needs. No route reads either table;
# this is a foreign-key bootstrap, NOT a banked row (manifest.json "fk_bootstrap").
FK_BOOTSTRAP = """
INSERT INTO sources (source_code, source_name, source_type)
VALUES ('usace', 'US Army Corps of Engineers', 'federal');
INSERT INTO datasets (dataset_code, dataset_name, source_code, granularity, unit)
VALUES ('cwms_outflow_hourly', 'CWMS outflow, hourly', 'usace', 'hourly', 'cfs');
"""


def text(kind: str) -> str:
    """The bank as Neon returned it, refused unless its sha256 is Neon's own."""
    f = MANIFEST["files"][kind]
    raw = gzip.decompress((FIX / f["path"]).read_bytes()).decode()
    got = hashlib.sha256(raw.encode()).hexdigest()
    if got != f["sha256"]:
        raise AssertionError(
            f"bank/{kind}: sha256 {got} is not the one Neon computed ({f['sha256']})")
    return raw


def lines(kind: str) -> list[str]:
    raw = text(kind)
    return raw.split("\n") if raw else []


def rows(kind: str) -> list[dict]:
    """The rows as stored, parsed from JSON (timestamps stay strings)."""
    return [json.loads(line) for line in lines(kind)]


def gate_fixture() -> dict:
    """The pantry's dry-run gate receipt (d091752), sha-pinned to the dashboard's bank."""
    f = MANIFEST["pantry_d091752"]
    path = FIX / "pantry_d091752" / "gate_receipt.json"
    raw = path.read_bytes()
    got = hashlib.sha256(raw).hexdigest()
    if got != f["gate_receipt.json"]:
        raise AssertionError(f"gate_receipt.json: sha256 {got} is not {f['gate_receipt.json']}")
    return json.loads(raw)


def gate_stdout() -> str:
    f = MANIFEST["pantry_d091752"]
    path = FIX / "pantry_d091752" / "gate_stdout.txt"
    raw = path.read_bytes()
    got = hashlib.sha256(raw).hexdigest()
    if got != f["gate_stdout.txt"]:
        raise AssertionError(f"gate_stdout.txt: sha256 {got} is not {f['gate_stdout.txt']}")
    return raw.decode()


def initdb_path() -> str | None:
    p = shutil.which("initdb") or "/usr/lib/postgresql/16/bin/initdb"
    return p if os.path.exists(p) else None


def load(conn, kinds=LOAD_ORDER) -> None:
    conn.execute((FIX / "ddl_series_tables.sql").read_text())
    conn.execute((FIX / "ddl_geo_tables.sql").read_text())
    conn.execute(FK_BOOTSTRAP)
    for kind in kinds:
        ls = lines(kind)
        if not ls:
            continue
        conn.execute(
            f"INSERT INTO {TABLES[kind]} SELECT * FROM json_populate_recordset("
            f"NULL::{TABLES[kind]}, %s::json)", ("[" + ",".join(ls) + "]",))
    for table, col in (("geo_layer_objects", "object_id"), ("geo_records", "record_id"),
                       ("geo_unit_receipts", "receipt_id")):
        # The rows carry Neon's ids; a later insert must not collide with them.
        conn.execute(f"SELECT setval(pg_get_serial_sequence('{table}', '{col}'), "
                     f"(SELECT max({col}) FROM {table}))")
    conn.execute("ANALYZE")


@contextlib.contextmanager
def cluster(load_bank: bool = True):
    """A throwaway Postgres on a unix socket, the bank loaded. Caller skips when
    initdb_path() is None. (load_bank_d091673's shape, this lane's DDL and banks.)"""
    import psycopg
    from psycopg.rows import dict_row
    initdb = initdb_path()
    bindir = os.path.dirname(initdb)
    d = tempfile.mkdtemp(prefix="pg_hydro_rivers_")
    as_pg = ["runuser", "-u", "postgres", "--"] if os.geteuid() == 0 else []
    if as_pg:
        shutil.chown(d, "postgres")
    data = os.path.join(d, "data")
    subprocess.run(as_pg + [initdb, "-D", data, "-A", "trust", "-U", "postgres",
                            "-E", "UTF8", "--locale=C.UTF-8"],
                   check=True, capture_output=True)
    subprocess.run(as_pg + [os.path.join(bindir, "pg_ctl"), "-D", data, "-w",
                            "-l", os.path.join(d, "pg.log"), "-o",
                            f"-k {d} -c listen_addresses='' -c timezone=UTC", "start"],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   timeout=60)
    conn = psycopg.connect(f"host={d} user=postgres dbname=postgres", autocommit=True,
                           row_factory=dict_row)
    try:
        if load_bank:
            load(conn)
        yield conn
    finally:
        conn.close()
        subprocess.run(as_pg + [os.path.join(bindir, "pg_ctl"), "-D", data,
                                "-m", "immediate", "stop"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
        shutil.rmtree(d, ignore_errors=True)
