"""
Tests for the CAISO trading-hub LMP endpoint:

  * GET /api/timeseries/caiso-hub-lmp

No live database is required. Like the sibling fuel-mix / demand-stack suites,
these swap a tiny in-memory fake pool into `main._pool` so the route's windowed
read and its shaping (dataset -> market bucketing, hub grouping, null-value
skip, per-market last_ts) are exercised end-to-end without a real Neon
connection. The fake captures the last query + params for structural assertions
(the single windowed timeseries_values read, the PT day boundary, the
dataset/hub ANY() binds). DB rows are faked as psycopg's `dict_row` would yield
them (ts, dataset, series, value), so the tests pin the contract shape.
"""

import datetime
import inspect

import pytest
from fastapi.testclient import TestClient

import main


# ---------------------------------------------------------------------------
# In-memory fake pool (same surface as tests/test_demand_stack.py).
# ---------------------------------------------------------------------------

class _FakeCursor:
    def __init__(self, rows, sink):
        self._rows = rows
        self._sink = sink

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, query, params=None):
        self._sink["query"] = query
        self._sink["params"] = params

    async def fetchall(self):
        return list(self._rows)

    async def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeConn:
    def __init__(self, rows, sink):
        self._rows = rows
        self._sink = sink

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def cursor(self):
        return _FakeCursor(self._rows, self._sink)


class FakePool:
    def __init__(self, rows):
        self._rows = rows
        self.sink = {}

    def connection(self):
        return _FakeConn(self._rows, self.sink)


@pytest.fixture
def client():
    # No `with` block => lifespan does not run => real pool is never opened.
    return TestClient(main.app)


def use_rows(rows):
    """Install a fake pool returning `rows`; return it for query inspection."""
    pool = FakePool(rows)
    main._pool = pool
    return pool


# ---------------------------------------------------------------------------
# Row builder — shaped as the windowed SQL yields it (dict_row).
# ---------------------------------------------------------------------------

def _row(*, ts, dataset, series, value):
    """One timeseries_values row: (ts, dataset, series, value)."""
    return {
        "ts": datetime.datetime.fromisoformat(ts),
        "dataset": dataset,
        "series": series,
        "value": value,
    }


DA = "caiso_lmp_da_hourly"
RTPD = "caiso_lmp_rt_15min"
RTD = "caiso_lmp_rt_5min"


# ---------------------------------------------------------------------------
# Response contract + shaping
# ---------------------------------------------------------------------------

def test_envelope_and_full_shape(client):
    use_rows([
        _row(ts="2026-07-04T07:00:00+00:00", dataset=DA,   series="NP15", value=42.15),
        _row(ts="2026-07-04T07:00:00+00:00", dataset=RTPD, series="NP15", value=41.9),
        _row(ts="2026-07-04T07:00:00+00:00", dataset=RTD,  series="NP15", value=40.0),
        _row(ts="2026-07-04T07:00:00+00:00", dataset=DA,   series="SP15", value=45.0),
        _row(ts="2026-07-04T07:00:00+00:00", dataset=DA,   series="ZP26", value=44.0),
    ])
    resp = client.get("/api/timeseries/caiso-hub-lmp")
    assert resp.status_code == 200
    body = resp.json()

    assert body["tz"] == "America/Los_Angeles"
    assert "start" in body["window"] and "end" in body["window"]
    assert body["sources"] == {
        "da":   {"dataset": DA,   "cadence": "hourly"},
        "rtpd": {"dataset": RTPD, "cadence": "15-min"},
        "rtd":  {"dataset": RTD,  "cadence": "5-min"},
    }

    # All three hubs present, each with all three markets present.
    assert set(body["hubs"]) == {"NP15", "SP15", "ZP26"}
    for hub in ("NP15", "SP15", "ZP26"):
        assert set(body["hubs"][hub]) == {"da", "rtpd", "rtd"}

    # Points shaped {ts, price}, price a number.
    assert body["hubs"]["NP15"]["da"] == [
        {"ts": "2026-07-04T07:00:00+00:00", "price": 42.15}
    ]
    assert body["hubs"]["NP15"]["rtpd"][0]["price"] == 41.9
    assert body["hubs"]["NP15"]["rtd"][0]["price"] == 40.0
    assert isinstance(body["hubs"]["SP15"]["da"][0]["price"], (int, float))

    # A market with no rows for a hub is an empty list, not missing/null.
    assert body["hubs"]["SP15"]["rtpd"] == []
    assert body["hubs"]["ZP26"]["rtd"] == []


def test_last_ts_is_max_per_market(client):
    # last_ts per market = the latest ts present for that dataset in the window.
    use_rows([
        _row(ts="2026-07-04T07:00:00+00:00", dataset=DA,   series="NP15", value=10.0),
        _row(ts="2026-07-04T08:00:00+00:00", dataset=DA,   series="NP15", value=11.0),
        _row(ts="2026-07-04T07:00:00+00:00", dataset=RTD,  series="SP15", value=9.0),
        _row(ts="2026-07-04T07:05:00+00:00", dataset=RTD,  series="SP15", value=9.5),
    ])
    body = client.get("/api/timeseries/caiso-hub-lmp").json()
    assert body["last_ts"]["da"] == "2026-07-04T08:00:00+00:00"
    assert body["last_ts"]["rtd"] == "2026-07-04T07:05:00+00:00"
    # A market with no rows -> last_ts is null.
    assert body["last_ts"]["rtpd"] is None


def test_null_value_rows_are_skipped(client):
    # Null prices are skipped rather than emitted as null points.
    use_rows([
        _row(ts="2026-07-04T07:00:00+00:00", dataset=DA, series="NP15", value=None),
        _row(ts="2026-07-04T08:00:00+00:00", dataset=DA, series="NP15", value=50.0),
    ])
    body = client.get("/api/timeseries/caiso-hub-lmp").json()
    da_points = body["hubs"]["NP15"]["da"]
    assert da_points == [{"ts": "2026-07-04T08:00:00+00:00", "price": 50.0}]
    # last_ts reflects only non-null rows.
    assert body["last_ts"]["da"] == "2026-07-04T08:00:00+00:00"


def test_points_preserve_ascending_ts(client):
    use_rows([
        _row(ts="2026-07-04T07:00:00+00:00", dataset=RTPD, series="ZP26", value=1.0),
        _row(ts="2026-07-04T07:15:00+00:00", dataset=RTPD, series="ZP26", value=2.0),
        _row(ts="2026-07-04T07:30:00+00:00", dataset=RTPD, series="ZP26", value=3.0),
    ])
    pts = client.get("/api/timeseries/caiso-hub-lmp").json()["hubs"]["ZP26"]["rtpd"]
    assert [p["price"] for p in pts] == [1.0, 2.0, 3.0]
    assert [p["ts"] for p in pts] == [
        "2026-07-04T07:00:00+00:00",
        "2026-07-04T07:15:00+00:00",
        "2026-07-04T07:30:00+00:00",
    ]


# ---------------------------------------------------------------------------
# Window selection — fixed previous + current PT trade date
# ---------------------------------------------------------------------------

def test_window_is_prev_and_current_pt_trade_date(client):
    pool = use_rows([_row(ts="2026-07-04T07:00:00+00:00", dataset=DA,
                          series="NP15", value=10.0)])
    body = client.get("/api/timeseries/caiso-hub-lmp").json()

    lo = datetime.date.fromisoformat(pool.sink["params"]["lo"])
    hi = datetime.date.fromisoformat(pool.sink["params"]["hi"])
    # Exactly one PT day apart (previous + current trade date).
    assert (hi - lo).days == 1
    # "Today" is the current PT calendar day.
    today_pt = datetime.datetime.now(main.ZoneInfo(main.MARKET_TZ)).date()
    assert hi == today_pt
    assert body["window"] == {"start": lo.isoformat(), "end": hi.isoformat()}


def test_endpoint_takes_no_params(client):
    # v1 is a fixed window: no hub / date / range params. Extra query params are
    # simply ignored by FastAPI (the signature declares none).
    use_rows([_row(ts="2026-07-04T07:00:00+00:00", dataset=DA,
                   series="NP15", value=10.0)])
    resp = client.get("/api/timeseries/caiso-hub-lmp",
                      params={"hub": "NP15", "date": "2026-07-04"})
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Read machinery (structural guards on the SQL)
# ---------------------------------------------------------------------------

def test_query_is_a_single_windowed_timeseries_read(client):
    pool = use_rows([_row(ts="2026-07-04T07:00:00+00:00", dataset=DA,
                          series="NP15", value=10.0)])
    client.get("/api/timeseries/caiso-hub-lmp")
    q = pool.sink["query"]
    # Reads the shared timeseries_values table.
    assert "timeseries_values" in q
    # Dataset + hub set filters bind as arrays.
    assert "dataset = ANY(%(datasets)s)" in q
    assert "series  = ANY(%(hubs)s)" in q
    # PT day-boundary window pattern reused from fuel mix.
    assert "AT TIME ZONE %(tz)s" in q
    # Ascending ts so last_ts derivation from the last-seen row is the max.
    assert "ORDER BY ts ASC" in q
    # No writes anywhere.
    assert "INSERT" not in q.upper()
    assert "UPDATE" not in q.upper()


def test_binds_the_three_datasets_and_hubs(client):
    pool = use_rows([_row(ts="2026-07-04T07:00:00+00:00", dataset=DA,
                          series="NP15", value=10.0)])
    client.get("/api/timeseries/caiso-hub-lmp")
    params = pool.sink["params"]
    assert set(params["datasets"]) == {DA, RTPD, RTD}
    assert params["hubs"] == ["NP15", "SP15", "ZP26"]
    assert params["tz"] == "America/Los_Angeles"


# ---------------------------------------------------------------------------
# Empties
# ---------------------------------------------------------------------------

def test_empty_window_is_404(client):
    use_rows([])
    resp = client.get("/api/timeseries/caiso-hub-lmp")
    assert resp.status_code == 404
    assert "hub-LMP" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Source-level guards
# ---------------------------------------------------------------------------

def test_source_is_read_only_and_maps_markets():
    src = inspect.getsource(main.caiso_hub_lmp)
    assert "timeseries_values" in src
    assert "INSERT" not in src.upper()
    assert "UPDATE " not in src.upper()
    # Market -> dataset map is the source of truth.
    assert main.HUB_LMP_MARKETS["da"]["dataset"] == "caiso_lmp_da_hourly"
    assert main.HUB_LMP_MARKETS["rtpd"]["dataset"] == "caiso_lmp_rt_15min"
    assert main.HUB_LMP_MARKETS["rtd"]["dataset"] == "caiso_lmp_rt_5min"
    assert main.HUB_LMP_HUBS == ["NP15", "SP15", "ZP26"]
