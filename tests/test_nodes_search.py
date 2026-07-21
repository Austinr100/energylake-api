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

import pytest
from fastapi.testclient import TestClient

import main


# ---------------------------------------------------------------------------
# In-memory fake pool (single canned result set + query/param capture).
# ---------------------------------------------------------------------------

class _FakeCursor:
    def __init__(self, rows, sink, raise_on_execute):
        self._rows = rows
        self._sink = sink
        self._raise = raise_on_execute

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, query, params=None):
        self._sink["query"] = query
        self._sink["params"] = params
        if self._raise:
            raise RuntimeError("boom")

    async def fetchall(self):
        return list(self._rows)


class _FakeConn:
    def __init__(self, rows, sink, raise_on_execute):
        self._rows = rows
        self._sink = sink
        self._raise = raise_on_execute

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def cursor(self):
        return _FakeCursor(self._rows, self._sink, self._raise)


class FakePool:
    def __init__(self, rows, raise_on_execute=False):
        self._rows = rows
        self._raise = raise_on_execute
        self.sink = {}

    def connection(self):
        return _FakeConn(self._rows, self.sink, self._raise)


@pytest.fixture
def client():
    return TestClient(main.app)


def use_rows(rows, raise_on_execute=False):
    pool = FakePool(rows, raise_on_execute)
    main._pool = pool
    return pool


def _node(pnode_id, node_type, area):
    return {"pnode_id": pnode_id, "node_type": node_type, "area": area}


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
        "pnode_id": "BLUELAKE_7_GN001", "node_type": "GEN", "area": "CA"
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
    use_rows([], raise_on_execute=True)
    resp = client.get("/api/nodes/search?q=x")
    assert resp.status_code == 503
