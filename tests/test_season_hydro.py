"""
Tests for California's reservoirs and snowpack in the season API (d091522):
`storage` on `reservoir:{id}` / `reservoir:ca_major8` and `swe_in` on
`snow:ca_{region}`, both levels on a 0.90 floor with null-aware per-day
statistics; `range`, each category's `outlook` and `enso.now`. season.py (pure)
and the routes in main.py against an in-memory stand-in for the two CDEC
datasets + the ENSO bank.

H1..H11 are the spec's §4 table (energylake-pantry
docs/cc_spec_2026_09_30_season_hydro_api.md); each test's name carries its
number, and the reds R1..R6 are asserted in the cell the spec names. H11 is the
rest of the suite, unchanged but for the fixture lines named in the handback.
Fixtures are built here, by calendar date.
"""

import datetime
import json
from datetime import date, timedelta

import numpy as np
import pytest
from fastapi.testclient import TestClient

import main
import season

UTC = datetime.timezone.utc
CUR_V = "cafe0022"
DEVELOPING = {"kind": "nino", "n_seasons": 4, "first_year": 2026, "latest_oni": 1.8,
              "latest_year": 2026, "first_season": "MAM", "latest_season": "JJA"}
SLOTS = season._slot_index("storage")
AX = season.axis("storage")


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

def _days(a: date, b: date):
    d = a
    while d <= b:
        yield d
        d += timedelta(days=1)


def _fill(daily, s, value, *, through=None, var="storage"):
    """Every day of water year `s` (start year), or, for swe_in, only the days
    of its Dec 1 -> May 31 window (the feed's snow season)."""
    a, b = season.count_window(var, s) if var == "swe_in" else season.bounds(var, s)
    for d in _days(a, min(b, through) if through else b):
        daily[d] = float(value(d, s))


def _history(first, last, value=lambda d, s: 1.0, *, frontier=None, var="storage"):
    daily = {}
    for s in range(first, last + 1):
        _fill(daily, s, value, through=frontier, var=var)
    return daily


def _tank(d, s):
    """A toy reservoir (TAF): low in autumn, filling to a spring high, drawn down
    by September. Seasons differ in size."""
    doy = (d - date(s, 10, 1)).days
    scale = 1000.0 + (s * 37 % 90) * 20
    return round(scale * (1.0 + 0.8 * np.sin(np.pi * doy / 365.0)), 3)


def _bins(first=1949, last=2026, overrides=()):
    rows = {y: {"enso_year": y, "kind": "neutral", "strength": None, "flavor": None}
            for y in range(first, last + 1)}
    for y, kind, strength in overrides:
        rows[y] = {"enso_year": y, "kind": kind, "strength": strength, "flavor": None}
    return [rows[y] for y in sorted(rows)]


def _build(daily, var="storage", area="reservoir:shasta", bins=None, developing=DEVELOPING,
           meta=None, classifier="cpc_oni"):
    return season.build_season(area, var, daily, classifier=classifier,
                               catalog_version=CUR_V, developing=developing,
                               bins=bins if bins is not None else _bins(),
                               oni_last_centre=(2026, 7), source_meta=meta)


# ---------------------------------------------------------------------------
# H1 — storage: a 0.90 floor, not strict completeness (R1)
# ---------------------------------------------------------------------------

def test_h1_storage_floor_keeps_a_holed_season_and_excludes_a_thin_one():
    daily = _history(1995, 2024, _tank)                     # WY1996..WY2025
    holes5 = [date(2011, 1, 1) + timedelta(days=3 * k) for k in range(18)]   # 18/365 = 4.9 %
    for d in holes5:
        del daily[d]
    holes15 = list(_days(date(2012, 1, 1), date(2012, 2, 24)))              # 55/366 = 15 %
    for d in holes15:
        del daily[d]
    p = _build(daily)

    b = p["base"]
    assert b["min_days_frac"] == 0.90
    assert b["window"] == {"start_md": "10-01", "end_md": "09-30"}
    assert "WY2011" in b["seasons"]                          # R1: strict would drop it
    assert "WY2012" not in b["seasons"] and b["n"] == 29
    assert b["excluded"] == [{"season": "WY2012", "days_complete": 311,
                              "days_in_window": 366, "first_missing": "2012-01-01"}]

    wy11 = p["curves"]["WY2011"]
    for d in holes5:
        assert wy11[AX.index(f"{d.month:02d}-{d.day:02d}")] is None
    assert wy11[AX.index("01-02")] == round(_tank(date(2011, 1, 2), 2010), 1)
    assert "WY2012" not in p["curves"]
    assert len(p["curves"]) == 29                            # every qualifying season
    by = {y["season"]: y for y in p["years"]}
    assert by["WY2011"]["complete"] is True and by["WY2012"]["complete"] is False
    assert by["WY2011"]["peak"] is not None and by["WY2012"]["peak"] is None


def test_h1_the_floor_is_inclusive_at_exactly_ninety_percent():
    # 0.90 x 366 = 329.4 -> 330 valued days needed in a leap water year;
    # 0.90 x 365 = 328.5 -> 329 (WY2001's 328 of 365 all-eight days fall short).
    w = season._Walk()
    for days, total, ok in ((329, 365, True), (328, 365, False),
                            (330, 366, True), (329, 366, False)):
        w.window_complete, w.window_days = days, total
        assert season._qualifies("storage", w) is ok


# ---------------------------------------------------------------------------
# H2 — the eight's sum: all eight or null, never renormalised (R2)
# ---------------------------------------------------------------------------

AF = {"trinity": 1_500_000, "shasta": 3_000_000, "oroville": 2_000_000, "folsom": 500_000,
      "new_melones": 1_800_000, "don_pedro": 1_400_000, "millerton": 300_000,
      "san_luis": 1_000_000}


def _rows(d, af, *, null=(), absent=()):
    return [{"obs_date": d, "series": i, "v": None if i in null else float(v)}
            for i, v in af.items() if i not in absent]


def test_h2_major8_needs_all_eight_and_sums_without_renormalising():
    d1, d2, d3 = date(2026, 9, 26), date(2026, 9, 27), date(2026, 9, 28)
    rows = (_rows(d1, AF) + _rows(d2, AF, null=("folsom",))
            + _rows(d3, AF, absent=("millerton",)))
    daily, fronts = season.major8_daily(rows)
    assert daily[d1] == sum(AF.values()) / 1000.0 == 11_500.0
    assert daily[d2] is None                                  # seven of eight: null
    assert daily[d3] is None                                  # R2: not 11_200, not x8/7
    assert fronts["folsom"] == "2026-09-28"                   # null on d2, valued again on d3
    assert fronts["millerton"] == "2026-09-27"                # no row on d3: it stopped
    assert fronts["shasta"] == "2026-09-28"
    assert season.storage_daily([{"obs_date": d1, "v": 4_552_000.0},
                                 {"obs_date": d2, "v": None}]) == {d1: 4552.0, d2: None}


def test_h2_major8_payload_source_and_a_null_day():
    daily = _history(2000, 2025, _tank, frontier=date(2026, 9, 28))
    daily[date(2026, 9, 27)] = None                           # a seven-of-eight day
    fronts = {i: "2026-09-28" for i in season.RESERVOIR_IDS}
    p = _build(daily, area="reservoir:ca_major8", meta={"series_frontiers": fronts})
    ts = p["this_season"]
    assert ts["values"][AX.index("09-27")] is None
    assert ts["values"][AX.index("09-28")] == round(_tank(date(2026, 9, 28), 2025), 1)
    assert ts["absence"]["mode"] == "incomplete"
    src = p["source"]
    assert src["dataset"] == "cdec_reservoir_storage_daily"
    assert src["capacity_taf"] == 18505.727
    assert sum(src["capacity_af_by_reservoir"].values()) == 18_505_727
    assert src["series_frontiers"] == fronts
    assert _build(daily)["source"]["capacity_taf"] == 4552.0          # shasta


# ---------------------------------------------------------------------------
# H3 — swe_in: Dec 1 -> May 31 counts; summer is off-season, not a gap (R3)
# ---------------------------------------------------------------------------

def test_h3_swe_in_counts_only_its_window():
    daily = _history(2006, 2024, lambda d, s: 10.0 + d.day / 10, var="swe_in")
    _fill(daily, 2025, lambda d, s: 12.0, var="swe_in")       # Dec 1 2025 -> May 31 2026
    for d in _days(date(2026, 6, 1), date(2026, 6, 5)):       # the feed's tail, as CDEC's
        daily[d] = 0.4
    for d in _days(date(2008, 11, 20), date(2008, 11, 30)):   # an early-season row or two
        daily[d] = 0.1
    p = _build(daily, var="swe_in", area="snow:ca_state")
    assert p["units"] == "in" and p["season"]["mode"] == "level"
    assert p["base"]["n"] == 19 and p["base"]["excluded"] == []          # R3
    assert p["base"]["window"] == {"start_md": "12-01", "end_md": "05-31"}
    assert p["frontier"] == "2026-06-05"
    ts = p["this_season"]
    assert ts["season"] == "WY2026" and ts["complete_to_date"] is True
    assert ts["absence"] is None and p["this_season_absence"] is None
    assert ts["values"][AX.index("07-15")] is None                       # no row, no gap
    assert p["readout"]["value"] == 0.4

    # In July the season's walk has still missed nothing.
    w = season._walk("swe_in", 2025, daily, date(2026, 7, 15), SLOTS)
    assert w.first_missing is None and w.days_missing == 0
    assert w.window_complete == w.window_days == 182
    assert w.days_complete == 182 + 5


def test_h3_a_hole_inside_the_window_is_a_gap_and_a_thin_season_is_excluded():
    daily = _history(2006, 2024, lambda d, s: 5.0, var="swe_in")
    _fill(daily, 2025, lambda d, s: 5.0, var="swe_in", through=date(2026, 4, 1))
    del daily[date(2026, 1, 10)]
    for d in _days(date(2015, 2, 1), date(2015, 2, 20)):      # 20/182 = 11 % of WY2015's window
        del daily[d]
    p = _build(daily, var="swe_in", area="snow:ca_north")
    assert p["this_season"]["absence"] == {"reason": "gap", "first_missing": "2026-01-10",
                                           "mode": "absent", "days_complete": 121,
                                           "days_missing": 1}
    assert p["base"]["excluded"] == [{"season": "WY2015", "days_complete": 162,
                                      "days_in_window": 182, "first_missing": "2015-02-01"}]


# ---------------------------------------------------------------------------
# H4 — per-day statistics ignore nulls; < 3 contributing -> null (R4)
# ---------------------------------------------------------------------------

def test_h4_null_aware_statistics_and_n_by_day_min():
    daily = _history(2018, 2023, _tank)                       # WY2019..WY2024, all neutral
    for s in (2018, 2019, 2020, 2021):                        # Jan 10: 2 left
        del daily[date(s + 1, 1, 10)]
    for s in (2018, 2019):                                    # Jan 11: 4 left
        del daily[date(s + 1, 1, 11)]
    p = _build(daily)
    i10, i11, i12 = AX.index("01-10"), AX.index("01-11"), AX.index("01-12")
    rg, fy, ne = p["range"], p["five_year"], p["enso"]["categories"]["neutral"]

    for block, keys in ((rg, ("min", "median", "max")), (fy, ("mean", "min", "max")),
                        (ne, ("median",))):
        for k in keys:
            assert block[k][i10] is None, (k, "two seasons: null in every statistic")

    v11 = [_tank(date(s + 1, 1, 11), s) for s in (2020, 2021, 2022, 2023)]
    assert rg["median"][i11] == round(float(np.median(v11)), 1)            # not with 0s
    assert rg["min"][i11] == round(min(v11), 1)                            # R4: not 0
    assert ne["median"][i11] == round(float(np.median(v11)), 1)
    assert fy["mean"][i11] == round(float(np.mean(v11)), 1)
    v12 = [_tank(date(s + 1, 1, 12), s) for s in range(2018, 2024)]
    assert rg["median"][i12] == round(float(np.median(v12)), 1)
    assert fy["mean"][i12] == round(float(np.mean(v12[1:])), 1)

    assert rg["n"] == 6 and rg["n_by_day_min"] == 4
    assert fy["n"] == 5 and fy["n_by_day_min"] == 4           # WY2020..WY2024 on Jan 11
    assert ne["n"] == 6 and ne["n_by_day_min"] == 4
    assert p["base"]["n_by_day_min"] == 4


def test_h4_cone_on_a_long_floored_record_ignores_nulls():
    daily = _history(1980, 2011, _tank)                       # 32 seasons
    for s in range(1980, 2009):                               # Jan 10: 3 left
        del daily[date(s + 1, 1, 10)]
    for s in range(1980, 2010):                               # Jan 11: 2 left
        del daily[date(s + 1, 1, 11)]
    p = _build(daily)
    pc = p["percentiles"]
    assert pc["n"] == 32 and pc["n_by_day_min"] == 3
    v10 = [_tank(date(s + 1, 1, 10), s) for s in (2009, 2010, 2011)]
    assert pc["p50"][AX.index("01-10")] == round(float(np.percentile(v10, 50)), 1)
    assert all(pc[k][AX.index("01-11")] is None for k in pc if k.startswith("p"))
    assert p["normal"] is None                                # WY1991..WY2012: 22 < 24
    assert p["normal_absence"] == {"reason": "short_window", "n": 22}


# ---------------------------------------------------------------------------
# H5 — short record: no percentile, a range from n = 5
# ---------------------------------------------------------------------------

def test_h5_short_record_carries_a_range_not_a_cone():
    p = _build(_history(2005, 2024, _tank))                   # n = 20
    assert p["percentiles"] is None
    assert p["percentiles_absence"] == {"reason": "short_record", "n": 20}
    rg = p["range"]
    assert p["range_absence"] is None and rg["n"] == 20 and len(rg["seasons"]) == 20
    assert rg["seasons"][0] == "WY2006" and rg["seasons"][-1] == "WY2025"
    assert set(rg) == {"n", "n_by_day_min", "seasons", "min", "median", "max"}
    assert p["readout"]["percentile"] is None and p["readout"]["median"] is None
    assert tuple(p) == season.LEVEL_RESPONSE_KEYS

    p4 = _build(_history(2021, 2024, _tank))                  # n = 4
    assert p4["range"] is None and p4["range_absence"] == {"reason": "small_n", "n": 4}


# ---------------------------------------------------------------------------
# H6 — each category's outlook: min / median / max, never a percentile (R5)
# ---------------------------------------------------------------------------

STRONG6 = (1997, 2002, 2009, 2012, 2015, 2023)               # ENSO years -> WY+1


def test_h6_outlook_equals_numpy_on_the_members():
    daily = _history(1995, 2024, _tank)
    del daily[date(2016, 4, 1)]                               # a member missing Apr 1
    bins = _bins(overrides=[(y, "nino", "strong") for y in STRONG6]
                 + [(1998, "nina", "strong"), (2010, "nina", "strong")])
    p = _build(daily, bins=bins)
    ol = p["enso"]["categories"]["nino_strong"]["outlook"]
    members = [s for s in STRONG6]
    ws = {s: season._walk("storage", s, daily, season.bounds("storage", s)[1], SLOTS)
          for s in members}

    assert ol["n"] == 6 and ol["seasons"] == [f"WY{s + 1}" for s in members]
    peaks = np.array([ws[s].peak for s in members])
    assert ol["peak"]["min"] == round(float(peaks.min()), 1)
    assert ol["peak"]["median"] == round(float(np.median(peaks)), 1)
    assert ol["peak"]["max"] == round(float(peaks.max()), 1)
    mds = sorted(SLOTS[season._md(ws[s].peak_date)] for s in members)
    assert ol["peak"]["median_md"] == AX[mds[2]]              # the lower median day
    for md, key in (("04-01", "apr1"), ("07-01", "jul1")):
        xs = [ws[s].values[SLOTS[md]] for s in members if ws[s].values[SLOTS[md]] is not None]
        assert ol["on_day"][md] == {"min": round(min(xs), 1),
                                    "median": round(float(np.median(xs)), 1),
                                    "max": round(max(xs), 1), "n": len(xs)}
    assert ol["on_day"]["04-01"]["n"] == 5 and ol["on_day"]["07-01"]["n"] == 6
    m15 = next(m for m in ol["members"] if m["season"] == "WY2016")
    assert m15["apr1"] is None and m15["jul1"] == round(ws[2015].values[SLOTS["07-01"]], 1)
    # R5: no percentile anywhere in an outlook
    assert set(ol["peak"]) == {"min", "median", "max", "median_md"}
    assert not any(k.startswith("p") and k[1:].isdigit() for k in json.dumps(ol).split('"'))

    ns = p["enso"]["categories"]["nina_strong"]
    assert ns["n"] == 2 and ns["outlook"] is None
    assert ns["outlook_absence"] == {"reason": "small_n", "n": 2}


def test_h6_swe_categories_carry_outlook_too():
    import test_season_snow as T
    daily = T._history(1983, 2025, T._snowish, frontier=date(2026, 4, 1))
    bins = _bins(overrides=[(y, "nino", "strong") for y in STRONG6])
    p = _build(daily, var="swe", area="snow:col_above_grand_coulee", bins=bins)
    ol = p["enso"]["categories"]["nino_strong"]["outlook"]
    assert ol["n"] == 6 and ol["peak"]["median"] is not None


# ---------------------------------------------------------------------------
# H7 — enso.now is read from `developing` and nothing else (R6)
# ---------------------------------------------------------------------------

def test_h7_enso_now():
    now = season.enso_now
    assert now(DEVELOPING, "cpc_oni")["category"] == "nino_strong"
    assert now({**DEVELOPING, "latest_oni": 1.2}, "cpc_oni")["category"] == "nino_moderate"
    assert now({**DEVELOPING, "kind": "nina", "latest_oni": -0.6}, "cpc_oni")["category"] \
        == "nina_weak"
    assert now(None, "cpc_oni")["category"] == "neutral"
    assert now({**DEVELOPING, "latest_oni": 1.5}, "cpc_oni")["category"] == "nino_strong"
    assert now({**DEVELOPING, "latest_oni": 0.5}, "cpc_oni")["category"] == "nino_weak"

    b = now(DEVELOPING, "cpc_oni")["basis"]
    assert b.startswith("El Niño developing, latest ONI 1.8 (JJA), 4 seasons")
    assert "not a forecast" in b
    assert "RONI 1.4" in now({**DEVELOPING, "latest_oni": 1.4}, "roni")["basis"]
    assert "not a forecast" in now(None, "cpc_oni")["basis"]

    # R6: the year bin says neutral for 2026; `developing` rules.
    p = _build(_history(2015, 2025, _tank, frontier=date(2026, 9, 1)))
    assert {b["kind"] for b in _bins() if b["enso_year"] == 2026} == {"neutral"}
    assert p["enso"]["now"]["category"] == "nino_strong"
    assert list(p["enso"])[-1] == "now"
    assert _build(_history(2015, 2025, _tank), developing=None)["enso"]["now"]["category"] \
        == "neutral"


# ---------------------------------------------------------------------------
# H8 — swe is unchanged but for the appended keys
# ---------------------------------------------------------------------------

def test_h8_swe_stays_strict_and_keeps_its_blocks():
    import test_season_snow as T
    daily = T._history(1983, 2025, T._snowish, frontier=date(2026, 4, 1))
    del daily[date(2001, 2, 3)]                               # 1 of 365: 99.7 % valued
    p = _build(daily, var="swe", area="snow:snake")
    assert "WY2001" not in p["base"]["seasons"]               # strict: still excluded
    assert set(p["base"]) == {"rule", "seasons", "n", "excluded"}
    assert p["base"]["rule"] == "every complete season, full record, excluding the current season"
    assert "n" not in p["percentiles"] and "n_by_day_min" not in p["percentiles"]
    assert "n_by_day_min" not in p["five_year"]
    assert all("n_by_day_min" not in c for c in p["enso"]["categories"].values())
    assert tuple(p) == season.LEVEL_RESPONSE_KEYS
    assert p["range"]["n"] == p["base"]["n"] == 41            # WY1984..WY2025 less WY2001
    assert p["source"]["dataset"] == "snow_basin_index_daily"
    assert "capacity_taf" not in p["source"]

    # The same one-day hole in storage is kept: the floor is storage's, not swe's.
    st = _history(1983, 2024, _tank)
    del st[date(2001, 2, 3)]
    assert "WY2001" in _build(st)["base"]["seasons"]


# ---------------------------------------------------------------------------
# Routes: a stand-in for the two CDEC datasets + the ENSO bank
# ---------------------------------------------------------------------------

class _Cur:
    def __init__(self, pool):
        self.pool, self._rows = pool, []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, q, params=None):
        self.pool.statements.append((q, params))
        self._rows = self.pool.answer(q, params or {})

    async def fetchall(self):
        return self._rows

    async def fetchone(self):
        return self._rows[0] if self._rows else None


class _Conn:
    def __init__(self, pool):
        self.pool = pool

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def cursor(self):
        return _Cur(self.pool)


class _HydroPool:
    def __init__(self):
        self.statements = []
        base = _history(1995, 2025, _tank, frontier=date(2026, 9, 28))
        self.res = {i: {d: v * 1000 * (k + 1) / 36 for d, v in base.items()}
                    for k, i in enumerate(season.RESERVOIR_IDS)}
        del self.res["don_pedro"][date(2026, 9, 27)]
        self.snow = {r: _history(2006, 2025, lambda d, s: 8.0 + d.day / 10, var="swe_in")
                     for r in ("state", "north", "central", "south")}

    def connection(self):
        return _Conn(self)

    def answer(self, q, p):
        if q == season.RESERVOIRS_SQL:
            assert p == {"d": "cdec_reservoir_storage_daily", "s": list(season.RESERVOIR_IDS)}
            return [{"obs_date": d, "series": i, "v": v}
                    for i in p["s"] for d, v in sorted(self.res[i].items())]
        if q == season.LWT_SQL and p["d"] == "cdec_reservoir_storage_daily":
            return [{"obs_date": d, "v": v} for d, v in sorted(self.res[p["s"]].items())]
        if q == season.LWT_SQL and p["d"] == "cdec_snowpack_swe_daily":
            assert p["s"].endswith("_avg_swc")
            return [{"obs_date": d, "v": v}
                    for d, v in sorted(self.snow[p["s"].split("_")[0]].items())]
        if q == main._enso.RUN_SQL:
            return [{"classifier": p["c"], "developing": DEVELOPING, "catalog_version": CUR_V,
                     "source": {}, "n_episodes": 0, "n_year_bins": 78,
                     "computed_at": datetime.datetime(2026, 9, 29, tzinfo=UTC)}]
        if q == main._enso.YEAR_BINS_SQL:
            return _bins(overrides=[(y, "nino", "strong") for y in (1997, 2009, 2015, 2023)])
        if q == season.ONI_LAST_SQL:
            return [{"ts": datetime.datetime(2026, 7, 1, tzinfo=UTC)}]
        if q in (season.AREAS_PRECIP_SQL, season.AREAS_STATION_DD_SQL, season.AREAS_LWT_SQL,
                 season.AREAS_SNOW_SQL):
            return []
        if q == season.AREAS_CA_SNOW_SQL:
            assert p == {"d": "cdec_snowpack_swe_daily", "s": list(season.CA_SNOW_SERIES)}
            return [{"id": r, "s": s, "n": season.window_days("swe_in", s) - (s == 2010) * 30}
                    for r in ("state", "north", "central", "south") for s in range(2005, 2026)]
        if q == season.AREAS_RESERVOIR_SQL:
            assert p["m"] == "ca_major8" and p["k"] == 8
            out = [{"id": i, "s": s, "n": season.days_in_window("storage", s)}
                   for i in p["s"] for s in range(1995, 2025)]
            out += [{"id": "ca_major8", "s": s, "n": 328 if s == 2000 else 360}
                    for s in range(1995, 2025)]
            return out
        raise AssertionError(f"unexpected statement: {q}")


@pytest.fixture(autouse=True)
def _clear_memos():
    main._season_cache.clear()
    main._season_areas_cache.clear()
    yield
    main._season_cache.clear()
    main._season_areas_cache.clear()


@pytest.fixture
def pool(monkeypatch):
    p = _HydroPool()
    monkeypatch.setattr(main, "_pool", p)
    return p


@pytest.fixture
def client(pool):
    return TestClient(main.app)


# ---------------------------------------------------------------------------
# H9 — refusals name the vocabulary
# ---------------------------------------------------------------------------

def test_h9_refusals(client):
    get = lambda **q: client.get("/api/weather/season", params=q)
    r = get(area="snow:ca_state", var="storage")
    assert r.status_code == 400
    d = r.json()["detail"]
    assert "not served for snow:ca_state" in d and "reservoir: areas carry storage" in d
    assert "swe_in on California's (snow:ca_*)" in d
    r = get(area="snow:snake", var="swe_in")
    assert r.status_code == 400 and "swe on the Columbia basins" in r.json()["detail"]
    r = get(area="reservoir:castaic", var="storage")
    assert r.status_code == 400
    assert ("reservoir:{id} for ca_major8, trinity, shasta, oroville, folsom, new_melones, "
            "don_pedro, millerton, san_luis") in r.json()["detail"]
    assert "snow:{California region} for ca_state, ca_north, ca_central, ca_south" \
        in r.json()["detail"]
    r = get(area="reservoir:shasta", var="swe")
    assert r.status_code == 400
    r = get(area="reservoir:shasta", var="level")
    assert r.status_code == 400 and "precip, hdd, cdd, swe, storage, swe_in" in r.json()["detail"]


# ---------------------------------------------------------------------------
# H10 — route smoke: /areas lists 57 in §1's order; the two new reads
# ---------------------------------------------------------------------------

def test_h10_areas_count_and_order(client, pool):
    areas = client.get("/api/weather/season/areas").json()["areas"]
    assert len(areas) == 44 + 4 + 9 == 57
    assert [a["area"] for a in areas[38:44]] == [f"snow:{b}" for b in season.SNOW_BASINS]
    assert [(a["area"], a["label"], a["kind"]) for a in areas[44:48]] == [
        ("snow:ca_state", "California statewide", "snow"),
        ("snow:ca_north", "Northern Sierra / Trinity", "snow"),
        ("snow:ca_central", "Central Sierra", "snow"),
        ("snow:ca_south", "Southern Sierra", "snow")]
    assert [(a["area"], a["label"]) for a in areas[48:]] == [
        ("reservoir:ca_major8", "California, eight major reservoirs"),
        ("reservoir:trinity", "Trinity Lake"), ("reservoir:shasta", "Shasta Lake"),
        ("reservoir:oroville", "Lake Oroville"), ("reservoir:folsom", "Folsom Lake"),
        ("reservoir:new_melones", "New Melones Lake"),
        ("reservoir:don_pedro", "Don Pedro Reservoir"),
        ("reservoir:millerton", "Millerton Lake"), ("reservoir:san_luis", "San Luis Reservoir")]
    assert areas[48]["capacity_taf"] == 18505.727 and areas[50]["capacity_taf"] == 4552.0
    assert areas[44]["vars"] == [{"var": "swe_in", "season": "water_year", "units": "in",
                                  "first_season": "WY2006", "complete_seasons": 20,
                                  "qualifying_seasons": 20}]
    assert areas[48]["vars"] == [{"var": "storage", "season": "water_year", "units": "TAF",
                                  "first_season": "WY1996", "complete_seasons": 0,
                                  "qualifying_seasons": 29}]
    assert areas[49]["vars"][0]["complete_seasons"] == 30


def test_h10_major8_and_ca_state_reads(client, pool):
    r = client.get("/api/weather/season", params={"area": "reservoir:ca_major8", "var": "storage"})
    assert r.status_code == 200 and r.headers["cache-control"] == "max-age=900"
    body = r.json()
    assert tuple(body) == season.LEVEL_RESPONSE_KEYS
    assert body["units"] == "TAF" and body["frontier"] == "2026-09-28"
    assert body["this_season"]["values"][AX.index("09-27")] is None     # don_pedro absent
    assert body["source"]["capacity_taf"] == 18505.727
    assert body["source"]["series_frontiers"]["don_pedro"] == "2026-09-28"
    assert body["base"]["n"] == 30 and body["percentiles"] is not None
    ns = body["enso"]["categories"]["nino_strong"]
    assert ns["n"] == 4 and ns["outlook"]["n"] == 4
    assert body["enso"]["now"]["category"] == "nino_strong"
    assert body["range"]["n"] == 30

    r = client.get("/api/weather/season", params={"area": "snow:ca_state", "var": "swe_in"})
    body = r.json()
    assert r.status_code == 200 and body["base"]["n"] == 19 and body["units"] == "in"
    assert body["percentiles"] is None and body["range"]["n"] == 19
    assert body["this_season"]["season"] == "WY2026"
    assert body["this_season"]["absence"] is None
    assert body["source"] == {"dataset": "cdec_snowpack_swe_daily",
                              "method": season.METHODS[("snow", "swe_in")]}
    q, p = [s for s in pool.statements if s[0] == season.LWT_SQL][-1]
    assert p == {"d": "cdec_snowpack_swe_daily", "s": "state_avg_swc"}
    json.dumps(body, allow_nan=False)                         # no NaN leaks to the wire


# ---------------------------------------------------------------------------
# H11 — the cumulative payload is untouched but for enso.now
# ---------------------------------------------------------------------------

def test_h11_cumulative_payload_gains_only_enso_now():
    import test_season as S
    p = S._build(S._history("precip", 1947, 2025, S._varied, frontier=date(2026, 9, 24)))
    assert tuple(p) == season.RESPONSE_KEYS
    assert "range" not in p
    assert all("outlook" not in c for c in p["enso"]["categories"].values())
    assert list(p["enso"]) == ["classifier", "catalog_version", "mapping", "developing",
                               "categories", "now"]


# ---------------------------------------------------------------------------
# The handback's sizes
# ---------------------------------------------------------------------------

def test_hydro_payload_sizes_for_the_handback(capsys):
    """reservoir:ca_major8 (rows 1996-01-01 -> 2026-09-28) and snow:ca_state
    (2006-05-04 -> 2026-06-05, snow season only) shapes."""
    m8 = {d: 1.0 for d in _days(date(1996, 1, 1), date(1996, 9, 30))}
    m8.update(_history(1996, 2025, _tank, frontier=date(2026, 9, 28)))
    sn = {d: 3.0 for d in _days(date(2006, 5, 4), date(2006, 5, 31))}
    sn.update(_history(2006, 2025, lambda d, s: 8.0 + d.day / 10, var="swe_in"))
    for d in _days(date(2026, 6, 1), date(2026, 6, 5)):
        sn[d] = 0.5
    bins = _bins(overrides=[(y, "nino", "strong") for y in (1997, 2009, 2015, 2023)])
    sizes = {
        "reservoir:ca_major8": len(json.dumps(_build(
            m8, area="reservoir:ca_major8", bins=bins,
            meta={"series_frontiers": {i: "2026-09-28" for i in season.RESERVOIR_IDS}})).encode()),
        "snow:ca_state": len(json.dumps(_build(sn, var="swe_in", area="snow:ca_state",
                                               bins=bins)).encode()),
    }
    with capsys.disabled():
        print(f"\n[hydro sizes] {sizes}")
    assert all(v < 2_000_000 for v in sizes.values())
