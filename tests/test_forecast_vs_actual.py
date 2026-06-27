"""
Tests for the CAISO forecast-vs-actual endpoint (D-18-07 data layer):

  * GET /api/timeseries/caiso-forecast-vs-actual

No live database is required. Like the other endpoint suites, these swap a
tiny in-memory fake pool into `main._pool` so the route's flatten-to-wide
shaping is exercised end-to-end without a real Neon connection. The fake
captures the last query + params for structural assertions (join, products,
PT window). The DB rows are faked as psycopg's `dict_row` would yield them
(the wide pivot the SQL produces), so the tests pin the response contract.
"""

import datetime
import inspect

import pytest
from fastapi.testclient import TestClient

import main


# ---------------------------------------------------------------------------
# In-memory fake pool (same surface as tests/test_regulatory.py).
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
# Row builders — shaped as the flatten-to-wide SQL yields them (dict_row).
# ---------------------------------------------------------------------------

def _wide_row(
    *,
    ts="2026-06-22T18:00:00+00:00",
    actual=24000.0,
    sevenda=24800.0,
    twoda=24300.0,
    dam=24100.0,
    is_scored=True,
):
    """A representative load wide row (actual + full vintage ladder)."""
    return {
        "target_ts": datetime.datetime.fromisoformat(ts),
        "actual": actual,
        "sevenda": sevenda,
        "twoda": twoda,
        "dam": dam,
        "is_scored": is_scored,
    }


# ---------------------------------------------------------------------------
# Load: response contract + the wide ladder
# ---------------------------------------------------------------------------

def test_load_envelope_and_wide_shape(client):
    use_rows([_wide_row()])
    resp = client.get("/api/timeseries/caiso-forecast-vs-actual", params={"quantity": "load"})
    assert resp.status_code == 200
    body = resp.json()

    assert body["quantity"] == "load"
    assert body["tz"] == "America/Los_Angeles"
    assert body["products"] == ["7DA", "2DA", "DAM"]
    assert body["count"] == 1
    assert "start" in body["window"] and "end" in body["window"]

    point = body["points"][0]
    assert point == {
        "ts": "2026-06-22T18:00:00+00:00",
        "actual": 24000.0,
        "7DA": 24800.0,
        "2DA": 24300.0,
        "DAM": 24100.0,
        "is_scored": True,
    }


def test_load_queries_both_stores_and_maps_the_key(client):
    pool = use_rows([_wide_row()])
    client.get("/api/timeseries/caiso-forecast-vs-actual", params={"quantity": "load"})
    q = pool.sink["query"]
    # Both stores are read, and the join maps forecast_type <-> series.
    assert "forecasts_caiso" in q
    assert "actuals_caiso" in q
    assert "forecast_type = %(quantity)s" in q
    assert "series = %(quantity)s" in q
    # Forecast spine LEFT JOIN actual so future rows survive.
    assert "LEFT JOIN act" in q
    # Latest vintage wins per (target_ts, product).
    assert "DISTINCT ON (target_ts, product)" in q


def test_load_binds_full_product_ladder(client):
    pool = use_rows([_wide_row()])
    client.get("/api/timeseries/caiso-forecast-vs-actual", params={"quantity": "load"})
    assert pool.sink["params"]["products"] == ["7DA", "2DA", "DAM"]
    assert pool.sink["params"]["quantity"] == "load"
    assert pool.sink["params"]["tz"] == "America/Los_Angeles"


def test_load_last_scored_ts_is_the_seam(client):
    # Two realized rows then an unrealized future row — the seam is the last
    # realized ts, and the future row carries actual=null / is_scored=false.
    use_rows([
        _wide_row(ts="2026-06-22T22:00:00+00:00", actual=23000.0, is_scored=True),
        _wide_row(ts="2026-06-22T23:00:00+00:00", actual=22500.0, is_scored=True),
        _wide_row(
            ts="2026-06-23T00:00:00+00:00",
            actual=None,
            is_scored=False,
        ),
    ])
    resp = client.get("/api/timeseries/caiso-forecast-vs-actual", params={"quantity": "load"})
    body = resp.json()
    assert body["last_scored_ts"] == "2026-06-22T23:00:00+00:00"
    future = body["points"][-1]
    assert future["actual"] is None
    assert future["is_scored"] is False
    # Forecast still present past the seam (the honest trailing edge).
    assert future["DAM"] == 24100.0


# ---------------------------------------------------------------------------
# Renewables: DAM-only is correct, not missing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("quantity", ["solar", "wind"])
def test_renewables_are_dam_only(client, quantity):
    pool = use_rows([_wide_row(actual=8000.0, dam=8200.0)])
    resp = client.get(
        "/api/timeseries/caiso-forecast-vs-actual", params={"quantity": quantity}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["products"] == ["DAM"]
    assert pool.sink["params"]["products"] == ["DAM"]
    point = body["points"][0]
    # DAM present; no 7DA/2DA columns — the source has no day-ahead+ renewables.
    assert point["DAM"] == 8200.0
    assert "7DA" not in point
    assert "2DA" not in point
    assert set(point.keys()) == {"ts", "actual", "DAM", "is_scored"}


# ---------------------------------------------------------------------------
# Window selection
# ---------------------------------------------------------------------------

def test_explicit_window_binds_start_end(client):
    pool = use_rows([_wide_row()])
    resp = client.get(
        "/api/timeseries/caiso-forecast-vs-actual",
        params={"quantity": "load", "start": "2026-06-16", "end": "2026-07-01"},
    )
    assert resp.status_code == 200
    assert pool.sink["params"]["lo"] == "2026-06-16"
    assert pool.sink["params"]["hi"] == "2026-07-01"
    body = resp.json()
    assert body["window"] == {"start": "2026-06-16", "end": "2026-07-01"}


def test_default_window_is_rolling_and_pt_bounded(client):
    pool = use_rows([_wide_row()])
    client.get("/api/timeseries/caiso-forecast-vs-actual", params={"quantity": "load"})
    lo = pool.sink["params"]["lo"]
    hi = pool.sink["params"]["hi"]
    # YYYY-MM-DD strings, lo strictly before hi (history then forward horizon).
    assert main._valid_date(lo) and main._valid_date(hi)
    assert lo < hi
    # PT day-boundary pattern reused from fuel mix.
    assert "AT TIME ZONE %(tz)s" in pool.sink["query"]


def test_days_back_and_horizon_widen_the_window(client):
    pool = use_rows([_wide_row()])
    client.get(
        "/api/timeseries/caiso-forecast-vs-actual",
        params={"quantity": "load", "days_back": 3, "horizon": 2},
    )
    lo = datetime.date.fromisoformat(pool.sink["params"]["lo"])
    hi = datetime.date.fromisoformat(pool.sink["params"]["hi"])
    # today-3 .. today+2  => exactly 5 days apart.
    assert (hi - lo).days == 5


# ---------------------------------------------------------------------------
# Date-addressable: ?date single day + past/today seam signal (Brick 1)
# ---------------------------------------------------------------------------

import zoneinfo  # noqa: E402

_TODAY_PT = datetime.datetime.now(
    zoneinfo.ZoneInfo("America/Los_Angeles")
).date()
_TODAY_ISO = _TODAY_PT.isoformat()
_PAST_ISO = (_TODAY_PT - datetime.timedelta(days=3)).isoformat()


def test_date_binds_single_pt_day(client):
    # ?date collapses the window to one PT day (lo == hi == date) and echoes
    # the date + an explicit is_today/seam signal.
    pool = use_rows([_wide_row()])
    resp = client.get(
        "/api/timeseries/caiso-forecast-vs-actual",
        params={"quantity": "load", "date": _PAST_ISO},
    )
    assert resp.status_code == 200
    assert pool.sink["params"]["lo"] == _PAST_ISO
    assert pool.sink["params"]["hi"] == _PAST_ISO
    body = resp.json()
    assert body["window"] == {"start": _PAST_ISO, "end": _PAST_ISO}
    assert body["date"] == _PAST_ISO
    assert "is_today" in body and "seam" in body


def test_date_today_keeps_live_seam(client):
    # date == today (PT): the live realized/forecast seam is preserved — seam
    # is the last realized ts, is_today true.
    use_rows([
        _wide_row(ts="2026-06-22T22:00:00+00:00", actual=23000.0, is_scored=True),
        _wide_row(ts="2026-06-22T23:00:00+00:00", actual=22500.0, is_scored=True),
        _wide_row(ts="2026-06-23T00:00:00+00:00", actual=None, is_scored=False),
    ])
    resp = client.get(
        "/api/timeseries/caiso-forecast-vs-actual",
        params={"quantity": "load", "date": _TODAY_ISO},
    )
    body = resp.json()
    assert body["is_today"] is True
    assert body["seam"] == body["last_scored_ts"] == "2026-06-22T23:00:00+00:00"


def test_date_past_suppresses_seam(client):
    # A fully-realized PAST day: every hour scored, so last_scored_ts lands on
    # the final interval — but seam is null and is_today false, so the chart
    # draws no now-line in the middle of a complete past day (locked invariant).
    use_rows([
        _wide_row(ts="2026-06-22T22:00:00+00:00", actual=23000.0, is_scored=True),
        _wide_row(ts="2026-06-22T23:00:00+00:00", actual=22500.0, is_scored=True),
    ])
    resp = client.get(
        "/api/timeseries/caiso-forecast-vs-actual",
        params={"quantity": "load", "date": _PAST_ISO},
    )
    body = resp.json()
    assert body["is_today"] is False
    assert body["seam"] is None
    # The realized edge is still reported; only the now-line signal is off.
    assert body["last_scored_ts"] == "2026-06-22T23:00:00+00:00"


def test_date_default_unchanged_no_seam_fields(client):
    # No param => default relative window, byte-for-byte: NO date/is_today/seam.
    use_rows([_wide_row()])
    body = client.get(
        "/api/timeseries/caiso-forecast-vs-actual", params={"quantity": "load"}
    ).json()
    assert "date" not in body
    assert "is_today" not in body
    assert "seam" not in body


def test_date_and_range_mutually_exclusive(client):
    use_rows([_wide_row()])
    resp = client.get(
        "/api/timeseries/caiso-forecast-vs-actual",
        params={"quantity": "load", "date": _PAST_ISO, "start": _PAST_ISO, "end": _TODAY_ISO},
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Date-addressable: ?start/?end range -> per-day records (synthesis spine)
# ---------------------------------------------------------------------------

def test_range_returns_per_day_records(client):
    # Rows spanning two PT days -> a `days` array, one record per day, each
    # shaped like the single-date response (date/is_today/seam/points).
    use_rows([
        _wide_row(ts="2026-06-22T18:00:00+00:00", actual=24000.0),  # PT 06-22
        _wide_row(ts="2026-06-23T18:00:00+00:00", actual=25000.0),  # PT 06-23
    ])
    resp = client.get(
        "/api/timeseries/caiso-forecast-vs-actual",
        params={"quantity": "load", "start": "2026-06-22", "end": "2026-06-23"},
    )
    assert resp.status_code == 200
    body = resp.json()
    # Range envelope: per-day records, not a flat points list.
    assert "days" in body and "points" not in body
    assert body["count"] == 2
    dates = [d["date"] for d in body["days"]]
    assert dates == ["2026-06-22", "2026-06-23"]
    day0 = body["days"][0]
    assert set(day0) == {"date", "is_today", "seam", "last_scored_ts", "count", "points"}
    # Each day's points are shaped exactly like the single-date contract.
    assert day0["points"][0] == {
        "ts": "2026-06-22T18:00:00+00:00",
        "actual": 24000.0,
        "7DA": 24800.0,
        "2DA": 24300.0,
        "DAM": 24100.0,
        "is_scored": True,
    }


@pytest.mark.parametrize("quantity", ["solar", "wind"])
def test_renewables_date_mode_dam_only_with_seam(client, quantity):
    # The renewables deviation surface is the same endpoint at quantity=
    # solar/wind: ?date returns DAM-only points plus the is_today/seam signal.
    use_rows([_wide_row(actual=8000.0, dam=8200.0, ts="2026-06-22T18:00:00+00:00")])
    resp = client.get(
        "/api/timeseries/caiso-forecast-vs-actual",
        params={"quantity": quantity, "date": _PAST_ISO},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["products"] == ["DAM"]
    assert body["is_today"] is False and body["seam"] is None
    point = body["points"][0]
    assert set(point.keys()) == {"ts", "actual", "DAM", "is_scored"}


def test_range_cap_rejects_overlong_span(client):
    # > 31-day span is rejected cleanly (bounded synthesis read).
    use_rows([_wide_row()])
    resp = client.get(
        "/api/timeseries/caiso-forecast-vs-actual",
        params={"quantity": "load", "start": "2026-01-01", "end": "2026-03-01"},
    )
    assert resp.status_code == 400
    assert "maximum" in resp.json()["detail"]


def test_range_cap_allows_31_days(client):
    # Exactly 31 days (inclusive) is allowed.
    use_rows([_wide_row(ts="2026-06-01T18:00:00+00:00")])
    resp = client.get(
        "/api/timeseries/caiso-forecast-vs-actual",
        params={"quantity": "load", "start": "2026-06-01", "end": "2026-07-01"},
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Validation + empties
# ---------------------------------------------------------------------------

def test_bad_quantity_is_422(client):
    use_rows([_wide_row()])
    resp = client.get(
        "/api/timeseries/caiso-forecast-vs-actual", params={"quantity": "gas"}
    )
    assert resp.status_code == 422  # pattern rejects it before the handler


def test_missing_quantity_is_422(client):
    use_rows([_wide_row()])
    resp = client.get("/api/timeseries/caiso-forecast-vs-actual")
    assert resp.status_code == 422


def test_start_without_end_is_400(client):
    use_rows([_wide_row()])
    resp = client.get(
        "/api/timeseries/caiso-forecast-vs-actual",
        params={"quantity": "load", "start": "2026-06-16"},
    )
    assert resp.status_code == 400


def test_start_after_end_is_400(client):
    use_rows([_wide_row()])
    resp = client.get(
        "/api/timeseries/caiso-forecast-vs-actual",
        params={"quantity": "load", "start": "2026-07-01", "end": "2026-06-16"},
    )
    assert resp.status_code == 400


def test_empty_window_is_404(client):
    use_rows([])
    resp = client.get(
        "/api/timeseries/caiso-forecast-vs-actual",
        params={"quantity": "load", "start": "2019-01-01", "end": "2019-01-02"},
    )
    assert resp.status_code == 404
    assert "load" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Source-level guards (caught even if the runtime query changes shape)
# ---------------------------------------------------------------------------

def test_source_does_not_store_the_wide_pivot():
    # The wide pivot is a read-time projection — the endpoint must build it in
    # SQL/shaping, never persist it. Guard the two-store read + LEFT join.
    src = inspect.getsource(main.caiso_forecast_vs_actual)
    assert "forecasts_caiso" in src
    assert "actuals_caiso" in src
    assert "LEFT JOIN act" in src
    # Renewables stay DAM-only at the source-of-truth product map.
    assert main.FORECAST_PRODUCTS["solar"] == ["DAM"]
    assert main.FORECAST_PRODUCTS["wind"] == ["DAM"]
    assert main.FORECAST_PRODUCTS["load"] == ["7DA", "2DA", "DAM"]
