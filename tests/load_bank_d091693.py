"""d091693: the CPC curve bank as Neon held it on 2026-10-11 ~13:47Z, both
method versions, real rows only.

tests/fixtures/cpc_flip_v2_d091693/*.json.gz are the exact text of six
read-only statements on Neon production (`jsonb_agg(to_jsonb(row) - 'id')`,
the statements in that folder's README), each checked against the sha-256
and length Neon computed over the same text before it is used:

  * verdicts_v1.json, verdicts_v2.json: every row of cpc_curve_verdicts of
    each method_version, every column but verdict_id (720 + 720);
  * curves_{product}_{v1,v2}.json: every column but curve_row_id of the
    cpc_outlook_curves rows of that product and version (a) at six places
    (KSAN, KDEN, San Jose, pnw at both weightings, caiso_sp15_proxy at
    load_share_365d) on every issuance 2026-09-24 .. 10-10, and (b) at every
    place on the version's newest issuance (v1 10-09, v2 10-10).

So /places (all verdicts, every window row of the newest issuance) and
/curves at any of the 30 places are what Neon would serve; /curves/vintages
is, at the six places, for n <= 14. Nothing is constructed.

`build(conn, versions)` creates pantry 286 + 298 (the same verbatim DDL as
d091691: tests/fixtures/cpc_method_pin_d091691/ddl_286_298.sql) and loads
the rows with json_populate_recordset, so Postgres parses each value from
Postgres's own text. Three banks:

  v1    the v1 rows alone (what main a10b6f2 reads; v2 rows change nothing
        for it, d091691)
  v2    the v2 rows alone (v1 retired)
  both  v1 and v2 beside each other: Neon as it stands

Not a test module (no test_ prefix).
"""

from __future__ import annotations

import contextlib
import gzip
import hashlib
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent
FIX = ROOT / "fixtures" / "cpc_flip_v2_d091693"
DDL = ROOT / "fixtures" / "cpc_method_pin_d091691" / "ddl_286_298.sql"

V1, V1_HASH, V1_BACKTEST = "cpc_curves_v1", "e99c71234013", "cpcv_2026-10-03_d0a93d5811"
V2, V2_HASH, V2_BACKTEST = "cpc_curves_v2", "b201e09ff97d", "cpcv_2026-10-06_243e6acd91"
PRODUCTS = ("610temp", "814temp")
NEWEST = {V1: "2026-10-09", V2: "2026-10-10"}

KSAN = ("station", "USW00023188", "")
KDEN = ("station", "USW00003017", "")
SJC = ("station", "USW00023293", "")
PNW_POP = ("region", "pnw", "population")
PNW_LOAD = ("region", "pnw", "load_share_365d")
SP15_LOAD = ("region", "caiso_sp15_proxy", "load_share_365d")
SIX = (KSAN, KDEN, SJC, PNW_POP, PNW_LOAD, SP15_LOAD)


def manifest() -> dict:
    out = {}
    for line in (FIX / "manifest.psv").read_text().splitlines():
        name, n, sha, ln, read_at = line.split("|")
        out[name] = {"rows": int(n), "sha256": sha, "length": int(ln), "read_at": read_at}
    return out


_CACHE: dict = {}


def text(name: str) -> str:
    """A fixture's text, checked against the sha-256 and length Neon computed."""
    if name not in _CACHE:
        m = manifest()[name]
        t = gzip.decompress((FIX / (name + ".gz")).read_bytes()).decode().rstrip("\n")
        if hashlib.sha256(t.encode()).hexdigest() != m["sha256"] or len(t) != m["length"]:
            raise AssertionError(f"{name}: not the text Neon computed its sha-256 over")
        _CACHE[name] = t
    return _CACHE[name]


def rows(name: str) -> list[dict]:
    r = json.loads(text(name))
    assert len(r) == manifest()[name]["rows"], name
    return r


def tag(version: str) -> str:
    return {V1: "v1", V2: "v2"}[version]


def verdicts(version: str) -> list[dict]:
    return rows(f"verdicts_{tag(version)}.json")


def curves(version: str, product: str) -> list[dict]:
    return rows(f"curves_{product}_{tag(version)}.json")


def _insert(conn, table: str, rs: list[dict]) -> None:
    cols = sorted({k for r in rs for k in r})
    conn.execute(f"INSERT INTO {table} ({', '.join(cols)}) SELECT {', '.join(cols)} "
                 f"FROM json_populate_recordset(NULL::{table}, %s::json)",
                 (json.dumps(rs),))


def build(conn, versions: tuple[str, ...]) -> dict:
    """286 + 298 on an empty database, then each version's rows (each insert
    passes pantry's CHECKs or raises). Returns the row counts loaded."""
    conn.execute("SET TimeZone = 'UTC'")
    conn.execute(DDL.read_text())
    n = {}
    for v in versions:
        vv = verdicts(v)
        _insert(conn, "cpc_curve_verdicts", vv)
        cc = [r for p in PRODUCTS for r in curves(v, p)]
        _insert(conn, "cpc_outlook_curves", cc)
        n[tag(v)] = {"verdicts": len(vv), "curves": len(cc)}
    conn.execute("ANALYZE")
    return n


BANKS = {"v1": (V1,), "v2": (V2,), "both": (V1, V2)}


@contextlib.contextmanager
def banks():
    """One throwaway cluster, one database per bank; yields {name: connection}.
    The caller skips when load_bank_d091673.initdb_path() is None."""
    import psycopg
    from psycopg.rows import dict_row
    import load_bank_d091673 as lb
    with lb.cluster(load_bank=False) as admin:
        host = admin.info.host
        conns = {}
        try:
            for name, versions in BANKS.items():
                admin.execute(f"CREATE DATABASE bank_{name}")
                c = psycopg.connect(f"host={host} user=postgres dbname=bank_{name}",
                                    autocommit=True, row_factory=dict_row)
                conns[name] = c
                build(c, versions)
            yield conns
        finally:
            for c in conns.values():
                c.close()
