"""
Tests for GET /api/joule/chart-brief (#99 render leg).

The endpoint serves the most recent Joule chart brief for a brief_type:

  * latest row by created_at (NOT DISTINCT ON),
  * unknown brief_type -> 400,
  * no brief yet -> 200 with body=null (NOT 404).

No live database is required. As in test_phase3a, a tiny in-memory fake pool
returns canned rows so the SQL is exercised end-to-end through the route's
shaping code without a real Neon connection. The TestClient is used WITHOUT
its context-manager form, so the app lifespan (real pool open) never runs.
"""

import datetime
import inspect

import pytest
from fastapi.testclient import TestClient

import main


# ---------------------------------------------------------------------------
# In-memory fake pool (same surface as test_phase3a).
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
    return TestClient(main.app)


def use_rows(rows):
    pool = FakePool(rows)
    main._pool = pool
    return pool


def _fuel_mix_row():
    return {
        "id": 18,
        "brief_type": "caiso_fuel_mix_chart",
        "brief_date": datetime.date(2026, 6, 22),
        "content_md": "Solar carried the midday peak while gas backfilled the evening ramp.",
        "voice_version": "v2.1",
        "word_count": 71,
        "created_at": datetime.datetime(
            2026, 6, 23, 11, 45, 37, 734038, tzinfo=datetime.timezone.utc
        ),
    }


# ---------------------------------------------------------------------------
# Happy path: latest row maps to the contract.
# ---------------------------------------------------------------------------

def test_chart_brief_maps_contract(client):
    use_rows([_fuel_mix_row()])
    resp = client.get(
        "/api/joule/chart-brief", params={"brief_type": "caiso_fuel_mix_chart"}
    )
    assert resp.status_code == 200
    assert resp.json() == {
        "brief_type": "caiso_fuel_mix_chart",
        "body": "Solar carried the midday peak while gas backfilled the evening ramp.",
        "brief_date": "2026-06-22",
        "voice_version": "v2.1",
        "word_count": 71,
        "generated_at": "2026-06-23T11:45:37.734038+00:00",
        "id": 18,
    }


def test_chart_brief_latest_by_created_at_not_distinct_on(client):
    pool = use_rows([_fuel_mix_row()])
    client.get("/api/joule/chart-brief", params={"brief_type": "caiso_fuel_mix_chart"})
    query = pool.sink["query"]
    # Newest-row, not the newswire DISTINCT ON version logic.
    assert "ORDER BY created_at DESC" in query
    assert "LIMIT 1" in query
    assert "DISTINCT ON" not in query
    assert pool.sink["params"] == {"brief_type": "caiso_fuel_mix_chart"}


# ---------------------------------------------------------------------------
# Empty case: 200 with body=null, NOT 404.
# ---------------------------------------------------------------------------

def test_chart_brief_empty_is_200_null_body(client):
    use_rows([])
    resp = client.get(
        "/api/joule/chart-brief", params={"brief_type": "caiso_fuel_mix_chart"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["body"] is None
    # brief_type is echoed back; the rest of the row fields are null.
    assert body["brief_type"] == "caiso_fuel_mix_chart"
    assert body["brief_date"] is None
    assert body["id"] is None


# ---------------------------------------------------------------------------
# Unknown brief_type: 400, not 500/404.
# ---------------------------------------------------------------------------

def test_chart_brief_unknown_type_is_400(client):
    use_rows([_fuel_mix_row()])
    resp = client.get("/api/joule/chart-brief", params={"brief_type": "garbage"})
    assert resp.status_code == 400
    assert "garbage" in resp.json()["detail"]


def test_chart_brief_missing_type_is_422(client):
    use_rows([])
    resp = client.get("/api/joule/chart-brief")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Allowlist + no-DISTINCT-ON guarded at the source level too, so a refactor
# that hardcodes the type or copies the newswire version logic is caught.
# ---------------------------------------------------------------------------

def test_chart_brief_allowlist_constant(client):
    assert "caiso_fuel_mix_chart" in main.CHART_BRIEF_TYPES


def test_chart_brief_source_uses_latest_row_not_distinct_on():
    src = inspect.getsource(main.joule_chart_brief)
    assert "DISTINCT ON" not in main._CHART_BRIEF_SELECT
    assert "CHART_BRIEF_TYPES" in src


# ---------------------------------------------------------------------------
# UTF-8 encoding (cc_spec 2026-06-23). Recon verdict (A): the bytes were always
# correct UTF-8; the live "â€"" mojibake was a browser raw-view guessing
# Latin-1 because the Content-Type lacked "; charset=utf-8". These two tests
# pin the serialization leg: multibyte chars round-trip clean AND the charset
# label is present. The live end-to-end re-check (Neon -> Railway -> browser)
# is a post-deploy receipt (egress blocks Railway here).
# ---------------------------------------------------------------------------

def _multibyte_row():
    row = _fuel_mix_row()
    # Em dash (U+2014, the reported case) plus a degree sign and an accented
    # vowel for multibyte breadth — all stored clean UTF-8 in Neon.
    row["content_md"] = "solar 36.8%, wind 14.0% — while gas held 28°, café"
    return row


def test_chart_brief_multibyte_round_trips_clean(client):
    use_rows([_multibyte_row()])
    resp = client.get(
        "/api/joule/chart-brief", params={"brief_type": "caiso_fuel_mix_chart"}
    )
    assert resp.status_code == 200
    # Parsed body holds the real characters, not "â€"" / mojibake.
    assert resp.json()["body"] == "solar 36.8%, wind 14.0% — while gas held 28°, café"


def test_chart_brief_response_declares_utf8_charset(client):
    use_rows([_multibyte_row()])
    resp = client.get(
        "/api/joule/chart-brief", params={"brief_type": "caiso_fuel_mix_chart"}
    )
    assert "charset=utf-8" in resp.headers["content-type"].lower()


# ---------------------------------------------------------------------------
# Optional brief_date filter (PR-1, cc_spec 2026-06-23). When provided, the
# endpoint serves the brief for (brief_type, brief_date) so the dashboard
# commentary can follow the chart's date selector. Omitted -> #8 behavior,
# unchanged (backward compatible).
# ---------------------------------------------------------------------------

def test_chart_brief_with_date_returns_that_days_brief(client):
    pool = use_rows([_fuel_mix_row()])
    resp = client.get(
        "/api/joule/chart-brief",
        params={"brief_type": "caiso_fuel_mix_chart", "brief_date": "2026-06-22"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == 18
    assert body["brief_date"] == "2026-06-22"
    # The date filter is spliced into the SQL with a bound param (not a literal).
    query = pool.sink["query"]
    assert "AND brief_date = %(brief_date)s" in query
    assert "ORDER BY created_at DESC" in query
    assert "LIMIT 1" in query
    assert pool.sink["params"] == {
        "brief_type": "caiso_fuel_mix_chart",
        "brief_date": datetime.date(2026, 6, 22),
    }


def test_chart_brief_with_date_no_brief_is_200_null_body_echoes_date(client):
    use_rows([])
    resp = client.get(
        "/api/joule/chart-brief",
        params={"brief_type": "caiso_fuel_mix_chart", "brief_date": "2026-06-19"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["body"] is None
    assert body["id"] is None
    # brief_type AND the requested brief_date are echoed back (not a 404).
    assert body["brief_type"] == "caiso_fuel_mix_chart"
    assert body["brief_date"] == "2026-06-19"


def test_chart_brief_malformed_date_is_422(client):
    use_rows([_fuel_mix_row()])
    resp = client.get(
        "/api/joule/chart-brief",
        params={"brief_type": "caiso_fuel_mix_chart", "brief_date": "garbage"},
    )
    assert resp.status_code == 422


def test_chart_brief_absent_date_unchanged_no_date_filter(client):
    # The #8 contract: no brief_date -> latest by created_at, and the SQL must
    # NOT carry a brief_date filter or param.
    pool = use_rows([_fuel_mix_row()])
    resp = client.get(
        "/api/joule/chart-brief", params={"brief_type": "caiso_fuel_mix_chart"}
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == 18
    query = pool.sink["query"]
    assert "brief_date" not in query.split("WHERE", 1)[1].split("ORDER BY", 1)[0]
    assert pool.sink["params"] == {"brief_type": "caiso_fuel_mix_chart"}
