"""
Tests for GET /api/weather/brief/latest and /api/weather/brief/history
(Kelvin — the Weather Desk, 2026-07-20).

Mirrors test_chart_brief: a tiny in-memory fake pool returns canned rows so the
route's SQL + shaping run end-to-end with no live Neon. The TestClient is used
WITHOUT its context-manager form, so the app lifespan (real pool open) never
runs.

The fake cursor here adds `fetchall` (the history route needs it) on top of the
`fetchone` surface the chart-brief harness uses.
"""

import datetime

import pytest
from fastapi.testclient import TestClient

import main


# ---------------------------------------------------------------------------
# In-memory fake pool (chart-brief surface + fetchall for the history route).
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

    async def fetchall(self):
        return list(self._rows)


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


def _weather_row(brief_date=datetime.date(2026, 7, 20), rid=101,
                 degraded=("extended",), empty=()):
    return {
        "id": rid,
        "brief_date": brief_date,
        "headline": "Interior heat persists; Sacramento runs 9.7F above normal",
        "content_md": (
            "Interior heat persists; Sacramento runs 9.7F above normal\n\n"
            "Body paragraphs here.\n\nWatching: the outlook.\n\n"
            "By Kelvin · Weather Desk (agent)"
        ),
        "word_count": 447,
        "voice_version": "v1",
        "sources_used": {
            "bundle_version": "weather_bundle_v1",
            "as_of_date": brief_date.isoformat(),
            "degraded_beats": list(degraded),
            "empty_beats": list(empty),
            "gates": {
                "nws_obs_hourly": {"status": "FRESH",
                                   "lifecycle_status": "active",
                                   "days_behind": 0.1},
                "climate_qbo_monthly": {"status": "STALE",
                                        "lifecycle_status": "planned",
                                        "days_behind": 169.7},
            },
        },
        "created_at": datetime.datetime(
            2026, 7, 20, 18, 24, 30, tzinfo=datetime.timezone.utc),
    }


# ── /latest happy path ─────────────────────────────────────────────────────

def test_latest_shape_and_byline(client):
    use_rows([_weather_row()])
    r = client.get("/api/weather/brief/latest")
    assert r.status_code == 200
    b = r.json()
    assert b["brief_type"] == "weather"
    # The byline invariant — "(agent)" present, exactly.
    assert b["byline"] == "By Kelvin · Weather Desk (agent)"
    assert b["dateline"]["byline"] == "By Kelvin · Weather Desk (agent)"
    # A stored row is a verified row (writer upserts only on PASS).
    assert b["dateline"]["verified"] is True
    assert b["dateline"]["tz"] == "America/Los_Angeles"
    assert b["dateline"]["date"] == "2026-07-20"
    # Column renames mirror the hub brief.
    assert b["body"].startswith("Interior heat")
    assert b["generated_at"].startswith("2026-07-20T18:24:30")
    assert b["word_count"] == 447
    assert b["voice_version"] == "v1"
    assert b["id"] == 101
    print("ok latest_shape_and_byline")


def test_latest_surfaces_gate_receipts_and_beats(client):
    use_rows([_weather_row(degraded=("extended",), empty=())])
    b = client.get("/api/weather/brief/latest").json()
    # Gate receipts from sources_used so the tab can PROVE what was fresh.
    assert b["gates"]["nws_obs_hourly"]["status"] == "FRESH"
    assert b["gates"]["climate_qbo_monthly"]["status"] == "STALE"
    assert b["degraded_beats"] == ["extended"]
    assert b["empty_beats"] == []
    assert b["bundle_version"] == "weather_bundle_v1"
    print("ok latest_surfaces_gate_receipts_and_beats")


def test_latest_empty_is_200_null_not_404(client):
    use_rows([])  # no brief yet
    r = client.get("/api/weather/brief/latest")
    assert r.status_code == 200                 # degrade quietly, never 404
    b = r.json()
    assert b["brief_type"] == "weather"
    assert b["body"] is None
    assert b["dateline"] is None                # nothing known
    assert b["publication"] is not None         # rider always present
    print("ok latest_empty_is_200_null_not_404")


def test_latest_orders_newest_first_not_distinct_on(client):
    use_rows([_weather_row()])
    client.get("/api/weather/brief/latest")
    q = main._pool.sink["query"]
    assert "ORDER BY created_at DESC" in q
    assert "LIMIT 1" in q
    assert "DISTINCT ON" not in q               # newest-row, not per-key latest
    print("ok latest_orders_newest_first")


def test_latest_pins_a_brief_date(client):
    use_rows([_weather_row(brief_date=datetime.date(2026, 7, 18), rid=99)])
    r = client.get("/api/weather/brief/latest?brief_date=2026-07-18")
    assert r.status_code == 200
    assert main._pool.sink["params"]["brief_date"] == datetime.date(2026, 7, 18)
    assert "AND brief_date = %(brief_date)s" in main._pool.sink["query"]
    print("ok latest_pins_a_brief_date")


def test_latest_rejects_garbage_date(client):
    use_rows([_weather_row()])
    r = client.get("/api/weather/brief/latest?brief_date=not-a-date")
    assert r.status_code == 422                 # FastAPI _date typing
    print("ok latest_rejects_garbage_date")


def test_latest_tolerates_null_sources_used(client):
    """A legacy row that predates receipt-stamping must not crash the shaper."""
    row = _weather_row()
    row["sources_used"] = None
    use_rows([row])
    b = client.get("/api/weather/brief/latest").json()
    assert b["gates"] == {}
    assert b["degraded_beats"] == []
    assert b["bundle_version"] is None
    print("ok latest_tolerates_null_sources_used")


# ── /history ───────────────────────────────────────────────────────────────

def test_history_returns_list_summary_only(client):
    use_rows([
        _weather_row(datetime.date(2026, 7, 20), rid=3),
        _weather_row(datetime.date(2026, 7, 19), rid=2, degraded=(), empty=("market overlay",)),
        _weather_row(datetime.date(2026, 7, 18), rid=1),
    ])
    r = client.get("/api/weather/brief/history")
    assert r.status_code == 200
    h = r.json()
    assert h["brief_type"] == "weather"
    assert h["count"] == 3
    assert len(h["briefs"]) == 3
    first = h["briefs"][0]
    # Summary shape — no full body in the strip.
    assert "body" not in first and "content_md" not in first
    assert first["verified"] is True
    assert first["brief_date"] == "2026-07-20"
    assert h["briefs"][1]["empty_beats"] == ["market overlay"]
    print("ok history_returns_list_summary_only")


def test_history_empty_is_empty_list_not_404(client):
    use_rows([])
    r = client.get("/api/weather/brief/history")
    assert r.status_code == 200
    h = r.json()
    assert h["count"] == 0 and h["briefs"] == []
    print("ok history_empty_is_empty_list")


def test_history_limit_is_bound_and_clamped(client):
    use_rows([_weather_row()])
    client.get("/api/weather/brief/history?limit=30")
    assert main._pool.sink["params"]["limit"] == 30
    assert "LIMIT %(limit)s" in main._pool.sink["query"]
    # Over the ceiling -> 422 (le=60).
    assert client.get("/api/weather/brief/history?limit=999").status_code == 422
    # Below the floor -> 422 (ge=1).
    assert client.get("/api/weather/brief/history?limit=0").status_code == 422
    print("ok history_limit_is_bound_and_clamped")


def test_history_orders_by_date_then_created(client):
    use_rows([_weather_row()])
    client.get("/api/weather/brief/history")
    q = main._pool.sink["query"]
    assert "ORDER BY brief_date DESC, created_at DESC" in q
    print("ok history_orders_by_date_then_created")


if __name__ == "__main__":
    import sys
    c = TestClient(main.app)
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn(c)
    print(f"\nALL {len(fns)} weather-brief API tests passed.")
