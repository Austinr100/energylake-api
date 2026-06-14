"""
Tests for the Regulatory Board endpoint (D-2026-06-14-03):

  * GET /api/regulatory/board

No live database is required. Like the Phase 3a tests, these swap a tiny
in-memory fake pool into `main._pool` so the SQL is exercised end-to-end
through the route's shaping code without a real Neon connection. The fake
captures the last query + params for structural assertions (filter, order).
"""

import datetime
import inspect

import pytest
from fastapi.testclient import TestClient

import main


# ---------------------------------------------------------------------------
# In-memory fake pool (same surface as tests/test_phase3a.py).
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


def _board_row(**over):
    """A representative regulatory_board row (as psycopg dict_row yields it)."""
    row = {
        "id": "ferc_rm26_4",
        "body": "FERC",
        "docket": "RM26-4",
        "title": "Large-load interconnection NOPR",
        "summary": "FERC proposes new rules for large-load interconnection.",
        "theme": ["Large_Load", "Interconnection"],
        "status": "pending_decision",
        "importance": 5,
        "market_impact": "bullish",
        "impact_note": "Tightens the queue.",
        "trading_angle": "Watch CEG.",
        "is_editorial": True,
        "key_dates": [{"type": "decision_expected", "date": "2026-06-30"}],
        "next_date": datetime.date(2026, 6, 30),
        "days_until": 16,
        "source_url": ["https://ferc.gov/x"],
        "salience": 11.00,
        "provenance": "curated",
        "as_of": datetime.date(2026, 6, 14),
    }
    row.update(over)
    return row


# ---------------------------------------------------------------------------
# Default behavior + response contract
# ---------------------------------------------------------------------------

def test_board_default_envelope_and_shape(client):
    pool = use_rows([_board_row()])
    resp = client.get("/api/regulatory/board")
    assert resp.status_code == 200
    body = resp.json()

    assert body["as_of"] == "2026-06-14"
    assert body["count"] == 1
    item = body["items"][0]
    # Every contract field present, passed through verbatim (with JSON-safety).
    assert item == {
        "id": "ferc_rm26_4",
        "body": "FERC",
        "docket": "RM26-4",
        "title": "Large-load interconnection NOPR",
        "summary": "FERC proposes new rules for large-load interconnection.",
        "theme": ["Large_Load", "Interconnection"],
        "status": "pending_decision",
        "importance": 5,
        "market_impact": "bullish",
        "impact_note": "Tightens the queue.",
        "trading_angle": "Watch CEG.",
        "is_editorial": True,
        "key_dates": [{"type": "decision_expected", "date": "2026-06-30"}],
        "next_date": "2026-06-30",
        "days_until": 16,
        "source_url": ["https://ferc.gov/x"],
        "salience": 11.0,
        "provenance": "curated",
    }


def test_board_default_filters_on_board_only(client):
    pool = use_rows([_board_row()])
    client.get("/api/regulatory/board")
    # Default keeps the on-board predicate and does NOT include resolved.
    assert "on_board" in pool.sink["query"]
    assert pool.sink["params"]["include_resolved"] is False


def test_board_orders_by_salience_then_importance_then_body(client):
    pool = use_rows([_board_row()])
    client.get("/api/regulatory/board")
    assert "ORDER BY salience DESC, importance DESC, body ASC" in pool.sink["query"]


def test_board_reads_the_view(client):
    pool = use_rows([_board_row()])
    client.get("/api/regulatory/board")
    assert "FROM regulatory_board" in pool.sink["query"]


def test_board_empty_is_200_with_count_zero(client):
    use_rows([])
    resp = client.get("/api/regulatory/board")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 0
    assert body["items"] == []
    assert "as_of" in body  # contract field never missing, even when empty


# ---------------------------------------------------------------------------
# include_resolved
# ---------------------------------------------------------------------------

def test_board_include_resolved_binds_true(client):
    pool = use_rows([_board_row()])
    resp = client.get("/api/regulatory/board", params={"include_resolved": "true"})
    assert resp.status_code == 200
    assert pool.sink["params"]["include_resolved"] is True


# ---------------------------------------------------------------------------
# General ?body= filter
# ---------------------------------------------------------------------------

def test_board_body_single_binds_list(client):
    pool = use_rows([_board_row()])
    resp = client.get("/api/regulatory/board", params={"body": "CAISO"})
    assert resp.status_code == 200
    assert pool.sink["params"]["bodies"] == ["CAISO"]


def test_board_body_multiple_comma_separated(client):
    pool = use_rows([_board_row()])
    resp = client.get("/api/regulatory/board", params={"body": "FERC,CPUC"})
    assert resp.status_code == 200
    assert pool.sink["params"]["bodies"] == ["FERC", "CPUC"]


def test_board_body_is_case_insensitive(client):
    pool = use_rows([_board_row()])
    resp = client.get("/api/regulatory/board", params={"body": "caiso,ferc"})
    assert resp.status_code == 200
    assert pool.sink["params"]["bodies"] == ["CAISO", "FERC"]


def test_board_body_dedupes_preserving_order(client):
    pool = use_rows([_board_row()])
    resp = client.get("/api/regulatory/board", params={"body": "FERC,FERC,CPUC"})
    assert resp.status_code == 200
    assert pool.sink["params"]["bodies"] == ["FERC", "CPUC"]


def test_board_body_default_is_all_bodies(client):
    pool = use_rows([_board_row()])
    client.get("/api/regulatory/board")
    # No body filter => bound array is NULL => view returns all bodies.
    assert pool.sink["params"]["bodies"] is None


def test_board_body_unknown_is_400(client):
    use_rows([_board_row()])
    resp = client.get("/api/regulatory/board", params={"body": "ENRON"})
    assert resp.status_code == 400
    assert "ENRON" in resp.json()["detail"]


def test_board_body_mixed_known_and_unknown_is_400(client):
    use_rows([_board_row()])
    resp = client.get("/api/regulatory/board", params={"body": "FERC,BOGUS"})
    assert resp.status_code == 400


def test_board_body_filter_is_general_not_caiso_hardcoded():
    # The filter must accept every known body, not just CAISO — guard at the
    # source so a future change can't silently narrow the door.
    assert main._parse_body_filter("CAISO") == ["CAISO"]
    for b in main.REGULATORY_BODIES:
        assert main._parse_body_filter(b) == [b]


# ---------------------------------------------------------------------------
# Field-level passthrough edge cases
# ---------------------------------------------------------------------------

def test_board_na_and_null_fields_pass_through(client):
    use_rows([_board_row(
        market_impact="na",
        impact_note=None,
        trading_angle=None,
        next_date=None,
        salience=2.00,
    )])
    resp = client.get("/api/regulatory/board")
    item = resp.json()["items"][0]
    assert item["market_impact"] == "na"
    assert item["impact_note"] is None
    assert item["trading_angle"] is None
    assert item["next_date"] is None
    assert item["salience"] == 2.0


def test_board_count_matches_items(client):
    use_rows([_board_row(id="a"), _board_row(id="b"), _board_row(id="c")])
    resp = client.get("/api/regulatory/board")
    body = resp.json()
    assert body["count"] == 3 == len(body["items"])


# ---------------------------------------------------------------------------
# Source-level guards (caught even if the runtime query changes shape)
# ---------------------------------------------------------------------------

def test_board_source_reads_view_and_orders_correctly():
    src = inspect.getsource(main.regulatory_board)
    assert "FROM regulatory_board" in src
    assert "ORDER BY salience DESC, importance DESC, body ASC" in src
    # The view owns salience — the endpoint must not recompute it.
    assert "salience" in src
