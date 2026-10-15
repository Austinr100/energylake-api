"""d091741: the ledger slice the Tropics index routes read, for their tests.

tests/fixtures/tropics_d091741/bank/*.jsonl.gz hold, as Postgres's own
row_to_json text read through the Neon connector (2026-10-15 13:55-13:57Z, cut
2026-10-15T13:50:00Z, manifest.json):

  dataset   the four datasets rows the ledger rows reference (FK)
  storm     tropical_storms for Isaias, Rachel, Simon and Trudy
  official  every nhc_official point of those four storms (the advisory and its
            synoptic time are resolved from these)
  ledger    every tropical_file_vintage row of those storms' cone_kmz, ww_kmz,
            surge_ww_kml, tcd, tcp and storm_entry, every wsp_5km_zip and
            wsp_halfdeg_zip, and those storms' tracks:ecmwf_ens,
            tracks:ecmwf_aifs_ens and tracks:adeck parquets

`rows(kind)` checks the sha-256 Neon computed over the same text before it
returns anything. `cluster()` starts a throwaway Postgres (load_bank_d091673's),
creates migration 291's tables and 294/297's views (d091673's and d091682's DDL
fixtures, verbatim), loads the slice, then the CONSTRUCTED display rows
(display_rows.json; make_display_rows.py says what they are and why), and with
`constructed=True` the second layer (constructed_rows.json: I2's correction,
two member manifests; and CONSTRUCTED_DELETE). `pad=True` adds filler ledger rows of other products up
to Neon's size (7,029 rows read 2026-10-15), so the planner plans as Neon does;
the filler names no product, storm or key any route reads.
Not a test module (no test_ prefix).
"""

from __future__ import annotations

import contextlib
import gzip
import hashlib
import json
import pathlib
from datetime import datetime, timezone

import load_bank_d091673 as lb

FIX = pathlib.Path(__file__).resolve().parent / "fixtures" / "tropics_d091741"
MANIFEST = json.loads((FIX / "manifest.json").read_text())
CUT = datetime.fromisoformat(MANIFEST["cut"])
NOW = datetime(2026, 10, 15, 14, 0, 0, tzinfo=timezone.utc)
TABLES = {"dataset": "datasets", "storm": "tropical_storms", "official": "tropical_track_points",
          "ledger": "tropical_file_vintage"}
LOAD_ORDER = ("dataset", "storm", "official", "ledger")
NEON_LEDGER_ROWS = 7029


def text(kind: str) -> str:
    f = MANIFEST["bank"][kind]
    raw = gzip.decompress((FIX / f["path"]).read_bytes()).decode()
    got = hashlib.sha256(raw.encode()).hexdigest()
    if got != f["sha256"]:
        raise AssertionError(f"bank/{kind}: sha256 {got} is not the one Neon computed ({f['sha256']})")
    return raw


def rows(kind: str) -> list[dict]:
    return [json.loads(line) for line in text(kind).split("\n")]


def display_rows() -> list[dict]:
    return json.loads((FIX / "display_rows.json").read_text())


def constructed_rows() -> list[dict]:
    return json.loads((FIX / "constructed_rows.json").read_text())


# The second layer's one removal: Isaias 019's banked TCD (its CurrentStorms
# entry links 019's own), so T1 meets a not_banked text. The live bank holds
# every text an entry links (read 2026-10-15), so this case can only be made.
CONSTRUCTED_DELETE = [("nhc", "tcd", "al092026", "019")]


def _insert(conn, table: str, objs) -> None:
    conn.execute(f"INSERT INTO {table} SELECT * FROM json_populate_recordset(NULL::{table}, %s::json)",
                 (json.dumps(objs) if not isinstance(objs, str) else objs,))


def pad(conn) -> int:
    """Filler ledger rows to Neon's size: products no route reads, storms that
    are not in the bank (al50..al99), so no read can return one."""
    n = conn.execute("SELECT count(*) AS n FROM tropical_file_vintage").fetchone()["n"]
    want = NEON_LEDGER_ROWS - n
    conn.execute("""
        INSERT INTO tropical_file_vintage (vintage_id, dataset, source, product, storm_id, vintage_key,
                                           status, fetch_ts, issued_ts, r2_key, byte_size, sha256, meta)
        SELECT 800000 + g, 'nhc_advisory_vintage',
               (ARRAY['nhc', 'nhc_ftp', 'parsed'])[1 + mod(g, 3)],
               (ARRAY['track_kmz', 'fcst_5day_zip', 'fcst_radii_zip', 'pws', 'tcm',
                      'initialradii_kmz', 'toa_earliest_kmz', 'tracks:ecmwf_ifs'])[1 + mod(g, 8)],
               'al' || (50 + mod(g, 50)) || '2025', lpad((g / 400)::text, 3, '0'), 'banked',
               '2025-09-01'::timestamptz + make_interval(mins => g), NULL,
               'filler/' || g, 100 + g, encode(sha256(g::text::bytea), 'hex'), '{}'::jsonb
          FROM generate_series(1, %(want)s) AS g""", {"want": want})
    return want


def load(conn, *, constructed: bool = False, padded: bool = False) -> None:
    conn.execute((lb.FIX / "ddl_291_tables.sql").read_text())
    import load_bank_d091682 as lb2
    conn.execute((lb2.FIX / "ddl_294.sql").read_text())
    conn.execute((lb2.FIX / "ddl_297.sql").read_text())
    conn.execute((FIX / "ddl_314.sql").read_text())  # the display CHECKs the fixture rows are held to
    for kind in LOAD_ORDER:
        _insert(conn, TABLES[kind], "[" + ",".join(text(kind).split("\n")) + "]")
    _insert(conn, "tropical_file_vintage", display_rows())
    if constructed:
        _insert(conn, "tropical_file_vintage", constructed_rows())
        for source, product, storm_id, vkey in CONSTRUCTED_DELETE:
            conn.execute("DELETE FROM tropical_file_vintage WHERE source = %s AND product = %s "
                         "AND storm_id = %s AND vintage_key = %s", (source, product, storm_id, vkey))
    if padded:
        pad(conn)
    conn.execute("ANALYZE")


@contextlib.contextmanager
def cluster(*, constructed: bool = False, padded: bool = False):
    with lb.cluster(load_bank=False) as conn:
        load(conn, constructed=constructed, padded=padded)
        yield conn


PgPool = lb.PgPool
initdb_path = lb.initdb_path


# The two not_served statements as d091673 banked them (tropics.py at 265f9fb).
# This lane points each at its index route; every Tropics vector changed by these
# two strings and nothing else (docs/receipts/tropics-api-index-d091741/rebank_vectors.py).
NOT_SERVED_D091673 = {
    "ensemble_members": (
        "ECMWF ENS (51 members), AIFS-ENS (52) and the A-deck's GEFS members AP01-AP30 and "
        "AC00 are not in Neon: the bank keeps every member as R2 parquet "
        "(tropical/tracks/{storm}/{cycle}/{source}.parquet), and tropical_track_points has "
        "no member column. This API reads no R2 per request."),
    "cone_and_warnings": (
        "NHC's cone and its watch and warning polygons are banked as files in R2 (the "
        "advisory's 5-day zip and cone/WW KMZ), not as rows. This API reads no R2."),
}


def revert_not_served(body: dict) -> dict:
    """The body as it was before this lane: the two pointers taken back out. Refuses
    a body whose two statements are not this lane's (so the revert cannot hide one)."""
    import tropics
    for key, old in NOT_SERVED_D091673.items():
        assert body["not_served"][key] == tropics.NOT_SERVED[key], key
        body["not_served"][key] = old
    return body
