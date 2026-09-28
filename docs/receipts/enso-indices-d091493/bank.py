"""
Bank the /api/enso/indices receipt (d091493) from a read-only production pull.

This container cannot reach Neon's Postgres port, so the rows were read through
the Neon SQL console (one read-only SELECT per index, 2026-09-28 UTC):

    SELECT string_agg(to_char(ts AT TIME ZONE 'UTC','YYYY-MM') || ' ' ||
                      coalesce(trim_scale(value)::text, 'null'), ';' ORDER BY ts),
           count(*), count(*) FILTER (WHERE value IS NULL),
           max(ingested_ts),
           encode(sha256(convert_to(<that text>, 'UTF8')), 'hex')
    FROM timeseries_values WHERE dataset = <d> AND series = <s>

source_<index>_2026_09_27.txt is that text, byte for byte — Postgres's own
sha256 is pinned below and checked here. Every row is rebuilt as psycopg would
hand it to the route (ts timestamptz at the month start, value Decimal,
ingested_ts timestamptz) and served through the REAL route (main.app, real
enso_indices module) on a fake pool. The response body bytes are the receipt.

    python docs/receipts/enso-indices-d091493/bank.py
"""

import datetime
import hashlib
import sys
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402

UTC = datetime.timezone.utc
PULL = {  # index -> (dataset, series, postgres sha256 of the text, n, n_null, max(ingested_ts))
    "oni": ("cpc_oni_monthly", "oni",
            "f42aa854ec76fde576a71c6a832254c3b906829ac9340bc9a7bdc7451c372416",
            919, 0, datetime.datetime(2026, 9, 27, 18, 40, 9, 322000, tzinfo=UTC)),
    "roni": ("cpc_roni_monthly", "roni",
             "ec794245022e9fcd5ee290a370d84008054b379fc94fb100bd4133e0baeb30f5",
             919, 0, datetime.datetime(2026, 9, 27, 18, 40, 12, 772000, tzinfo=UTC)),
}


def rows(index):
    dataset, series, sha, n, n_null, ingested = PULL[index]
    text = (HERE / f"source_{index}_2026_09_27.txt").read_bytes().rstrip(b"\n")
    assert hashlib.sha256(text).hexdigest() == sha, f"{index} source text drifted"
    out = []
    for item in text.decode().split(";"):
        month, value = item.split(" ")
        y, m = map(int, month.split("-"))
        out.append({"ts": datetime.datetime(y, m, 1, tzinfo=UTC), "dataset": dataset,
                    "series": series,
                    "value": None if value == "null" else Decimal(value),
                    "ingested_ts": ingested})
    assert len(out) == n and sum(r["value"] is None for r in out) == n_null
    return out


class _Cur:
    def __init__(self, table):
        self.table, self.rows = table, []
    async def __aenter__(self):
        return self
    async def __aexit__(self, *exc):
        return False
    async def execute(self, query, params):
        assert query == main._enso_idx.SERIES_SQL
        self.rows = [{"ts": r["ts"], "value": r["value"], "ingested_ts": r["ingested_ts"]}
                     for r in self.table
                     if (r["dataset"], r["series"]) == (params["d"], params["s"])]
    async def fetchall(self):
        return self.rows


class _Conn:
    def __init__(self, table):
        self.table = table
    async def __aenter__(self):
        return self
    async def __aexit__(self, *exc):
        return False
    def cursor(self):
        return _Cur(self.table)


class _Pool:
    def __init__(self, table):
        self.table = table
    def connection(self):
        return _Conn(self.table)


if __name__ == "__main__":
    main._pool = _Pool(rows("oni") + rows("roni"))
    resp = TestClient(main.app).get("/api/enso/indices")
    assert resp.status_code == 200, resp.text
    out = HERE / "indices_2026_09_27.json"
    out.write_bytes(resp.content)
    digest = hashlib.sha256(resp.content).hexdigest()
    (HERE / "indices_2026_09_27.json.sha256").write_text(f"{digest}  indices_2026_09_27.json\n")
    print(f"status {resp.status_code}  ETag {resp.headers['etag']}  "
          f"Cache-Control {resp.headers['cache-control']}  bytes {len(resp.content)}")
    print(f"sha256 {digest}")
