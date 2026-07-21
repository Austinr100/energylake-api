"""
Tests for the Cockpit node-search endpoint (PR-1):

  * GET /api/nodes/search?q=&area=&node_type=

No live database is required. Like the sibling suites, these swap a tiny
in-memory fake pool into `main._pool`. The fake captures the query + params so
the structural rails can be asserted at the source-of-truth level:
  * the latest-instant CTE (index-driven, never a scan of all history),
  * IS NOT DISTINCT FROM on the interval (NULL matches itself),
  * the P2 sentinel floor (market_date >= 2020-01-01),
  * a properly escaped ILIKE substring pattern.

DB rows are faked as psycopg's `dict_row` would yield them from the SELECT
(pnode_id, node_type, area).
"""

import psycopg
import pytest
from fastapi.testclient import TestClient

import main


# ---------------------------------------------------------------------------
# In-memory fake pool. Canned result set + query/param capture, and an optional
# retry simulation: the first `fail_times` execute() calls raise `exc` (default
# a dropped-connection error). The attempt counter lives on the pool so it is
# shared across the fresh connection each retry acquires.
# ---------------------------------------------------------------------------

class _FakeCursor:
    def __init__(self, rows, sink, state):
        self._rows = rows
        self._sink = sink
        self._state = state

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, query, params=None):
        self._sink["query"] = query
        self._sink["params"] = params
        self._state["attempts"] += 1
        if self._state["attempts"] <= self._state["fail_times"]:
            raise self._state["exc"]

    async def fetchall(self):
        return list(self._rows)


class _FakeConn:
    def __init__(self, rows, sink, state):
        self._rows = rows
        self._sink = sink
        self._state = state

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def cursor(self):
        return _FakeCursor(self._rows, self._sink, self._state)


class FakePool:
    def __init__(self, rows, fail_times=0, exc=None):
        self._rows = rows
        self.sink = {}
        self.state = {
            "attempts": 0,
            "fail_times": fail_times,
            "exc": exc or psycopg.OperationalError("SSL connection has been closed unexpectedly"),
        }

    def connection(self):
        return _FakeConn(self._rows, self.sink, self.state)


@pytest.fixture
def client():
    return TestClient(main.app)


def use_rows(rows, fail_times=0, exc=None):
    pool = FakePool(rows, fail_times=fail_times, exc=exc)
    main._pool = pool
    return pool


def _node(pnode_id, node_type, area, plant_name=None, matched_via_plant=False):
    return {
        "pnode_id": pnode_id, "node_type": node_type, "area": area,
        "plant_name": plant_name, "matched_via_plant": matched_via_plant,
    }


# ---------------------------------------------------------------------------
# Happy path + shape
# ---------------------------------------------------------------------------

def test_returns_matches_shape(client):
    use_rows([
        _node("BLUELAKE_7_GN001", "GEN", "CA"),
        _node("LAKEPARK_LNODED1", "LOAD", "PACE"),
    ])
    resp = client.get("/api/nodes/search?q=LAKE")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"query", "area", "node_type", "count", "matches"}
    assert body["query"] == "LAKE"
    assert body["area"] is None and body["node_type"] is None
    assert body["count"] == 2
    assert body["matches"][0] == {
        "pnode_id": "BLUELAKE_7_GN001", "node_type": "GEN", "area": "CA",
        "plant_name": None, "matched_via_plant": False,
    }


def test_no_matches_is_empty_200(client):
    use_rows([])
    resp = client.get("/api/nodes/search?q=ZZZZZ")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 0 and body["matches"] == []


def test_caps_at_50(client):
    pool = use_rows([])
    client.get("/api/nodes/search?q=x")
    assert pool.sink["params"]["limit"] == main.NODE_SEARCH_MAX == 50


# ---------------------------------------------------------------------------
# Structural rails — the query is index-driven and P2-filtered.
# ---------------------------------------------------------------------------

def test_query_uses_latest_instant_and_p2_and_escape(client):
    pool = use_rows([])
    client.get("/api/nodes/search?q=abc")
    q = pool.sink["query"]
    # Latest-instant CTE (not a scan of all history), interval NULL-safe match.
    assert "ORDER BY market_date DESC, market_hour DESC, market_interval DESC NULLS LAST" in q
    assert "LIMIT 1" in q
    assert "IS NOT DISTINCT FROM" in q
    # P2 sentinel floor is bound and referenced.
    assert "market_date >= %(sentinel)s" in q
    assert pool.sink["params"]["sentinel"] == "2020-01-01"
    # Escaped ILIKE.
    assert "ILIKE %(q)s ESCAPE" in q
    # Universe read from the DAM instant.
    assert pool.sink["params"]["market"] == main.NODE_SEARCH_MARKET == "DAM"


def test_like_metacharacters_are_escaped(client):
    pool = use_rows([])
    client.get("/api/nodes/search", params={"q": "a_b%c\\d"})
    # Each LIKE metachar is backslash-escaped inside the %...% pattern.
    assert pool.sink["params"]["q"] == "%a\\_b\\%c\\\\d%"


def test_empty_q_browses_all(client):
    pool = use_rows([_node("AAA_1_N001", "GEN", "CA")])
    resp = client.get("/api/nodes/search")
    assert resp.status_code == 200
    assert pool.sink["params"]["q"] == "%%"  # matches everything


def test_area_and_node_type_filters_bind(client):
    pool = use_rows([])
    client.get("/api/nodes/search?q=x&area=ca&node_type=gen")
    p = pool.sink["params"]
    assert p["area"] == "ca" and p["node_type"] == "gen"
    # Blank filters collapse to NULL (no filter).
    pool2 = use_rows([])
    client.get("/api/nodes/search?q=x&area=&node_type=")
    assert pool2.sink["params"]["area"] is None
    assert pool2.sink["params"]["node_type"] is None


# ---------------------------------------------------------------------------
# Error contract
# ---------------------------------------------------------------------------

def test_db_error_is_503(client):
    # Two consecutive dropped connections (retry exhausted) -> honest 503.
    use_rows([], fail_times=2)
    resp = client.get("/api/nodes/search?q=x")
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Plant-name search via the Atlas crosswalk (captain request).
# ---------------------------------------------------------------------------

def test_query_joins_plant_crosswalk(client):
    pool = use_rows([])
    client.get("/api/nodes/search?q=Alamitos")
    q = pool.sink["query"]
    # The crosswalk (plant_code -> pnode_id) and atlas_plants (plant_name) are
    # joined, intersected with the priced universe, and plant_name matched.
    assert "atlas_pnode_crosswalk" in q
    assert "atlas_plants" in q
    assert "plant_name ILIKE %(q)s" in q
    # Still bounded by the latest-instant universe + the P2 floor.
    assert "market_date >= %(sentinel)s" in q
    assert "LIMIT %(limit)s" in q


def test_plant_name_and_flag_surfaced(client):
    use_rows([
        _node("ALAMT3G_7_B1", "GEN", "CA", plant_name="AES Alamitos LLC", matched_via_plant=True),
        _node("ALAMITOS_2_N001", "LOAD", "CA", plant_name=None, matched_via_plant=False),
    ])
    resp = client.get("/api/nodes/search?q=Alamitos")
    assert resp.status_code == 200
    matches = resp.json()["matches"]
    assert matches[0] == {
        "pnode_id": "ALAMT3G_7_B1", "node_type": "GEN", "area": "CA",
        "plant_name": "AES Alamitos LLC", "matched_via_plant": True,
    }
    # A pnode_id-only match carries a null plant_name and matched_via_plant False.
    assert matches[1]["plant_name"] is None
    assert matches[1]["matched_via_plant"] is False


# ---------------------------------------------------------------------------
# Connection lifecycle — recover a stale connection with a single retry.
# ---------------------------------------------------------------------------

def test_retry_once_recovers_dropped_connection(client):
    # First execute raises a dropped-connection error; the retry succeeds.
    pool = use_rows([_node("BLUELAKE_7_GN001", "GEN", "CA")], fail_times=1)
    resp = client.get("/api/nodes/search?q=LAKE")
    assert resp.status_code == 200
    assert resp.json()["count"] == 1
    assert pool.state["attempts"] == 2  # one failure + one successful retry


def test_non_connection_error_is_not_retried(client):
    # A non-connection error is not a stale-socket case: surface it (503) without
    # a spurious retry.
    pool = use_rows([], fail_times=1, exc=ValueError("bad sql"))
    resp = client.get("/api/nodes/search?q=x")
    assert resp.status_code == 503
    assert pool.state["attempts"] == 1  # no retry for a non-connection error
