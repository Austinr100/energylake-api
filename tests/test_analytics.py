"""
Tests for THE ANALYTICS DEPARTMENT v0 — the Node Analytics Package (2026-07-30):

  * GET /api/analytics/node-search
  * GET /api/analytics/node/{pnode_id}
  * GET /api/analytics/movers

No live database is required. Like the sibling suites, the route tests swap a
tiny in-memory fake pool into `main._pool`; the fake dispatches canned rows by
inspecting the query text and params, because the package route makes FIVE
reads in one request (depth, identity, DAM leg, RTPD leg, hub leg).

Most of the arithmetic is pinned directly against the pure builders
(`_an_build_dart`, `_an_build_basis`, `_an_build_ledger`, ...) with hand-built
rows — no DB, no clock — mirroring the outlooks/drivers pattern.

WHAT THESE TESTS EXIST TO PROTECT. Four properties are silent on the server and
catastrophic on the client, so each has its own test:

  * THE DART SIGN. DART = FMM − DA, positive = RT above DA. The OPPOSITE sign
    ships from /api/timeseries/caiso-hub-lmp and the Watchboard `dart` tile
    under the field name `spread`. Both live in main.py. Flip this and every
    envelope chart in the department renders inverted, with no error anywhere.
  * ENVELOPE MONOTONICITY (acceptance 3). p5<=p25<=p50<=p75<=p95 per HE. A band
    chart fed a crossed envelope draws negative-height ribbons.
  * BASIS ABSENCE IS ABSENCE. A node with no CAISO trading hub must produce
    available:false + a reason — never a zero-filled panel. ~89% of the priced
    universe is in this state, so this is the COMMON path, not an edge case.
  * DEPTH HONESTY. Every response carries `depth`, and every distribution
    carries its own `n`. The snapshot hot tier holds EIGHT DATES; nothing may
    imply more.

Two tests (`test_acceptance_*`) carry literal figures re-derived by hand from
raw rows read out of the live pantry on 2026-07-30 — see the handoff. They are
the arithmetic spot-check, pinned so it cannot silently rot.
"""

import datetime

import psycopg
import pytest
from fastapi.testclient import TestClient

import main


UTC = datetime.timezone.utc
D = datetime.date

# The live spot-check node: AES Alamitos, paired to SP15 by CAISO.
NODE = "ALAMT3G_7_B1"


# ---------------------------------------------------------------------------
# Fake pool. Canned result sets dispatched by query text + params, so one
# request's five reads each get the right rows.
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
def _clear_movers_cache():
    main._an_movers_cache.clear()
    yield
    main._an_movers_cache.clear()


def use_router(router, fail_times=0, exc=None):
    pool = FakePool(router, fail_times=fail_times, exc=exc)
    main._pool = pool
    return pool


# ---------------------------------------------------------------------------
# Row builders — shaped exactly as psycopg's dict_row yields them.
# ---------------------------------------------------------------------------

def depth_rows(da=("2026-07-22", "2026-07-29"), rt=("2026-07-22", "2026-07-29")):
    out = []
    for market, span in ((main.AN_DA_MARKET, da), (main.AN_RT_MARKET, rt)):
        out.append({"market": market,
                    "min_date": D.fromisoformat(span[0]) if span else None,
                    "max_date": D.fromisoformat(span[1]) if span else None})
    return out


def identity_row(pnode_id=NODE, priced=True, th_hub="TH_SP15_GEN-APND",
                 node_type="GEN", area="CA", plant_name="AES Alamitos LLC"):
    return {
        "pnode_id": pnode_id, "in_universe": True, "priced": priced,
        "th_hub": th_hub, "apnode_type": None,
        "node_type": node_type if priced else None,
        "area": area if priced else None,
        "min_date": D(2026, 7, 22) if priced else None,
        "max_date": D(2026, 7, 29) if priced else None,
        "latitude": 33.765978, "longitude": -118.103663,
        "geocode_status": "ok", "plant_name": plant_name,
    }


def da_row(day, he, lmp, energy=None, congestion=None, loss=None, ghg=None):
    return {"market_date": D(2026, 7, day), "market_hour": he,
            "market_interval": 0, "lmp": lmp, "energy": energy,
            "congestion": congestion, "loss": loss, "ghg": ghg}


def rt_row(day, he, iv, lmp, energy=None, congestion=None, loss=None, ghg=None):
    return {"market_date": D(2026, 7, day), "market_hour": he,
            "market_interval": iv, "lmp": lmp, "energy": energy,
            "congestion": congestion, "loss": loss, "ghg": ghg}


def hub_row(day, he, value):
    return {"d": D(2026, 7, day), "he": he, "value": value}


def make_router(depth=None, ident=None, da=None, rt=None, hub=None,
                search=None, ref_node=NODE, grid=None, pairing=None,
                movers=None):
    """Dispatch a read to its canned rows by looking at the SQL."""
    def router(query, params):
        q = query
        if "FROM (VALUES (%(da)s), (%(rt)s)) AS m(market)" in q:
            return depth if depth is not None else depth_rows()
        if "in_universe" in q:
            return [ident] if ident is not None else [identity_row()]
        if "plant_match" in q:
            return search if search is not None else []
        if "FROM timeseries_values" in q:
            return hub if hub is not None else []
        if "GROUP BY 1, 2" in q and "count(*)::float8 AS k" in q:
            return grid if grid is not None else []
        if "LIMIT 1" in q and "s.pnode_id" in q and "ORDER BY s.pnode_id" in q:
            return [{"pnode_id": ref_node}]
        if "FROM atlas_pnode_universe" in q and "th_hub = ANY" in q:
            return pairing if pairing is not None else []
        if "weighted_sum" in q:
            return movers(query, params) if callable(movers) else (movers or [])
        if "market = %(market)s" in q:
            market = params.get("market")
            if market == main.AN_DA_MARKET:
                return da if da is not None else []
            return rt if rt is not None else []
        raise AssertionError(f"unrouted query: {q[:200]}")
    return router


# ===========================================================================
# Pure arithmetic — the house axioms
# ===========================================================================

def test_dart_is_fmm_minus_da_positive_means_rt_above_da():
    # DA 50, four FMM intervals averaging 60 -> DART = +10 (RT above DA).
    da = [da_row(23, 5, 50.0)]
    rt = [rt_row(23, 5, i, v) for i, v in enumerate([58.0, 60.0, 62.0, 60.0], 1)]
    out = main._an_build_dart(da, rt)
    assert out["latest_day"]["hours"][0]["dart"] == 10.0
    assert out["latest_day"]["hours"][0]["rt"] == 60.0
    assert "positive = rt above da" in out["convention"]


def test_dart_sign_is_the_opposite_of_the_hub_readers_spread_field():
    # Guards the one confusion that would silently invert every chart: the hub
    # reader and the Watchboard tile emit DA − RT under the name `spread`.
    da_px, rt_px = 50.0, 60.0
    dart = main._an_build_dart(
        [da_row(23, 5, da_px)],
        [rt_row(23, 5, 1, rt_px)],
    )["latest_day"]["hours"][0]["dart"]
    legacy_spread = round(da_px - rt_px, 5)   # what _cockpit_build_dart emits
    assert dart == -legacy_spread
    assert dart > 0 > legacy_spread


def test_rtpd_hour_is_averaged_over_the_intervals_that_exist():
    # A ragged hour (a missed dispatch) averages over what is there and says so,
    # rather than assuming four intervals or dropping the hour.
    out = main._an_build_dart(
        [da_row(23, 5, 50.0)],
        [rt_row(23, 5, 1, 60.0), rt_row(23, 5, 2, 70.0)],
    )
    hour = out["latest_day"]["hours"][0]
    assert hour["rt"] == 65.0
    assert hour["rt_intervals"] == 2


def test_an_hour_missing_either_leg_is_skipped_never_half_computed():
    da = [da_row(23, 5, 50.0), da_row(23, 6, 55.0)]
    rt = [rt_row(23, 5, 1, 60.0)]            # HE6 has no RT leg
    out = main._an_build_dart(da, rt)
    assert [h["he"] for h in out["latest_day"]["hours"]] == [5]
    assert out["n_hours"] == 1


def test_null_prices_do_not_become_zero():
    out = main._an_build_dart(
        [da_row(23, 5, None), da_row(23, 6, 50.0)],
        [rt_row(23, 5, 1, 60.0), rt_row(23, 6, 1, 60.0)],
    )
    assert [h["he"] for h in out["latest_day"]["hours"]] == [6]


def test_rtd_is_never_a_settlement_leg():
    # House axiom: this lane reads DAM and RTPD only.
    assert main.AN_DA_MARKET == "DAM"
    assert main.AN_RT_MARKET == "RTPD"
    assert "RTD" not in (main.AN_DA_MARKET, main.AN_RT_MARKET)


# ===========================================================================
# The envelope (acceptance 3)
# ===========================================================================

def _dart_days(values_by_day, he=5, da_px=50.0):
    """Build DA/RT rows so that DART on day d equals values_by_day[d]."""
    da, rt = [], []
    for day, dart in values_by_day.items():
        da.append(da_row(day, he, da_px))
        rt.append(rt_row(day, he, 1, da_px + dart))
    return da, rt


def test_envelope_bands_are_monotone():
    da, rt = _dart_days({d: v for d, v in
                         zip(range(22, 30), [-30, -5, 0, 3, 8, 12, 40, 100])})
    env = main._an_build_dart(da, rt)["per_he"][0]["envelope"]
    vals = [env[k] for k, _q in main.AN_ENVELOPE_QUANTILES]
    assert vals == sorted(vals), vals
    assert env["p5"] <= env["p25"] <= env["p50"] <= env["p75"] <= env["p95"]


@pytest.mark.parametrize("series", [
    [1.0],
    [2.0, 1.0],
    [5.0, -5.0, 0.0],
    [-30.0, -5.0, 0.0, 3.0, 8.0, 12.0, 40.0, 100.0],
    [7.0] * 8,
    [0.0, -0.0, 1e-9, -1e-9],
])
def test_envelope_is_monotone_for_any_sample(series):
    env = main._an_envelope(series)
    vals = [env[k] for k, _q in main.AN_ENVELOPE_QUANTILES]
    assert vals == sorted(vals), (series, vals)
    assert env["n"] == len(series)


def test_envelope_matches_postgres_percentile_cont():
    # percentile_cont(q) linear interpolation, verified against the same figures
    # Postgres produced for this node/HE in the live cross-check.
    s = [1.0, 2.0, 3.0, 4.0]
    assert main._an_percentile_cont(s, 0.50) == 2.5
    assert main._an_percentile_cont(s, 0.25) == 1.75
    assert main._an_percentile_cont(s, 0.75) == 3.25
    assert main._an_percentile_cont(s, 0.05) == pytest.approx(1.15)
    assert main._an_percentile_cont(s, 0.95) == pytest.approx(3.85)
    # Single sample: every band collapses onto it (still monotone).
    assert main._an_percentile_cont([9.0], 0.05) == 9.0


def test_envelope_is_absent_not_zero_filled_when_there_are_no_samples():
    assert main._an_envelope([]) is None


def test_every_envelope_states_its_own_n():
    da, rt = _dart_days({22: 1.0, 23: 2.0, 24: 3.0})
    out = main._an_build_dart(da, rt)
    assert out["per_he"][0]["n"] == 3
    assert out["n_days"] == 3


def test_latest_day_dots_are_placed_against_the_bands():
    da, rt = _dart_days({d: v for d, v in
                         zip(range(22, 30), [-30, -5, 0, 3, 8, 12, 40, 100])})
    out = main._an_build_dart(da, rt)
    hour = out["latest_day"]["hours"][0]
    assert out["latest_day"]["date"] == "2026-07-29"
    assert hour["dart"] == 100.0
    assert hour["band"] == "above_p95"


@pytest.mark.parametrize("value,expected", [
    (-100.0, "below_p5"), (100.0, "above_p95"),
])
def test_band_extremes(value, expected):
    env = main._an_envelope([-30.0, -5.0, 0.0, 3.0, 8.0, 12.0, 40.0, 50.0])
    assert main._an_band_of(value, env) == expected


def test_band_of_absence_is_absence():
    assert main._an_band_of(None, main._an_envelope([1.0, 2.0])) is None
    assert main._an_band_of(1.0, None) is None


# ===========================================================================
# Hub pairing rule v0 (STOP-gate 1)
# ===========================================================================

@pytest.mark.parametrize("th_hub,zone", [
    ("TH_NP15_GEN-APND", "NP15"),
    ("TH_SP15_GEN-APND", "SP15"),
    ("TH_ZP26_GEN-APND", "ZP26"),
])
def test_pairing_uses_caiso_th_hub_verbatim(th_hub, zone):
    p = main._an_pair_hub(th_hub)
    assert p == {"th_hub": th_hub, "hub": zone, "hub_priceable": True,
                 "pairing": "paired", "pairing_rule": "caiso_th_hub_v0"}


@pytest.mark.parametrize("th_hub", main.AN_TH_HUB_UNPRICEABLE)
def test_paired_but_unpriceable_is_its_own_verdict(th_hub):
    # CAISO pairs these; the pantry banks no hub price series for them. That is
    # NOT the same as unpaired and must not be reported as such.
    p = main._an_pair_hub(th_hub)
    assert p["pairing"] == "paired_unpriceable"
    assert p["th_hub"] == th_hub
    assert p["hub"] is None and p["hub_priceable"] is False


@pytest.mark.parametrize("th_hub", [None, ""])
def test_unpaired_nodes_report_unpaired(th_hub):
    p = main._an_pair_hub(th_hub)
    assert p["pairing"] == "unpaired"
    assert p["hub"] is None and p["hub_priceable"] is False


def test_pairing_never_infers_from_geography_or_area():
    # The rule reads ONE attribute. If this ever grows a lat/lon or `area`
    # branch, that is a guessed mapping and the STOP-gate applies again.
    import inspect
    src = inspect.getsource(main._an_pair_hub)
    for forbidden in ("latitude", "longitude", "area", "lat", "lon"):
        assert forbidden not in src, f"pairing must not consult {forbidden}"


# ===========================================================================
# Basis + blocks
# ===========================================================================

PAIRED = {"th_hub": "TH_SP15_GEN-APND", "hub": "SP15", "hub_priceable": True,
          "pairing": "paired", "pairing_rule": "caiso_th_hub_v0"}
UNPAIRED = {"th_hub": None, "hub": None, "hub_priceable": False,
            "pairing": "unpaired", "pairing_rule": "caiso_th_hub_v0"}


def test_7x16_is_he7_to_he22_every_day_of_the_week():
    assert [he for he in range(1, 25) if main._an_is_onpeak_he(he)] == list(range(7, 23))
    assert main._an_block_of(6) == "offpeak"
    assert main._an_block_of(7) == "7x16"
    assert main._an_block_of(22) == "7x16"
    assert main._an_block_of(23) == "offpeak"


def test_7x16_is_not_the_nerc_onpeak_block():
    # _lmp_is_onpeak() is Mon-Sat ex-holiday; 7x16 here is every day. A Sunday
    # HE10 is off-peak by NERC and IN the 7x16 block by this spec.
    sunday_he10 = datetime.datetime(2026, 7, 26, 9, 0)   # 2026-07-26 is a Sunday
    assert sunday_he10.isoweekday() == 7
    assert main._lmp_is_onpeak(sunday_he10) is False
    assert main._an_is_onpeak_he(10) is True


def test_basis_is_node_minus_hub_per_he():
    b = main._an_build_basis([da_row(23, 10, 60.0)], [hub_row(23, 10, 50.0)], PAIRED)
    assert b["available"] is True
    assert b["per_he"][0]["basis"] == 10.0
    assert b["per_he"][0]["node"] == 60.0
    assert b["per_he"][0]["hub"] == 50.0


def test_block_basis_equals_avg_of_diffs_and_diff_of_avgs():
    # The join is balanced, so these two definitions coincide. The ledger uses
    # the difference of block averages; pin the identity so they cannot drift.
    da = [da_row(23, he, 50.0 + he) for he in range(1, 25)]
    hub = [hub_row(23, he, 40.0 + he * 0.5) for he in range(1, 25)]
    b = main._an_build_basis(da, hub, PAIRED)
    on = [(50.0 + he) - (40.0 + he * 0.5) for he in range(7, 23)]
    assert b["blocks"]["7x16"]["basis"] == pytest.approx(sum(on) / len(on))
    assert b["blocks"]["7x16"]["n_hours"] == 16
    off = [(50.0 + he) - (40.0 + he * 0.5)
           for he in list(range(1, 7)) + [23, 24]]
    assert b["blocks"]["offpeak"]["basis"] == pytest.approx(sum(off) / len(off))
    assert b["blocks"]["offpeak"]["n_hours"] == 8


def test_basis_is_an_honest_absence_when_the_node_has_no_hub():
    b = main._an_build_basis([da_row(23, 10, 60.0)], [], UNPAIRED)
    assert b["available"] is False
    assert "no CAISO trading hub" in b["reason"]
    assert b["hub"] is None
    assert b["per_he"] == [] and b["blocks"] == {} and b["months"] == []
    # And nothing anywhere is a zero standing in for a missing number.
    assert 0 not in b["blocks"].values()


def test_basis_absence_when_paired_but_no_hub_series():
    b = main._an_build_basis([da_row(23, 10, 60.0)], [],
                             main._an_pair_hub("TH_PACE_GEN-APND"))
    assert b["available"] is False
    assert "no banked hub price series" in b["reason"]


def test_basis_absence_when_no_hour_carries_both_legs():
    b = main._an_build_basis([da_row(23, 10, 60.0)], [hub_row(24, 10, 50.0)], PAIRED)
    assert b["available"] is False
    assert "both" in b["reason"]


def test_hub_hours_with_no_node_row_are_ignored_not_fabricated():
    b = main._an_build_basis([da_row(23, 10, 60.0)],
                            [hub_row(23, 10, 50.0), hub_row(23, 11, 55.0)], PAIRED)
    assert [r["he"] for r in b["per_he"]] == [10]


def test_month_completeness_is_computed_not_assumed():
    # July 2026 covered by 07-22..07-29 is NOT a complete month.
    assert main._an_month_complete("2026-07", D(2026, 7, 22), D(2026, 7, 29)) is False
    assert main._an_month_complete("2026-07", D(2026, 7, 1), D(2026, 7, 31)) is True
    assert main._an_month_complete("2026-07", D(2026, 6, 1), D(2026, 8, 5)) is True
    # A December month boundary must not roll the year wrong.
    assert main._an_month_complete("2026-12", D(2026, 12, 1), D(2026, 12, 31)) is True
    assert main._an_month_complete("2026-12", D(2026, 12, 1), D(2026, 12, 30)) is False


def test_basis_months_carry_completeness_and_n():
    da = [da_row(d, he, 60.0) for d in (23, 24) for he in range(7, 23)]
    hub = [hub_row(d, he, 50.0) for d in (23, 24) for he in range(7, 23)]
    b = main._an_build_basis(da, hub, PAIRED)
    assert len(b["months"]) == 1
    m = b["months"][0]
    assert m["month"] == "2026-07"
    assert m["complete"] is False
    assert m["7x16"]["n_hours"] == 32 and m["7x16"]["n_days"] == 2
    assert m["7x16"]["basis"] == 10.0


# ===========================================================================
# Components
# ===========================================================================

def test_components_average_each_column_over_its_own_non_null_sample():
    # A null ghg must not shrink the congestion sample.
    da = [da_row(23, 1, 50.0, energy=48.0, congestion=1.0, loss=1.0, ghg=None),
          da_row(23, 2, 60.0, energy=58.0, congestion=3.0, loss=(-1.0), ghg=2.0)]
    c = main._an_build_components(da, [])
    assert c["DAM"]["congestion"] == {"mean": 2.0, "n": 2}
    assert c["DAM"]["ghg"] == {"mean": 2.0, "n": 1}
    assert c["DAM"]["rows"] == 2
    assert c["RTPD"]["lmp"] == {"mean": None, "n": 0}


def test_negative_components_are_real_and_pass_through():
    da = [da_row(23, 1, 50.0, congestion=-5.0)]
    c = main._an_build_components(da, [])
    assert c["DAM"]["congestion"]["mean"] == -5.0


# ===========================================================================
# The ledger
# ===========================================================================

def test_ledger_is_derived_from_basis_and_cannot_disagree_with_it():
    da = [da_row(d, he, 60.0) for d in (23, 24) for he in range(1, 25)]
    hub = [hub_row(d, he, 50.0) for d in (23, 24) for he in range(1, 25)]
    b = main._an_build_basis(da, hub, PAIRED)
    led = main._an_build_ledger(b)
    assert led["available"] is True
    assert len(led["rows"]) == 1
    row = led["rows"][0]
    assert row["node_7x16"] == b["months"][0]["7x16"]["node"]
    assert row["hub_7x16"] == b["months"][0]["7x16"]["hub"]
    assert row["basis_7x16"] == b["months"][0]["7x16"]["basis"]
    assert row["n_hours"] == b["months"][0]["7x16"]["n_hours"]


def test_every_ledger_row_states_its_n_and_completeness():
    da = [da_row(d, he, 60.0) for d in (23, 24) for he in range(7, 23)]
    hub = [hub_row(d, he, 50.0) for d in (23, 24) for he in range(7, 23)]
    led = main._an_build_ledger(main._an_build_basis(da, hub, PAIRED))
    for row in led["rows"]:
        assert {"month", "complete", "node_7x16", "hub_7x16", "basis_7x16",
                "n_hours", "n_days"} <= set(row)
        assert isinstance(row["n_hours"], int) and row["n_hours"] > 0
        assert row["complete"] is False      # 8 days of depth is never a month


def test_ledger_is_absent_when_basis_is():
    led = main._an_build_ledger(main._an_build_basis([da_row(23, 1, 5.0)], [], UNPAIRED))
    assert led["available"] is False
    assert led["rows"] == []
    assert led["reason"]


# ===========================================================================
# ACCEPTANCE 2 — hand re-derivation against raw pantry rows (2026-07-30)
# ===========================================================================

def test_acceptance_one_he_dart_re_derived_from_raw_rows():
    """ALAMT3G_7_B1, 2026-07-29 HE20 — raw rows read from the live pantry:

        DAM  interval 0            lmp = 99.50456
        RTPD interval 1            lmp = 65.27968
        RTPD interval 2            lmp = 62.13957
        RTPD interval 3            lmp = 63.56517
        RTPD interval 4            lmp = 63.27074

    By hand: sum(RTPD) = 254.25516, / 4 = 63.56379 = FMM(HE20).
             DART = FMM − DA = 63.56379 − 99.50456 = −35.94077
    Negative because RT settled BELOW the day-ahead schedule that hour.
    """
    da = [da_row(29, 20, 99.50456)]
    rt = [rt_row(29, 20, i, v) for i, v in enumerate(
        [65.27968, 62.13957, 63.56517, 63.27074], 1)]

    hand_rt = (65.27968 + 62.13957 + 63.56517 + 63.27074) / 4
    assert hand_rt == pytest.approx(63.56379, abs=1e-5)
    hand_dart = hand_rt - 99.50456
    assert hand_dart == pytest.approx(-35.94077, abs=1e-5)

    hour = main._an_build_dart(da, rt)["latest_day"]["hours"][0]
    assert hour["he"] == 20
    assert hour["rt_intervals"] == 4
    assert hour["rt"] == pytest.approx(63.56379, abs=1e-4)
    assert hour["da"] == pytest.approx(99.50456, abs=1e-4)
    assert hour["dart"] == pytest.approx(-35.94077, abs=1e-4)
    assert hour["dart"] < 0


def test_acceptance_one_month_7x16_basis_re_derived_from_raw_rows():
    """ALAMT3G_7_B1 vs SP15, 2026-07 7x16 — aggregated from raw pantry rows:

        matched 7x16 hours            n = 112   (8 dates, two of them partial)
        sum(node DAM lmp)                 6181.79594
        sum(hub  DAM value)               5590.52533

    By hand: node_7x16 = 6181.79594 / 112 = 55.194607
             hub_7x16  = 5590.52533 / 112 = 49.915405
             basis     = 55.194607 − 49.915405 = 5.279202

    112 = 1 (07-22 opens at HE22) + 6*16 (07-23..07-28) + 15 (07-29 ends HE21),
    which is the partial-edge arithmetic the depth report describes.
    """
    n = 112
    node_sum, hub_sum = 6181.79594, 5590.52533
    assert node_sum / n == pytest.approx(55.194607, abs=1e-6)
    assert hub_sum / n == pytest.approx(49.915405, abs=1e-6)
    assert node_sum / n - hub_sum / n == pytest.approx(5.279202, abs=1e-6)

    # Drive the builder with rows whose sums are the measured sums, so the
    # builder's own arithmetic is what is under test.
    da = [da_row(23, 7, node_sum / n) for _ in range(n)]
    hub_vals = [hub_sum / n] * n
    # One row per (date, HE) key is required, so spread across dates/hours.
    da, hub = [], []
    for i in range(n):
        day = 23 + i // 16
        he = 7 + i % 16
        da.append(da_row(day, he, node_sum / n))
        hub.append(hub_row(day, he, hub_sum / n))
    b = main._an_build_basis(da, hub, PAIRED)
    cut = b["blocks"]["7x16"]
    assert cut["n_hours"] == n
    assert cut["node"] == pytest.approx(55.1946, abs=1e-4)
    assert cut["hub"] == pytest.approx(49.9154, abs=1e-4)
    assert cut["basis"] == pytest.approx(5.2792, abs=1e-4)
    assert main._an_build_ledger(b)["rows"][0]["basis_7x16"] == cut["basis"]


def test_acceptance_partial_edge_dates_sum_to_112_onpeak_hours():
    # The depth arithmetic the ledger's N rests on, pinned independently.
    hours = 0
    hours += sum(1 for he in [22, 23, 24] if main._an_is_onpeak_he(he))       # 07-22
    hours += 6 * sum(1 for he in range(1, 25) if main._an_is_onpeak_he(he))   # 07-23..28
    hours += sum(1 for he in range(1, 22) if main._an_is_onpeak_he(he))       # 07-29
    assert hours == 112


# ===========================================================================
# Depth honesty (STOP-gate 3)
# ===========================================================================

def test_depth_reports_eight_dates_and_the_narrower_leg():
    d = main._an_depth(depth_rows())
    assert d["markets"]["DAM"] == {"min_date": "2026-07-22",
                                   "max_date": "2026-07-29", "days": 8}
    assert d["depth_days"] == 8
    assert "hot tier" in d["retention"]
    assert d["source"] == "atlas_pnode_lmp_snapshot"


def test_depth_days_takes_the_narrower_of_the_two_legs():
    d = main._an_depth(depth_rows(da=("2026-07-22", "2026-07-29"),
                                  rt=("2026-07-25", "2026-07-29")))
    assert d["markets"]["RTPD"]["days"] == 5
    assert d["depth_days"] == 5


def test_depth_of_an_empty_table_is_zero_not_a_crash():
    d = main._an_depth([{"market": "DAM", "min_date": None, "max_date": None},
                        {"market": "RTPD", "min_date": None, "max_date": None}])
    assert d["depth_days"] == 0
    assert d["markets"]["DAM"]["days"] == 0


# ===========================================================================
# The hub leg's UTC<->PT seam
# ===========================================================================

def test_hub_ts_bounds_cover_the_pt_days_in_utc():
    lo, hi = main._an_hub_ts_bounds(D(2026, 7, 22), D(2026, 7, 29))
    # PT is UTC-7 in July, so PT midnight 07-22 is 07:00Z the same day.
    assert lo == datetime.datetime(2026, 7, 22, 7, 0, tzinfo=UTC)
    assert hi == datetime.datetime(2026, 7, 30, 7, 0, tzinfo=UTC)
    assert lo < hi


def test_hub_ts_bounds_use_the_offset_actually_in_force():
    # January is UTC-8, so the same PT midnight is 08:00Z — not a hardcoded -7.
    lo, _hi = main._an_hub_ts_bounds(D(2026, 1, 10), D(2026, 1, 10))
    assert lo == datetime.datetime(2026, 1, 10, 8, 0, tzinfo=UTC)


# ===========================================================================
# Movers ranking + chunk planning (STOP-gate 2)
# ===========================================================================

def test_movers_rank_is_top_n_both_directions():
    r = main._an_movers_rank({"a": 5.0, "b": -3.0, "c": 1.0, "d": -10.0}, 2)
    assert [e["pnode_id"] for e in r["up"]] == ["a", "c"]
    assert [e["pnode_id"] for e in r["down"]] == ["d", "b"]
    assert r["ranked_nodes"] == 4


def test_movers_rank_drops_nodes_with_no_value_rather_than_zeroing_them():
    r = main._an_movers_rank({"a": 5.0, "b": None}, 50)
    assert [e["pnode_id"] for e in r["up"]] == ["a"]
    assert r["ranked_nodes"] == 1
    assert all(e["value"] is not None for e in r["up"] + r["down"])


def test_movers_rank_breaks_ties_deterministically():
    a = main._an_movers_rank({"z": 1.0, "a": 1.0, "m": 1.0}, 3)
    b = main._an_movers_rank({"m": 1.0, "a": 1.0, "z": 1.0}, 3)
    assert [e["pnode_id"] for e in a["up"]] == ["a", "m", "z"]
    assert a == b


def test_chunk_plan_rtpd_caps_at_six_hours():
    grid = {D(2026, 7, 28): {he: 4.0 for he in range(1, 25)}}
    chunks = main._an_chunk_plan([D(2026, 7, 28)], grid, main.AN_MOVERS_CHUNK_HOURS)
    assert len(chunks) == 4
    assert [(c["he_lo"], c["he_hi"]) for c in chunks] == [(1, 6), (7, 12), (13, 18), (19, 24)]
    assert all(len(c["pairs"]) <= 6 for c in chunks)


def test_chunk_plan_dam_is_one_chunk_per_date():
    grid = {D(2026, 7, 28): {he: 1.0 for he in range(1, 25)},
            D(2026, 7, 29): {he: 1.0 for he in range(1, 22)}}
    chunks = main._an_chunk_plan(sorted(grid), grid, None)
    assert len(chunks) == 2
    assert chunks[0]["hours"] == list(range(1, 25))
    assert chunks[1]["hours"] == list(range(1, 22))


def test_chunk_plan_reads_only_banked_hours_never_an_assumed_day():
    grid = {D(2026, 7, 22): {22: 4.0, 23: 4.0, 24: 2.0}}
    chunks = main._an_chunk_plan([D(2026, 7, 22)], grid, 6)
    assert len(chunks) == 1
    assert chunks[0]["hours"] == [22, 23, 24]
    assert chunks[0]["he_lo"] == 22 and chunks[0]["he_hi"] == 24


def test_chunk_plan_skips_a_date_with_no_banked_hours():
    assert main._an_chunk_plan([D(2026, 7, 22)], {}, 6) == []


def test_rt_chunk_sql_inlines_the_grid_and_keeps_the_between_bound():
    sql = main._an_movers_rt_sql([(13, 4.0), (14, 3.0)])
    assert "(13, 4.0::float8)" in sql and "(14, 3.0::float8)" in sql
    # Both halves of the pinned plan must survive any edit.
    assert "market_hour BETWEEN" in sql
    assert "GROUP BY s.pnode_id" in sql
    assert "market_hour" in sql and "g.he" in sql


def test_rt_chunk_sql_coerces_the_grid_to_numbers():
    # The grid is inlined, not bound, so it must never be able to carry text.
    sql = main._an_movers_rt_sql([("13", "4.0")])
    assert "(13, 4.0::float8)" in sql
    assert "'" not in sql.split("VALUES")[1].split(")")[0]


def test_dam_chunk_sql_uses_any_hours_not_a_values_grid():
    # Reusing the RTPD shape here cost 2,430 ms for a quarter of the rows.
    assert "market_hour = ANY(%(hours)s)" in main._AN_MOVERS_DA_CHUNK_SQL
    assert "VALUES" not in main._AN_MOVERS_DA_CHUNK_SQL
    assert "GROUP BY pnode_id" in main._AN_MOVERS_DA_CHUNK_SQL


# ===========================================================================
# Route level — shapes, honesty fields, error contract
# ===========================================================================

def test_search_returns_matches_with_pairing_and_coordinates(client):
    use_router(make_router(search=[{
        "pnode_id": NODE, "node_type": "GEN", "area": "CA",
        "name": "AES Alamitos LLC", "matched_via_name": True,
        "th_hub": "TH_SP15_GEN-APND",
        "latitude": 33.765978, "longitude": -118.103663,
    }]))
    r = client.get("/api/analytics/node-search", params={"q": "Alamitos"})
    assert r.status_code == 200
    b = r.json()
    assert b["count"] == 1 and b["query"] == "Alamitos"
    m = b["matches"][0]
    assert m["pnode_id"] == NODE
    assert m["name"] == "AES Alamitos LLC"
    assert m["hub"] == "SP15" and m["hub_priceable"] is True
    assert m["latitude"] == 33.765978 and m["longitude"] == -118.103663


def test_search_surfaces_a_null_name_rather_than_the_id_dressed_up(client):
    use_router(make_router(search=[{
        "pnode_id": "ALAMITOS_2_N001", "node_type": "LOAD", "area": "CA",
        "name": None, "matched_via_name": False, "th_hub": None,
        "latitude": 33.77, "longitude": -118.1,
    }]))
    m = client.get("/api/analytics/node-search", params={"q": "Alam"}).json()["matches"][0]
    assert m["name"] is None
    assert m["pnode_id"] == "ALAMITOS_2_N001"
    assert m["hub"] is None and m["pairing"] == "unpaired"


def test_search_no_matches_is_an_empty_list_not_an_error(client):
    use_router(make_router(search=[]))
    b = client.get("/api/analytics/node-search", params={"q": "zzz"}).json()
    assert b["count"] == 0 and b["matches"] == []


def test_search_escapes_like_metacharacters(client):
    pool = use_router(make_router(search=[]))
    client.get("/api/analytics/node-search", params={"q": "100%_x"})
    params = [p for p in pool.sink["params"] if p and "q" in p][0]
    assert params["q"] == r"%100\%\_x%"
    assert params["prefix"] == r"100\%\_x%"


def test_search_is_bounded(client):
    pool = use_router(make_router(search=[]))
    client.get("/api/analytics/node-search")
    params = [p for p in pool.sink["params"] if p and "limit" in p][0]
    assert params["limit"] == main.AN_SEARCH_MAX
    assert client.get("/api/analytics/node-search",
                      params={"limit": 999}).status_code == 400
    assert client.get("/api/analytics/node-search",
                      params={"limit": 0}).status_code == 400


def test_search_rides_the_latest_instant_never_a_history_scan(client):
    pool = use_router(make_router(search=[]))
    client.get("/api/analytics/node-search", params={"q": "a"})
    q = [q for q in pool.sink["queries"] if "plant_match" in q][0]
    assert "ORDER BY market_date DESC" in q and "LIMIT 1" in q
    assert "IS NOT DISTINCT FROM" in q
    assert "market_date >= %(sentinel)s" in q


def test_search_carries_depth_and_as_of(client):
    use_router(make_router(search=[]))
    b = client.get("/api/analytics/node-search").json()
    assert b["depth"]["depth_days"] == 8
    assert b["as_of"].endswith("+00:00")


def _package_router(**kw):
    da = kw.pop("da", None)
    if da is None:
        da = [da_row(d, he, 60.0 + he, energy=58.0, congestion=1.0, loss=0.5)
              for d in range(23, 30) for he in range(1, 25)]
    rt = kw.pop("rt", None)
    if rt is None:
        rt = [rt_row(d, he, iv, 62.0 + he)
              for d in range(23, 30) for he in range(1, 25) for iv in (1, 2, 3, 4)]
    hub = kw.pop("hub", None)
    if hub is None:
        hub = [hub_row(d, he, 50.0 + he) for d in range(23, 30) for he in range(1, 25)]
    return make_router(da=da, rt=rt, hub=hub, **kw)


def test_package_has_all_five_stanzas_plus_honesty_fields(client):
    use_router(_package_router())
    r = client.get(f"/api/analytics/node/{NODE}")
    assert r.status_code == 200
    b = r.json()
    assert set(b) == {"pnode_id", "as_of", "depth", "identity", "dart",
                      "basis", "components", "ledger"}
    assert b["depth"]["depth_days"] == 8
    assert b["identity"]["hub"] == "SP15"
    assert b["identity"]["pairing_rule"] == "caiso_th_hub_v0"
    assert b["dart"]["per_he"] and b["dart"]["latest_day"]
    assert b["basis"]["available"] is True
    assert b["ledger"]["available"] is True
    assert b["components"]["DAM"]["congestion"]["n"] > 0


def test_package_envelope_is_monotone_over_the_wire(client):
    use_router(_package_router())
    b = client.get(f"/api/analytics/node/{NODE}").json()
    for row in b["dart"]["per_he"]:
        env = row["envelope"]
        vals = [env[k] for k, _q in main.AN_ENVELOPE_QUANTILES]
        assert vals == sorted(vals), (row["he"], vals)
        assert row["n"] >= 1


def test_package_of_an_unpaired_node_omits_basis_and_ledger_honestly(client):
    use_router(_package_router(ident=identity_row(th_hub=None), hub=[]))
    b = client.get(f"/api/analytics/node/{NODE}").json()
    assert b["identity"]["hub"] is None
    assert b["identity"]["pairing"] == "unpaired"
    assert b["basis"]["available"] is False and b["basis"]["reason"]
    assert b["ledger"]["available"] is False and b["ledger"]["rows"] == []
    # DART does not need a hub, so it must still be there.
    assert b["dart"]["per_he"]


def test_package_does_not_read_the_hub_store_for_an_unpaired_node(client):
    pool = use_router(_package_router(ident=identity_row(th_hub=None), hub=[]))
    client.get(f"/api/analytics/node/{NODE}")
    assert not [q for q in pool.sink["queries"] if "timeseries_values" in q]


def test_package_hub_read_is_bounded_by_the_nodes_own_span(client):
    pool = use_router(_package_router())
    client.get(f"/api/analytics/node/{NODE}")
    p = [p for i, p in enumerate(pool.sink["params"])
         if "timeseries_values" in pool.sink["queries"][i]][0]
    assert p["series"] == "SP15"
    assert p["dataset"] == main.AN_HUB_DA_DATASET
    assert p["ts_lo"] < p["ts_hi"]
    # 07-23..07-29 PT -> 07-23T07:00Z .. 07-30T07:00Z
    assert p["ts_lo"] == datetime.datetime(2026, 7, 23, 7, 0, tzinfo=UTC)
    assert p["ts_hi"] == datetime.datetime(2026, 7, 30, 7, 0, tzinfo=UTC)


def test_package_node_legs_are_pnode_scoped_and_sentinel_floored(client):
    pool = use_router(_package_router())
    client.get(f"/api/analytics/node/{NODE}")
    legs = [(q, pool.sink["params"][i]) for i, q in enumerate(pool.sink["queries"])
            if "FROM atlas_pnode_lmp_snapshot" in q and "pnode_id = %(pnode_id)s" in q
            and "market = %(market)s" in q]
    assert {p["market"] for _q, p in legs} == {"DAM", "RTPD"}
    for q, p in legs:
        assert p["pnode_id"] == NODE
        assert p["sentinel"] == main.AN_SENTINEL_MIN_DATE
        assert "market_date >= %(sentinel)s" in q


def test_package_unknown_node_is_400(client):
    use_router(_package_router(ident=identity_row(priced=False, th_hub=None)))
    r = client.get("/api/analytics/node/NOPE_NOT_A_NODE")
    assert r.status_code == 400
    assert "unknown pnode_id" in r.json()["detail"]


def test_package_known_node_with_no_banked_rows_is_an_honest_200(client):
    use_router(_package_router(da=[], rt=[], hub=[]))
    r = client.get(f"/api/analytics/node/{NODE}")
    assert r.status_code == 200
    b = r.json()
    assert b["dart"]["per_he"] == [] and b["dart"]["latest_day"] is None
    assert b["dart"]["n_days"] == 0
    assert b["basis"]["available"] is False
    assert b["components"]["DAM"]["lmp"] == {"mean": None, "n": 0}


def test_package_db_down_is_503(client):
    use_router(_package_router(), fail_times=99)
    r = client.get(f"/api/analytics/node/{NODE}")
    assert r.status_code == 503
    assert "db unavailable" in r.json()["detail"]


def test_package_recovers_from_one_stale_connection(client):
    use_router(_package_router(), fail_times=1)
    assert client.get(f"/api/analytics/node/{NODE}").status_code == 200


def _movers_router(**kw):
    grid = kw.pop("grid", None)
    if grid is None:
        grid = [{"d": D(2026, 7, 29), "he": he, "k": 4.0} for he in range(1, 25)]

    def movers(query, params):
        # DAM rows sum lmp; RTPD rows sum lmp/k. Two nodes, so ranking is real.
        if params.get("market") == main.AN_DA_MARKET:
            n = len(params["hours"])
            return [{"pnode_id": "AAA", "weighted_sum": 50.0 * n, "n_rows": n},
                    {"pnode_id": "BBB", "weighted_sum": 60.0 * n, "n_rows": n}]
        span = params["he_hi"] - params["he_lo"] + 1
        return [{"pnode_id": "AAA", "weighted_sum": 55.0 * span, "n_rows": span * 4},
                {"pnode_id": "BBB", "weighted_sum": 58.0 * span, "n_rows": span * 4}]

    return make_router(grid=grid, movers=movers, **kw)


def test_movers_dart_ranks_both_directions_with_the_plan_stated(client):
    use_router(_movers_router())
    r = client.get("/api/analytics/movers", params={"window": "1d", "metric": "dart"})
    assert r.status_code == 200
    b = r.json()
    assert b["metric"] == "dart" and b["window"] == "1d"
    # AAA: 55-50 = +5 ; BBB: 58-60 = -2
    assert b["up"][0] == {"pnode_id": "AAA", "value": 5.0}
    assert b["down"][0] == {"pnode_id": "BBB", "value": -2.0}
    assert b["coverage"]["ranked_nodes"] == 2
    assert b["top_n"] == main.AN_MOVERS_TOP_N
    assert isinstance(b["runtime_ms"], int)
    assert b["query_plan"]["index"] == "idx_lmp_snap_market_time"
    assert b["query_plan"]["chunks"]["dam"] == 1      # one chunk per date
    assert b["query_plan"]["chunks"]["rtpd"] == 4     # 24 hours / 6
    assert "positive = RT above DA" in b["definition"]


def test_movers_basis_reads_no_rtpd_and_reports_unpaired_exclusions(client):
    pool = use_router(_movers_router(pairing=[
        {"pnode_id": "AAA", "th_hub": "TH_SP15_GEN-APND"}],
        hub=[hub_row(29, he, 45.0) for he in range(1, 25)]))
    b = client.get("/api/analytics/movers",
                   params={"window": "1d", "metric": "basis"}).json()
    # AAA is paired (50 − 45 = +5); BBB is not and is counted, not ranked.
    assert b["up"] == [{"pnode_id": "AAA", "value": 5.0}]
    assert b["coverage"]["excluded_unpaired"] == 1
    assert b["coverage"]["pairing_rule"] == "caiso_th_hub_v0"
    assert b["query_plan"]["chunks"]["rtpd"] == 0
    assert not [p for i, p in enumerate(pool.sink["params"])
                if "weighted_sum" in pool.sink["queries"][i]
                and p.get("market") == main.AN_RT_MARKET]


def test_movers_excludes_nodes_that_are_short_of_the_matched_hour_set(client):
    def movers(query, params):
        if params.get("market") == main.AN_DA_MARKET:
            n = len(params["hours"])
            return [{"pnode_id": "AAA", "weighted_sum": 50.0 * n, "n_rows": n},
                    {"pnode_id": "SHORT", "weighted_sum": 50.0 * (n - 1),
                     "n_rows": n - 1}]
        span = params["he_hi"] - params["he_lo"] + 1
        return [{"pnode_id": "AAA", "weighted_sum": 55.0 * span, "n_rows": span * 4},
                {"pnode_id": "SHORT", "weighted_sum": 55.0 * span, "n_rows": span * 4}]

    use_router(make_router(
        grid=[{"d": D(2026, 7, 29), "he": he, "k": 4.0} for he in range(1, 25)],
        movers=movers))
    b = client.get("/api/analytics/movers", params={"metric": "dart"}).json()
    assert [e["pnode_id"] for e in b["up"]] == ["AAA"]
    assert b["coverage"]["excluded_incomplete"] == 1


def test_movers_counts_a_node_missing_from_one_leg_entirely(client):
    # A node in DAM but absent from RTPD must show up in excluded_incomplete,
    # not vanish from the tally.
    def movers(query, params):
        if params.get("market") == main.AN_DA_MARKET:
            n = len(params["hours"])
            return [{"pnode_id": "AAA", "weighted_sum": 50.0 * n, "n_rows": n},
                    {"pnode_id": "DA_ONLY", "weighted_sum": 50.0 * n, "n_rows": n}]
        span = params["he_hi"] - params["he_lo"] + 1
        return [{"pnode_id": "AAA", "weighted_sum": 55.0 * span, "n_rows": span * 4},
                {"pnode_id": "RT_ONLY", "weighted_sum": 55.0 * span, "n_rows": span * 4}]

    use_router(make_router(
        grid=[{"d": D(2026, 7, 29), "he": he, "k": 4.0} for he in range(1, 25)],
        movers=movers))
    b = client.get("/api/analytics/movers", params={"metric": "dart"}).json()
    assert [e["pnode_id"] for e in b["up"]] == ["AAA"]
    assert b["coverage"]["excluded_incomplete"] == 2   # DA_ONLY and RT_ONLY
    assert b["coverage"]["ranked_nodes"] == 1


def test_movers_only_ranks_hours_both_legs_published(client):
    # RTPD grid has 24 hours; DAM only 21 (the real 07-29 shape).
    def router(query, params):
        base = _movers_router()
        if "count(*)::float8 AS k" in query:
            hours = range(1, 25) if params["market"] == main.AN_RT_MARKET else range(1, 22)
            return [{"d": D(2026, 7, 29), "he": he, "k": 4.0} for he in hours]
        return base(query, params)

    use_router(router)
    b = client.get("/api/analytics/movers", params={"metric": "dart"}).json()
    assert b["window_served"]["matched_hours"] == 21
    assert b["window_served"]["matched_days"] == 1


def test_basis_movers_hour_set_is_not_narrowed_by_the_rtpd_leg(client):
    """basis is node DA − hub DA. The FMM leg has nothing to do with it, so a
    thin RTPD day must NOT shrink the basis window (it did in the first cut)."""
    def router(query, params):
        base = _movers_router(
            pairing=[{"pnode_id": "AAA", "th_hub": "TH_SP15_GEN-APND"}],
            hub=[hub_row(29, he, 45.0) for he in range(1, 25)])
        if "count(*)::float8 AS k" in query:
            # RTPD published only 6 hours; DAM published all 24.
            hours = range(1, 7) if params["market"] == main.AN_RT_MARKET else range(1, 25)
            return [{"d": D(2026, 7, 29), "he": he, "k": 4.0} for he in hours]
        return base(query, params)

    use_router(router)
    b = client.get("/api/analytics/movers",
                   params={"window": "1d", "metric": "basis"}).json()
    assert b["window_served"]["matched_hours"] == 24
    assert b["up"] == [{"pnode_id": "AAA", "value": 5.0}]

    # The dart metric, by contrast, IS correctly limited to the 6 shared hours.
    main._an_movers_cache.clear()
    use_router(router)
    d = client.get("/api/analytics/movers",
                   params={"window": "1d", "metric": "dart"}).json()
    assert d["window_served"]["matched_hours"] == 6


def test_basis_movers_hour_set_requires_every_zone_to_have_banked_the_hour(client):
    # All three zones must span identical hours or NP15/SP15/ZP26 nodes would be
    # ranked over different windows.
    def router(query, params):
        base = _movers_router(pairing=[{"pnode_id": "AAA", "th_hub": "TH_SP15_GEN-APND"}])
        if "FROM timeseries_values" in query:
            hours = range(1, 25) if params["series"] != "ZP26" else range(1, 13)
            return [hub_row(29, he, 45.0) for he in hours]
        return base(query, params)

    use_router(router)
    b = client.get("/api/analytics/movers",
                   params={"window": "1d", "metric": "basis"}).json()
    assert b["window_served"]["matched_hours"] == 12


def test_movers_window_is_anchored_on_banked_data_not_wall_clock(client):
    use_router(_movers_router())
    b = client.get("/api/analytics/movers", params={"window": "1d"}).json()
    assert b["window_served"]["end"] == "2026-07-29"
    assert b["window_served"]["start"] == "2026-07-29"


def test_movers_7d_window_spans_seven_dates(client):
    use_router(_movers_router(grid=[
        {"d": D(2026, 7, d), "he": he, "k": 4.0}
        for d in range(23, 30) for he in range(1, 25)]))
    b = client.get("/api/analytics/movers", params={"window": "7d"}).json()
    assert b["window_served"] == {"start": "2026-07-23", "end": "2026-07-29",
                                  "matched_hours": 168, "matched_days": 7}
    assert b["query_plan"]["chunks"] == {"dam": 7, "rtpd": 28}


@pytest.mark.parametrize("params", [
    {"window": "30d"}, {"window": ""}, {"metric": "spread"}, {"metric": "rtd"},
])
def test_movers_rejects_unknown_window_or_metric(client, params):
    use_router(_movers_router())
    r = client.get("/api/analytics/movers", params=params)
    assert r.status_code == 400


def test_movers_is_cached_per_metric_and_window(client):
    use_router(_movers_router())
    first = client.get("/api/analytics/movers", params={"metric": "dart"}).json()
    assert first["cached"] is False
    second = client.get("/api/analytics/movers", params={"metric": "dart"}).json()
    assert second["cached"] is True
    assert second["up"] == first["up"]
    assert "max-age" in client.get("/api/analytics/movers",
                                   params={"metric": "dart"}).headers["cache-control"]


def test_movers_db_down_is_503(client):
    use_router(_movers_router(), fail_times=99)
    r = client.get("/api/analytics/movers")
    assert r.status_code == 503


def test_movers_carries_depth(client):
    use_router(_movers_router())
    b = client.get("/api/analytics/movers").json()
    assert b["depth"]["depth_days"] == 8
    assert b["depth"]["source"] == "atlas_pnode_lmp_snapshot"


# ===========================================================================
# The client's shape guards — Python mirrors (the outlooks pattern)
# ===========================================================================

def _passes_is_node_package(body) -> bool:
    """Mirror of the dashboard's isNodePackage(). Every array must be an ARRAY,
    never null: the Nodes room renders five panels off one read, and a null
    where a list belongs fails the guard, which fails the read, which blanks the
    WHOLE package rather than one panel."""
    return (
        isinstance(body, dict)
        and isinstance(body.get("pnode_id"), str)
        and isinstance(body.get("as_of"), str)
        and isinstance(body.get("depth"), dict)
        and isinstance(body["depth"].get("depth_days"), int)
        and isinstance(body.get("identity"), dict)
        and isinstance(body["identity"].get("pnode_id"), str)
        and isinstance(body.get("dart"), dict)
        and isinstance(body["dart"].get("per_he"), list)
        and all(isinstance(r.get("he"), int) and isinstance(r.get("envelope"), dict)
                for r in body["dart"]["per_he"])
        and isinstance(body.get("basis"), dict)
        and isinstance(body["basis"].get("available"), bool)
        and isinstance(body["basis"].get("per_he"), list)
        and isinstance(body["basis"].get("months"), list)
        and isinstance(body.get("components"), dict)
        and isinstance(body.get("ledger"), dict)
        and isinstance(body["ledger"].get("rows"), list)
    )


def _passes_is_movers(body) -> bool:
    return (
        isinstance(body, dict)
        and isinstance(body.get("up"), list)
        and isinstance(body.get("down"), list)
        and all(isinstance(e.get("pnode_id"), str)
                and isinstance(e.get("value"), (int, float))
                for e in body["up"] + body["down"])
        and isinstance(body.get("depth"), dict)
    )


def _passes_is_search(body) -> bool:
    return (
        isinstance(body, dict)
        and isinstance(body.get("matches"), list)
        and isinstance(body.get("count"), int)
        and all(isinstance(m.get("pnode_id"), str) for m in body["matches"])
    )


def test_package_passes_the_clients_shape_guard(client):
    use_router(_package_router())
    assert _passes_is_node_package(client.get(f"/api/analytics/node/{NODE}").json())


def test_package_passes_the_guard_even_with_nothing_banked(client):
    # The all-absent case is the one most likely to ship a null where the client
    # expects a list.
    use_router(_package_router(da=[], rt=[], hub=[]))
    b = client.get(f"/api/analytics/node/{NODE}").json()
    assert _passes_is_node_package(b)
    assert b["dart"]["per_he"] == []
    assert b["basis"]["per_he"] == [] and b["basis"]["months"] == []
    assert b["ledger"]["rows"] == []


def test_package_passes_the_guard_for_an_unpaired_node(client):
    use_router(_package_router(ident=identity_row(th_hub=None), hub=[]))
    assert _passes_is_node_package(client.get(f"/api/analytics/node/{NODE}").json())


def test_movers_passes_the_clients_shape_guard(client):
    use_router(_movers_router())
    assert _passes_is_movers(client.get("/api/analytics/movers").json())


def test_movers_passes_the_guard_with_nothing_to_rank(client):
    use_router(_movers_router(), )
    main._an_movers_cache.clear()

    def movers(query, params):
        return []

    use_router(make_router(
        grid=[{"d": D(2026, 7, 29), "he": he, "k": 4.0} for he in range(1, 25)],
        movers=movers))
    b = client.get("/api/analytics/movers").json()
    assert _passes_is_movers(b)
    assert b["up"] == [] and b["down"] == []
    assert b["coverage"]["ranked_nodes"] == 0


def test_search_passes_the_clients_shape_guard(client):
    use_router(make_router(search=[]))
    assert _passes_is_search(client.get("/api/analytics/node-search").json())


def test_all_three_surfaces_share_the_depth_grammar(client):
    """The department renders one depth caption across three pages; if these
    ever diverge that reuse silently breaks."""
    use_router(_package_router())
    pkg = client.get(f"/api/analytics/node/{NODE}").json()["depth"]
    use_router(make_router(search=[]))
    search = client.get("/api/analytics/node-search").json()["depth"]
    main._an_movers_cache.clear()
    use_router(_movers_router())
    movers = client.get("/api/analytics/movers").json()["depth"]
    assert set(pkg) == set(search) == set(movers)
    assert pkg["depth_days"] == search["depth_days"] == movers["depth_days"]
