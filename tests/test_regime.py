"""
Tests for THE REGIME PACKAGE v0 (2026-07-30) — Analytics Room 1, second course:

  * GET /api/analytics/node/{pnode_id}/ladder   the percentile ladder + chip
  * GET /api/analytics/node/{pnode_id}/grid     the HE x date heat grid
  * GET /api/analytics/tb4                      the top-bottom-4 spread
  * GET /api/analytics/movers-grid              top-N nodes x HE1-24
  * the basis classing + stance verdicts folded into the node package

No live database. Same discipline as tests/test_analytics.py: a tiny in-memory
fake pool dispatches canned rows by inspecting the SQL, and most of the
arithmetic is pinned directly against the pure builders with hand-built rows.

WHAT THESE TESTS EXIST TO PROTECT. Five properties are silent on the server and
either wrong or dishonest on the client, so each gets its own test:

  * THE DEPTH FLOOR IS SERVED, NOT CAPTIONED. Below 15 samples a ladder row
    carries min/mid/max and the percentile keys DO NOT EXIST on it — absent,
    never nulled, because `row.p95 ?? fallback` renders the fallback while
    `"p95" in row` cannot be misread. The chip is absent for the same reason: a
    rank against six samples is not a rank.
  * ONE PERCENTILE DEFINITION. The ladder's columns are `_an_percentile_cont`
    (the envelope's own function) and the chip's rank is its exact INVERSE.
    Both are cross-checked against `_an_envelope` on identical rows. A second
    percentile definition in this department is the defect this guards.
  * BASIS CLASSING IS THE DASHBOARD'S GATE. `computeBasisStance` has shipped as
    a null-returning detector reading `band ?? class` through `classifyBand`.
    The six class names and the context strings are pinned against that
    module's vocabulary verbatim; drift here silently un-lights a panel.
  * A VERDICT NEVER SHIPS WITHOUT ITS COUNTS. There is no code path producing a
    bare BULLISH, and an unclassable panel is an ABSENCE, not a NEUTRAL.
  * GRIDS ARE NEVER PADDED. `dates` is exactly what the pantry holds; an absent
    cell is null, never zero — zero reads as "no spread", not "no data".

Four tests carry literal figures re-derived BY HAND from rows read out of the
live pantry on 2026-07-30 (`test_acceptance_*`): one full ladder row, one
regime chip rank, one TB4 day with its 4+4 hours named, and one movers-grid
cell. The pantry cannot supply 15 complete days today — the hot tier holds
eight dates — so the FULL-ladder acceptance rides a constructed 21-day series
whose percentiles are exact by hand, while the depth-floor acceptance rides the
real seven-sample pantry rows. Both are labelled as such below.
"""

import datetime

import psycopg
import pytest
from fastapi.testclient import TestClient

import main


UTC = datetime.timezone.utc
D = datetime.date

NODE = "ALAMT3G_7_B1"


# ---------------------------------------------------------------------------
# Fake pool
# ---------------------------------------------------------------------------

class _FakeCursor:
    def __init__(self, router, sink, state):
        self._router, self._sink, self._state = router, sink, state
        self._rows = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, query, params=None):
        self._sink["queries"].append(query)
        self._sink["params"].append(params)
        self._state["attempts"] += 1
        if self._state["attempts"] <= self._state["fail_times"]:
            raise self._state["exc"]
        self._rows = self._router(query, params or {})

    async def fetchall(self):
        return list(self._rows)


class _FakeConn:
    def __init__(self, router, sink, state):
        self._router, self._sink, self._state = router, sink, state

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def cursor(self):
        return _FakeCursor(self._router, self._sink, self._state)


class FakePool:
    def __init__(self, router, fail_times=0, exc=None):
        self._router = router
        self.sink = {"queries": [], "params": []}
        self.state = {
            "attempts": 0,
            "fail_times": fail_times,
            "exc": exc or psycopg.OperationalError("SSL connection has been closed unexpectedly"),
        }

    def connection(self):
        return _FakeConn(self._router, self.sink, self.state)


@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.fixture(autouse=True)
def _clear_caches():
    for cache in (main._an_movers_cache, main._an_tb4_cache,
                  main._an_movers_grid_cache):
        cache.clear()
    yield
    for cache in (main._an_movers_cache, main._an_tb4_cache,
                  main._an_movers_grid_cache):
        cache.clear()


def use_router(router, fail_times=0, exc=None):
    pool = FakePool(router, fail_times=fail_times, exc=exc)
    main._pool = pool
    return pool


# ---------------------------------------------------------------------------
# Row builders
# ---------------------------------------------------------------------------

def depth_rows(da=("2026-07-23", "2026-07-30"), rt=("2026-07-23", "2026-07-30")):
    out = []
    for market, span in ((main.AN_DA_MARKET, da), (main.AN_RT_MARKET, rt)):
        out.append({"market": market,
                    "min_date": D.fromisoformat(span[0]) if span else None,
                    "max_date": D.fromisoformat(span[1]) if span else None})
    return out


def identity_row(pnode_id=NODE, priced=True, th_hub="TH_SP15_GEN-APND"):
    return {
        "pnode_id": pnode_id, "in_universe": True, "priced": priced,
        "th_hub": th_hub, "apnode_type": None,
        "node_type": "GEN" if priced else None,
        "area": "CA" if priced else None,
        "min_date": D(2026, 7, 23) if priced else None,
        "max_date": D(2026, 7, 30) if priced else None,
        "latitude": 33.765978, "longitude": -118.103663,
        "geocode_status": "ok", "plant_name": "AES Alamitos LLC",
    }


def da_row(day, he, lmp, month=7):
    return {"market_date": D(2026, month, day), "market_hour": he,
            "market_interval": 0, "lmp": lmp, "energy": None,
            "congestion": None, "loss": None, "ghg": None}


def rt_row(day, he, iv, lmp, month=7):
    return {"market_date": D(2026, month, day), "market_hour": he,
            "market_interval": iv, "lmp": lmp, "energy": None,
            "congestion": None, "loss": None, "ghg": None}


def hub_row(day, he, value, month=7):
    return {"d": D(2026, month, day), "he": he, "value": value}


def tb4_hub_row(day, n_hours, top, bottom, month=7):
    return {"d": D(2026, month, day), "n_hours": n_hours,
            "top_mean": top, "bottom_mean": bottom}


def node_he_row(pnode_id, day, he, v, k=1, month=7):
    return {"pnode_id": pnode_id, "d": D(2026, month, day), "he": he,
            "v": v, "k": k}


def make_router(depth=None, ident=None, da=None, rt=None, hub=None,
                tb4=None, node_he=None, grid=None, pairing=None, movers=None,
                ref_node="AAA"):
    """Dispatch a read to its canned rows by looking at the SQL. Order matters:
    the Regime lane's per-node-per-HE read also mentions `market = %(market)s`,
    so it must be recognised before the generic leg read."""
    def router(query, params):
        q = query
        if "FROM (VALUES (%(da)s), (%(rt)s)) AS m(market)" in q:
            return depth if depth is not None else depth_rows()
        if "in_universe" in q:
            return [ident] if ident is not None else [identity_row()]
        if "array_agg(value::float8 ORDER BY value)" in q:
            return tb4 if tb4 is not None else []
        if "FROM timeseries_values" in q:
            return hub if hub is not None else []
        if "pnode_id = ANY(%(nodes)s)" in q:
            if callable(node_he):
                return node_he(query, params)
            return node_he if node_he is not None else []
        if "GROUP BY 1, 2" in q and "count(*)::float8 AS k" in q:
            return grid if grid is not None else []
        if "LIMIT 1" in q and "s.pnode_id" in q and "ORDER BY s.pnode_id" in q:
            return [{"pnode_id": ref_node}]
        if "FROM atlas_pnode_universe" in q and "th_hub = ANY" in q:
            return pairing if pairing is not None else []
        if "weighted_sum" in q:
            return movers(query, params) if callable(movers) else (movers or [])
        if "market = %(market)s" in q:
            if params.get("market") == main.AN_DA_MARKET:
                return da if da is not None else []
            return rt if rt is not None else []
        raise AssertionError(f"unrouted query: {q[:200]}")
    return router


# ===========================================================================
# THE PANTRY'S OWN NUMBERS — read 2026-07-30, used by the acceptance tests
# ===========================================================================
#
# ALAMT3G_7_B1 (AES Alamitos, GEN, area CA, paired TH_SP15_GEN-APND -> SP15),
# HE20, every banked day the hot tier held. `k` is the real RTPD interval count
# for that hour; the DART figures are what the pantry returns for
# (avg(RTPD) - DAM) at that hour.
#
#   date        DA lmp      RTPD hourly mean   k   DART
#   2026-07-23  84.12518      99.2353375       4     15.1101575
#   2026-07-24  86.53285    1105.628775        4   1019.0959250
#   2026-07-25  75.37830      94.6906366667    3     19.3123367
#   2026-07-26  83.68728      67.429435        4    -16.2578450
#   2026-07-27  94.25102      90.7823925       4     -3.4686275
#   2026-07-28  96.66384      70.2441875       4    -26.4196525
#   2026-07-29  99.50456      63.56379         4    -35.9407700
#
# 2026-07-29's four RTPD intervals are the real ones (they are the Nodes lane's
# own hand-derivation); the other days' intervals are reconstructed as k copies
# of the measured hourly mean, which reproduces the measured DART exactly and
# is the only figure the ladder consumes.
HE20 = 20
PANTRY_HE20 = [
    # (day, da_lmp, rt_hourly_mean, k)
    (23, 84.12518, 99.2353375, 4),
    (24, 86.53285, 1105.628775, 4),
    (25, 75.37830, 94.69063666666667, 3),
    (26, 83.68728, 67.429435, 4),
    (27, 94.25102, 90.7823925, 4),
    (28, 96.66384, 70.2441875, 4),
    (29, 99.50456, None, 4),   # real intervals below
]
PANTRY_HE20_REAL_INTERVALS = [65.27968, 62.13957, 63.56517, 63.27074]


def pantry_he20_rows():
    """The measured HE20 rows, as the leg reads would hand them back."""
    da, rt = [], []
    for day, da_lmp, rt_mean, k in PANTRY_HE20:
        da.append(da_row(day, HE20, da_lmp))
        if rt_mean is None:
            for i, v in enumerate(PANTRY_HE20_REAL_INTERVALS, start=1):
                rt.append(rt_row(day, HE20, i, v))
        else:
            for i in range(1, k + 1):
                rt.append(rt_row(day, HE20, i, rt_mean))
    return da, rt


# SP15 day-ahead LMPs for 2026-07-29, all 24 hours, read from
# timeseries_values (dataset caiso_lmp_da_hourly). Used by the TB4 acceptance.
SP15_2026_07_29 = {
    1: 57.790700, 2: 51.558860, 3: 46.600300, 4: 46.253500,
    5: 46.550980, 6: 53.088340, 7: 56.622160, 8: 40.118800,
    9: 37.372350, 10: 37.308310, 11: 37.143810, 12: 37.356590,
    13: 37.466270, 14: 37.814640, 15: 38.348400, 16: 40.303960,
    17: 41.605580, 18: 58.923240, 19: 65.475550, 20: 86.105330,
    21: 75.525770, 22: 72.321160, 23: 64.032250, 24: 59.037650,
}


# ===========================================================================
# ONE PERCENTILE DEFINITION — the rank is the exact inverse of the column
# ===========================================================================

@pytest.mark.parametrize("vals", [
    [0.0, 10.0, 20.0, 30.0, 40.0],
    [-35.94077, -26.4196525, -16.257845, -3.4686275, 15.1101575, 19.3123367,
     1019.095925],
    [1.5, 1.5, 2.0, 9.0, 100.0],
    [7.0],
])
def test_percentile_rank_is_the_exact_inverse_of_percentile_cont(vals):
    """percentile_cont(rank(v)) == v for every sample in the list. This is the
    property that makes 'the chip's rank' and 'the ladder's columns' two views
    of ONE distribution rather than two definitions that usually agree."""
    s = sorted(vals)
    for v in s:
        q = main._an_percentile_rank(s, v)
        assert main._an_percentile_cont(s, q) == pytest.approx(v, abs=1e-9)


def test_percentile_rank_inverts_a_value_that_falls_inside_a_gap():
    s = [0.0, 10.0, 20.0, 30.0, 40.0]
    # 24 sits 40% of the way from s[2]=20 to s[3]=30 -> (2 + 0.4) / 4 = 0.6
    assert main._an_percentile_rank(s, 24.0) == pytest.approx(0.6)
    assert main._an_percentile_cont(s, 0.6) == pytest.approx(24.0)


def test_percentile_rank_of_a_tie_run_is_its_midpoint():
    """Taking the first index would report the maximum of an all-equal sample
    as P0 — the tie run's midpoint is what keeps a flat hour reading MID."""
    s = [1.0, 5.0, 5.0, 5.0, 9.0]
    assert main._an_percentile_rank(s, 5.0) == pytest.approx(0.5)
    flat = [4.0, 4.0, 4.0, 4.0]
    assert main._an_percentile_rank(flat, 4.0) == pytest.approx(0.5)
    assert main._an_regime_chip(flat, 4.0)["regime"] == "MID"


def test_percentile_rank_clamps_at_the_extremes():
    s = [1.0, 2.0, 3.0]
    assert main._an_percentile_rank(s, 0.0) == 0.0
    assert main._an_percentile_rank(s, 99.0) == 1.0


def test_percentile_rank_of_one_sample_is_the_middle_not_a_verdict():
    """One sample IS the distribution; it has no interior to rank against, so
    it ranks in the middle and the chip reads MID rather than HIGH."""
    assert main._an_percentile_rank([42.0], 42.0) == 0.5
    assert main._an_regime_chip([42.0], 42.0)["regime"] == "MID"


def test_percentile_rank_of_absence_is_absence():
    assert main._an_percentile_rank([], 1.0) is None
    assert main._an_percentile_rank([1.0, 2.0], None) is None
    assert main._an_regime_chip([], 1.0) is None


def test_the_ladder_columns_are_the_envelopes_own_math_on_the_same_rows():
    """THE CROSS-CHECK the lane is required to carry: five of the ladder's nine
    columns are the envelope's five bands, and they must be the same numbers,
    not merely close ones."""
    vals = [3.0, -1.5, 22.0, 8.25, 0.0, 14.75, -6.0, 41.0, 5.5, 2.25,
            18.0, -12.0, 7.0, 30.5, 9.0, 1.0, 26.0, -3.25, 11.5, 16.0]
    entries = [{"date": D(2026, 6, i + 1), "he": 5, "dart": v}
               for i, v in enumerate(vals)]
    row = main._an_ladder_row(5, entries, "dart")
    env = main._an_envelope(vals)
    assert row["vocabulary"] == "percentile"
    for ladder_key, env_key in (("p05", "p5"), ("p25", "p25"), ("p50", "p50"),
                                ("p75", "p75"), ("p95", "p95")):
        assert row[ladder_key] == env[env_key], ladder_key
    assert row["n"] == env["n"] == 20


def test_the_ladder_columns_are_monotone():
    vals = [float(v) for v in range(40, 0, -1)]
    entries = [{"date": D(2026, 6, 1) + datetime.timedelta(days=i), "he": 5,
                "dart": v} for i, v in enumerate(vals)]
    row = main._an_ladder_row(5, entries, "dart")
    ladder = [row[name] for name, _q in main.AN_LADDER_QUANTILES]
    assert ladder == sorted(ladder)


# ===========================================================================
# THE DEPTH FLOOR — served, not captioned
# ===========================================================================

def _entries(vals, he=5, key="dart", start=D(2026, 6, 1)):
    return [{"date": start + datetime.timedelta(days=i), "he": he, key: v}
            for i, v in enumerate(vals)]


def test_the_floor_is_fifteen_and_matches_the_dashboards_percentile_floor():
    """src/components/analytics/analytics.ts exports PERCENTILE_FLOOR = 15. If
    these two ever disagree the room refuses percentile words while the server
    prints them (or the reverse), and nothing errors."""
    assert main.AN_PERCENTILE_FLOOR == 15


@pytest.mark.parametrize("n", [1, 6, 8, 14])
def test_below_the_floor_no_percentile_key_exists_at_all(n):
    """Absent, NOT nulled. `row.p95 ?? fallback` renders the fallback and looks
    fine; `"p95" in row` is a fact a client cannot misread."""
    row = main._an_ladder_row(5, _entries([float(i) for i in range(n)]), "dart")
    assert row["vocabulary"] == "range"
    for name, _q in main.AN_LADDER_QUANTILES:
        assert name not in row, f"{name} leaked at n={n}"
    assert set(("min", "mid", "max")) <= set(row)
    assert row["n"] == n


@pytest.mark.parametrize("n", [1, 6, 8, 14])
def test_below_the_floor_the_chip_key_is_absent(n):
    """A rank against six samples is not a rank."""
    row = main._an_ladder_row(5, _entries([float(i) for i in range(n)]), "dart")
    assert "chip" not in row


@pytest.mark.parametrize("n", [15, 16, 30])
def test_at_or_above_the_floor_the_full_ladder_and_the_chip_appear(n):
    row = main._an_ladder_row(5, _entries([float(i) for i in range(n)]), "dart")
    assert row["vocabulary"] == "percentile"
    for name, _q in main.AN_LADDER_QUANTILES:
        assert name in row
    assert "min" not in row and "mid" not in row and "max" not in row
    assert row["chip"]["regime"] in ("HIGH", "MID", "LOW")


def test_the_floor_is_the_only_thing_that_switches_the_shape():
    """Fourteen samples and fifteen identical-looking samples must differ ONLY
    by the floor — the shape flip is a depth decision, never a data one."""
    lo = main._an_ladder_row(5, _entries([float(i) for i in range(14)]), "dart")
    hi = main._an_ladder_row(5, _entries([float(i) for i in range(15)]), "dart")
    assert lo["vocabulary"] == "range" and hi["vocabulary"] == "percentile"
    assert set(lo) & {"p50", "chip"} == set()
    assert {"p50", "chip"} <= set(hi)


def test_every_row_states_its_n_and_its_current_at_both_depths():
    for n in (7, 21):
        row = main._an_ladder_row(5, _entries([float(i) for i in range(n)]), "dart")
        assert row["n"] == n
        assert row["current"] == float(n - 1)
        assert row["current_date"] is not None


def test_current_is_the_latest_day_that_hour_has_not_the_nodes_latest_day():
    """The banked frontier is ragged: a mid-dispatch day holds HE1..HE16. HE20's
    current must be yesterday's HE20, not a null because today has no HE20."""
    series = ([{"date": D(2026, 7, 29), "he": 20, "dart": 5.0},
               {"date": D(2026, 7, 30), "he": 3, "dart": 1.0},
               {"date": D(2026, 7, 29), "he": 3, "dart": 2.0}])
    out = main._an_build_ladder("dart", series)
    by_he = {r["he"]: r for r in out["per_he"]}
    assert by_he[20]["current_date"] == "2026-07-29"
    assert by_he[3]["current_date"] == "2026-07-30"


# ===========================================================================
# THE REGIME CHIP
# ===========================================================================

@pytest.mark.parametrize("current,regime,pct", [
    (200.0, "HIGH", 100),
    (150.0, "HIGH", 75),
    (140.0, "MID", 70),
    (100.0, "MID", 50),
    (60.0, "MID", 30),
    (50.0, "LOW", 25),
    (0.0, "LOW", 0),
])
def test_chip_bins_on_the_rounded_rank(current, regime, pct):
    """21 evenly spaced samples 0..200: rank(v) = v/200 exactly, so the bins are
    hand-checkable. Binning on the ROUNDED percent is what stops a raw 0.748
    from printing 'MID (P75)'."""
    s = [float(10 * i) for i in range(21)]
    chip = main._an_regime_chip(s, current)
    assert chip["regime"] == regime
    assert chip["rank"] == pct
    assert chip["label"] == f"{regime} (P{pct})"


def test_chip_carries_the_exact_rank_in_its_label():
    s = [float(10 * i) for i in range(21)]
    assert main._an_regime_chip(s, 190.0)["label"] == "HIGH (P95)"
    assert main._an_regime_chip(s, 20.0)["label"] == "LOW (P10)"


def test_chip_ranks_against_that_hours_own_history_never_the_nodes():
    """Same value, two hours, two different histories -> two different chips.
    A chip computed against the node's pooled hours would return one."""
    series = (
        [{"date": D(2026, 6, 1) + datetime.timedelta(days=i), "he": 5,
          "dart": float(i)} for i in range(20)]
        + [{"date": D(2026, 6, 1) + datetime.timedelta(days=i), "he": 6,
            "dart": float(100 - i)} for i in range(20)]
    )
    # HE5's last day is its maximum; HE6's last day is its minimum.
    out = main._an_build_ladder("dart", series)
    by_he = {r["he"]: r for r in out["per_he"]}
    assert by_he[5]["chip"]["regime"] == "HIGH"
    assert by_he[6]["chip"]["regime"] == "LOW"


def test_chip_rule_is_serialized_with_the_chip():
    s = [float(10 * i) for i in range(21)]
    assert "HIGH" in main._an_regime_chip(s, 200.0)["rule"]
    assert str(main.AN_REGIME_HIGH_PCT) in main.AN_REGIME_RULE


# ===========================================================================
# ACCEPTANCE — hand-derived, and labelled where the depth had to be constructed
# ===========================================================================

def test_acceptance_full_ladder_row_derived_by_hand():
    """ONE FULL LADDER ROW, every percentile derived by hand.

    The hot tier holds EIGHT dates, so the pantry cannot supply a row at or
    above the depth floor today; this acceptance therefore rides a CONSTRUCTED
    21-day series whose percentiles are exact by hand. The sub-floor acceptance
    below rides the real pantry rows.

    Samples: v_i = 10i for i = 0..20, so n = 21 and n-1 = 20, evenly spaced.
    percentile_cont(q) interpolates at index q*(n-1) = 20q, and because the
    spacing is uniform the value at index x is exactly 10x:

        P01  20 x 0.01 = 0.2  -> 10 x 0.2  =   2
        P05  20 x 0.05 = 1.0  -> 10 x 1.0  =  10
        P10  20 x 0.10 = 2.0  -> 10 x 2.0  =  20
        P25  20 x 0.25 = 5.0  -> 10 x 5.0  =  50
        P50  20 x 0.50 = 10.0 -> 10 x 10.0 = 100
        P75  20 x 0.75 = 15.0 -> 10 x 15.0 = 150
        P90  20 x 0.90 = 18.0 -> 10 x 18.0 = 180
        P95  20 x 0.95 = 19.0 -> 10 x 19.0 = 190
        P99  20 x 0.99 = 19.8 -> 190 + 0.8 x (200 - 190) = 198

    The dates are laid out so the LATEST day carries 190, which is the chip
    acceptance immediately below.
    """
    values = [float(10 * i) for i in range(19)] + [200.0, 190.0]
    entries = [{"date": D(2026, 6, 10) + datetime.timedelta(days=i), "he": 20,
                "dart": v} for i, v in enumerate(values)]
    row = main._an_ladder_row(20, entries, "dart")

    assert row["n"] == 21
    assert row["vocabulary"] == "percentile"
    assert row["p01"] == 2.0
    assert row["p05"] == 10.0
    assert row["p10"] == 20.0
    assert row["p25"] == 50.0
    assert row["p50"] == 100.0
    assert row["p75"] == 150.0
    assert row["p90"] == 180.0
    assert row["p95"] == 190.0
    assert row["p99"] == 198.0
    assert row["current"] == 190.0
    assert row["current_date"] == "2026-06-30"


def test_acceptance_regime_chip_rank_derived_by_hand():
    """ONE REGIME CHIP RANK, by hand, on the same constructed series.

    Sorted, the samples are 0, 10, ..., 200 — twenty-one values, so the ranks
    available are k/20 for k = 0..20. The current value is 190, which sits at
    index 19 and appears exactly once, so

        rank = 19 / 20 = 0.95  ->  P95  ->  95 >= 75  ->  HIGH (P95)

    and the round trip holds: percentile_cont(0.95) reads index 20 x 0.95 = 19,
    which is 190 — the value we started from.
    """
    values = [float(10 * i) for i in range(19)] + [200.0, 190.0]
    s = sorted(values)
    assert main._an_percentile_rank(s, 190.0) == pytest.approx(0.95)
    assert main._an_percentile_cont(s, 0.95) == pytest.approx(190.0)

    entries = [{"date": D(2026, 6, 10) + datetime.timedelta(days=i), "he": 20,
                "dart": v} for i, v in enumerate(values)]
    chip = main._an_ladder_row(20, entries, "dart")["chip"]
    assert chip == {"regime": "HIGH", "rank": 95, "label": "HIGH (P95)",
                    "rule": main.AN_REGIME_RULE}


def test_acceptance_real_pantry_he20_is_a_range_row_with_no_chip():
    """THE REAL ROWS, at the depth the pantry actually holds (2026-07-30).

    ALAMT3G_7_B1 HE20 banked seven DART values (see the table at the top of this
    file). Sorted ascending:

        -35.94077, -26.4196525, -16.257845, -3.4686275,
         15.1101575, 19.3123367, 1019.095925

    n = 7, which is below the floor of 15, so by hand:

        min = -35.94077              (2026-07-29, the lowest of the seven)
        mid = -3.4686275             (the 4th of 7 — the median)
        max = 1019.095925            (2026-07-24's scarcity evening)

    and the row carries NO percentile column and NO chip. `current` is
    2026-07-29's -35.94077: real time settled $35.94 below the day-ahead
    schedule that hour, which is the correct reading of the ratified sign.
    """
    da, rt = pantry_he20_rows()
    series = main._an_dart_series(da, rt)
    assert len(series) == 7

    row = main._an_ladder_row(HE20, series, "dart")
    assert row["n"] == 7
    assert row["vocabulary"] == "range"
    assert row["min"] == -35.9408
    assert row["mid"] == -3.4686
    assert row["max"] == 1019.0959
    assert row["current"] == -35.9408
    assert row["current_date"] == "2026-07-29"
    assert "chip" not in row
    for name, _q in main.AN_LADDER_QUANTILES:
        assert name not in row

    # And the DART the row is built on is the pantry's own, to 4 dp.
    darts = {e["date"].isoformat(): round(e["dart"], 4) for e in series}
    assert darts["2026-07-29"] == -35.9408
    assert darts["2026-07-24"] == 1019.0959
    assert darts["2026-07-25"] == 19.3123


def test_acceptance_tb4_day_named_hours_and_arithmetic():
    """ONE TB4 DAY, hours named, arithmetic shown.

    SP15 day-ahead, 2026-07-29, all 24 hours banked (see SP15_2026_07_29).

    The four HIGHEST hours are the evening peak:
        HE20  86.105330
        HE21  75.525770
        HE22  72.321160
        HE19  65.475550   (HE23's 64.032250 is next, and is excluded)
        sum = 299.427810  ->  top-4 mean = 74.85695250

    The four LOWEST hours are the solar midday belly:
        HE11  37.143810
        HE10  37.308310
        HE12  37.356590
        HE9   37.372350   (HE13's 37.466270 is next, and is excluded)
        sum = 149.181060  ->  bottom-4 mean = 37.29526500

    TB4 = 74.85695250 - 37.29526500 = 37.56168750  ->  37.5617 on the wire.
    """
    top_hours = [20, 21, 22, 19]
    bottom_hours = [11, 10, 12, 9]
    assert sorted(SP15_2026_07_29, key=SP15_2026_07_29.get,
                  reverse=True)[:4] == top_hours
    assert sorted(SP15_2026_07_29, key=SP15_2026_07_29.get)[:4] == bottom_hours

    top_sum = sum(SP15_2026_07_29[h] for h in top_hours)
    bottom_sum = sum(SP15_2026_07_29[h] for h in bottom_hours)
    assert top_sum == pytest.approx(299.427810)
    assert bottom_sum == pytest.approx(149.181060)

    day = main._an_tb4_day(list(SP15_2026_07_29.values()))
    assert day["hours"] == 24
    assert day["top_mean"] == pytest.approx(74.85695250)
    assert day["bottom_mean"] == pytest.approx(37.29526500)
    assert day["tb4"] == pytest.approx(37.56168750)
    assert main._an_r(day["tb4"]) == 37.5617


# ===========================================================================
# BASIS CLASSING — the dashboard's gate
# ===========================================================================

def test_basis_per_he_rows_carry_a_server_supplied_class_and_context():
    """computeBasisStance ships as a gated detector reading `band ?? class`
    off each per_he row. These two fields ARE the gate."""
    da = [da_row(d, 5, 60.0 + d) for d in range(23, 30)]
    hub = [hub_row(d, 5, 50.0) for d in range(23, 30)]
    basis = main._an_build_basis(da, hub, main._an_pair_hub("TH_SP15_GEN-APND"))
    row = basis["per_he"][0]
    assert row["class"] in main.AN_STANCE_UPPER_CLASSES
    assert row["context"] == "above the observed range"
    assert row["current_date"] == "2026-07-29"
    assert row["class_n"] == 7


def test_basis_class_vocabulary_is_the_same_six_names_the_dart_band_uses():
    """One class vocabulary across the department — the dashboard's
    classifyBand() recognises exactly these six and nothing else."""
    six = set(main.AN_STANCE_UPPER_CLASSES + main.AN_STANCE_MIDDLE_CLASSES
              + main.AN_STANCE_LOWER_CLASSES)
    assert six == {"above_p95", "p75_p95", "p50_p75", "p25_p50", "p5_p25",
                   "below_p5"}
    env = main._an_envelope([1.0, 2.0, 3.0, 4.0, 5.0])
    produced = {main._an_band_of(v, env) for v in (0.0, 1.6, 2.6, 3.4, 4.4, 9.0)}
    assert produced <= six


@pytest.mark.parametrize("band,below,above", [
    ("above_p95", "above the observed range", "above p95"),
    ("p75_p95", "upper range", "p75–p95"),
    ("p50_p75", "above median", "p50–p75"),
    ("p25_p50", "below median", "p25–p50"),
    ("p5_p25", "lower range", "p5–p25"),
    ("below_p5", "below the observed range", "below p5"),
])
def test_context_mirrors_the_dashboards_band_label_at_both_depths(band, below, above):
    """Verbatim mirror of bandLabel(band, n) in
    src/components/analytics/analytics.ts. Percentile words are earned at the
    floor and refused below it — on the SERVER, so a consumer that renders the
    string blind still reads honestly."""
    assert main._an_context_label(band, 7) == below
    assert main._an_context_label(band, main.AN_PERCENTILE_FLOOR) == above


def test_context_of_an_unknown_or_absent_class_does_not_invent_one():
    assert main._an_context_label(None, 7) is None
    assert main._an_context_label("", 20) is None
    assert main._an_context_label("sideways", 7) == "within range"


def test_basis_classing_is_against_that_hours_own_history():
    """HE5 rises to its own maximum; HE6 falls to its own minimum. The same
    node, the same window, two opposite classes."""
    da = ([da_row(d, 5, 50.0 + d) for d in range(23, 30)]
          + [da_row(d, 6, 100.0 - d) for d in range(23, 30)])
    hub = ([hub_row(d, 5, 40.0) for d in range(23, 30)]
           + [hub_row(d, 6, 40.0) for d in range(23, 30)])
    basis = main._an_build_basis(da, hub, main._an_pair_hub("TH_SP15_GEN-APND"))
    by_he = {r["he"]: r for r in basis["per_he"]}
    assert main._an_stance_side(by_he[5]["class"]) == "upper"
    assert main._an_stance_side(by_he[6]["class"]) == "lower"


def test_basis_classing_never_appears_without_a_priceable_hub():
    basis = main._an_build_basis([da_row(29, 5, 60.0)], [],
                                 main._an_pair_hub(None))
    assert basis["available"] is False
    assert basis["per_he"] == []
    assert basis["stance"]["available"] is False
    assert "no CAISO trading hub" in basis["stance"]["reason"]


# ===========================================================================
# STANCE VERDICTS — a verdict never ships without its counts
# ===========================================================================

def test_stance_counts_quartile_extremes_and_maps_the_verdict():
    up = main._an_stance(["above_p95", "p75_p95", "p50_p75", "p5_p25"], "none")
    assert up["verdict"] == "BULLISH"
    assert (up["upper"], up["lower"], up["middle"], up["n"]) == (2, 1, 1, 4)

    down = main._an_stance(["below_p5", "p5_p25", "p25_p50", "above_p95"], "none")
    assert down["verdict"] == "BEARISH"
    assert (down["upper"], down["lower"]) == (1, 2)


def test_stance_ties_are_neutral_not_a_coin_flip():
    tie = main._an_stance(["above_p95", "below_p5"], "none")
    assert tie["verdict"] == "NEUTRAL"
    assert tie["upper"] == tie["lower"] == 1


def test_stance_line_is_the_dashboards_format_verbatim():
    """stance.ts builds `${upper}h upper / ${lower}h lower of ${n} banked →
    ${read}`. Two computations of one truth must agree byte for byte."""
    s = main._an_stance(["above_p95", "p75_p95", "p50_p75", "below_p5"], "none")
    assert s["line"] == ("2h upper / 1h lower of 4 banked → real time ran above "
                         "its banked shape in more hours than below")


def test_a_verdict_is_never_serialized_without_its_counts():
    """There is no code path that produces a bare BULLISH."""
    for bands in (["above_p95"], ["below_p5"], ["p50_p75", "p25_p50"],
                  ["above_p95", "below_p5", "p50_p75"]):
        s = main._an_stance(bands, "none")
        assert "verdict" in s
        for key in ("upper", "lower", "middle", "n", "line", "rule"):
            assert key in s, key
        assert str(s["upper"]) in s["line"] and str(s["n"]) in s["line"]


def test_an_unclassable_panel_is_an_absence_not_a_neutral():
    """NEUTRAL means 'counted, and it split evenly'. Nothing counted is a
    different statement and must not wear the same word."""
    s = main._an_stance([], "nothing carries a class")
    assert s == {"available": False, "reason": "nothing carries a class"}
    assert "verdict" not in s
    assert main._an_stance([None, "sideways"], "unrecognised")["available"] is False


def test_unrecognised_classes_are_counted_nowhere():
    s = main._an_stance(["above_p95", "sideways", None, "below_p5"], "none")
    assert s["n"] == 2


def test_dart_stance_counts_the_latest_days_classes():
    da, rt = pantry_he20_rows()
    dart = main._an_build_dart(da, rt)
    bands = [h["band"] for h in dart["latest_day"]["hours"]]
    assert dart["stance"]["n"] == len([b for b in bands if b])
    assert dart["stance"]["verdict"] in ("BULLISH", "BEARISH", "NEUTRAL")
    # 2026-07-29's HE20 DART is the lowest of the seven banked days.
    assert dart["stance"]["verdict"] == "BEARISH"
    assert dart["stance"]["line"].startswith("0h upper / 1h lower of 1 banked")


def test_basis_stance_counts_the_per_he_classes():
    da = ([da_row(d, he, 60.0 + d) for d in range(23, 30) for he in (5, 6)])
    hub = ([hub_row(d, he, 50.0) for d in range(23, 30) for he in (5, 6)])
    basis = main._an_build_basis(da, hub, main._an_pair_hub("TH_SP15_GEN-APND"))
    assert basis["stance"]["n"] == 2
    assert basis["stance"]["verdict"] == "BULLISH"
    assert basis["stance"]["upper"] == 2


def test_dart_stance_of_an_empty_series_is_an_absence():
    dart = main._an_build_dart([], [])
    assert dart["stance"]["available"] is False
    assert "verdict" not in dart["stance"]


# ===========================================================================
# THE HEAT GRID
# ===========================================================================

def _grid_series(days=(23, 24, 25), hours=(1, 2, 3)):
    return [{"date": D(2026, 7, d), "he": he, "dart": float(d * 100 + he)}
            for d in days for he in hours]


def test_grid_dates_are_exactly_what_is_banked_never_padded():
    """Gaps in the calendar stay gaps. The grid widens as depth banks; it never
    invents a column to square the matrix."""
    series = _grid_series(days=(23, 25, 30))
    grid = main._an_build_heat_grid("dart", series)
    assert grid["dates"] == ["2026-07-23", "2026-07-25", "2026-07-30"]
    assert grid["n_dates"] == 3
    assert all(len(r["cells"]) == 3 for r in grid["rows"])


def test_grid_rows_are_the_full_he_axis_so_the_shape_holds():
    grid = main._an_build_heat_grid("dart", _grid_series(hours=(1, 2)))
    assert [r["he"] for r in grid["rows"]] == list(range(1, 25))
    empty = [r for r in grid["rows"] if r["he"] == 24][0]
    assert empty["cells"] == [None, None, None]
    assert empty["row_avg"] is None and empty["n"] == 0


def test_grid_absent_cell_is_null_never_zero():
    """A zero reads as 'no spread'; a null reads as 'no data'. They are not the
    same statement and a heat grid colours them very differently."""
    series = [{"date": D(2026, 7, 23), "he": 1, "dart": 5.0},
              {"date": D(2026, 7, 24), "he": 2, "dart": 7.0}]
    grid = main._an_build_heat_grid("dart", series)
    by_he = {r["he"]: r for r in grid["rows"]}
    assert by_he[1]["cells"] == [5.0, None]
    assert by_he[2]["cells"] == [None, 7.0]


def test_grid_margins_are_hand_checkable_and_state_their_n():
    series = [{"date": D(2026, 7, 23), "he": 1, "dart": 10.0},
              {"date": D(2026, 7, 24), "he": 1, "dart": 20.0},
              {"date": D(2026, 7, 23), "he": 2, "dart": 30.0}]
    grid = main._an_build_heat_grid("dart", series)
    by_he = {r["he"]: r for r in grid["rows"]}
    # HE1 across two days: (10 + 20) / 2 = 15, n = 2.
    assert by_he[1]["row_avg"] == 15.0 and by_he[1]["n"] == 2
    # HE2 has one banked day: 30 / 1 = 30, n = 1 — averaged over what exists.
    assert by_he[2]["row_avg"] == 30.0 and by_he[2]["n"] == 1
    # 07-23 across its two banked hours: (10 + 30) / 2 = 20, n = 2.
    assert grid["day_avg"][0] == {"date": "2026-07-23", "avg": 20.0, "n": 2}
    assert grid["day_avg"][1] == {"date": "2026-07-24", "avg": 20.0, "n": 1}


def test_grid_never_averages_a_null_into_a_margin():
    series = [{"date": D(2026, 7, 23), "he": 1, "dart": 10.0}]
    grid = main._an_build_heat_grid("dart", series)
    by_he = {r["he"]: r for r in grid["rows"]}
    assert by_he[1]["row_avg"] == 10.0
    assert by_he[2]["row_avg"] is None


def test_grid_counts_every_banked_cell():
    grid = main._an_build_heat_grid("dart", _grid_series())
    assert grid["n_cells"] == 9


# ===========================================================================
# TB4 — the arithmetic
# ===========================================================================

def test_tb4_is_top_four_minus_bottom_four():
    vals = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0]
    day = main._an_tb4_day(vals)
    # top 4: 50+60+70+80 = 260 / 4 = 65 ; bottom 4: 10+20+30+40 = 100 / 4 = 25
    assert day["top_mean"] == 65.0 and day["bottom_mean"] == 25.0
    assert day["tb4"] == 40.0
    assert day["hours"] == 8


def test_tb4_refuses_a_day_too_short_for_disjoint_baskets():
    """Seven hours would put at least one hour in BOTH baskets, and the number
    would stop meaning anything."""
    assert main._an_tb4_day([float(i) for i in range(7)]) is None
    assert main._an_tb4_day([float(i) for i in range(8)]) is not None
    assert main.AN_TB4_MIN_HOURS == 2 * main.AN_TB4_TOP_N


def test_tb4_short_days_are_dropped_and_counted_never_averaged():
    hours = {D(2026, 7, 23): [float(i) for i in range(24)],
             D(2026, 7, 24): [1.0, 2.0, 3.0],
             D(2026, 7, 25): [float(i) for i in range(10)]}
    daily, short = main._an_tb4_daily_from_hours(hours)
    assert [r["date"] for r in daily] == ["2026-07-23", "2026-07-25"]
    assert short == 1


def test_tb4_nulls_are_skipped_not_read_as_zero():
    day = main._an_tb4_day([10.0, None, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0])
    assert day["hours"] == 8
    assert day["bottom_mean"] == 25.0


def test_tb4_monthly_completeness_is_counted_not_assumed():
    """A month inside the span with a missing day is INCOMPLETE. Thirty banked
    days of June is a full month; twenty-nine is not."""
    june = [{"date": f"2026-06-{d:02d}", "tb4": 10.0} for d in range(1, 31)]
    assert main._an_tb4_monthly(june)[0] == {
        "month": "2026-06", "tb4_mean": 10.0, "n_days": 30, "complete": True}
    short = june[:-1]
    assert main._an_tb4_monthly(short)[0]["complete"] is False
    assert main._an_tb4_monthly(short)[0]["n_days"] == 29


def test_tb4_monthly_mean_is_the_mean_of_its_days():
    daily = [{"date": "2026-06-01", "tb4": 10.0},
             {"date": "2026-06-02", "tb4": 20.0},
             {"date": "2026-07-01", "tb4": 90.0}]
    monthly = main._an_tb4_monthly(daily)
    assert monthly[0]["tb4_mean"] == 15.0 and monthly[0]["n_days"] == 2
    assert monthly[1]["tb4_mean"] == 90.0


def test_tb4_trailing_average_is_honest_n_never_padded():
    daily = [{"date": f"2026-07-{d:02d}", "tb4": float(d)} for d in range(1, 9)]
    avg = main._an_tb4_trailing(daily, 30)
    # Eight banked days, averaged over eight — and it says eight.
    assert avg["n_days"] == 8 and avg["requested_days"] == 30
    assert avg["complete"] is False
    assert avg["value"] == pytest.approx(sum(range(1, 9)) / 8)
    seven = main._an_tb4_trailing(daily, 7)
    assert seven["n_days"] == 7 and seven["complete"] is True
    assert seven["start"] == "2026-07-02" and seven["end"] == "2026-07-08"


def test_tb4_regime_shift_withholds_the_verdict_when_both_windows_are_one_window():
    """MEASURED ON THE LIVE PANTRY, 2026-07-30: with seven banked nodal days the
    7d and 30d windows resolve to the SAME seven days, so the delta is
    necessarily 0.0000 and the flag necessarily false. `flag: false` there reads
    as a finding when it is an artifact of depth, so the verdict is withheld."""
    daily = [{"date": f"2026-07-{d:02d}", "tb4": float(d)} for d in range(24, 31)]
    short = main._an_tb4_trailing(daily, 7)
    long_ = main._an_tb4_trailing(daily, 30)
    assert short["value"] == long_["value"]        # the trap
    shift = main._an_tb4_shift(short, long_)
    assert shift["available"] is False
    assert "flag" not in shift
    assert "same 7 banked days" in shift["reason"]
    # The threshold is still stated, so the rule is visible at any depth.
    assert shift["threshold_fraction"] == main.AN_TB4_SHIFT_FRACTION


def test_tb4_regime_shift_carries_its_threshold_and_its_arithmetic():
    """The flag must be auditable off the payload alone."""
    short = {"value": 60.0, "n_days": 7, "start": "2026-07-25", "end": "2026-07-31"}
    long_ = {"value": 40.0, "n_days": 30, "start": "2026-07-02", "end": "2026-07-31"}
    shift = main._an_tb4_shift(short, long_)
    # |60 - 40| = 20 vs 0.25 x 40 = 10 -> 20 > 10 -> shift.
    assert shift["flag"] is True
    assert shift["delta"] == 20.0
    assert shift["threshold"] == 10.0
    assert shift["threshold_fraction"] == 0.25
    assert "20.0000" in shift["arithmetic"] and "10.0000" in shift["arithmetic"]
    assert shift["arithmetic"].endswith("regime shift")


def test_tb4_regime_shift_is_false_inside_the_threshold():
    shift = main._an_tb4_shift(
        {"value": 44.0, "start": "2026-07-25", "end": "2026-07-31"},
        {"value": 40.0, "start": "2026-07-02", "end": "2026-07-31"})
    # |44 - 40| = 4 vs 10 -> no shift, and the arithmetic says so.
    assert shift["flag"] is False
    assert shift["arithmetic"].endswith("no regime shift")


def test_tb4_regime_shift_is_an_absence_when_a_window_is_empty():
    shift = main._an_tb4_shift({"value": None, "start": None, "end": None},
                               {"value": 40.0, "start": "a", "end": "b"})
    assert shift["available"] is False
    assert "flag" not in shift
    assert shift["threshold_fraction"] == main.AN_TB4_SHIFT_FRACTION


def test_tb4_hub_sql_slices_the_measured_shape_not_a_window_frame():
    """The pinned shape. row_number() window frames cost 12.6x for the same
    answer (see the header note); this guards the regression."""
    sql = main._AN_TB4_HUB_SQL
    assert "array_agg(value::float8 ORDER BY value)" in sql
    assert "row_number()" not in sql
    assert f"d.vals[1:{main.AN_TB4_TOP_N}]" in sql
    assert f"array_length(d.vals, 1) - {main.AN_TB4_TOP_N - 1}" in sql


# ===========================================================================
# ROUTES — the ladder
# ===========================================================================

def _ladder_router(**kw):
    da, rt = pantry_he20_rows()
    kw.setdefault("da", da)
    kw.setdefault("rt", rt)
    kw.setdefault("hub", [hub_row(d, HE20, 50.0) for d in range(23, 30)])
    return make_router(**kw)


def test_ladder_serves_range_rows_at_todays_depth(client):
    use_router(_ladder_router())
    r = client.get(f"/api/analytics/node/{NODE}/ladder", params={"metric": "dart"})
    assert r.status_code == 200
    b = r.json()
    assert b["metric"] == "dart"
    assert b["depth_floor"] == 15
    assert b["available"] is True
    row = b["per_he"][0]
    assert row["he"] == HE20 and row["vocabulary"] == "range"
    assert row["min"] == -35.9408 and row["max"] == 1019.0959
    assert "p50" not in row and "chip" not in row


def test_ladder_serializes_its_convention_and_method_verbatim(client):
    use_router(_ladder_router())
    b = client.get(f"/api/analytics/node/{NODE}/ladder").json()
    assert b["convention"] == "dart = fmm(rtpd) - da; positive = rt above da"
    assert "percentile_cont" in b["percentile_method"]
    assert b["range_definition"] == main.AN_RANGE_DEFINITION
    assert b["chip_rule"] == main.AN_REGIME_RULE


def test_ladder_basis_metric_reads_the_hub_leg(client):
    use_router(_ladder_router())
    b = client.get(f"/api/analytics/node/{NODE}/ladder",
                   params={"metric": "basis"}).json()
    assert b["hub"] == "SP15"
    assert b["available"] is True
    # node DAM - 50.0 at HE20, seven banked days.
    assert b["per_he"][0]["n"] == 7
    assert b["per_he"][0]["current"] == round(99.50456 - 50.0, 4)


def test_ladder_basis_of_an_unpaired_node_is_an_honest_absence(client):
    use_router(_ladder_router(ident=identity_row(th_hub=None), hub=[]))
    r = client.get(f"/api/analytics/node/{NODE}/ladder", params={"metric": "basis"})
    assert r.status_code == 200
    b = r.json()
    assert b["available"] is False
    assert "no CAISO trading hub" in b["reason"]
    assert b["per_he"] == []


def test_ladder_does_not_read_the_rtpd_leg_for_basis(client):
    pool = use_router(_ladder_router())
    client.get(f"/api/analytics/node/{NODE}/ladder", params={"metric": "basis"})
    assert not [p for p in pool.sink["params"]
                if isinstance(p, dict) and p.get("market") == main.AN_RT_MARKET]


@pytest.mark.parametrize("params", [{"metric": "spread"}, {"metric": ""},
                                    {"metric": "rtd"}])
def test_ladder_rejects_an_unknown_metric(client, params):
    use_router(_ladder_router())
    r = client.get(f"/api/analytics/node/{NODE}/ladder", params=params)
    assert r.status_code == 400
    assert "metric must be one of" in r.json()["detail"]


def test_ladder_of_an_unknown_node_is_400(client):
    use_router(_ladder_router(ident=identity_row(priced=False)))
    r = client.get("/api/analytics/node/NOPE/ladder")
    assert r.status_code == 400
    assert "unknown pnode_id" in r.json()["detail"]


def test_ladder_db_down_is_503(client):
    use_router(_ladder_router(), fail_times=99)
    assert client.get(f"/api/analytics/node/{NODE}/ladder").status_code == 503


def test_ladder_carries_depth_and_as_of(client):
    use_router(_ladder_router())
    b = client.get(f"/api/analytics/node/{NODE}/ladder").json()
    assert b["depth"]["depth_days"] == 8
    assert b["depth"]["source"] == "atlas_pnode_lmp_snapshot"
    assert isinstance(b["as_of"], str)


# ===========================================================================
# ROUTES — the heat grid
# ===========================================================================

def test_grid_route_serves_the_matrix_with_both_margins(client):
    use_router(_ladder_router())
    r = client.get(f"/api/analytics/node/{NODE}/grid", params={"metric": "dart"})
    assert r.status_code == 200
    b = r.json()
    assert b["dates"] == [f"2026-07-{d}" for d in range(23, 30)]
    assert b["hours"] == list(range(1, 25))
    assert len(b["rows"]) == 24
    row = [r for r in b["rows"] if r["he"] == HE20][0]
    assert row["cells"][-1] == -35.9408
    assert row["n"] == 7
    assert len(b["day_avg"]) == 7


def test_grid_route_states_its_scale_cap_but_does_not_clip(client):
    use_router(_ladder_router())
    b = client.get(f"/api/analytics/node/{NODE}/grid").json()
    assert b["scale_cap"]["value"] == 15.0
    assert b["scale_cap"]["applies_to"] == "cell colour only"
    row = [r for r in b["rows"] if r["he"] == HE20][0]
    # 2026-07-24's scarcity evening is $1,019 and arrives at $1,019.
    assert max(c for c in row["cells"] if c is not None) == 1019.0959


def test_grid_route_basis_cap_is_ten(client):
    use_router(_ladder_router())
    b = client.get(f"/api/analytics/node/{NODE}/grid",
                   params={"metric": "basis"}).json()
    assert b["scale_cap"]["value"] == 10.0


def test_grid_route_of_an_unpaired_node_is_an_honest_absence(client):
    use_router(_ladder_router(ident=identity_row(th_hub=None), hub=[]))
    b = client.get(f"/api/analytics/node/{NODE}/grid",
                   params={"metric": "basis"}).json()
    assert b["available"] is False
    assert b["rows"] == [] and b["dates"] == []
    assert b["hours"] == list(range(1, 25))


@pytest.mark.parametrize("params", [{"metric": "spread"}, {"metric": "lmp"}])
def test_grid_route_rejects_an_unknown_metric(client, params):
    use_router(_ladder_router())
    assert client.get(f"/api/analytics/node/{NODE}/grid",
                      params=params).status_code == 400


def test_grid_route_db_down_is_503(client):
    use_router(_ladder_router(), fail_times=99)
    assert client.get(f"/api/analytics/node/{NODE}/grid").status_code == 503


# ===========================================================================
# ROUTES — TB4
# ===========================================================================

def _tb4_hub_rows(n=40, start_day=1, month=6):
    """n consecutive daily TB4 rows, tb4 = 10 + day so the averages are
    hand-checkable."""
    out = []
    d = D(2026, month, start_day)
    for i in range(n):
        day = d + datetime.timedelta(days=i)
        out.append({"d": day, "n_hours": 24,
                    "top_mean": 50.0 + i, "bottom_mean": 10.0})
    return out


def test_tb4_hub_serves_daily_monthly_and_the_shift(client):
    use_router(make_router(tb4=_tb4_hub_rows(40)))
    r = client.get("/api/analytics/tb4",
                   params={"scope": "hub", "id": "SP15", "window": "7d"})
    assert r.status_code == 200
    b = r.json()
    assert b["scope"] == "hub" and b["id"] == "SP15" and b["market"] == "DAM"
    assert b["available"] is True
    # window=7d serves the last seven BANKED days; monthly rides full depth.
    assert len(b["daily"]) == 7
    assert b["n_days_banked"] == 40
    assert [m["month"] for m in b["monthly"]] == ["2026-06", "2026-07"]
    assert b["avg_7d"]["n_days"] == 7 and b["avg_30d"]["n_days"] == 30
    assert b["regime_shift"]["available"] is True
    assert "regime shift" in b["regime_shift"]["arithmetic"]
    assert b["cached"] is False and isinstance(b["runtime_ms"], int)


def test_tb4_hub_daily_row_is_top_minus_bottom(client):
    use_router(make_router(tb4=[tb4_hub_row(29, 24, 74.85695250, 37.29526500)]))
    b = client.get("/api/analytics/tb4", params={"scope": "hub", "id": "SP15"}).json()
    assert b["daily"][0] == {"date": "2026-07-29", "tb4": 37.5617,
                             "top4_mean": 74.857, "bottom4_mean": 37.2953,
                             "hours": 24}


def test_tb4_full_window_serves_every_banked_day(client):
    use_router(make_router(tb4=_tb4_hub_rows(40)))
    b = client.get("/api/analytics/tb4",
                   params={"scope": "hub", "id": "SP15", "window": "full"}).json()
    assert len(b["daily"]) == 40
    assert b["window_served"]["days"] == 40


def test_tb4_hub_depth_uses_the_departments_depth_grammar(client):
    """One depth caption across the department, over two different stores."""
    use_router(make_router(tb4=_tb4_hub_rows(40)))
    b = client.get("/api/analytics/tb4", params={"scope": "hub", "id": "SP15"}).json()
    assert set(("source", "retention", "markets", "depth_days")) <= set(b["depth"])
    assert b["depth"]["source"] == "timeseries_values"
    assert b["depth"]["series"] == "SP15"
    assert b["depth"]["tb4_days"] == 40


def test_tb4_node_scope_is_the_same_shape_with_honest_n(client):
    da = [da_row(d, he, float(50 + he)) for d in range(23, 30)
          for he in range(1, 25)]
    use_router(make_router(da=da))
    b = client.get("/api/analytics/tb4",
                   params={"scope": "node", "id": NODE, "window": "30d"}).json()
    assert b["scope"] == "node" and b["hub"] is None
    assert len(b["daily"]) == 7
    # top 4 = HE21..24 -> (71+72+73+74)/4 = 72.5 ; bottom 4 = HE1..4 -> 52.5
    assert b["daily"][0]["tb4"] == 20.0
    assert b["avg_30d"]["n_days"] == 7 and b["avg_30d"]["complete"] is False
    assert b["depth"]["source"] == "atlas_pnode_lmp_snapshot"
    assert b["monthly"][0]["complete"] is False


def test_tb4_node_scope_drops_and_counts_short_days(client):
    da = ([da_row(23, he, 50.0 + he) for he in range(1, 25)]
          + [da_row(24, he, 50.0 + he) for he in range(1, 4)])
    use_router(make_router(da=da))
    b = client.get("/api/analytics/tb4", params={"scope": "node", "id": NODE}).json()
    assert len(b["daily"]) == 1
    assert b["excluded_short_days"] == 1


def test_tb4_node_scope_reads_no_rtpd_leg(client):
    da = [da_row(23, he, 50.0 + he) for he in range(1, 25)]
    pool = use_router(make_router(da=da))
    client.get("/api/analytics/tb4", params={"scope": "node", "id": NODE})
    assert not [p for p in pool.sink["params"]
                if isinstance(p, dict) and p.get("market") == main.AN_RT_MARKET]


@pytest.mark.parametrize("params", [
    {"scope": "zone", "id": "SP15"},
    {"scope": "hub", "id": "SP15", "window": "1d"},
    {"scope": "hub", "id": "SP15", "window": ""},
    {"scope": "hub", "id": "TH_SP15_GEN-APND"},
    {"scope": "hub", "id": ""},
])
def test_tb4_rejects_bad_bounds_rather_than_trimming(client, params):
    use_router(make_router(tb4=_tb4_hub_rows(10)))
    r = client.get("/api/analytics/tb4", params=params)
    assert r.status_code == 400


def test_tb4_unknown_node_is_400(client):
    use_router(make_router(ident=identity_row(priced=False)))
    r = client.get("/api/analytics/tb4", params={"scope": "node", "id": "NOPE"})
    assert r.status_code == 400


def test_tb4_is_cached_per_scope_id_and_window(client):
    use_router(make_router(tb4=_tb4_hub_rows(10)))
    first = client.get("/api/analytics/tb4",
                       params={"scope": "hub", "id": "SP15"}).json()
    second = client.get("/api/analytics/tb4",
                        params={"scope": "hub", "id": "SP15"}).json()
    assert first["cached"] is False and second["cached"] is True
    assert second["daily"] == first["daily"]
    r = client.get("/api/analytics/tb4", params={"scope": "hub", "id": "SP15"})
    assert "max-age" in r.headers["cache-control"]


def test_tb4_db_down_is_503(client):
    use_router(make_router(tb4=_tb4_hub_rows(10)), fail_times=99)
    r = client.get("/api/analytics/tb4", params={"scope": "hub", "id": "SP15"})
    assert r.status_code == 503


def test_tb4_states_its_definition_and_its_requirement(client):
    use_router(make_router(tb4=_tb4_hub_rows(10)))
    b = client.get("/api/analytics/tb4", params={"scope": "hub", "id": "SP15"}).json()
    assert b["definition"] == ("tb4 = mean(top-4 HE LMPs) − mean(bottom-4 HE "
                               "LMPs), per day, DAM")
    assert "at least 8 priced hours" in b["requirements"]
    assert b["query_plan"]["index"] == "idx_tsv_series_ts"


# ===========================================================================
# ROUTES — the movers grid
# ===========================================================================

def _movers_router_rows(query, params):
    """The movers ranking phase: AAA is up (+5 dart), BBB is down (-2)."""
    if params.get("market") == main.AN_DA_MARKET:
        n = len(params["hours"])
        return [{"pnode_id": "AAA", "weighted_sum": 50.0 * n, "n_rows": n},
                {"pnode_id": "BBB", "weighted_sum": 60.0 * n, "n_rows": n}]
    span = params["he_hi"] - params["he_lo"] + 1
    return [{"pnode_id": "AAA", "weighted_sum": 55.0 * span, "n_rows": span * 4},
            {"pnode_id": "BBB", "weighted_sum": 58.0 * span, "n_rows": span * 4}]


def _grid_route_router(node_he=None, **kw):
    grid = kw.pop("grid", None)
    if grid is None:
        grid = [{"d": D(2026, 7, d), "he": he, "k": 4.0}
                for d in (29, 30) for he in range(1, 25)]
    return make_router(grid=grid, movers=_movers_router_rows,
                       node_he=node_he, **kw)


def test_acceptance_movers_grid_cell_derived_by_hand():
    """ONE MOVERS-GRID CELL, by hand.

    Node AAA, metric dart, a two-date window (2026-07-29 and 2026-07-30). At
    HE5 the pantry banked:

        2026-07-29   DA 50.0   FMM 56.0   ->  dart = +6.0
        2026-07-30   DA 60.0   FMM 61.0   ->  dart = +1.0

    The cell is the window mean over the dates that hour is banked:

        (6.0 + 1.0) / 2 = 3.5,  cell_n = 2

    At HE6 only 07-29 is banked (DA 20.0, FMM 32.0), so the cell is 12.0 with
    cell_n = 1 — averaged over what exists, not over an assumed two days. HE7 is
    banked in neither and stays null with cell_n = 0: a null reads as "no
    data", where a zero would read as "no spread".
    """
    da = [node_he_row("AAA", 29, 5, 50.0), node_he_row("AAA", 30, 5, 60.0),
          node_he_row("AAA", 29, 6, 20.0)]
    rt = [node_he_row("AAA", 29, 5, 56.0, k=4), node_he_row("AAA", 30, 5, 61.0, k=4),
          node_he_row("AAA", 29, 6, 32.0, k=4)]
    da_map, rt_map = main._an_he_map(da), main._an_he_map(rt)
    dates = [D(2026, 7, 29), D(2026, 7, 30)]

    def cell(pid, d, he):
        a, b = da_map.get((pid, d, he)), rt_map.get((pid, d, he))
        return None if (a is None or b is None) else b - a

    cells, counts = main._an_grid_cells(["AAA"], dates, cell)["AAA"]
    assert cells[4] == 3.5 and counts[4] == 2       # HE5
    assert cells[5] == 12.0 and counts[5] == 1      # HE6
    assert cells[6] is None and counts[6] == 0      # HE7
    assert len(cells) == 24


def test_movers_grid_serves_top_25_each_way_with_he_columns(client):
    node_he = ([node_he_row(p, d, he, 50.0 + he)
                for p in ("AAA", "BBB") for d in (29, 30) for he in range(1, 25)])

    def route(query, params):
        if params["market"] == main.AN_DA_MARKET:
            return node_he
        return [dict(r, v=r["v"] + 5.0) for r in node_he]

    use_router(_grid_route_router(node_he=route))
    r = client.get("/api/analytics/movers-grid",
                   params={"window": "1d", "metric": "dart"})
    assert r.status_code == 200
    b = r.json()
    assert b["top_n"] == 25
    assert b["hours"] == list(range(1, 25))
    assert [row["pnode_id"] for row in b["up"]] == ["AAA", "BBB"]
    assert b["up"][0]["rank"] == 1 and b["up"][0]["direction"] == "up"
    assert b["down"][0]["direction"] == "down"
    # Every hour banked on the served date: dart = 5.0 flat.
    assert b["up"][0]["cells"] == [5.0] * 24
    assert b["up"][0]["n_cells"] == 24


def test_movers_grid_reuses_the_movers_ranking_verbatim(client):
    use_router(_grid_route_router())
    ranked = client.get("/api/analytics/movers",
                        params={"window": "1d", "metric": "dart"}).json()
    b = client.get("/api/analytics/movers-grid",
                   params={"window": "1d", "metric": "dart"}).json()
    assert [r["pnode_id"] for r in b["up"]] == [e["pnode_id"] for e in ranked["up"]]
    assert b["up"][0]["value"] == ranked["up"][0]["value"]
    assert b["definition"] == ranked["definition"]
    assert b["window_served"] == ranked["window_served"]
    # Phase 1 came out of the movers cache the second time round.
    assert b["ranking_cached"] is True


def test_movers_grid_reads_only_the_ranked_nodes(client):
    seen = {}

    def route(query, params):
        seen["nodes"] = params["nodes"]
        return []

    use_router(_grid_route_router(node_he=route))
    client.get("/api/analytics/movers-grid", params={"window": "1d"})
    assert seen["nodes"] == ["AAA", "BBB"]


def test_movers_grid_cells_are_null_where_nothing_is_banked(client):
    use_router(_grid_route_router(node_he=[]))
    b = client.get("/api/analytics/movers-grid", params={"window": "1d"}).json()
    assert b["up"][0]["cells"] == [None] * 24
    assert b["up"][0]["cell_n"] == [0] * 24
    assert b["up"][0]["n_cells"] == 0


def test_movers_grid_basis_cells_join_the_paired_hub(client):
    node_he = [node_he_row("AAA", 29, he, 60.0) for he in range(1, 25)]
    use_router(_grid_route_router(
        node_he=node_he,
        pairing=[{"pnode_id": "AAA", "th_hub": "TH_SP15_GEN-APND"}],
        hub=[hub_row(29, he, 45.0) for he in range(1, 25)]))
    b = client.get("/api/analytics/movers-grid",
                   params={"window": "1d", "metric": "basis"}).json()
    # 60 - 45 = 15 at every hour, for the one paired node.
    assert b["up"][0]["pnode_id"] == "AAA"
    assert b["up"][0]["cells"] == [15.0] * 24


def test_movers_grid_states_its_two_phase_plan_and_its_runtime(client):
    use_router(_grid_route_router())
    b = client.get("/api/analytics/movers-grid", params={"window": "1d"}).json()
    assert isinstance(b["runtime_ms"], int)
    assert b["cached"] is False
    assert b["query_plan"]["phase_1"]["index"] == "idx_lmp_snap_market_time"
    assert b["query_plan"]["phase_2"]["index"] == "atlas_pnode_lmp_snapshot_pkey"
    assert "no sort, no spill" in b["query_plan"]["phase_2"]["shape"]


def test_movers_grid_names_the_precompute_follow_on_rather_than_building_it(client):
    """The 20-second cold path's durable answer is a pantry-side artifact. It is
    a NAMED FOLLOW-ON, and the payload says so instead of implying this endpoint
    already solved it."""
    use_router(_grid_route_router())
    b = client.get("/api/analytics/movers-grid", params={"window": "1d"}).json()
    assert b["precompute"]["built"] is False
    assert "follow-on lane" in b["precompute"]["note"]


def test_movers_grid_distinguishes_the_value_from_the_row_of_cells(client):
    use_router(_grid_route_router())
    b = client.get("/api/analytics/movers-grid", params={"window": "1d"}).json()
    assert "NOT the mean of this row's cells" in b["value_definition"]
    assert "null where the pantry banked no such hour" in b["cell_definition"]


def test_movers_grid_states_its_scale_cap(client):
    use_router(_grid_route_router())
    b = client.get("/api/analytics/movers-grid",
                   params={"window": "1d", "metric": "dart"}).json()
    assert b["scale_cap"]["value"] == 15.0


def test_movers_grid_is_cached_per_metric_and_window(client):
    use_router(_grid_route_router())
    first = client.get("/api/analytics/movers-grid", params={"window": "1d"}).json()
    second = client.get("/api/analytics/movers-grid", params={"window": "1d"}).json()
    assert first["cached"] is False and second["cached"] is True
    assert second["up"] == first["up"]
    r = client.get("/api/analytics/movers-grid", params={"window": "1d"})
    assert "max-age" in r.headers["cache-control"]


@pytest.mark.parametrize("params", [
    {"window": "30d"}, {"window": ""}, {"metric": "spread"}, {"metric": "rtd"},
])
def test_movers_grid_rejects_unknown_window_or_metric(client, params):
    use_router(_grid_route_router())
    assert client.get("/api/analytics/movers-grid",
                      params=params).status_code == 400


def test_movers_grid_db_down_is_503(client):
    use_router(_grid_route_router(), fail_times=99)
    assert client.get("/api/analytics/movers-grid").status_code == 503


# ===========================================================================
# The package's new regime fields, over the wire
# ===========================================================================

def _package_router(**kw):
    da = kw.pop("da", None)
    if da is None:
        da = [da_row(d, he, 60.0 + he + d) for d in range(23, 30)
              for he in range(1, 25)]
    rt = kw.pop("rt", None)
    if rt is None:
        rt = [rt_row(d, he, iv, 62.0 + he)
              for d in range(23, 30) for he in range(1, 25) for iv in (1, 2, 3, 4)]
    hub = kw.pop("hub", None)
    if hub is None:
        hub = [hub_row(d, he, 50.0 + he) for d in range(23, 30)
               for he in range(1, 25)]
    return make_router(da=da, rt=rt, hub=hub, **kw)


def test_package_basis_rows_now_carry_class_and_context_over_the_wire(client):
    use_router(_package_router())
    b = client.get(f"/api/analytics/node/{NODE}").json()
    rows = b["basis"]["per_he"]
    assert rows and all("class" in r and "context" in r for r in rows)
    assert all(r["class_n"] == 7 for r in rows)
    assert b["basis"]["definition"]["class"].startswith("each hour's latest")


def test_package_carries_a_stance_for_both_metrics(client):
    use_router(_package_router())
    b = client.get(f"/api/analytics/node/{NODE}").json()
    for stanza in ("dart", "basis"):
        stance = b[stanza]["stance"]
        assert stance["available"] is True
        assert stance["verdict"] in ("BULLISH", "NEUTRAL", "BEARISH")
        assert stance["line"].endswith(main.AN_STANCE_READS[stance["verdict"]])
        assert f"of {stance['n']} banked" in stance["line"]


def test_package_stance_is_absent_for_an_unpaired_nodes_basis(client):
    use_router(_package_router(ident=identity_row(th_hub=None), hub=[]))
    b = client.get(f"/api/analytics/node/{NODE}").json()
    assert b["basis"]["stance"]["available"] is False
    assert "verdict" not in b["basis"]["stance"]
    assert b["dart"]["stance"]["available"] is True


def test_package_top_level_keys_are_unchanged(client):
    """The stance objects live INSIDE their metric's stanza. The package's
    top-level contract is pinned by the Nodes lane and is not disturbed."""
    use_router(_package_router())
    b = client.get(f"/api/analytics/node/{NODE}").json()
    assert set(b) == {"pnode_id", "as_of", "depth", "identity", "dart",
                      "basis", "components", "ledger"}


# ===========================================================================
# The client's shape guards — Python mirrors, one per payload
# ===========================================================================

def _passes_is_ladder(body) -> bool:
    """Mirror of the room's isLadder(). The depth floor is part of the GUARD:
    a row must be internally consistent — a `range` row that also carries p50,
    or a `percentile` row missing its columns, is a contract violation the
    client should reject rather than render half of."""
    if not (isinstance(body, dict)
            and isinstance(body.get("pnode_id"), str)
            and isinstance(body.get("metric"), str)
            and isinstance(body.get("as_of"), str)
            and isinstance(body.get("depth"), dict)
            and isinstance(body["depth"].get("depth_days"), int)
            and isinstance(body.get("depth_floor"), int)
            and isinstance(body.get("convention"), str)
            and isinstance(body.get("available"), bool)
            and isinstance(body.get("per_he"), list)):
        return False
    for row in body["per_he"]:
        if not (isinstance(row, dict) and isinstance(row.get("he"), int)
                and isinstance(row.get("n"), int)
                and row.get("vocabulary") in ("range", "percentile")):
            return False
        has_p = any(name in row for name, _q in main.AN_LADDER_QUANTILES)
        if row["vocabulary"] == "range":
            if has_p or "chip" in row:
                return False
            if not all(k in row for k in ("min", "mid", "max")):
                return False
        else:
            if not all(name in row for name, _q in main.AN_LADDER_QUANTILES):
                return False
            if "chip" not in row:
                return False
    return True


def _passes_is_heat_grid(body) -> bool:
    return (
        isinstance(body, dict)
        and isinstance(body.get("dates"), list)
        and all(isinstance(d, str) for d in body["dates"])
        and body.get("hours") == list(range(1, 25))
        and isinstance(body.get("rows"), list)
        and len(body["rows"]) == 24
        and all(isinstance(r.get("he"), int)
                and isinstance(r.get("cells"), list)
                and len(r["cells"]) == len(body["dates"])
                and isinstance(r.get("n"), int)
                for r in body["rows"])
        and isinstance(body.get("day_avg"), list)
        and isinstance(body.get("scale_cap"), dict)
        and isinstance(body["scale_cap"].get("value"), (int, float))
        and isinstance(body.get("depth"), dict)
    )


def _passes_is_tb4(body) -> bool:
    return (
        isinstance(body, dict)
        and body.get("scope") in main.AN_TB4_SCOPES
        and isinstance(body.get("id"), str)
        and isinstance(body.get("definition"), str)
        and isinstance(body.get("daily"), list)
        and all(isinstance(r.get("date"), str)
                and isinstance(r.get("tb4"), (int, float))
                and isinstance(r.get("hours"), int)
                for r in body["daily"])
        and isinstance(body.get("monthly"), list)
        and all(isinstance(m.get("month"), str)
                and isinstance(m.get("complete"), bool)
                and isinstance(m.get("n_days"), int)
                for m in body["monthly"])
        and isinstance(body.get("avg_7d"), dict)
        and isinstance(body["avg_7d"].get("n_days"), int)
        and isinstance(body.get("avg_30d"), dict)
        and isinstance(body.get("regime_shift"), dict)
        and isinstance(body["regime_shift"].get("available"), bool)
        and isinstance(body.get("depth"), dict)
        and isinstance(body.get("runtime_ms"), int)
    )


def _passes_is_movers_grid(body) -> bool:
    if not (isinstance(body, dict)
            and body.get("hours") == list(range(1, 25))
            and isinstance(body.get("up"), list)
            and isinstance(body.get("down"), list)
            and isinstance(body.get("runtime_ms"), int)
            and isinstance(body.get("cached"), bool)
            and isinstance(body.get("depth"), dict)):
        return False
    for row in body["up"] + body["down"]:
        if not (isinstance(row.get("pnode_id"), str)
                and isinstance(row.get("rank"), int)
                and isinstance(row.get("cells"), list) and len(row["cells"]) == 24
                and isinstance(row.get("cell_n"), list) and len(row["cell_n"]) == 24):
            return False
    return True


def _passes_is_stance(stance) -> bool:
    """A stance either carries its verdict WITH its counts, or it is an honest
    absence carrying neither."""
    if not isinstance(stance, dict) or not isinstance(stance.get("available"), bool):
        return False
    if not stance["available"]:
        return "verdict" not in stance and isinstance(stance.get("reason"), str)
    return (stance.get("verdict") in ("BULLISH", "NEUTRAL", "BEARISH")
            and all(isinstance(stance.get(k), int)
                    for k in ("upper", "lower", "middle", "n"))
            and isinstance(stance.get("line"), str)
            and stance["n"] == stance["upper"] + stance["lower"] + stance["middle"])


def test_ladder_passes_the_clients_shape_guard(client):
    use_router(_ladder_router())
    assert _passes_is_ladder(
        client.get(f"/api/analytics/node/{NODE}/ladder").json())


def test_ladder_passes_the_guard_with_nothing_banked(client):
    use_router(_ladder_router(da=[], rt=[], hub=[]))
    b = client.get(f"/api/analytics/node/{NODE}/ladder").json()
    assert _passes_is_ladder(b)
    assert b["available"] is False and b["per_he"] == []


def test_ladder_passes_the_guard_above_the_floor_too(client):
    """The percentile branch has to survive the same guard — it is the branch
    the pantry has not reached yet, so nothing else exercises it."""
    da = [da_row(d, 5, 50.0, month=6) for d in range(1, 21)]
    rt = [rt_row(d, 5, 1, 50.0 + d, month=6) for d in range(1, 21)]
    use_router(_ladder_router(da=da, rt=rt))
    b = client.get(f"/api/analytics/node/{NODE}/ladder").json()
    assert _passes_is_ladder(b)
    assert b["per_he"][0]["vocabulary"] == "percentile"
    assert b["per_he"][0]["chip"]["regime"] == "HIGH"


def test_heat_grid_passes_the_clients_shape_guard(client):
    use_router(_ladder_router())
    assert _passes_is_heat_grid(
        client.get(f"/api/analytics/node/{NODE}/grid").json())


def test_heat_grid_passes_the_guard_with_nothing_banked(client):
    use_router(_ladder_router(da=[], rt=[], hub=[]))
    assert _passes_is_heat_grid(
        client.get(f"/api/analytics/node/{NODE}/grid").json())


def test_tb4_passes_the_clients_shape_guard(client):
    use_router(make_router(tb4=_tb4_hub_rows(40)))
    assert _passes_is_tb4(
        client.get("/api/analytics/tb4", params={"scope": "hub", "id": "SP15"}).json())


def test_tb4_passes_the_guard_with_nothing_banked(client):
    use_router(make_router(tb4=[]))
    b = client.get("/api/analytics/tb4", params={"scope": "hub", "id": "SP15"}).json()
    assert _passes_is_tb4(b)
    assert b["available"] is False and b["daily"] == [] and b["monthly"] == []
    assert b["regime_shift"]["available"] is False


def test_movers_grid_passes_the_clients_shape_guard(client):
    use_router(_grid_route_router())
    assert _passes_is_movers_grid(
        client.get("/api/analytics/movers-grid", params={"window": "1d"}).json())


def test_movers_grid_passes_the_guard_with_nothing_to_rank(client):
    use_router(make_router(
        grid=[{"d": D(2026, 7, 29), "he": he, "k": 4.0} for he in range(1, 25)],
        movers=lambda q, p: []))
    b = client.get("/api/analytics/movers-grid", params={"window": "1d"}).json()
    assert _passes_is_movers_grid(b)
    assert b["up"] == [] and b["down"] == []


def test_both_package_stances_pass_the_stance_guard(client):
    use_router(_package_router())
    b = client.get(f"/api/analytics/node/{NODE}").json()
    assert _passes_is_stance(b["dart"]["stance"])
    assert _passes_is_stance(b["basis"]["stance"])


def test_an_absent_stance_passes_the_stance_guard(client):
    use_router(_package_router(ident=identity_row(th_hub=None), hub=[]))
    b = client.get(f"/api/analytics/node/{NODE}").json()
    assert _passes_is_stance(b["basis"]["stance"])


def test_every_regime_payload_states_its_depth(client):
    """House standard: depth/n/window on every payload, in one grammar."""
    use_router(_ladder_router())
    ladder = client.get(f"/api/analytics/node/{NODE}/ladder").json()["depth"]
    use_router(_ladder_router())
    grid = client.get(f"/api/analytics/node/{NODE}/grid").json()["depth"]
    use_router(make_router(tb4=_tb4_hub_rows(10)))
    tb4 = client.get("/api/analytics/tb4",
                     params={"scope": "hub", "id": "SP15"}).json()["depth"]
    use_router(_grid_route_router())
    mgrid = client.get("/api/analytics/movers-grid",
                       params={"window": "1d"}).json()["depth"]
    keys = {"source", "retention", "markets", "depth_days"}
    for d in (ladder, grid, tb4, mgrid):
        assert keys <= set(d)
        assert isinstance(d["depth_days"], int)
