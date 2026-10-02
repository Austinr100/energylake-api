"""d091551 — bank the before-bodies for R2 (regime) and D1 (peak demand).

    python docs/receipts/polled-routes-d091551/bank.py --inputs
        writes the two input row sets under tests/fixtures/polled_routes_d091551/
    python docs/receipts/polled-routes-d091551/bank.py --source DIR
        imports main.py from DIR (a checkout of the code BEFORE this lane, e.g.
        `git worktree add DIR d08d602`), serves both routes over the banked
        inputs with the clock frozen, and writes the raw response bytes as
        regime_body.json / peak_demand_body.json.
    python docs/receipts/polled-routes-d091551/bank.py --source DIR --check
        the same, but compares instead of writing (exit 1 on any byte).

The bodies were banked from d08d602 (main before this lane). The tests
(tests/test_polled_routes_d091551.py R2, D1) serve the same inputs through this
branch's code and compare bytes.

Inputs, honestly labelled:
  * regime: the five driver rows are production's, read 2026-10-01 through
    the Neon connector (the newest row of each dataset). The CPC row is
    production's 814temp vintage. The other three CPC products are left out:
    production has them with valid_start / valid_end NULL, and the route
    raises on that before and after this lane (see the handback).
  * peak demand: the 36 series names are production's (2026-10-01); the
    values are generated (a deterministic diurnal curve per series), not read.
    The shape covers every branch the derivation has: a tie, a partial edge
    day, a series missing a date, a row whose meta has no publish_time, and an
    unparseable publish_time.
"""

import argparse
import datetime
import importlib
import json
import math
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
FIX = ROOT / "tests" / "fixtures" / "polled_routes_d091551"

NOW = "2026-10-01T17:24:00+00:00"          # the frozen request instant

REGIME_DRIVERS = [
    {"dataset": "climate_iod_dmi_monthly", "series": "iod_dmi",
     "ts": "2026-05-01T00:00:00+00:00", "value": "0.146000"},
    {"dataset": "climate_pdo_monthly", "series": "pdo",
     "ts": "2026-07-01T00:00:00+00:00", "value": "-2.030000"},
    {"dataset": "climate_qbo_monthly", "series": "qbo",
     "ts": "2026-02-01T00:00:00+00:00", "value": "-23.130000"},
    {"dataset": "cpc_oni_monthly", "series": "oni",
     "ts": "2026-07-01T00:00:00+00:00", "value": "1.800000"},
    {"dataset": "cpc_roni_monthly", "series": "roni",
     "ts": "2026-07-01T00:00:00+00:00", "value": "1.360000"},
]
REGIME_CPC = [
    {"product": "814temp", "issued_date": "2026-09-30",
     "valid_start": "2026-10-08", "valid_end": "2026-10-14",
     "artifact_format": "shapefile"},
]
REGIME_DEPTH = "2021-10-26"

PEAK_SERIES = (
    "AVA|AVRN|AZPS|BANC|BANCMID|BANCRDNG|BANCRSVL|BANCSMUD|BANCWASN|BHBA|BPAT|"
    "CA ISO-TAC|EPE|GWA|IPCO|LADWP|MWD-TAC|NEVP|NWMT|PACE|PACW|PGE|PGE-TAC|PNM|"
    "PSEI|SCE-TAC|SCL|SDGE-TAC|SRP|TEPC|TIDC|TPWR|VEA-TAC|WALC|WALCAEPCO|WALCDSW"
).split("|")


def _peak_rows():
    """Five PT operating dates (2026-09-24 .. 09-28), hourly, interval-
    beginning UTC, in the route's ORDER BY series, ts."""
    first = datetime.datetime(2026, 9, 24, 7, tzinfo=datetime.timezone.utc)  # 00:00 PDT
    hours = 4 * 24 + 13                    # the fifth day is partial: 13 hours
    rows = []
    for i, series in enumerate(sorted(PEAK_SERIES)):
        base = 400.0 + 97.0 * i
        for h in range(hours):
            ts = first + datetime.timedelta(hours=h)
            op = (ts - datetime.timedelta(hours=7)).date()
            if series == "BANCWASN" and op == datetime.date(2026, 9, 27):
                continue                   # a null cell for one date
            local_hour = (ts.hour - 7) % 24
            curve = 0.75 + 0.25 * math.sin((local_hour - 11) / 24 * 2 * math.pi)
            value = round(base * curve * (1 + 0.01 * (op.day % 5)), 2)
            if series == "SCE-TAC" and op == datetime.date(2026, 9, 25) \
                    and local_hour in (16, 18):
                value = 99999.99           # a tie: HE17 and HE19; the earliest wins
            vintage = op - datetime.timedelta(days=7)
            publish = f"{vintage.isoformat()}T16:10:00+00:00"
            if series == "AVA" and op == datetime.date(2026, 9, 26) and local_hour == 0:
                publish = None             # a row whose meta has no publish_time
            if series == "AVA" and op == datetime.date(2026, 9, 26) and local_hour == 1:
                publish = "not-a-time"     # sorts before every real one
            # Stored compact: [ts, series, value, publish_time | null].
            rows.append([ts.isoformat(), series, value, publish])
    return rows


def write_inputs():
    FIX.mkdir(parents=True, exist_ok=True)
    (FIX / "regime_rows.json").write_text(json.dumps(
        {"now": NOW, "drivers": REGIME_DRIVERS, "cpc": REGIME_CPC,
         "depth": REGIME_DEPTH}, indent=1) + "\n")
    (FIX / "peak_demand_rows.json").write_text(json.dumps(
        {"now": NOW, "rows": _peak_rows()}, separators=(",", ":")) + "\n")


# ── Serving the banked inputs through a given main.py ───────────────────────

def load_regime():
    raw = json.loads((FIX / "regime_rows.json").read_text())
    from decimal import Decimal
    drivers = [{**r, "ts": datetime.datetime.fromisoformat(r["ts"]),
                "value": Decimal(r["value"])} for r in raw["drivers"]]
    d = datetime.date.fromisoformat
    cpc = [{**r, "issued_date": d(r["issued_date"]),
            "valid_start": d(r["valid_start"]), "valid_end": d(r["valid_end"])}
           for r in raw["cpc"]]
    return (datetime.datetime.fromisoformat(raw["now"]), drivers, cpc,
            [{"depth": d(raw["depth"])}])


def load_peak():
    raw = json.loads((FIX / "peak_demand_rows.json").read_text())
    rows = []
    for ts, series, value, publish in raw["rows"]:
        meta = {"product": "7DA", "horizon_days": 7,
                "generated_ts_source": "oasis_convention"}
        if publish is not None:
            meta["publish_time"] = publish
        rows.append({"ts": datetime.datetime.fromisoformat(ts), "series": series,
                     "value": value, "meta": meta})
    return datetime.datetime.fromisoformat(raw["now"]), rows


class RoutingPool:
    """(substring, rows) routes; first match wins. Has transaction() so the
    same pool serves the code before and after this lane."""

    def __init__(self, routes):
        self.routes, self.statements = routes, []

    def connection(self):
        pool = self

        class _Cur:
            rows = []

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def execute(self, query, params=None):
                pool.statements.append(query)
                self.rows = []
                for needle, rows in pool.routes:
                    if needle in query:
                        self.rows = rows
                        break

            async def fetchall(self):
                return list(self.rows)

            async def fetchone(self):
                return self.rows[0] if self.rows else None

        class _Tx:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

        class _Conn:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            def cursor(self):
                return _Cur()

            def transaction(self):
                return _Tx()

        return _Conn()


def regime_pool(drivers, cpc, depth):
    return RoutingPool([("SET LOCAL", []), ("timeseries_values", drivers),
                        ("MIN(issued_date)", depth), ("cpc_outlook_vintage", cpc)])


def peak_pool(rows):
    return RoutingPool([("SET LOCAL", []), ("timeseries_values", rows)])


def serve(main, path, pool, now):
    from fastapi.testclient import TestClient
    main._pool = pool
    main._utcnow = lambda: now
    cache = getattr(main, "_peak_demand_cache", None)
    if cache is not None:
        cache.clear()
    r = TestClient(main.app).get(path)
    assert r.status_code == 200, (path, r.status_code, r.text[:200])
    return r.content


def bodies(main):
    now, drivers, cpc, depth = load_regime()
    regime = serve(main, "/api/weather/regime", regime_pool(drivers, cpc, depth), now)
    now, rows = load_peak()
    peak = serve(main, "/api/timeseries/caiso-peak-demand", peak_pool(rows), now)
    return {"regime_body.json": regime, "peak_demand_body.json": peak}


def main_cli():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inputs", action="store_true")
    ap.add_argument("--source")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    if a.inputs:
        write_inputs()
        print("inputs written")
    if a.source:
        sys.path.insert(0, str(pathlib.Path(a.source).resolve()))
        main = importlib.import_module("main")
        print("main from", main.__file__)
        bad = 0
        for name, content in bodies(main).items():
            p = FIX / name
            if a.check:
                same = p.read_bytes() == content
                bad += not same
                print(name, "identical" if same else "DIFFERS", len(content), "bytes")
            else:
                p.write_bytes(content)
                print(name, "written", len(content), "bytes")
        sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main_cli()
