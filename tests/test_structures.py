"""
Tests for the Analytics Department, Room 2 — Structures (2026-07-30):

  GET  /api/analytics/structures/catalog
  POST /api/analytics/structures/evaluate
  GET  /api/analytics/structures/screener

THE ACCEPTANCE CENTREPIECE is test_hrco_november_2025_hand_derived: one full
HRCO month re-derived BY HAND from raw banked rows — power block average, gas
index average, heat_rate x gas + adder, and the daily-exercise sum — with both
legs shown. The 30 rows in _NOV_2025_SP15_7x16 are verbatim banked pantry rows
(caiso_lmp_da_hourly / SP15, and eia_gas_henry_hub_daily / HENRY_HUB), and the
expected figures below were computed independently IN SQL, in the pantry, then
pinned here. If the arithmetic in structures.py drifts, that test fails against
numbers this code never produced.

The suite also pins:
  * the three posture rails as PAYLOAD CONTENT, not just comments — a payload
    that loses them ships a pricing desk;
  * the DST-derived completeness gate (23/24/25-hour days), which is why a
    partial month can never sneak into a replay;
  * the block partition identity 7x16 x h16 + 7x8 x h8 == 7x24 x h24;
  * the Python mirror of the client's shape guard, for all three endpoints;
  * honest absence — a leg with no banked month returns a DIAGRAM and an empty
    replay carrying a reason, never a fabricated month.

No live database. Like the sibling suites this swaps a tiny in-memory fake pool
into `main._pool`; DB rows are faked as psycopg's dict_row would yield them.
"""

import datetime
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

import main
import structures as S


# ═══════════════════════════════════════════════════════════════════════════
# Fake pool — canned result sets, dispatched by which SQL the route ran.
# ═══════════════════════════════════════════════════════════════════════════

class _FakeCursor:
    def __init__(self, pool):
        self._pool = pool
        self._rows: list = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, query, params=None):
        self._pool.calls.append({"query": query, "params": params})
        if self._pool.raise_exc is not None:
            raise self._pool.raise_exc
        self._rows = self._pool.rows_for(query, params)

    async def fetchall(self):
        return list(self._rows)


class _FakeConn:
    def __init__(self, pool):
        self._pool = pool

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def cursor(self):
        return _FakeCursor(self._pool)


class FakePool:
    """Routes each SQL to a canned result set.

    `power` is keyed by series so a screener sweep can hand a different strip to
    each leg; `gas` is a flat list of {d, price}; `inventory` and `nodes` back
    the catalog. Anything unrecognized returns [] — which is itself a useful
    default, since it exercises the honest-absence paths.
    """

    def __init__(self, *, power=None, gas=None, inventory=None, nodes=None,
                 raise_exc=None):
        self.power = power or {}
        self.gas = gas or []
        self.inventory = inventory or []
        self.nodes = nodes or []
        self.raise_exc = raise_exc
        self.calls: list = []

    def connection(self):
        return _FakeConn(self)

    def rows_for(self, query, params):
        params = params or {}
        if "GROUP BY 1, 2" in query and "SUM(v.value" in query:
            wanted = set(params.get("series_list") or [])
            hours = set(params.get("hours") or [])
            out = []
            for series, strip in self.power.items():
                if series not in wanted:
                    continue
                for row in strip:
                    # The fake honours the block: a strip is stored per block id
                    # so a 7x24 read cannot silently serve 7x16 rows.
                    if row.get("hours_key") and row["hours_key"] != len(hours):
                        continue
                    out.append({"series": series, "d": row["d"],
                                "block_sum": row["block_sum"],
                                "n_hours": row["n_hours"]})
            return out
        if "v.value::numeric AS price" in query:
            return list(self.gas)
        if "days_held" in query:
            return list(self.inventory)
        if "suffix_pattern" in query:
            return list(self.nodes)
        if query.strip().startswith("SELECT 1 AS ok"):
            series = params.get("series")
            return [{"ok": 1}] if series in self.power else []
        return []


@pytest.fixture
def client(monkeypatch):
    def _make(pool):
        monkeypatch.setattr(main, "_pool", pool)
        monkeypatch.setattr(main, "_struct_catalog_cache", None)
        return TestClient(main.app)
    return _make


# ═══════════════════════════════════════════════════════════════════════════
# THE ACCEPTANCE CENTREPIECE — one full HRCO month, hand-derived
# ═══════════════════════════════════════════════════════════════════════════
#
# November 2025, SP15 7x16 against Henry Hub. Chosen because it is the month
# where BOTH HRCO variants land non-zero, so the daily-exercise-dominates-
# monthly-average result is visible rather than asserted (see below).
#
# POWER LEG — verbatim banked rows: caiso_lmp_da_hourly / SP15, summed over the
# 16 on-peak PT hours of each Pacific day. Every day carries exactly 16 hours,
# so November 2025 holds 30 x 16 = 480 block hours and passes the gate whole.
#
# GAS LEG — verbatim banked rows: eia_gas_henry_hub_daily / HENRY_HUB, a
# BUSINESS-DAY index. Only 17 of November's days carry a print; the other 13
# resolve by carrying the last trade day forward (the gas-day convention — the
# weekend package prices Sat/Sun/Mon). The `gas_trade_date` column records
# where each print came from, so every carry is visible:
#   * Nov 1-2   <- Oct 31 (carry-in from the prior month, spans 1 and 2)
#   * Nov 8-9   <- Nov 7   * Nov 11    <- Nov 10 (no Veterans Day print)
#   * Nov 15-16 <- Nov 14  * Nov 22-23 <- Nov 21
#   * Nov 27-30 <- Nov 26 (Thanksgiving — the longest carry, span 4)
# Span 4 is exactly the gate's limit, so this month is also the boundary case
# for max_gas_fill_span_days.
#
# (day, 7x16 price sum, gas $/MMBtu, gas trade date)
_NOV_2025_SP15_7x16 = [
    (1,  "399.138850", "3.57", (2025, 10, 31)),
    (2,  "411.221610", "3.57", (2025, 10, 31)),
    (3,  "658.738360", "3.37", (2025, 11, 3)),
    (4,  "606.368320", "3.35", (2025, 11, 4)),
    (5,  "563.068960", "3.51", (2025, 11, 5)),
    (6,  "300.981100", "3.71", (2025, 11, 6)),
    (7,  "436.587640", "3.76", (2025, 11, 7)),
    (8,  "340.138650", "3.76", (2025, 11, 7)),
    (9,  "315.756210", "3.76", (2025, 11, 7)),
    (10, "578.134150", "3.80", (2025, 11, 10)),
    (11, "738.534320", "3.80", (2025, 11, 10)),
    (12, "800.576450", "3.60", (2025, 11, 12)),
    (13, "631.827510", "3.60", (2025, 11, 13)),
    (14, "801.600480", "3.49", (2025, 11, 14)),
    (15, "743.286670", "3.49", (2025, 11, 14)),
    (16, "573.375290", "3.49", (2025, 11, 14)),
    (17, "723.825320", "3.71", (2025, 11, 17)),
    (18, "772.647880", "3.70", (2025, 11, 18)),
    (19, "733.905890", "3.94", (2025, 11, 19)),
    (20, "728.400740", "3.97", (2025, 11, 20)),
    (21, "807.449840", "4.13", (2025, 11, 21)),
    (22, "694.087760", "4.13", (2025, 11, 21)),
    (23, "395.645100", "4.13", (2025, 11, 21)),
    (24, "605.171890", "4.15", (2025, 11, 24)),
    (25, "699.123200", "4.12", (2025, 11, 25)),
    (26, "694.152560", "4.59", (2025, 11, 26)),
    (27, "745.255660", "4.59", (2025, 11, 26)),
    (28, "529.291280", "4.59", (2025, 11, 26)),
    (29, "614.633410", "4.59", (2025, 11, 26)),
    (30, "470.688430", "4.59", (2025, 11, 26)),
]

# The HRCO under test: a 50 MW heat-rate call option, 7.5 MMBtu/MWh against
# Henry Hub, $3.50/MWh VOM adder, on the SP15 7x16 block.
_HRCO_HEAT_RATE = 7.5
_HRCO_VOM = 3.50
_HRCO_MW = 50.0

# ── THE HAND DERIVATION ──────────────────────────────────────────────────────
# Every figure below was computed independently in SQL against the pantry and
# is pinned here as a literal. Nothing in this block is produced by the code
# under test.
#
# POWER LEG:
#   sum of the 480 hourly prices          = 17,713.02...  (the 30 day-sums above)
#   block hours                           = 30 days x 16 = 480
#   November 7x16 block average           = 37.736695 $/MWh
#
# GAS LEG (day-weighted over all 30 flow days, each carrying its gas-day print):
#   November Henry Hub average            = 3.885333 $/MMBtu
#   trade days actually held              = 17
#   longest carry                         = 4 days (Thanksgiving)
#
# STRIKE (monthly-average variant):
#   heat_rate x gas + adder = 7.5 x 3.885333... + 3.50
#                           = 29.14 + 3.50   = 32.640000 $/MWh   (exact)
#
# MONTHLY-AVERAGE EXERCISE — one decision for the whole month:
#   max(0, 37.736695 - 32.640000)         =  5.096695 $/MWh
#   MWh = 50 MW x 480 h                   = 24,000 MWh
#   payoff                                = $122,320.6765
#
# DAILY EXERCISE — 30 separate decisions, each against THAT day's gas, summed:
#   e.g. Nov 3: day avg 658.738360/16 = 41.17114750; strike 7.5 x 3.37 + 3.50
#        = 28.775; payoff (41.17114750 - 28.775) x 50 MW x 16 h = $9,916.918
#   21 of 30 days exercise; 9 finish below their strike and contribute zero.
#   (Not to be confused with the 17 GAS TRADE DAYS held — a different count.)
#   payoff per MWh                        =  7.507843 $/MWh
#   payoff                                = $180,188.2330
#
# THE TWO ARE NOT EQUAL, AND THE ORDER IS NOT AN ACCIDENT: a sum of maxes
# dominates the max of a sum, so daily exercise ($180,188) beats monthly-average
# ($122,320) on identical inputs. Both are offered because that $57,867 gap IS
# the distinction a structuring conversation turns on.
_HRCO_EXPECT_BLOCK_HOURS = 480
_HRCO_EXPECT_BLOCK_AVG = 37.736695
_HRCO_EXPECT_GAS_AVG = 3.885333
_HRCO_EXPECT_GAS_DAYS_HELD = 17
_HRCO_EXPECT_MAX_FILL = 4
_HRCO_EXPECT_STRIKE = 32.640000
_HRCO_EXPECT_MWH = 24000.0
_HRCO_EXPECT_MONTHLY_PER_MWH = 5.096695
_HRCO_EXPECT_MONTHLY_TOTAL = 122320.6765
_HRCO_EXPECT_DAILY_PER_MWH = 7.507843
_HRCO_EXPECT_DAILY_TOTAL = 180188.2330


def _nov_rows_and_gas():
    """The banked November 2025 rows, in the shape the route layer hands over."""
    rows, gas = [], {}
    for dd, psum, gx, gt in _NOV_2025_SP15_7x16:
        day = date(2025, 11, dd)
        trade = date(*gt)
        rows.append({"day": day, "block_sum": Decimal(psum), "n_hours": 16})
        gas[day] = {"price": float(gx), "trade_date": trade,
                    "fill_span_days": (day - trade).days}
    return rows, gas


def _hrco_definition(kind):
    return S.normalize_definition({
        "structure": kind,
        "leg": {"kind": "hub", "id": "SP15"},
        "block": "7x16",
        "size_mw": _HRCO_MW,
        "heat_rate": _HRCO_HEAT_RATE,
        "vom_adder": _HRCO_VOM,
        "gas_index": "eia_hh.HENRY_HUB",
    })


def test_hrco_november_2025_hand_derived():
    """ACCEPTANCE #1 — one full HRCO month re-derived by hand, both legs shown.

    Pins the power block average, the gas index average, the heat_rate x gas +
    adder strike, and the daily-exercise sum against figures computed
    independently in SQL.
    """
    rows, gas = _nov_rows_and_gas()

    monthly = S.replay(_hrco_definition("hrco_monthly"), rows, gas)
    daily = S.replay(_hrco_definition("hrco_daily"), rows, gas)

    assert monthly["months"] == 1, monthly["months_rejected"]
    assert daily["months"] == 1, daily["months_rejected"]
    m, d = monthly["rows"][0], daily["rows"][0]

    # ── The power leg ────────────────────────────────────────────────────────
    assert m["month"] == "2025-11"
    assert m["days"] == 30
    assert m["block_hours"] == _HRCO_EXPECT_BLOCK_HOURS
    assert m["block_avg"] == _HRCO_EXPECT_BLOCK_AVG
    assert m["mwh"] == _HRCO_EXPECT_MWH

    # ── The gas leg ──────────────────────────────────────────────────────────
    assert m["gas_avg"] == _HRCO_EXPECT_GAS_AVG
    assert m["gas_days_held"] == _HRCO_EXPECT_GAS_DAYS_HELD
    assert m["max_gas_fill_span_days"] == _HRCO_EXPECT_MAX_FILL

    # ── The strike: heat_rate x gas + adder ──────────────────────────────────
    assert m["strike"] == _HRCO_EXPECT_STRIKE
    assert d["strike"] == _HRCO_EXPECT_STRIKE  # the monthly strike, reported

    # ── Monthly-average exercise ─────────────────────────────────────────────
    assert m["payoff_per_mwh"] == _HRCO_EXPECT_MONTHLY_PER_MWH
    assert m["payoff"] == _HRCO_EXPECT_MONTHLY_TOTAL

    # ── Daily exercise, summed over the month ────────────────────────────────
    assert d["payoff_per_mwh"] == _HRCO_EXPECT_DAILY_PER_MWH
    assert d["payoff"] == _HRCO_EXPECT_DAILY_TOTAL


def test_daily_exercise_dominates_monthly_average():
    """A sum of maxes dominates the max of a sum — the two HRCO variants exist
    precisely because that gap is real. If this ever inverts, the daily leg is
    averaging when it should be summing."""
    rows, gas = _nov_rows_and_gas()
    monthly = S.replay(_hrco_definition("hrco_monthly"), rows, gas)
    daily = S.replay(_hrco_definition("hrco_daily"), rows, gas)
    assert daily["total"] > monthly["total"]
    assert round(daily["total"] - monthly["total"], 4) == 57867.5565


def test_hrco_day_by_day_reconstructs_the_daily_total():
    """The daily-exercise sum re-derived here, in the test, from the raw rows —
    an independent path to the same number the engine reports."""
    rows, gas = _nov_rows_and_gas()
    total = 0.0
    exercised = 0
    for r in rows:
        day_avg = float(r["block_sum"]) / r["n_hours"]
        day_strike = _HRCO_HEAT_RATE * gas[r["day"]]["price"] + _HRCO_VOM
        pay = max(0.0, day_avg - day_strike)
        if pay > 0:
            exercised += 1
        total += pay * _HRCO_MW * r["n_hours"]
    # 21 of 30 days finish above their strike; the other 9 contribute zero.
    # (Distinct from the 17 gas trade days held — see the derivation above.)
    assert exercised == 21
    assert round(total, 4) == _HRCO_EXPECT_DAILY_TOTAL


def test_gas_carry_forward_is_the_gas_day_convention_not_interpolation():
    """Every carried day repeats a REAL trade-day print. No value in the gas
    series is ever averaged, smoothed, or interpolated into existence."""
    _, gas = _nov_rows_and_gas()
    # The real trade-day prints, taken from the FIXTURE rather than from the
    # resolved map — the Nov 1-2 carry-in comes from Oct 31, which never
    # appears as a span-0 day inside November.
    held = {date(*gt): float(gx) for _dd, _psum, gx, gt in _NOV_2025_SP15_7x16}
    for day, g in gas.items():
        assert g["price"] == held[g["trade_date"]]
        assert g["trade_date"] <= day
    assert max(g["fill_span_days"] for g in gas.values()) == _HRCO_EXPECT_MAX_FILL


def test_a_gas_hole_longer_than_a_holiday_weekend_gates_the_month():
    """A five-day carry is not a weekend — it is a hole in the index, and the
    month is gated out with a receipt rather than replayed."""
    rows, gas = _nov_rows_and_gas()
    gas[date(2025, 11, 30)] = {"price": 4.59, "trade_date": date(2025, 11, 25),
                               "fill_span_days": 5}
    rep = S.replay(_hrco_definition("hrco_monthly"), rows, gas)
    assert rep["months"] == 0
    assert not rep["available"]
    (rejected,) = rep["months_rejected"]
    assert rejected["month"] == "2025-11"
    assert "fill span" in rejected["reason"]
    assert rejected["max_fill_span_days"] == 5
    assert rejected["max_fill_span_allowed"] == 4


def test_an_unresolved_gas_day_gates_the_month():
    rows, gas = _nov_rows_and_gas()
    gas[date(2025, 11, 14)] = {"price": None, "trade_date": None,
                               "fill_span_days": None}
    rep = S.replay(_hrco_definition("hrco_monthly"), rows, gas)
    assert rep["months"] == 0
    (rejected,) = rep["months_rejected"]
    assert rejected["gas_days_unresolved"] == 1
    assert "does not cover the month" in rejected["reason"]


# ═══════════════════════════════════════════════════════════════════════════
# ACCEPTANCE #2 — the captain's own example: an Asian call at K=$26 on SP15 7x16
# ═══════════════════════════════════════════════════════════════════════════

def test_asian_call_k26_on_sp15_7x16_over_banked_depth():
    """The $26-strike 7x16 example, replayed over a multi-month banked strip.

    Uses the November 2025 rows plus a synthetic October so the cumulative
    series, the total, and the depth statement are all exercised. The payoff
    arithmetic is max(0, block avg - 26) x MWh, per month, and nothing else.
    """
    rows, _ = _nov_rows_and_gas()
    # A complete October 2025: 31 days x 16 hours, each day flat at $20/MWh —
    # deliberately BELOW the strike so the month banks and pays zero, proving
    # a zero month is replayed rather than dropped.
    oct_rows = [
        {"day": date(2025, 10, dd), "block_sum": Decimal("320.00"), "n_hours": 16}
        for dd in range(1, 32)
    ]
    defn = S.normalize_definition({
        "structure": "asian_call",
        "leg": {"kind": "hub", "id": "SP15"},
        "block": "7x16", "size_mw": 1.0, "strike": 26.0,
    })
    rep = S.replay(defn, oct_rows + rows)

    assert rep["months"] == 2
    assert [r["month"] for r in rep["rows"]] == ["2025-10", "2025-11"]

    october, november = rep["rows"]
    assert october["block_avg"] == 20.0
    assert october["payoff_per_mwh"] == 0.0      # $20 average, $26 strike
    assert october["payoff"] == 0.0
    assert october["mwh"] == 496.0               # 31 days x 16 h x 1 MW

    assert november["block_avg"] == _HRCO_EXPECT_BLOCK_AVG
    # max(0, 37.7366948958... - 26) = 11.736695 $/MWh (reported at 6dp) over
    # 480 MWh. The dollar total is struck from the UNROUNDED rate, so it is
    # 5633.6135 and not 480 x the rounded rate — rates are for reading, totals
    # are for settling, and the engine never compounds a display rounding.
    assert november["payoff_per_mwh"] == 11.736695
    assert november["payoff"] == 5633.6135

    assert rep["total"] == november["payoff"]
    assert rep["cumulative"][-1]["cumulative"] == rep["total"]
    assert rep["depth_statement"] == (
        "2 banked complete months (2025-10 through 2025-11) — every month "
        "whole, none partial."
    )
    assert "would have paid" in rep["note"].lower()


def test_asian_put_is_the_mirror():
    defn = S.normalize_definition({
        "structure": "asian_put", "leg": {"kind": "hub", "id": "SP15"},
        "block": "7x16", "size_mw": 1.0, "strike": 26.0,
    })
    assert S.payoff_per_mwh(defn, 20.0) == 6.0
    assert S.payoff_per_mwh(defn, 30.0) == 0.0


def test_swap_is_two_sided():
    defn = S.normalize_definition({
        "structure": "swap", "leg": {"kind": "hub", "id": "SP15"},
        "block": "7x16", "size_mw": 1.0, "fixed_price": 26.0,
    })
    assert S.payoff_per_mwh(defn, 30.0) == 4.0
    assert S.payoff_per_mwh(defn, 20.0) == -6.0     # a swap CAN lose money
    assert S.breakeven(defn) == 26.0


def test_digital_pays_a_lump_sum_that_does_not_scale_with_mw():
    """The 'or fixed' case: a fixed amount if the monthly average clears K."""
    defn = S.normalize_definition({
        "structure": "asian_digital_call", "leg": {"kind": "hub", "id": "SP15"},
        "block": "7x16", "size_mw": 500.0, "strike": 26.0, "payout": 100_000.0,
    })
    # size_mw is 500 and mwh is large, yet the payout is unchanged.
    assert S.payoff_total(defn, 30.0, 240_000.0) == 100_000.0
    assert S.payoff_total(defn, 20.0, 240_000.0) == 0.0
    assert S.payoff_total(defn, 26.0, 240_000.0) == 100_000.0   # clears AT K
    assert S.breakeven(defn) is None                             # steps, no cross
    assert defn["y_basis"] == "total"


def test_digital_put_mirrors_at_or_below_the_strike():
    defn = S.normalize_definition({
        "structure": "asian_digital_put", "leg": {"kind": "hub", "id": "SP15"},
        "block": "7x16", "size_mw": 1.0, "strike": 26.0, "payout": 50_000.0,
    })
    assert S.payoff_total(defn, 25.0, 480.0) == 50_000.0
    assert S.payoff_total(defn, 26.0, 480.0) == 50_000.0
    assert S.payoff_total(defn, 27.0, 480.0) == 0.0


# ═══════════════════════════════════════════════════════════════════════════
# RAIL 2 — the depth-honest completeness gate
# ═══════════════════════════════════════════════════════════════════════════

def test_expected_block_hours_are_derived_from_the_tz_database():
    """7x16 is 16 hours on every day of the year; 7x24 is 23 on spring-forward
    and 25 on fall-back; 7x8 absorbs the whole DST effect. Derived per date, so
    the gate stays correct without a hardcoded DST calendar."""
    spring, fall, plain = date(2026, 3, 8), date(2026, 11, 1), date(2026, 6, 15)
    assert S.expected_block_hours(spring, "7x16") == 16
    assert S.expected_block_hours(fall, "7x16") == 16
    assert S.expected_block_hours(plain, "7x16") == 16
    assert S.expected_block_hours(spring, "7x24") == 23
    assert S.expected_block_hours(fall, "7x24") == 25
    assert S.expected_block_hours(plain, "7x24") == 24
    assert S.expected_block_hours(spring, "7x8") == 7
    assert S.expected_block_hours(fall, "7x8") == 9
    assert S.expected_block_hours(plain, "7x8") == 8


def test_dst_months_expect_the_right_hour_totals():
    assert S.expected_month_hours(2026, 3, "7x24") == (31, 743)   # 31x24 - 1
    assert S.expected_month_hours(2025, 11, "7x24") == (30, 721)  # 30x24 + 1
    assert S.expected_month_hours(2025, 11, "7x16") == (30, 480)


def test_the_block_partition_identity_holds():
    """7x16 and 7x8 are disjoint, cover 7x24 exactly, and their hour-weighted
    averages reconcile — the menu arbiters itself."""
    for day in (date(2026, 3, 8), date(2026, 11, 1), date(2026, 6, 15)):
        h16 = S.expected_block_hours(day, "7x16")
        h8 = S.expected_block_hours(day, "7x8")
        h24 = S.expected_block_hours(day, "7x24")
        assert h16 + h8 == h24


def test_a_partial_month_is_gated_whole_with_a_receipt():
    """A month missing even one day is NOT replayed. A monthly-average structure
    settles on the month's average, and a partial month's average is a
    different number."""
    rows, _ = _nov_rows_and_gas()
    short = rows[:-1]                       # drop 2025-11-30
    defn = S.normalize_definition({
        "structure": "asian_call", "leg": {"kind": "hub", "id": "SP15"},
        "block": "7x16", "size_mw": 1.0, "strike": 26.0,
    })
    rep = S.replay(defn, short)
    assert rep["months"] == 0
    assert not rep["available"]
    (rejected,) = rep["months_rejected"]
    assert rejected == {
        "month": "2025-11", "reason": "incomplete power month",
        "days_present": 29, "days_in_month": 30,
        "block_hours_present": 464, "block_hours_expected": 480,
    }
    assert "gate" in rep["reason"] and "partial month is not replayed" in rep["reason"]


def test_a_month_missing_hours_but_not_days_is_still_gated():
    """Every day present, one day short an hour — still a partial month."""
    rows, _ = _nov_rows_and_gas()
    rows[10] = {**rows[10], "n_hours": 15}
    defn = S.normalize_definition({
        "structure": "asian_call", "leg": {"kind": "hub", "id": "SP15"},
        "block": "7x16", "size_mw": 1.0, "strike": 26.0,
    })
    rep = S.replay(defn, rows)
    assert rep["months"] == 0
    (rejected,) = rep["months_rejected"]
    assert rejected["days_present"] == 30
    assert rejected["block_hours_present"] == 479


def test_every_replay_carries_a_depth_statement():
    """RAIL 2 has no exceptions — including the empty case."""
    defn = S.normalize_definition({
        "structure": "asian_call", "leg": {"kind": "hub", "id": "SP15"},
        "block": "7x16", "size_mw": 1.0, "strike": 26.0,
    })
    empty = S.replay(defn, [])
    assert empty["depth_statement"] == "0 banked complete months — nothing to replay."
    assert empty["months"] == 0 and not empty["available"] and empty["reason"]

    rows, _ = _nov_rows_and_gas()
    one = S.replay(defn, rows)
    assert one["depth_statement"] == "1 banked complete month (2025-11)."


def test_window_months_trims_banked_months_not_calendar_months():
    """The window counts BANKED months, so a gated month inside it cannot
    silently shorten the replay."""
    rows, _ = _nov_rows_and_gas()
    oct_rows = [
        {"day": date(2025, 10, dd), "block_sum": Decimal("320.00"), "n_hours": 16}
        for dd in range(1, 32)
    ]
    defn = S.normalize_definition({
        "structure": "asian_call", "leg": {"kind": "hub", "id": "SP15"},
        "block": "7x16", "size_mw": 1.0, "strike": 26.0, "window_months": 1,
    })
    rep = S.replay(defn, oct_rows + rows)
    assert rep["months"] == 1
    assert rep["rows"][0]["month"] == "2025-11"          # the LAST banked month
    assert rep["window"]["banked_months_available"] == 2
    assert rep["window"]["months_trimmed_by_window"] == 1


# ═══════════════════════════════════════════════════════════════════════════
# RAIL 1 — the payoff diagram is a pure function of the inputs
# ═══════════════════════════════════════════════════════════════════════════

def test_the_diagram_needs_no_data_and_marks_the_breakeven():
    defn = S.normalize_definition({
        "structure": "asian_call", "leg": {"kind": "node", "id": "NOTHING_BANKED"},
        "block": "7x16", "size_mw": 1.0, "strike": 26.0,
    })
    dg = S.payoff_diagram(defn)
    assert dg["breakeven"] == 26.0
    assert any(p["settle"] == 26.0 for p in dg["points"])   # the kink is sampled
    for p in dg["points"]:
        expected = max(0.0, p["settle"] - 26.0)
        assert p["payoff_per_mwh"] == pytest.approx(expected, abs=1e-6)
    assert "not what the settle will be" in dg["note"]


def test_the_diagram_spans_negative_settles():
    """Negative CAISO prices are REAL (see the fuel-mix note in main.py), so the
    payoff shape must not be clamped at zero on the x axis."""
    defn = S.normalize_definition({
        "structure": "swap", "leg": {"kind": "hub", "id": "SP15"},
        "block": "7x16", "size_mw": 1.0, "fixed_price": 26.0,
    })
    dg = S.payoff_diagram(defn)
    assert min(p["settle"] for p in dg["points"]) < 0.0
    assert min(p["payoff_per_mwh"] for p in dg["points"]) < 0.0


def test_an_hrco_diagram_is_struck_off_a_stated_gas_price():
    """RAIL 1: the strike comes from a REALIZED gas print, passed in and
    labelled. The pure layer never reaches for a forward mark."""
    defn = _hrco_definition("hrco_monthly")
    dg = S.payoff_diagram(defn, strike=32.64)
    assert dg["strike_used"] == 32.64
    assert dg["breakeven"] == 32.64
    assert S.payoff_per_mwh(defn, 40.0, strike=32.64) == pytest.approx(7.36)


def test_no_forward_pricing_vocabulary_anywhere_in_the_module():
    """RAIL 1, mechanically: this room has no valuation vocabulary. A premium,
    a vol, or a greek appearing here means someone built a pricing desk."""
    import inspect
    src = inspect.getsource(S).lower()
    for banned in ("black_scholes", "black76", "implied_vol", "vol_surface",
                   "premium_value", "discount_factor", "forward_curve", "delta_hedge"):
        assert banned not in src, f"pricing-desk vocabulary leaked in: {banned}"


# ═══════════════════════════════════════════════════════════════════════════
# RAIL 3 — the screener is a descriptive sort
# ═══════════════════════════════════════════════════════════════════════════

def test_rank_legs_sorts_and_keeps_unavailable_legs_visible():
    results = [
        {"leg": {"kind": "hub", "id": "SP15"}, "months": 118, "total": 900.0},
        {"leg": {"kind": "hub", "id": "NP15"}, "months": 118, "total": 1500.0},
        {"leg": {"kind": "node", "id": "AGRICO_7_UNIT"}, "months": 0,
         "total": 0.0, "reason": "no banked complete month"},
    ]
    out = S.rank_legs(results)
    assert [r["leg"]["id"] for r in out["ranked"]] == ["NP15", "SP15"]
    assert [r["rank"] for r in out["ranked"]] == [1, 2]
    # The unreplayable leg is REPORTED, not dropped — a leg missing from a
    # ranking would itself read as a ranking.
    assert out["unavailable"] == [
        {"leg": {"kind": "node", "id": "AGRICO_7_UNIT"},
         "reason": "no banked complete month", "months": 0}
    ]
    assert "descriptive sort" in out["note"]
    assert "not a recommendation register" in out["note"].lower()


def test_rank_legs_refuses_to_imply_a_shared_window_when_depth_differs():
    """Hub strips run years, nodal legs run weeks. Ranking totals across
    unequal windows compares a decade against a fortnight, so the payload says
    so instead of hiding it."""
    mixed = S.rank_legs([
        {"leg": {"kind": "hub", "id": "SP15"}, "months": 118, "total": 900.0},
        {"leg": {"kind": "node", "id": "X"}, "months": 1, "total": 1500.0},
    ])
    assert mixed["comparable_window"] is False
    assert mixed["months_spread"] == {"min": 1, "max": 118}
    assert "do NOT share a window" in mixed["note"]

    even = S.rank_legs([
        {"leg": {"kind": "hub", "id": "SP15"}, "months": 118, "total": 900.0},
        {"leg": {"kind": "hub", "id": "NP15"}, "months": 118, "total": 1500.0},
    ])
    assert even["comparable_window"] is True
    assert "do NOT share a window" not in even["note"]


def test_rank_legs_ascending_finds_the_other_direction():
    out = S.rank_legs([
        {"leg": {"kind": "hub", "id": "SP15"}, "months": 2, "total": 900.0},
        {"leg": {"kind": "hub", "id": "NP15"}, "months": 2, "total": 1500.0},
    ], direction="asc")
    assert [r["leg"]["id"] for r in out["ranked"]] == ["SP15", "NP15"]


def test_rank_legs_rejects_a_bad_direction():
    with pytest.raises(S.StructureError):
        S.rank_legs([], direction="sideways")


# ═══════════════════════════════════════════════════════════════════════════
# The definition contract
# ═══════════════════════════════════════════════════════════════════════════

def test_normalize_rejects_unknown_structures_blocks_and_legs():
    for raw, field in [
        ({"structure": "straddle", "leg": {"kind": "hub", "id": "SP15"}}, "structure"),
        ({"structure": "swap", "block": "5x16", "fixed_price": 1.0,
          "leg": {"kind": "hub", "id": "SP15"}}, "block"),
        ({"structure": "swap", "fixed_price": 1.0,
          "leg": {"kind": "zone", "id": "SP15"}}, "leg.kind"),
        ({"structure": "swap", "fixed_price": 1.0, "leg": {"kind": "hub", "id": " "}}, "leg.id"),
        ({"structure": "swap", "leg": {"kind": "hub", "id": "SP15"}}, "fixed_price"),
        ({"structure": "asian_call", "leg": {"kind": "hub", "id": "SP15"}}, "strike"),
        ({"structure": "asian_digital_call", "strike": 1.0,
          "leg": {"kind": "hub", "id": "SP15"}}, "payout"),
        ({"structure": "hrco_monthly", "leg": {"kind": "hub", "id": "SP15"}}, "heat_rate"),
        ({"structure": "hrco_monthly", "heat_rate": 7.5,
          "leg": {"kind": "hub", "id": "SP15"}}, "gas_index"),
    ]:
        with pytest.raises(S.StructureError) as exc:
            S.normalize_definition(raw)
        assert exc.value.field == field


def test_normalize_rejects_non_finite_and_out_of_range_numbers():
    base = {"structure": "asian_call", "leg": {"kind": "hub", "id": "SP15"}}
    for bad in (float("nan"), float("inf"), 10 ** 9, "26", True):
        with pytest.raises(S.StructureError) as exc:
            S.normalize_definition({**base, "strike": bad})
        assert exc.value.field == "strike"


def test_normalize_offers_exactly_the_banked_gas_indices_by_name():
    """An unbanked index is a refusal that LISTS what is held — never a silent
    substitution of one basis for another."""
    raw = {"structure": "hrco_monthly", "leg": {"kind": "hub", "id": "SP15"},
           "heat_rate": 7.5, "gas_index": "eia_spot.socal_citygate"}
    with pytest.raises(S.StructureError) as exc:
        S.normalize_definition(raw, known_gas_indices={"eia_spot.socal_border",
                                                       "eia_hh.HENRY_HUB"})
    assert exc.value.field == "gas_index"
    assert "eia_spot.socal_border" in exc.value.message


def test_normalize_defaults_and_canonicalization():
    out = S.normalize_definition({
        "structure": "asian_call", "leg": {"kind": "hub", "id": " sp15 "},
        "strike": 26.0,
    })
    assert out["leg"] == {"kind": "hub", "id": "SP15"}   # trimmed + upcased
    assert out["block"] == "7x16"                        # the default block
    assert out["size_mw"] == 1.0
    assert out["window_months"] is None                  # null = all banked
    assert out["label"] == "Monthly-average (Asian) call"


def test_vom_adder_is_optional_and_defaults_to_zero():
    out = S.normalize_definition({
        "structure": "hrco_monthly", "leg": {"kind": "hub", "id": "SP15"},
        "heat_rate": 7.5, "gas_index": "eia_hh.HENRY_HUB",
    })
    assert out["vom_adder"] == 0.0


def test_6x16_is_not_on_the_menu():
    """The NERC on-peak block needs a holiday calendar, and this codebase holds
    two that disagree (observed vs actual dates). Shipping it would ship a block
    that silently disagrees with the pantry's own caiso_blocks_daily column."""
    assert "6x16" not in S.BLOCKS
    with pytest.raises(S.StructureError) as exc:
        S.normalize_definition({"structure": "asian_call", "strike": 26.0,
                                "block": "6x16",
                                "leg": {"kind": "hub", "id": "SP15"}})
    assert exc.value.field == "block"


# ═══════════════════════════════════════════════════════════════════════════
# The HTTP surface — routes, shape guard, honest absence
# ═══════════════════════════════════════════════════════════════════════════

def _power_strip(block="7x16"):
    """A banked November 2025 strip in the fake pool's shape."""
    hours_key = len(S.block_hour_list(block))
    return [
        {"d": date(2025, 11, dd), "block_sum": Decimal(psum), "n_hours": 16,
         "hours_key": hours_key}
        for dd, psum, _gx, _gt in _NOV_2025_SP15_7x16
    ]


def _gas_strip():
    """Henry Hub prints for the fixture month, plus the Oct 31 carry-in."""
    seen, out = set(), []
    for _dd, _psum, gx, gt in _NOV_2025_SP15_7x16:
        trade = date(*gt)
        if trade in seen:
            continue
        seen.add(trade)
        out.append({"d": trade, "price": Decimal(gx)})
    return sorted(out, key=lambda r: r["d"])


_INVENTORY = [
    {"dataset": "eia_gas_henry_hub_daily", "series": "HENRY_HUB",
     "first_day": date(2006, 6, 19), "last_day": date(2026, 7, 20), "days_held": 5066},
    {"dataset": "eia_gas_spot_prices", "series": "socal_border",
     "first_day": date(2026, 7, 9), "last_day": date(2026, 7, 28), "days_held": 14},
    {"dataset": "caiso_fuel_prices", "series": "frscec",
     "first_day": date(2018, 2, 21), "last_day": date(2026, 5, 30), "days_held": 1324},
]


def _passes_shape_guard(body) -> bool:
    """The Python mirror of the client's shape guard.

    The structures page renders three views off one payload, so the guard is
    total on the client: `diagram.points` and `replay.rows` must be ARRAYS
    (a null blanks the page), `replay.depth_statement` must be a string
    (the depth caption is not optional), and `posture.rails` must be a
    non-empty array (page copy carries the rails).
    """
    if not isinstance(body, dict):
        return False
    posture = body.get("posture")
    if not isinstance(posture, dict) or not isinstance(posture.get("rails"), list):
        return False
    if not posture["rails"]:
        return False
    replay = body.get("replay")
    if not isinstance(replay, dict):
        return False
    if not isinstance(replay.get("rows"), list):
        return False
    if not isinstance(replay.get("cumulative"), list):
        return False
    if not isinstance(replay.get("months_rejected"), list):
        return False
    if not isinstance(replay.get("depth_statement"), str):
        return False
    diagram = body.get("diagram")
    if diagram is not None:
        if not isinstance(diagram, dict) or not isinstance(diagram.get("points"), list):
            return False
    return True


def test_evaluate_asian_call_passes_the_clients_shape_guard(client):
    c = client(FakePool(power={"SP15": _power_strip()}))
    r = c.post(f"{main.STRUCTURES_PREFIX}/evaluate", json={
        "structure": "asian_call", "leg": {"kind": "hub", "id": "SP15"},
        "block": "7x16", "size_mw": 1.0, "strike": 26.0,
    })
    assert r.status_code == 200
    body = r.json()
    assert _passes_shape_guard(body)
    assert body["replay"]["months"] == 1
    assert body["replay"]["rows"][0]["payoff_per_mwh"] == 11.736695
    assert body["diagram"]["breakeven"] == 26.0
    assert body["leg_resolved"] == {"series": "SP15", "kind": "hub", "id": "SP15"}


def test_evaluate_hrco_end_to_end_matches_the_hand_derivation(client):
    """The route, the SQL shapes, and the pure arithmetic together land on the
    same hand-derived figures — the whole path, not just the calculator."""
    c = client(FakePool(power={"SP15": _power_strip()}, gas=_gas_strip(),
                        inventory=_INVENTORY))
    r = c.post(f"{main.STRUCTURES_PREFIX}/evaluate", json={
        "structure": "hrco_daily", "leg": {"kind": "hub", "id": "SP15"},
        "block": "7x16", "size_mw": _HRCO_MW, "heat_rate": _HRCO_HEAT_RATE,
        "vom_adder": _HRCO_VOM, "gas_index": "eia_hh.HENRY_HUB",
    })
    assert r.status_code == 200
    body = r.json()
    assert _passes_shape_guard(body)
    row = body["replay"]["rows"][0]
    assert row["block_avg"] == _HRCO_EXPECT_BLOCK_AVG
    assert row["gas_avg"] == _HRCO_EXPECT_GAS_AVG
    assert row["strike"] == _HRCO_EXPECT_STRIKE
    assert row["payoff"] == _HRCO_EXPECT_DAILY_TOTAL
    # The gas leg states its cadence and where the diagram's strike came from.
    assert body["gas_index"]["cadence"] == "business_day"
    assert body["diagram_basis"]["available"] is True
    assert body["diagram_basis"]["gas_as_of"] == "2025-11-26"
    assert "not a forward mark" in body["diagram_basis"]["reason"]


def test_evaluate_honest_absence_still_returns_a_diagram(client):
    """A leg with nothing banked gets the payoff shape (a pure function of the
    inputs) and an EMPTY replay carrying its reason — never a fabricated month."""
    c = client(FakePool(power={"SP15": []}))
    r = c.post(f"{main.STRUCTURES_PREFIX}/evaluate", json={
        "structure": "asian_call", "leg": {"kind": "hub", "id": "SP15"},
        "strike": 26.0,
    })
    assert r.status_code == 200
    body = r.json()
    assert _passes_shape_guard(body)
    assert body["replay"]["available"] is False
    assert body["replay"]["rows"] == []
    assert body["replay"]["months"] == 0
    assert body["replay"]["reason"]
    assert body["diagram"]["points"]                    # the shape still renders
    assert body["diagram"]["mwh_basis"] is None         # no month to size it from


def test_evaluate_rejects_a_node_leg_outside_the_replayable_universe(client):
    """The spec's shared picker cannot be pointed at /api/nodes/search: none of
    that lane's 14,827 hot-tier pnode_ids are replay-capable series. A leg the
    replay cannot serve is a 400 that says where the real universe lives."""
    c = client(FakePool(power={"SP15": _power_strip()}))
    r = c.post(f"{main.STRUCTURES_PREFIX}/evaluate", json={
        "structure": "asian_call", "leg": {"kind": "node", "id": "ALAMT1G_7_B1"},
        "strike": 26.0,
    })
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "not a replay-capable nodal leg" in detail
    assert "/catalog" in detail


def test_evaluate_400s_name_the_offending_field(client):
    c = client(FakePool(power={"SP15": _power_strip()}))
    r = c.post(f"{main.STRUCTURES_PREFIX}/evaluate", json={
        "structure": "asian_call", "leg": {"kind": "hub", "id": "SP15"},
    })
    assert r.status_code == 400
    assert r.json()["detail"].startswith("strike:")


def test_evaluate_rejects_a_non_object_body(client):
    c = client(FakePool())
    assert c.post(f"{main.STRUCTURES_PREFIX}/evaluate", json=[1, 2]).status_code == 400


def test_evaluate_503s_when_the_db_is_unavailable(client):
    import psycopg
    c = client(FakePool(power={"SP15": _power_strip()},
                        raise_exc=psycopg.OperationalError("connection closed")))
    r = c.post(f"{main.STRUCTURES_PREFIX}/evaluate", json={
        "structure": "asian_call", "leg": {"kind": "hub", "id": "SP15"},
        "strike": 26.0,
    })
    assert r.status_code == 503
    assert "db unavailable" in r.json()["detail"]


def test_every_evaluate_response_carries_the_three_rails(client):
    """RAIL CONTENT, not rail comments. A payload that loses the rails ships a
    pricing desk to a page that promised a calculator."""
    c = client(FakePool(power={"SP15": _power_strip()}))
    body = c.post(f"{main.STRUCTURES_PREFIX}/evaluate", json={
        "structure": "asian_call", "leg": {"kind": "hub", "id": "SP15"},
        "strike": 26.0,
    }).json()
    rails = body["posture"]["rails"]
    assert len(rails) == 3
    assert "No forward pricing" in rails[0]
    assert "Depth-honest" in rails[1]
    assert "descriptive sorts" in rails[2]
    assert body["posture"]["history_wording"] == "would have paid"
    assert "not advice" in body["posture"]["what_this_is_not"]


# ── The catalog ──────────────────────────────────────────────────────────────

def test_catalog_lists_the_banked_menu_and_the_gas_reality_check(client):
    c = client(FakePool(
        power={h: _power_strip() for h in ("SP15", "NP15", "ZP26")},
        inventory=_INVENTORY,
        nodes=[{"series": "AGRICO_7_UNIT-APND", "first_day": date(2026, 6, 7),
                "last_day": date(2026, 7, 31), "n_hours": 485}],
    ))
    r = c.get(f"{main.STRUCTURES_PREFIX}/catalog")
    assert r.status_code == 200
    body = r.json()

    assert [b["id"] for b in body["blocks"]] == ["7x16", "7x8", "7x24"]
    assert body["blocks_omitted"][0]["id"] == "6x16"
    assert {s["id"] for s in body["structures"]} == set(S.STRUCTURES)

    # Gas indices are offered BY NAME with cadence, depth and staleness stated.
    ids = {g["id"] for g in body["gas_indices"]}
    assert ids == {"eia_hh.HENRY_HUB", "eia_spot.socal_border",
                   "caiso_fuel.frscec"}
    hh = next(g for g in body["gas_indices"] if g["id"] == "eia_hh.HENRY_HUB")
    assert hh["cadence"] == "business_day"
    assert hh["days_held"] == 5066
    assert hh["stale_days"] is not None
    assert "NOT a California delivered-gas basis" in hh["basis_note"]

    # The STOP-gate verdict, and the gaps filed rather than proxied around.
    check = body["gas_leg_reality_check"]
    assert check["verdict"] == "PASS"
    assert any("citygate" in g for g in check["gaps_filed"])
    assert "never an improvised proxy" in check["no_proxy_rule"]

    # Nodal depth is stated, and the picker deviation is stated with it.
    assert body["legs"]["nodal"]["universe_size"] == 1
    assert "WEEKS where hub strips run YEARS" in body["legs"]["nodal"]["depth_statement"]
    assert "not the /api/nodes/search universe" in body["legs"]["nodal"]["picker_note"].lower()

    # Hub depth is reported per block, because DST hour counts differ.
    sp15 = next(h for h in body["legs"]["hubs"] if h["id"] == "SP15")
    assert set(sp15["depth_by_block"]) == {"7x16", "7x8", "7x24"}
    assert sp15["depth_by_block"]["7x16"]["banked_months"] == 1

    assert body["bounds"]["screener_max_node_legs"] == 40
    assert "never a silent truncation" in body["bounds"]["bound_statement"]


def test_catalog_stops_hrco_honestly_when_no_gas_is_banked(client):
    """The spec's STOP-gate: no usable banked gas series -> ship the swap and
    Asian structures, render HRCO as an honest absence, report."""
    c = client(FakePool(power={h: _power_strip() for h in ("SP15", "NP15", "ZP26")},
                        inventory=[], nodes=[]))
    body = c.get(f"{main.STRUCTURES_PREFIX}/catalog").json()
    assert body["gas_leg_reality_check"]["verdict"] == "STOP"
    by_id = {s["id"]: s for s in body["structures"]}
    assert by_id["asian_call"]["available"] is True
    assert by_id["hrco_monthly"]["available"] is False
    assert by_id["hrco_monthly"]["unavailable_reason"] == "requires gas index — coming"
    assert by_id["hrco_daily"]["available"] is False


def test_catalog_is_cached(client):
    pool = FakePool(power={h: _power_strip() for h in ("SP15", "NP15", "ZP26")},
                    inventory=_INVENTORY, nodes=[])
    c = client(pool)
    first = c.get(f"{main.STRUCTURES_PREFIX}/catalog")
    n_after_first = len(pool.calls)
    second = c.get(f"{main.STRUCTURES_PREFIX}/catalog")
    assert first.headers["X-Cache"] == "miss"
    assert second.headers["X-Cache"] == "hit"
    assert len(pool.calls) == n_after_first        # the hit ran no SQL
    assert second.json() == first.json()


# ── The screener ─────────────────────────────────────────────────────────────

def test_screener_ranks_the_hubs_and_states_its_bound(client):
    strong = _power_strip()
    weak = [{**row, "block_sum": Decimal("320.00")} for row in _power_strip()]
    c = client(FakePool(power={"SP15": strong, "NP15": weak, "ZP26": weak}))
    r = c.get(f"{main.STRUCTURES_PREFIX}/screener",
              params={"structure": "asian_call", "strike": 26.0, "window_months": 0})
    assert r.status_code == 200
    body = r.json()

    # SP15 carries the real November strip and pays; the flat $20 legs do not.
    assert body["ranked"][0]["leg"]["id"] == "SP15"
    assert body["ranked"][0]["rank"] == 1
    assert body["ranked"][0]["total"] > 0
    assert body["ranked"][0]["months_paying"] == 1
    assert all(r["total"] == 0.0 for r in body["ranked"][1:])

    assert body["bound"]["legs_requested"] == 3
    assert body["bound"]["truncated"] is False
    assert body["comparable_window"] is True
    assert "descriptive sort" in body["note"]
    assert len(body["posture"]["rails"]) == 3
    assert body["runtime_ms"] >= 0
    assert "X-Query-Ms" in r.headers


def test_screener_reports_unreplayable_legs_instead_of_dropping_them(client):
    c = client(FakePool(power={"SP15": _power_strip(), "NP15": []}))
    body = c.get(f"{main.STRUCTURES_PREFIX}/screener",
                 params={"structure": "asian_call", "strike": 26.0,
                         "legs": "SP15,NP15"}).json()
    assert [r["leg"]["id"] for r in body["ranked"]] == ["SP15"]
    assert len(body["unavailable"]) == 1
    assert body["unavailable"][0]["leg"]["id"] == "NP15"
    assert body["unavailable"][0]["reason"]


def test_screener_rejects_an_over_cap_nodal_request(client):
    c = client(FakePool(power={}))
    too_many = ",".join(f"NODE{i}" for i in range(main._STRUCT_SCREENER_MAX_NODE_LEGS + 1))
    r = c.get(f"{main.STRUCTURES_PREFIX}/screener",
              params={"structure": "asian_call", "strike": 26.0, "legs": too_many})
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "measured cap" in detail
    assert "will not silently truncate" in detail


def test_screener_400s_on_a_bad_definition(client):
    c = client(FakePool(power={"SP15": _power_strip()}))
    # asian_call with no strike
    r = c.get(f"{main.STRUCTURES_PREFIX}/screener", params={"structure": "asian_call"})
    assert r.status_code == 400
    assert r.json()["detail"].startswith("strike:")
    # a bad sort direction
    r = c.get(f"{main.STRUCTURES_PREFIX}/screener",
              params={"structure": "asian_call", "strike": 26.0, "direction": "up"})
    assert r.status_code == 400


def test_screener_default_sweep_is_the_three_hubs(client):
    pool = FakePool(power={h: _power_strip() for h in ("SP15", "NP15", "ZP26")})
    c = client(pool)
    body = c.get(f"{main.STRUCTURES_PREFIX}/screener",
                 params={"structure": "asian_call", "strike": 26.0}).json()
    swept = {r["leg"]["id"] for r in body["ranked"]} | {
        u["leg"]["id"] for u in body["unavailable"]}
    assert swept == {"SP15", "NP15", "ZP26"}
    assert body["bound"]["hub_legs"] == 3
    assert body["bound"]["node_legs"] == 0


# ═══════════════════════════════════════════════════════════════════════════
# Query hygiene — the same rails as v0's movers
# ═══════════════════════════════════════════════════════════════════════════

def test_the_power_read_is_index_served_and_block_bound(client):
    """dataset + series lead the predicate so idx_tsv_series_ts serves the scan,
    and the block's hour set is BOUND, never interpolated into the SQL."""
    pool = FakePool(power={"SP15": _power_strip()})
    c = client(pool)
    c.post(f"{main.STRUCTURES_PREFIX}/evaluate", json={
        "structure": "asian_call", "leg": {"kind": "hub", "id": "SP15"},
        "strike": 26.0, "window_months": 24,
    })
    call = next(c for c in pool.calls if "SUM(v.value" in c["query"])
    assert "v.dataset = %(dataset)s" in call["query"]
    assert "v.series = ANY(%(series_list)s::text[])" in call["query"]
    assert call["params"]["hours"] == list(range(6, 22))
    assert call["params"]["series_list"] == ["SP15"]
    # A windowed request bounds the scan by ts as well.
    assert call["params"]["start"] is not None


def test_a_null_window_reads_full_depth(client):
    pool = FakePool(power={"SP15": _power_strip()})
    c = client(pool)
    c.post(f"{main.STRUCTURES_PREFIX}/evaluate", json={
        "structure": "asian_call", "leg": {"kind": "hub", "id": "SP15"},
        "strike": 26.0, "window_months": None,
    })
    call = next(c for c in pool.calls if "SUM(v.value" in c["query"])
    assert call["params"]["start"] is None


def test_the_read_window_pads_for_gated_months(client):
    """window_months counts BANKED months, so the read reaches back further than
    the request asks — a coverage hole inside the window must not silently
    shorten the replay."""
    pool = FakePool(power={"SP15": _power_strip()})
    c = client(pool)
    c.post(f"{main.STRUCTURES_PREFIX}/evaluate", json={
        "structure": "asian_call", "leg": {"kind": "hub", "id": "SP15"},
        "strike": 26.0, "window_months": 12,
    })
    call = next(c for c in pool.calls if "SUM(v.value" in c["query"])
    start = call["params"]["start"]
    months_back = (datetime.datetime.now(datetime.timezone.utc) - start).days / 30.44
    assert months_back > 12        # padded beyond the bare 12-month request


def test_the_gas_read_reaches_back_for_a_carry_in_print(client):
    """The first day of a replay window needs a prior trade-day print to resolve
    under the gas-day convention."""
    pool = FakePool(power={"SP15": _power_strip()}, gas=_gas_strip(),
                    inventory=_INVENTORY)
    c = client(pool)
    c.post(f"{main.STRUCTURES_PREFIX}/evaluate", json={
        "structure": "hrco_monthly", "leg": {"kind": "hub", "id": "SP15"},
        "heat_rate": 7.5, "vom_adder": 3.5, "gas_index": "eia_hh.HENRY_HUB",
    })
    call = next(c for c in pool.calls if "v.value::numeric AS price" in c["query"])
    assert call["params"]["start"] == date(2025, 11, 1) - datetime.timedelta(
        days=main._STRUCT_GAS_CARRY_IN_DAYS)
