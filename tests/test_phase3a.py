"""
Tests for Tape rebuild Phase 3a (2026-06-10):

  * GET /api/briefs/daily/latest
  * GET /api/briefs/daily/{date}
  * GET /api/wire/recent
  * the D-2026-06-10-03 predicate added to /api/tape/recent

No live database is required. The endpoints talk to a module-global async
connection pool (`main._pool`); these tests swap in a tiny in-memory fake
that returns canned rows, so the SQL is exercised end-to-end through the
route shaping code without a real Neon connection.

The Starlette TestClient is used WITHOUT its context-manager form, which
means the app's lifespan (the real pool open) never runs — `main._pool`
stays whatever the test sets it to.
"""

import datetime
import inspect

import pytest
from fastapi.testclient import TestClient

import main


# ---------------------------------------------------------------------------
# In-memory fake pool: mimics psycopg's async pool/connection/cursor surface
# (async context managers + await execute/fetchall/fetchone), returning the
# rows the test hands it. `last_query` captures the SQL for assertions.
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
# /api/briefs/daily/latest
# ---------------------------------------------------------------------------

def test_briefs_latest_404_when_empty(client):
    use_rows([])
    resp = client.get("/api/briefs/daily/latest")
    assert resp.status_code == 404
    assert "daily brief" in resp.json()["detail"]


def test_briefs_latest_shape(client):
    use_rows([{
        "brief_date": datetime.date(2026, 6, 10),
        "headline": "Grid tightens into summer",
        "content_md": "# Heading\n\nbody",
        "word_count": 412,
        "voice_version": "v3",
        "created_at": datetime.datetime(2026, 6, 10, 13, 0, tzinfo=datetime.timezone.utc),
    }])
    resp = client.get("/api/briefs/daily/latest")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "brief_date": "2026-06-10",
        "headline": "Grid tightens into summer",
        "content_md": "# Heading\n\nbody",
        "word_count": 412,
        "voice_version": "v3",
        "created_at": "2026-06-10T13:00:00+00:00",
    }


# ---------------------------------------------------------------------------
# /api/briefs/daily/{date}
# ---------------------------------------------------------------------------

def test_briefs_by_date_422_on_garbage(client):
    use_rows([])
    resp = client.get("/api/briefs/daily/not-a-date")
    assert resp.status_code == 422


def test_briefs_by_date_422_on_impossible_date(client):
    use_rows([])
    # Well-formed YYYY-MM-DD but not a real calendar day.
    resp = client.get("/api/briefs/daily/2026-13-45")
    assert resp.status_code == 422


def test_briefs_by_date_404_when_missing(client):
    use_rows([])
    resp = client.get("/api/briefs/daily/2026-06-09")
    assert resp.status_code == 404
    assert "2026-06-09" in resp.json()["detail"]


def test_briefs_by_date_ok(client):
    use_rows([{
        "brief_date": datetime.date(2026, 6, 9),
        "headline": "h",
        "content_md": "c",
        "word_count": 1,
        "voice_version": "v3",
        "created_at": datetime.datetime(2026, 6, 9, 13, 0, tzinfo=datetime.timezone.utc),
    }])
    resp = client.get("/api/briefs/daily/2026-06-09")
    assert resp.status_code == 200
    assert resp.json()["brief_date"] == "2026-06-09"


# ---------------------------------------------------------------------------
# /api/wire/recent
# ---------------------------------------------------------------------------

def _wire_row():
    return {
        "ts": datetime.datetime(2026, 6, 2, 16, 15, 28, tzinfo=datetime.timezone.utc),
        "ticker": "CEG",
        "form_type": "8-K",
        "item_codes": ["8.01", "9.01"],
        "headline": "CEG launches secondary",
        "raw_title": "Other Material Event",
        "url": "https://www.sec.gov/Archives/edgar/x",
        "source_type": "sec_filing",
        "external_id": "0001104659-26-069482",
        "is_power_signal": True,
    }


def test_wire_recent_shape_includes_power_signal_and_source_type(client):
    use_rows([_wire_row()])
    resp = client.get("/api/wire/recent")
    assert resp.status_code == 200
    body = resp.json()
    item = body["data"][0]
    # Matches /api/tape/recent fields ...
    for field in ("ts", "ticker", "form_type", "item_codes", "headline",
                  "raw_title", "url", "source_type", "external_id"):
        assert field in item
    # ... plus is_power_signal.
    assert item["is_power_signal"] is True
    assert item["source_type"] == "sec_filing"
    assert body["meta"]["limit"] == 50  # default


def test_wire_recent_422_on_unknown_stream(client):
    use_rows([])
    resp = client.get("/api/wire/recent", params={"stream": "press_release"})
    assert resp.status_code == 422


def test_wire_recent_accepts_known_stream(client):
    pool = use_rows([_wire_row()])
    resp = client.get("/api/wire/recent", params={"stream": "ir_press"})
    assert resp.status_code == 200
    assert resp.json()["meta"]["stream"] == "ir_press"
    # The filter binds the stream value into the query params.
    assert pool.sink["params"]["stream"] == "ir_press"


def test_wire_recent_limit_clamped_to_hard_cap(client):
    pool = use_rows([_wire_row()])
    resp = client.get("/api/wire/recent", params={"limit": 9999})
    assert resp.status_code == 200
    # Clamp, not reject: meta + the bound SQL LIMIT both show the cap.
    assert resp.json()["meta"]["limit"] == main.WIRE_MAX_LIMIT == 200
    assert pool.sink["params"]["limit"] == 200


def test_wire_recent_predicate_present(client):
    pool = use_rows([_wire_row()])
    client.get("/api/wire/recent")
    assert "is_power_signal IS DISTINCT FROM FALSE" in pool.sink["query"]


# ---------------------------------------------------------------------------
# /api/tape/recent — D-2026-06-10-03 predicate
# ---------------------------------------------------------------------------

def test_tape_recent_query_has_power_signal_predicate(client):
    pool = use_rows([])
    # Empty rows -> 200 with empty data (tape/recent does not 404 on empty).
    resp = client.get("/api/tape/recent")
    assert resp.status_code == 200
    assert "is_power_signal IS DISTINCT FROM FALSE" in pool.sink["query"]


def test_tape_recent_source_defines_predicate():
    # Also guard at the source level so a refactor that drops the predicate
    # from the literal SQL is caught even if the runtime query changes shape.
    src = inspect.getsource(main.tape_recent)
    assert "is_power_signal IS DISTINCT FROM FALSE" in src


def test_tape_recent_marked_deprecated():
    assert "DEPRECATED" in (main.tape_recent.__doc__ or "")
    assert "/api/wire/recent" in (main.tape_recent.__doc__ or "")
