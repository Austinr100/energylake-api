"""
Tests for d091550 (cc_spec_2026_10_01_season_missing_days.md, D-09-25-86): a
precipitation season summed over the days that reported, and the single-area
readout taking the snapshot's median rule.

S1..S8 are the spec's §2.6 table; each test's name carries its number.

STOP-B fired (the handback has the stations): the base change is not shipped.
PRECIP_TOLERANT_BASE is False, so past seasons stay strict; S4 and S8 pin that,
and pin the rule with the flag set, so the change is ready when it is ruled on.

S5 compares against digests of main's season.py (main at d08d602, banked in
tests/fixtures/season_s5_main_digests.json). Regenerate them with
    python tests/test_season_missing_days.py <path to main's season.py>
"""

import datetime
import hashlib
import importlib.util
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import season  # noqa: E402

FIX = Path(__file__).parent / "fixtures"
UTC = datetime.timezone.utc
CUR_V = "cafe0001"
DEVELOPING = {"kind": "nino", "n_seasons": 4, "first_year": 2026, "latest_oni": 1.8,
              "latest_year": 2026, "first_season": "MAM", "latest_season": "JJA"}
SAC = "station:USW00023232"
LAX = "station:USW00023174"
AX = season.axis("precip")


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

def _days(a: date, b: date):
    d = a
    while d <= b:
        yield d
        d += timedelta(days=1)


def _history(var, first, last, value=lambda d, s: 1.0, *, frontier=None):
    daily = {}
    for s in range(first, last + 1):
        a, b = season.bounds(var, s)
        for d in _days(a, min(b, frontier) if frontier else b):
            daily[d] = float(value(d, s))
    return daily


def _bins(first=1949, last=2026):
    return [{"enso_year": y, "kind": "nino" if y % 4 == 0 else "neutral",
             "strength": "strong" if y % 8 == 0 else None, "flavor": None}
            for y in range(first, last + 1)]


def _build(daily, var="precip", area=SAC, mod=season, extras=None):
    return mod.build_season(area, var, daily, classifier="cpc_oni", catalog_version=CUR_V,
                            developing=DEVELOPING, bins=_bins(), oni_last_centre=(2026, 7),
                            extras=extras)


def banked(station: str) -> dict:
    """tests/fixtures/season_precip_{station}.json -> {date: mm or None} (a
    date with no row is not a key), as `daily_from_rows` hands it over."""
    body = json.loads((FIX / f"season_precip_{station}.json").read_text())
    out, d = {}, date.fromisoformat(body["first"])
    for f in body["days"].split(","):
        if f != "-":
            out[d] = None if f == "" else int(f) / 10.0
        d += timedelta(days=1)
    assert len(out) == body["n_rows"] and d - timedelta(days=1) == date.fromisoformat(body["last"])
    return out


def _wave(d, s):
    """A season that differs by year and by day."""
    doy = d.timetuple().tm_yday
    return round(10.0 + 6.0 * np.sin(2 * np.pi * doy / 365.0) + (s * 13 % 7), 3)


def _tank(d, s):
    doy = (d - date(s, 10, 1)).days
    return round((1000.0 + (s * 37 % 90) * 20) * (1.0 + 0.8 * np.sin(np.pi * doy / 365.0)), 3)


def _swe(d, s):
    doy = (d - date(s, 10, 1)).days
    return round(max(0.0, (90.0 + (s * 11 % 40)) * np.sin(np.pi * doy / 300.0)), 3)


def s5_cases():
    """The S5 fixtures: HDD and CDD (a station and a load region), SWE (n >=
    30) and storage (n = 20 and n = 35), each with a hole in a past season
    and one in the season in progress."""
    cases = {}
    d = _history("hdd", 1980, 2025, _wave, frontier=date(2026, 2, 10))
    del d[date(1999, 12, 24)]
    d[date(2026, 1, 5)] = None
    cases["hdd_station"] = (SAC, "hdd", d)
    d = _history("cdd", 1980, 2026, _wave, frontier=date(2026, 8, 20))
    del d[date(2004, 7, 4)]
    del d[date(2026, 6, 30)]
    cases["cdd_station"] = (SAC, "cdd", d)
    d = _history("hdd", 1990, 2025, _wave, frontier=date(2026, 3, 1))
    del d[date(2026, 2, 1)]
    cases["hdd_lwt"] = ("lwt:BPAT", "hdd", d)
    d = _history("swe", 1984, 2025, _swe, frontier=date(2026, 4, 1))
    del d[date(2001, 1, 15)]
    del d[date(2026, 3, 3)]
    cases["swe"] = ("snow:snake", "swe", d)
    d = _history("storage", 2005, 2025, _tank, frontier=date(2026, 7, 15))
    del d[date(2010, 5, 5)]
    d[date(2026, 6, 1)] = None
    cases["storage_n20"] = ("reservoir:shasta", "storage", d)
    d = _history("storage", 1990, 2025, _tank, frontier=date(2026, 7, 15))
    del d[date(2010, 5, 5)]
    cases["storage_n35"] = ("reservoir:shasta", "storage", d)
    return cases


def _digest(obj) -> str:
    return hashlib.sha256(json.dumps(obj).encode()).hexdigest()


def _sans_readout(p: dict) -> dict:
    return {k: v for k, v in p.items() if k not in ("readout", "readout_absence")}


def _main_digests(mod) -> dict:
    out = {}
    for name, (area, var, daily) in s5_cases().items():
        p = _build(daily, var, area, mod=mod)
        out[name] = {"body": _digest(p), "sans_readout": _digest(_sans_readout(p)),
                     "n": p["base"]["n"], "readout": p["readout"],
                     "readout_absence": p["readout_absence"]}
    return out


# ---------------------------------------------------------------------------
# S1 — Sacramento's banked WY2026 rows (red on main's walker: through Feb 18)
# ---------------------------------------------------------------------------

def test_s1_sacramento_banked_wy2026():
    daily = banked("USW00023232")
    p = _build(daily)
    assert p["frontier"] == "2026-09-26"
    ts = p["this_season"]
    assert ts["season"] == "WY2026"
    assert ts["through"] == "2026-09-26"
    assert ts["days_missing"] == 1
    assert ts["missing_days"] == ["2026-02-19"]
    assert ts["first_missing"] == "2026-02-19"
    assert ts["complete_to_date"] is False and ts["within_tolerance"] is True
    i19, i26 = AX.index("02-19"), AX.index("09-26")
    assert all(v is not None for v in ts["values"][:i26 + 1])
    assert all(v is None for v in ts["values"][i26 + 1:])
    # every slot after Feb 19 is the sum of the reported days through it
    reported = sum(v for d, v in daily.items()
                   if date(2025, 10, 1) <= d <= date(2026, 9, 26) and v is not None)
    assert ts["values"][i26] == round(reported, 1)
    assert ts["values"][i19 + 1] > 0
    r = p["readout"]
    assert p["readout_absence"] is None and r["day"] == i26
    assert r["value"] == ts["values"][i26]
    assert r["days_missing"] == 1 and r["basis"] == "sum of reported days; 1 missing"
    assert r["median"] is not None and r["percentile"] is not None      # n >= 30
    assert list(r)[-2:] == list(season.READOUT_CARRIED_KEYS)


# ---------------------------------------------------------------------------
# S2 — the missing day's slot carries the day before; nothing is added
# ---------------------------------------------------------------------------

def test_s2_the_feb_19_slot_equals_the_feb_18_slot():
    p = _build(banked("USW00023232"))
    v = p["this_season"]["values"]
    i18 = AX.index("02-18")
    assert v[i18 + 1] == v[i18]
    # and the walk itself, unrounded
    w = season._walk("precip", 2025, banked("USW00023232"), date(2026, 9, 26),
                     season._slot_index("precip"), SAC, carry=True)
    assert w.values[i18 + 1] == w.values[i18]
    assert w.days_missing == 1 and w.missing == [date(2026, 2, 19)]


# ---------------------------------------------------------------------------
# S3 — the tolerance: 5 given with its basis, 6 withheld with the curve kept
# ---------------------------------------------------------------------------

HOLES = [date(2025, 11, 3), date(2025, 12, 9), date(2026, 1, 20), date(2026, 3, 2),
         date(2026, 4, 14), date(2026, 5, 30)]


def _holed(k):
    daily = _history("precip", 1989, 2025, frontier=date(2026, 9, 24))
    for d in HOLES[:k]:
        del daily[d]
    return daily


def test_s3_five_missing_is_given_six_is_withheld():
    i24 = AX.index("09-24")
    p5 = _build(_holed(5))
    assert p5["this_season"]["days_missing"] == 5
    assert p5["this_season"]["within_tolerance"] is True
    assert p5["readout_absence"] is None
    assert p5["readout"]["value"] == float(i24 + 1 - 5)
    assert p5["readout"]["basis"] == "sum of reported days; 5 missing"

    p6 = _build(_holed(6))
    ts = p6["this_season"]
    assert ts["days_missing"] == 6 and ts["within_tolerance"] is False
    assert p6["readout"] is None
    assert p6["readout_absence"] == {"reason": "too_many_missing", "days_missing": 6,
                                     "tolerance": 5, "first_missing": "2025-11-03"}
    assert ts["values"][i24] == float(i24 + 1 - 6)          # the curve is still drawn
    assert all(v is not None for v in ts["values"][:i24 + 1])


def test_s3_snapshot_row_follows_the_tolerance():
    for k, given in ((5, True), (6, False)):
        ex = {}
        p = _build(_holed(k), extras=ex)
        row = season.snapshot_row(SAC, "precip", p, ex, on=None, cat=None,
                                  area_label="Sacramento", place={}, geo=None)
        assert tuple(row) == season.SNAPSHOT_KEYS + season.SNAPSHOT_CARRIED_KEYS
        assert row["days_missing"] == k
        if given:
            assert row["value"] == p["readout"]["value"] and row["absence"] is None
            assert row["basis"] == "sum of reported days; 5 missing"
            assert row["median"] == p["readout"]["median"]
        else:
            assert row["value"] is None and row["basis"] is None
            assert row["absence"].startswith("6 days missing in the season to date")
    # counted to the row's own day: on Dec 31 only two holes have passed
    ex = {}
    p = _build(_holed(6), extras=ex)
    row = season.snapshot_row(SAC, "precip", p, ex, on=date(2025, 12, 31), cat=None,
                              area_label="Sacramento", place={}, geo=None)
    assert row["days_missing"] == 2 and row["basis"] == "sum of reported days; 2 missing"


def test_s3_a_complete_season_says_none_missing():
    p = _build(_history("precip", 1989, 2025, frontier=date(2026, 9, 24)))
    ts = p["this_season"]
    assert ts["complete_to_date"] is True and ts["absence"] is None
    assert (ts["days_missing"], ts["missing_days"], ts["first_missing"]) == (0, [], None)
    assert p["readout"]["basis"] == "sum of reported days; 0 missing"


# ---------------------------------------------------------------------------
# S4 — a past season with 3 missing days (STOP-B: strict while the flag is off)
# ---------------------------------------------------------------------------

def _three_holes(var):
    first, last, frontier = ((1989, 2025, date(2026, 9, 24)) if var == "precip"
                             else (1989, 2024, date(2026, 2, 1)))
    daily = _history(var, first, last + 1, frontier=frontier)
    for d in (date(2000, 1, 15), date(2000, 2, 1), date(2000, 3, 1)):
        del daily[d]
    return daily


def test_s4_three_missing_with_the_flag_set_precip_qualifies_hdd_does_not(monkeypatch):
    monkeypatch.setattr(season, "PRECIP_TOLERANT_BASE", True)
    p = _build(_three_holes("precip"))
    assert "WY2000" in p["base"]["seasons"] and p["base"]["n"] == 36
    assert p["base"]["tolerance"] == 5 and p["base"]["excluded"] == []
    wy = next(y for y in p["years"] if y["season"] == "WY2000")
    assert wy["complete"] is True and wy["final"] == 366.0 - 3
    assert p["source"]["method"] == season.METHOD_PRECIP_TOLERANT

    h = _build(_three_holes("hdd"), "hdd", SAC)
    assert "1999-00" not in h["base"]["seasons"]
    assert [e["season"] for e in h["base"]["excluded"]] == ["1999-00"]


def test_s4_three_missing_flag_off_the_base_is_unchanged():
    assert season.PRECIP_TOLERANT_BASE is False       # STOP-B
    p = _build(_three_holes("precip"))
    assert "WY2000" not in p["base"]["seasons"] and p["base"]["n"] == 35
    assert p["base"]["excluded"] == [{"season": "WY2000", "days_complete": 363,
                                      "days_in_window": 366, "first_missing": "2000-01-15"}]
    assert p["base"]["rule"] == ("every complete season, full record, excluding the "
                                 "current season")
    assert p["source"]["method"] == season.METHODS[("station", "precip")]


# ---------------------------------------------------------------------------
# S5 — HDD, CDD, SWE and storage against main
# ---------------------------------------------------------------------------

MAIN = json.loads((FIX / "season_s5_main_digests.json").read_text())


@pytest.mark.parametrize("name", ["hdd_station", "cdd_station", "hdd_lwt"])
def test_s5_degree_days_are_byte_identical_to_main(name):
    area, var, daily = s5_cases()[name]
    assert _digest(_build(daily, var, area)) == MAIN[name]["body"]


@pytest.mark.parametrize("name", ["swe", "storage_n20", "storage_n35"])
def test_s5_levels_identical_but_for_the_readouts_median_rule(name):
    """§2.3 changes a level's readout by design: it gains `median_basis`, and
    with 5 <= n < 30 carries the range median. Everything else is main's."""
    area, var, daily = s5_cases()[name]
    p = _build(daily, var, area)
    assert _digest(_sans_readout(p)) == MAIN[name]["sans_readout"]
    assert p["readout_absence"] == MAIN[name]["readout_absence"]
    r = dict(p["readout"])
    basis = r.pop("median_basis")
    old = MAIN[name]["readout"]
    if MAIN[name]["n"] >= season.CONE_MIN_N:
        assert r == old and basis.startswith("the cone's p50")
    else:
        assert old["median"] is None and r["median"] is not None
        assert basis.startswith("the base's range median")
        assert {k: v for k, v in r.items() if k not in ("median", "pct_of_median")} == \
            {k: v for k, v in old.items() if k not in ("median", "pct_of_median")}


# ---------------------------------------------------------------------------
# S6 — a missing Feb 29 keeps the Feb 28 slot
# ---------------------------------------------------------------------------

def test_s6_missing_feb_29_keeps_the_feb_28_slot():
    daily = _history("precip", 1990, 2023, frontier=date(2024, 5, 1))
    del daily[date(2024, 2, 29)]
    p = _build(daily)
    ts = p["this_season"]
    assert ts["season"] == "WY2024" and ts["through"] == "2024-05-01"
    i27, i28 = AX.index("02-27"), AX.index("02-28")
    assert ts["values"][i28] == ts["values"][i27] + 1.0           # Feb 28 kept
    assert ts["values"][i28 + 1] == ts["values"][i28] + 1.0       # Mar 1 adds on
    assert ts["missing_days"] == ["2024-02-29"]
    w = season._walk("precip", 2023, daily, date(2024, 5, 1), season._slot_index("precip"),
                     SAC, carry=True)
    assert w.values[i28] == float(i28 + 1) and w.days_missing == 1


# ---------------------------------------------------------------------------
# S7 — readout and snapshot row: one median, one basis (a level, n = 19, 25)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("first", [2006, 2000])                 # n = 19, n = 25
def test_s7_readout_and_snapshot_row_agree(first):
    daily = _history("storage", first, 2024, _tank, frontier=date(2026, 7, 15))
    daily.update(_history("storage", 2025, 2025, _tank, frontier=date(2026, 7, 15)))
    ex = {}
    p = _build(daily, "storage", "reservoir:shasta", extras=ex)
    assert p["base"]["n"] == 2025 - first
    row = season.snapshot_row("reservoir:shasta", "storage", p, ex, on=None, cat=None,
                              area_label="Shasta", place={}, geo=None)
    r = p["readout"]
    assert row["season_day"] == r["day"] and row["value"] == r["value"]
    assert row["median"] == r["median"] is not None
    assert row["median_basis"] == r["median_basis"]
    assert r["median_basis"] == (f"the base's range median (n = {2025 - first}, 5 <= n < 30); "
                                 "no percentile below n = 30")
    assert row["pct_of_median"] == r["pct_of_median"]
    assert row["percentile"] is None and r["percentile"] is None


def test_s7_day_median_is_the_one_rule():
    B = np.arange(12.0).reshape(3, 4)
    assert season.day_median(1, n=3, p50=None) == (None, None)
    m, b = season.day_median(2, n=31, p50=np.array([1.0, 2.0, 3.0, 4.0]))
    assert m == 3.0 and b == "the cone's p50 (n = 31 >= 30)"
    m, b = season.day_median(0, n=7, p50=None, range_n=7, range_median=np.median(B, axis=0))
    assert m == 4.0 and b.startswith("the base's range median (n = 7")


# ---------------------------------------------------------------------------
# S8 — /season/areas: complete_seasons unchanged, qualifying_seasons by the rule
# ---------------------------------------------------------------------------

def _areas_counts():
    full = {s: season.days_in_window("precip", s) for s in range(1990, 2026)}
    full[2000] -= 3          # WY2001: 3 missing
    full[2005] -= 5          # WY2006: 5 missing
    full[2010] -= 6          # WY2011: 6 missing
    full[2025] = 200         # the season in progress
    return {(SAC, "precip"): full,
            (SAC, "hdd"): {1999: season.days_in_window("hdd", 1999) - 3}}


def _precip_row(body):
    a = next(a for a in body["areas"] if a["area"] == SAC)
    return next(v for v in a["vars"] if v["var"] == "precip"), \
        next(v for v in a["vars"] if v["var"] == "hdd")


META = [{"station_id": "USW00023232", "display_name": "Sacramento", "state": "CA"}]


def test_s8_areas_counts(monkeypatch):
    lasts = {(SAC, "precip"): date(2026, 9, 26)}
    off, hdd_off = _precip_row(season.build_areas(META, _areas_counts(), None, lasts))
    assert off["complete_seasons"] == 32 and "qualifying_seasons" not in off

    monkeypatch.setattr(season, "PRECIP_TOLERANT_BASE", True)
    on, hdd_on = _precip_row(season.build_areas(META, _areas_counts(), None, lasts))
    assert on["complete_seasons"] == 32                 # unchanged
    assert on["qualifying_seasons"] == 34               # + the 3- and 5-missing seasons
    assert list(on).index("qualifying_seasons") == list(on).index("complete_seasons") + 1
    assert hdd_on == hdd_off and "qualifying_seasons" not in hdd_on


# ---------------------------------------------------------------------------
# Regenerate the S5 digests from main's season.py
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    spec = importlib.util.spec_from_file_location("season_main", sys.argv[1])
    main_season = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(main_season)
    (FIX / "season_s5_main_digests.json").write_text(
        json.dumps(_main_digests(main_season), indent=1) + "\n")
