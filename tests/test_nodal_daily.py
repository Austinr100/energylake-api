"""
Tests for NODAL DAILY SERVING v0 (lane ruled 2026-07-31):

  * GET /api/analytics/node/{pnode_id}/daily
  * cb_only=true on /api/analytics/movers and /api/analytics/movers-grid
  * the shared pool's validate-on-checkout pre-ping

No live database is required — the route tests swap a tiny in-memory fake pool
into `main._pool`, dispatching canned rows by inspecting the query text, exactly
as the sibling suites do.

WHAT THESE TESTS EXIST TO PROTECT. Each of these is silent on the server and
wrong-on-the-glass at the client, which is why each has its own test:

  * THE RULES BLOCK IS A CONTRACT, NOT PROSE. The strings in
    `main.AN_DAILY_RULES` are carried from the COMMENTs on
    atlas_node_daily_stats and a client may key a caption or tooltip off them.
    The load-bearing CLAUSES are pinned verbatim here — the DART sign, the
    6x16-is-not-7x16 disclaimer, the tb4 unit caveat — so a reword that changes
    the MEANING fails rather than shipping.

  * NULL-VS-ABSENT. A null dart_onpk on a Sunday or a NERC holiday is the
    CALENDAR (no on-peak block exists), not a missing measurement. The same
    null on a Tuesday is a genuine absence. The server decides which, per row,
    so the client never re-derives it — and this suite drives both cases.

  * SATURDAY IS BOTH. is_weekend is true AND has_onpeak_block is true on a
    plain Saturday. A consumer conflating them is wrong 52 days a year, so the
    fixture carries a real Saturday and asserts the pair.

  * GAPS ARE REAL. days_returned must report what the node HAS, never
    days_requested, and no zero-filled row may appear to pad the axis.

  * THE CB VINTAGE CONTRACT. caiso_cb_eligible_nodes is vintaged and
    append-only; an unscoped read answers with nodes that were eligible at some
    point in history. The SQL must carry the max(vintage_date) scoping, the
    filter must narrow the ranking universe, and default-false must leave the
    public board's ranking untouched.

THE LUNDY FIXTURE is the live shape: LUNDY_7_N003 has exactly six banked rows
(2026-07-25..30) read out of the pantry on 2026-07-31, and 2026-07-26 is a
SUNDAY carrying dart_onpk NULL with hours_onpk_matched 0. That row is the
null-vs-absent case as it actually exists, not a hand-invented one.
"""

import datetime

import psycopg
import pytest
from fastapi.testclient import TestClient

import main


UTC = datetime.timezone.utc
D = datetime.date

NODE = "LUNDY_7_N003"


# ---------------------------------------------------------------------------
# Fake pool. Same shape as the sibling suites: canned rows dispatched by SQL
# text, with an optional "fail the first N executes" mode for the retry path.
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
            "exc": exc or psycopg.OperationalError(
                "SSL connection has been closed unexpectedly"),
        }

    def connection(self):
        return _FakeConn(self._router, self.sink, self.state)


@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.fixture(autouse=True)
def _clear_caches():
    main._an_movers_cache.clear()
    main._an_movers_grid_cache.clear()
    yield
    main._an_movers_cache.clear()
    main._an_movers_grid_cache.clear()


def use_router(router, fail_times=0, exc=None):
    pool = FakePool(router, fail_times=fail_times, exc=exc)
    main._pool = pool
    return pool


# ---------------------------------------------------------------------------
# Row builders — shaped as psycopg's dict_row yields them (numerics as floats
# here; the endpoint rounds through _an_r either way).
# ---------------------------------------------------------------------------

def daily_row(day, dow, weekend, holiday, dart, onpk, offpk,
              onpk_hours, offpk_hours, hours=24, tb4_da=50.0, tb4_rt=90.0,
              basis_da=None, basis_fmm=None, cb=True, month=7):
    return {
        "market_date": D(2026, month, day),
        "dow": dow,
        "is_weekend": weekend,
        "is_nerc_holiday": holiday,
        "dart_mean": dart,
        "dart_onpk": onpk,
        "dart_offpk": offpk,
        "tb4_da": tb4_da,
        "tb4_rt": tb4_rt,
        "basis_da": basis_da,
        "basis_fmm": basis_fmm,
        "hours_matched": hours,
        "hours_onpk_matched": onpk_hours,
        "hours_offpk_matched": offpk_hours,
        "cb_eligible": cb,
        "computed_at": datetime.datetime(2026, 7, 31, 9, 15, tzinfo=UTC),
    }


# LUNDY_7_N003's six live rows, read out of the pantry on 2026-07-31.
# 07-26 is the SUNDAY: dart_onpk NULL, hours_onpk_matched 0.
# 07-25 is a SATURDAY: on-peak block present, 16 hours — Saturday is BOTH.
LUNDY_ROWS = [
    daily_row(30, 3, False, False, 104.2500421875, 92.570636875, 127.6088528125,
              16, 8, tb4_da=70.6756725, tb4_rt=146.987592291667),
    daily_row(29, 2, False, False, -397.204930520833, -282.087536875,
              -627.4397178125, 16, 8, tb4_da=30.9012425, tb4_rt=906.863009375),
    daily_row(28, 1, False, False, -25.2539788541667, -36.72190046875,
              -2.318135625, 16, 8, tb4_da=67.0590275, tb4_rt=119.567295625),
    daily_row(27, 0, False, False, 27.1930570833333, 26.71807546875,
              28.1430203125, 16, 8, tb4_da=29.8871975, tb4_rt=47.4768475),
    # SUNDAY — no on-peak block exists.
    daily_row(26, 6, True, False, 41.7191817361111, None, 41.7191817361111,
              0, 24, tb4_da=83.26154, tb4_rt=60.8726225),
    # SATURDAY — weekend AND on-peak.
    daily_row(25, 5, True, False, 33.4968763194444, 39.5820083854167,
              21.3266121875, 16, 8, tb4_da=37.8061275, tb4_rt=90.8377510416667),
]

DEPTH_ROW = {"min_date": D(2026, 7, 25), "max_date": D(2026, 7, 30), "dates": 6}


def daily_router(rows=None, depth=None):
    def router(query, params):
        if "FROM atlas_node_daily_stats" in query and "min(market_date)" in query:
            return [depth if depth is not None else DEPTH_ROW]
        if "FROM atlas_node_daily_stats" in query:
            limit = params.get("days", 30)
            body = LUNDY_ROWS if rows is None else rows
            return body[:limit]
        raise AssertionError(f"unexpected query: {query[:120]}")
    return router


def get_daily(client, node=NODE, **params):
    return client.get(f"/api/analytics/node/{node}/daily", params=params)


# ===========================================================================
# 1. THE RULES BLOCK — pinned strings
# ===========================================================================

def test_rules_block_is_served_whole_on_every_response(client):
    use_router(daily_router())
    body = get_daily(client).json()
    assert set(body["rules"]) == {
        "dart_sign", "onpeak_block", "onpeak_null_is_calendar", "is_weekend",
        "tb4_unit", "tb4_full_day_gate", "basis", "hours_matched", "dow",
        "cb_eligible", "gaps",
    }
    # Served verbatim from the module constant — no per-request rewording.
    assert body["rules"] == main.AN_DAILY_RULES


def test_dart_sign_clause_is_pinned_verbatim():
    """The single most dangerous string in this lane. This repo carries BOTH
    conventions; a client that reads the wrong one inverts every chart."""
    rule = main.AN_DAILY_RULES["dart_sign"]
    assert "SIGN IS PINNED: positive = RT above DA" in rule
    assert "hourly-mean FMM minus DA" in rule
    assert "MATCHED hours only" in rule
    assert "NEGATION" in rule
    assert "fingerprints/build.py" in rule


def test_onpeak_block_clause_states_it_is_not_7x16():
    """The lane's explicit requirement: say so, in as many words."""
    rule = main.AN_DAILY_RULES["onpeak_block"]
    assert "HE7-HE22 inclusive, Monday-Saturday" in rule
    assert "EXCLUDING NERC holidays" in rule
    assert "This is NOT the 7x16 block" in rule
    assert "holiday-blind" in rule
    assert "must never be conflated" in rule


def test_onpeak_null_clause_says_calendar_not_absence():
    rule = main.AN_DAILY_RULES["onpeak_null_is_calendar"]
    assert "hours_onpk_matched = 0" in rule
    assert "every Sunday and every NERC holiday" in rule
    assert "no on-peak block at all" in rule
    assert "Never read a NULL there as a missing measurement" in rule


def test_tb4_unit_caveat_names_caiso_blocks_daily_and_the_ratio():
    rule = main.AN_DAILY_RULES["tb4_unit"]
    assert "$/MWh" in rule
    assert "caiso_blocks_daily.tb4" in rule
    assert "SUM of the extremes in $/MW-day" in rule
    assert "~4x this number" in rule
    assert "not comparable without dividing" in rule


def test_is_weekend_clause_warns_about_saturday():
    rule = main.AN_DAILY_RULES["is_weekend"]
    assert "Saturday is a weekend day AND an on-peak day" in rule
    assert "wrong 52 days a year" in rule


def test_gaps_clause_says_days_bounds_rows_not_a_span():
    rule = main.AN_DAILY_RULES["gaps"]
    assert "bounds ROWS, not a calendar span" in rule
    assert "never zero-filled, never interpolated" in rule


def test_cb_eligible_clause_states_the_point_in_time_stamp():
    rule = main.AN_DAILY_RULES["cb_eligible"]
    assert "point-in-time stamp, not a foreign key" in rule
    assert "NOT \"was it eligible on market_date\"" in rule


# ===========================================================================
# 2. NULL-VS-ABSENT SEMANTICS
# ===========================================================================

def test_sunday_onpeak_null_is_reported_as_calendar(client):
    """THE LANE'S HEADLINE CASE, on the live LUNDY row. 2026-07-26 is a Sunday
    with dart_onpk NULL — that is the calendar, not a data gap."""
    use_router(daily_router())
    body = get_daily(client).json()
    sunday = next(d for d in body["days"] if d["market_date"] == "2026-07-26")

    assert sunday["dow"] == 6
    assert sunday["is_weekend"] is True
    assert sunday["is_nerc_holiday"] is False
    assert sunday["hours_onpk_matched"] == 0
    assert sunday["dart_onpk"] is None
    assert sunday["has_onpeak_block"] is False
    assert sunday["null_semantics"]["dart_onpk"] == main.AN_DAILY_NULL_CALENDAR
    assert "calendar" in sunday["null_semantics"]["dart_onpk"]
    # The off-peak leg is PRESENT that day (all 24 hours are off-peak), so it
    # has nothing to explain.
    assert sunday["dart_offpk"] is not None
    assert sunday["null_semantics"]["dart_offpk"] is None


def test_nerc_holiday_onpeak_null_is_also_calendar(client):
    """A weekday NERC holiday has no on-peak block either — and after the
    calendar correction, Saturday 2026-07-04 is that holiday."""
    holiday_rows = [
        daily_row(4, 5, True, True, 12.0, None, 12.0, 0, 24),   # Sat Jul 4 2026
    ]
    use_router(daily_router(rows=holiday_rows))
    body = get_daily(client).json()
    row = body["days"][0]
    assert row["is_nerc_holiday"] is True
    assert row["is_weekend"] is True          # it is a Saturday
    assert row["has_onpeak_block"] is False   # ...and the holiday
    assert row["null_semantics"]["dart_onpk"] == main.AN_DAILY_NULL_CALENDAR


def test_weekday_onpeak_null_is_reported_as_absence(client):
    """The OTHER half. A Tuesday with zero matched on-peak hours is a genuine
    absence — the block existed and the data did not."""
    thin = [daily_row(28, 1, False, False, 5.0, None, 5.0, 0, 6, hours=6)]
    use_router(daily_router(rows=thin))
    row = get_daily(client).json()["days"][0]
    assert row["has_onpeak_block"] is True
    assert row["dart_onpk"] is None
    assert row["null_semantics"]["dart_onpk"] == main.AN_DAILY_NULL_ABSENT
    assert "absent" in row["null_semantics"]["dart_onpk"]
    # And the two verdicts are genuinely different strings.
    assert main.AN_DAILY_NULL_ABSENT != main.AN_DAILY_NULL_CALENDAR


def test_offpeak_null_on_a_peak_day_is_absence_not_calendar(client):
    """Off-peak exists on EVERY day, so a null there is never the calendar."""
    rows = [daily_row(27, 0, False, False, 9.0, 9.0, None, 16, 0, hours=16)]
    use_router(daily_router(rows=rows))
    row = get_daily(client).json()["days"][0]
    assert row["null_semantics"]["dart_offpk"] == main.AN_DAILY_NULL_ABSENT


def test_present_values_carry_no_explanation(client):
    use_router(daily_router())
    body = get_daily(client).json()
    monday = next(d for d in body["days"] if d["market_date"] == "2026-07-27")
    assert monday["dart_onpk"] is not None
    assert monday["null_semantics"] == {"dart_onpk": None, "dart_offpk": None}
    assert "there is nothing to explain about a number that exists" in \
        body["null_semantics_vocabulary"]["note"]


def test_null_semantics_vocabulary_matches_the_row_level_verdicts(client):
    use_router(daily_router())
    vocab = get_daily(client).json()["null_semantics_vocabulary"]
    assert vocab["calendar"] == main.AN_DAILY_NULL_CALENDAR
    assert vocab["absent"] == main.AN_DAILY_NULL_ABSENT


# ===========================================================================
# 3. SATURDAY IS BOTH — the 52-days-a-year trap
# ===========================================================================

def test_saturday_is_weekend_and_has_an_onpeak_block(client):
    use_router(daily_router())
    body = get_daily(client).json()
    saturday = next(d for d in body["days"] if d["market_date"] == "2026-07-25")
    assert saturday["dow"] == 5
    assert saturday["is_weekend"] is True
    assert saturday["has_onpeak_block"] is True      # <- NOT the inverse
    assert saturday["hours_onpk_matched"] == 16
    assert saturday["dart_onpk"] is not None
    assert saturday["null_semantics"]["dart_onpk"] is None


# ===========================================================================
# 4. GAPS ARE REAL — days_returned vs days_requested
# ===========================================================================

def test_days_returned_reports_what_exists_not_what_was_asked(client):
    """LUNDY has SIX banked rows. Asking for 30 returns six and says six."""
    use_router(daily_router())
    body = get_daily(client, days=30).json()
    assert body["days_requested"] == 30
    assert body["days_returned"] == 6
    assert len(body["days"]) == 6


def test_no_zero_fill_and_no_padded_dates(client):
    use_router(daily_router())
    body = get_daily(client, days=30).json()
    dates = [d["market_date"] for d in body["days"]]
    assert dates == ["2026-07-30", "2026-07-29", "2026-07-28",
                     "2026-07-27", "2026-07-26", "2026-07-25"]
    # Nothing fabricated: every row carries a real hours_matched.
    assert all(d["hours_matched"] > 0 for d in body["days"])


def test_a_real_gap_is_left_as_a_gap(client):
    """A hole INSIDE the window stays a hole — window_served spans six days
    while only four rows exist, and the count is what says so."""
    holed = [LUNDY_ROWS[0], LUNDY_ROWS[1], LUNDY_ROWS[4], LUNDY_ROWS[5]]
    use_router(daily_router(rows=holed))
    body = get_daily(client, days=30).json()
    assert body["days_returned"] == 4
    assert body["window_served"] == {"start": "2026-07-25", "end": "2026-07-30"}
    dates = [d["market_date"] for d in body["days"]]
    assert "2026-07-28" not in dates and "2026-07-27" not in dates


def test_newest_first_ordering(client):
    use_router(daily_router())
    body = get_daily(client).json()
    dates = [d["market_date"] for d in body["days"]]
    assert dates == sorted(dates, reverse=True)
    assert body["order"] == "market_date DESC (newest first)"
    assert "ORDER BY market_date DESC" in main._AN_DAILY_SQL


def test_window_served_is_null_when_nothing_is_banked(client):
    use_router(daily_router(rows=[]))
    body = get_daily(client).json()
    assert body["days_returned"] == 0
    assert body["days"] == []
    assert body["window_served"] is None


def test_unknown_node_is_200_with_depth_not_404(client):
    """House standard: a node that exists but has nothing to say is never a
    404 and never a fabricated flat line."""
    use_router(daily_router(rows=[]))
    resp = get_daily(client, node="NOT_A_NODE_7_X1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["days_returned"] == 0
    # The table's depth is still attached, so zero rows is interpretable.
    assert body["depth"]["min_date"] == "2026-07-25"
    assert body["depth"]["max_date"] == "2026-07-30"
    assert body["depth"]["dates"] == 6
    assert body["depth"]["source"] == "atlas_node_daily_stats"


# ===========================================================================
# 5. PARAMETERS, PLAN AND SHAPE
# ===========================================================================

def test_days_cap_is_90(client):
    use_router(daily_router())
    assert main.AN_DAILY_DAYS_MAX == 90
    assert get_daily(client, days=90).status_code == 200
    r = get_daily(client, days=91)
    assert r.status_code == 400
    assert "90" in r.json()["detail"]


def test_days_floor_is_1(client):
    use_router(daily_router())
    assert get_daily(client, days=1).status_code == 200
    assert get_daily(client, days=0).status_code == 400
    assert get_daily(client, days=-5).status_code == 400


def test_days_defaults_to_30(client):
    pool = use_router(daily_router())
    body = get_daily(client).json()
    assert body["days_requested"] == main.AN_DAILY_DAYS_DEFAULT == 30
    # And the default really reaches the LIMIT rather than being clamped later.
    assert pool.sink["params"][0]["days"] == 30


def test_blank_pnode_id_is_400(client):
    use_router(daily_router())
    r = get_daily(client, node="%20")
    assert r.status_code == 400
    assert "pnode_id is required" in r.json()["detail"]


def test_the_read_is_index_served_and_limit_bounded(client):
    pool = use_router(daily_router())
    get_daily(client, days=7)
    sql = pool.sink["queries"][0]
    assert "WHERE pnode_id = %(pnode_id)s" in sql
    assert "LIMIT %(days)s" in sql
    assert pool.sink["params"][0] == {"pnode_id": NODE, "days": 7}
    body = get_daily(client, days=7).json()
    assert body["query_plan"]["index"] == \
        "idx_node_daily_node_date (pnode_id, market_date DESC)"
    assert body["query_plan"]["reads"] == 2


def test_every_contract_column_reaches_the_wire(client):
    use_router(daily_router())
    row = get_daily(client).json()["days"][0]
    for key in ("market_date", "dow", "is_weekend", "is_nerc_holiday",
                "dart_mean", "dart_onpk", "dart_offpk", "tb4_da", "tb4_rt",
                "basis_da", "basis_fmm", "hours_matched", "hours_onpk_matched",
                "hours_offpk_matched", "cb_eligible"):
        assert key in row, key


def test_hour_counts_sum_to_hours_matched(client):
    """The table's own hours_sane CHECK, re-asserted on the wire."""
    use_router(daily_router())
    for row in get_daily(client).json()["days"]:
        assert row["hours_onpk_matched"] + row["hours_offpk_matched"] == \
            row["hours_matched"]


def test_basis_null_today_is_carried_as_null_not_zero(client):
    """Every basis value is NULL today (upstream th_hub gap). It must never be
    zero-filled, and the rules block must say why."""
    use_router(daily_router())
    body = get_daily(client).json()
    assert all(d["basis_da"] is None and d["basis_fmm"] is None
               for d in body["days"])
    assert "NULL FOR EVERY ROW" in body["rules"]["basis"]
    assert "upstream ingester gap" in body["rules"]["basis"]


def test_cb_eligible_null_is_preserved_as_null(client):
    """NULL means absent from the vintage, which is not False."""
    rows = [daily_row(30, 3, False, False, 1.0, 1.0, 1.0, 16, 8, cb=None)]
    use_router(daily_router(rows=rows))
    assert get_daily(client).json()["days"][0]["cb_eligible"] is None


def test_db_down_is_503(client):
    def boom(query, params):
        raise RuntimeError("connection refused")
    use_router(boom)
    r = get_daily(client)
    assert r.status_code == 503
    assert "db unavailable" in r.json()["detail"]


# ===========================================================================
# 6. THE CB-ELIGIBILITY FILTER
# ===========================================================================

def test_cb_sql_carries_the_vintage_scoping_contract():
    """The table's COMMENT states the consumer contract in one line. NEVER
    READ IT UNSCOPED — an unscoped read unions every vintage ever pulled."""
    sql = main._AN_CB_ELIGIBLE_SQL
    assert "WHERE cb_eligible" in sql
    assert "vintage_date = (SELECT max(vintage_date) FROM caiso_cb_eligible_nodes)" \
        in sql


def test_cb_vintage_rule_is_stated_on_the_wire():
    rule = main.AN_CB_VINTAGE_RULE
    assert "max(vintage_date)" in rule
    assert "UNSCOPED" in rule
    assert "Absent from the vintage != ineligible" in rule


def test_cb_block_reports_nulls_when_the_filter_is_off():
    """Off, nothing was read — so the counts are null, not zero. A 0 would
    read as 'no eligible nodes exist'."""
    block = main._an_cb_block(False, None, None, None)
    assert block["cb_only"] is False
    assert block["applied"] is False
    assert block["eligible_universe"] is None
    assert block["excluded_not_cb_eligible"] is None
    assert block["vintage_date"] is None
    assert "not measured, not zero" in block["note"]


def test_cb_block_reports_the_universe_count_when_on():
    block = main._an_cb_block(True, D(2026, 7, 16), 3082, 12000)
    assert block["cb_only"] is True
    assert block["applied"] is True
    assert block["vintage_date"] == "2026-07-16"
    assert block["eligible_universe"] == 3082
    assert block["excluded_not_cb_eligible"] == 12000
    assert block["note"] is None
    assert block["source"] == "caiso_cb_eligible_nodes"


# --- the filter end-to-end, through the movers route ------------------------

# A movers request makes several reads; this router answers each by SQL text.
# The universe is four nodes and only two of them are CB-eligible.
CB_ELIGIBLE = {"NODE_A", "NODE_C"}
ALL_NODES = ["NODE_A", "NODE_B", "NODE_C", "NODE_D"]


def movers_router(eligible=None, vintage=D(2026, 7, 16)):
    """A one-date, one-hour universe of four nodes.

    DAM prices HE12 once (k=1); RTPD prices it with four intervals (k=4). So
    the matched-hour set is a single hour, expected_da is 1 row per node and
    expected_rt is 4 — every node carries full coverage and all four rank.
    DART per node = rt_sum - da_sum, giving A=10, B=11, C=12, D=13.
    """
    elig = CB_ELIGIBLE if eligible is None else eligible

    def router(query, params):
        if "FROM (VALUES (%(da)s), (%(rt)s)) AS m(market)" in query:
            return [{"market": main.AN_DA_MARKET,
                     "min_date": D(2026, 7, 30), "max_date": D(2026, 7, 30)},
                    {"market": main.AN_RT_MARKET,
                     "min_date": D(2026, 7, 30), "max_date": D(2026, 7, 30)}]
        if "FROM caiso_cb_eligible_nodes" in query:
            return [{"node_id": n, "vintage_date": vintage} for n in sorted(elig)]
        if "ORDER BY s.pnode_id" in query and "LIMIT 1" in query:
            return [{"pnode_id": "NODE_A"}]
        if "count(*)::float8 AS k" in query:
            k = 1.0 if params.get("market") == main.AN_DA_MARKET else 4.0
            return [{"d": D(2026, 7, 30), "he": 12, "k": k}]
        if "GROUP BY 1, 2, 3" in query:
            return [{"pnode_id": n, "d": D(2026, 7, 30), "he": 12,
                     "v": 5.0, "k": 4} for n in ALL_NODES]
        if "FROM atlas_pnode_lmp_snapshot" in query or "AS g(he, k)" in query:
            # Both legs' chunk reads. Columns are the pinned chunk SQL's:
            # weighted_sum + n_rows.
            if params.get("market") == main.AN_DA_MARKET:
                return [{"pnode_id": n, "weighted_sum": 10.0 + i, "n_rows": 1}
                        for i, n in enumerate(ALL_NODES)]
            return [{"pnode_id": n, "weighted_sum": 20.0 + 2 * i, "n_rows": 4}
                    for i, n in enumerate(ALL_NODES)]
        raise AssertionError(f"unexpected query: {query[:140]}")
    return router


def test_cb_only_defaults_to_false_and_the_public_board_is_unchanged(client):
    use_router(movers_router())
    body = client.get("/api/analytics/movers",
                      params={"window": "1d", "metric": "dart"}).json()
    ranked = {e["pnode_id"] for e in body["up"]}
    assert ranked == set(ALL_NODES)          # the whole universe still ranks
    assert body["cb_filter"]["cb_only"] is False
    assert body["cb_filter"]["applied"] is False
    assert body["cb_filter"]["eligible_universe"] is None


def test_cb_only_false_does_not_read_the_eligibility_table(client):
    """Off costs ZERO extra reads — the public path must not pay for a filter
    it is not applying."""
    pool = use_router(movers_router())
    client.get("/api/analytics/movers", params={"window": "1d"})
    assert not any("caiso_cb_eligible_nodes" in q for q in pool.sink["queries"])


def test_cb_only_true_narrows_the_ranking_universe(client):
    pool = use_router(movers_router())
    body = client.get("/api/analytics/movers",
                      params={"window": "1d", "metric": "dart",
                              "cb_only": "true"}).json()
    ranked = {e["pnode_id"] for e in body["up"]}
    assert ranked == CB_ELIGIBLE
    assert "NODE_B" not in ranked and "NODE_D" not in ranked
    # And it did so by reading the vintaged table exactly once.
    assert sum("caiso_cb_eligible_nodes" in q for q in pool.sink["queries"]) == 1


def test_cb_only_echoes_the_filter_and_the_eligible_universe_count(client):
    use_router(movers_router())
    body = client.get("/api/analytics/movers",
                      params={"window": "1d", "cb_only": "true"}).json()
    cb = body["cb_filter"]
    assert cb["cb_only"] is True
    assert cb["applied"] is True
    assert cb["vintage_date"] == "2026-07-16"
    assert cb["eligible_universe"] == len(CB_ELIGIBLE) == 2
    assert cb["excluded_not_cb_eligible"] == len(ALL_NODES) - len(CB_ELIGIBLE) == 2
    assert cb["vintage_rule"] == main.AN_CB_VINTAGE_RULE


def test_cb_only_does_not_change_the_sweep_reads(client):
    """The filter is applied AFTER the sweep, so the pinned chunk plan — and
    its measured cost — is identical either way."""
    pool_off = use_router(movers_router())
    client.get("/api/analytics/movers", params={"window": "1d"})
    off = [q for q in pool_off.sink["queries"]
           if "caiso_cb_eligible_nodes" not in q]

    main._an_movers_cache.clear()
    pool_on = use_router(movers_router())
    client.get("/api/analytics/movers",
               params={"window": "1d", "cb_only": "true"})
    on = [q for q in pool_on.sink["queries"]
          if "caiso_cb_eligible_nodes" not in q]

    assert off == on


def test_cb_only_is_part_of_the_cache_key(client):
    """Otherwise a filtered board would be served from an unfiltered cache
    entry, or the reverse — the worst possible failure for this parameter."""
    use_router(movers_router())
    unfiltered = client.get("/api/analytics/movers",
                            params={"window": "1d"}).json()
    filtered = client.get("/api/analytics/movers",
                          params={"window": "1d", "cb_only": "true"}).json()
    assert unfiltered["cb_filter"]["applied"] is False
    assert filtered["cb_filter"]["applied"] is True
    assert {e["pnode_id"] for e in filtered["up"]} == CB_ELIGIBLE
    assert ("dart", "1d", False) in main._an_movers_cache
    assert ("dart", "1d", True) in main._an_movers_cache


def test_an_empty_vintage_ranks_nothing_rather_than_everything(client):
    """A filter that silently no-ops when its universe is empty is worse than
    an empty board — it silently un-filters."""
    use_router(movers_router(eligible=set()))
    body = client.get("/api/analytics/movers",
                      params={"window": "1d", "cb_only": "true"}).json()
    assert body["up"] == [] and body["down"] == []
    assert body["cb_filter"]["eligible_universe"] == 0
    assert body["cb_filter"]["vintage_date"] is None


# --- movers-grid passes it straight through ---------------------------------

def grid_router():
    """movers_router already answers the phase-2 per-HE read (GROUP BY 1, 2, 3),
    so the grid needs no additional canned rows."""
    return movers_router()


def test_movers_grid_forwards_cb_only_to_phase_1(client):
    use_router(grid_router())
    body = client.get("/api/analytics/movers-grid",
                      params={"window": "1d", "metric": "dart",
                              "cb_only": "true"}).json()
    assert body["cb_filter"]["cb_only"] is True
    assert body["cb_filter"]["eligible_universe"] == 2
    rows = {r["pnode_id"] for r in body["up"]}
    assert rows == CB_ELIGIBLE


def test_movers_grid_default_is_unfiltered(client):
    use_router(grid_router())
    body = client.get("/api/analytics/movers-grid",
                      params={"window": "1d"}).json()
    assert body["cb_filter"]["applied"] is False
    assert {r["pnode_id"] for r in body["up"]} == set(ALL_NODES)


def test_movers_grid_cb_only_is_part_of_its_cache_key(client):
    use_router(grid_router())
    client.get("/api/analytics/movers-grid", params={"window": "1d"})
    client.get("/api/analytics/movers-grid",
               params={"window": "1d", "cb_only": "true"})
    assert ("dart", "1d", False) in main._an_movers_grid_cache
    assert ("dart", "1d", True) in main._an_movers_grid_cache


# ===========================================================================
# 7. THE POOL PRE-PING (validate-on-checkout)
# ===========================================================================

class _PingConn:
    """A connection whose SELECT 1 fails the first `fail_times` calls."""

    def __init__(self, fail_times=0, exc=None):
        self.calls = []
        self.fail_times = fail_times
        self.exc = exc or psycopg.OperationalError(
            "SSL connection has been closed unexpectedly")

    async def execute(self, sql):
        self.calls.append(sql)
        if len(self.calls) <= self.fail_times:
            raise self.exc
        return None


@pytest.mark.anyio
async def test_pre_ping_runs_a_bare_select_1():
    conn = _PingConn()
    await main._pool_pre_ping(conn)
    assert conn.calls == ["SELECT 1"]


@pytest.mark.anyio
async def test_pre_ping_raises_on_a_dead_connection_so_the_pool_discards_it():
    """The raise IS the mechanism: psycopg_pool's check loop catches it, puts
    the dead connection back for disposal, and hands the caller a fresh one."""
    conn = _PingConn(fail_times=1)
    with pytest.raises(psycopg.OperationalError):
        await main._pool_pre_ping(conn)


@pytest.mark.anyio
async def test_pre_ping_raises_on_a_closed_interface_too():
    conn = _PingConn(fail_times=1, exc=psycopg.InterfaceError("connection closed"))
    with pytest.raises(psycopg.InterfaceError):
        await main._pool_pre_ping(conn)


@pytest.mark.anyio
async def test_pre_ping_passes_a_live_connection_through_quietly():
    conn = _PingConn(fail_times=0)
    assert await main._pool_pre_ping(conn) is None


def test_dead_connection_errors_cover_operational_and_interface():
    assert psycopg.OperationalError in main._POOL_DEAD_CONN_ERRORS
    assert psycopg.InterfaceError in main._POOL_DEAD_CONN_ERRORS


def test_the_shared_reader_still_retries_once_on_a_dead_connection(client):
    """The pre-ping cannot cover a connection that dies MID-statement, so the
    retry-once wrapper stays. First execute raises, second succeeds, and the
    route serves a clean 200 — this is the belt to the pre-ping's suspenders."""
    pool = use_router(daily_router(), fail_times=1)
    resp = get_daily(client)
    assert resp.status_code == 200
    assert resp.json()["days_returned"] == 6
    assert pool.state["attempts"] > 1


def test_a_connection_that_fails_twice_propagates_as_503(client):
    use_router(daily_router(), fail_times=99)
    assert get_daily(client).status_code == 503
