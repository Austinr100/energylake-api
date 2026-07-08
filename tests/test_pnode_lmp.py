"""
Tests for the CAISO pnode-LMP latest-snapshot endpoint (PR-2):

  * GET /api/atlas/pnode-lmp?market={RTD|RTPD|DAM}

No live database is required. Like the sibling suites, these swap a tiny
in-memory fake pool into `main._pool`. The instant SELECTOR itself lives in SQL
(latest instant clearing the 90% completeness floor), so the fake pool cannot
exercise selection — instead each test feeds the rows the SELECT *would* return
for the chosen instant and asserts the route's columnar shaping, scalar
extraction, null passthrough, array alignment, and the 400/503 error contract.
The fake captures the last query + params so the structural guards (single read
of atlas_pnode_lmp_snapshot, the completeness floor, ORDER BY pnode_id, the
bound market) can be asserted at the source-of-truth level.

DB rows are faked as psycopg's `dict_row` would yield them from the final
SELECT (pnode_id, lmp, energy, congestion, loss, ghg, market_date, market_hour,
market_interval, snapshot_vintage, feed_generated_at).
"""

import datetime
import inspect

import pytest
from fastapi.testclient import TestClient

import main


# ---------------------------------------------------------------------------
# In-memory fake pool (same surface as tests/test_hub_lmp.py).
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
# Row builder — one row as the final SELECT yields it for the chosen instant.
# ---------------------------------------------------------------------------

UTC = datetime.timezone.utc

# A fixed instant used across tests (2026-07-05 HE9 interval 8, the recon RTD tip).
MARKET_DATE = datetime.date(2026, 7, 5)
MARKET_HOUR = 9
MARKET_INTERVAL = 8
VINTAGE = datetime.datetime(2026, 7, 5, 13, 45, 50, tzinfo=UTC)
FEED = datetime.datetime(2026, 7, 5, 13, 40, 0, tzinfo=UTC)


def _row(pnode_id, lmp, energy, congestion, loss, ghg,
         *, market_date=MARKET_DATE, market_hour=MARKET_HOUR,
         market_interval=MARKET_INTERVAL, vintage=VINTAGE, feed=FEED):
    return {
        "pnode_id": pnode_id,
        "lmp": lmp,
        "energy": energy,
        "congestion": congestion,
        "loss": loss,
        "ghg": ghg,
        "market_date": market_date,
        "market_hour": market_hour,
        "market_interval": market_interval,
        "snapshot_vintage": vintage,
        "feed_generated_at": feed,
    }


# ---------------------------------------------------------------------------
# Envelope + columnar contract
# ---------------------------------------------------------------------------

def test_envelope_shape_and_columnar_arrays(client):
    use_rows([
        _row("AAA_1_N001", 41.2, 40.0, 1.1, 0.1, 0.0),
        _row("BBB_2_N002", 39.8, 39.0, 0.7, 0.1, None),
    ])
    resp = client.get("/api/atlas/pnode-lmp?market=RTD")
    assert resp.status_code == 200
    body = resp.json()

    assert set(body) == {
        "market", "snapshot_vintage", "market_date", "market_hour",
        "market_interval", "feed_generated_at", "pnode_count",
        "pnode_id", "lmp", "energy", "congestion", "loss", "ghg",
    }
    # Prices only — no geometry, ever.
    assert "geometry" not in body
    assert not any(k in body for k in ("lat", "lon", "longitude", "latitude", "coordinates"))

    assert body["market"] == "RTD"
    assert body["market_date"] == "2026-07-05"
    assert body["market_hour"] == 9
    assert body["market_interval"] == 8
    assert body["pnode_count"] == 2

    # All six arrays order-aligned and equal length.
    assert body["pnode_id"] == ["AAA_1_N001", "BBB_2_N002"]
    assert body["lmp"] == [41.2, 39.8]
    assert body["energy"] == [40.0, 39.0]
    assert body["congestion"] == [1.1, 0.7]
    assert body["loss"] == [0.1, 0.1]
    assert body["ghg"] == [0.0, None]   # NULL passes through as JSON null
    for k in ("pnode_id", "lmp", "energy", "congestion", "loss", "ghg"):
        assert len(body[k]) == body["pnode_count"]


def test_null_component_is_json_null_not_zero(client):
    # Absence is not zero: a NULL congestion is emitted as null, held in place so
    # the column stays aligned with pnode_id.
    use_rows([_row("AAA_1_N001", 41.2, 40.0, None, 0.1, None)])
    body = client.get("/api/atlas/pnode-lmp?market=RTD").json()
    assert body["congestion"] == [None]
    assert body["ghg"] == [None]
    assert body["lmp"] == [41.2]


def test_prices_are_numeric(client):
    use_rows([_row("AAA_1_N001", 41.2, 40.0, 1.1, 0.1, 0.5)])
    body = client.get("/api/atlas/pnode-lmp?market=RTD").json()
    for k in ("lmp", "energy", "congestion", "loss", "ghg"):
        assert all(isinstance(v, (int, float)) for v in body[k])


# ---------------------------------------------------------------------------
# Scalars: constant instant, vintage/feed = MAX among served rows, UTC ISO
# ---------------------------------------------------------------------------

def test_scalars_taken_from_the_served_instant(client):
    use_rows([_row("AAA_1_N001", 41.2, 40.0, 1.1, 0.1, 0.0)])
    body = client.get("/api/atlas/pnode-lmp?market=RTD").json()
    assert body["snapshot_vintage"] == VINTAGE.isoformat()
    assert body["feed_generated_at"] == FEED.isoformat()
    assert body["snapshot_vintage"].endswith("+00:00")
    assert body["feed_generated_at"].endswith("+00:00")


def test_vintage_and_feed_are_max_among_rows(client):
    # A mixed-vintage instant: report the newest pull that touched it. Same for
    # feed_generated_at.
    older_v = datetime.datetime(2026, 7, 5, 12, 0, 0, tzinfo=UTC)
    newer_v = datetime.datetime(2026, 7, 5, 14, 0, 0, tzinfo=UTC)
    older_f = datetime.datetime(2026, 7, 5, 11, 0, 0, tzinfo=UTC)
    newer_f = datetime.datetime(2026, 7, 5, 13, 0, 0, tzinfo=UTC)
    use_rows([
        _row("AAA_1_N001", 41.2, 40.0, 1.1, 0.1, 0.0, vintage=older_v, feed=older_f),
        _row("BBB_2_N002", 39.8, 39.0, 0.7, 0.1, 0.0, vintage=newer_v, feed=newer_f),
    ])
    body = client.get("/api/atlas/pnode-lmp?market=RTD").json()
    assert body["snapshot_vintage"] == newer_v.isoformat()
    assert body["feed_generated_at"] == newer_f.isoformat()


def test_null_interval_scalar_passes_through(client):
    use_rows([_row("AAA_1_N001", 41.2, 40.0, 1.1, 0.1, 0.0, market_interval=None)])
    body = client.get("/api/atlas/pnode-lmp?market=DAM").json()
    assert body["market_interval"] is None


# ---------------------------------------------------------------------------
# Market param: default, case-insensitivity, validation -> 400
# ---------------------------------------------------------------------------

def test_default_market_is_rtd(client):
    pool = use_rows([_row("AAA_1_N001", 41.2, 40.0, 1.1, 0.1, 0.0)])
    body = client.get("/api/atlas/pnode-lmp").json()
    assert body["market"] == "RTD"
    assert pool.sink["params"]["market"] == "RTD"


def test_explicit_markets_bind_through(client):
    for m in ("RTD", "RTPD", "DAM"):
        pool = use_rows([_row("AAA_1_N001", 41.2, 40.0, 1.1, 0.1, 0.0)])
        body = client.get(f"/api/atlas/pnode-lmp?market={m}").json()
        assert body["market"] == m
        assert pool.sink["params"]["market"] == m


def test_market_is_case_insensitive(client):
    pool = use_rows([_row("AAA_1_N001", 41.2, 40.0, 1.1, 0.1, 0.0)])
    body = client.get("/api/atlas/pnode-lmp?market=rtpd").json()
    assert body["market"] == "RTPD"
    assert pool.sink["params"]["market"] == "RTPD"


def test_unknown_market_is_400_with_allowed_values(client):
    use_rows([_row("AAA_1_N001", 41.2, 40.0, 1.1, 0.1, 0.0)])
    resp = client.get("/api/atlas/pnode-lmp?market=TH_NP15")
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "TH_NP15" in detail
    for m in ("RTD", "RTPD", "DAM"):
        assert m in detail


def test_unknown_market_short_circuits_before_db(client):
    # A bad market must not touch the pool at all.
    pool = use_rows([_row("AAA_1_N001", 41.2, 40.0, 1.1, 0.1, 0.0)])
    client.get("/api/atlas/pnode-lmp?market=NOPE")
    assert pool.sink == {}  # execute() never called


# ---------------------------------------------------------------------------
# No complete instant -> 503 (deliberate fork from the sibling's 404)
# ---------------------------------------------------------------------------

def test_no_rows_is_503_not_404_or_empty_200(client):
    use_rows([])
    resp = client.get("/api/atlas/pnode-lmp?market=RTD")
    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert "RTD" in detail
    assert "expired" in detail or "empty" in detail


# ---------------------------------------------------------------------------
# Cache-Control header
# ---------------------------------------------------------------------------

def test_cache_control_max_age_60(client):
    use_rows([_row("AAA_1_N001", 41.2, 40.0, 1.1, 0.1, 0.0)])
    resp = client.get("/api/atlas/pnode-lmp?market=RTD")
    assert resp.headers.get("cache-control") == "max-age=60"


# ---------------------------------------------------------------------------
# Query structure / source-level guards
# ---------------------------------------------------------------------------

def test_query_is_single_read_with_completeness_floor(client):
    pool = use_rows([_row("AAA_1_N001", 41.2, 40.0, 1.1, 0.1, 0.0)])
    client.get("/api/atlas/pnode-lmp?market=RTD")
    q = pool.sink["query"]
    assert "atlas_pnode_lmp_snapshot" in q
    assert "market = %(market)s" in q
    # Completeness floor + latest-instant ordering live in SQL.
    assert "%(floor)s" in q
    assert "COUNT(DISTINCT pnode_id)" in q
    assert "ORDER BY r.pnode_id ASC" in q
    assert "IS NOT DISTINCT FROM" in q
    params = pool.sink["params"]
    assert params["market"] == "RTD"
    assert params["floor"] == main.PNODE_LMP_COMPLETENESS_FLOOR


def test_no_geometry_columns_selected(client):
    pool = use_rows([_row("AAA_1_N001", 41.2, 40.0, 1.1, 0.1, 0.0)])
    client.get("/api/atlas/pnode-lmp?market=RTD")
    q = pool.sink["query"].lower()
    # Prices only — the query must never reach for coordinates/geometry.
    for banned in ("geom", "latitude", "longitude", " lat", " lon", "coordinates"):
        assert banned not in q


def test_alignment_guard_and_constants_present():
    # The array-alignment 500 guard is a source-level invariant (can't be tripped
    # through the public path since arrays are built from one row walk).
    src = inspect.getsource(main.atlas_pnode_lmp)
    assert "misaligned" in src
    assert "status_code=500" in src
    # The market allowlist + default are the single source of truth.
    assert main.PNODE_LMP_DEFAULT_MARKET == "RTD"
    assert set(main.PNODE_LMP_MARKETS) == {"RTD", "RTPD", "DAM"}
    assert main.PNODE_LMP_COMPLETENESS_FLOOR == 0.90
