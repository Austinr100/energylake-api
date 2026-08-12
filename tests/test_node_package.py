"""
Tests for NODE ANALYTICS API v0 (2026-08-12):

  * GET /api/nodes/{pnode_id}/package
  * GET /api/hubs/{hub}/blocks
  * GET /api/nodes/top-movers

No live database. Like the sibling suites, a tiny in-memory fake pool is swapped
into `main._pool` and canned rows are dispatched by inspecting the query text.

`block_pricing`'s own arithmetic is proven in tests/test_block_pricing.py. What
this suite protects is the WIRE: that the contract keys Lane B builds against
are present and spelled exactly as agreed, that the window is derived from the
bank rather than from a constant, that suppression and absence survive
serialization, and that the block hour counts still reconcile after passing
through the endpoint.

THE JULY 2026 FIXTURE IS THE WHOLE MONTH, and July 2026 is the interesting
month: Independence Day falls on a SATURDAY (observed in place, not moved) and
there are four Sundays. So the month contains 26 6x16 days and 5 2x16h days,
and every block hour count below is hand-derived from that split.
"""

import datetime

import pytest
from fastapi.testclient import TestClient

import main


D = datetime.date
NODE = "FIXT1_7_N001"
UNKNOWN = "NO_SUCH_NODE_9_N999"

# Price rule: 40 on peak hours (HE7-22), 10 off-peak. Flat across days so the
# block averages are hand-checkable to the cent.
PEAK_PRICE = 40.0
OFF_PRICE = 10.0


# ---------------------------------------------------------------------------
# Fake pool — same shape as the sibling suites.
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
def _clear_movers_cache():
    main._nap_movers_cache.clear()
    yield
    main._nap_movers_cache.clear()


# ---------------------------------------------------------------------------
# The fixture: all of July 2026, 24 HE a day.
# ---------------------------------------------------------------------------

def _july_rows(first=D(2026, 7, 1), last=D(2026, 7, 31), rt_n=12, rt=True):
    rows = []
    d = first
    while d <= last:
        for he in range(1, 25):
            dam = PEAK_PRICE if 7 <= he <= 22 else OFF_PRICE
            rows.append({
                "trade_date": d, "he": he,
                "dam_lmp": dam,
                "rt_avg": (dam + 5.0) if rt else None,
                "rt_n": rt_n if rt else 0,
                "dart": 5.0 if rt else None,
                "basis_sp15": -2.0,
                "dam_congestion": (1.0 if d >= D(2026, 7, 3) else None),
                "dam_loss": 0.5,
            })
        d += datetime.timedelta(days=1)
    return rows


def _router(rows=None, identity=True, depth=True):
    rows = _july_rows() if rows is None else rows
    days = sorted({r["trade_date"] for r in rows})

    def route(query, params):
        q = " ".join(query.split())
        if "atlas_pnode_universe u" in q:
            if not identity:
                return []
            return [{"pnode_id": NODE, "th_hub": "TH_SP15_GEN-APND",
                     "apnode_type": None, "description_name": None,
                     "node_type": "GEN", "area": "CA",
                     "plant_name": "Fixture Generating Station"}]
        if "days_banked" in q:
            if not depth or not days:
                return [{"first_day": None, "last_day": None, "days_banked": 0}]
            return [{"first_day": days[0], "last_day": days[-1],
                     "days_banked": len(days)}]
        if "dam_congestion, dam_loss" in q:
            lo = params.get("lo")
            return [r for r in rows if lo is None or r["trade_date"] >= lo]
        raise AssertionError(f"unrouted query: {q[:120]}")

    return route


@pytest.fixture
def pool(monkeypatch):
    def _install(router):
        p = FakePool(router)
        monkeypatch.setattr(main, "_pool", p)
        return p
    return _install


# ═══════════════════════════════════════════════════════════════════════════
# 1. The package contract — the keys Lane B builds against, verbatim
# ═══════════════════════════════════════════════════════════════════════════

CONTRACT_KEYS = ("identity", "profile", "profile_rt", "heatmap", "distribution",
                 "distribution_rt", "tb", "blocks", "blocks_daily", "basis",
                 "dart", "rt_coverage_note")


def test_package_carries_every_contract_key(client, pool):
    pool(_router())
    r = client.get(f"/api/nodes/{NODE}/package")
    assert r.status_code == 200
    body = r.json()
    for key in CONTRACT_KEYS:
        assert key in body, f"missing contract key {key}"


def test_identity_stanza_fields(client, pool):
    pool(_router())
    ident = client.get(f"/api/nodes/{NODE}/package").json()["identity"]
    for key in ("pnode_id", "name", "zone", "first_day", "last_day",
                "days_banked", "components_from"):
        assert key in ident, key
    assert ident["pnode_id"] == NODE
    assert ident["name"] == "Fixture Generating Station"
    assert ident["zone"] == "SP15"              # th_hub -> zone, verbatim
    assert ident["first_day"] == "2026-07-01"
    assert ident["last_day"] == "2026-07-31"
    assert ident["days_banked"] == 31


def test_components_from_is_derived_from_the_nodes_own_rows(client, pool):
    """The fixture's congestion starts 2026-07-03, three days after the bank
    does. components_from must report the NODE's floor, not the window's."""
    pool(_router())
    ident = client.get(f"/api/nodes/{NODE}/package").json()["identity"]
    assert ident["components_from"] == "2026-07-03"


def test_components_from_is_null_when_no_row_carries_congestion(client, pool):
    rows = _july_rows()
    for r in rows:
        r["dam_congestion"] = None
    pool(_router(rows))
    ident = client.get(f"/api/nodes/{NODE}/package").json()["identity"]
    assert ident["components_from"] is None


# ═══════════════════════════════════════════════════════════════════════════
# 2. Block arithmetic, end to end over a Saturday NERC holiday
# ═══════════════════════════════════════════════════════════════════════════
#
# July 2026 hand-split:
#   Sundays        Jul 5, 12, 19, 26                        -> 4 days
#   NERC holiday   Jul 4 (SATURDAY, observed in place)      -> 1 day
#   6x16 days      31 - 4 - 1                               -> 26 days
#   2x16h days     4 Sundays + Jul 4                        -> 5 days
#
# Hours:  6x16   26 x 16 = 416      2x16h   5 x 16 =  80
#         7x16   31 x 16 = 496      7x8    31 x  8 = 248
#         atc    31 x 24 = 744      off_peak 26x8 + 5x24 = 208 + 120 = 328
#
# Averages at 40 on-peak / 10 off-peak:
#         6x16 = 2x16h = 7x16 = 40.0      7x8 = 10.0
#         atc  = (496x40 + 248x10)/744 = 22320/744 = 30.0
#         off_peak = (208x10 + 80x40 + 40x10)/328 = 5680/328 = 17.3171

def test_block_hours_and_averages_over_a_full_month(client, pool):
    pool(_router())
    blocks = client.get(f"/api/nodes/{NODE}/package").json()["blocks"]

    expect_hours = {"6x16": 416, "2x16h": 80, "7x16": 496,
                    "7x8": 248, "atc": 744, "off_peak": 328}
    expect_avg = {"6x16": 40.0, "2x16h": 40.0, "7x16": 40.0,
                  "7x8": 10.0, "atc": 30.0, "off_peak": 17.3171}

    for block, hours in expect_hours.items():
        cell = blocks[block]["monthly"][0]
        assert cell["month"] == "2026-07"
        assert cell["hours"] == hours, f"{block} hours"
        assert cell["avg_dam"] == pytest.approx(expect_avg[block], abs=1e-4), block
        # rt_avg is dam + 5 everywhere.
        assert cell["avg_rt"] == pytest.approx(expect_avg[block] + 5.0, abs=1e-4)


def test_the_partition_still_holds_on_the_wire(client, pool):
    """6x16 + off_peak == atc, and 7x8 + 2x16h == off_peak, in COUNTED hours."""
    pool(_router())
    blocks = client.get(f"/api/nodes/{NODE}/package").json()["blocks"]
    h = {b: blocks[b]["monthly"][0]["hours"] for b in blocks}
    assert h["6x16"] + h["off_peak"] == h["atc"] == 744
    assert h["7x8"] + h["2x16h"] == h["off_peak"] == 328


def test_every_block_is_served_including_6x16(client, pool):
    """6x16 was previously WITHHELD from the Structures room pending an
    arithmetic reconciliation against the pantry. It ships here."""
    pool(_router())
    blocks = client.get(f"/api/nodes/{NODE}/package").json()["blocks"]
    assert set(blocks) == set(main._bp.BLOCK_IDS)
    assert "6x16" in blocks
    assert blocks["6x16"]["definition"].startswith("HE7-22, Mon-Sat")


def test_blocks_daily_omits_6x16_on_the_saturday_holiday_and_on_sundays(client, pool):
    pool(_router())
    daily = client.get(f"/api/nodes/{NODE}/package").json()["blocks_daily"]
    by_date = {}
    for c in daily:
        by_date.setdefault(c["date"], set()).add(c["block"])

    # blocks_daily is the LAST 31 banked days; July is exactly 31, so all in.
    assert "6x16" not in by_date["2026-07-04"]      # Saturday + Independence
    assert "2x16h" in by_date["2026-07-04"]
    assert "6x16" not in by_date["2026-07-05"]      # Sunday
    assert "6x16" in by_date["2026-07-03"]          # ordinary Friday
    assert "6x16" in by_date["2026-07-11"]          # ORDINARY Saturday -> 6x16


def test_blocks_daily_is_bounded_to_31_days(client, pool):
    """A 60-day bank still yields 31 days of daily blocks."""
    rows = _july_rows(D(2026, 6, 1), D(2026, 7, 31))
    pool(_router(rows))
    daily = client.get(f"/api/nodes/{NODE}/package").json()["blocks_daily"]
    dates = sorted({c["date"] for c in daily})
    assert len(dates) == 31
    assert dates[-1] == "2026-07-31"
    assert dates[0] == "2026-07-01"


# ═══════════════════════════════════════════════════════════════════════════
# 3. The window is DERIVED, never hardcoded
# ═══════════════════════════════════════════════════════════════════════════

def test_window_all_covers_the_whole_bank(client, pool):
    pool(_router())
    w = client.get(f"/api/nodes/{NODE}/package?window_days=all").json()["window"]
    assert w == {"days_requested": "all", "first_day": "2026-07-01",
                 "last_day": "2026-07-31", "days_in_window": 31, "hours": 744}


def test_window_30_counts_back_from_the_nodes_own_last_banked_day(client, pool):
    """NOT from today. The fixture's bank ends 2026-07-31, so a 30-day ask
    starts 2026-07-02 regardless of what the wall clock says."""
    p = pool(_router())
    w = client.get(f"/api/nodes/{NODE}/package?window_days=30").json()["window"]
    assert w["first_day"] == "2026-07-02"
    assert w["last_day"] == "2026-07-31"
    assert w["days_in_window"] == 30
    # And the SQL was actually bounded — the window is pushed into the read.
    lo = [pp.get("lo") for pp in p.sink["params"] if pp and "lo" in pp]
    assert lo == [D(2026, 7, 2)]


def test_window_90_clamps_to_the_bank_rather_than_inventing_depth(client, pool):
    pool(_router())
    w = client.get(f"/api/nodes/{NODE}/package?window_days=90").json()["window"]
    assert w["first_day"] == "2026-07-01"      # the bank starts there
    assert w["days_in_window"] == 31


def test_bad_window_is_a_400_that_names_the_options(client, pool):
    pool(_router())
    r = client.get(f"/api/nodes/{NODE}/package?window_days=45")
    assert r.status_code == 400
    assert "all" in r.json()["detail"]


# ═══════════════════════════════════════════════════════════════════════════
# 4. Partial months, absence, suppression
# ═══════════════════════════════════════════════════════════════════════════

def test_complete_month_summarises_and_partial_month_does_not(client, pool):
    pool(_router())
    body = client.get(f"/api/nodes/{NODE}/package").json()
    cell = body["blocks"]["6x16"]["monthly"][0]
    assert cell["partial"] is False
    assert body["blocks"]["6x16"]["summary"]["n_months"] == 1

    # Half a month -> partial, and the summary refuses to average it.
    pool(_router(_july_rows(D(2026, 7, 1), D(2026, 7, 15))))
    body = client.get(f"/api/nodes/{NODE}/package").json()
    cell = body["blocks"]["6x16"]["monthly"][0]
    assert cell["partial"] is True
    assert body["blocks"]["6x16"]["summary"]["n_months"] == 0
    assert body["blocks"]["6x16"]["summary"]["avg"] is None


def test_tb_stanza_carries_both_n_and_the_unit(client, pool):
    pool(_router())
    tb = client.get(f"/api/nodes/{NODE}/package").json()["tb"]
    assert set(tb) == {"2", "4"}
    for n in ("2", "4"):
        assert tb[n]["unit"] == "$/kW-month"
        assert tb[n]["months"][0]["month"] == "2026-07"
        assert "partial" in tb[n]["months"][0]
        assert "summary" in tb[n]
    # Flat 40/10 prices: TB2 spread = 30 -> 30x2/1000 = 0.06 $/kW-day x 31 days.
    assert tb["2"]["months"][0]["value"] == pytest.approx(0.06 * 31, abs=1e-4)
    assert tb["4"]["months"][0]["value"] == pytest.approx(30 * 4 / 1000 * 31, abs=1e-4)


def test_absent_hours_are_absent_not_zero_filled(client, pool):
    """A bank holding only HE1-3 yields a 3-row profile, not 24 rows of zeros."""
    rows = [r for r in _july_rows() if r["he"] <= 3]
    pool(_router(rows))
    body = client.get(f"/api/nodes/{NODE}/package").json()
    assert [c["he"] for c in body["profile"]] == [1, 2, 3]


def test_distribution_ships_all_bins_as_a_fixed_axis(client, pool):
    pool(_router())
    dist = client.get(f"/api/nodes/{NODE}/package").json()["distribution"]
    assert len(dist) == len(main._bp.DISTRIBUTION_BINS)
    by_bin = {c["bin"]: c["n"] for c in dist}
    # 496 peak hours at 40 -> (30,50]; 248 off-peak at 10 -> (0,10].
    assert by_bin["(30,50]"] == 496
    assert by_bin["(0,10]"] == 248
    assert by_bin["<=-40"] == 0          # empty bins are PRESENT


def test_thin_rt_suppresses_rt_block_averages_and_states_the_rule(client, pool):
    """rt_n = 0 everywhere -> no HE carries coverage -> every day is thin."""
    rows = _july_rows(rt=False)
    pool(_router(rows))
    body = client.get(f"/api/nodes/{NODE}/package").json()

    note = body["rt_coverage_note"]
    assert note["suppressed"] is True
    assert "20 of 24" in note["rule"]           # the rule is ON THE WIRE
    assert note["days_below_threshold"] == 31

    for block in body["blocks"].values():
        assert block["monthly"][0]["avg_rt"] is None
        assert block["monthly"][0]["avg_dam"] is not None   # DAM is unaffected
    for cell in body["blocks_daily"]:
        assert cell["avg_rt"] is None


def test_dense_rt_is_not_suppressed(client, pool):
    pool(_router())
    note = client.get(f"/api/nodes/{NODE}/package").json()["rt_coverage_note"]
    assert note["suppressed"] is False
    assert note["days_below_threshold"] == 0


# ═══════════════════════════════════════════════════════════════════════════
# 5. Error and empty paths
# ═══════════════════════════════════════════════════════════════════════════

def test_unknown_node_is_a_400_that_names_the_id(client, pool):
    pool(_router(rows=[], identity=False))
    r = client.get(f"/api/nodes/{UNKNOWN}/package")
    assert r.status_code == 400
    assert UNKNOWN in r.json()["detail"]


def test_known_node_with_nothing_banked_is_an_honest_200(client, pool):
    pool(_router(rows=[], identity=True))
    r = client.get(f"/api/nodes/{NODE}/package")
    assert r.status_code == 200
    body = r.json()
    assert body["window"]["days_in_window"] == 0
    assert body["profile"] == []
    assert body["blocks"] == {}
    assert body["identity"]["days_banked"] == 0
    # The bins still ship — a fixed axis, even with nothing in it.
    assert len(body["distribution"]) == len(main._bp.DISTRIBUTION_BINS)


# ═══════════════════════════════════════════════════════════════════════════
# 6. /api/hubs/{hub}/blocks
# ═══════════════════════════════════════════════════════════════════════════

def _hub_router(rows=None):
    rows = _july_rows() if rows is None else rows

    def route(query, params):
        q = " ".join(query.split())
        assert "timeseries_values" in q, q[:120]
        return [{"trade_date": r["trade_date"], "he": r["he"],
                 "dam_lmp": r["dam_lmp"]} for r in rows]
    return route


def test_hub_blocks_monthly_measures_its_own_depth(client, pool):
    pool(_hub_router())
    r = client.get("/api/hubs/SP15/blocks")
    assert r.status_code == 200
    body = r.json()
    assert body["hub"] == "SP15"
    assert body["depth"]["first_day"] == "2026-07-01"
    assert body["depth"]["last_day"] == "2026-07-31"
    assert body["depth"]["days_banked"] == 31
    # Same calendar as the node lane -> same hour split.
    assert body["blocks"]["6x16"]["monthly"][0]["hours"] == 416
    assert body["blocks"]["atc"]["monthly"][0]["avg_dam"] == pytest.approx(30.0)


def test_hub_reads_the_deep_series_not_the_nodal_table(client, pool):
    p = pool(_hub_router())
    client.get("/api/hubs/SP15/blocks")
    q = " ".join(p.sink["queries"][0].split())
    assert "timeseries_values" in q
    assert "atlas_node_hourly_stats" not in q
    assert p.sink["params"][0]["series"] == "SP15"
    assert p.sink["params"][0]["dataset"] == "caiso_lmp_da_hourly"


def test_hub_daily_states_its_bound_rather_than_truncating_silently(client, pool):
    pool(_hub_router())
    body = client.get("/api/hubs/SP15/blocks?granularity=daily").json()
    assert "blocks_daily" in body
    assert body["bound"]["days_available"] == 31
    assert "bounded" in body["bound"]["rule"]


def test_unknown_hub_is_a_400(client, pool):
    pool(_hub_router())
    r = client.get("/api/hubs/SP16/blocks")
    assert r.status_code == 400
    assert "SP15" in r.json()["detail"]


def test_preblocked_apnode_ids_are_not_hub_aliases(client, pool):
    """TH_SP15_GEN_ONPEAK-APND carries 16 HE/day. Accepting it as a hub alias
    would run the block calendar over pre-blocked data and misreport every
    block, so it is refused rather than resolved."""
    pool(_hub_router())
    r = client.get("/api/hubs/TH_SP15_GEN_ONPEAK-APND/blocks")
    assert r.status_code == 400


def test_bad_granularity_is_a_400(client, pool):
    pool(_hub_router())
    assert client.get("/api/hubs/SP15/blocks?granularity=hourly").status_code == 400


# ═══════════════════════════════════════════════════════════════════════════
# 7. /api/nodes/top-movers
# ═══════════════════════════════════════════════════════════════════════════

def _movers_router(last_day=D(2026, 8, 11), per_day=None):
    """per_day: {date: [(pnode_id, value, n_hours), ...]}"""
    per_day = per_day or {}

    def route(query, params):
        q = " ".join(query.split())
        if "max(trade_date) AS last_day" in q:
            return [{"last_day": last_day}]
        if "GROUP BY pnode_id" in q:
            day = params["day"]
            return [{"pnode_id": p, "v": v, "n_hours": n}
                    for p, v, n in per_day.get(day, [])]
        raise AssertionError(f"unrouted: {q[:120]}")
    return route


def test_top_movers_ranks_hour_weighted_across_chunks(client, pool):
    """A node priced 100 for 24 h on one day and 0 for 24 h on another averages
    50 — NOT the mean of the per-day means weighted equally by accident."""
    d1, d2 = D(2026, 8, 11), D(2026, 8, 10)
    pool(_movers_router(last_day=d1, per_day={
        d1: [("A", 100.0, 24), ("B", 10.0, 24)],
        d2: [("A", 0.0, 24),   ("B", 10.0, 12)],
    }))
    body = client.get("/api/nodes/top-movers?metric=dart&days=2").json()
    by_id = {m["pnode_id"]: m for m in body["movers"]}
    assert by_id["A"]["value"] == pytest.approx(50.0)
    assert by_id["A"]["days_present"] == 2
    assert by_id["A"]["hours"] == 48
    assert by_id["B"]["value"] == pytest.approx(10.0)
    assert by_id["B"]["hours"] == 36


def test_top_movers_direction_flips_the_sort(client, pool):
    d1 = D(2026, 8, 11)
    router = _movers_router(last_day=d1, per_day={
        d1: [("HI", 50.0, 24), ("LO", -50.0, 24)]})
    pool(router)
    up = client.get("/api/nodes/top-movers?days=1&direction=up").json()
    assert up["movers"][0]["pnode_id"] == "HI"
    main._nap_movers_cache.clear()
    pool(router)
    down = client.get("/api/nodes/top-movers?days=1&direction=down").json()
    assert down["movers"][0]["pnode_id"] == "LO"


def test_top_movers_window_is_read_not_hardcoded(client, pool):
    p = pool(_movers_router(last_day=D(2026, 8, 11), per_day={}))
    body = client.get("/api/nodes/top-movers?days=3").json()
    assert body["window"]["last_day"] == "2026-08-11"
    assert body["window"]["first_day"] == "2026-08-09"
    days = sorted(pp["day"] for pp in p.sink["params"] if pp and "day" in pp)
    assert days == [D(2026, 8, 9), D(2026, 8, 10), D(2026, 8, 11)]


def test_top_movers_caps_days_and_names_the_cap(client, pool):
    pool(_movers_router())
    r = client.get("/api/nodes/top-movers?days=30")
    assert r.status_code == 400
    assert "1..7" in r.json()["detail"]


def test_top_movers_rejects_an_unknown_metric(client, pool):
    pool(_movers_router())
    r = client.get("/api/nodes/top-movers?metric=lmp")
    assert r.status_code == 400
    assert "basis_sp15" in r.json()["detail"]


def test_top_movers_publishes_its_measured_plan(client, pool):
    pool(_movers_router(per_day={}))
    plan = client.get("/api/nodes/top-movers?days=1").json()["plan"]
    assert plan["cap_days"] == 7
    assert "seq scan" in plan["why_not_one_query"]
    assert plan["measured_ms_per_chunk"]["cold"] == 845


def test_top_movers_is_capped_at_50(client, pool):
    d1 = D(2026, 8, 11)
    pool(_movers_router(last_day=d1, per_day={
        d1: [(f"N{i:04d}", float(i), 24) for i in range(200)]}))
    body = client.get("/api/nodes/top-movers?days=1").json()
    assert body["count"] == 50
    assert body["universe"]["nodes_ranked"] == 200      # the honest denominator
