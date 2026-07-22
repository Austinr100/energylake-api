"""
Tests for the Cockpit facets endpoint (Cockpit v0.3, PR-1):

  * GET /api/nodes/facets

No live database is required. Like the sibling suites, these swap a tiny
in-memory fake pool into `main._pool`. The fake captures the query + params so
the structural rails can be asserted at the source-of-truth level:
  * the SAME latest-instant CTE as /api/nodes/search (index-driven, never a scan
    of all history), IS NOT DISTINCT FROM on the interval, the P2 sentinel floor,
  * two GROUP BY aggregates stacked with a `kind` discriminator.

DB rows are faked as psycopg's `dict_row` would yield them from the UNION SELECT
(kind, label, n, md, mh, mi). The in-process facets cache is a module global, so
every test resets it first.
"""

from datetime import date

import psycopg
import pytest
from fastapi.testclient import TestClient

import main


# ---------------------------------------------------------------------------
# In-memory fake pool — identical shape to the node-search suite.
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


@pytest.fixture(autouse=True)
def _reset_cache():
    # The facets endpoint memoizes in a module global; every test starts cold.
    main._node_facets_cache = None
    yield
    main._node_facets_cache = None


def use_rows(rows, fail_times=0, exc=None):
    pool = FakePool(rows, fail_times=fail_times, exc=exc)
    main._pool = pool
    return pool


# A representative latest DAM instant (matches the P5 receipts in main.py:
# DAM 2026-07-21 HE12 int0 -> PT 11:00 -> 2026-07-21T18:00Z).
_MD, _MH, _MI = date(2026, 7, 21), 12, 0


def _row(kind, label, n, md=_MD, mh=_MH, mi=_MI):
    return {"kind": kind, "label": label, "n": n, "md": md, "mh": mh, "mi": mi}


# ---------------------------------------------------------------------------
# Happy path + shape
# ---------------------------------------------------------------------------

def test_returns_facets_shape(client):
    use_rows([
        _row("area", "CA", 12043),
        _row("area", "PACE", 512),
        _row("node_type", "LOAD", 8214),
        _row("node_type", "GEN", 4341),
    ])
    resp = client.get("/api/nodes/facets")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"areas", "node_types", "as_of"}
    assert body["areas"] == [
        {"area": "CA", "count": 12043},
        {"area": "PACE", "count": 512},
    ]
    assert body["node_types"] == [
        {"node_type": "LOAD", "count": 8214},
        {"node_type": "GEN", "count": 4341},
    ]


def test_as_of_is_reconstructed_instant_utc(client):
    # DAM 2026-07-21 HE12 int0 -> PT 11:00 -> 18:00Z (the main.py P5 receipt).
    use_rows([_row("area", "CA", 5)])
    body = client.get("/api/nodes/facets").json()
    assert body["as_of"] == "2026-07-21T18:00:00+00:00"


def test_sorted_by_count_desc_then_label(client):
    use_rows([
        _row("area", "BPAT", 100),
        _row("area", "CA", 100),   # tie -> alphabetical: CA after BPAT
        _row("area", "PACE", 900),
    ])
    areas = client.get("/api/nodes/facets").json()["areas"]
    assert [a["area"] for a in areas] == ["PACE", "BPAT", "CA"]


def test_null_categories_are_dropped(client):
    # A NULL area/node_type is not a value search's exact filter can match, so it
    # must never appear as a selectable dropdown option.
    use_rows([
        _row("area", "CA", 10),
        _row("area", None, 3),
        _row("node_type", "GEN", 7),
        _row("node_type", None, 2),
    ])
    body = client.get("/api/nodes/facets").json()
    assert body["areas"] == [{"area": "CA", "count": 10}]
    assert body["node_types"] == [{"node_type": "GEN", "count": 7}]


def test_empty_universe_is_empty_lists_null_as_of(client):
    use_rows([])
    resp = client.get("/api/nodes/facets")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"areas": [], "node_types": [], "as_of": None}


# ---------------------------------------------------------------------------
# Structural rails — same latest-instant, P2-floored universe as /nodes/search.
# ---------------------------------------------------------------------------

def test_query_shares_search_universe(client):
    pool = use_rows([])
    client.get("/api/nodes/facets")
    q = pool.sink["query"]
    # Latest-instant CTE (index-driven, not a scan of all history).
    assert "ORDER BY market_date DESC, market_hour DESC, market_interval DESC NULLS LAST" in q
    assert "LIMIT 1" in q
    # NULL-safe interval match + the P2 sentinel floor, bound to the DAM instant.
    assert "IS NOT DISTINCT FROM" in q
    assert "market_date >= %(sentinel)s" in q
    assert pool.sink["params"]["sentinel"] == "2020-01-01"
    assert pool.sink["params"]["market"] == main.NODE_SEARCH_MARKET == "DAM"
    # Two grouped aggregates stacked with a kind discriminator.
    assert "'area'" in q and "'node_type'" in q
    assert "UNION ALL" in q


# ---------------------------------------------------------------------------
# Caching — one DB read per TTL window, served from the module global after.
# ---------------------------------------------------------------------------

def test_second_call_is_served_from_cache(client):
    pool = use_rows([_row("area", "CA", 5)])
    first = client.get("/api/nodes/facets")
    assert first.status_code == 200
    assert first.headers.get("Cache-Control") == "max-age=300"
    attempts_after_first = pool.state["attempts"]
    # Second call within the TTL must not hit the DB again.
    second = client.get("/api/nodes/facets")
    assert second.status_code == 200
    assert second.json() == first.json()
    assert pool.state["attempts"] == attempts_after_first


# ---------------------------------------------------------------------------
# Connection lifecycle + error contract (mirrors the node-search lane).
# ---------------------------------------------------------------------------

def test_retry_once_recovers_dropped_connection(client):
    pool = use_rows([_row("area", "CA", 5)], fail_times=1)
    resp = client.get("/api/nodes/facets")
    assert resp.status_code == 200
    assert resp.json()["areas"] == [{"area": "CA", "count": 5}]
    assert pool.state["attempts"] == 2  # one failure + one successful retry


def test_db_error_is_503(client):
    use_rows([], fail_times=2)  # retry exhausted -> honest 503
    resp = client.get("/api/nodes/facets")
    assert resp.status_code == 503


def test_non_connection_error_is_not_retried(client):
    pool = use_rows([], fail_times=1, exc=ValueError("bad sql"))
    resp = client.get("/api/nodes/facets")
    assert resp.status_code == 503
    assert pool.state["attempts"] == 1  # no retry for a non-connection error
