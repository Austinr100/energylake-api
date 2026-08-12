"""
Tests for `block_pricing` — the WECC block calendar and the package arithmetic.

No live database. Every number below is hand-computed in the docstring or the
comment beside it, so a failure says which arithmetic broke rather than only
that two floats differ.

The calendar itself is main.py's (`nerc_holidays`), already pinned by
tests/test_nerc_calendar.py against the pantry's 66 rows. What this suite adds
is the BLOCK layer on top of it: the hour sets, the partition invariant, the
block averages, TB, the bins and the month flags.
"""

from datetime import date as D

import pytest

import block_pricing as bp
import main


# The injected calendar — main.py's, never a second copy. Every test in this
# file that needs holidays uses this and nothing else.
HOL = main.is_nerc_holiday


# ═══════════════════════════════════════════════════════════════════════════
# 1. The NERC dates this lane asserts
# ═══════════════════════════════════════════════════════════════════════════
# Cited in the handback. 2026 is the live year; 2027 carries the Sunday shift
# (Jan 1 2028 and Jul 4 2027 both land on a Sunday -> observed Monday).

def test_2026_nerc_dates_are_what_this_lane_asserts():
    cal = main.nerc_holidays(2026)
    assert cal == {
        D(2026, 1, 1):   "new_years",     # Thursday
        D(2026, 5, 25):  "memorial",      # last Mon in May
        D(2026, 7, 4):   "independence",  # SATURDAY — not moved
        D(2026, 9, 7):   "labor",         # 1st Mon in Sep
        D(2026, 11, 26): "thanksgiving",  # 4th Thu in Nov
        D(2026, 12, 25): "christmas",     # Friday
    }


def test_sunday_shift_year_2027():
    """2027-07-04 is a Sunday -> observed Monday the 5th. The Saturday rule is
    the opposite and is asserted above on 2026-07-04."""
    cal = main.nerc_holidays(2027)
    assert cal[D(2027, 7, 5)] == "independence"
    assert D(2027, 7, 4) not in cal
    # 2027-12-25 is a Saturday -> NOT moved.
    assert cal[D(2027, 12, 25)] == "christmas"
    assert D(2027, 12, 27) not in cal


# ═══════════════════════════════════════════════════════════════════════════
# 2. The hour sets
# ═══════════════════════════════════════════════════════════════════════════

def test_6x16_hour_set_on_an_ordinary_weekday():
    # 2026-07-06 is a Monday and not a holiday.
    assert bp.block_hours("6x16", D(2026, 7, 6), HOL) == tuple(range(7, 23))
    assert bp.block_hours("2x16h", D(2026, 7, 6), HOL) == ()
    assert bp.block_hours("off_peak", D(2026, 7, 6), HOL) == (1, 2, 3, 4, 5, 6, 23, 24)


def test_saturday_is_a_6x16_day_but_a_saturday_holiday_is_not():
    """The whole Saturday rule, on one pair of dates.

    2026-07-11 is an ordinary Saturday -> 6x16 runs.
    2026-07-04 is a Saturday AND Independence Day (not moved) -> no 6x16, and
    those 16 hours become 2x16h instead.
    """
    assert bp.is_6x16_day(D(2026, 7, 11), HOL) is True
    assert bp.block_hours("6x16", D(2026, 7, 11), HOL) == tuple(range(7, 23))

    assert bp.is_6x16_day(D(2026, 7, 4), HOL) is False
    assert bp.block_hours("6x16", D(2026, 7, 4), HOL) == ()
    assert bp.block_hours("2x16h", D(2026, 7, 4), HOL) == tuple(range(7, 23))


def test_sunday_has_no_6x16_and_a_full_off_peak():
    sunday = D(2026, 7, 5)
    assert bp.block_hours("6x16", sunday, HOL) == ()
    assert bp.block_hours("2x16h", sunday, HOL) == tuple(range(7, 23))
    assert bp.block_hours("off_peak", sunday, HOL) == tuple(range(1, 25))


def test_day_independent_blocks_never_move():
    for d in (D(2026, 7, 4), D(2026, 7, 5), D(2026, 7, 6)):
        assert bp.block_hours("7x16", d, HOL) == tuple(range(7, 23))
        assert bp.block_hours("7x8", d, HOL) == (1, 2, 3, 4, 5, 6, 23, 24)
        assert bp.block_hours("atc", d, HOL) == tuple(range(1, 25))


def test_unknown_block_is_an_error_not_an_empty_set():
    with pytest.raises(ValueError):
        bp.block_hours("5x16", D(2026, 7, 6), HOL)


# ═══════════════════════════════════════════════════════════════════════════
# 3. The invariant — over a fixture week containing a NERC holiday
# ═══════════════════════════════════════════════════════════════════════════

def test_block_invariant_over_the_july_4_week():
    """6x16 + off_peak == 24 every day, and off_peak == 7x8 ∪ 2x16h exactly.

    2026-06-29..2026-07-12 spans Independence Day on a Saturday plus two whole
    Sundays, so every branch of the calendar is exercised.
    """
    for i in range(14):
        d = D(2026, 6, 29) + __import__("datetime").timedelta(days=i)
        bp.assert_block_invariant(d, HOL)


def test_block_invariant_over_two_full_years():
    """Cheap and total: every date in 2026 and 2027 partitions correctly."""
    import datetime
    d = D(2026, 1, 1)
    while d <= D(2027, 12, 31):
        bp.assert_block_invariant(d, HOL)
        d += datetime.timedelta(days=1)


def test_2x16h_days_are_exactly_sundays_plus_nerc_holidays_in_2026():
    import datetime
    got = set()
    d = D(2026, 1, 1)
    while d <= D(2026, 12, 31):
        if bp.block_hours("2x16h", d, HOL):
            got.add(d)
        d += datetime.timedelta(days=1)
    sundays = set()
    d = D(2026, 1, 1)
    while d <= D(2026, 12, 31):
        if d.weekday() == 6:
            sundays.add(d)
        d += datetime.timedelta(days=1)
    assert got == sundays | set(main.nerc_holidays(2026))


# ═══════════════════════════════════════════════════════════════════════════
# 4. Block arithmetic — one fixture spanning a NERC holiday, hand-computed
# ═══════════════════════════════════════════════════════════════════════════
#
# Four consecutive days in July 2026, 24 HE each:
#     Jul 3  Friday                  -> 6x16 day
#     Jul 4  SATURDAY + Independence -> NOT 6x16; its peak hours are 2x16h
#     Jul 5  Sunday                  -> NOT 6x16; its peak hours are 2x16h
#     Jul 6  Monday                  -> 6x16 day
#
# Price rule:  dam_lmp = base(day) + 40 on peak hours (HE7-22), base off-peak.
#     base: Jul3=100, Jul4=200, Jul5=300, Jul6=400
#
# Hand arithmetic (sums are $/MWh × hours):
#     peak hours   Jul3 16×140=2240  Jul4 16×240=3840
#                  Jul5 16×340=5440  Jul6 16×440=7040     (peak total 18560)
#     wrap hours   Jul3  8×100= 800  Jul4  8×200=1600
#                  Jul5  8×300=2400  Jul6  8×400=3200     (wrap total  8000)
#
#     6x16     Jul3+Jul6 peak = (2240+7040)/32 =  9280/32 = 290.0
#     2x16h    Jul4+Jul5 peak = (3840+5440)/32 =  9280/32 = 290.0
#     7x16     all four peak  = 18560/64                  = 290.0
#     7x8      all four wrap  =  8000/32                  = 250.0
#     atc      everything     = 26560/96                  = 276.666...
#     off_peak Jul3 wrap 800 + Jul4 ALL 5440 + Jul5 ALL 7840 + Jul6 wrap 3200
#              = 17280 / 64 hours                          = 270.0

def _july_fixture():
    rows = []
    base = {D(2026, 7, 3): 100.0, D(2026, 7, 4): 200.0,
            D(2026, 7, 5): 300.0, D(2026, 7, 6): 400.0}
    for d, b in base.items():
        for he in range(1, 25):
            price = b + (40.0 if 7 <= he <= 22 else 0.0)
            rows.append({"trade_date": d, "he": he, "dam_lmp": price,
                         "rt_avg": price + 1.0, "rt_n": 12})
    return rows


def test_block_monthly_arithmetic_over_a_nerc_holiday():
    rows = _july_fixture()
    # July 2026 is NOT complete in this fixture (4 days), so nothing summarises.
    out = bp.blocks_monthly(rows, HOL, complete_months=set())

    expect = {"6x16": 290.0, "2x16h": 290.0, "7x16": 290.0,
              "7x8": 250.0, "off_peak": 270.0, "atc": 276.6667}
    for block, avg in expect.items():
        cell = out[block]["monthly"][0]
        assert cell["month"] == "2026-07"
        assert cell["avg_dam"] == pytest.approx(avg, abs=1e-4), block
        # rt_avg is dam + 1 everywhere, so every block average shifts by exactly 1.
        assert cell["avg_rt"] == pytest.approx(avg + 1.0, abs=1e-4), block

    # `hours` is the receipt that the calendar ran.
    assert out["6x16"]["monthly"][0]["hours"] == 32     # Jul 3 + Jul 6 only
    assert out["2x16h"]["monthly"][0]["hours"] == 32    # Jul 4 + Jul 5 only
    assert out["7x16"]["monthly"][0]["hours"] == 64
    assert out["7x8"]["monthly"][0]["hours"] == 32
    assert out["off_peak"]["monthly"][0]["hours"] == 64
    assert out["atc"]["monthly"][0]["hours"] == 96

    # The partition, in counted hours rather than in nominal ones.
    assert (out["6x16"]["monthly"][0]["hours"]
            + out["off_peak"]["monthly"][0]["hours"]) == 96
    assert (out["7x8"]["monthly"][0]["hours"]
            + out["2x16h"]["monthly"][0]["hours"]) == 64


def test_partial_month_is_flagged_and_kept_out_of_the_summary():
    rows = _july_fixture()
    partial = bp.blocks_monthly(rows, HOL, complete_months=set())
    assert partial["6x16"]["monthly"][0]["partial"] is True
    assert partial["6x16"]["summary"]["n_months"] == 0
    assert partial["6x16"]["summary"]["avg"] is None

    complete = bp.blocks_monthly(rows, HOL, complete_months={"2026-07"})
    assert complete["6x16"]["monthly"][0]["partial"] is False
    assert complete["6x16"]["summary"]["n_months"] == 1
    assert complete["6x16"]["summary"]["avg"] == pytest.approx(290.0, abs=1e-4)


def test_blocks_daily_omits_blocks_the_calendar_closed():
    rows = _july_fixture()
    daily = bp.blocks_daily(rows, HOL)
    by_date = {}
    for cell in daily:
        by_date.setdefault(cell["date"], set()).add(cell["block"])

    # Jul 4 (Sat holiday) and Jul 5 (Sun) carry NO 6x16 row at all — absent,
    # not a zero-hour row.
    assert "6x16" not in by_date["2026-07-04"]
    assert "6x16" not in by_date["2026-07-05"]
    assert "2x16h" in by_date["2026-07-04"]
    # Jul 3 / Jul 6 are the mirror image.
    assert "6x16" in by_date["2026-07-03"]
    assert "2x16h" not in by_date["2026-07-03"]

    jul6_6x16 = [c for c in daily
                 if c["date"] == "2026-07-06" and c["block"] == "6x16"][0]
    assert jul6_6x16["hours"] == 16
    assert jul6_6x16["avg_dam"] == pytest.approx(440.0)


# ═══════════════════════════════════════════════════════════════════════════
# 5. TB2 / TB4 — a 3-day fixture, hand-computed
# ═══════════════════════════════════════════════════════════════════════════
#
#   Day A 2026-06-01  price(HE h) = h        -> 1..24
#   Day B 2026-06-02  price(HE h) = 2h       -> 2..48
#   Day C 2026-06-03  price(HE h) = 10       -> flat
#
#   TB2:  A  top2 (24,23) mean 23.5 ; bottom2 (1,2) mean 1.5 ; spread 22
#            22 × 2 / 1000                                     = 0.044
#         B  top2 (48,46) mean 47   ; bottom2 (2,4) mean 3   ; spread 44
#            44 × 2 / 1000                                     = 0.088
#         C  spread 0                                          = 0.000
#         month total                                          = 0.132
#
#   TB4:  A  top4 (21..24) mean 22.5 ; bottom4 (1..4) mean 2.5 ; spread 20
#            20 × 4 / 1000                                     = 0.080
#         B  top4 (42..48) mean 45   ; bottom4 (2..8) mean 5   ; spread 40
#            40 × 4 / 1000                                     = 0.160
#         C  spread 0                                          = 0.000
#         month total                                          = 0.240

def _tb_fixture():
    rows = []
    for d, f in ((D(2026, 6, 1), lambda h: float(h)),
                 (D(2026, 6, 2), lambda h: 2.0 * h),
                 (D(2026, 6, 3), lambda h: 10.0)):
        for he in range(1, 25):
            rows.append({"trade_date": d, "he": he, "dam_lmp": f(he)})
    return rows


def test_tb_day_values_to_the_cent():
    day_a = [float(h) for h in range(1, 25)]
    day_b = [2.0 * h for h in range(1, 25)]
    day_c = [10.0] * 24
    assert bp.tb_day_value(day_a, 2) == pytest.approx(0.044, abs=1e-9)
    assert bp.tb_day_value(day_b, 2) == pytest.approx(0.088, abs=1e-9)
    assert bp.tb_day_value(day_c, 2) == pytest.approx(0.0, abs=1e-9)
    assert bp.tb_day_value(day_a, 4) == pytest.approx(0.080, abs=1e-9)
    assert bp.tb_day_value(day_b, 4) == pytest.approx(0.160, abs=1e-9)
    assert bp.tb_day_value(day_c, 4) == pytest.approx(0.0, abs=1e-9)


def test_tb_monthly_totals_to_the_cent():
    rows = _tb_fixture()
    tb2 = bp.tb_monthly(rows, "dam_lmp", 2, complete_months={"2026-06"})
    tb4 = bp.tb_monthly(rows, "dam_lmp", 4, complete_months={"2026-06"})

    assert tb2["unit"] == "$/kW-month"
    assert tb2["months"][0]["month"] == "2026-06"
    assert tb2["months"][0]["value"] == pytest.approx(0.132, abs=1e-4)
    assert tb2["months"][0]["days"] == 3
    assert tb4["months"][0]["value"] == pytest.approx(0.240, abs=1e-4)
    assert tb4["months"][0]["days"] == 3

    # Summary over the one complete month is that month.
    assert tb2["summary"]["n_months"] == 1
    assert tb2["summary"]["avg"] == pytest.approx(0.132, abs=1e-4)


def test_tb_skips_days_too_short_to_price_without_overlapping_legs():
    """A day with fewer than 2N priced hours would reuse hours on both legs."""
    short = [{"trade_date": D(2026, 6, 1), "he": he, "dam_lmp": float(he)}
             for he in range(1, 8)]  # 7 hours
    assert bp.tb_day_value([float(h) for h in range(1, 8)], 4) is None  # needs 8
    assert bp.tb_day_value([float(h) for h in range(1, 9)], 4) is not None
    out = bp.tb_monthly(short, "dam_lmp", 4, complete_months=set())
    assert out["months"][0]["value"] is None
    assert out["months"][0]["days"] == 0
    assert out["months"][0]["days_skipped"] == 1


# ═══════════════════════════════════════════════════════════════════════════
# 6. Distribution bins — inclusive/exclusive exactly as written
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("value,expected", [
    (-1000.0, "<=-40"),
    (-40.0,   "<=-40"),      # the first edge is INCLUSIVE: <= -40
    (-39.999, "(-40,-20]"),
    (-20.0,   "(-40,-20]"),  # right edge inclusive
    (-19.999, "(-20,0]"),
    (0.0,     "(-20,0]"),    # zero belongs to the NEGATIVE-side bin
    (0.001,   "(0,10]"),
    (10.0,    "(0,10]"),
    (10.001,  "(10,20]"),
    (20.0,    "(10,20]"),
    (30.0,    "(20,30]"),
    (30.001,  "(30,50]"),
    (50.0,    "(30,50]"),
    (75.0,    "(50,75]"),
    (125.0,   "(75,125]"),
    (175.0,   "(125,175]"),
    (225.0,   "(175,225]"),  # the last closed edge is INCLUSIVE
    (225.001, ">225"),
    (10000.0, ">225"),
])
def test_bin_edges(value, expected):
    assert bp.bin_of(value) == expected


def test_distribution_ships_every_bin_including_the_empty_ones():
    rows = [{"dam_lmp": v} for v in (5.0, 5.0, 300.0)]
    out = bp.distribution(rows, "dam_lmp")
    assert len(out) == len(bp.DISTRIBUTION_BINS)
    by_bin = {c["bin"]: c for c in out}
    assert by_bin["(0,10]"]["n"] == 2
    assert by_bin[">225"]["n"] == 1
    assert by_bin["<=-40"]["n"] == 0          # present, not omitted
    assert by_bin["(0,10]"]["share"] == pytest.approx(2 / 3, abs=1e-6)


def test_distribution_ignores_nulls_rather_than_binning_them():
    rows = [{"dam_lmp": 5.0}, {"dam_lmp": None}]
    out = {c["bin"]: c["n"] for c in bp.distribution(rows, "dam_lmp")}
    assert out["(0,10]"] == 1
    assert sum(out.values()) == 1


# ═══════════════════════════════════════════════════════════════════════════
# 7. Absence is stated, never zero-filled
# ═══════════════════════════════════════════════════════════════════════════

def test_profile_omits_hours_the_bank_never_carried():
    rows = [{"trade_date": D(2026, 6, 1), "he": he, "dam_lmp": float(he)}
            for he in (1, 2, 3)]
    out = bp.profile(rows, "dam_lmp")
    assert [c["he"] for c in out] == [1, 2, 3]      # not 1..24 with zeros


def test_profile_quantiles_are_monotone_and_match_the_house_convention():
    rows = [{"trade_date": D(2026, 6, 1 + i), "he": 7, "dam_lmp": float(v)}
            for i, v in enumerate([10, 20, 30, 40, 100])]
    cell = bp.profile(rows, "dam_lmp")[0]
    assert cell["n"] == 5
    assert cell["p25"] <= cell["p50"] <= cell["p75"]
    # percentile_cont on [10,20,30,40,100]: p25 -> idx 1.0 -> 20; p50 -> 30.
    assert cell["p25"] == pytest.approx(20.0)
    assert cell["p50"] == pytest.approx(30.0)


def test_percentile_matches_mains_implementation_exactly():
    """Two percentile conventions in one API is a bug that only shows up in a
    screenshot. Pinned rather than commented."""
    for vals in ([1.0], [1.0, 2.0], [1.0, 2.0, 3.0, 4.0],
                 [-5.0, 0.0, 0.5, 17.0, 100.0, 100.0]):
        s = sorted(vals)
        for q in (0.0, 0.25, 0.5, 0.75, 0.9, 1.0):
            assert bp.percentile_cont(s, q) == main._an_percentile_cont(s, q)


def test_heatmap_only_carries_months_in_the_bank():
    rows = [{"trade_date": D(2026, 6, 1), "he": 7, "dam_lmp": 10.0},
            {"trade_date": D(2026, 6, 2), "he": 7, "dam_lmp": 20.0},
            {"trade_date": D(2026, 7, 1), "he": 8, "dam_lmp": 30.0}]
    out = bp.heatmap(rows, "dam_lmp")
    assert out == [
        {"month": "2026-06", "he": 7, "avg": 15.0, "n": 2},
        {"month": "2026-07", "he": 8, "avg": 30.0, "n": 1},
    ]


# ═══════════════════════════════════════════════════════════════════════════
# 8. Month completeness
# ═══════════════════════════════════════════════════════════════════════════

def test_month_completeness_edges():
    # The live window as of 2026-08-12: 2026-05-03 .. 2026-08-11.
    got = bp.month_completeness(D(2026, 5, 3), D(2026, 8, 11))
    assert got == {"2026-06", "2026-07"}        # May starts late, Aug ends early

    # Exact boundaries are complete.
    assert bp.month_completeness(D(2026, 6, 1), D(2026, 6, 30)) == {"2026-06"}
    # One day short at either end is not.
    assert bp.month_completeness(D(2026, 6, 2), D(2026, 6, 30)) == set()
    assert bp.month_completeness(D(2026, 6, 1), D(2026, 6, 29)) == set()
    # Year boundary.
    assert bp.month_completeness(D(2025, 12, 1), D(2026, 1, 31)) == {"2025-12", "2026-01"}


def test_month_completeness_of_an_empty_window():
    assert bp.month_completeness(None, None) == set()


# ═══════════════════════════════════════════════════════════════════════════
# 9. RT coverage suppression
# ═══════════════════════════════════════════════════════════════════════════

def test_rt_coverage_suppresses_only_when_most_days_are_thin():
    # 10 days, 24 HEs of RT each -> dense.
    dense = [{"trade_date": D(2026, 6, 1) + __import__("datetime").timedelta(days=i),
              "he": he, "rt_n": 12}
             for i in range(10) for he in range(1, 25)]
    cov = bp.rt_coverage(dense)
    assert cov["suppressed"] is False
    assert cov["days"] == 10
    assert cov["days_below_threshold"] == 0

    # 10 days, only 5 HEs of RT each -> every day below the 20-of-24 floor.
    thin = [{"trade_date": D(2026, 6, 1) + __import__("datetime").timedelta(days=i),
             "he": he, "rt_n": 3}
            for i in range(10) for he in range(1, 6)]
    cov = bp.rt_coverage(thin)
    assert cov["suppressed"] is True
    assert cov["days_below_threshold"] == 10
    assert "20 of 24" in cov["rule"]


def test_rt_coverage_at_the_half_boundary_does_not_suppress():
    """'more than half' is strict: exactly half thin is NOT suppression."""
    import datetime
    rows = []
    for i in range(10):
        d = D(2026, 6, 1) + datetime.timedelta(days=i)
        hes = range(1, 25) if i < 5 else range(1, 6)   # 5 dense, 5 thin
        for he in hes:
            rows.append({"trade_date": d, "he": he, "rt_n": 4})
    cov = bp.rt_coverage(rows)
    assert cov["days_below_threshold"] == 5
    assert cov["suppressed"] is False
