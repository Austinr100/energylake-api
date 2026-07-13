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
    body = resp.json()
    # The additive publication rider rides alongside; pop it and assert the
    # existing chart-brief contract is byte-for-byte unchanged.
    publication = body.pop("publication")
    assert body == {
        "brief_type": "caiso_fuel_mix_chart",
        "body": "Solar carried the midday peak while gas backfilled the evening ramp.",
        "brief_date": "2026-06-22",
        "voice_version": "v2.1",
        "word_count": 71,
        "generated_at": "2026-06-23T11:45:37.734038+00:00",
        "id": 18,
    }
    # A month-old fuel-mix brief (12:00 UTC, 60m grace) is unambiguously overdue.
    assert publication["status"] == "overdue"
    assert publication["trade_date"] == "2026-06-22"
    assert publication["published_at"] == "2026-06-23T11:45:37.734038+00:00"


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


# ---------------------------------------------------------------------------
# Allow-list extension (cc_spec 2026-06-26): the two Today deviation chart
# types are admitted (the rows already exist in joule_briefs, the API just
# wasn't handing them out), plus a forward reservation for week-ahead. The
# guardrail stays a membership gate — editorial / non-chart types still 400.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "chart_type",
    [
        "caiso_load_deviation",
        "caiso_renewables_deviation",
        "caiso_week_ahead",
    ],
)
def test_chart_brief_extended_types_admitted(client, chart_type):
    # A row of the new type maps through the same flat contract — the only
    # type-specific thing is membership, so a present row returns 200 with body.
    row = _fuel_mix_row()
    row["brief_type"] = chart_type
    use_rows([row])
    resp = client.get("/api/joule/chart-brief", params={"brief_type": chart_type})
    assert resp.status_code == 200
    body = resp.json()
    assert body["brief_type"] == chart_type
    assert body["body"] == row["content_md"]


@pytest.mark.parametrize(
    "chart_type",
    ["caiso_load_deviation", "caiso_renewables_deviation"],
)
def test_chart_brief_deviation_types_not_400(client, chart_type):
    # The bug this PR fixes: these used to 4xx with "unknown brief_type".
    use_rows([])
    resp = client.get("/api/joule/chart-brief", params={"brief_type": chart_type})
    assert resp.status_code == 200


def test_chart_brief_week_ahead_reserved_empty_is_200_null(client):
    # Empty case for the admitted week-ahead type: no rows -> 200/null, never a
    # 400. (A populated row is covered by test_chart_brief_extended_types_admitted.)
    use_rows([])
    resp = client.get(
        "/api/joule/chart-brief", params={"brief_type": "caiso_week_ahead"}
    )
    assert resp.status_code == 200
    assert resp.json()["body"] is None


def test_chart_brief_editorial_daily_still_400(client):
    # Guardrail intact: the editorial Daily Brief type ('daily') must NOT be
    # pullable through the chart-commentary route.
    use_rows([_fuel_mix_row()])
    resp = client.get(
        "/api/joule/chart-brief", params={"brief_type": main.BRIEF_TYPE_DAILY}
    )
    assert resp.status_code == 400
    assert "daily" in resp.json()["detail"]


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


# ---------------------------------------------------------------------------
# Optional `key` param (cc_spec 2026-07-06). The caiso_hub_lmp chart-brief type
# partitions its briefs by brief_key (one row per hub slug). `key` selects the
# hub; it is validated against the closed slug set (unknown -> 404) and defaults
# the bound brief_key param to '' when omitted. Every non-keyed brief_type
# ignores `key` and keeps its exact prior SQL/params — byte-identical behavior.
# ---------------------------------------------------------------------------

HUB_KEYS = ["TH_NP15", "TH_SP15", "TH_ZP26"]


def _hub_row(key="TH_SP15"):
    row = _fuel_mix_row()
    row["id"] = 42
    row["brief_type"] = "caiso_hub_lmp"
    row["content_md"] = f"SP15 DA held a premium to RT through the evening ramp ({key})."
    return row


def test_hub_lmp_admitted_to_allowlist():
    assert "caiso_hub_lmp" in main.CHART_BRIEF_TYPES
    assert set(main.CHART_BRIEF_KEYS["caiso_hub_lmp"]) == set(HUB_KEYS)


def test_chart_brief_absent_key_on_existing_type_is_byte_identical(client):
    # A non-keyed type never gains a brief_key clause or param, with OR without
    # the query param present — the whole point of "existing types unaffected."
    pool = use_rows([_fuel_mix_row()])
    resp = client.get(
        "/api/joule/chart-brief", params={"brief_type": "caiso_fuel_mix_chart"}
    )
    assert resp.status_code == 200
    assert "brief_key" not in pool.sink["query"]
    assert pool.sink["params"] == {"brief_type": "caiso_fuel_mix_chart"}


def test_chart_brief_key_ignored_for_non_keyed_type(client):
    # Passing ?key to a non-keyed type is a no-op: same row, same SQL, no 404.
    pool = use_rows([_fuel_mix_row()])
    resp = client.get(
        "/api/joule/chart-brief",
        params={"brief_type": "caiso_fuel_mix_chart", "key": "TH_SP15"},
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == 18
    assert "brief_key" not in pool.sink["query"]
    assert pool.sink["params"] == {"brief_type": "caiso_fuel_mix_chart"}


@pytest.mark.parametrize("key", HUB_KEYS)
def test_chart_brief_hub_with_key_filters_brief_key(client, key):
    # Keyed type + valid key -> brief_key clause bound to that exact slug.
    pool = use_rows([_hub_row(key)])
    resp = client.get(
        "/api/joule/chart-brief",
        params={"brief_type": "caiso_hub_lmp", "key": key},
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == 42
    assert "AND brief_key = %(brief_key)s" in pool.sink["query"]
    assert pool.sink["params"] == {"brief_type": "caiso_hub_lmp", "brief_key": key}


def test_chart_brief_hub_absent_key_defaults_to_empty(client):
    # Keyed type WITHOUT ?key: brief_key is pinned to '' (deterministic empty),
    # never left off so the latest row of any hub bleeds through.
    pool = use_rows([])
    resp = client.get(
        "/api/joule/chart-brief", params={"brief_type": "caiso_hub_lmp"}
    )
    assert resp.status_code == 200
    assert resp.json()["body"] is None
    assert "AND brief_key = %(brief_key)s" in pool.sink["query"]
    assert pool.sink["params"] == {"brief_type": "caiso_hub_lmp", "brief_key": ""}


def test_chart_brief_hub_unknown_key_is_404_not_empty_fallback(client):
    # Unknown key -> clean 404. Must NOT fall back to the '' partition, and the
    # DB is never queried (the guard fires before any execute()).
    pool = use_rows([_hub_row()])
    resp = client.get(
        "/api/joule/chart-brief",
        params={"brief_type": "caiso_hub_lmp", "key": "TH_SP99"},
    )
    assert resp.status_code == 404
    assert "TH_SP99" in resp.json()["detail"]
    assert pool.sink == {}


def test_chart_brief_hub_key_and_date_both_filter(client):
    # key + brief_date compose: both clauses present, both params bound.
    pool = use_rows([_hub_row("TH_NP15")])
    resp = client.get(
        "/api/joule/chart-brief",
        params={
            "brief_type": "caiso_hub_lmp",
            "key": "TH_NP15",
            "brief_date": "2026-07-05",
        },
    )
    assert resp.status_code == 200
    query = pool.sink["query"]
    assert "AND brief_key = %(brief_key)s" in query
    assert "AND brief_date = %(brief_date)s" in query
    assert pool.sink["params"] == {
        "brief_type": "caiso_hub_lmp",
        "brief_key": "TH_NP15",
        "brief_date": datetime.date(2026, 7, 5),
    }


def test_chart_brief_hub_valid_key_missing_date_is_200_null(client):
    # Valid key but no brief for that (key, date) combo -> 200/null, not 404.
    # The 404 is reserved for an *unknown key*, not an empty result set.
    pool = use_rows([])
    resp = client.get(
        "/api/joule/chart-brief",
        params={
            "brief_type": "caiso_hub_lmp",
            "key": "TH_ZP26",
            "brief_date": "2026-07-05",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["body"] is None
    assert body["brief_type"] == "caiso_hub_lmp"
    assert body["brief_date"] == "2026-07-05"
    assert pool.sink["params"] == {
        "brief_type": "caiso_hub_lmp",
        "brief_key": "TH_ZP26",
        "brief_date": datetime.date(2026, 7, 5),
    }
