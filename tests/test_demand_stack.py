"""
Tests for the CAISO forward demand-stack endpoint:

  * GET /api/timeseries/caiso-demand-stack

No live database is required. Like the sibling forecast-vs-actual suite, these
swap a tiny in-memory fake pool into `main._pool` so the route's flatten-to-wide
shaping and the SERVER-SIDE net-demand subtraction are exercised end-to-end
without a real Neon connection. The fake captures the last query + params for
structural assertions (the two-store read, latest-vintage DISTINCT ON, the
FILTER pivot, the PT window). DB rows are faked as psycopg's `dict_row` would
yield them (the wide pivot the SQL produces), so the tests pin the contract:
`net + solar + wind = total`, net is null-poison-safe, and the is_scored seam.
"""

import datetime
import inspect

import pytest
from fastapi.testclient import TestClient

import main


# ---------------------------------------------------------------------------
# In-memory fake pool (same surface as tests/test_forecast_vs_actual.py).
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
# Row builder — shaped as the flatten-to-wide SQL yields it (dict_row).
# ---------------------------------------------------------------------------

def _stack_row(
    *,
    ts="2026-06-27T01:00:00+00:00",
    total_fc=31000.0,
    solar_fc=0.0,
    wind_fc=4200.0,
    total_act=None,
    solar_act=None,
    wind_act=None,
    is_scored=False,
):
    """One demand-stack wide row (three forecasts + three actuals + seam)."""
    return {
        "target_ts": datetime.datetime.fromisoformat(ts),
        "total_fc": total_fc,
        "solar_fc": solar_fc,
        "wind_fc": wind_fc,
        "total_act": total_act,
        "solar_act": solar_act,
        "wind_act": wind_act,
        "is_scored": is_scored,
    }


# ---------------------------------------------------------------------------
# Response contract + the server-side subtraction
# ---------------------------------------------------------------------------

def test_envelope_and_point_shape(client):
    use_rows([_stack_row()])
    resp = client.get("/api/timeseries/caiso-demand-stack")
    assert resp.status_code == 200
    body = resp.json()

    assert body["tz"] == "America/Los_Angeles"
    assert "start" in body["window"] and "end" in body["window"]
    assert body["sources"] == {
        "total_demand": "7DA load",
        "solar": "DAM solar",
        "wind": "DAM wind",
        "net_demand": "total_demand - solar - wind (server-side)",
    }
    assert body["count"] == 1

    point = body["points"][0]
    assert point == {
        "ts": "2026-06-27T01:00:00+00:00",
        "is_scored": False,
        "total_demand_fc": 31000.0,
        "solar_fc": 0.0,
        "wind_fc": 4200.0,
        "net_demand_fc": 26800.0,        # 31000 - 0 - 4200, server-side
        "total_demand_act": None,
        "solar_act": None,
        "wind_act": None,
        "net_demand_act": None,
    }


def test_net_equals_total_minus_solar_minus_wind_both_sides(client):
    # A fully realized hour: net = total - solar - wind on BOTH fc and act,
    # and net + solar + wind == total holds by construction on both.
    use_rows([_stack_row(
        total_fc=30000.0, solar_fc=5000.0, wind_fc=4000.0,
        total_act=29500.0, solar_act=4800.0, wind_act=4100.0,
        is_scored=True,
    )])
    body = client.get("/api/timeseries/caiso-demand-stack").json()
    p = body["points"][0]

    assert p["net_demand_fc"] == 30000.0 - 5000.0 - 4000.0
    assert p["net_demand_act"] == 29500.0 - 4800.0 - 4100.0
    # The invariant the whole endpoint exists to guarantee:
    assert p["net_demand_fc"] + p["solar_fc"] + p["wind_fc"] == p["total_demand_fc"]
    assert p["net_demand_act"] + p["solar_act"] + p["wind_act"] == p["total_demand_act"]


@pytest.mark.parametrize("missing", ["total_fc", "solar_fc", "wind_fc"])
def test_net_forecast_is_null_when_any_input_missing(client, missing):
    # Null-poison guard: a missing input yields net=NULL, never a partial
    # subtraction against an implicit zero.
    kwargs = {"total_fc": 30000.0, "solar_fc": 5000.0, "wind_fc": 4000.0}
    kwargs[missing] = None
    use_rows([_stack_row(**kwargs)])
    body = client.get("/api/timeseries/caiso-demand-stack").json()
    assert body["points"][0]["net_demand_fc"] is None


def test_net_actual_is_null_when_a_renewable_actual_lags(client):
    # is_scored on the load actual, but wind actual missing -> total present,
    # net_demand_act NULL (fail-honest), not total - solar - 0.
    use_rows([_stack_row(
        total_act=29500.0, solar_act=4800.0, wind_act=None, is_scored=True,
    )])
    p = client.get("/api/timeseries/caiso-demand-stack").json()["points"][0]
    assert p["total_demand_act"] == 29500.0
    assert p["net_demand_act"] is None


def test_seam_and_forward_rows(client):
    # Two realized hours then a forward hour: seam is the last realized ts;
    # the forward row carries actuals=null/is_scored=false but a full forecast
    # stack (the honest trailing edge).
    use_rows([
        _stack_row(ts="2026-06-24T17:00:00+00:00", total_act=33000.0,
                   solar_act=9000.0, wind_act=3000.0, is_scored=True),
        _stack_row(ts="2026-06-24T18:00:00+00:00", total_act=34000.0,
                   solar_act=7000.0, wind_act=3200.0, is_scored=True),
        _stack_row(ts="2026-06-24T19:00:00+00:00", is_scored=False),
    ])
    body = client.get("/api/timeseries/caiso-demand-stack").json()
    assert body["last_scored_ts"] == "2026-06-24T18:00:00+00:00"

    future = body["points"][-1]
    assert future["is_scored"] is False
    assert future["total_demand_act"] is None
    assert future["net_demand_act"] is None
    # Forecast still present past the seam, net computed forward.
    assert future["total_demand_fc"] == 31000.0
    assert future["net_demand_fc"] == 26800.0


# ---------------------------------------------------------------------------
# Latest-vintage / two-store / flatten-to-wide machinery (reused from sibling)
# ---------------------------------------------------------------------------

def test_query_reuses_the_sibling_machinery(client):
    pool = use_rows([_stack_row()])
    client.get("/api/timeseries/caiso-demand-stack")
    q = pool.sink["query"]
    # Both stores are read.
    assert "forecasts_caiso" in q
    assert "actuals_caiso" in q
    # Latest vintage wins per (forecast_type, target_ts) — never blend vintages.
    assert "DISTINCT ON (forecast_type, target_ts)" in q
    assert "generated_ts DESC NULLS LAST" in q
    # Flatten-to-wide via FILTER pivot, one column per series.
    assert "FILTER (WHERE forecast_type = 'load')" in q
    assert "FILTER (WHERE forecast_type = 'solar')" in q
    assert "FILTER (WHERE forecast_type = 'wind')" in q
    # Forecast spine LEFT JOIN actuals so forward rows survive unscored.
    assert "LEFT JOIN act" in q
    # is_scored seam keyed on the load (total-demand) actual.
    assert "(a.total_act IS NOT NULL)" in q


def test_binds_the_baked_in_vintages(client):
    pool = use_rows([_stack_row()])
    client.get("/api/timeseries/caiso-demand-stack")
    params = pool.sink["params"]
    assert params["load_product"] == "7DA"
    assert params["solar_product"] == "DAM"
    assert params["wind_product"] == "DAM"
    assert params["tz"] == "America/Los_Angeles"


# ---------------------------------------------------------------------------
# Window selection
# ---------------------------------------------------------------------------

def test_default_frame_is_today_to_plus_7d_pt(client):
    pool = use_rows([_stack_row()])
    body = client.get("/api/timeseries/caiso-demand-stack").json()
    lo = datetime.date.fromisoformat(pool.sink["params"]["lo"])
    hi = datetime.date.fromisoformat(pool.sink["params"]["hi"])
    # Forward-only frame: lo = today, hi = today + 7d (exactly 7 days apart).
    assert (hi - lo).days == main.DEMAND_STACK_HORIZON_DAYS
    assert body["window"]["start"] == lo.isoformat()
    assert body["window"]["end"] == hi.isoformat()
    # PT day-boundary pattern reused from fuel mix.
    assert "AT TIME ZONE %(tz)s" in pool.sink["query"]


def test_explicit_window_binds_start_end(client):
    pool = use_rows([_stack_row()])
    resp = client.get(
        "/api/timeseries/caiso-demand-stack",
        params={"start": "2026-06-24", "end": "2026-07-01"},
    )
    assert resp.status_code == 200
    assert pool.sink["params"]["lo"] == "2026-06-24"
    assert pool.sink["params"]["hi"] == "2026-07-01"
    assert resp.json()["window"] == {"start": "2026-06-24", "end": "2026-07-01"}


# ---------------------------------------------------------------------------
# Validation + empties
# ---------------------------------------------------------------------------

def test_start_without_end_is_400(client):
    use_rows([_stack_row()])
    resp = client.get(
        "/api/timeseries/caiso-demand-stack", params={"start": "2026-06-24"}
    )
    assert resp.status_code == 400


def test_start_after_end_is_400(client):
    use_rows([_stack_row()])
    resp = client.get(
        "/api/timeseries/caiso-demand-stack",
        params={"start": "2026-07-01", "end": "2026-06-24"},
    )
    assert resp.status_code == 400


def test_bad_date_is_400(client):
    use_rows([_stack_row()])
    resp = client.get(
        "/api/timeseries/caiso-demand-stack",
        params={"start": "2026-6-24", "end": "2026-07-01"},
    )
    assert resp.status_code == 400


def test_empty_window_is_404(client):
    use_rows([])
    resp = client.get(
        "/api/timeseries/caiso-demand-stack",
        params={"start": "2019-01-01", "end": "2019-01-02"},
    )
    assert resp.status_code == 404
    assert "demand-stack" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Source-level guards (caught even if the runtime query changes shape)
# ---------------------------------------------------------------------------

def test_source_does_not_store_the_wide_pivot():
    # The wide pivot + net subtraction are read-time projections — built in
    # SQL/shaping, never persisted. Guard the two-store read + LEFT join +
    # server-side net helper.
    src = inspect.getsource(main.caiso_demand_stack)
    assert "forecasts_caiso" in src
    assert "actuals_caiso" in src
    assert "LEFT JOIN act" in src
    assert "_net_demand(" in src
    # Baked-in vintages live at the source-of-truth map.
    assert main.DEMAND_STACK_FORECAST["load"] == "7DA"
    assert main.DEMAND_STACK_FORECAST["solar"] == "DAM"
    assert main.DEMAND_STACK_FORECAST["wind"] == "DAM"


def test_net_demand_helper_is_null_poison_safe():
    # Unit-level guard on the subtraction primitive itself.
    assert main._net_demand(30000.0, 5000.0, 4000.0) == 21000.0
    assert main._net_demand(None, 5000.0, 4000.0) is None
    assert main._net_demand(30000.0, None, 4000.0) is None
    assert main._net_demand(30000.0, 5000.0, None) is None
