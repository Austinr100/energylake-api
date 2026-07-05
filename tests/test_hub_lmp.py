"""
Tests for the CAISO trading-hub LMP endpoint (PR-1):

  * GET /api/timeseries/caiso-hub-lmp

No live database is required. Like the sibling suites, these swap a tiny
in-memory fake pool into `main._pool` so the route's partition/shape logic and
the two SERVER-SIDE derivations (DART = DA − avg RTPD, and the on/off-peak DA
averages) are exercised end-to-end without a real Neon connection. The fake
captures the last query + params for structural assertions (single-read of the
three caiso_lmp_* datasets, the PT window). DB rows are faked as psycopg's
`dict_row` would yield them from the long SELECT (ts, dataset, series, value).
"""

import datetime
import inspect
from zoneinfo import ZoneInfo

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
# Row builder — one long row as the SELECT (ts, dataset, series, value) yields.
# ---------------------------------------------------------------------------

DA = "caiso_lmp_da_hourly"
RTPD = "caiso_lmp_rt_15min"
RTD = "caiso_lmp_rt_5min"

PT = ZoneInfo("America/Los_Angeles")
UTC = datetime.timezone.utc


def _row(ts, dataset, series, value):
    return {
        "ts": datetime.datetime.fromisoformat(ts),
        "dataset": dataset,
        "series": series,
        "value": value,
    }


def _today_pt():
    return datetime.datetime.now(PT).date()


def _pt_hour_utc(date_obj, hour):
    """UTC-aware datetime for a Pacific-clock (date, hour) start."""
    return datetime.datetime(
        date_obj.year, date_obj.month, date_obj.day, hour, tzinfo=PT
    ).astimezone(UTC)


# ---------------------------------------------------------------------------
# Envelope + top-level contract
# ---------------------------------------------------------------------------

def test_envelope_shape_and_sources(client):
    ts = _pt_hour_utc(_today_pt(), 0).isoformat()
    use_rows([
        _row(ts, DA, "NP15", 41.2),
        _row(ts, RTPD, "NP15", 39.8),
        _row(ts, RTD, "NP15", 40.1),
    ])
    resp = client.get("/api/timeseries/caiso-hub-lmp")
    assert resp.status_code == 200
    body = resp.json()

    assert body["tz"] == "America/Los_Angeles"
    assert set(body["window"]) == {"start", "end"}
    assert body["sources"] == {
        "da":   {"dataset": "caiso_lmp_da_hourly", "cadence": "hourly"},
        "rtpd": {"dataset": "caiso_lmp_rt_15min",  "cadence": "15-min"},
        "rtd":  {"dataset": "caiso_lmp_rt_5min",   "cadence": "5-min"},
    }
    assert set(body["last_ts"]) == {"da", "rtpd", "rtd"}
    # All three hubs present in every block, even hubs with no rows.
    for block in ("hubs", "latest", "peak"):
        assert set(body[block]) == {"NP15", "SP15", "ZP26"}
    # Per-hub market keys present.
    assert set(body["hubs"]["NP15"]) == {"da", "rtpd", "rtd", "dart"}
    assert set(body["latest"]["NP15"]) == {"da", "rtpd", "rtd"}
    assert set(body["peak"]["NP15"]) == {"onpeak_avg", "offpeak_avg"}


def test_window_is_prev_and_current_pt_day(client):
    ts = _pt_hour_utc(_today_pt(), 0).isoformat()
    pool = use_rows([_row(ts, RTD, "NP15", 40.1)])
    body = client.get("/api/timeseries/caiso-hub-lmp").json()
    today = _today_pt()
    prev = today - datetime.timedelta(days=1)
    assert body["window"] == {"start": prev.isoformat(), "end": today.isoformat()}
    # Params bind the same PT window.
    assert pool.sink["params"]["lo"] == prev.isoformat()
    assert pool.sink["params"]["hi"] == today.isoformat()
    assert pool.sink["params"]["tz"] == "America/Los_Angeles"


def test_empty_window_is_404(client):
    use_rows([])
    resp = client.get("/api/timeseries/caiso-hub-lmp")
    assert resp.status_code == 404
    assert "hub-LMP" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Arrays: prices as numbers, ordering, null-safety
# ---------------------------------------------------------------------------

def test_arrays_carry_ts_and_numeric_price(client):
    today = _today_pt()
    t0 = _pt_hour_utc(today, 0)
    t1 = _pt_hour_utc(today, 1)
    use_rows([
        _row(t0.isoformat(), DA, "SP15", 30.0),
        _row(t1.isoformat(), DA, "SP15", 31.5),
    ])
    body = client.get("/api/timeseries/caiso-hub-lmp").json()
    da = body["hubs"]["SP15"]["da"]
    assert da == [
        {"ts": t0.astimezone(UTC).isoformat(), "price": 30.0},
        {"ts": t1.astimezone(UTC).isoformat(), "price": 31.5},
    ]
    assert all(isinstance(p["price"], (int, float)) for p in da)


def test_null_value_rows_are_skipped(client):
    today = _today_pt()
    t0 = _pt_hour_utc(today, 0)
    t1 = _pt_hour_utc(today, 1)
    use_rows([
        _row(t0.isoformat(), RTD, "NP15", None),   # skipped, never emitted as null
        _row(t1.isoformat(), RTD, "NP15", 40.1),
    ])
    rtd = client.get("/api/timeseries/caiso-hub-lmp").json()["hubs"]["NP15"]["rtd"]
    assert rtd == [{"ts": t1.astimezone(UTC).isoformat(), "price": 40.1}]


# ---------------------------------------------------------------------------
# DART — server-side DA − avg(RTPD in that hour), positive = DA over RT
# ---------------------------------------------------------------------------

def test_dart_spread_arithmetic_and_sign(client):
    today = _today_pt()
    hour = _pt_hour_utc(today, 10)  # 10:00 PT hour start
    # DA for the hour = 50.0; four RTPD intervals averaging 45.0 -> spread +5.0
    rows = [_row(hour.isoformat(), DA, "NP15", 50.0)]
    for i, px in enumerate((44.0, 46.0, 45.0, 45.0)):  # avg 45.0
        iv = hour + datetime.timedelta(minutes=15 * i)
        rows.append(_row(iv.isoformat(), RTPD, "NP15", px))
    use_rows(rows)
    dart = client.get("/api/timeseries/caiso-hub-lmp").json()["hubs"]["NP15"]["dart"]
    assert dart == [{"ts": hour.astimezone(UTC).isoformat(), "spread": 5.0}]

    # Now an hour where RT cleared ABOVE DA -> negative spread.
    rows = [_row(hour.isoformat(), DA, "SP15", 40.0)]
    for i, px in enumerate((50.0, 50.0)):  # avg 50.0, only two intervals so far
        iv = hour + datetime.timedelta(minutes=15 * i)
        rows.append(_row(iv.isoformat(), RTPD, "SP15", px))
    use_rows(rows)
    dart = client.get("/api/timeseries/caiso-hub-lmp").json()["hubs"]["SP15"]["dart"]
    assert dart == [{"ts": hour.astimezone(UTC).isoformat(), "spread": -10.0}]


def test_dart_only_when_both_da_and_rtpd_present(client):
    today = _today_pt()
    h_da_only = _pt_hour_utc(today, 3)     # DA but no RTPD -> no dart
    h_rtpd_only = _pt_hour_utc(today, 4)   # RTPD but no DA -> no dart
    h_both = _pt_hour_utc(today, 5)        # both -> dart emitted
    use_rows([
        _row(h_da_only.isoformat(), DA, "NP15", 20.0),
        _row(h_rtpd_only.isoformat(), RTPD, "NP15", 22.0),
        _row(h_both.isoformat(), DA, "NP15", 30.0),
        _row(h_both.isoformat(), RTPD, "NP15", 28.0),
    ])
    dart = client.get("/api/timeseries/caiso-hub-lmp").json()["hubs"]["NP15"]["dart"]
    assert dart == [{"ts": h_both.astimezone(UTC).isoformat(), "spread": 2.0}]


def test_dart_uses_rtpd_not_rtd(client):
    # An RTD interval in the same hour must NOT enter the DART average.
    today = _today_pt()
    hour = _pt_hour_utc(today, 12)
    use_rows([
        _row(hour.isoformat(), DA, "NP15", 50.0),
        _row(hour.isoformat(), RTPD, "NP15", 40.0),  # avg RTPD = 40 -> spread 10
        _row(hour.isoformat(), RTD, "NP15", 10.0),   # must be ignored by DART
    ])
    dart = client.get("/api/timeseries/caiso-hub-lmp").json()["hubs"]["NP15"]["dart"]
    assert dart == [{"ts": hour.astimezone(UTC).isoformat(), "spread": 10.0}]


# ---------------------------------------------------------------------------
# latest ticker block
# ---------------------------------------------------------------------------

def test_latest_rt_is_array_tail(client):
    today = _today_pt()
    t0 = _pt_hour_utc(today, 8)
    t1 = t0 + datetime.timedelta(minutes=5)
    use_rows([
        _row(t0.isoformat(), RTD, "NP15", 40.0),
        _row(t1.isoformat(), RTD, "NP15", 41.0),   # freshest
    ])
    latest = client.get("/api/timeseries/caiso-hub-lmp").json()["latest"]["NP15"]
    assert latest["rtd"] == {"ts": t1.astimezone(UTC).isoformat(), "price": 41.0}


def test_latest_da_is_current_pt_hour(client):
    # DA "latest" is the schedule value for the CURRENT PT hour, not the tail.
    now_pt = datetime.datetime.now(PT)
    cur_hour_utc = now_pt.replace(minute=0, second=0, microsecond=0).astimezone(UTC)
    earlier = cur_hour_utc - datetime.timedelta(hours=1)
    use_rows([
        _row(earlier.isoformat(), DA, "NP15", 30.0),
        _row(cur_hour_utc.isoformat(), DA, "NP15", 55.0),  # current PT hour
    ])
    latest = client.get("/api/timeseries/caiso-hub-lmp").json()["latest"]["NP15"]
    assert latest["da"] == {"ts": cur_hour_utc.isoformat(), "price": 55.0}


def test_latest_da_null_when_current_hour_absent(client):
    # Only a past DA hour present -> DA latest is null (schedule, not a tape).
    now_pt = datetime.datetime.now(PT)
    cur_hour_utc = now_pt.replace(minute=0, second=0, microsecond=0).astimezone(UTC)
    earlier = cur_hour_utc - datetime.timedelta(hours=2)
    use_rows([_row(earlier.isoformat(), DA, "NP15", 30.0)])
    latest = client.get("/api/timeseries/caiso-hub-lmp").json()["latest"]["NP15"]
    assert latest["da"] is None


def test_latest_null_for_hub_without_rows(client):
    ts = _pt_hour_utc(_today_pt(), 0).isoformat()
    use_rows([_row(ts, RTD, "NP15", 40.0)])
    latest = client.get("/api/timeseries/caiso-hub-lmp").json()["latest"]
    assert latest["ZP26"] == {"da": None, "rtpd": None, "rtd": None}


# ---------------------------------------------------------------------------
# peak — on/off-peak DA averages for the current trade date
# ---------------------------------------------------------------------------

def test_peak_averages_split_by_nerc_block(client):
    # Build DA rows across today's hours; compute the expected split with the
    # same helper the endpoint uses so the assertion is weekday-agnostic.
    today = _today_pt()
    prices = {2: 20.0, 6: 30.0, 14: 50.0, 20: 55.0, 23: 25.0}
    rows = [_row(_pt_hour_utc(today, h).isoformat(), DA, "NP15", px)
            for h, px in prices.items()]
    use_rows(rows)
    peak = client.get("/api/timeseries/caiso-hub-lmp").json()["peak"]["NP15"]

    on, off = [], []
    for h, px in prices.items():
        ts_pt = datetime.datetime(today.year, today.month, today.day, h, tzinfo=PT)
        (on if main._lmp_is_onpeak(ts_pt) else off).append(px)
    exp_on = round(sum(on) / len(on), 2) if on else None
    exp_off = round(sum(off) / len(off), 2) if off else None
    assert peak["onpeak_avg"] == exp_on
    assert peak["offpeak_avg"] == exp_off


def test_peak_ignores_previous_trade_date(client):
    # A DA row on the PREVIOUS trade date must not contribute to peak (which is
    # the CURRENT trade date only).
    today = _today_pt()
    prev = today - datetime.timedelta(days=1)
    use_rows([
        _row(_pt_hour_utc(prev, 14).isoformat(), DA, "NP15", 99.0),   # prev day
        _row(_pt_hour_utc(today, 2).isoformat(), DA, "NP15", 20.0),   # off-peak today
    ])
    peak = client.get("/api/timeseries/caiso-hub-lmp").json()["peak"]["NP15"]
    # 99.0 (prev day, would-be on-peak) excluded; only the off-peak 20.0 counts.
    assert peak["offpeak_avg"] == 20.0
    assert 99.0 not in (peak["onpeak_avg"], peak["offpeak_avg"])


def test_peak_bucket_with_no_rows_is_null(client):
    # Only an overnight (off-peak) DA hour today -> onpeak_avg is null, not 0.
    today = _today_pt()
    use_rows([_row(_pt_hour_utc(today, 2).isoformat(), DA, "NP15", 20.0)])
    peak = client.get("/api/timeseries/caiso-hub-lmp").json()["peak"]["NP15"]
    assert peak["onpeak_avg"] is None
    assert peak["offpeak_avg"] == 20.0


# ---------------------------------------------------------------------------
# last_ts per market
# ---------------------------------------------------------------------------

def test_last_ts_is_max_per_market(client):
    today = _today_pt()
    a = _pt_hour_utc(today, 8)
    b = a + datetime.timedelta(minutes=5)
    use_rows([
        _row(a.isoformat(), RTD, "NP15", 40.0),
        _row(b.isoformat(), RTD, "SP15", 41.0),   # newest RTD across hubs
        _row(a.isoformat(), RTPD, "NP15", 39.0),
    ])
    body = client.get("/api/timeseries/caiso-hub-lmp").json()
    assert body["last_ts"]["rtd"] == b.astimezone(UTC).isoformat()
    assert body["last_ts"]["rtpd"] == a.astimezone(UTC).isoformat()
    assert body["last_ts"]["da"] is None  # no DA rows supplied


# ---------------------------------------------------------------------------
# ts is emitted in UTC
# ---------------------------------------------------------------------------

def test_ts_emitted_in_utc(client):
    # A row stored with a non-UTC offset still emits as +00:00.
    today = _today_pt()
    pt_hour = datetime.datetime(today.year, today.month, today.day, 9, tzinfo=PT)
    use_rows([_row(pt_hour.isoformat(), RTD, "NP15", 40.0)])
    rtd = client.get("/api/timeseries/caiso-hub-lmp").json()["hubs"]["NP15"]["rtd"]
    assert rtd[0]["ts"].endswith("+00:00")
    assert rtd[0]["ts"] == pt_hour.astimezone(UTC).isoformat()


# ---------------------------------------------------------------------------
# Query structure / source-level guards
# ---------------------------------------------------------------------------

def test_query_is_single_read_of_the_three_datasets(client):
    ts = _pt_hour_utc(_today_pt(), 0).isoformat()
    pool = use_rows([_row(ts, RTD, "NP15", 40.0)])
    client.get("/api/timeseries/caiso-hub-lmp")
    q = pool.sink["query"]
    assert "timeseries_values" in q
    assert "dataset = ANY(%(datasets)s)" in q
    assert "series  = ANY(%(hubs)s)" in q or "series = ANY(%(hubs)s)" in q
    assert "value IS NOT NULL" in q
    assert "AT TIME ZONE %(tz)s" in q
    params = pool.sink["params"]
    assert set(params["datasets"]) == {DA, RTPD, RTD}
    assert params["hubs"] == ["NP15", "SP15", "ZP26"]


def test_derivations_are_read_time_only():
    # DART + peak are read-time projections, computed in the route, never stored.
    src = inspect.getsource(main.caiso_hub_lmp)
    assert "timeseries_values" in src
    assert "_lmp_is_onpeak(" in src
    # The market map is the single source of truth for dataset codes.
    assert main.HUB_LMP_MARKETS["da"]["dataset"] == "caiso_lmp_da_hourly"
    assert main.HUB_LMP_MARKETS["rtpd"]["dataset"] == "caiso_lmp_rt_15min"
    assert main.HUB_LMP_MARKETS["rtd"]["dataset"] == "caiso_lmp_rt_5min"


def test_onpeak_helper_matches_nerc_block():
    # HE7-HE22 Mon-Sat ex-holiday on-peak; nights / Sunday / holiday off-peak.
    def pt(y, m, d, h):
        return datetime.datetime(y, m, d, h, tzinfo=PT)

    # 2026-07-06 is a Monday.
    assert main._lmp_is_onpeak(pt(2026, 7, 6, 14)) is True    # Mon HE15
    assert main._lmp_is_onpeak(pt(2026, 7, 6, 6)) is True     # Mon HE7 edge
    assert main._lmp_is_onpeak(pt(2026, 7, 6, 21)) is True    # Mon HE22 edge
    assert main._lmp_is_onpeak(pt(2026, 7, 6, 5)) is False    # Mon HE6 (pre-peak)
    assert main._lmp_is_onpeak(pt(2026, 7, 6, 22)) is False   # Mon HE23 (post-peak)
    # 2026-07-04 is a Saturday -> on-peak under Mon-Sat block.
    assert main._lmp_is_onpeak(pt(2026, 7, 4, 14)) is True
    # 2026-07-05 is a Sunday -> all off-peak.
    assert main._lmp_is_onpeak(pt(2026, 7, 5, 14)) is False
    # 2026-07-03 is the locked NERC holiday -> off-peak even midday Friday.
    assert main._lmp_is_onpeak(pt(2026, 7, 3, 14)) is False
