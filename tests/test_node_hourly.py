"""
Tests for NODE HOURLY REGIME (lane ruled 2026-08-01, history block F1):

  * GET /api/analytics/nodes/{pnode_id}/hourly

No live database is required — the route tests swap a tiny in-memory fake pool
into `main._pool`, dispatching canned rows by inspecting the query text, exactly
as the sibling suites do.

WHAT THESE TESTS EXIST TO PROTECT. Each is silent on the server and wrong on the
glass at the client, which is why each has its own test:

  * THE DART SIGN IS PINNED AND THE OPPOSITE ONE SHIPS FROM THIS SAME API.
    dart = rt_avg - dam_lmp, so POSITIVE MEANS RT ABOVE DA. The field named
    `spread` on /api/timeseries/caiso-hub-lmp carries the other sign. A flip
    here inverts every regime chip, every stance verdict and every heat-grid
    cell at once, and it inverts them PLAUSIBLY - the payload still validates.
    So the law is asserted on the banked leg (the producer's own column, served
    verbatim), on today's leg (recomputed here from two snapshot legs), and on
    the raw history cells, because those are three different code paths that
    could disagree.

  * THE COVERAGE STRING IS A SHAPE, NOT A SENTENCE. "3/30" is present-days over
    days ASKED FOR, on EVERY envelope and EVERY ladder row. Over days available
    it would always read 100% and the honesty machinery would be decorative.
    The denominator must track `days` and the numerator must never exceed it.

  * THE BOUNDS ARE THE BOUNDS. days is 7..90 inclusive. 6 and 91 are the two
    cells that decide whether the rail is off by one; 200 is the far case the
    architect's battery drove. All three are 400s that NAME the bound and the
    value, because a client shows that string.

  * AN UNKNOWN NODE IS A 200. The house convention is an empty window plus a
    depth block that says so - never a 404 and never a fabricated flat line.
    The zero-row path is also the ONLY path that pays for the warehouse
    frontier, and the frontier is what distinguishes a bad id from an empty
    table, so its presence is part of the contract.

  * n_cells == n_banked_cells IS THE HISTORY BLOCK'S WHOLE WARRANT. The grid
    the dashboard draws and the distributions the payload derives must be the
    same cells counted twice, not two populations that happen to be close. If
    they can diverge, one of them is lying and the reader cannot tell which.

  * TODAY IS NOT IN HISTORY. Today is read live and partial from a different
    store and is deliberately excluded from every distribution it is placed
    against. A today row leaking into `history` would put an unsettled cell in
    a grid that claims to be banked - and would quietly put today back inside
    its own sample.

THE HPLNDJT FIXTURE IS REAL. These 87 rows are HPLNDJT_6_N001's actual banked
rows for 2026-07-25, 07-29, 07-30 and 07-31, read out of the pantry on
2026-08-01. 07-25 is the SEED DAY and carries the live warehouse's three
different per-HE depths in one date: HE1-9 have no row at all, HE10 is RT-only
(dam_lmp NULL, so dart NULL, so no history cell), and HE11-24 are whole. That
row is the null-vs-absent case as it actually exists, not a hand-invented one.
"""

import datetime

import psycopg
import pytest
from fastapi.testclient import TestClient

import main


UTC = datetime.timezone.utc
D = datetime.date

NODE = "HPLNDJT_6_N001"
UNKNOWN = "NO_SUCH_NODE_1_N999"

# The frozen instant. 16:46Z on 2026-08-01 is 09:46 PT the same day, so
# today_PT = 2026-08-01, hi = 07-31 and a days=30 ask starts at 07-02.
NOW = datetime.datetime(2026, 8, 1, 16, 46, tzinfo=UTC)
TODAY_PT = D(2026, 8, 1)
YESTERDAY_PT = D(2026, 7, 31)


# ---------------------------------------------------------------------------
# Fake pool. Same shape as the sibling suites: canned rows dispatched by SQL
# text.
# ---------------------------------------------------------------------------

class _FakeCursor:
    def __init__(self, router, sink):
        self._router, self._sink = router, sink
        self._rows = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, query, params=None):
        self._sink["queries"].append(query)
        self._sink["params"].append(params)
        self._rows = self._router(query, params or {})

    async def fetchall(self):
        return list(self._rows)


class _FakeConn:
    def __init__(self, router, sink):
        self._router, self._sink = router, sink

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def cursor(self):
        return _FakeCursor(self._router, self._sink)


class FakePool:
    def __init__(self, router):
        self._router = router
        self.sink = {"queries": [], "params": []}

    def connection(self):
        return _FakeConn(self._router, self.sink)


@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.fixture(autouse=True)
def _freeze_and_clear(monkeypatch):
    """Freeze the clock and drop the frontier's TTL cache.

    The frontier is cached for five minutes across requests, so without this a
    test that seeds one warehouse frontier would leak it into the next.
    """
    monkeypatch.setattr(main, "_utcnow", lambda: NOW)
    main._anh_frontier_cache.clear()
    yield
    main._anh_frontier_cache.clear()


# ---------------------------------------------------------------------------
# The fixture. HPLNDJT_6_N001's real banked rows, (date, he, dam, rt, rt_n,
# dart) exactly as the pantry holds them.
# ---------------------------------------------------------------------------

_BANKED = [
    # 2026-07-25 — the seed day. No row before HE10; HE10 is RT-only.
    (D(2026, 7, 25), 10, None, 12.724400, 2, None),
    (D(2026, 7, 25), 11, 6.083500, 6.231273, 4, 0.147772),
    (D(2026, 7, 25), 12, 12.887720, 13.462535, 4, 0.574815),
    (D(2026, 7, 25), 13, 13.798110, 13.446215, 4, -0.351895),
    (D(2026, 7, 25), 14, 24.373580, 13.403058, 4, -10.970523),
    (D(2026, 7, 25), 15, 19.630220, 13.062668, 4, -6.567553),
    (D(2026, 7, 25), 16, 37.889550, 22.434848, 4, -15.454703),
    (D(2026, 7, 25), 17, 37.485730, 15.638503, 4, -21.847228),
    (D(2026, 7, 25), 18, 40.780000, 7.716043, 4, -33.063958),
    (D(2026, 7, 25), 19, 31.154780, 21.115018, 4, -10.039763),
    (D(2026, 7, 25), 20, 43.092170, -5.438507, 3, -48.530677),
    (D(2026, 7, 25), 21, 41.423730, -0.515445, 4, -41.939175),
    (D(2026, 7, 25), 22, 42.289080, -11.038918, 4, -53.327998),
    (D(2026, 7, 25), 23, 3.453430, -20.738515, 4, -24.191945),
    (D(2026, 7, 25), 24, 14.802220, -18.957888, 4, -33.760108),
    # 2026-07-29 — whole.
    (D(2026, 7, 29), 1, -33.548210, -29.305115, 4, 4.243095),
    (D(2026, 7, 29), 2, -28.614760, -22.743608, 4, 5.871153),
    (D(2026, 7, 29), 3, -25.315100, -29.120533, 4, -3.805433),
    (D(2026, 7, 29), 4, -24.870170, -24.344918, 4, 0.525253),
    (D(2026, 7, 29), 5, 144.829700, 90.256163, 4, -54.573538),
    (D(2026, 7, 29), 6, 168.171430, 94.941538, 4, -73.229893),
    (D(2026, 7, 29), 7, 183.047790, 90.437170, 4, -92.610620),
    (D(2026, 7, 29), 8, 131.696720, 83.560170, 4, -48.136550),
    (D(2026, 7, 29), 9, 120.961710, 117.631930, 4, -3.329780),
    (D(2026, 7, 29), 10, 112.463130, 111.889623, 4, -0.573508),
    (D(2026, 7, 29), 11, 113.760050, 146.460145, 4, 32.700095),
    (D(2026, 7, 29), 12, 109.263430, 121.144158, 4, 11.880728),
    (D(2026, 7, 29), 13, 111.323200, 135.388305, 4, 24.065105),
    (D(2026, 7, 29), 14, 34.154190, 64.582330, 4, 30.428140),
    (D(2026, 7, 29), 15, 36.069960, 37.726223, 4, 1.656263),
    (D(2026, 7, 29), 16, 38.554210, 41.253960, 4, 2.699750),
    (D(2026, 7, 29), 17, 40.953470, -21.828438, 4, -62.781908),
    (D(2026, 7, 29), 18, 51.516430, -28.087213, 4, -79.603643),
    (D(2026, 7, 29), 19, 71.855440, -29.404878, 4, -101.260318),
    (D(2026, 7, 29), 20, -52.936500, -37.902000, 4, 15.034500),
    (D(2026, 7, 29), 21, -46.848920, -34.833785, 4, 12.015135),
    (D(2026, 7, 29), 22, -44.677470, -26.469983, 4, 18.207488),
    (D(2026, 7, 29), 23, -38.312720, -26.089165, 4, 12.223555),
    (D(2026, 7, 29), 24, -34.511490, -22.098890, 4, 12.412600),
    # 2026-07-30 — whole. HE3 and HE11 are the architect's two spot-match cells.
    (D(2026, 7, 30), 1, -11.045070, -22.858103, 4, -11.813033),
    (D(2026, 7, 30), 2, -18.355750, -20.041293, 3, -1.685543),
    (D(2026, 7, 30), 3, -25.417600, -21.748005, 4, 3.669595),
    (D(2026, 7, 30), 4, -20.611350, -23.122980, 4, -2.511630),
    (D(2026, 7, 30), 5, 94.308940, 94.394743, 4, 0.085802),
    (D(2026, 7, 30), 6, 104.392890, 95.478473, 4, -8.914418),
    (D(2026, 7, 30), 7, 103.729190, 134.454575, 4, 30.725385),
    (D(2026, 7, 30), 8, 100.552850, 99.378920, 4, -1.173930),
    (D(2026, 7, 30), 9, 94.994500, 100.358473, 4, 5.363973),
    (D(2026, 7, 30), 10, 92.103310, 94.642678, 4, 2.539368),
    (D(2026, 7, 30), 11, 89.941440, 70.539185, 4, -19.402255),
    (D(2026, 7, 30), 12, 92.822400, 115.361108, 4, 22.538708),
    (D(2026, 7, 30), 13, 87.011440, 103.123365, 4, 16.111925),
    (D(2026, 7, 30), 14, -19.257580, -4.602653, 4, 14.654928),
    (D(2026, 7, 30), 15, -28.084860, -17.960225, 4, 10.124635),
    (D(2026, 7, 30), 16, -10.523970, -24.046388, 4, -13.522418),
    (D(2026, 7, 30), 17, -22.413650, -20.538858, 4, 1.874793),
    (D(2026, 7, 30), 18, -6.884590, -23.826070, 4, -16.941480),
    (D(2026, 7, 30), 19, -6.255570, -32.381533, 4, -26.125963),
    (D(2026, 7, 30), 20, -12.961420, -35.910440, 4, -22.949020),
    (D(2026, 7, 30), 21, -6.241590, -32.679958, 4, -26.438368),
    (D(2026, 7, 30), 22, 0.683110, -26.115210, 4, -26.798320),
    (D(2026, 7, 30), 23, -18.875730, -23.388883, 3, -4.513153),
    (D(2026, 7, 30), 24, -24.432030, -22.873408, 4, 1.558623),
    # 2026-07-31 — yesterday, whole. The last date the window may reach.
    (D(2026, 7, 31), 1, -23.170360, -133.989325, 4, -110.818965),
    (D(2026, 7, 31), 2, -6.140400, -133.160825, 4, -127.020425),
    (D(2026, 7, 31), 3, -15.271550, -132.111805, 4, -116.840255),
    (D(2026, 7, 31), 4, -9.673700, -130.988920, 3, -121.315220),
    (D(2026, 7, 31), 5, 85.149280, 179.411067, 3, 94.261787),
    (D(2026, 7, 31), 6, 67.053690, 191.183173, 4, 124.129483),
    (D(2026, 7, 31), 7, 91.298650, 218.682810, 4, 127.384160),
    (D(2026, 7, 31), 8, 84.611500, 174.080223, 4, 89.468723),
    (D(2026, 7, 31), 9, 97.136710, 252.283290, 4, 155.146580),
    (D(2026, 7, 31), 10, 84.305900, 167.867405, 4, 83.561505),
    (D(2026, 7, 31), 11, 111.849200, 170.431348, 4, 58.582148),
    (D(2026, 7, 31), 12, 92.415870, 195.171925, 4, 102.756055),
    (D(2026, 7, 31), 13, 103.640470, 182.927528, 4, 79.287058),
    (D(2026, 7, 31), 14, 38.312880, 49.633053, 4, 11.320173),
    (D(2026, 7, 31), 15, 26.009970, 55.444198, 4, 29.434228),
    (D(2026, 7, 31), 16, 27.538030, 65.500633, 4, 37.962603),
    (D(2026, 7, 31), 17, 17.737550, 74.828385, 4, 57.090835),
    (D(2026, 7, 31), 18, -9.053350, 74.596045, 4, 83.649395),
    (D(2026, 7, 31), 19, -18.600460, -134.548168, 4, -115.947708),
    (D(2026, 7, 31), 20, -39.067400, 56.189075, 4, 95.256475),
    (D(2026, 7, 31), 21, -40.595950, -153.527703, 4, -112.931753),
    (D(2026, 7, 31), 22, -7.216150, -145.584210, 4, -138.368060),
    (D(2026, 7, 31), 23, -29.467820, -136.424190, 3, -106.956370),
    (D(2026, 7, 31), 24, -16.724140, -134.605750, 4, -117.881610),
]

# The four dates the fixture holds, and the count of cells that carry a dart.
# 07-25 HE10 is dam-less, so it is banked but NOT a dart cell — 87 rows, 86
# cells. That one-row difference is the whole null-vs-absent contract.
BANKED_DATES = 4
BANKED_ROWS = len(_BANKED)
DART_CELLS = sum(1 for r in _BANKED if r[5] is not None)


def window_rows(lo, hi):
    """The banked rows inside [lo, hi], shaped as psycopg's dict_row yields."""
    return [
        {"trade_date": d, "he": he, "dam_lmp": dam, "rt_avg": rt,
         "rt_n": n, "dart": dart, "basis_sp15": None}
        for (d, he, dam, rt, n, dart) in _BANKED
        if lo <= d <= hi
    ]


# Today's live snapshot. Three hours, DAM one print each and RTPD four
# intervals each, so today's dart is computed from both legs by the same
# expression the producer used. HE1 is RT-BELOW-DA (negative dart) and HE2/HE3
# are RT-ABOVE-DA (positive) - both directions, so the sign law is driven from
# each side rather than confirmed once.
_TODAY_DAM = {1: 40.0, 2: 10.0, 3: -5.0}
_TODAY_RTPD = {
    1: [30.0, 31.0, 29.0, 30.0],     # mean 30.0  -> dart -10.0
    2: [12.0, 13.0, 11.0, 12.0],     # mean 12.0  -> dart  +2.0
    3: [4.0, 6.0, 5.0, 5.0],         # mean  5.0  -> dart +10.0
}


def today_rows():
    out = []
    for he, lmp in sorted(_TODAY_DAM.items()):
        out.append({"market": main.AN_DA_MARKET, "market_hour": he,
                    "market_interval": 1, "lmp": lmp})
    for he, ivs in sorted(_TODAY_RTPD.items()):
        for i, lmp in enumerate(ivs, start=1):
            out.append({"market": main.AN_RT_MARKET, "market_hour": he,
                        "market_interval": i, "lmp": lmp})
    return out


DEPTH_ROW = {"min_date": D(2026, 7, 25), "max_date": D(2026, 7, 31),
             "rows": BANKED_ROWS}
EMPTY_DEPTH_ROW = {"min_date": None, "max_date": None, "rows": 0}
FRONTIER_ROW = {"min_date": D(2026, 7, 25), "max_date": D(2026, 7, 31)}


def hourly_router(present=True, today=True):
    """Dispatch the endpoint's four reads by SQL text.

    `present=False` drives the unknown-node path: empty window, zero-row depth,
    and the table-wide frontier the endpoint only asks for in that case.
    """
    def router(query, params):
        if "atlas_pnode_lmp_snapshot" in query:
            return today_rows() if (present and today) else []
        if "atlas_node_hourly_stats" not in query:
            raise AssertionError(f"unexpected query: {query[:120]}")
        if "min(trade_date)" in query:
            if "pnode_id = %(pnode_id)s" in query:
                return [DEPTH_ROW if present else EMPTY_DEPTH_ROW]
            return [FRONTIER_ROW]
        if not present:
            return []
        return window_rows(params["lo"], params["hi"])
    return router


def use_router(router):
    pool = FakePool(router)
    main._pool = pool
    return pool


def get(client, node=NODE, present=True, today=True, **params):
    use_router(hourly_router(present=present, today=today))
    return client.get(f"/api/analytics/nodes/{node}/hourly", params=params)


# ---------------------------------------------------------------------------
# 1. The dart sign law: positive = RT above DA.
# ---------------------------------------------------------------------------

def test_dart_sign_law_holds_on_the_banked_leg(client):
    """Every history cell equals rt_avg - dam_lmp, sign included.

    The banked leg serves the producer's own `dart` column verbatim, so this
    asserts the warehouse's arithmetic AND that the endpoint did not negate it
    in transit. Checked per cell rather than in aggregate: a sign flip on one
    hour is exactly the failure that averages out of a summary statistic.
    """
    r = get(client)
    assert r.status_code == 200
    cells = {(c["trade_date"], c["he"]): c["value"]
             for c in r.json()["dart"]["history"]["cells"]}
    assert cells, "fixture must produce cells or this test proves nothing"

    for (d, he, dam, rt, _n, dart) in _BANKED:
        if dart is None:
            continue
        served = cells[(d.isoformat(), he)]
        assert served == pytest.approx(rt - dam, abs=1e-4), (d, he)
        # The law itself, stated as a direction and not as a subtraction.
        if rt > dam:
            assert served > 0, f"RT above DA must be positive at {d} HE{he}"
        elif rt < dam:
            assert served < 0, f"RT below DA must be negative at {d} HE{he}"


def test_dart_sign_law_holds_on_todays_recomputed_leg(client):
    """Today's dart is recomputed here from two snapshot legs, so it is a
    SECOND implementation of the same law and gets its own assertion.

    HE1 is RT below DA and must be negative; HE2 and HE3 are RT above DA and
    must be positive. A flip that spared the banked column and caught only this
    path would show as today's chip landing on the wrong side of its own
    history.
    """
    r = get(client)
    rows = {row["he"]: row for row in r.json()["today"]["rows"]}
    assert rows[1]["dart"] == pytest.approx(-10.0)
    assert rows[2]["dart"] == pytest.approx(2.0)
    assert rows[3]["dart"] == pytest.approx(10.0)
    for he, row in rows.items():
        assert row["dart"] == pytest.approx(row["rt_avg"] - row["dam_lmp"])
        assert row["partial"] is True


def test_dart_sign_rule_names_the_direction_and_the_opposite_shipper(client):
    """The rule string is a contract a client may render. Its two load-bearing
    clauses - which sign means what, and where the OTHER sign ships from - are
    pinned so a reword that changes the meaning fails."""
    rule = get(client).json()["rules"]["dart_sign"]
    assert "positive = RT above DA" in rule
    assert "rt_avg - dam_lmp" in rule
    assert "caiso-hub-lmp" in rule


# ---------------------------------------------------------------------------
# 2. The coverage string shape.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("days", [7, 30, 90])
def test_coverage_string_shape_on_every_envelope_and_ladder_row(client, days):
    """"<present>/<requested>" on EVERY row of both forms.

    The denominator is days ASKED FOR, never days available - over the latter it
    would read 100% at every depth and the honesty machinery would be
    decorative. The numerator may never exceed it.
    """
    r = get(client, days=days)
    assert r.status_code == 200
    body = r.json()
    assert body["requested_days"] == days

    for form in ("envelope", "ladder"):
        rows = body["dart"][form]
        assert rows, form
        for row in rows:
            num, _, den = row["coverage"].partition("/")
            assert den == str(days), (form, row["he"], row["coverage"])
            assert num == str(row["days_present"])
            assert 0 <= row["days_present"] <= days

    # And on the window block, where the numerator is DATES banked, not cells.
    assert body["window"]["coverage"] == f"{BANKED_DATES}/{days}"
    assert body["window"]["days_banked"] == BANKED_DATES


def test_coverage_is_not_uniform_across_hours(client):
    """The seed day banks from HE10, so HE1-9 are one day shallower than
    HE11-24 in this window. Per-HE coverage that came out uniform would mean
    the axis was being padded somewhere."""
    body = get(client, days=30).json()
    cov = {row["he"]: row["coverage"] for row in body["dart"]["envelope"]}
    assert cov[1] == "3/30", "HE1 has no 07-25 row"
    assert cov[10] == "3/30", "07-25 HE10 is RT-only: banked, but no dart"
    assert cov[11] == "4/30", "HE11 is whole on all four dates"


# ---------------------------------------------------------------------------
# 3. The 400 bounds.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("days", [6, 91, 200])
def test_days_outside_the_bounds_is_a_400_naming_bound_and_value(client, days):
    """6 and 91 are the two cells that decide an off-by-one on the rail; 200 is
    the far case. The detail must name BOTH bounds and the value the caller
    sent, because that string is what a client shows."""
    r = get(client, days=days)
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert f"between {main.ANH_DAYS_MIN} and {main.ANH_DAYS_MAX}" in detail
    assert f"got {days}" in detail


@pytest.mark.parametrize("days", [7, 90])
def test_the_bounds_themselves_are_inclusive(client, days):
    """The other half of the off-by-one: 7 and 90 must be served."""
    assert get(client, days=days).status_code == 200


# ---------------------------------------------------------------------------
# 4. The unknown node.
# ---------------------------------------------------------------------------

def test_unknown_node_is_a_200_with_the_absent_depth_shape(client):
    """Never a 404 and never a fabricated flat line.

    The depth block must SAY the node has no rows at any date, and must carry
    the warehouse frontier - the only thing that lets a client tell a bad id
    from an empty table. Both metric blocks keep their full shape with zero
    cells, so the client's renderer does not need an absent-node branch.
    """
    r = get(client, node=UNKNOWN, present=False)
    assert r.status_code == 200
    body = r.json()

    depth = body["depth"]
    assert depth["node_present"] is False
    assert depth["node_rows"] == 0
    assert depth["node_min_trade_date"] is None
    assert depth["node_max_trade_date"] is None
    assert "no row in" in depth["node_absent_note"]
    assert depth["warehouse_frontier"]["max_trade_date"] == "2026-07-31"
    # No first banked date means there is no date to promise completion on.
    assert depth["accretion"]["window_complete_on"] is None
    assert depth["accretion"]["window_complete"] is False

    assert body["window"]["days_banked"] == 0
    assert body["window"]["first_trade_date"] is None
    assert body["window"]["gap_days_interior"] == []
    assert body["query_plan"]["reads"] == 4

    for metric in ("dart", "basis"):
        block = body[metric]
        assert block["available"] is False
        assert block["n_banked_cells"] == 0
        assert block["reason"]
        assert block["history"] == {
            "cells": [],
            "n_cells": 0,
            "rule": main.ANH_HISTORY_RULE.format(metric=metric),
        }
        # The shape survives: every form is present, every value null.
        assert len(block["envelope"]) == len(body["hours"])
        assert all(row["p50"] is None for row in block["envelope"])


def test_a_present_node_never_pays_for_the_frontier(client):
    """The frontier is a 219 ms parallel seq scan. Three reads, not four."""
    body = get(client).json()
    assert body["query_plan"]["reads"] == 3
    assert "warehouse_frontier" not in body["depth"]


# ---------------------------------------------------------------------------
# 5. history.n_cells == dart.n_banked_cells.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("days", [7, 30, 90])
def test_history_n_cells_equals_n_banked_cells(client, days):
    """The grid and the distributions must be the SAME cells counted twice.

    If these can diverge, one of them is lying about the warehouse and the
    reader has no way to tell which. Asserted against the length of the list
    too, so a stale count cannot pass by agreeing with a different number.
    """
    body = get(client, days=days).json()
    for metric in ("dart", "basis"):
        block = body[metric]
        hist = block["history"]
        assert hist["n_cells"] == block["n_banked_cells"], metric
        assert hist["n_cells"] == len(hist["cells"]), metric

    assert body["dart"]["history"]["n_cells"] == DART_CELLS
    assert body["basis"]["history"]["n_cells"] == 0


def test_history_lists_present_cells_only_and_never_pads_the_grid(client):
    """A dam-less hour has a NULL dart, so it has no cell - the grid renders an
    honest hole. 87 banked rows, 86 dart cells, and 07-25 HE10 is the one that
    is missing on purpose. Nothing is padded to a 24 x days rectangle."""
    body = get(client, days=30).json()
    cells = body["dart"]["history"]["cells"]
    keys = {(c["trade_date"], c["he"]) for c in cells}

    assert len(cells) == BANKED_ROWS - 1 == DART_CELLS
    assert ("2026-07-25", 10) not in keys, "RT-only hour must not appear"
    assert ("2026-07-25", 11) in keys
    assert ("2026-07-25", 9) not in keys, "no row at all, so no cell"
    assert len(keys) == len(cells), "no duplicate (trade_date, he)"
    assert len(cells) < 24 * BANKED_DATES, "a padded grid would be 96"

    # Every cell is the three-key shape and nothing else.
    assert all(set(c) == {"trade_date", "he", "value"} for c in cells)
    assert all(c["value"] is not None for c in cells)


def test_history_cells_spot_match_the_warehouse(client):
    """Two cells matched to the values the pantry holds, one of them the
    architect's own. HPLNDJT_6_N001 07-30 HE3 = 3.6696 and 07-30 HE11 =
    -19.4023, rounded to the payload's four places."""
    cells = {(c["trade_date"], c["he"]): c["value"]
             for c in get(client, days=30).json()["dart"]["history"]["cells"]}
    assert cells[("2026-07-30", 3)] == 3.6696
    assert cells[("2026-07-30", 11)] == -19.4023


def test_history_is_ordered_by_trade_date_then_he(client):
    """The window read's own ORDER BY, carried to the wire. A grid renderer
    that walks the list in order should not have to sort it first."""
    cells = get(client, days=30).json()["dart"]["history"]["cells"]
    keys = [(c["trade_date"], c["he"]) for c in cells]
    assert keys == sorted(keys)


def test_history_rule_states_the_absence_contract(client):
    """The one line that tells a reader what a missing cell means."""
    rule = get(client, days=30).json()["dart"]["history"]["rule"]
    assert "never interpolated" in rule
    assert "never padded to a full grid" in rule
    assert "dart" in rule


@pytest.mark.parametrize("days", [7, 30, 90])
def test_history_window_matches_the_window_the_derivations_used(client, days):
    """Same window, or the grid and the ladder above it describe different
    spans.

    Every cell must sit inside [requested_start, requested_end], and that pair
    must be lo = today - days .. yesterday. At the days=7 FLOOR lo lands
    exactly on 2026-07-25, so the floor still reaches the seed day - the
    warehouse is shallower than the shortest window this route will serve, and
    no ask can currently truncate the near end.
    """
    body = get(client, days=days).json()
    win = body["window"]
    assert win["requested_start"] == (TODAY_PT - datetime.timedelta(days=days)).isoformat()
    assert win["requested_end"] == YESTERDAY_PT.isoformat()

    dates = {c["trade_date"] for c in body["dart"]["history"]["cells"]}
    assert dates == {"2026-07-25", "2026-07-29", "2026-07-30", "2026-07-31"}
    assert all(win["requested_start"] <= d <= win["requested_end"] for d in dates)
    assert body["dart"]["history"]["n_cells"] == body["dart"]["n_banked_cells"]

    # The grid's dates ARE the window block's banked dates, not a near-miss.
    assert dates == {b["trade_date"] for b in win["banked_dates"]}


# ---------------------------------------------------------------------------
# 6. History contains no today rows.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("days", [7, 30, 90])
def test_history_contains_no_today_rows(client, days):
    """Today is live, partial, and deliberately outside every distribution it
    is placed against. A today cell in `history` would put an unsettled value
    in a grid that claims to be banked - and would quietly put today back
    inside its own sample."""
    body = get(client, days=days).json()
    assert body["as_of_pt_date"] == TODAY_PT.isoformat()
    assert body["today"]["rows"], "today must be populated or this proves nothing"

    for metric in ("dart", "basis"):
        dates = {c["trade_date"] for c in body[metric]["history"]["cells"]}
        assert TODAY_PT.isoformat() not in dates, metric
        assert all(d <= YESTERDAY_PT.isoformat() for d in dates), metric


def test_history_holds_even_when_todays_hours_overlap_banked_hours(client):
    """Today carries HE1-3, and so do the banked dates. The exclusion is by
    DATE, not by hour, so the banked HE1-3 cells must still be present while
    today's are not."""
    cells = {(c["trade_date"], c["he"]): c["value"]
             for c in get(client, days=30).json()["dart"]["history"]["cells"]}
    assert ("2026-07-31", 1) in cells
    assert ("2026-07-31", 3) in cells
    assert ("2026-08-01", 1) not in cells
    assert ("2026-08-01", 3) not in cells


def test_history_costs_no_extra_read(client):
    """The block is emitted from rows already in hand. If it ever grows its own
    query this fails, which is the point - a heat grid is not worth a fifth
    round trip against a table this request already read."""
    pool = use_router(hourly_router())
    r = client.get(f"/api/analytics/nodes/{NODE}/hourly", params={"days": 30})
    assert r.status_code == 200
    assert len(pool.sink["queries"]) == 3
    assert r.json()["query_plan"]["reads"] == 3


# ---------------------------------------------------------------------------
# The dark basis shelf keeps the same shape.
# ---------------------------------------------------------------------------

def test_basis_history_mirrors_the_shape_dark(client):
    """basis_sp15 is NULL on every row today, so the SAME call yields an empty
    list under the existing reason. No branch, no separate shape - the block
    lights itself when the hub lands."""
    block = get(client, days=30).json()["basis"]
    assert block["available"] is False
    assert block["reason"] == main.ANH_BASIS_DARK_REASON
    assert "D-07-31-01" in block["reason"]
    assert block["history"]["cells"] == []
    assert block["history"]["n_cells"] == 0
    assert block["history"]["rule"] == main.ANH_HISTORY_RULE.format(metric="basis")
    assert set(block["history"]) == set(get(client, days=30)
                                        .json()["dart"]["history"])


# ---------------------------------------------------------------------------
# The route itself.
# ---------------------------------------------------------------------------

def test_route_is_plural_nodes(client):
    """/nodes/ (plural) is the deployed contract. The singular is a different
    room's route and must not answer here."""
    use_router(hourly_router())
    assert client.get(
        f"/api/analytics/nodes/{NODE}/hourly").status_code == 200
    assert client.get(
        f"/api/analytics/node/{NODE}/hourly").status_code == 404


def test_blank_pnode_id_is_a_400(client):
    r = get(client, node="%20")
    assert r.status_code == 400
    assert "pnode_id is required" in r.json()["detail"]


def test_db_down_is_a_503(client):
    def boom(query, params):
        raise psycopg.OperationalError("connection refused")
    use_router(boom)
    r = client.get(f"/api/analytics/nodes/{NODE}/hourly")
    assert r.status_code == 503
    assert "db unavailable" in r.json()["detail"]
