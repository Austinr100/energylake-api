"""
Tests for the CAISO pnode-LMP latest-snapshot endpoint (PR-2):

  * GET /api/atlas/pnode-lmp?market={RTD|RTPD|DAM}

No live database is required. Like the sibling suites, these swap a tiny
in-memory fake pool into `main._pool`. Selection is now TWO index-driven steps
(D-07-17 load-perf reshape): (1) the latest instant via ORDER BY ... LIMIT 1,
(2) that instant's rows via an index range scan; a thin latest instant triggers
a one-instant fallback. The fake pool below is query-aware — it answers the
latest-instant selector, the previous-instant selector, and the row fetch from a
per-scenario script, and records every (query, params) so the structural guards
(index-only two-step shape, no geometry, the bound market) can be asserted at the
source-of-truth level.

DB rows are faked as psycopg's `dict_row` would yield them: instant selectors
yield {market_date, market_hour, market_interval}; the fetch yields the full
row (pnode_id, lmp, energy, congestion, loss, ghg, market_date, market_hour,
market_interval, snapshot_vintage, feed_generated_at).

Completeness note: the absolute floor is `PNODE_LMP_COMPLETENESS_FLOOR *
PNODE_LMP_EXPECTED_NODES` (~13,344 nodes). Tiny fixture payloads sit BELOW it, so
by default a single-instant scenario serves the latest instant flagged
`complete=false` (and, with no prior instant, `fell_back=false`) — the value-shape
assertions are unaffected. Tests that need the complete/fallback paths either
monkeypatch the expected-universe constant small or script a fuller prior instant.
"""

import datetime
import inspect

import pytest
from fastapi.testclient import TestClient

import main


# ---------------------------------------------------------------------------
# Query-aware in-memory fake pool.
#
# The endpoint runs, in order, on ONE cursor:
#   latest-instant selector  -> {market_date, market_hour, market_interval} | None
#   fetch(latest instant)    -> the instant's rows
#   [only if thin] prev-instant selector -> prior instant | None
#   [only if thin] fetch(prev instant)   -> the prior instant's rows
# Detection: a fetch query selects `pnode_id`; the prev selector is the only
# *selector* carrying %(md)s params; everything else is the latest selector.
# ---------------------------------------------------------------------------

def _instant(row):
    return {
        "market_date": row["market_date"],
        "market_hour": row["market_hour"],
        "market_interval": row["market_interval"],
    }


def _key(md, mh, mi):
    return (md, mh, mi)


class _FakeCursor:
    def __init__(self, scenario, sink):
        self._s = scenario
        self._sink = sink
        self._result = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, query, params=None):
        self._sink["calls"].append({"query": query, "params": params})
        self._sink["query"] = query   # last-call convenience
        self._sink["params"] = params

        if "pnode_id" in query:
            # Row fetch for a specific instant.
            key = _key(params["md"], params["mh"], params["mi"])
            self._result = list(self._s["rows_by_instant"].get(key, []))
        elif params is not None and "md" in params:
            # Previous-instant selector.
            prev = self._s["prev"]
            self._result = [prev] if prev is not None else []
        else:
            # Latest-instant selector.
            latest = self._s["latest"]
            self._result = [latest] if latest is not None else []

    async def fetchall(self):
        return list(self._result)

    async def fetchone(self):
        return self._result[0] if self._result else None


class _FakeConn:
    def __init__(self, scenario, sink):
        self._s = scenario
        self._sink = sink

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def cursor(self):
        return _FakeCursor(self._s, self._sink)


class FakePool:
    def __init__(self, scenario):
        self._s = scenario
        self.sink = {"calls": []}

    def connection(self):
        return _FakeConn(self._s, self.sink)


@pytest.fixture
def client():
    # No `with` block => lifespan does not run => real pool is never opened.
    return TestClient(main.app)


def use_scenario(latest_rows, prev_rows=None):
    """Install a fake pool for a scenario. `latest_rows` are the latest instant's
    rows (empty list => no instant => 503); `prev_rows`, if given, are the prior
    instant's rows used by the partial-write fallback. Returns the pool for
    query/params inspection."""
    latest = _instant(latest_rows[0]) if latest_rows else None
    prev = _instant(prev_rows[0]) if prev_rows else None
    rows_by_instant = {}
    if latest_rows:
        r0 = latest_rows[0]
        rows_by_instant[_key(r0["market_date"], r0["market_hour"], r0["market_interval"])] = latest_rows
    if prev_rows:
        p0 = prev_rows[0]
        rows_by_instant[_key(p0["market_date"], p0["market_hour"], p0["market_interval"])] = prev_rows
    pool = FakePool({"latest": latest, "prev": prev, "rows_by_instant": rows_by_instant})
    main._pool = pool
    return pool


def use_rows(rows):
    """Back-compat shim: a single-instant scenario (no prior instant)."""
    return use_scenario(rows)


# ---------------------------------------------------------------------------
# Row builder — one row as the fetch step yields it for the chosen instant.
# ---------------------------------------------------------------------------

UTC = datetime.timezone.utc

# A fixed instant used across tests (2026-07-05 HE9 interval 8, the recon RTD tip).
MARKET_DATE = datetime.date(2026, 7, 5)
MARKET_HOUR = 9
MARKET_INTERVAL = 8
VINTAGE = datetime.datetime(2026, 7, 5, 13, 45, 50, tzinfo=UTC)
FEED = datetime.datetime(2026, 7, 5, 13, 40, 0, tzinfo=UTC)


def _row(pnode_id, lmp, energy, congestion, loss, ghg,
         *, market_date=MARKET_DATE, market_hour=MARKET_HOUR,
         market_interval=MARKET_INTERVAL, vintage=VINTAGE, feed=FEED):
    return {
        "pnode_id": pnode_id,
        "lmp": lmp,
        "energy": energy,
        "congestion": congestion,
        "loss": loss,
        "ghg": ghg,
        "market_date": market_date,
        "market_hour": market_hour,
        "market_interval": market_interval,
        "snapshot_vintage": vintage,
        "feed_generated_at": feed,
    }


# ---------------------------------------------------------------------------
# Envelope + columnar contract
# ---------------------------------------------------------------------------

def test_envelope_shape_and_columnar_arrays(client):
    use_rows([
        _row("AAA_1_N001", 41.2, 40.0, 1.1, 0.1, 0.0),
        _row("BBB_2_N002", 39.8, 39.0, 0.7, 0.1, None),
    ])
    resp = client.get("/api/atlas/pnode-lmp?market=RTD")
    assert resp.status_code == 200
    body = resp.json()

    assert set(body) == {
        "market", "snapshot_vintage", "market_date", "market_hour",
        "market_interval", "feed_generated_at", "pnode_count",
        "expected_pnode_count", "complete", "fell_back",
        "pnode_id", "lmp", "energy", "congestion", "loss", "ghg",
    }
    # Prices only — no geometry, ever.
    assert "geometry" not in body
    assert not any(k in body for k in ("lat", "lon", "longitude", "latitude", "coordinates"))

    assert body["market"] == "RTD"
    assert body["market_date"] == "2026-07-05"
    assert body["market_hour"] == 9
    assert body["market_interval"] == 8
    assert body["pnode_count"] == 2
    assert body["expected_pnode_count"] == main.PNODE_LMP_EXPECTED_NODES

    # All six arrays order-aligned and equal length.
    assert body["pnode_id"] == ["AAA_1_N001", "BBB_2_N002"]
    assert body["lmp"] == [41.2, 39.8]
    assert body["energy"] == [40.0, 39.0]
    assert body["congestion"] == [1.1, 0.7]
    assert body["loss"] == [0.1, 0.1]
    assert body["ghg"] == [0.0, None]   # NULL passes through as JSON null
    for k in ("pnode_id", "lmp", "energy", "congestion", "loss", "ghg"):
        assert len(body[k]) == body["pnode_count"]


def test_null_component_is_json_null_not_zero(client):
    # Absence is not zero: a NULL congestion is emitted as null, held in place so
    # the column stays aligned with pnode_id.
    use_rows([_row("AAA_1_N001", 41.2, 40.0, None, 0.1, None)])
    body = client.get("/api/atlas/pnode-lmp?market=RTD").json()
    assert body["congestion"] == [None]
    assert body["ghg"] == [None]
    assert body["lmp"] == [41.2]


def test_prices_are_numeric(client):
    use_rows([_row("AAA_1_N001", 41.2, 40.0, 1.1, 0.1, 0.5)])
    body = client.get("/api/atlas/pnode-lmp?market=RTD").json()
    for k in ("lmp", "energy", "congestion", "loss", "ghg"):
        assert all(isinstance(v, (int, float)) for v in body[k])


# ---------------------------------------------------------------------------
# Scalars: constant instant, vintage/feed = MAX among served rows, UTC ISO
# ---------------------------------------------------------------------------

def test_scalars_taken_from_the_served_instant(client):
    use_rows([_row("AAA_1_N001", 41.2, 40.0, 1.1, 0.1, 0.0)])
    body = client.get("/api/atlas/pnode-lmp?market=RTD").json()
    assert body["snapshot_vintage"] == VINTAGE.isoformat()
    assert body["feed_generated_at"] == FEED.isoformat()
    assert body["snapshot_vintage"].endswith("+00:00")
    assert body["feed_generated_at"].endswith("+00:00")


def test_vintage_and_feed_are_max_among_rows(client):
    # A mixed-vintage instant: report the newest pull that touched it. Same for
    # feed_generated_at.
    older_v = datetime.datetime(2026, 7, 5, 12, 0, 0, tzinfo=UTC)
    newer_v = datetime.datetime(2026, 7, 5, 14, 0, 0, tzinfo=UTC)
    older_f = datetime.datetime(2026, 7, 5, 11, 0, 0, tzinfo=UTC)
    newer_f = datetime.datetime(2026, 7, 5, 13, 0, 0, tzinfo=UTC)
    use_rows([
        _row("AAA_1_N001", 41.2, 40.0, 1.1, 0.1, 0.0, vintage=older_v, feed=older_f),
        _row("BBB_2_N002", 39.8, 39.0, 0.7, 0.1, 0.0, vintage=newer_v, feed=newer_f),
    ])
    body = client.get("/api/atlas/pnode-lmp?market=RTD").json()
    assert body["snapshot_vintage"] == newer_v.isoformat()
    assert body["feed_generated_at"] == newer_f.isoformat()


def test_null_interval_scalar_passes_through(client):
    use_rows([_row("AAA_1_N001", 41.2, 40.0, 1.1, 0.1, 0.0, market_interval=None)])
    body = client.get("/api/atlas/pnode-lmp?market=DAM").json()
    assert body["market_interval"] is None


# ---------------------------------------------------------------------------
# Completeness verdict + one-instant partial-write fallback
# ---------------------------------------------------------------------------

def test_complete_instant_is_two_reads_no_fallback(client, monkeypatch):
    # Shrink the expected universe so a 2-row payload clears the floor: the happy
    # path is exactly the two-step (latest selector + fetch) — no fallback query.
    monkeypatch.setattr(main, "PNODE_LMP_EXPECTED_NODES", 2)
    pool = use_rows([
        _row("AAA_1_N001", 41.2, 40.0, 1.1, 0.1, 0.0),
        _row("BBB_2_N002", 39.8, 39.0, 0.7, 0.1, 0.0),
    ])
    body = client.get("/api/atlas/pnode-lmp?market=RTD").json()
    assert body["complete"] is True
    assert body["fell_back"] is False
    # Exactly two reads: latest-instant selector, then the fetch.
    assert len(pool.sink["calls"]) == 2
    assert "pnode_id" not in pool.sink["calls"][0]["query"]   # selector
    assert "pnode_id" in pool.sink["calls"][1]["query"]       # fetch


def test_thin_latest_with_no_prior_serves_latest_incomplete(client):
    # Tiny payload sits below the real floor and there is no prior instant: serve
    # the latest, flagged incomplete, without falling back.
    pool = use_rows([_row("AAA_1_N001", 41.2, 40.0, 1.1, 0.1, 0.0)])
    body = client.get("/api/atlas/pnode-lmp?market=RTD").json()
    assert body["complete"] is False
    assert body["fell_back"] is False
    assert body["pnode_count"] == 1
    # The prev-instant probe (a selector carrying md/mh/mi) did happen.
    assert any((c["params"] or {}).get("md") is not None and "pnode_id" not in c["query"]
               for c in pool.sink["calls"])


def test_partial_write_falls_back_to_fuller_prior_instant(client):
    # Latest instant is a thin mid-dispatch write (1 node); the prior instant is
    # fuller (2 nodes). Serve the prior, flagged fell_back, with ITS coordinates.
    latest = [_row("AAA_1_N001", 10.0, 9.0, 1.0, 0.0, 0.0,
                    market_date=datetime.date(2026, 7, 6), market_hour=10, market_interval=3)]
    prior = [
        _row("AAA_1_N001", 41.2, 40.0, 1.1, 0.1, 0.0,
             market_date=datetime.date(2026, 7, 6), market_hour=10, market_interval=2),
        _row("BBB_2_N002", 39.8, 39.0, 0.7, 0.1, 0.0,
             market_date=datetime.date(2026, 7, 6), market_hour=10, market_interval=2),
    ]
    use_scenario(latest, prev_rows=prior)
    body = client.get("/api/atlas/pnode-lmp?market=RTD").json()
    assert body["fell_back"] is True
    assert body["pnode_count"] == 2
    assert body["market_interval"] == 2            # the prior instant, not 3
    assert body["pnode_id"] == ["AAA_1_N001", "BBB_2_N002"]


def test_prior_not_fuller_keeps_latest(client):
    # Latest thin (2 nodes), prior even thinner (1 node): the fallback does not
    # improve coverage, so keep the freshest instant, flagged incomplete.
    latest = [
        _row("AAA_1_N001", 41.2, 40.0, 1.1, 0.1, 0.0, market_interval=8),
        _row("BBB_2_N002", 39.8, 39.0, 0.7, 0.1, 0.0, market_interval=8),
    ]
    prior = [_row("AAA_1_N001", 1.0, 1.0, 0.0, 0.0, 0.0, market_interval=7)]
    use_scenario(latest, prev_rows=prior)
    body = client.get("/api/atlas/pnode-lmp?market=RTD").json()
    assert body["fell_back"] is False
    assert body["complete"] is False
    assert body["market_interval"] == 8            # kept the latest
    assert body["pnode_count"] == 2


# ---------------------------------------------------------------------------
# Market param: default, case-insensitivity, validation -> 400
# ---------------------------------------------------------------------------

def test_default_market_is_rtd(client):
    pool = use_rows([_row("AAA_1_N001", 41.2, 40.0, 1.1, 0.1, 0.0)])
    body = client.get("/api/atlas/pnode-lmp").json()
    assert body["market"] == "RTD"
    assert pool.sink["calls"][0]["params"]["market"] == "RTD"


def test_explicit_markets_bind_through(client):
    for m in ("RTD", "RTPD", "DAM"):
        pool = use_rows([_row("AAA_1_N001", 41.2, 40.0, 1.1, 0.1, 0.0)])
        body = client.get(f"/api/atlas/pnode-lmp?market={m}").json()
        assert body["market"] == m
        assert pool.sink["calls"][0]["params"]["market"] == m


def test_market_is_case_insensitive(client):
    pool = use_rows([_row("AAA_1_N001", 41.2, 40.0, 1.1, 0.1, 0.0)])
    body = client.get("/api/atlas/pnode-lmp?market=rtpd").json()
    assert body["market"] == "RTPD"
    assert pool.sink["calls"][0]["params"]["market"] == "RTPD"


def test_unknown_market_is_400_with_allowed_values(client):
    use_rows([_row("AAA_1_N001", 41.2, 40.0, 1.1, 0.1, 0.0)])
    resp = client.get("/api/atlas/pnode-lmp?market=TH_NP15")
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "TH_NP15" in detail
    for m in ("RTD", "RTPD", "DAM"):
        assert m in detail


def test_unknown_market_short_circuits_before_db(client):
    # A bad market must not touch the pool at all.
    pool = use_rows([_row("AAA_1_N001", 41.2, 40.0, 1.1, 0.1, 0.0)])
    client.get("/api/atlas/pnode-lmp?market=NOPE")
    assert pool.sink["calls"] == []  # execute() never called


# ---------------------------------------------------------------------------
# No instant at all -> 503 (deliberate fork from the sibling's 404)
# ---------------------------------------------------------------------------

def test_no_rows_is_503_not_404_or_empty_200(client):
    use_rows([])
    resp = client.get("/api/atlas/pnode-lmp?market=RTD")
    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert "RTD" in detail
    assert "expired" in detail or "empty" in detail


# ---------------------------------------------------------------------------
# Cache-Control header
# ---------------------------------------------------------------------------

def test_cache_control_max_age_60(client):
    use_rows([_row("AAA_1_N001", 41.2, 40.0, 1.1, 0.1, 0.0)])
    resp = client.get("/api/atlas/pnode-lmp?market=RTD")
    assert resp.headers.get("cache-control") == "max-age=60"


# ---------------------------------------------------------------------------
# Query structure / source-level guards
# ---------------------------------------------------------------------------

def test_two_step_index_query_shape(client):
    pool = use_rows([_row("AAA_1_N001", 41.2, 40.0, 1.1, 0.1, 0.0)])
    client.get("/api/atlas/pnode-lmp?market=RTD")
    calls = pool.sink["calls"]

    # Step 1: latest-instant selector — ORDER BY ... LIMIT 1 over the instant
    # coordinates, bound market, no pnode_id, no old scan-and-sort machinery.
    q0 = calls[0]["query"]
    assert "atlas_pnode_lmp_snapshot" in q0
    assert "market = %(market)s" in q0
    assert "ORDER BY market_date DESC" in q0
    assert "LIMIT 1" in q0
    assert "pnode_id" not in q0
    assert calls[0]["params"]["market"] == "RTD"

    # Step 2: fetch — the instant's rows, IS NOT DISTINCT FROM on interval,
    # ordered by pnode_id.
    q1 = calls[1]["query"]
    assert "pnode_id" in q1
    assert "market_date = %(md)s" in q1
    assert "IS NOT DISTINCT FROM %(mi)s" in q1
    assert "ORDER BY pnode_id ASC" in q1

    # The old scan-and-sort shape is gone entirely.
    for c in calls:
        assert "COUNT(DISTINCT" not in c["query"]
        assert "%(floor)s" not in c["query"]


def test_no_geometry_columns_selected(client):
    pool = use_rows([_row("AAA_1_N001", 41.2, 40.0, 1.1, 0.1, 0.0)])
    client.get("/api/atlas/pnode-lmp?market=RTD")
    # Prices only — no query may ever reach for coordinates/geometry.
    for c in pool.sink["calls"]:
        q = c["query"].lower()
        for banned in ("geom", "latitude", "longitude", " lat", " lon", "coordinates"):
            assert banned not in q


def test_alignment_guard_and_constants_present():
    # The array-alignment 500 guard is a source-level invariant (can't be tripped
    # through the public path since arrays are built from one row walk).
    src = inspect.getsource(main.atlas_pnode_lmp)
    assert "misaligned" in src
    assert "status_code=500" in src
    # The market allowlist + default + floor base are the single source of truth.
    assert main.PNODE_LMP_DEFAULT_MARKET == "RTD"
    assert set(main.PNODE_LMP_MARKETS) == {"RTD", "RTPD", "DAM"}
    assert main.PNODE_LMP_COMPLETENESS_FLOOR == 0.90
    assert main.PNODE_LMP_EXPECTED_NODES == 14827
