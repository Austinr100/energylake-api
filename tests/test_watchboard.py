"""
Tests for the Cockpit / Watchboard v0 batch endpoint (PR-1):

  * POST /api/watchboard

No live database is required. A query-aware in-memory fake pool is swapped into
`main._pool`; it emulates the two batched reads the endpoint issues —
  * snapshot: pnode_id = ANY(...) + market + the P2 sentinel floor
    (market_date >= 2020-01-01), sorted by (pnode, date, hour, interval);
  * hub: timeseries_values over (dataset, series) since a lower bound —
so the per-view shaping, per-tile isolation, and the P5 UTC alignment are
exercised end-to-end without a real Neon connection. `main._utcnow` (the
injectable clock seam) is pinned per test so `as_of` / `lag_minutes` are
deterministic and the hub lower-bound window is stable.

Live receipts these fixtures mirror (verified against Neon 2026-07-21):
  RTPD BLUELAKE_7_GN001 HE12 int4 -> PT 11:45 -> 2026-07-21T18:45Z; node
  lmp 36.92829, hub SP15 FMM 11.25682, basis 25.67147.
"""

import datetime

import psycopg
import pytest
from fastapi.testclient import TestClient

import main

UTC = datetime.timezone.utc
DAY = datetime.date(2026, 7, 21)
# Pinned clock: 2026-07-21T19:00Z. Latest RT interval (18:45Z) -> lag 15 min.
NOW = datetime.datetime(2026, 7, 21, 19, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Query-aware fake pool.
# ---------------------------------------------------------------------------

class _FakeCursor:
    def __init__(self, scen):
        self._s = scen
        self._result = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, query, params=None):
        self._s["calls"].append({"query": query, "params": params})
        if "timeseries_values" in query:
            # Simulate a dropped connection on the hub read (per-attempt).
            if self._s["fail_hub"] > 0:
                self._s["fail_hub"] -= 1
                raise self._s["exc"]
            datasets = set(params["datasets"])
            series = set(params["series"])
            lo = params["lo"]
            self._result = [
                r for r in self._s["hub_rows"]
                if r["dataset"] in datasets and r["series"] in series and r["ts"] >= lo
            ]
        elif "atlas_pnode_lmp_snapshot" in query:
            m = params["market"]
            # Simulate a dropped connection on this market's snapshot read.
            if self._s["fail_snapshot"].get(m, 0) > 0:
                self._s["fail_snapshot"][m] -= 1
                raise self._s["exc"]
            pnodes = set(params["pnodes"])
            floor = datetime.date.fromisoformat(params["sentinel"])
            rows = [
                r for r in self._s["snap_rows"]
                if r["market"] == m and r["pnode_id"] in pnodes and r["market_date"] >= floor
            ]
            rows.sort(key=lambda r: (
                r["pnode_id"], r["market_date"], r["market_hour"], r["market_interval"]
            ))
            self._result = rows
        else:
            self._result = []

    async def fetchall(self):
        return list(self._result)


class _FakeConn:
    def __init__(self, scen):
        self._s = scen

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def cursor(self):
        return _FakeCursor(self._s)


class FakePool:
    def __init__(self, scen):
        self._s = scen
        self.scen = scen

    def connection(self):
        return _FakeConn(self._s)


@pytest.fixture(autouse=True)
def _restore_clock():
    """Pin/restore the injectable clock so as_of/lag are deterministic and the
    pinned NOW never leaks into sibling suites."""
    saved = main._utcnow
    main._utcnow = lambda: NOW
    yield
    main._utcnow = saved


@pytest.fixture
def client():
    return TestClient(main.app)


def use_scenario(snap_rows=None, hub_rows=None, now=None,
                 fail_snapshot=None, fail_hub=0, exc=None):
    if now is not None:
        main._utcnow = lambda: now
    scen = {
        "snap_rows": snap_rows or [],
        "hub_rows": hub_rows or [],
        # Per-source dropped-connection simulation (attempts to fail, per source).
        "fail_snapshot": dict(fail_snapshot or {}),
        "fail_hub": fail_hub,
        "exc": exc or psycopg.OperationalError("SSL connection has been closed unexpectedly"),
        "calls": [],
    }
    pool = FakePool(scen)
    main._pool = pool
    return pool


def snap(pnode, market, mh, mi, lmp, energy=0.0, congestion=0.0, loss=0.0, ghg=0.0,
         md=DAY):
    return {
        "pnode_id": pnode, "market": market, "market_date": md,
        "market_hour": mh, "market_interval": mi,
        "lmp": lmp, "energy": energy, "congestion": congestion,
        "loss": loss, "ghg": ghg,
        "feed_generated_at": datetime.datetime(2026, 7, 21, 18, 46, tzinfo=UTC),
        "snapshot_vintage": datetime.datetime(2026, 7, 21, 18, 46, tzinfo=UTC),
    }


def hub(dataset, series, ts, value):
    return {"dataset": dataset, "series": series, "ts": ts, "value": value}


def post(client, tiles):
    return client.post("/api/watchboard", json={"tiles": tiles})


# Node BLUELAKE with real-mirroring numbers across all four markets/views.
PNODE = "BLUELAKE_7_GN001"


def _full_scenario():
    snap_rows = [
        # RTD 5-min: int8/9/10 -> 18:35 / 18:40 / 18:45Z
        snap(PNODE, "RTD", 12, 8, 40.0, energy=39.0, congestion=1.0, loss=0.1, ghg=0.2),
        snap(PNODE, "RTD", 12, 9, 41.0, energy=39.5, congestion=1.2, loss=0.1, ghg=0.2),
        snap(PNODE, "RTD", 12, 10, 42.0, energy=40.0, congestion=1.5, loss=0.2, ghg=0.3),
        # A P2 sentinel row (market_date 0001-01-01) — MUST be excluded.
        snap(PNODE, "RTD", 1, 1, 999.0, md=datetime.date(1, 1, 1)),
        # RTPD 15-min: int3/4 -> 18:30 / 18:45Z
        snap(PNODE, "RTPD", 12, 3, 30.0),
        snap(PNODE, "RTPD", 12, 4, 36.92829),
        # DAM hourly: HE11/HE12 -> 17:00 / 18:00Z
        snap(PNODE, "DAM", 11, 0, 48.0),
        snap(PNODE, "DAM", 12, 0, 50.0),
    ]
    hub_rows = [
        hub("caiso_lmp_rt_15min", "SP15", datetime.datetime(2026, 7, 21, 18, 30, tzinfo=UTC), 10.0),
        hub("caiso_lmp_rt_15min", "SP15", datetime.datetime(2026, 7, 21, 18, 45, tzinfo=UTC), 11.25682),
        hub("caiso_lmp_da_hourly", "SP15", datetime.datetime(2026, 7, 21, 17, 0, tzinfo=UTC), 45.0),
        hub("caiso_lmp_da_hourly", "SP15", datetime.datetime(2026, 7, 21, 18, 0, tzinfo=UTC), 46.0),
    ]
    return snap_rows, hub_rows


# ---------------------------------------------------------------------------
# Acceptance #1 — a 4-tile board, one per view, all healthy.
# ---------------------------------------------------------------------------

def test_four_view_board(client):
    snap_rows, hub_rows = _full_scenario()
    use_scenario(snap_rows, hub_rows)
    resp = post(client, [
        {"tile_id": "lmp1", "tile_type": "pnode", "pnode_id": PNODE, "market": "RTD", "view": "lmp"},
        {"tile_id": "cmp1", "tile_type": "pnode", "pnode_id": PNODE, "market": "RTD", "view": "components"},
        {"tile_id": "drt1", "tile_type": "pnode", "pnode_id": PNODE, "market": "RTPD", "view": "dart"},
        {"tile_id": "bas1", "tile_type": "pnode", "pnode_id": PNODE, "market": "RTPD", "view": "basis"},
    ])
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 4
    tiles = body["tiles"]
    assert set(tiles) == {"lmp1", "cmp1", "drt1", "bas1"}
    # Every healthy tile carries as_of + lag_minutes and is not empty.
    for tid in tiles:
        assert "error" not in tiles[tid]
        assert tiles[tid]["as_of"] is not None
        assert tiles[tid]["lag_minutes"] is not None
        assert tiles[tid]["empty"] is False

    # lmp: latest at 18:45Z (int10), lag 15 min from pinned NOW.
    lmp = tiles["lmp1"]
    assert lmp["as_of"] == "2026-07-21T18:45:00+00:00"
    assert lmp["lag_minutes"] == 15
    assert lmp["latest"] == {"lmp": 42.0, "energy": 40.0, "congestion": 1.5, "loss": 0.2, "ghg": 0.3}
    assert [p["lmp"] for p in lmp["sparkline"]] == [40.0, 41.0, 42.0]
    assert lmp["sparkline"][-1]["ts_utc"] == "2026-07-21T18:45:00+00:00"

    # components: latest four components + congestion sparkline (no lmp key).
    cmp = tiles["cmp1"]
    assert cmp["latest"] == {"energy": 40.0, "congestion": 1.5, "loss": 0.2, "ghg": 0.3}
    assert [p["congestion"] for p in cmp["congestion_sparkline"]] == [1.0, 1.2, 1.5]


# ---------------------------------------------------------------------------
# Acceptance #1 — basis hand-verified for one aligned interval (PT->UTC shown).
# ---------------------------------------------------------------------------

def test_basis_hand_check(client):
    snap_rows, hub_rows = _full_scenario()
    use_scenario(snap_rows, hub_rows)
    resp = post(client, [
        {"tile_id": "b", "tile_type": "pnode", "pnode_id": PNODE, "market": "RTPD",
         "view": "basis", "hub_ref": "SP15"},
    ])
    tile = resp.json()["tiles"]["b"]
    assert tile["hub_ref"] == "SP15"
    # Aligned interval: RTPD HE12 int4 -> PT 11:45 -> 18:45Z.
    latest = tile["latest"]
    assert latest["ts_utc"] == "2026-07-21T18:45:00+00:00"
    assert latest["node"] == 36.92829
    assert latest["hub"] == 11.25682
    # basis = node - hub = 36.92829 - 11.25682 = 25.67147
    assert latest["basis"] == pytest.approx(25.67147)
    # The run carries both aligned intervals (18:30 and 18:45).
    assert [e["ts_utc"] for e in tile["run"]] == [
        "2026-07-21T18:30:00+00:00", "2026-07-21T18:45:00+00:00",
    ]
    assert tile["run"][0]["basis"] == 20.0  # 30.0 - 10.0


def test_basis_defaults_to_sp15(client):
    snap_rows, hub_rows = _full_scenario()
    use_scenario(snap_rows, hub_rows)
    resp = post(client, [
        {"tile_id": "b", "tile_type": "pnode", "pnode_id": PNODE, "market": "RTPD", "view": "basis"},
    ])
    assert resp.json()["tiles"]["b"]["hub_ref"] == "SP15"


def test_basis_skips_unaligned_intervals(client):
    # Snapshot has two intervals; hub has only the 18:45 one -> only that aligns.
    snap_rows = [
        snap(PNODE, "RTPD", 12, 3, 30.0),
        snap(PNODE, "RTPD", 12, 4, 36.92829),
    ]
    hub_rows = [
        hub("caiso_lmp_rt_15min", "SP15", datetime.datetime(2026, 7, 21, 18, 45, tzinfo=UTC), 11.25682),
    ]
    use_scenario(snap_rows, hub_rows)
    resp = post(client, [
        {"tile_id": "b", "tile_type": "pnode", "pnode_id": PNODE, "market": "RTPD", "view": "basis"},
    ])
    tile = resp.json()["tiles"]["b"]
    assert len(tile["run"]) == 1
    assert tile["latest"]["ts_utc"] == "2026-07-21T18:45:00+00:00"


# ---------------------------------------------------------------------------
# dart — DA(HE) - RT(aligned), signed, hourly.
# ---------------------------------------------------------------------------

def test_dart_sign_and_value(client):
    snap_rows, hub_rows = _full_scenario()
    use_scenario(snap_rows, hub_rows)
    resp = post(client, [
        {"tile_id": "d", "tile_type": "pnode", "pnode_id": PNODE, "market": "RTPD", "view": "dart"},
    ])
    tile = resp.json()["tiles"]["d"]
    assert tile["da_market"] == "DAM" and tile["rt_market"] == "RTPD"
    # Hour 18:00Z: DAM HE12 = 50.0; RTPD intervals in that hour = [30.0, 36.92829]
    # avg = 33.464145 -> spread = 50.0 - 33.464145 = 16.535855 (positive = DA over RT).
    latest = tile["latest"]
    assert latest["ts_utc"] == "2026-07-21T18:00:00+00:00"
    assert latest["da"] == 50.0
    assert latest["rt"] == pytest.approx(33.464145)
    assert latest["spread"] == pytest.approx(16.535855)
    # HE11 (17:00Z) had no RT intervals -> excluded from the run.
    assert [e["ts_utc"] for e in tile["run"]] == ["2026-07-21T18:00:00+00:00"]


# ---------------------------------------------------------------------------
# Acceptance #2 — assert-the-raise: per-tile errors, batch stays healthy.
# ---------------------------------------------------------------------------

def test_unknown_tile_type_is_per_tile(client):
    snap_rows, hub_rows = _full_scenario()
    use_scenario(snap_rows, hub_rows)
    resp = post(client, [
        {"tile_id": "bad", "tile_type": "series", "pnode_id": PNODE, "market": "RTD", "view": "lmp"},
        {"tile_id": "good", "tile_type": "pnode", "pnode_id": PNODE, "market": "RTD", "view": "lmp"},
    ])
    assert resp.status_code == 200
    tiles = resp.json()["tiles"]
    assert tiles["bad"] == {"error": "unknown_tile_type"}
    # The batch stays healthy: the sibling tile is fully served.
    assert "error" not in tiles["good"]
    assert tiles["good"]["latest"]["lmp"] == 42.0


def test_per_tile_validation_errors(client):
    use_scenario(*_full_scenario())
    resp = post(client, [
        {"tile_id": "a", "tile_type": "pnode", "pnode_id": PNODE, "market": "RTD", "view": "nope"},
        {"tile_id": "b", "tile_type": "pnode", "pnode_id": PNODE, "market": "ZZZ", "view": "lmp"},
        {"tile_id": "c", "tile_type": "pnode", "pnode_id": "", "market": "RTD", "view": "lmp"},
        {"tile_id": "d", "tile_type": "pnode", "pnode_id": PNODE, "market": "DAM", "view": "dart"},
        {"tile_id": "e", "tile_type": "pnode", "pnode_id": PNODE, "market": "RTD", "view": "basis", "hub_ref": "BOGUS"},
    ])
    tiles = resp.json()["tiles"]
    assert tiles["a"]["error"] == "unknown_view"
    assert tiles["b"]["error"] == "unknown_market"
    assert tiles["c"]["error"] == "missing_pnode_id"
    assert tiles["d"]["error"] == "dart_requires_rt_market"
    assert tiles["e"]["error"] == "unknown_hub_ref"


def test_fmm_market_label_rejected(client):
    use_scenario(*_full_scenario())
    resp = post(client, [
        {"tile_id": "x", "tile_type": "pnode", "pnode_id": PNODE, "market": "FMM", "view": "lmp"},
    ])
    assert resp.json()["tiles"]["x"]["error"] == "unknown_market"


def test_no_rows_is_honest_empty_tile(client):
    # Node not present in the fetched window -> honest empty tile, NOT an error.
    use_scenario(*_full_scenario())
    resp = post(client, [
        {"tile_id": "empty", "tile_type": "pnode", "pnode_id": "GHOST_9_N999", "market": "RTD", "view": "lmp"},
    ])
    tile = resp.json()["tiles"]["empty"]
    assert "error" not in tile
    assert tile["empty"] is True
    assert tile["as_of"] is None
    assert tile["lag_minutes"] is None
    assert tile["latest"] is None
    assert tile["sparkline"] == []


# ---------------------------------------------------------------------------
# Acceptance #2 — the 0001-01-01 sentinel exclusion, proven.
# ---------------------------------------------------------------------------

def test_p2_sentinel_row_excluded(client):
    # The RTD fixture includes a market_date=0001-01-01 row with lmp=999. It must
    # never surface: latest stays the real 18:45 interval and 999 appears nowhere.
    use_scenario(*_full_scenario())
    resp = post(client, [
        {"tile_id": "t", "tile_type": "pnode", "pnode_id": PNODE, "market": "RTD", "view": "lmp"},
    ])
    tile = resp.json()["tiles"]["t"]
    assert tile["as_of"] == "2026-07-21T18:45:00+00:00"
    assert tile["latest"]["lmp"] == 42.0
    assert 999.0 not in [p["lmp"] for p in tile["sparkline"]]
    # And the P2 floor is bound in the snapshot read itself.
    snap_calls = [c for c in main._pool.scen["calls"] if "atlas_pnode_lmp_snapshot" in c["query"]]
    assert snap_calls and all(c["params"]["sentinel"] == "2020-01-01" for c in snap_calls)


def test_batched_snapshot_uses_any(client):
    use_scenario(*_full_scenario())
    post(client, [
        {"tile_id": "1", "tile_type": "pnode", "pnode_id": PNODE, "market": "RTD", "view": "lmp"},
        {"tile_id": "2", "tile_type": "pnode", "pnode_id": "OTHER_1_N001", "market": "RTD", "view": "lmp"},
    ])
    snap_calls = [c for c in main._pool.scen["calls"] if "atlas_pnode_lmp_snapshot" in c["query"]]
    # Two RTD tiles -> ONE batched RTD query carrying both pnodes via = ANY(...).
    rtd = [c for c in snap_calls if c["params"]["market"] == "RTD"]
    assert len(rtd) == 1
    assert "pnode_id = ANY(%(pnodes)s)" in rtd[0]["query"]
    assert set(rtd[0]["params"]["pnodes"]) == {PNODE, "OTHER_1_N001"}


def test_dart_adds_dam_leg_to_reads(client):
    use_scenario(*_full_scenario())
    post(client, [
        {"tile_id": "d", "tile_type": "pnode", "pnode_id": PNODE, "market": "RTPD", "view": "dart"},
    ])
    markets = {c["params"]["market"] for c in main._pool.scen["calls"]
               if "atlas_pnode_lmp_snapshot" in c["query"]}
    # dart pulls both the RT leg (RTPD) and the DA leg (DAM).
    assert markets == {"RTPD", "DAM"}


# ---------------------------------------------------------------------------
# Envelope faults — the only 400s.
# ---------------------------------------------------------------------------

def test_body_not_object(client):
    use_scenario()
    assert client.post("/api/watchboard", json=[1, 2]).status_code == 400


def test_missing_tiles_array(client):
    use_scenario()
    assert client.post("/api/watchboard", json={"nope": 1}).status_code == 400


def test_too_many_tiles(client):
    use_scenario()
    tiles = [{"tile_id": f"t{i}", "tile_type": "pnode", "pnode_id": PNODE,
              "market": "RTD", "view": "lmp"} for i in range(25)]
    assert post(client, tiles).status_code == 400


def test_missing_tile_id_is_envelope_400(client):
    use_scenario()
    resp = post(client, [{"tile_type": "pnode", "pnode_id": PNODE, "market": "RTD", "view": "lmp"}])
    assert resp.status_code == 400


def test_duplicate_tile_id_is_envelope_400(client):
    use_scenario()
    resp = post(client, [
        {"tile_id": "dup", "tile_type": "pnode", "pnode_id": PNODE, "market": "RTD", "view": "lmp"},
        {"tile_id": "dup", "tile_type": "pnode", "pnode_id": PNODE, "market": "RTD", "view": "lmp"},
    ])
    assert resp.status_code == 400


def test_empty_board_returns_empty(client):
    use_scenario()
    resp = post(client, [])
    assert resp.status_code == 200
    assert resp.json() == {"count": 0, "tiles": {}}


# ---------------------------------------------------------------------------
# Connection lifecycle + error honesty (the micro's core fix).
# ---------------------------------------------------------------------------

def test_dead_connection_retry_recovers(client):
    # The RTD snapshot read drops its connection once; the retry succeeds, so the
    # tile renders normally (assert-the-raise -> recover).
    snap_rows, hub_rows = _full_scenario()
    use_scenario(snap_rows, hub_rows, fail_snapshot={"RTD": 1})
    resp = post(client, [
        {"tile_id": "t", "tile_type": "pnode", "pnode_id": PNODE, "market": "RTD", "view": "lmp"},
    ])
    assert resp.status_code == 200
    tile = resp.json()["tiles"]["t"]
    assert "error" not in tile
    assert tile["latest"]["lmp"] == 42.0
    # Two RTD executes recorded: one failure + one successful retry.
    rtd = [c for c in main._pool.scen["calls"]
           if "atlas_pnode_lmp_snapshot" in c["query"] and c["params"]["market"] == "RTD"]
    assert len(rtd) == 2


def test_db_failure_is_per_tile_db_error_not_503(client):
    # The RTD read fails both attempts (retry exhausted). The RTD tile gets an
    # honest per-tile db_error; the batch still returns 200 and the RTPD basis
    # tile (a healthy source) renders — a per-tile failure never sinks the board.
    snap_rows, hub_rows = _full_scenario()
    use_scenario(snap_rows, hub_rows, fail_snapshot={"RTD": 2})
    resp = post(client, [
        {"tile_id": "dead", "tile_type": "pnode", "pnode_id": PNODE, "market": "RTD", "view": "lmp"},
        {"tile_id": "live", "tile_type": "pnode", "pnode_id": PNODE, "market": "RTPD", "view": "basis"},
    ])
    assert resp.status_code == 200
    tiles = resp.json()["tiles"]
    assert tiles["dead"] == {"error": "db_error"}
    assert "error" not in tiles["live"]
    assert tiles["live"]["latest"]["ts_utc"] == "2026-07-21T18:45:00+00:00"


def test_db_error_is_distinct_from_empty(client):
    # THE distinction: a failed read -> db_error; a fine read with zero rows ->
    # an honest empty tile. Same board, same shape difference.
    snap_rows, hub_rows = _full_scenario()
    use_scenario(snap_rows, hub_rows, fail_snapshot={"RTD": 2})
    resp = post(client, [
        # RTD read fails -> db_error.
        {"tile_id": "failed", "tile_type": "pnode", "pnode_id": PNODE, "market": "RTD", "view": "lmp"},
        # RTPD read is fine but this node has no rows -> empty tile, NOT db_error.
        {"tile_id": "empty", "tile_type": "pnode", "pnode_id": "GHOST_9_N999", "market": "RTPD", "view": "lmp"},
    ])
    tiles = resp.json()["tiles"]
    assert tiles["failed"] == {"error": "db_error"}
    assert "error" not in tiles["empty"]
    assert tiles["empty"]["empty"] is True
    assert tiles["empty"]["as_of"] is None


def test_dart_db_error_when_da_leg_read_fails(client):
    # dart needs BOTH the RT leg (RTPD) and the DA leg (DAM). If DAM fails, the
    # dart tile is db_error even though its RTPD read was fine.
    snap_rows, hub_rows = _full_scenario()
    use_scenario(snap_rows, hub_rows, fail_snapshot={"DAM": 2})
    resp = post(client, [
        {"tile_id": "d", "tile_type": "pnode", "pnode_id": PNODE, "market": "RTPD", "view": "dart"},
    ])
    assert resp.json()["tiles"]["d"] == {"error": "db_error"}


def test_basis_db_error_when_hub_read_fails(client):
    # basis needs the hub read; if it fails, the basis tile is db_error.
    snap_rows, hub_rows = _full_scenario()
    use_scenario(snap_rows, hub_rows, fail_hub=2)
    resp = post(client, [
        {"tile_id": "b", "tile_type": "pnode", "pnode_id": PNODE, "market": "RTPD", "view": "basis"},
    ])
    assert resp.json()["tiles"]["b"] == {"error": "db_error"}


# ---------------------------------------------------------------------------
# Acceptance #3 — DST alignment. The interval-start reconstruction resolves the
# offset by tz conversion, never fixed offset math.
# ---------------------------------------------------------------------------

def test_dst_spring_forward_offsets():
    f = main._cockpit_interval_start_utc
    # 2026-03-08: clocks spring 2:00 -> 3:00. An early hour is PST (-8), a later
    # hour is PDT (-7) — the SAME calendar day, two different UTC offsets.
    assert f("DAM", datetime.date(2026, 3, 8), 2, 0).isoformat() == "2026-03-08T09:00:00+00:00"   # 01:00 PST
    assert f("DAM", datetime.date(2026, 3, 8), 10, 0).isoformat() == "2026-03-08T16:00:00+00:00"  # 09:00 PDT


def test_dst_fall_back_offsets():
    f = main._cockpit_interval_start_utc
    # 2026-11-01: clocks fall 2:00 -> 1:00. Early hour PDT (-7), later hour PST (-8).
    assert f("DAM", datetime.date(2026, 11, 1), 1, 0).isoformat() == "2026-11-01T07:00:00+00:00"   # 00:00 PDT (-7)
    assert f("DAM", datetime.date(2026, 11, 1), 10, 0).isoformat() == "2026-11-01T17:00:00+00:00"  # 09:00 PST (-8)


def test_dst_same_he_differs_by_one_hour_across_transition():
    f = main._cockpit_interval_start_utc
    before = f("RTD", datetime.date(2026, 3, 7), 12, 1)  # PT 11:00 PST -> 19:00Z
    after = f("RTD", datetime.date(2026, 3, 9), 12, 1)   # PT 11:00 PDT -> 18:00Z
    # Same HE/interval (PT 11:00), either side of spring-forward -> the UTC clock
    # time differs by exactly 1h. Proof the offset comes from tz conversion.
    assert before.strftime("%H:%M") == "19:00"
    assert after.strftime("%H:%M") == "18:00"


def test_dst_boundary_basis_alignment_synthetic(client):
    # Synthetic basis join across the fall-back boundary: RTD HE10 int1 on
    # 2026-11-01 -> PT 09:00 PST -> 17:00Z. Hub SP15 5-min row at 17:00Z aligns.
    dst_now = datetime.datetime(2026, 11, 1, 18, 0, tzinfo=UTC)
    snap_rows = [
        snap(PNODE, "RTD", 10, 1, 55.0, md=datetime.date(2026, 11, 1)),
    ]
    hub_rows = [
        hub("caiso_lmp_rt_5min", "SP15",
            datetime.datetime(2026, 11, 1, 17, 0, tzinfo=UTC), 20.0),
    ]
    use_scenario(snap_rows, hub_rows, now=dst_now)
    resp = post(client, [
        {"tile_id": "b", "tile_type": "pnode", "pnode_id": PNODE, "market": "RTD", "view": "basis"},
    ])
    tile = resp.json()["tiles"]["b"]
    assert tile["latest"]["ts_utc"] == "2026-11-01T17:00:00+00:00"
    assert tile["latest"]["basis"] == 35.0  # 55.0 - 20.0
    assert tile["lag_minutes"] == 60
