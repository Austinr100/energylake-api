"""
Tests for the CAISO pnode price-history endpoint (PR-1):

  * GET /api/atlas/pnode-history?pnode_id=<id>&market={RTD|RTPD|DAM}&hours={24|168}

No live database is required. Like the sibling suites, these swap a tiny
in-memory fake pool into `main._pool`. The endpoint issues ONE query whose
result set is: a `known` universe-membership probe LEFT JOINed to the `hist`
range scan, so every row carries `is_known` plus (for real instants) the
market-coordinate + component columns. The fake feeds the rows that joined
SELECT *would* return and the tests assert the route's columnar shaping,
ascending order passthrough, null passthrough, the known/empty/unknown split,
array alignment, and the 400/503 error contract.

The route emits NO server-derived timestamp (captain-adjudicated 2026-07-08):
it mirrors the hot tier's market_date / market_hour / market_interval verbatim
as parallel arrays and the dashboard derives the ISO time axis. Rows are faked
as psycopg's `dict_row` would yield them from the joined SELECT.
"""

import datetime
import inspect

import pytest
from fastapi.testclient import TestClient

import main


# ---------------------------------------------------------------------------
# In-memory fake pool (same surface as tests/test_pnode_lmp.py).
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


class _BoomPool:
    """Pool whose connection() raises — simulates DB unavailability."""

    def __init__(self):
        self.sink = {}

    def connection(self):
        raise RuntimeError("connection refused")


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
# Row builders — one row as the joined SELECT yields it.
#
# A REAL instant row carries is_known=true and the coordinate + component
# columns. The EMPTY-window sentinel (known LEFT JOIN empty hist) carries only
# is_known with all history columns NULL.
# ---------------------------------------------------------------------------

MARKET_DATE = datetime.date(2026, 7, 5)


def _row(market_date, market_hour, market_interval,
         lmp, energy, congestion, loss, ghg, *, is_known=True):
    return {
        "is_known": is_known,
        "market_date": market_date,
        "market_hour": market_hour,
        "market_interval": market_interval,
        "lmp": lmp,
        "energy": energy,
        "congestion": congestion,
        "loss": loss,
        "ghg": ghg,
    }


def _empty_sentinel(is_known):
    """The single row the joined SELECT returns when hist is empty."""
    return {
        "is_known": is_known,
        "market_date": None,
        "market_hour": None,
        "market_interval": None,
        "lmp": None, "energy": None, "congestion": None, "loss": None, "ghg": None,
    }


GOOD = "KRNCNYN_6_N001"
URL = "/api/atlas/pnode-history"


# ---------------------------------------------------------------------------
# Envelope + columnar contract
# ---------------------------------------------------------------------------

def test_envelope_shape_and_parallel_arrays(client):
    use_rows([
        _row(MARKET_DATE, 9, 7, 41.2, 40.0, 1.1, 0.1, 0.0),
        _row(MARKET_DATE, 9, 8, 39.8, 39.0, 0.7, 0.1, None),
    ])
    resp = client.get(f"{URL}?pnode_id={GOOD}&market=RTD&hours=24")
    assert resp.status_code == 200
    body = resp.json()

    assert set(body) == {
        "pnode_id", "market", "hours", "known", "row_count",
        "market_date", "market_hour", "market_interval",
        "lmp", "energy", "congestion", "loss", "ghg",
    }
    # Prices only — no geometry, ever.
    assert "geometry" not in body
    assert not any(k in body for k in ("lat", "lon", "longitude", "latitude", "coordinates"))

    assert body["pnode_id"] == GOOD
    assert body["market"] == "RTD"
    assert body["hours"] == 24
    assert body["known"] is True
    assert body["row_count"] == 2

    # Market-coordinate arrays mirror the hot tier verbatim.
    assert body["market_date"] == ["2026-07-05", "2026-07-05"]
    assert body["market_hour"] == [9, 9]
    assert body["market_interval"] == [7, 8]

    # Component arrays order-aligned; NULL passes through as JSON null.
    assert body["lmp"] == [41.2, 39.8]
    assert body["ghg"] == [0.0, None]
    for k in ("market_date", "market_hour", "market_interval",
              "lmp", "energy", "congestion", "loss", "ghg"):
        assert len(body[k]) == body["row_count"]


def test_no_server_derived_timestamp_fields(client):
    # The API must NOT emit any ISO instant / feed timestamp — the client owns
    # the time axis. Only the three raw market-coordinate arrays are exposed.
    use_rows([_row(MARKET_DATE, 9, 7, 41.2, 40.0, 1.1, 0.1, 0.0)])
    body = client.get(f"{URL}?pnode_id={GOOD}&market=RTD").json()
    for banned in ("instants", "instant", "ts", "timestamp", "feed_generated_at",
                   "snapshot_vintage", "ingested_at"):
        assert banned not in body


def test_dam_null_interval_passes_through(client):
    # DAM is hourly: market_interval is NULL and must survive as JSON null.
    use_rows([_row(MARKET_DATE, 9, None, 30.0, 30.0, 0.0, 0.0, 0.0)])
    body = client.get(f"{URL}?pnode_id={GOOD}&market=DAM").json()
    assert body["market_interval"] == [None]
    assert body["market"] == "DAM"


def test_dam_all_zero_sentinel_passes_through_unfiltered(client):
    # A no-price DAM sentinel (all-zero components) is served as-is; honesty of
    # rendering the no-price state is the client's job, not the API's.
    use_rows([_row(MARKET_DATE, 3, None, 0.0, 0.0, 0.0, 0.0, 0.0)])
    body = client.get(f"{URL}?pnode_id={GOOD}&market=DAM").json()
    assert body["row_count"] == 1
    assert body["lmp"] == [0.0]
    assert body["congestion"] == [0.0]


def test_ascending_order_is_preserved_from_sql(client):
    # The route relies on SQL ORDER BY (ascending by instant) and must not
    # re-sort — it emits rows in the order the fake yields them.
    rows = [
        _row(MARKET_DATE, 8, 1, 10.0, 10.0, 0.0, 0.0, None),
        _row(MARKET_DATE, 9, 12, 20.0, 20.0, 0.0, 0.0, None),
        _row(datetime.date(2026, 7, 6), 0, 1, 30.0, 30.0, 0.0, 0.0, None),
    ]
    use_rows(rows)
    body = client.get(f"{URL}?pnode_id={GOOD}&market=RTD&hours=168").json()
    assert body["lmp"] == [10.0, 20.0, 30.0]
    assert body["market_date"] == ["2026-07-05", "2026-07-05", "2026-07-06"]


# ---------------------------------------------------------------------------
# known / empty-window / unknown split
# ---------------------------------------------------------------------------

def test_known_pnode_empty_window_is_200_empty_known_true(client):
    # Valid node, no rows in the window/market -> 200 with empty arrays and
    # known:true. Never a 404, never a fabricated flat line.
    use_rows([_empty_sentinel(is_known=True)])
    resp = client.get(f"{URL}?pnode_id={GOOD}&market=RTD")
    assert resp.status_code == 200
    body = resp.json()
    assert body["known"] is True
    assert body["row_count"] == 0
    for k in ("market_date", "market_hour", "market_interval",
              "lmp", "energy", "congestion", "loss", "ghg"):
        assert body[k] == []


def test_unknown_pnode_is_400(client):
    # Absent from the hot-tier universe -> 400 (not a 200 with known:false).
    use_rows([_empty_sentinel(is_known=False)])
    resp = client.get(f"{URL}?pnode_id=NOPE_1_N999&market=RTD")
    assert resp.status_code == 400
    assert "NOPE_1_N999" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# 400 error contract — params
# ---------------------------------------------------------------------------

def test_missing_pnode_id_is_400_before_db(client):
    pool = use_rows([_row(MARKET_DATE, 9, 7, 1.0, 1.0, 0.0, 0.0, None)])
    resp = client.get(f"{URL}?market=RTD")
    assert resp.status_code == 400
    assert pool.sink == {}  # never touched the pool


def test_blank_pnode_id_is_400(client):
    resp = client.get(f"{URL}?pnode_id=%20%20&market=RTD")
    assert resp.status_code == 400


def test_unknown_market_is_400_with_allowed_values(client):
    pool = use_rows([_row(MARKET_DATE, 9, 7, 1.0, 1.0, 0.0, 0.0, None)])
    resp = client.get(f"{URL}?pnode_id={GOOD}&market=TH_NP15")
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "TH_NP15" in detail
    for m in ("RTD", "RTPD", "DAM"):
        assert m in detail
    assert pool.sink == {}  # short-circuits before DB


def test_fmm_is_rejected_400_pointing_to_rtpd(client):
    # FMM is a dashboard display name only; the wire token is RTPD.
    pool = use_rows([_row(MARKET_DATE, 9, 7, 1.0, 1.0, 0.0, 0.0, None)])
    resp = client.get(f"{URL}?pnode_id={GOOD}&market=FMM")
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "FMM" in detail
    assert "RTPD" in detail
    assert pool.sink == {}


def test_market_is_case_insensitive(client):
    use_rows([_row(MARKET_DATE, 9, 7, 1.0, 1.0, 0.0, 0.0, None)])
    body = client.get(f"{URL}?pnode_id={GOOD}&market=rtpd").json()
    assert body["market"] == "RTPD"


def test_default_market_is_rtd(client):
    pool = use_rows([_row(MARKET_DATE, 9, 7, 1.0, 1.0, 0.0, 0.0, None)])
    body = client.get(f"{URL}?pnode_id={GOOD}").json()
    assert body["market"] == "RTD"
    assert pool.sink["params"]["market"] == "RTD"


@pytest.mark.parametrize("bad", ["48", "0", "169", "7", "-24", "abc"])
def test_bad_hours_is_400(client, bad):
    pool = use_rows([_row(MARKET_DATE, 9, 7, 1.0, 1.0, 0.0, 0.0, None)])
    resp = client.get(f"{URL}?pnode_id={GOOD}&market=RTD&hours={bad}")
    assert resp.status_code in (400, 422)  # non-int -> 422 at parse, else 400
    if resp.status_code == 400:
        assert pool.sink == {}  # bad value short-circuits before DB


def test_default_hours_is_24(client):
    pool = use_rows([_row(MARKET_DATE, 9, 7, 1.0, 1.0, 0.0, 0.0, None)])
    body = client.get(f"{URL}?pnode_id={GOOD}&market=RTD").json()
    assert body["hours"] == 24
    assert pool.sink["params"]["days"] == 1


def test_hours_168_maps_to_seven_days(client):
    pool = use_rows([_row(MARKET_DATE, 9, 7, 1.0, 1.0, 0.0, 0.0, None)])
    body = client.get(f"{URL}?pnode_id={GOOD}&market=RTD&hours=168").json()
    assert body["hours"] == 168
    assert pool.sink["params"]["days"] == 7


# ---------------------------------------------------------------------------
# 503 on DB unavailability (the house standard body, per /health)
# ---------------------------------------------------------------------------

def test_db_unavailable_is_503_standard_body(client):
    main._pool = _BoomPool()
    resp = client.get(f"{URL}?pnode_id={GOOD}&market=RTD")
    assert resp.status_code == 503
    assert "db unavailable" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Cache-Control header
# ---------------------------------------------------------------------------

def test_cache_control_max_age_60(client):
    use_rows([_row(MARKET_DATE, 9, 7, 41.2, 40.0, 1.1, 0.1, 0.0)])
    resp = client.get(f"{URL}?pnode_id={GOOD}&market=RTD")
    assert resp.headers.get("cache-control") == "max-age=60"


# ---------------------------------------------------------------------------
# Query structure / source-level guards
# ---------------------------------------------------------------------------

def test_query_is_pure_range_scan_no_completeness_floor(client):
    pool = use_rows([_row(MARKET_DATE, 9, 7, 41.2, 40.0, 1.1, 0.1, 0.0)])
    client.get(f"{URL}?pnode_id={GOOD}&market=RTD&hours=168")
    q = pool.sink["query"]
    assert "atlas_pnode_lmp_snapshot" in q
    assert "pnode_id = %(pnode_id)s" in q
    assert "market = %(market)s" in q
    assert "market_date >= (CURRENT_DATE - %(days)s)" in q
    # History does NOT gate on completeness (that's the live endpoint's job).
    assert "floor" not in q
    assert "COUNT(DISTINCT pnode_id)" not in q
    # Membership probe + ascending order live in SQL.
    assert "EXISTS" in q
    assert "ORDER BY" in q and "ASC" in q
    params = pool.sink["params"]
    assert params["pnode_id"] == GOOD
    assert params["market"] == "RTD"
    assert params["days"] == 7


def test_no_geometry_columns_selected(client):
    pool = use_rows([_row(MARKET_DATE, 9, 7, 41.2, 40.0, 1.1, 0.1, 0.0)])
    client.get(f"{URL}?pnode_id={GOOD}&market=RTD")
    q = pool.sink["query"].lower()
    for banned in ("geom", "latitude", "longitude", " lat", " lon", "coordinates"):
        assert banned not in q


def test_alignment_guard_and_constants_present():
    src = inspect.getsource(main.atlas_pnode_history)
    assert "misaligned" in src
    assert "status_code=500" in src
    # The market allowlist mirrors the sibling; hours allowlist is the SoT.
    assert main.PNODE_HISTORY_DEFAULT_MARKET == "RTD"
    assert set(main.PNODE_HISTORY_MARKETS) == {"RTD", "RTPD", "DAM"}
    assert main.PNODE_HISTORY_ALLOWED_HOURS == (24, 168)
    assert main.PNODE_HISTORY_DEFAULT_HOURS == 24
