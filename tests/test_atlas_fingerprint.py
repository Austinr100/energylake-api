"""
Tests for the per-node binding-constraint fingerprint endpoint:

  * GET /api/atlas/constraints/fingerprint?constraint_id=<id>&variant=<v>&limit=<n>

Two layers, no live database required:

  1. Pure-function unit tests on the param validator
     (`_parse_atlas_fingerprint_params`), the per-node SQL builder
     (`_build_atlas_fingerprint_nodes_query`), the newest-success ledger SQL
     constant, the honest empty-reason resolver (`_atlas_fingerprint_reason`),
     and the co-binding advisory (`_atlas_fingerprint_advisory`). These carry the
     validation + SQL-shape + reason + advisory contract without touching Postgres.

  2. Endpoint tests that swap a tiny in-memory fake pool into `main._pool` (same
     surface as the sibling atlas suites). Because this endpoint issues TWO reads
     on one cursor — first `fetchone()` for the newest-success ledger row, then
     `fetchall()` for that build's node rows — the fake cursor serves the ledger
     from fetchone and the nodes from fetchall, and records every execute() so the
     tests can assert newest-success scoping and the limit clamp reach the DB.
"""

import datetime
from decimal import Decimal

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import main


UTC = datetime.timezone.utc

URL = "/api/atlas/constraints/fingerprint"


# ---------------------------------------------------------------------------
# In-memory fake pool. One cursor serves both reads: fetchone -> ledger row,
# fetchall -> node rows. Every execute() is recorded in `sink`.
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
        return list(self._store["nodes"])


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
    def __init__(self, ledger, nodes):
        self._store = {"ledger": ledger, "nodes": nodes}
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
    # The in-process cache is module-global; isolate every test.
    main._atlas_fingerprint_cache.clear()
    yield
    main._atlas_fingerprint_cache.clear()


@pytest.fixture
def client():
    # No `with` block => lifespan does not run => real pool is never opened.
    return TestClient(main.app)


def use(ledger, nodes):
    pool = FakePool(ledger, nodes)
    main._pool = pool
    return pool


def _ledger(build_id="fpb_20260712T151820Z_7d", *, source_as_of=None,
            row_counts=None, params=None):
    """A newest-success ledger row as the resolver SELECT yields it (dict_row;
    jsonb columns arrive as parsed Python dicts)."""
    return {
        "build_id": build_id,
        "source_as_of": source_as_of if source_as_of is not None else {
            "constraints_dam_max_ts": "2026-07-11T06:00:00+00:00",
        },
        "row_counts": row_counts if row_counts is not None else {},
        "params": params if params is not None else {
            "window_start": "2026-07-06", "window_end": "2026-07-12",
        },
    }


def _node(pnode_id, *, delta, beta=None, sign_consistency="0.5", n_bound=19,
          co_bind_frac="1", lat=37.34, lon=-118.47):
    """One node row as the per-node SELECT yields it (dict_row). Numerics arrive
    as Decimal; coordinates as float/None."""
    return {
        "pnode_id": pnode_id,
        "delta": Decimal(str(delta)) if delta is not None else None,
        "beta": Decimal(str(beta)) if beta is not None else None,
        "sign_consistency": (
            Decimal(str(sign_consistency)) if sign_consistency is not None else None
        ),
        "n_bound": n_bound,
        "co_bind_frac": (
            Decimal(str(co_bind_frac)) if co_bind_frac is not None else None
        ),
        "lat": lat,
        "lon": lon,
    }


# A real demoted fixture from this build's ledger (da_fmm), per the spec.
_DEMOTED_ID = "34002_SALADO  _60.0_34008_STNSLSRP_60.0_BR_1 _1"
_ROW_COUNTS = {
    "by_variant": {
        "da_fmm": {
            "rows": 216255,
            "written": 15,
            "degenerate": False,
            "skipped_below_floor": 24,
            "skipped_insufficient_overlap": 13,
            "demoted": [
                {"bound_hours": 36, "joined_hours": 11, "constraint_id": _DEMOTED_ID},
            ],
        },
        "congestion_beta_rtd": {
            "rows": 0,
            "written": 0,
            "degenerate": True,
            "demoted": [],
        },
    },
    "degenerate_variants": ["congestion_beta_rtd"],
}


# ═══════════════════════════════════════════════════════════════════════════
# Pure: param validator
# ═══════════════════════════════════════════════════════════════════════════

def test_parse_params_valid_and_default_limit():
    assert main._parse_atlas_fingerprint_params("da_fmm", 50) == ("da_fmm", 50)
    assert main._parse_atlas_fingerprint_params("fmm_rtd", 50) == ("fmm_rtd", 50)
    assert (
        main._parse_atlas_fingerprint_params("congestion_beta_dam", 200)
        == ("congestion_beta_dam", 200)
    )


def test_parse_params_limit_is_clamped_not_rejected():
    # Over the hard cap -> clamped down to 200 (never a 4xx).
    assert main._parse_atlas_fingerprint_params("da_fmm", 9999)[1] == 200
    assert main._parse_atlas_fingerprint_params("da_fmm", 201)[1] == 200
    # A stray sub-1 value floors at 1 (the Query layer already guards ge=1).
    assert main._parse_atlas_fingerprint_params("da_fmm", 0)[1] == 1


def test_parse_params_bad_variant_is_400():
    with pytest.raises(HTTPException) as ei:
        main._parse_atlas_fingerprint_params("da_rtd", 50)
    assert ei.value.status_code == 400
    assert "variant" in ei.value.detail


def test_parse_params_variant_is_space_stripped():
    assert main._parse_atlas_fingerprint_params("  da_fmm  ", 50)[0] == "da_fmm"


# ═══════════════════════════════════════════════════════════════════════════
# Pure: newest-success ledger SQL + per-node SQL builder
# ═══════════════════════════════════════════════════════════════════════════

def test_latest_success_sql_scopes_to_newest_success():
    sql = main._ATLAS_FP_LATEST_SUCCESS_SQL
    # The consumer contract: only successful builds, newest first, exactly one.
    assert "status = 'success'" in sql
    assert "ORDER BY started_at DESC" in sql
    assert "LIMIT 1" in sql
    assert "fingerprint_builds" in sql


def test_build_nodes_query_shape_and_geo_coalesce():
    sql, params = main._build_atlas_fingerprint_nodes_query(
        "fpb_X", "31336_HPLND JT", "da_fmm", 50
    )
    # Scoped by resolved build_id + exact constraint_id + variant.
    assert "cf.build_id = %(build_id)s" in sql
    assert "cf.constraint_id = %(constraint_id)s" in sql
    assert "cf.variant = %(variant)s" in sql
    # Coordinates: the corrected table, mandatory COALESCE, LEFT JOIN (nulls kept).
    assert "LEFT JOIN atlas_pnode_geo_corrected" in sql
    assert "COALESCE(g.corrected_lat, g.latitude)" in sql
    assert "COALESCE(g.corrected_lon, g.longitude)" in sql
    assert "atlas_pnode_geo " not in sql.replace("atlas_pnode_geo_corrected", "")
    # Capped, deterministic tiebreak.
    assert "LIMIT %(limit)s" in sql
    assert "cf.pnode_id ASC" in sql
    assert params == {
        "build_id": "fpb_X", "constraint_id": "31336_HPLND JT",
        "variant": "da_fmm", "limit": 50,
    }


def test_build_nodes_query_ordering_non_beta_vs_beta():
    non_beta, _ = main._build_atlas_fingerprint_nodes_query("b", "c", "da_fmm", 50)
    assert "ORDER BY ABS(cf.delta) DESC NULLS LAST" in non_beta
    assert "COALESCE(ABS(cf.delta)" not in non_beta

    beta, _ = main._build_atlas_fingerprint_nodes_query(
        "b", "c", "congestion_beta_rtd", 50
    )
    # Beta variants fall back to ABS(beta) when delta is null.
    assert "ORDER BY COALESCE(ABS(cf.delta), ABS(cf.beta)) DESC NULLS LAST" in beta


# ═══════════════════════════════════════════════════════════════════════════
# Pure: empty-reason resolver
# ═══════════════════════════════════════════════════════════════════════════

def test_reason_insufficient_overlap_carries_numbers():
    out = main._atlas_fingerprint_reason(_ROW_COUNTS, "da_fmm", _DEMOTED_ID)
    assert out == {
        "reason": "insufficient_overlap", "bound_hours": 36, "joined_hours": 11,
    }


def test_reason_variant_degenerate():
    out = main._atlas_fingerprint_reason(_ROW_COUNTS, "congestion_beta_rtd", "anything")
    assert out == {"reason": "variant_degenerate"}


def test_reason_not_in_window_is_the_catch_all():
    out = main._atlas_fingerprint_reason(_ROW_COUNTS, "da_fmm", "99999_NOWHERE")
    assert out == {"reason": "not_in_window"}


def test_reason_below_floor_when_ledger_enumerates_ids():
    # Defensive: if a build lists below-floor ids (not just a scalar count),
    # honor below_floor. Usual builds carry a count only -> not_in_window.
    rc = {"by_variant": {"da_fmm": {"skipped_below_floor": ["7430_CP6_NG"]}}}
    assert main._atlas_fingerprint_reason(rc, "da_fmm", "7430_CP6_NG") == {
        "reason": "below_floor"
    }


def test_reason_handles_empty_row_counts():
    assert main._atlas_fingerprint_reason(None, "da_fmm", "x") == {
        "reason": "not_in_window"
    }
    assert main._atlas_fingerprint_reason({}, "da_fmm", "x") == {
        "reason": "not_in_window"
    }


# ═══════════════════════════════════════════════════════════════════════════
# Pure: co-binding advisory
# ═══════════════════════════════════════════════════════════════════════════

def test_advisory_note_trips_above_threshold():
    out = main._atlas_fingerprint_advisory([Decimal("1"), Decimal("0.9")])
    assert out["co_bind_frac_avg"] == 0.95
    assert "not deconfounded" in out["note"]


def test_advisory_no_note_at_or_below_threshold():
    out = main._atlas_fingerprint_advisory([Decimal("0.8"), Decimal("0.8")])
    assert out["co_bind_frac_avg"] == 0.8
    assert "note" not in out


def test_advisory_empty_has_null_avg_and_no_note():
    out = main._atlas_fingerprint_advisory([])
    assert out == {"co_bind_frac_avg": None}


# ═══════════════════════════════════════════════════════════════════════════
# Endpoint: happy path (Hopland)
# ═══════════════════════════════════════════════════════════════════════════

def test_happy_path_hopland(client):
    hplnd = "31336_HPLND JT_60.0_31370_CLVRDLJT_60.0_BR_1 _1"
    use(
        _ledger(source_as_of={"constraints_dam_max_ts": "2026-07-11T06:00:00+00:00"}),
        [
            _node("CONTROLX_1_N003", delta="286.2710988790624101",
                  sign_consistency="0.52631578947368421053", co_bind_frac="1",
                  lat=37.342760774939634, lon=-118.4719311923788),
            _node("CONTROL_7_N025", delta="281.29135174072476271475",
                  sign_consistency="0.52631578947368421053", co_bind_frac="1",
                  lat=37.33551332206491, lon=-118.48085),
        ],
    )
    resp = client.get(f"{URL}?constraint_id={hplnd}&variant=da_fmm")
    assert resp.status_code == 200
    body = resp.json()

    assert set(body) == {
        "constraint_id", "variant", "build_id", "window_start", "window_end",
        "as_of", "advisory", "nodes",
    }
    assert body["constraint_id"] == hplnd
    assert body["variant"] == "da_fmm"
    assert body["build_id"] == "fpb_20260712T151820Z_7d"
    assert body["window_start"] == "2026-07-06"
    assert body["window_end"] == "2026-07-12"
    assert body["as_of"] == {"constraints_dam_max_ts": "2026-07-11T06:00:00+00:00"}

    # co_bind_frac_avg = 1.0 (> 0.8) -> advisory note present.
    assert body["advisory"]["co_bind_frac_avg"] == 1.0
    assert "not deconfounded" in body["advisory"]["note"]

    n0 = body["nodes"][0]
    assert set(n0) == {
        "pnode_id", "lat", "lon", "delta", "beta", "sign_consistency",
        "n_bound", "co_bind_frac",
    }
    # CONTROLX/CONTROL_7-class nodes with positive deltas (spec's Hopland check).
    assert n0["pnode_id"] == "CONTROLX_1_N003"
    assert n0["delta"] > 0
    assert n0["lat"] == 37.342761  # 6dp round, effective (corrected) coord
    assert n0["beta"] is None
    assert n0["n_bound"] == 19


def test_default_variant_is_da_fmm(client):
    use(_ledger(), [_node("N1", delta="10")])
    body = client.get(f"{URL}?constraint_id=31336_HPLND").json()
    assert body["variant"] == "da_fmm"


def test_null_coords_node_is_kept_not_dropped(client):
    # A node with no geo row survives the LEFT JOIN with null lat/lon.
    use(_ledger(), [_node("ORPHAN_NODE", delta="5", lat=None, lon=None)])
    body = client.get(f"{URL}?constraint_id=C&variant=da_fmm").json()
    assert len(body["nodes"]) == 1
    assert body["nodes"][0]["pnode_id"] == "ORPHAN_NODE"
    assert body["nodes"][0]["lat"] is None
    assert body["nodes"][0]["lon"] is None


# ═══════════════════════════════════════════════════════════════════════════
# Endpoint: variant validation, limit clamp, newest-success scoping
# ═══════════════════════════════════════════════════════════════════════════

def test_unknown_variant_is_400(client):
    use(_ledger(), [])
    resp = client.get(f"{URL}?constraint_id=C&variant=rtm_da")
    assert resp.status_code == 400


def test_missing_constraint_id_is_422(client):
    # constraint_id is a required query param -> FastAPI 422 when absent.
    use(_ledger(), [])
    assert client.get(f"{URL}?variant=da_fmm").status_code == 422


def test_limit_is_clamped_to_200_at_the_db(client):
    pool = use(_ledger(), [_node("N1", delta="1")])
    client.get(f"{URL}?constraint_id=C&variant=da_fmm&limit=9999")
    # The node query (second execute) must carry the clamped limit.
    node_calls = [
        c for c in pool.sink["calls"] if "constraint_fingerprints" in c["query"]
    ]
    assert node_calls[-1]["params"]["limit"] == 200


def test_newest_success_scoping_uses_ledger_build_id(client):
    # The ledger SELECT filters status='success' (a newer FAILED build is ignored),
    # and the node query is scoped to exactly the build_id it resolved.
    pool = use(_ledger(build_id="fpb_NEWEST_SUCCESS"), [_node("N1", delta="1")])
    client.get(f"{URL}?constraint_id=C&variant=da_fmm")

    ledger_call = pool.sink["calls"][0]
    assert "status = 'success'" in ledger_call["query"]

    node_calls = [
        c for c in pool.sink["calls"] if "constraint_fingerprints" in c["query"]
    ]
    assert node_calls[-1]["params"]["build_id"] == "fpb_NEWEST_SUCCESS"


def test_beta_variant_orders_by_beta_fallback_at_db(client):
    pool = use(_ledger(), [_node("N1", delta=None, beta="2.5")])
    client.get(f"{URL}?constraint_id=C&variant=congestion_beta_rtd")
    node_calls = [
        c for c in pool.sink["calls"] if "constraint_fingerprints" in c["query"]
    ]
    assert "COALESCE(ABS(cf.delta), ABS(cf.beta))" in node_calls[-1]["query"]


# ═══════════════════════════════════════════════════════════════════════════
# Endpoint: empty-with-reason (honesty feature)
# ═══════════════════════════════════════════════════════════════════════════

def test_empty_with_reason_insufficient_overlap(client):
    # The demoted da_fmm fixture: no rows, but a 200 with an honest reason and the
    # demoted constraint's bound_hours/joined_hours — never a bare empty array.
    use(_ledger(row_counts=_ROW_COUNTS), [])
    resp = client.get(f"{URL}?constraint_id={_DEMOTED_ID}&variant=da_fmm")
    assert resp.status_code == 200
    body = resp.json()
    assert body["nodes"] == []
    assert body["reason"] == "insufficient_overlap"
    assert body["bound_hours"] == 36
    assert body["joined_hours"] == 11
    # Envelope fields still present; advisory degrades to a null average.
    assert body["build_id"] == "fpb_20260712T151820Z_7d"
    assert body["advisory"] == {"co_bind_frac_avg": None}


def test_empty_with_reason_variant_degenerate(client):
    use(_ledger(row_counts=_ROW_COUNTS), [])
    body = client.get(f"{URL}?constraint_id=C&variant=congestion_beta_rtd").json()
    assert body["nodes"] == []
    assert body["reason"] == "variant_degenerate"


def test_empty_with_reason_not_in_window(client):
    use(_ledger(row_counts=_ROW_COUNTS), [])
    body = client.get(f"{URL}?constraint_id=99999_NOWHERE&variant=da_fmm").json()
    assert body["nodes"] == []
    assert body["reason"] == "not_in_window"


# ═══════════════════════════════════════════════════════════════════════════
# Endpoint: error contract
# ═══════════════════════════════════════════════════════════════════════════

def test_db_unavailable_is_503(client):
    main._pool = _BoomPool()
    resp = client.get(f"{URL}?constraint_id=C&variant=da_fmm")
    assert resp.status_code == 503


def test_no_successful_build_is_503(client):
    # Ledger resolves to nothing (no success row) -> 503, not a fake payload.
    use(None, [])
    resp = client.get(f"{URL}?constraint_id=C&variant=da_fmm")
    assert resp.status_code == 503


# ═══════════════════════════════════════════════════════════════════════════
# Endpoint: in-process cache (5-min TTL, keyed (constraint_id, variant, limit))
# ═══════════════════════════════════════════════════════════════════════════

def test_cache_serves_hit_without_touching_db(client):
    use(_ledger(), [_node("N1", delta="1")])
    first = client.get(f"{URL}?constraint_id=C&variant=da_fmm")
    assert first.status_code == 200

    # Break the pool: a genuine second read would 503; a cache hit returns 200.
    main._pool = _BoomPool()
    second = client.get(f"{URL}?constraint_id=C&variant=da_fmm")
    assert second.status_code == 200
    assert second.json() == first.json()


def test_cache_key_separates_variant_and_limit(client):
    use(_ledger(), [_node("N1", delta="1")])
    client.get(f"{URL}?constraint_id=C&variant=da_fmm&limit=50")  # populate one key

    # Different variant and different limit are distinct keys -> genuine reads.
    main._pool = _BoomPool()
    assert client.get(f"{URL}?constraint_id=C&variant=fmm_rtd&limit=50").status_code == 503
    assert client.get(f"{URL}?constraint_id=C&variant=da_fmm&limit=25").status_code == 503
