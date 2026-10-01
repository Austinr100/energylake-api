"""d091551 (D-09-25-75) — the two page-load reads that walked whole datasets.

  R1  /api/weather/regime's driver statement is one LIMIT 1 lateral per
      (dataset, series), and the route runs exactly that statement.
  R2  /api/weather/regime's body is byte-identical to the one banked from
      d08d602 (main before this lane) on the same rows.
  D1  /api/timeseries/caiso-peak-demand's body is byte-identical to the one
      banked from d08d602, on a cold memo, a fresh hit and a stale serve.

The banked inputs and bodies are tests/fixtures/polled_routes_d091551/, made by
docs/receipts/polled-routes-d091551/bank.py (whose docstring says which rows
are production's and which are generated). Reds:
docs/receipts/polled-routes-d091551/reds.txt.
"""

import asyncio
import importlib.util
import pathlib
import re

import pytest
from fastapi.testclient import TestClient

import main

_HERE = pathlib.Path(__file__).resolve().parent
FIX = _HERE / "fixtures" / "polled_routes_d091551"
_BANK = _HERE.parent / "docs" / "receipts" / "polled-routes-d091551" / "bank.py"

_spec = importlib.util.spec_from_file_location("polled_routes_bank", _BANK)
bank = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bank)


@pytest.fixture(autouse=True)
def _cold_peak_demand_memo():
    main._peak_demand_cache.clear()
    yield
    main._peak_demand_cache.clear()


@pytest.fixture
def client():
    return TestClient(main.app)


# ═══════════════════════════════════════════════════════════════════════════
# R1 — the regime statement's shape
# ═══════════════════════════════════════════════════════════════════════════

_VALUES = re.compile(r"\(VALUES\s*(.*?)\)\s*AS\s+d\s*\(dataset,\s*series\)", re.S | re.I)
_PAIR = re.compile(r"\(\s*'([^']+)'\s*,\s*'([^']+)'\s*\)")
_LATERAL = re.compile(r"\bLATERAL\s*\((.*?)\)\s*AS\s+l\b", re.S | re.I)


def _regime_problems(sql):
    """[] when `sql` reads one row per (dataset, series) of _REGIME_DRIVERS,
    each through its own LIMIT 1 lateral; else what is wrong."""
    want = [(ds, series) for _key, ds, series in main._REGIME_DRIVERS]
    problems = []
    if re.search(r"DISTINCT\s+ON", sql, re.I):
        problems.append("DISTINCT ON")
    values = _VALUES.search(sql)
    if values is None:
        problems.append("no VALUES (dataset, series) list")
    elif _PAIR.findall(values.group(1)) != want:
        problems.append(f"VALUES {_PAIR.findall(values.group(1))} != {want}")
    laterals = _LATERAL.findall(sql)
    if len(laterals) != 1:
        problems.append(f"{len(laterals)} laterals")
    for body in laterals:
        if body.count("FROM timeseries_values") != 1:
            problems.append("lateral does not read timeseries_values once")
        if not re.search(r"\bt\.dataset\s*=\s*d\.dataset\b", body):
            problems.append("lateral does not name the dataset")
        if not re.search(r"\bt\.series\s*=\s*d\.series\b", body):
            problems.append("lateral does not name the series")
        if not re.search(r"ORDER BY\s+t\.ts\s+DESC\s+LIMIT\s+1\s*$", body.strip(), re.I):
            problems.append("lateral is not ORDER BY ts DESC LIMIT 1")
    if sql.count("timeseries_values") != 1:
        problems.append("timeseries_values read outside the lateral")
    return problems


def test_R1_one_limit_1_lateral_per_dataset_and_series():
    assert _regime_problems(main._REGIME_DRIVERS_SQL) == []
    # one VALUES row per driver chip, so one lookup per dataset
    assert len(_PAIR.findall(_VALUES.search(main._REGIME_DRIVERS_SQL).group(1))) == 5


def test_R1_rehearsed_red_the_shipped_statement_is_caught():
    shipped = """
        SELECT DISTINCT ON (dataset) dataset, series, ts, value
        FROM timeseries_values
        WHERE dataset = ANY(%(datasets)s)
        ORDER BY dataset, ts DESC
    """
    assert "DISTINCT ON" in _regime_problems(shipped)
    # and each half of the lateral's key is load-bearing
    no_series = main._REGIME_DRIVERS_SQL.replace(" AND t.series = d.series", "")
    assert no_series != main._REGIME_DRIVERS_SQL
    assert _regime_problems(no_series) == ["lateral does not name the series"]
    no_limit = main._REGIME_DRIVERS_SQL.replace("LIMIT 1", "")
    assert _regime_problems(no_limit) == ["lateral is not ORDER BY ts DESC LIMIT 1"]


def test_R1_the_route_runs_that_statement(client, monkeypatch):
    now, drivers, cpc, depth = bank.load_regime()
    pool = bank.regime_pool(drivers, cpc, depth)
    monkeypatch.setattr(main, "_pool", pool)
    monkeypatch.setattr(main, "_utcnow", lambda: now)
    assert client.get("/api/weather/regime").status_code == 200
    reads = [q for q in pool.statements if "timeseries_values" in q]
    assert reads == [main._REGIME_DRIVERS_SQL]


# ═══════════════════════════════════════════════════════════════════════════
# R2 — the regime body, before and after
# ═══════════════════════════════════════════════════════════════════════════

def test_R2_regime_body_is_byte_identical_to_the_banked_body(client, monkeypatch):
    now, drivers, cpc, depth = bank.load_regime()
    monkeypatch.setattr(main, "_pool", bank.regime_pool(drivers, cpc, depth))
    monkeypatch.setattr(main, "_utcnow", lambda: now)
    r = client.get("/api/weather/regime")
    assert r.status_code == 200
    assert r.content == (FIX / "regime_body.json").read_bytes()


# ═══════════════════════════════════════════════════════════════════════════
# D1 — the peak-demand body, before and after (and through the memo)
# ═══════════════════════════════════════════════════════════════════════════

def test_D1_peak_demand_body_is_byte_identical_cold_fresh_and_stale(client, monkeypatch):
    banked = (FIX / "peak_demand_body.json").read_bytes()
    now, rows = bank.load_peak()
    pool = bank.peak_pool(rows)
    monkeypatch.setattr(main, "_pool", pool)
    monkeypatch.setattr(main, "_utcnow", lambda: now)

    cold = client.get("/api/timeseries/caiso-peak-demand")
    assert cold.status_code == 200 and cold.content == banked
    assert pool.statements == ["SET LOCAL statement_timeout = '5s'",
                               main.PEAK_DEMAND_SQL]

    fresh = client.get("/api/timeseries/caiso-peak-demand")
    assert fresh.content == banked
    assert len(pool.statements) == 2                 # served from the memo

    # Expired: the stale answer, unchanged, with one refresh behind it.
    main._peak_demand_cache._entries["all"].built_mono -= main.PEAK_DEMAND_MEMO_TTL + 1
    stale = client.get("/api/timeseries/caiso-peak-demand")
    assert stale.content == banked


def test_D1_as_of_is_the_request_instant_not_the_build(client, monkeypatch):
    import datetime
    now, rows = bank.load_peak()
    monkeypatch.setattr(main, "_pool", bank.peak_pool(rows))
    monkeypatch.setattr(main, "_utcnow", lambda: now)
    first = client.get("/api/timeseries/caiso-peak-demand").json()
    later = now + datetime.timedelta(seconds=42)
    monkeypatch.setattr(main, "_utcnow", lambda: later)
    second = client.get("/api/timeseries/caiso-peak-demand").json()
    assert first["as_of"] == now.isoformat()
    assert second["as_of"] == later.isoformat()      # memoised, yet the request's
    first.pop("as_of"), second.pop("as_of")
    assert first == second


def test_D1_twenty_concurrent_cold_requests_make_one_read(monkeypatch):
    import httpx
    now, rows = bank.load_peak()
    pool = bank.peak_pool(rows)
    monkeypatch.setattr(main, "_pool", pool)
    monkeypatch.setattr(main, "_utcnow", lambda: now)

    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),
                                     base_url="http://t") as c:
            return await asyncio.gather(*(c.get("/api/timeseries/caiso-peak-demand")
                                          for _ in range(20)))

    rs = asyncio.run(go())
    assert [r.status_code for r in rs] == [200] * 20
    assert pool.statements.count(main.PEAK_DEMAND_SQL) == 1


def test_D1_a_failed_read_is_a_503_and_not_memoised(client, monkeypatch):
    class _Boom:
        def connection(self):
            raise RuntimeError("connection refused")

    monkeypatch.setattr(main, "_pool", _Boom())
    r = client.get("/api/timeseries/caiso-peak-demand")
    assert r.status_code == 503
    assert r.json()["detail"] == "db unavailable: connection refused"
    assert main._peak_demand_cache._entries == {}


# ═══════════════════════════════════════════════════════════════════════════
# N1 — a CPC vintage with NULL valid dates is a null in the body, not a 500
# (production 2026-10-01: three of four live vintages; the route was dark)
# ═══════════════════════════════════════════════════════════════════════════

def test_N1_regime_survives_null_cpc_valid_dates(client, monkeypatch):
    now, drivers, cpc, depth = bank.load_regime()
    cpc = [{**r, "valid_start": None, "valid_end": None} for r in cpc]
    monkeypatch.setattr(main, "_pool", bank.regime_pool(drivers, cpc, depth))
    monkeypatch.setattr(main, "_utcnow", lambda: now)
    r = client.get("/api/weather/regime")
    assert r.status_code == 200, r.text[:300]
    body = r.json()
    chips = body["cpc"]["chips"]
    held = [c for c in chips if c["issued_date"] is not None]
    assert held and all(c["valid_start"] is None and c["valid_end"] is None for c in held)
