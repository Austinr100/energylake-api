"""
Tests for the CAISO binding-constraint blotter league table:

  * GET /api/atlas/constraints?market={DAM|RTD|RTM}&window={1d|7d|30d|90d}
    (RTM is a deprecated alias resolving to RTD during the pantry label
    transition — migration 072 relabels stored market='RTM' -> 'RTD'.)

Two layers, no live database required:

  1. Pure-function unit tests on the aggregation SQL builder
     (`_build_atlas_constraints_query`), the param validator
     (`_parse_atlas_constraints_params`), and the stale predicate
     (`_atlas_constraints_is_stale`). These carry the SQL-shape + window-math +
     staleness contract without touching Postgres.

  2. Endpoint tests that swap a tiny in-memory fake pool into `main._pool` (same
     surface as the sibling pnode suites) and feed the rows the final SELECT
     *would* return — one row per constraint carrying constraint/hours_bound/
     max_shadow/avg_shadow_when_bound/last_bound_ts/trend plus the per-row `as_of`
     scalar. The tests assert row shaping (int-rounded hours + trend, ISO
     timestamps), the stale flag, the 5-min in-process cache, and the
     400/500/503 error contract.
"""

import datetime
from decimal import Decimal

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import main


UTC = datetime.timezone.utc


# ---------------------------------------------------------------------------
# In-memory fake pool (same surface as tests/test_pnode_history.py).
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


class _BoomPool:
    """Pool whose connection() raises — simulates DB unavailability."""

    def __init__(self):
        self.sink = {}

    def connection(self):
        raise RuntimeError("connection refused")


URL = "/api/atlas/constraints"


@pytest.fixture(autouse=True)
def _clear_cache():
    # The in-process cache is module-global; isolate every test.
    main._atlas_constraints_cache.clear()
    yield
    main._atlas_constraints_cache.clear()


@pytest.fixture
def client():
    # No `with` block => lifespan does not run => real pool is never opened.
    return TestClient(main.app)


def use_rows(rows):
    pool = FakePool(rows)
    main._pool = pool
    return pool


def _row(constraint, hours_bound, max_shadow, avg_shadow, last_bound_ts, trend,
         as_of, landmark_name=None, landmark_slug=None,
         landmark_function_type=None, geom_resolution=None, geom_line_ids=None,
         geom_sub_id=None, geom_landmark_id=None, geom_match_method=None,
         geom_confidence=None):
    """One row as the final SELECT yields it (psycopg dict_row). Numerics arrive
    as Decimal; trend is a Python list; timestamps are tz-aware datetimes. The
    landmark_* columns come from the LEFT JOIN on the ratified gazetteer — all
    null when the constraint belongs to no ratified landmark. The geom_* columns
    come from the LEFT JOIN on constraint_geometry_current — all null when the
    constraint has no geometry row in the newest-success build."""
    return {
        "constraint": constraint,
        "hours_bound": Decimal(str(hours_bound)),
        "max_shadow": Decimal(str(max_shadow)) if max_shadow is not None else None,
        "avg_shadow_when_bound": (
            Decimal(str(avg_shadow)) if avg_shadow is not None else None
        ),
        "last_bound_ts": last_bound_ts,
        "trend": [Decimal(str(v)) for v in trend],
        "as_of": as_of,
        "landmark_name": landmark_name,
        "landmark_slug": landmark_slug,
        "landmark_function_type": landmark_function_type,
        "geom_resolution": geom_resolution,
        "geom_line_ids": geom_line_ids,
        "geom_sub_id": geom_sub_id,
        "geom_landmark_id": geom_landmark_id,
        "geom_match_method": geom_match_method,
        "geom_confidence": (
            Decimal(str(geom_confidence)) if geom_confidence is not None else None
        ),
    }


def _recent(hours_ago=1):
    return datetime.datetime.now(UTC) - datetime.timedelta(hours=hours_ago)


# ═══════════════════════════════════════════════════════════════════════════
# Pure: param validator
# ═══════════════════════════════════════════════════════════════════════════

def test_parse_params_defaults_and_normalization():
    # case-insensitive market, default window 7d -> 7 days.
    assert main._parse_atlas_constraints_params("dam", "7d") == ("DAM", "7d", 7)
    # RTM is the deprecated alias -> resolves to the canonical RTD label.
    assert main._parse_atlas_constraints_params(" RtM ", "1d") == ("RTD", "1d", 1)
    assert main._parse_atlas_constraints_params("rtd", "1d") == ("RTD", "1d", 1)
    assert main._parse_atlas_constraints_params("DAM", "30d") == ("DAM", "30d", 30)
    assert main._parse_atlas_constraints_params("DAM", "90d") == ("DAM", "90d", 90)


def test_parse_params_rtm_alias_resolves_to_rtd():
    # Both the legacy alias and the canonical token resolve to 'RTD'; DAM is
    # unchanged. The resolved label is what the response echoes / the cache keys on.
    assert main._parse_atlas_constraints_params("RTM", "7d")[0] == "RTD"
    assert main._parse_atlas_constraints_params("RTD", "7d")[0] == "RTD"
    assert main._parse_atlas_constraints_params("DAM", "7d")[0] == "DAM"


def test_parse_params_bad_market_is_400():
    with pytest.raises(HTTPException) as ei:
        main._parse_atlas_constraints_params("MISO", "7d")
    assert ei.value.status_code == 400
    assert "market" in ei.value.detail


def test_parse_params_rtpd_is_400():
    # RTPD is prepared in the lake mapping but NOT yet exposed -> rejected exactly
    # like any invalid value until RTPD rows exist (activation is a one-line
    # uncomment in each transition map).
    with pytest.raises(HTTPException) as ei:
        main._parse_atlas_constraints_params("RTPD", "7d")
    assert ei.value.status_code == 400
    assert "market" in ei.value.detail


def test_parse_params_bad_window_is_400():
    with pytest.raises(HTTPException) as ei:
        main._parse_atlas_constraints_params("DAM", "14d")
    assert ei.value.status_code == 400
    assert "window" in ei.value.detail


# ═══════════════════════════════════════════════════════════════════════════
# Pure: aggregation SQL builder
# ═══════════════════════════════════════════════════════════════════════════

def test_build_query_params_math():
    _, params = main._build_atlas_constraints_query("DAM", 90, 100)
    # days_back is (days - 1): a 90-day window spans [anchor-89 .. anchor].
    assert params == {"markets": ["DAM"], "days_back": 89, "cap": 100}

    _, params_1d = main._build_atlas_constraints_query("RTD", 1, 100)
    assert params_1d["days_back"] == 0  # 1-day window is a single day (anchor)


def test_build_query_dual_label_for_rtd():
    # RTD must match BOTH the new 'RTD' rows and the legacy 'RTM' rows so the
    # endpoint is correct before AND after migration 072 UPDATEs the symbol.
    sql, params = main._build_atlas_constraints_query("RTD", 7, 100)
    assert "market = ANY(%(markets)s)" in sql
    assert "%(market)s" not in sql  # no single-label predicate lingers
    assert params["markets"] == ["RTD", "RTM"]


def test_build_query_dam_single_label():
    # DAM is a single stable symbol — unchanged by the transition.
    sql, params = main._build_atlas_constraints_query("DAM", 7, 100)
    assert "market = ANY(%(markets)s)" in sql
    assert params["markets"] == ["DAM"]


def test_build_query_sql_shape():
    sql, _ = main._build_atlas_constraints_query("DAM", 7, 100)
    # Sourced from the daily rollup, keyed by constraint_id.
    assert "caiso_binding_constraints_daily" in sql
    assert "GROUP BY constraint_id" in sql
    # League table: hours DESC with a deterministic tiebreak, capped.
    assert "ORDER BY hours_bound DESC, constraint_id ASC" in sql
    assert "LIMIT %(cap)s" in sql
    # Window anchored on the latest trade_date; full N-day axis via series.
    assert "MAX(trade_date)" in sql
    assert "generate_series" in sql
    # Interval-weighted average (not a naive AVG of daily means).
    assert "mean_shadow_price * intervals_binding" in sql
    # as_of = max last_binding_ts across the window.
    assert "MAX(last_binding_ts) AS as_of" in sql


def test_build_query_landmark_join():
    # The gazetteer name rides in via a LEFT JOIN to the members/landmarks
    # tables, filtered to ratified. This filter is the guard that provisional
    # and retired landmarks NEVER surface on the blotter.
    sql, _ = main._build_atlas_constraints_query("DAM", 7, 100)
    assert "atlas_landmark_members" in sql
    assert "atlas_landmarks" in sql
    assert "l.status = 'ratified'" in sql            # provisional/retired excluded
    assert "lm.entity_type = 'constraint'" in sql    # only constraint members
    assert "LEFT JOIN landmarks" in sql              # unnamed constraints kept
    # DISTINCT ON guards the league-table row count against double-membership.
    assert "DISTINCT ON (lm.entity_id)" in sql
    # The three landmark columns the endpoint shapes into the `landmark` object.
    assert "l.canonical_name AS landmark_name" in sql
    assert "l.slug           AS landmark_slug" in sql
    assert "l.function_type  AS landmark_function_type" in sql


def test_build_query_geometry_join():
    # geometry_ref rides in via a LEFT JOIN to the newest-success geometry view,
    # so a constraint with no geometry row simply carries geometry_ref: null.
    sql, _ = main._build_atlas_constraints_query("DAM", 7, 100)
    assert "constraint_geometry_current" in sql       # the scoped view, not raw
    assert "LEFT JOIN geometry g" in sql              # unplaced constraints kept
    # The geom_* columns the endpoint feeds to _atlas_geometry_ref.
    assert "g.resolution     AS geom_resolution" in sql
    assert "g.line_ids       AS geom_line_ids" in sql
    assert "g.landmark_id    AS geom_landmark_id" in sql
    assert "g.confidence     AS geom_confidence" in sql


# ═══════════════════════════════════════════════════════════════════════════
# Pure: stale predicate
# ═══════════════════════════════════════════════════════════════════════════

def test_stale_no_data():
    now = datetime.datetime.now(UTC)
    assert main._atlas_constraints_is_stale(None, now) is True


def test_stale_fresh_vs_old():
    now = datetime.datetime(2026, 7, 11, 12, 0, tzinfo=UTC)
    fresh = now - datetime.timedelta(hours=47, minutes=59)
    old = now - datetime.timedelta(hours=48, minutes=1)
    assert main._atlas_constraints_is_stale(fresh, now) is False
    assert main._atlas_constraints_is_stale(old, now) is True


def test_stale_exactly_48h_is_not_stale():
    # Boundary: exactly 48h is the edge of the window, not past it.
    now = datetime.datetime(2026, 7, 11, 12, 0, tzinfo=UTC)
    edge = now - datetime.timedelta(hours=48)
    assert main._atlas_constraints_is_stale(edge, now) is False


# ═══════════════════════════════════════════════════════════════════════════
# Endpoint: envelope + row shaping
# ═══════════════════════════════════════════════════════════════════════════

def test_envelope_and_row_shaping(client):
    as_of = _recent(1)
    last = _recent(2)
    use_rows([
        _row("HPLND_CLVRDL", 1835, 553.15802, 160.9037631, last,
             trend=[10], as_of=as_of),
    ])
    resp = client.get(f"{URL}?market=DAM&window=1d")
    assert resp.status_code == 200
    body = resp.json()

    assert set(body) == {"as_of", "market", "window", "stale", "rows"}
    assert body["market"] == "DAM"
    assert body["window"] == "1d"
    assert body["stale"] is False
    assert body["as_of"] == as_of.isoformat()

    row = body["rows"][0]
    assert set(row) == {
        "constraint", "hours_bound", "max_shadow",
        "avg_shadow_when_bound", "last_bound_ts", "trend", "landmark",
        "geometry_ref",
    }
    assert row["constraint"] == "HPLND_CLVRDL"
    # hours reported as a rounded int per the contract.
    assert row["hours_bound"] == 1835
    assert isinstance(row["hours_bound"], int)
    assert row["max_shadow"] == 553.15802
    assert row["avg_shadow_when_bound"] == 160.90376  # rounded to 5dp
    assert row["last_bound_ts"] == last.isoformat()
    assert row["trend"] == [10]
    # This fixture row carried no landmark columns -> null (additive field).
    assert row["landmark"] is None
    # No geometry columns -> geometry_ref null (additive field).
    assert row["geometry_ref"] is None


def test_rtm_fractional_hours_round_to_int(client):
    as_of = _recent(1)
    use_rows([
        _row("USWP_SBTAP", Decimal("103.9168"), 109.14747, 80.6516242,
             _recent(1), trend=[7.25, 13.1667, 15.9167, 23.4167, 14.75, 10.25,
                                 19.1667], as_of=as_of),
    ])
    body = client.get(f"{URL}?market=RTM&window=7d").json()
    row = body["rows"][0]
    assert row["hours_bound"] == 104          # 103.9168 -> 104
    assert row["trend"] == [7, 13, 16, 23, 15, 10, 19]  # each per-day rounded
    assert len(row["trend"]) == 7


def test_default_window_is_7d(client):
    use_rows([
        _row("X", 5, 1.0, 0.5, _recent(1), trend=[1, 1, 1, 1, 1, 0, 0],
             as_of=_recent(1)),
    ])
    body = client.get(f"{URL}?market=DAM").json()
    assert body["window"] == "7d"


# ═══════════════════════════════════════════════════════════════════════════
# Endpoint: stale + empty
# ═══════════════════════════════════════════════════════════════════════════

def test_stale_true_when_data_old(client):
    old = datetime.datetime.now(UTC) - datetime.timedelta(hours=72)
    use_rows([
        _row("OLD", 3, 2.0, 1.0, old, trend=[3], as_of=old),
    ])
    body = client.get(f"{URL}?market=DAM&window=1d").json()
    assert body["stale"] is True
    assert body["as_of"] == old.isoformat()
    assert body["rows"][0]["constraint"] == "OLD"  # rows still served honestly


def test_empty_data_is_stale_with_no_rows(client):
    use_rows([])
    body = client.get(f"{URL}?market=RTM&window=90d").json()
    assert body["stale"] is True
    assert body["as_of"] is None
    assert body["rows"] == []


# ═══════════════════════════════════════════════════════════════════════════
# Endpoint: gazetteer landmark (per-row, additive, nullable)
# ═══════════════════════════════════════════════════════════════════════════

def test_named_row_carries_landmark(client):
    # The canonical named row: 7690-CONTRL-INYOKN_EXP_NG -> Inyokern Export
    # Corridor. name/slug/function_type surface; no blurb on the blotter.
    use_rows([
        _row("7690-CONTRL-INYOKN_EXP_NG", 42, 5.0, 2.5, _recent(1), trend=[42],
             as_of=_recent(1), landmark_name="Inyokern Export Corridor",
             landmark_slug="inyokern-export-corridor",
             landmark_function_type="corridor"),
    ])
    body = client.get(f"{URL}?market=RTM&window=1d").json()
    assert body["rows"][0]["landmark"] == {
        "name": "Inyokern Export Corridor",
        "slug": "inyokern-export-corridor",
        "function_type": "corridor",
    }


def test_unnamed_row_landmark_is_null(client):
    # A constraint in no ratified landmark -> the LEFT JOIN yields null columns
    # -> landmark is null (not an empty object, not a missing key).
    use_rows([
        _row("34724_KRN OL J_115_NOT_A_LANDMARK", 7, 1.0, 0.5, _recent(1),
             trend=[7], as_of=_recent(1)),
    ])
    row = client.get(f"{URL}?market=DAM&window=1d").json()["rows"][0]
    assert "landmark" in row
    assert row["landmark"] is None


def test_family_members_share_one_landmark(client):
    # Family-sharing by design: an OMS-prefixed Inyokern member resolves to the
    # SAME landmark as the 7690- member (many raw ids -> one name).
    use_rows([
        _row("7690-CONTRL-INYOKN_EXP_NG", 42, 5.0, 2.5, _recent(1), trend=[42],
             as_of=_recent(1), landmark_name="Inyokern Export Corridor",
             landmark_slug="inyokern-export-corridor",
             landmark_function_type="corridor"),
        _row("OMS20107865-CONTRL-INYOKN_EXP_NG", 30, 4.0, 2.0, _recent(1),
             trend=[30], as_of=_recent(1),
             landmark_name="Inyokern Export Corridor",
             landmark_slug="inyokern-export-corridor",
             landmark_function_type="corridor"),
    ])
    rows = client.get(f"{URL}?market=RTM&window=1d").json()["rows"]
    assert rows[0]["landmark"] == rows[1]["landmark"]
    assert rows[0]["landmark"]["name"] == "Inyokern Export Corridor"


# ═══════════════════════════════════════════════════════════════════════════
# Endpoint: geometry_ref (from the newest-success constraint-geometry build)
# ═══════════════════════════════════════════════════════════════════════════

def test_row_carries_geometry_ref_line_and_landmark(client):
    # The Hopland Gate case: a BR constraint resolved to line 306104, ALSO
    # carrying its ratified landmark id. geometry_ref carries both the line and
    # the landmark_id (line 306104 + landmark), per the pointer contract.
    use_rows([
        _row("31336_HPLND JT_60.0_31370_CLVRDLJT_60.0_BR_1 _1", 1835, 553.15802,
             160.9037631, _recent(1), trend=[10], as_of=_recent(1),
             landmark_name="Hopland Gate", landmark_slug="hopland-gate",
             landmark_function_type="gate",
             geom_resolution="line", geom_line_ids=["306104"],
             geom_landmark_id="lmk_hopland_gate", geom_match_method="br_line",
             geom_confidence=0.9),
    ])
    row = client.get(f"{URL}?market=DAM&window=1d").json()["rows"][0]
    assert row["geometry_ref"] == {
        "resolution": "line",
        "line_ids": ["306104"],
        "sub_id": None,
        "landmark_id": "lmk_hopland_gate",
        "confidence": 0.9,
        "match_method": "br_line",
    }
    # The gazetteer `landmark` object rides alongside, unchanged.
    assert row["landmark"]["name"] == "Hopland Gate"


def test_row_unmatched_geometry_ref_is_null(client):
    # An 'unmatched' geometry row -> geometry_ref null (no geometry to draw),
    # exactly like a constraint with no geometry row at all.
    use_rows([
        _row("34540_HENRITTA_70.0_30881_HENRIETA_230_XF_4", 12, 3.0, 1.5,
             _recent(1), trend=[12], as_of=_recent(1),
             geom_resolution="unmatched"),
    ])
    row = client.get(f"{URL}?market=DAM&window=1d").json()["rows"][0]
    assert row["geometry_ref"] is None


def test_row_approx_geometry_ref_carries_sub_id(client):
    # 'approx' IS a returned geometry (it has a substation sub_id); its low
    # confidence is the signal the map renders it approximately.
    use_rows([
        _row("22008_ASH_69.0_22012_ASH TP_69.0_BR_1 _1", 40, 8.0, 4.0,
             _recent(1), trend=[40], as_of=_recent(1),
             geom_resolution="approx", geom_sub_id=6482,
             geom_match_method="br_approx", geom_confidence=0.5),
    ])
    row = client.get(f"{URL}?market=DAM&window=1d").json()["rows"][0]
    assert row["geometry_ref"] == {
        "resolution": "approx",
        "line_ids": None,
        "sub_id": 6482,
        "landmark_id": None,
        "confidence": 0.5,
        "match_method": "br_approx",
    }


# ═══════════════════════════════════════════════════════════════════════════
# Endpoint: error contract
# ═══════════════════════════════════════════════════════════════════════════

def test_unknown_market_is_400(client):
    use_rows([])
    resp = client.get(f"{URL}?market=PJM&window=7d")
    assert resp.status_code == 400


def test_rtpd_market_is_400(client):
    # RTPD is prepared but NOT exposed until its rows land -> 400 like any invalid.
    use_rows([])
    resp = client.get(f"{URL}?market=RTPD&window=7d")
    assert resp.status_code == 400


def test_unknown_window_is_400(client):
    use_rows([])
    resp = client.get(f"{URL}?market=DAM&window=5d")
    assert resp.status_code == 400


def test_db_unavailable_is_503(client):
    main._pool = _BoomPool()
    resp = client.get(f"{URL}?market=DAM&window=7d")
    assert resp.status_code == 503


def test_trend_axis_skew_is_500(client):
    # window=7d -> 7 buckets expected; a 3-length trend is an internal skew.
    use_rows([
        _row("SKEW", 5, 1.0, 0.5, _recent(1), trend=[1, 2, 3], as_of=_recent(1)),
    ])
    resp = client.get(f"{URL}?market=DAM&window=7d")
    assert resp.status_code == 500


# ═══════════════════════════════════════════════════════════════════════════
# Endpoint: in-process cache (5-min TTL, keyed (market, window))
# ═══════════════════════════════════════════════════════════════════════════

def test_cache_serves_hit_without_touching_db(client):
    use_rows([
        _row("C", 9, 1.0, 0.5, _recent(1), trend=[9], as_of=_recent(1)),
    ])
    first = client.get(f"{URL}?market=DAM&window=1d")
    assert first.status_code == 200

    # Break the pool: a genuine second read would 503. A cache hit returns 200.
    main._pool = _BoomPool()
    second = client.get(f"{URL}?market=DAM&window=1d")
    assert second.status_code == 200
    assert second.json() == first.json()


def test_cache_key_separates_market_and_window(client):
    use_rows([
        _row("C", 9, 1.0, 0.5, _recent(1), trend=[9], as_of=_recent(1)),
    ])
    client.get(f"{URL}?market=DAM&window=1d")  # populate (DAM,1d) only

    # A different key must NOT be served from the (DAM,1d) entry. (RTM resolves to
    # RTD, so (RTD,1d) is a distinct key from the populated (DAM,1d).)
    main._pool = _BoomPool()
    assert client.get(f"{URL}?market=RTM&window=1d").status_code == 503
    assert client.get(f"{URL}?market=DAM&window=7d").status_code == 503


# ═══════════════════════════════════════════════════════════════════════════
# Endpoint: market label transition (pantry migration 072) — RTM->RTD alias,
# dual-label SQL, cache-key unification
# ═══════════════════════════════════════════════════════════════════════════

def test_rtm_alias_echoes_resolved_rtd_label(client):
    use_rows([
        _row("C", 9, 1.0, 0.5, _recent(1), trend=[9], as_of=_recent(1)),
    ])
    body = client.get(f"{URL}?market=RTM&window=1d").json()
    # The deprecated alias echoes the CANONICAL resolved label, not the wire token.
    assert body["market"] == "RTD"


def test_endpoint_rtd_queries_both_stored_labels(client):
    # The SQL that actually reaches the DB filters BOTH 'RTD' and 'RTM', so the
    # blotter is correct before AND after migration 072 relabels the rows.
    pool = use_rows([
        _row("C", 9, 1.0, 0.5, _recent(1), trend=[9], as_of=_recent(1)),
    ])
    client.get(f"{URL}?market=RTM&window=1d")
    assert pool.sink["params"]["markets"] == ["RTD", "RTM"]
    assert "market = ANY(%(markets)s)" in pool.sink["query"]


def test_rtm_and_rtd_share_one_cache_entry(client):
    use_rows([
        _row("C", 9, 1.0, 0.5, _recent(1), trend=[9], as_of=_recent(1)),
    ])
    first = client.get(f"{URL}?market=RTD&window=1d")
    assert first.status_code == 200

    # RTM resolves to RTD -> served from the SAME cache entry: a broken pool would
    # 503 on a genuine read, but the cache hit returns 200 with identical bytes.
    main._pool = _BoomPool()
    second = client.get(f"{URL}?market=RTM&window=1d")
    assert second.status_code == 200
    assert second.json() == first.json()
    assert second.json()["market"] == "RTD"
