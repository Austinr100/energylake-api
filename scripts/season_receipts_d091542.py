"""
Bank d091542's receipts: the branch's own routes, served from production rows.

The container this lane ran in cannot open a socket to Neon (the egress proxy
refuses it), so the production rows were read through the Neon console's SQL
runner, read-only, on 2026-10-01 (~12:40Z), and saved as four pulls:

  areas_aggregates.json        every /areas GROUP BY, the branch's SQL verbatim
                               with its parameters written in (season.py
                               AREAS_*_SQL), one json_agg per family
  snow_basin_index_daily.json  the seven `{basin}.SWE_PCT` series, every row
                               (date=value), and the frontier row's meta
  cdec_snow_and_reservoirs.json  CDEC's four `{region}_avg_swc` and the eight
                               reservoirs, every row
  enso.json                    the newest catalog run per classifier, its year
                               bins, and the newest ONI / RONI month

This script answers each statement the routes execute from those pulls (a
stand-in pool, the tests' pattern) and calls the routes through FastAPI's test
client: what it writes is what the branch serves from those rows. Each
response is written to docs/receipts/season-regions-snapshot/ with its sha-256
in SHA256SUMS. Usage:

    python scripts/season_receipts_d091542.py <pulls dir>
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import main  # noqa: E402
import season  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

OUT = ROOT / "docs" / "receipts" / "season-regions-snapshot"


def _d(s):
    return None if s is None else date.fromisoformat(s[:10])


def _series(text: str) -> list[tuple[date, float | None]]:
    out = []
    for part in text.split(";"):
        k, v = part.split("=")
        out.append((date.fromisoformat(k), None if v == "null" else float(v)))
    return out


class Pulls:
    def __init__(self, d: Path):
        def load(name):
            p = d / name
            if not p.exists():
                p = d / (name + ".gz")
                return json.loads(gzip.decompress(p.read_bytes()))
            return json.loads(p.read_text())
        self.areas = {r["family"]: r["rows"] for r in load("areas_aggregates.json")}
        for rows in self.areas.values():
            for r in rows:
                for k in ("last", "last7"):
                    if k in r:
                        r[k] = _d(r[k])
        self.snow = {r["series"]: (_series(r["d"]), r["meta"])
                     for r in load("snow_basin_index_daily.json")}
        self.cdec = {(r["dataset"], r["series"]): _series(r["d"])
                     for r in load("cdec_snow_and_reservoirs.json")}
        self.enso = {r["classifier"]: r for r in load("enso.json")}


class _Cur:
    def __init__(self, pool):
        self.pool, self._rows = pool, []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, q, params=None):
        self.pool.statements += 1
        self._rows = self.pool.answer(q, params or {})

    async def fetchall(self):
        return self._rows

    async def fetchone(self):
        return self._rows[0] if self._rows else None


class _Conn:
    def __init__(self, pool):
        self.pool = pool

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def cursor(self):
        return _Cur(self.pool)


class PullPool:
    """Answers the season routes' statements from the pulls."""

    def __init__(self, pulls: Pulls):
        self.p, self.statements = pulls, 0

    def connection(self):
        return _Conn(self)

    def answer(self, q, p):
        A = self.p.areas
        fam = {season.AREAS_PRECIP_SQL: "precip", season.AREAS_STATION_DD_SQL: "station_dd",
               season.AREAS_LWT_SQL: "lwt", season.AREAS_SNOW_SQL: "snow",
               season.AREAS_CA_SNOW_SQL: "ca_snow", season.AREAS_RESERVOIR_SQL: "reservoir",
               season.AREAS_RESERVOIR_REGIONS_SQL: "reservoir_regions",
               season.AREAS_LOAD_SQL: "load"}.get(q)
        if fam is not None:
            return A[fam]
        if q == season.SNOW_SQL:
            rows, meta = self.p.snow[p["s"]]
            last = max(d for d, v in rows if v is not None)
            return [{"obs_date": d, "v": v, "meta": meta if d == last else None}
                    for d, v in rows]
        if q == season.LWT_SQL and p["d"] in (season.CA_SNOW_DATASET, season.RESERVOIR_DATASET):
            return [{"obs_date": d, "v": v} for d, v in self.p.cdec[(p["d"], p["s"])]]
        if q == season.RESERVOIRS_SQL:
            rows = [(d, s, v) for s in p["s"] for d, v in self.p.cdec[(p["d"], s)]]
            return [{"obs_date": d, "series": s, "v": v} for d, s, v in sorted(rows)]
        if q == main._enso.RUN_SQL:
            r = self.p.enso[p["c"]]
            return [{"classifier": p["c"], "developing": r["developing"],
                     "catalog_version": r["catalog_version"]}]
        if q == main._enso.YEAR_BINS_SQL:
            return self.p.enso[p["c"]]["bins"]
        if q == season.ONI_LAST_SQL:
            c = "cpc_oni" if p["s"] == "oni" else "roni"
            ts = datetime.fromisoformat(self.p.enso[c]["oni_last"].replace("Z", "+00:00"))
            return [{"ts": ts.astimezone(timezone.utc)}]
        raise AssertionError(f"no pull answers: {q[:80]!r} {p}")


GETS = (
    ("areas.json", "/api/weather/season/areas", {}),
    ("snapshot_swe_frontier.json", "/api/weather/season/snapshot",
     {"data_type": "snow_water_equivalent"}),
    ("snapshot_swe_2026-04-01.json", "/api/weather/season/snapshot",
     {"data_type": "snow_water_equivalent", "date": "2026-04-01"}),
    ("snapshot_swe_2026-04-01_nino.json", "/api/weather/season/snapshot",
     {"data_type": "snow_water_equivalent", "date": "2026-04-01", "cat": "nino"}),
    ("snapshot_storage_frontier.json", "/api/weather/season/snapshot",
     {"data_type": "reservoir_storage"}),
    ("season_reservoir_ca_north.json", "/api/weather/season",
     {"area": "reservoir:ca_north", "var": "storage"}),
    ("season_reservoir_ca_central.json", "/api/weather/season",
     {"area": "reservoir:ca_central", "var": "storage"}),
    ("season_reservoir_ca_south.json", "/api/weather/season",
     {"area": "reservoir:ca_south", "var": "storage"}),
)


def main_(pulls_dir: str) -> None:
    pool = PullPool(Pulls(Path(pulls_dir)))
    main._pool = None                    # the season routes must not need it
    main._season_pool = pool
    client = TestClient(main.app)
    OUT.mkdir(parents=True, exist_ok=True)
    sums = []
    for name, url, params in GETS:
        r = client.get(url, params=params)
        assert r.status_code == 200, (url, params, r.status_code, r.text[:300])
        body = r.json()
        for k in ("built_at",):        # the wall clock of this run, not of the data
            body[k] = "(receipt run)"
        raw = (json.dumps(body, ensure_ascii=False, indent=1, sort_keys=False) + "\n").encode()
        (OUT / name).write_bytes(raw)
        sums.append(f"{hashlib.sha256(raw).hexdigest()}  {name}")
        print(f"{name}: {len(raw)} bytes")
    (OUT / "SHA256SUMS").write_text("\n".join(sums) + "\n")


if __name__ == "__main__":
    main_(sys.argv[1])
