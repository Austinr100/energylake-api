"""
Tests for d091553 (D-09-25-93): the tolerant precipitation base shipped
(season.PRECIP_TOLERANT_BASE = True), and `median_basis` on the cumulative
readouts (precip, HDD, CDD), equal to the snapshot row's.

T1..T4; each test's name carries its number. The banked records are d091550's
(tests/fixtures/season_precip_USW000232{32,174}.json, frontier 2026-09-26).
"""

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import season  # noqa: E402
from test_season_missing_days import (LAX, SAC, _build, _history, _wave,  # noqa: E402
                                      banked)

AX = season.axis("precip")
CUMULATIVE_HEAD = ["day", "value", "median", "pct_of_median", "percentile",
                   "vs_five_year", "vs_category"]


def _row(area, var, p, ex, on=None):
    return season.snapshot_row(area, var, p, ex, on=on, cat=None, area_label=area,
                               place={}, geo=None)


# ---------------------------------------------------------------------------
# T1 — the banked stations' base, against d091550's Neon measurement (§2.4)
# ---------------------------------------------------------------------------

def test_t1_sacramento_base_takes_its_three_carried_seasons():
    ex = {}
    p = _build(banked("USW00023232"), extras=ex)
    assert p["base"]["n"] == 77                       # 74 complete + 3 with 1-5 missing
    assert p["base"]["tolerance"] == season.PRECIP_MISSING_TOLERANCE
    assert all(e["days_missing"] > 5 for e in p["base"]["excluded"])
    assert p["source"]["method"] == season.METHOD_PRECIP_TOLERANT
    # the handback's "after" Sep 30 median, read off the cone unrounded
    assert round(float(ex["p50"][AX.index("09-30")]), 1) == 411.7
    r = p["readout"]
    assert r["days_missing"] == 1 and r["median_basis"] == "the cone's p50 (n = 77 >= 30)"


def test_t1_lax_base_is_unchanged():
    p = _build(banked("USW00023174"), area=LAX)
    assert p["base"]["n"] == 81                       # no 1-5-day season at LAX


# ---------------------------------------------------------------------------
# T2 — precip: the readout's median_basis is the snapshot row's
# ---------------------------------------------------------------------------

def test_t2_precip_readout_and_snapshot_row_agree():
    ex = {}
    p = _build(banked("USW00023232"), extras=ex)
    r, row = p["readout"], _row(SAC, "precip", p, ex)
    assert list(r) == (CUMULATIVE_HEAD + list(season.READOUT_CUMULATIVE_ADDED_KEYS)
                       + list(season.READOUT_CARRIED_KEYS))
    assert row["season_day"] == r["day"] and row["value"] == r["value"]
    assert row["median"] == r["median"] is not None
    assert row["median_basis"] == r["median_basis"]
    assert row["pct_of_median"] == r["pct_of_median"]
    assert row["percentile"] == r["percentile"]


# ---------------------------------------------------------------------------
# T3 — HDD and CDD: the same, and never carried
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("var,first,frontier", [("hdd", 1980, date(2026, 2, 10)),
                                                ("cdd", 1980, date(2026, 8, 20))])
def test_t3_degree_day_readout_and_snapshot_row_agree(var, first, frontier):
    last = 2025 if var == "hdd" else 2026
    ex = {}
    p = _build(_history(var, first, last, _wave, frontier=frontier), var, SAC, extras=ex)
    r, row = p["readout"], _row(SAC, var, p, ex)
    assert p["readout_absence"] is None and p["base"]["n"] >= season.CONE_MIN_N
    assert list(r) == CUMULATIVE_HEAD + list(season.READOUT_CUMULATIVE_ADDED_KEYS)
    assert r["median_basis"] == f"the cone's p50 (n = {p['base']['n']} >= 30)"
    assert row["median"] == r["median"] and row["median_basis"] == r["median_basis"]
    assert row["pct_of_median"] == r["pct_of_median"]


# ---------------------------------------------------------------------------
# T4 — a short base: no median, so no basis, on both sides
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("var", ["precip", "hdd"])
def test_t4_short_base_has_no_median_basis(var):
    frontier = date(2026, 9, 24) if var == "precip" else date(2026, 2, 10)
    ex = {}
    p = _build(_history(var, 2010, 2025, frontier=frontier), var, SAC, extras=ex)
    r, row = p["readout"], _row(SAC, var, p, ex)
    assert p["base"]["n"] < season.CONE_MIN_N
    assert r["median"] is None and r["median_basis"] is None
    assert row["median"] is None and row["median_basis"] is None
    assert row["absence"].startswith("short base")


# ---------------------------------------------------------------------------
# T5 — a past season carried past the tolerance has no to_date (§1.4, §1.6)
# ---------------------------------------------------------------------------

def test_t5_years_to_date_is_withheld_past_the_tolerance():
    # LAX's WY1944: the record opens 305 days in. Carried, its Sep 26 slot is
    # the sum of nothing; it must not read 0.0.
    p = _build(banked("USW00023174"), area=LAX)
    wy = {y["season"]: y for y in p["years"]}
    assert wy["WY1944"]["complete"] is False and wy["WY1944"]["to_date"] is None
    # counted to the day: 6 holes, the 6th on Jun 1; the readout day is Sep 24
    daily = _history("precip", 1989, 2025, frontier=date(2026, 9, 24))
    for d in (date(2000, 1, 15), date(2000, 2, 1), date(2000, 3, 1), date(2000, 4, 1),
              date(2000, 5, 1)):
        del daily[d]
    p5 = {y["season"]: y for y in _build(dict(daily))["years"]}
    del daily[date(2000, 6, 1)]
    p6 = {y["season"]: y for y in _build(daily)["years"]}
    i24 = AX.index("09-24")
    assert p5["WY2000"]["complete"] is True and p5["WY2000"]["to_date"] == float(i24 + 2 - 5)
    assert p6["WY2000"]["complete"] is False and p6["WY2000"]["to_date"] is None
