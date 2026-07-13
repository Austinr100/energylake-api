"""
Tests for the constraint→geometry map-layer endpoint:

  * GET /api/atlas/constraints/geometry

Two layers, no live database required:

  1. Pure-function unit tests on the geometry-ref bundler (`_atlas_geometry_ref`,
     shared with the blotter), the dollar-window resolver
     (`_atlas_geometry_window_days`), the layer SQL builder
     (`_build_atlas_geometry_query`), the coverage summary
     (`_atlas_geometry_summary`), the dollar rounder (`_atlas_geometry_dollar`),
     and the newest-success ledger SQL constant. These carry the pointer +
     window + SQL-shape + coverage-math contract without touching Postgres.

  2. Endpoint tests that swap a tiny in-memory fake pool into `main._pool` (same
     surface as the sibling atlas suites). The endpoint issues TWO reads on one
     cursor — `fetchone()` for the newest-success ledger row, then `fetchall()`
     for that build's geometry rows — so the fake cursor serves the ledger from
     fetchone and the rows from fetchall, and records every execute() so tests
     can assert newest-success scoping reaches the DB.
"""

import datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

import main


UTC = datetime.timezone.utc

URL = "/api/atlas/constraints/geometry"


# ---------------------------------------------------------------------------
# In-memory fake pool. One cursor: fetchone -> ledger row, fetchall -> rows.
# ---------------------------------------------------------------------------

class _FakeCursor:
    def __init__(self, store, sink):
        self._store = store
        self._sink = sink

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, query, params=None):
        self._sink.setdefault("calls", []).append({"query": query, "params": params})
        self._sink["query"] = query
        self._sink["params"] = params

    async def fetchone(self):
        return self._store["ledger"]

    async def fetchall(self):
        return list(self._store["rows"])


class _FakeConn:
    def __init__(self, store, sink):
        self._store = store
        self._sink = sink

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def cursor(self):
        return _FakeCursor(self._store, self._sink)


class FakePool:
    def __init__(self, ledger, rows):
        self._store = {"ledger": ledger, "rows": rows}
        self.sink = {}

    def connection(self):
        return _FakeConn(self._store, self.sink)


class _BoomPool:
    """Pool whose connection() raises — simulates DB unavailability."""

    def __init__(self):
        self.sink = {}

    def connection(self):
        raise RuntimeError("connection refused")


@pytest.fixture(autouse=True)
def _clear_cache():
    main._atlas_geometry_cache.clear()
    yield
    main._atlas_geometry_cache.clear()


@pytest.fixture
def client():
    # No `with` block => lifespan does not run => real pool is never opened.
    return TestClient(main.app)


def use(ledger, rows):
    pool = FakePool(ledger, rows)
    main._pool = pool
    return pool


def _ledger(build_id="cgb_20260713T224545Z_30d", window_days=30,
            rule_version="geom_v1", source_as_of=None):
    return {
        "build_id": build_id,
        "source_as_of": source_as_of or {
            "caiso_binding_constraints_max_ts": "2026-07-13T06:55:00+00:00",
        },
        "row_counts": {"n_written": 144},
        "params": {"window_days": window_days, "rule_version": rule_version},
        "build_ts": datetime.datetime(2026, 7, 13, 22, 45, 48, tzinfo=UTC),
        "finished_at": datetime.datetime(2026, 7, 13, 22, 45, 48, tzinfo=UTC),
    }


def _grow(constraint, resolution, dollar_weight, constraint_name="Base Case",
          constraint_type="BR", line_ids=None, sub_id=None, landmark_id=None,
          match_method=None, confidence=None, landmark_name=None,
          landmark_slug=None, landmark_function_type=None):
    """One row as _build_atlas_geometry_query's final SELECT yields it."""
    return {
        "constraint": constraint,
        "constraint_name": constraint_name,
        "constraint_type": constraint_type,
        "resolution": resolution,
        "line_ids": line_ids,
        "sub_id": sub_id,
        "landmark_id": landmark_id,
        "match_method": match_method,
        "confidence": Decimal(str(confidence)) if confidence is not None else None,
        "dollar_weight": Decimal(str(dollar_weight)),
        "landmark_name": landmark_name,
        "landmark_slug": landmark_slug,
        "landmark_function_type": landmark_function_type,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Pure: geometry_ref bundler (shared with the blotter)
# ═══════════════════════════════════════════════════════════════════════════

def test_geometry_ref_line_carries_line_and_landmark():
    ref = main._atlas_geometry_ref(
        "line", ["306104"], None, "lmk_hopland_gate", Decimal("0.9"), "br_line"
    )
    assert ref == {
        "resolution": "line",
        "line_ids": ["306104"],
        "sub_id": None,
        "landmark_id": "lmk_hopland_gate",
        "confidence": 0.9,
        "match_method": "br_line",
    }


def test_geometry_ref_point_carries_sub_id_as_int():
    ref = main._atlas_geometry_ref(
        "point", None, 8647, None, Decimal("0.85"), "xf_point"
    )
    assert ref["sub_id"] == 8647
    assert isinstance(ref["sub_id"], int)
    assert ref["line_ids"] is None


def test_geometry_ref_unmatched_is_none():
    # 'unmatched' -> no geometry to draw -> null (not an object of nulls).
    assert main._atlas_geometry_ref(
        "unmatched", None, None, None, Decimal("0"), "unmatched"
    ) is None


def test_geometry_ref_absent_row_is_none():
    # resolution None (constraint not in the build at all) -> null.
    assert main._atlas_geometry_ref(None, None, None, None, None, None) is None


def test_geometry_ref_approx_is_returned():
    # 'approx' has a sub_id and IS returned; the map reads confidence to draw it
    # approximately.
    ref = main._atlas_geometry_ref(
        "approx", None, 6482, None, Decimal("0.5"), "br_approx"
    )
    assert ref is not None
    assert ref["resolution"] == "approx"
    assert ref["sub_id"] == 6482
    assert ref["confidence"] == 0.5


# ═══════════════════════════════════════════════════════════════════════════
# Pure: dollar-window resolver
# ═══════════════════════════════════════════════════════════════════════════

def test_window_days_from_params():
    assert main._atlas_geometry_window_days({"window_days": 30}) == 30
    assert main._atlas_geometry_window_days({"window_days": 7}) == 7


def test_window_days_fallback_on_missing_or_junk():
    default = main.ATLAS_GEOMETRY_DEFAULT_WINDOW_DAYS
    assert main._atlas_geometry_window_days({}) == default
    assert main._atlas_geometry_window_days(None) == default
    assert main._atlas_geometry_window_days({"window_days": None}) == default
    assert main._atlas_geometry_window_days({"window_days": "abc"}) == default
    assert main._atlas_geometry_window_days({"window_days": 0}) == default
    assert main._atlas_geometry_window_days({"window_days": -5}) == default


# ═══════════════════════════════════════════════════════════════════════════
# Pure: layer SQL builder
# ═══════════════════════════════════════════════════════════════════════════

def test_build_query_params_and_shape():
    sql, params = main._build_atlas_geometry_query(29)
    # days_back is threaded straight through (caller passes window_days - 1).
    assert params == {"days_back": 29}
    # Served from the newest-success + override VIEW, never the raw table.
    assert "constraint_geometry_current" in sql
    assert "FROM constraint_geometry cg" not in sql
    # Dollar weight = congestion-dollar proxy over the window.
    assert "ABS(d.mean_shadow_price) * d.hours_binding" in sql
    # Window anchored on the latest trade_date (blotter's anchor convention).
    assert "MAX(trade_date)" in sql
    assert "d.trade_date BETWEEN (anchor.a - %(days_back)s) AND anchor.a" in sql
    # Landmark display joined by the geometry's own landmark_id, ratified only.
    assert "atlas_landmarks l" in sql
    assert "l.landmark_id = g.landmark_id AND l.status = 'ratified'" in sql
    # Costliest first; deterministic tiebreak.
    assert "ORDER BY dollar_weight DESC, g.constraint_id ASC" in sql


# ═══════════════════════════════════════════════════════════════════════════
# Pure: coverage summary
# ═══════════════════════════════════════════════════════════════════════════

def test_summary_count_and_dollar_coverage():
    rows = [
        {"resolution": "line", "dollar_weight": Decimal("50")},
        {"resolution": "point", "dollar_weight": Decimal("10")},
        {"resolution": "landmark", "dollar_weight": Decimal("40")},  # lpl = 100
        {"resolution": "approx", "dollar_weight": Decimal("60")},
        {"resolution": "unmatched", "dollar_weight": Decimal("40")},  # total 200
    ]
    s = main._atlas_geometry_summary(rows, 30)
    assert s["n_constraints"] == 5
    assert s["by_resolution"] == {
        "line": 1, "point": 1, "landmark": 1, "approx": 1, "unmatched": 1,
    }
    # 3 of 5 constraints placed with real geometry.
    assert s["count_coverage_pct"] == 60.0
    # Dollar tiers: strict = 100/200, with_approx = 160/200.
    assert s["dollar_coverage"]["window_days"] == 30
    assert s["dollar_coverage"]["strict_pct"] == 50.0
    assert s["dollar_coverage"]["with_approx_pct"] == 80.0
    assert s["dollar_coverage"]["total_dollar_weight"] == 200.0


def test_summary_dollars_run_ahead_of_count():
    # The whole point of dollar-coverage: a couple of costly placed constraints
    # can cover most of the money while covering few of the rows.
    rows = (
        [{"resolution": "line", "dollar_weight": Decimal("900")}]
        + [{"resolution": "unmatched", "dollar_weight": Decimal("10")}
           for _ in range(9)]
    )
    s = main._atlas_geometry_summary(rows, 30)
    assert s["count_coverage_pct"] == 10.0        # 1 of 10 rows
    assert s["dollar_coverage"]["strict_pct"] == 90.9   # 900 of 990 dollars


def test_summary_zero_dollars_is_none_not_divide_by_zero():
    rows = [
        {"resolution": "line", "dollar_weight": Decimal("0")},
        {"resolution": "unmatched", "dollar_weight": Decimal("0")},
    ]
    s = main._atlas_geometry_summary(rows, 30)
    assert s["count_coverage_pct"] == 50.0        # count still divides
    assert s["dollar_coverage"]["strict_pct"] is None
    assert s["dollar_coverage"]["with_approx_pct"] is None


def test_summary_empty_rows():
    s = main._atlas_geometry_summary([], 30)
    assert s["n_constraints"] == 0
    assert s["count_coverage_pct"] is None
    assert s["dollar_coverage"]["strict_pct"] is None
    assert s["dollar_coverage"]["total_dollar_weight"] == 0


# ═══════════════════════════════════════════════════════════════════════════
# Pure: dollar rounder + ledger SQL constant
# ═══════════════════════════════════════════════════════════════════════════

def test_dollar_rounds_to_two_dp():
    assert main._atlas_geometry_dollar(Decimal("30251.409")) == 30251.41
    assert main._atlas_geometry_dollar(None) is None


def test_latest_success_sql_scoping():
    sql = main._ATLAS_GEOMETRY_LATEST_SUCCESS_SQL
    assert "status = 'success'" in sql             # a newer FAILED build ignored
    assert "build_id <> 'cgb_manual'" in sql       # override channel excluded
    assert "ORDER BY started_at DESC" in sql
    assert "LIMIT 1" in sql


# ═══════════════════════════════════════════════════════════════════════════
# Endpoint: envelope + row shaping
# ═══════════════════════════════════════════════════════════════════════════

def test_envelope_and_row_shaping(client):
    use(_ledger(), [
        _grow("31336_HPLND JT_60.0_31370_CLVRDLJT_60.0_BR_1 _1", "line", 30251.4,
              constraint_name="PG1 EGLRK-SLVR-FLTN 115_M",
              line_ids=["306104"], landmark_id="lmk_hopland_gate",
              match_method="br_line", confidence=0.9,
              landmark_name="Hopland Gate", landmark_slug="hopland-gate",
              landmark_function_type="gate"),
        _grow("34540_HENRITTA_70.0_30881_HENRIETA_230_XF_4", "unmatched", 812.0,
              constraint_type="XF", match_method="unmatched", confidence=0),
    ])
    resp = client.get(URL)
    assert resp.status_code == 200
    body = resp.json()

    assert set(body) == {"build_id", "rule_version", "as_of", "summary", "rows"}
    assert body["build_id"] == "cgb_20260713T224545Z_30d"
    assert body["rule_version"] == "geom_v1"
    assert body["as_of"]["caiso_binding_constraints_max_ts"] == \
        "2026-07-13T06:55:00+00:00"

    hopland = body["rows"][0]
    assert set(hopland) == {
        "constraint", "constraint_name", "constraint_type", "resolution",
        "geometry_ref", "landmark", "dollar_weight",
    }
    assert hopland["resolution"] == "line"
    assert hopland["geometry_ref"] == {
        "resolution": "line",
        "line_ids": ["306104"],
        "sub_id": None,
        "landmark_id": "lmk_hopland_gate",
        "confidence": 0.9,
        "match_method": "br_line",
    }
    assert hopland["landmark"] == {
        "name": "Hopland Gate", "slug": "hopland-gate", "function_type": "gate",
    }
    assert hopland["dollar_weight"] == 30251.4


def test_unmatched_row_carries_resolution_and_null_geometry(client):
    use(_ledger(), [
        _grow("34540_HENRITTA_70.0_30881_HENRIETA_230_XF_4", "unmatched", 812.0,
              constraint_type="XF", match_method="unmatched", confidence=0),
    ])
    row = client.get(URL).json()["rows"][0]
    # The unmatched contract: resolution surfaces at the row level, geometry null.
    assert row["resolution"] == "unmatched"
    assert row["geometry_ref"] is None
    assert row["landmark"] is None


def test_summary_reaches_payload(client):
    use(_ledger(window_days=30), [
        _grow("A", "line", 50), _grow("B", "point", 10),
        _grow("C", "landmark", 40), _grow("D", "approx", 60),
        _grow("E", "unmatched", 40),
    ])
    summary = client.get(URL).json()["summary"]
    assert summary["count_coverage_pct"] == 60.0
    assert summary["dollar_coverage"]["strict_pct"] == 50.0
    assert summary["dollar_coverage"]["with_approx_pct"] == 80.0
    assert summary["dollar_coverage"]["window_days"] == 30


def test_window_days_from_ledger_drives_dollar_window(client):
    # The dollar window is the build's own window_days: days_back = window_days-1
    # must reach the SQL params.
    pool = use(_ledger(window_days=7), [_grow("A", "line", 1)])
    client.get(URL)
    # The second execute (the rows query) carries days_back.
    row_call = pool.sink["calls"][-1]
    assert row_call["params"] == {"days_back": 6}


# ═══════════════════════════════════════════════════════════════════════════
# Endpoint: scoping + cache + error contract
# ═══════════════════════════════════════════════════════════════════════════

def test_newest_success_ledger_is_read(client):
    pool = use(_ledger(), [_grow("A", "line", 1)])
    client.get(URL)
    first = pool.sink["calls"][0]["query"]
    assert "status = 'success'" in first
    assert "build_id <> 'cgb_manual'" in first


def test_no_successful_build_is_503(client):
    use(None, [])       # ledger fetchone yields None -> no build universe
    resp = client.get(URL)
    assert resp.status_code == 503
    assert "no successful" in resp.json()["detail"]


def test_empty_build_returns_200_zero_coverage(client):
    # A build exists but placed nothing -> honest zero-coverage, not an error.
    use(_ledger(), [])
    body = client.get(URL).json()
    assert body["rows"] == []
    assert body["summary"]["n_constraints"] == 0
    assert body["summary"]["count_coverage_pct"] is None


def test_db_unavailable_is_503(client):
    main._pool = _BoomPool()
    resp = client.get(URL)
    assert resp.status_code == 503
    assert "db unavailable" in resp.json()["detail"]


def test_cache_serves_hit_without_touching_db(client):
    pool = use(_ledger(), [_grow("A", "line", 1)])
    client.get(URL)
    calls_after_first = len(pool.sink["calls"])
    # Swap in a pool that would blow up if touched — a cache hit never touches it.
    main._pool = _BoomPool()
    resp = client.get(URL)
    assert resp.status_code == 200
    # The boom pool recorded nothing; the first pool's call count is unchanged.
    assert len(pool.sink["calls"]) == calls_after_first
