"""
Tests for the inverse fingerprint endpoint (constraints ranked by one pnode):

  * GET /api/atlas/nodes/fingerprint?pnode_id=<id>&variant=<v>&limit=<n>

The mirror image of /api/atlas/constraints/fingerprint — fix a NODE, rank the
constraints that move it. Two layers, no live database required:

  1. Pure-function unit tests on the param validator
     (`_parse_atlas_node_fingerprint_params`), the per-constraint SQL builder
     (`_build_atlas_node_fingerprint_constraints_query`), and the pnode-existence
     probe SQL constant. The advisory and newest-success ledger SQL are shared
     with the forward endpoint (covered by test_atlas_fingerprint.py) and only
     spot-checked here.

  2. Endpoint tests that swap a tiny in-memory fake pool into `main._pool` (same
     surface as the sibling atlas suites). The endpoint issues up to THREE reads
     on one cursor — `fetchone()` for the newest-success ledger row, `fetchall()`
     for that build's constraint rows, and (ONLY when empty) a second `fetchone()`
     for the pnode-existence probe. The fake cursor serves the ledger from the
     first fetchone, the constraints from fetchall, and the existence probe from
     the second fetchone, recording every execute() so tests can assert
     newest-success scoping and the limit clamp reach the DB.
"""

import datetime
from decimal import Decimal

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import main


UTC = datetime.timezone.utc

URL = "/api/atlas/nodes/fingerprint"


# ---------------------------------------------------------------------------
# In-memory fake pool. One cursor serves all reads: fetchone -> ledger row,
# fetchall -> constraint rows, second fetchone -> pnode-existence probe. Every
# execute() is recorded in `sink`.
# ---------------------------------------------------------------------------

class _FakeCursor:
    def __init__(self, store, sink):
        self._store = store
        self._sink = sink
        self._fetchone_calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, query, params=None):
        self._sink.setdefault("calls", []).append({"query": query, "params": params})
        self._sink["query"] = query
        self._sink["params"] = params

    async def fetchone(self):
        # Two fetchone reads at most, in endpoint order: the first resolves the
        # newest-success ledger row, the second (only when there are no rows) the
        # pnode-existence probe (a truthy row when the pnode exists, else None).
        self._fetchone_calls += 1
        if self._fetchone_calls == 1:
            return self._store["ledger"]
        return {"exists": 1} if self._store.get("pnode_exists") else None

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
    def __init__(self, ledger, rows, pnode_exists=False):
        self._store = {"ledger": ledger, "rows": rows, "pnode_exists": pnode_exists}
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
    main._atlas_node_fingerprint_cache.clear()
    yield
    main._atlas_node_fingerprint_cache.clear()


@pytest.fixture
def client():
    # No `with` block => lifespan does not run => real pool is never opened.
    return TestClient(main.app)


def use(ledger, rows, pnode_exists=False):
    pool = FakePool(ledger, rows, pnode_exists)
    main._pool = pool
    return pool


def _ledger(build_id="fpb_20260712T151820Z_7d", *, source_as_of=None, params=None):
    """A newest-success ledger row as the resolver SELECT yields it (dict_row;
    jsonb columns arrive as parsed Python dicts)."""
    return {
        "build_id": build_id,
        "source_as_of": source_as_of if source_as_of is not None else {
            "constraints_dam_max_ts": "2026-07-11T06:00:00+00:00",
        },
        "row_counts": {},
        "params": params if params is not None else {
            "window_start": "2026-07-06", "window_end": "2026-07-12",
        },
    }


def _row(constraint_id, *, delta, beta=None, sign_consistency="0.5", n_bound=19,
         co_bind_frac="1", landmark_name=None, landmark_slug=None,
         landmark_function_type=None):
    """One constraint row as the per-constraint SELECT yields it (dict_row).
    Numerics arrive as Decimal; the LEFT-JOINed landmark columns are None when the
    constraint sits in no ratified landmark."""
    return {
        "constraint_id": constraint_id,
        "delta": Decimal(str(delta)) if delta is not None else None,
        "beta": Decimal(str(beta)) if beta is not None else None,
        "sign_consistency": (
            Decimal(str(sign_consistency)) if sign_consistency is not None else None
        ),
        "n_bound": n_bound,
        "co_bind_frac": (
            Decimal(str(co_bind_frac)) if co_bind_frac is not None else None
        ),
        "landmark_name": landmark_name,
        "landmark_slug": landmark_slug,
        "landmark_function_type": landmark_function_type,
    }


def _inyokern_row(constraint_id, *, delta):
    """A constraint row carrying the ratified Inyokern Export Corridor landmark —
    the family the CONTROL_7 nodes hang off of."""
    return _row(
        constraint_id, delta=delta,
        landmark_name="Inyokern Export Corridor",
        landmark_slug="inyokern-export-corridor",
        landmark_function_type="corridor",
    )


# ═══════════════════════════════════════════════════════════════════════════
# Pure: param validator
# ═══════════════════════════════════════════════════════════════════════════

def test_parse_params_valid_and_default_limit():
    assert main._parse_atlas_node_fingerprint_params("da_fmm", 20) == ("da_fmm", 20)
    assert main._parse_atlas_node_fingerprint_params("fmm_rtd", 20) == ("fmm_rtd", 20)
    assert (
        main._parse_atlas_node_fingerprint_params("congestion_beta_dam", 50)
        == ("congestion_beta_dam", 50)
    )


def test_parse_params_limit_is_clamped_not_rejected():
    # Over the hard cap (50, NOT the forward endpoint's 200) -> clamped, never 4xx.
    assert main._parse_atlas_node_fingerprint_params("da_fmm", 9999)[1] == 50
    assert main._parse_atlas_node_fingerprint_params("da_fmm", 51)[1] == 50
    # A stray sub-1 value floors at 1 (the Query layer already guards ge=1).
    assert main._parse_atlas_node_fingerprint_params("da_fmm", 0)[1] == 1


def test_parse_params_bad_variant_is_400():
    with pytest.raises(HTTPException) as ei:
        main._parse_atlas_node_fingerprint_params("da_rtd", 20)
    assert ei.value.status_code == 400
    assert "variant" in ei.value.detail


def test_parse_params_variant_is_space_stripped():
    assert main._parse_atlas_node_fingerprint_params("  da_fmm  ", 20)[0] == "da_fmm"


def test_parse_params_shares_the_forward_variant_set():
    # Same four wire tokens as the forward endpoint — no drift.
    for v in main.ATLAS_FP_VARIANTS:
        assert main._parse_atlas_node_fingerprint_params(v, 20)[0] == v


# ═══════════════════════════════════════════════════════════════════════════
# Pure: per-constraint SQL builder + existence probe
# ═══════════════════════════════════════════════════════════════════════════

def test_build_constraints_query_shape_and_gazetteer_join():
    sql, params = main._build_atlas_node_fingerprint_constraints_query(
        "fpb_X", "CONTROL_7_N022", "da_fmm", 20
    )
    # Scoped by resolved build_id + exact pnode_id + variant (the transposed axis:
    # WHERE pnode_id, not constraint_id).
    assert "cf.build_id = %(build_id)s" in sql
    assert "cf.pnode_id = %(pnode_id)s" in sql
    assert "cf.variant = %(variant)s" in sql
    # Gazetteer joined per-row, blotter's DISTINCT ON shape, ratified only.
    assert "DISTINCT ON (lm.entity_id)" in sql
    assert "lm.entity_type = 'constraint'" in sql
    assert "l.status = 'ratified'" in sql
    assert "LEFT JOIN landmarks lk ON lk.entity_id = cf.constraint_id" in sql
    # Per-row landmark object omits blurb (the blotter's shape, not the forward
    # endpoint's top-level landmark).
    assert "l.blurb" not in sql
    # Capped, deterministic tiebreak on constraint_id.
    assert "LIMIT %(limit)s" in sql
    assert "cf.constraint_id ASC" in sql
    assert params == {
        "build_id": "fpb_X", "pnode_id": "CONTROL_7_N022",
        "variant": "da_fmm", "limit": 20,
    }


def test_build_constraints_query_ordering_non_beta_vs_beta():
    non_beta, _ = main._build_atlas_node_fingerprint_constraints_query(
        "b", "p", "da_fmm", 20
    )
    assert "ORDER BY ABS(cf.delta) DESC NULLS LAST" in non_beta
    assert "COALESCE(ABS(cf.delta)" not in non_beta

    beta, _ = main._build_atlas_node_fingerprint_constraints_query(
        "b", "p", "congestion_beta_rtd", 20
    )
    # Beta variants fall back to ABS(beta) when delta is null.
    assert "ORDER BY COALESCE(ABS(cf.delta), ABS(cf.beta)) DESC NULLS LAST" in beta


def test_pnode_exists_sql_probes_corrected_geo():
    sql = main._ATLAS_NODE_FP_PNODE_EXISTS_SQL
    assert "atlas_pnode_geo_corrected" in sql
    assert "pnode_id = %(pnode_id)s" in sql
    assert "LIMIT 1" in sql


# ═══════════════════════════════════════════════════════════════════════════
# Endpoint: happy path (CONTROL_7_N022 -> Inyokern corridor family)
# ═══════════════════════════════════════════════════════════════════════════

def test_happy_path_control_7_n022(client):
    # A node cratering on the DART layer: the Inyokern corridor family moving it,
    # with NEGATIVE deltas (the node loses on export binding) and the landmark.
    use(
        _ledger(source_as_of={"constraints_dam_max_ts": "2026-07-11T06:00:00+00:00"}),
        [
            _inyokern_row("7690-CONTRL-INYOKN_EXP_NG", delta="-286.2710988790624101"),
            _inyokern_row(
                "OMS20107865-CONTRL-INYOKN_EXP_NG", delta="-281.291351740724762"
            ),
        ],
    )
    resp = client.get(f"{URL}?pnode_id=CONTROL_7_N022&variant=da_fmm")
    assert resp.status_code == 200
    body = resp.json()

    assert set(body) == {
        "pnode_id", "variant", "build_id", "window_start", "window_end",
        "as_of", "advisory", "constraints",
    }
    assert body["pnode_id"] == "CONTROL_7_N022"
    assert body["variant"] == "da_fmm"
    assert body["build_id"] == "fpb_20260712T151820Z_7d"
    assert body["window_start"] == "2026-07-06"
    assert body["window_end"] == "2026-07-12"
    assert body["as_of"] == {"constraints_dam_max_ts": "2026-07-11T06:00:00+00:00"}

    # co_bind_frac_avg = 1.0 (> 0.8) -> advisory note present (shared computation).
    assert body["advisory"]["co_bind_frac_avg"] == 1.0
    assert "not deconfounded" in body["advisory"]["note"]

    c0 = body["constraints"][0]
    assert set(c0) == {
        "constraint_id", "landmark", "delta", "beta", "sign_consistency",
        "n_bound", "co_bind_frac",
    }
    assert c0["constraint_id"] == "7690-CONTRL-INYOKN_EXP_NG"
    assert c0["delta"] < 0  # the node craters on this constraint (spec's check)
    assert c0["beta"] is None
    assert c0["n_bound"] == 19
    # The per-row landmark object (name/slug/function_type, NO blurb).
    assert c0["landmark"] == {
        "name": "Inyokern Export Corridor",
        "slug": "inyokern-export-corridor",
        "function_type": "corridor",
    }


def test_default_variant_is_da_fmm(client):
    use(_ledger(), [_row("C1", delta="-10")])
    body = client.get(f"{URL}?pnode_id=CONTROL_7_N022").json()
    assert body["variant"] == "da_fmm"


def test_unnamed_constraint_landmark_is_null(client):
    # A constraint in no ratified landmark -> the LEFT JOIN yields null columns ->
    # per-row landmark is null (present key, null value).
    use(_ledger(), [_row("99999_NOWHERE_BR_1 _1", delta="-5")])
    body = client.get(f"{URL}?pnode_id=CONTROL_7_N022&variant=da_fmm").json()
    assert len(body["constraints"]) == 1
    assert "landmark" in body["constraints"][0]
    assert body["constraints"][0]["landmark"] is None


# ═══════════════════════════════════════════════════════════════════════════
# Endpoint: variant validation, limit clamp, newest-success scoping
# ═══════════════════════════════════════════════════════════════════════════

def test_unknown_variant_is_400(client):
    use(_ledger(), [])
    resp = client.get(f"{URL}?pnode_id=CONTROL_7_N022&variant=rtm_da")
    assert resp.status_code == 400


def test_missing_pnode_id_is_422(client):
    # pnode_id is a required query param -> FastAPI 422 when absent.
    use(_ledger(), [])
    assert client.get(f"{URL}?variant=da_fmm").status_code == 422


def test_limit_is_clamped_to_50_at_the_db(client):
    pool = use(_ledger(), [_row("C1", delta="-1")])
    client.get(f"{URL}?pnode_id=CONTROL_7_N022&variant=da_fmm&limit=9999")
    # The constraint query must carry the clamped limit (50, not 200).
    node_calls = [
        c for c in pool.sink["calls"] if "constraint_fingerprints" in c["query"]
    ]
    assert node_calls[-1]["params"]["limit"] == 50


def test_newest_success_scoping_uses_ledger_build_id(client):
    # The ledger SELECT filters status='success' (a newer FAILED build is ignored),
    # and the constraint query is scoped to exactly the build_id it resolved.
    pool = use(_ledger(build_id="fpb_NEWEST_SUCCESS"), [_row("C1", delta="-1")])
    client.get(f"{URL}?pnode_id=CONTROL_7_N022&variant=da_fmm")

    ledger_call = pool.sink["calls"][0]
    assert "status = 'success'" in ledger_call["query"]

    node_calls = [
        c for c in pool.sink["calls"] if "constraint_fingerprints" in c["query"]
    ]
    assert node_calls[-1]["params"]["build_id"] == "fpb_NEWEST_SUCCESS"


def test_beta_variant_orders_by_beta_fallback_at_db(client):
    pool = use(_ledger(), [_row("C1", delta=None, beta="2.5")])
    client.get(f"{URL}?pnode_id=CONTROL_7_N022&variant=congestion_beta_rtd")
    node_calls = [
        c for c in pool.sink["calls"] if "constraint_fingerprints" in c["query"]
    ]
    assert "COALESCE(ABS(cf.delta), ABS(cf.beta))" in node_calls[-1]["query"]


# ═══════════════════════════════════════════════════════════════════════════
# Endpoint: empty handling (node_not_in_build vs unknown_pnode)
# ═══════════════════════════════════════════════════════════════════════════

def test_empty_known_node_is_node_not_in_build(client):
    # A real node (present in corrected geo) that no constraint moved this build:
    # a legitimate 200 with node_not_in_build — never a bare empty array.
    use(_ledger(), [], pnode_exists=True)
    resp = client.get(f"{URL}?pnode_id=CONTROL_7_N022&variant=da_fmm")
    assert resp.status_code == 200
    body = resp.json()
    assert body["constraints"] == []
    assert body["reason"] == "node_not_in_build"
    # Envelope fields still present; advisory degrades to a null average.
    assert body["build_id"] == "fpb_20260712T151820Z_7d"
    assert body["advisory"] == {"co_bind_frac_avg": None}


def test_empty_unknown_node_is_unknown_pnode(client):
    # An id in no corrected-geo row at all -> unknown_pnode (likely a typo).
    use(_ledger(), [], pnode_exists=False)
    body = client.get(f"{URL}?pnode_id=NOT_A_REAL_NODE&variant=da_fmm").json()
    assert body["constraints"] == []
    assert body["reason"] == "unknown_pnode"


def test_empty_probes_geo_only_when_no_rows(client):
    # The existence probe is on the COLD path only: a non-empty result must NOT
    # touch atlas_pnode_geo_corrected (no wasted read on the hot path).
    pool = use(_ledger(), [_row("C1", delta="-1")], pnode_exists=True)
    client.get(f"{URL}?pnode_id=CONTROL_7_N022&variant=da_fmm")
    geo_calls = [
        c for c in pool.sink["calls"] if "atlas_pnode_geo_corrected" in c["query"]
    ]
    assert geo_calls == []


# ═══════════════════════════════════════════════════════════════════════════
# Endpoint: error contract
# ═══════════════════════════════════════════════════════════════════════════

def test_db_unavailable_is_503(client):
    main._pool = _BoomPool()
    resp = client.get(f"{URL}?pnode_id=CONTROL_7_N022&variant=da_fmm")
    assert resp.status_code == 503


def test_no_successful_build_is_503(client):
    # Ledger resolves to nothing (no success row) -> 503, not a fake payload.
    use(None, [])
    resp = client.get(f"{URL}?pnode_id=CONTROL_7_N022&variant=da_fmm")
    assert resp.status_code == 503


# ═══════════════════════════════════════════════════════════════════════════
# Endpoint: in-process cache (5-min TTL, keyed (pnode_id, variant, limit))
# ═══════════════════════════════════════════════════════════════════════════

def test_cache_serves_hit_without_touching_db(client):
    use(_ledger(), [_row("C1", delta="-1")])
    first = client.get(f"{URL}?pnode_id=CONTROL_7_N022&variant=da_fmm")
    assert first.status_code == 200

    # Break the pool: a genuine second read would 503; a cache hit returns 200.
    main._pool = _BoomPool()
    second = client.get(f"{URL}?pnode_id=CONTROL_7_N022&variant=da_fmm")
    assert second.status_code == 200
    assert second.json() == first.json()


def test_cache_key_separates_variant_and_limit(client):
    use(_ledger(), [_row("C1", delta="-1")])
    client.get(f"{URL}?pnode_id=CONTROL_7_N022&variant=da_fmm&limit=20")  # populate

    # Different variant and different limit are distinct keys -> genuine reads.
    main._pool = _BoomPool()
    assert client.get(
        f"{URL}?pnode_id=CONTROL_7_N022&variant=fmm_rtd&limit=20"
    ).status_code == 503
    assert client.get(
        f"{URL}?pnode_id=CONTROL_7_N022&variant=da_fmm&limit=10"
    ).status_code == 503
