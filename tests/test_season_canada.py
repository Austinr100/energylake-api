"""
Tests for the Canadian Columbia in the season API (d091536): `swe` on
`snow:col_canada`, a level like the six US basins, whose season qualifies when
every day Nov 1 -> May 31 carries a value (summer days the BC stations do not
report are gaps, not zeros), in season.py (pure) and the routes in main.py
against an in-memory stand-in for `snow_basin_index_daily` + the ENSO bank.

K1..K6 are the spec's §2 table (energylake-pantry
docs/cc_spec_2026_09_30_season_canada_snow_api.md); each test's name carries its
number. K0 replays the bank's own record shape (Gate 0, read from Neon
2026-10-01): the missing days of col_canada.SWE_PCT, exactly.
"""

import datetime
import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

import main
import season

UTC = datetime.timezone.utc
CUR_V = "cafe0001"
DEVELOPING = {"kind": "nino", "n_seasons": 4, "first_year": 2026, "latest_oni": 1.8,
              "latest_year": 2026, "first_season": "MAM", "latest_season": "JJA"}
SLOTS = season._slot_index("swe")
AX = season.axis("swe")
CANADA = "snow:col_canada"
LABEL = "Canadian Columbia (BC)"
FIXTURE = Path(__file__).parent / "fixtures" / "season_areas_us_snow_main_be703ac.json"


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

def _days(a: date, b: date):
    d = a
    while d <= b:
        yield d
        d += timedelta(days=1)


def _level(d: date) -> float:
    """A toy level, never 0 (a 0 read in would show): a ramp to a mid-March
    peak and down, sized by the season."""
    s = d.year if d.month >= 10 else d.year - 1
    doy = (d - date(s, 10, 1)).days
    scale = 60.0 + (s * 37 % 90)
    return round(1.0 + scale * (1.0 - abs(doy - 165) / 200.0), 2)


def _summerless(first: int, last: int, *, frontier: date, summer=((7, 1), (9, 30))):
    """Seasons first..last with every day valued but the summer (default
    Jul 1 -> Sep 30, the bare-pillow months): those days have NO ROW."""
    (am, ad), (bm, bd) = summer
    out = {}
    for d in _days(date(first, 10, 1), frontier):
        if (am, ad) <= (d.month, d.day) <= (bm, bd):
            continue
        out[d] = _level(d)
    return out


def _bins(first=1949, last=2026):
    return [{"enso_year": y, "kind": "neutral", "strength": None, "flavor": None}
            for y in range(first, last + 1)]


def _build(daily, area=CANADA, meta=None):
    return season.build_season(area, "swe", daily, classifier="cpc_oni",
                               catalog_version=CUR_V, developing=DEVELOPING,
                               bins=_bins(), oni_last_centre=(2026, 7),
                               source_meta=meta)


def _slot(d: date) -> int:
    return AX.index(f"{d.month:02d}-{d.day:02d}")


# ---------------------------------------------------------------------------
# K0 — the bank's record, replayed (Gate 0)
# ---------------------------------------------------------------------------

# col_canada.SWE_PCT: rows 1997-07-27 -> 2026-09-29, every row valued; the days
# with no row inside that span, as Neon listed them 2026-10-01.
GATE0_MISSING = (
    ("1998-04-19", "1998-04-19"), ("1998-06-21", "1998-09-30"),
    ("2012-05-02", "2012-05-08"),
    ("2021-08-24", "2021-08-25"), ("2021-08-28", "2021-08-28"),
    ("2021-09-02", "2021-09-04"), ("2021-09-13", "2021-09-14"),
    ("2021-09-17", "2021-09-17"), ("2021-10-02", "2021-10-02"),
    ("2022-07-16", "2022-07-16"),
    ("2023-10-16", "2023-10-16"), ("2023-10-21", "2023-10-21"),
    ("2023-10-23", "2023-10-23"),
    ("2024-07-01", "2024-09-21"), ("2024-09-28", "2024-09-28"),
    ("2024-10-09", "2024-10-09"), ("2025-01-18", "2025-01-18"),
    ("2025-06-11", "2025-06-11"), ("2025-06-15", "2025-06-20"),
    ("2025-06-22", "2025-06-22"), ("2025-06-25", "2025-09-30"),
    ("2025-10-16", "2025-10-16"),
    ("2026-07-04", "2026-09-13"), ("2026-09-18", "2026-09-25"),
)


def _gate0_daily():
    gone = set()
    for a, b in GATE0_MISSING:
        gone.update(_days(date.fromisoformat(a), date.fromisoformat(b)))
    daily = {d: _level(d) for d in _days(date(1997, 7, 27), date(2026, 9, 29))
             if d not in gone}
    daily[date(2026, 4, 4)] = 109.98            # WY2026's banked peak (§3)
    return daily


def test_k0_gate0_record_base_is_25():
    p = _build(_gate0_daily())
    strict = ([f"WY{y}" for y in range(1999, 2012)] + [f"WY{y}" for y in range(2013, 2021)]
              + ["WY2023"])
    assert len(strict) == 22
    assert p["base"]["seasons"] == sorted(strict + ["WY2021", "WY2022", "WY2024"])
    assert p["base"]["n"] == 25
    ex = {e["season"]: e for e in p["base"]["excluded"]}
    assert set(ex) == {"WY1997", "WY1998", "WY2012", "WY2025"}
    assert ex["WY2012"]["first_missing"] == "2012-05-02"
    assert ex["WY2012"]["days_complete"] == 213 - 7 and ex["WY2012"]["days_in_window"] == 213
    assert ex["WY2025"]["first_missing"] == "2025-01-18"
    assert ex["WY1998"]["first_missing"] == "1998-04-19"
    # WY2026 is the current season (frontier 2026-09-29 < Sep 30): not in the base.
    assert p["frontier"] == "2026-09-29" and p["this_season"]["season"] == "WY2026"
    assert p["this_season"]["complete_to_date"] is True
    assert p["peak"]["this_season"] == {"value": 110.0, "date": "2026-04-04"}


# ---------------------------------------------------------------------------
# K2 — the window qualifies Canada; the US basins stay strict
# ---------------------------------------------------------------------------

def test_k2_summer_gaps_qualify_a_feb_gap_does_not_and_the_us_stay_strict():
    daily = _summerless(2000, 2025, frontier=date(2026, 6, 30))
    del daily[date(2011, 2, 14)]                 # WY2011: one day in Feb
    p = _build(daily)
    by = {y["season"]: y for y in p["years"]}
    assert by["WY2010"]["complete"] is True      # missing only Jul–Sep
    assert by["WY2011"]["complete"] is False
    assert "WY2010" in p["base"]["seasons"] and "WY2011" not in p["base"]["seasons"]
    assert p["base"]["n"] == 24                  # WY2001..WY2025 but WY2011 (WY2026 current)
    ex = {e["season"]: e for e in p["base"]["excluded"]}
    assert ex == {"WY2011": {"season": "WY2011", "days_complete": 211,
                             "days_in_window": 212, "first_missing": "2011-02-14"}}

    # The same rows on a US basin: strict, the whole water year, so none qualifies.
    us = _build(daily, area="snow:snake")
    assert us["base"]["n"] == 0
    assert {y["season"]: y["complete"] for y in us["years"]}["WY2010"] is False
    assert all(e["days_in_window"] in (365, 366) for e in us["base"]["excluded"])
    assert season.floor("swe", "snow:snake") is None
    assert season.floor("swe", CANADA) == (1.0, ((11, 1), (5, 31)))

    # The window is Nov 1 -> May 31 by calendar date, a leap Feb 29 inside it.
    assert season.count_window("swe", 2011, CANADA) == (date(2011, 11, 1), date(2012, 5, 31))
    assert season.window_days("swe", 2011, CANADA) == 213
    assert season.count_window("swe", 2011, "snow:snake") == season.bounds("swe", 2011)


def test_k2_oct_and_june_gaps_do_not_disqualify_nov_1_and_may_31_do():
    daily = _summerless(2005, 2010, frontier=date(2011, 6, 30))
    for d in (date(2005, 10, 20), date(2006, 6, 3)):     # WY2006: outside the window
        del daily[d]
    del daily[date(2006, 11, 1)]                          # WY2007: the window's first day
    del daily[date(2008, 5, 31)]                          # WY2008: its last day
    by = {y["season"]: y["complete"] for y in _build(daily)["years"]}
    assert by["WY2006"] is True and by["WY2007"] is False and by["WY2008"] is False


# ---------------------------------------------------------------------------
# K3 — a summer day with no row is a gap: absent, never 0, never carried
# ---------------------------------------------------------------------------

def test_k3_a_missing_summer_day_is_a_gap_on_the_chart():
    daily = _summerless(2000, 2025, frontier=date(2026, 6, 30))
    p = _build(daily)
    aug15, jun30, jul1 = _slot(date(2001, 8, 15)), _slot(date(2001, 6, 30)), _slot(date(2001, 7, 1))
    wy = p["curves"]["WY2010"]
    assert wy[jun30] == round(_level(date(2010, 6, 30)), 1)
    assert wy[jul1] is None and wy[aug15] is None and wy[-1] is None
    assert all(v is None for v in wy[jul1:])                 # not 0, not Jun 30 carried
    ls = p["last_season"]
    assert ls["season"] == "WY2025" and ls["values"][aug15] is None
    assert ls["complete"] is True and ls["absence"] is None    # a summer gap is not a gap
    assert p["range"]["min"][aug15] is None and p["range"]["max"][aug15] is None
    by = {y["season"]: y for y in p["years"]}
    assert by["WY2010"]["final"] is None
    # The walk itself: a summer day is counted nowhere, missing or complete.
    w = season._walk("swe", 2009, daily, date(2010, 9, 30), SLOTS, CANADA)
    assert w.first_missing is None and w.days_missing == 0
    assert w.window_complete == w.window_days == 212


# ---------------------------------------------------------------------------
# K4 — the ENSO outlook: peak and Apr 1 by hand; a missing Jul 1 drops n
# ---------------------------------------------------------------------------

def test_k4_outlook_equals_a_hand_calculation_and_jul1_excludes_a_missing_year():
    # Rows through Jul 15 each year (Jul 16 -> Sep 30 bare); WY2004 has no Jul 1.
    # The frontier is in WY2008, so WY2001..WY2007 are past.
    daily = _summerless(2000, 2007, frontier=date(2007, 10, 5), summer=((7, 16), (9, 30)))
    del daily[date(2004, 7, 1)]
    p = _build(daily)
    seasons = [f"WY{y}" for y in range(2001, 2008)]
    assert p["base"]["seasons"] == seasons and p["base"]["n"] == 7

    # By hand, from the fixture's own formula — no walk.
    def year_days(wy):
        return [d for d in _days(date(wy - 1, 10, 1), date(wy, 9, 30)) if d in daily]
    peaks, peak_days, apr1, jul1 = [], [], [], []
    for wy in range(2001, 2008):
        ds = year_days(wy)
        top = max(daily[d] for d in ds)
        peaks.append(top)
        peak_days.append(next(d for d in ds if daily[d] == top))
        apr1.append(daily[date(wy, 4, 1)])
        if date(wy, 7, 1) in daily:
            jul1.append(daily[date(wy, 7, 1)])
    assert len(jul1) == 6

    ol = p["enso"]["categories"]["neutral"]["outlook"]
    assert ol["n"] == 7 and ol["seasons"] == seasons
    assert ol["peak"]["min"] == round(min(peaks), 1)
    assert ol["peak"]["median"] == round(float(np.median(peaks)), 1)
    assert ol["peak"]["max"] == round(max(peaks), 1)
    assert ol["on_day"]["04-01"] == {"min": round(min(apr1), 1),
                                     "median": round(float(np.median(apr1)), 1),
                                     "max": round(max(apr1), 1), "n": 7}
    assert ol["on_day"]["07-01"] == {"min": round(min(jul1), 1),
                                     "median": round(float(np.median(jul1)), 1),
                                     "max": round(max(jul1), 1), "n": 6}
    m04 = next(m for m in ol["members"] if m["season"] == "WY2004")
    assert m04["jul1"] is None and m04["apr1"] == round(daily[date(2004, 4, 1)], 1)
    assert p["peak"]["base"]["n"] == 7
    assert p["peak"]["base"]["median"] == round(float(np.median(peaks)), 1)
    assert p["enso"]["now"]["category"] == "nino_strong"


# ---------------------------------------------------------------------------
# K6 — the receipt says the window and the source's summer, in words
# ---------------------------------------------------------------------------

def test_k6_the_receipt_names_the_window_and_the_summer():
    p = _build(_summerless(2000, 2025, frontier=date(2026, 6, 30)),
               meta={"n_index": 6, "n_reporting": 6, "normals_version": "v1"})
    b = p["base"]
    assert b["window"] == {"start_md": "11-01", "end_md": "05-31"}
    assert b["min_days_frac"] == 1.0
    for text in (b["rule"], p["source"]["method"]):
        assert "every day from Nov 1 through May 31 carries a value" in text
        assert "summer days with no value are not reported by the source" in text
    assert p["source"]["dataset"] == "snow_basin_index_daily"
    assert (p["source"]["n_index"], p["source"]["n_reporting"]) == (6, 6)
    assert p["units"] == "% of normal peak" and p["season"]["mode"] == "level"
    assert tuple(p) == season.LEVEL_RESPONSE_KEYS and p["peak_absence"] is None

    # The US basins' receipt is main's, word for word.
    us = _build(_summerless(2000, 2025, frontier=date(2026, 6, 30)), area="snow:snake")
    assert us["base"]["rule"] == ("every complete season, full record, excluding the "
                                  "current season")
    assert "window" not in us["base"]
    assert us["source"]["method"] == season.METHODS[("snow", "swe")]


# ---------------------------------------------------------------------------
# Routes: K1 (areas) and K5 (the board)
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


def _wy_start(d: date) -> int:
    return d.year if d.month >= 10 else d.year - 1


class _CanadaPool:
    """The six US basins (whole years, 1983 -> 2026-09-28) and the Canadian
    Columbia (the Gate 0 record). AREAS_SNOW_SQL's counts are computed from
    those rows as the SQL would: `n` every valued day, `nw` Nov -> May only."""

    def __init__(self):
        self.statements = []
        us = {d: _level(d) for d in _days(date(1983, 10, 1), date(2026, 9, 28))}
        self.snow = {b: us for b in season.SNOW_BASINS}
        self.snow["col_canada"] = _gate0_daily()

    def connection(self):
        return _Conn(self)

    def answer(self, q, p):
        if q == season.SNOW_SQL:
            b = p["s"].removesuffix(".SWE_PCT")
            rows = [{"obs_date": d, "v": v, "meta": None} for d, v in sorted(self.snow[b].items())]
            rows[-1]["meta"] = {"basin": b, "n_index": 6, "n_reporting": 6,
                                "normals_version": "v1"}
            return rows
        if q == main._enso.RUN_SQL:
            return [{"classifier": p["c"], "developing": DEVELOPING, "catalog_version": CUR_V,
                     "source": {}, "n_episodes": 0, "n_year_bins": 78,
                     "computed_at": datetime.datetime(2026, 9, 24, tzinfo=UTC)}]
        if q == main._enso.YEAR_BINS_SQL:
            return _bins()
        if q == season.ONI_LAST_SQL:
            return [{"ts": datetime.datetime(2026, 7, 1, tzinfo=UTC)}]
        if q in (season.AREAS_PRECIP_SQL, season.AREAS_STATION_DD_SQL, season.AREAS_LWT_SQL,
                 season.AREAS_CA_SNOW_SQL, season.AREAS_RESERVOIR_SQL, season.AREAS_LOAD_SQL, season.AREAS_RESERVOIR_REGIONS_SQL):
            return []
        if q == season.AREAS_SNOW_SQL:
            out = []
            for b, daily in self.snow.items():
                per: dict[int, list[int]] = {}
                for d, v in daily.items():
                    c = per.setdefault(_wy_start(d), [0, 0])
                    if v is not None:
                        c[0] += 1
                        c[1] += d.month in (11, 12, 1, 2, 3, 4, 5)
                out += [{"id": b, "s": s, "n": n, "nw": nw} for s, (n, nw) in per.items()]
            return out
        raise AssertionError(f"unexpected statement: {q}")


@pytest.fixture(autouse=True)
def _clear_memos():
    main._season_cache.clear()
    main._season_areas_cache.clear()
    main._season_snapshot_cache.clear()
    yield
    main._season_cache.clear()
    main._season_areas_cache.clear()
    main._season_snapshot_cache.clear()


@pytest.fixture
def pool(monkeypatch):
    p = _CanadaPool()
    monkeypatch.setattr(main, "_pool", p)
    monkeypatch.setattr(main, "_season_pool", p)     # d091542: the season routes' own pool
    return p


@pytest.fixture
def client(pool):
    return TestClient(main.app)


US_SIX = [f"snow:{b}" for b in season.SNOW_BASINS]


def _us_six_json(areas) -> str:
    """The US six as main @ be703ac served them: d091542's added keys (and only
    those) are taken back out before the comparison."""
    six = [{k: ([{vk: vv for vk, vv in v.items() if vk not in season.VAR_ADDED_KEYS}
                 for v in val] if k == "vars" else val)
            for k, val in a.items() if k not in season.AREA_ADDED_KEYS}
           for a in areas if a["area"] in US_SIX]
    return json.dumps(six, ensure_ascii=False, indent=1) + "\n"


def test_k1_areas_lists_canada_once_after_the_six_and_the_six_are_mains(client):
    r = client.get("/api/weather/season/areas")
    assert r.status_code == 200
    areas = r.json()["areas"]
    assert len(areas) == 89                           # d091542: + 3 reservoir regions
    ids = [a["area"] for a in areas]
    assert ids.count(CANADA) == 1
    i = ids.index(CANADA)
    assert ids[i - 6:i] == US_SIX and ids[i + 1] == "snow:ca_state"
    ca = areas[i]
    assert (ca["kind"], ca["label"]) == ("snow", LABEL)
    # /areas counts every season with rows (d091522's rule), the current WY2026 too:
    # the base's 25 plus WY2026; WY1997 (rows only from Jul 27) is the first season.
    assert ca["vars"] == [{"var": "swe", "season": "water_year", "units": "% of normal peak",
                           "first_season": "WY1997", "complete_seasons": 26,
                           "qualifying_seasons": 26,
                           "data_type": "snow_water_equivalent",          # d091542
                           "measure": "percent of normal peak"}]
    # The US six, byte for byte, as main @ be703ac served them from these rows.
    assert _us_six_json(areas) == FIXTURE.read_text(encoding="utf-8")


def test_k1_the_season_read_and_the_vocabulary(client, pool):
    r = client.get("/api/weather/season", params={"area": CANADA, "var": "swe"})
    assert r.status_code == 200 and r.headers["cache-control"] == "max-age=900"
    body = r.json()
    assert body["base"]["n"] == 25 and body["peak"]["this_season"]["value"] == 110.0
    q, p = next(s for s in pool.statements if s[0] == season.SNOW_SQL)
    assert p == {"d": "snow_basin_index_daily", "s": "col_canada.SWE_PCT"}
    r = client.get("/api/weather/season", params={"area": CANADA, "var": "swe_in"})
    assert r.status_code == 400 and "snow: areas carry swe" in r.json()["detail"]
    r = client.get("/api/weather/season", params={"area": "snow:yakima", "var": "swe"})
    assert r.status_code == 400 and "col_canada" in r.json()["detail"]


def test_k5_the_board_is_the_six_us_basins_in_mains_order(client, pool):
    r = client.get("/api/weather/snow/board")
    assert r.status_code == 200
    rows = r.json()["basins"]
    assert [b["area"] for b in rows] == US_SIX
    assert season.SNOW_BASINS == ("columbia_above_the_dalles", "col_above_grand_coulee",
                                  "col_mid_tributaries", "snake", "snake_upper", "snake_lower")
    assert not any(p and p.get("s") == "col_canada.SWE_PCT" for _, p in pool.statements)


def test_k5_no_union_carries_canada():
    assert "col_canada" not in season.SNOW_BASINS
    assert season.CANADA_SNOW == ("col_canada",)
    assert all("col_canada" not in lab for lab in season.SNOW_LABELS)
