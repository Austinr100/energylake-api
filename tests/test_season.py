"""
Tests for the season-to-date API (d091503): season.py, pure, and the two routes
in main.py against an in-memory stand-in for the three sources + the ENSO bank.

S1..S11 are the spec's §3 table (cc_spec_2026_09_28_season_api.md); each test's
name carries its number. Fixtures are built here, by calendar date.
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
CUR_V = "cafe0001"
DEVELOPING = {"kind": "nino", "n_seasons": 4, "first_year": 2026, "latest_oni": 1.8,
              "latest_year": 2026, "first_season": "MAM", "latest_season": "JJA"}


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

def _days(a: date, b: date):
    d = a
    while d <= b:
        yield d
        d += timedelta(days=1)


def _fill(daily, var, s, value, *, through=None):
    """Every calendar day of season `s` (through `through`) gets value(d, s)."""
    a, b = season.bounds(var, s)
    for d in _days(a, min(b, through) if through else b):
        daily[d] = float(value(d, s))


def _history(var, first, last, value=lambda d, s: 1.0, *, frontier=None):
    daily = {}
    for s in range(first, last + 1):
        _fill(daily, var, s, value, through=frontier)
    return daily


def _bin(y, kind="neutral", strength=None, flavor=None):
    return {"enso_year": y, "kind": kind, "strength": strength, "flavor": flavor}


def _bins(first=1949, last=2026, overrides=None):
    over = {b["enso_year"]: b for b in (overrides or [])}
    return [over.get(y, _bin(y)) for y in range(first, last + 1)]


def _build(daily, var="precip", area="station:USW00023232", bins=None,
           developing=DEVELOPING, oni_last_centre=(2026, 7)):
    return season.build_season(area, var, daily, classifier="cpc_oni",
                               catalog_version=CUR_V, developing=developing,
                               bins=_bins() if bins is None else bins,
                               oni_last_centre=oni_last_centre)


# ---------------------------------------------------------------------------
# S1 — a gap is a missing day, in the base and in the current season
# ---------------------------------------------------------------------------

def test_s1_gap_excludes_base_season_and_stops_this_season():
    # WY1990..WY2026 (start years 1989..2025); frontier 2026-09-24.
    daily = _history("precip", 1989, 2025, frontier=date(2026, 9, 24))
    del daily[date(2000, 1, 15)]                   # absent row inside WY2000
    daily[date(2003, 3, 3)] = None                 # NULL value inside WY2003
    del daily[date(2026, 2, 19)]                   # Sacramento's own hole, WY2026
    p = _build(daily)

    ex = {e["season"]: e for e in p["base"]["excluded"]}
    assert ex["WY2000"] == {"season": "WY2000", "days_complete": 365,
                            "days_in_window": 366, "first_missing": "2000-01-15"}
    assert ex["WY2003"]["first_missing"] == "2003-03-03"
    assert "WY2000" not in p["base"]["seasons"] and "WY2003" not in p["base"]["seasons"]
    assert p["base"]["n"] == 36 - 2

    ts = p["this_season"]
    assert ts["season"] == "WY2026"
    assert ts["through"] == "2026-02-18"
    assert ts["complete_to_date"] is False
    assert ts["absence"]["first_missing"] == "2026-02-19"
    assert ts["absence"]["mode"] == "absent"
    i18 = p["axis"].index("02-18")
    assert ts["values"][i18] == float(i18 + 1)
    assert all(v is None for v in ts["values"][i18 + 1:])
    assert p["readout"] is None
    assert p["readout_absence"]["first_missing"] == "2026-02-19"
    # to_date is on the through day, like for like.
    wy25 = next(y for y in p["years"] if y["season"] == "WY2025")
    assert wy25["to_date"] == float(i18 + 1)


def test_s1_a_null_value_is_incomplete_not_zero():
    daily = _history("precip", 2020, 2025, frontier=date(2026, 9, 24))
    daily[date(2025, 12, 1)] = None
    p = _build(daily)
    assert p["this_season"]["absence"]["mode"] == "incomplete"
    assert p["this_season"]["through"] == "2025-11-30"


# ---------------------------------------------------------------------------
# S2 — the leap fold
# ---------------------------------------------------------------------------

def test_s2_leap_day_folds_into_feb_28():
    daily = {}
    _fill(daily, "precip", 2023, lambda d, s: d.day)       # WY2024 holds 2024-02-29
    _fill(daily, "precip", 2024, lambda d, s: 0.0)
    p = _build(daily)
    assert len(p["axis"]) == 365 and "02-29" not in p["axis"]
    walk = season._walk("precip", 2023, daily, date(2024, 9, 30), season._slot_index("precip"))
    i27, i28 = p["axis"].index("02-27"), p["axis"].index("02-28")
    assert walk.values[i28] - walk.values[i27] == 28 + 29
    total = sum(d.day for d in _days(date(2023, 10, 1), date(2024, 9, 30)))
    assert walk.values[-1] == total
    assert walk.days_in_window == 366 and walk.days_complete == 366
    y = next(y for y in p["years"] if y["season"] == "WY2024")
    assert y["final"] == round(total, 1) and y["complete"] is True


def test_s2_missing_feb_29_withdraws_the_feb_28_day():
    daily = {}
    _fill(daily, "precip", 2023, lambda d, s: 1.0)
    del daily[date(2024, 2, 29)]
    w = season._walk("precip", 2023, daily, date(2024, 9, 30), season._slot_index("precip"))
    ax = season.axis("precip")
    assert w.values[ax.index("02-27")] is not None
    assert w.values[ax.index("02-28")] is None
    assert w.through == date(2024, 2, 27)
    assert w.first_missing == date(2024, 2, 29)


# ---------------------------------------------------------------------------
# S3 — the cone
# ---------------------------------------------------------------------------

def test_s3_cone_is_numpy_linear_per_day():
    rng = np.random.default_rng(7)
    B = rng.gamma(2.0, 3.0, size=(5, 365)).cumsum(axis=1)
    c = season.cone(B)
    for q in season.PERCENTILES:
        assert np.array_equal(c[f"p{q}"], np.percentile(B, q, axis=0, method="linear"))
    assert np.array_equal(c["p0"], B.min(axis=0))
    assert np.array_equal(c["p100"], B.max(axis=0))


def _varied(d, s):
    return ((s * 7 + d.toordinal()) % 11) / 2.0


def test_s3_cone_gate_29_is_null_30_is_present():
    frontier = date(2026, 9, 24)
    d29 = _history("precip", 1996, 2025, _varied, frontier=frontier)   # 29 complete + current
    p = _build(d29)
    assert p["base"]["n"] == 29
    assert p["percentiles"] is None
    assert p["percentiles_absence"] == {"reason": "short_record", "n": 29}
    assert p["readout"]["percentile"] is None and p["readout"]["median"] is None

    d30 = _history("precip", 1995, 2025, _varied, frontier=frontier)
    p = _build(d30)
    assert p["base"]["n"] == 30 and p["percentiles_absence"] is None
    B = np.array([season._walk("precip", s, d30, season.bounds("precip", s)[1],
                               season._slot_index("precip")).values for s in range(1995, 2025)])
    want = np.round(np.percentile(B, 50, axis=0, method="linear"), 1)
    assert p["percentiles"]["p50"] == want.tolist()
    assert p["percentiles"]["method"] == "linear (type 7)"


# ---------------------------------------------------------------------------
# S4 — the five-year band skips incomplete seasons
# ---------------------------------------------------------------------------

def test_s4_five_year_skips_incomplete_and_lists_its_five():
    daily = _history("precip", 2015, 2025, lambda d, s: s - 2000, frontier=date(2026, 9, 24))
    del daily[date(2023, 6, 1)]                    # WY2023 incomplete
    p = _build(daily)
    fy = p["five_year"]
    assert fy["seasons"] == ["WY2020", "WY2021", "WY2022", "WY2024", "WY2025"]
    assert fy["mean"][0] == np.mean([19, 20, 21, 23, 24])
    assert fy["min"][0] == 19 and fy["max"][0] == 24


def test_s4_fewer_than_five_is_null():
    p = _build(_history("precip", 2021, 2025, frontier=date(2026, 9, 24)))
    assert p["five_year"] is None and p["five_year_absence"]["n"] == 4


# ---------------------------------------------------------------------------
# S5 — season → ENSO year
# ---------------------------------------------------------------------------

def test_s5_mapping():
    assert season.label("precip", 2015) == "WY2016" and season.enso_year("precip", 2015) == 2015
    assert season.label("hdd", 2015) == "2015-16" and season.enso_year("hdd", 2015) == 2015
    assert season.label("cdd", 2016) == "2016" and season.enso_year("cdd", 2016) == 2016
    assert season.season_of("precip", date(2016, 9, 30)) == 2015
    assert season.season_of("hdd", date(2016, 3, 31)) == 2015
    assert season.season_of("hdd", date(2016, 4, 1)) is None
    assert season.season_of("cdd", date(2016, 5, 1)) == 2016
    assert [len(season.axis(v)) for v in ("precip", "hdd", "cdd")] == [365, 151, 153]


# ---------------------------------------------------------------------------
# S6 — categories from a year bin; the open year; before the catalog
# ---------------------------------------------------------------------------

def test_s6_categories():
    assert season.categories_of(_bin(1997, "nino", "strong", "EP")) == ["nino", "nino_strong", "nino_EP"]
    assert season.categories_of(_bin(2015, "nino", "strong", "mixed")) == ["nino", "nino_strong"]
    assert season.categories_of(_bin(2001, "neutral")) == ["neutral"]
    bins = {b["enso_year"]: b for b in _bins()}
    opened = season.open_years(DEVELOPING, (2026, 7), bins)
    assert season.classify(2026, bins, opened) == (None, "open")      # binned neutral, still open
    assert season.classify(2025, bins, opened) == (["neutral"], None)
    assert season.classify(1948, bins, opened) == (None, "before_catalog")


def test_s6_open_year_in_payload_and_pantry_fallback():
    daily = _history("precip", 1947, 2026, frontier=date(2026, 10, 5))
    p = _build(daily)
    by = {y["season"]: y for y in p["years"]}
    assert by["WY2027"]["categories"] is None and by["WY2027"]["category_absence"] == "open"
    assert by["WY1948"]["category_absence"] == "before_catalog"
    assert "WY2027" not in p["curves"] and "WY1948" not in p["curves"]
    assert p["enso"]["developing"] == DEVELOPING
    # developing null: the pantry's rule — open iff (E+1, 3) > the newest ONI centre.
    assert season.open_years(None, (2026, 7), [2024, 2025, 2026]) == {2026}
    assert season.open_years(None, (2026, 2), [2024, 2025, 2026]) == {2025, 2026}


# ---------------------------------------------------------------------------
# S7 — a small category has no median
# ---------------------------------------------------------------------------

def test_s7_small_n_category():
    bins = _bins(overrides=[_bin(1997, "nino", "strong", "EP"), _bin(2015, "nino", "strong", "mixed")])
    p = _build(_history("precip", 1990, 2025, frontier=date(2026, 9, 24)), bins=bins)
    ns = p["enso"]["categories"]["nino_strong"]
    assert ns["n"] == 2 and ns["seasons"] == ["WY1998", "WY2016"]
    assert ns["median"] is None and ns["absence"] == {"reason": "small_n", "n": 2}
    assert p["readout"]["vs_category"]["nino_strong"] is None
    assert p["enso"]["categories"]["neutral"]["median"] is not None


# ---------------------------------------------------------------------------
# S8 — the readout
# ---------------------------------------------------------------------------

def test_s8_mid_rank_with_ties():
    assert season.mid_rank(5.0, [1, 5, 5, 9]) == 100 * (1 + 0.5 * 2) / 4
    assert season.mid_rank(0.0, [1, 2]) == 0.0
    assert season.mid_rank(3.0, [1, 2]) == 100.0


def test_s8_readout_zero_median_and_category_keys():
    # Every base season dry: p50 is 0 everywhere, so pct_of_median is null.
    daily = _history("precip", 1990, 2025, lambda d, s: 0.0, frontier=date(2026, 9, 24))
    p = _build(daily)
    r = p["readout"]
    assert r["median"] == 0.0 and r["pct_of_median"] is None
    assert r["percentile"] == 50.0                     # all tied
    assert list(r["vs_category"]) == list(season.CATEGORIES) and len(r["vs_category"]) == 13
    assert r["day"] == p["axis"].index("09-24")
    assert list(p["enso"]["categories"]) == list(season.CATEGORIES)


def test_s8_readout_numbers():
    daily = _history("precip", 1990, 2025, lambda d, s: (s % 4) + 1, frontier=date(2026, 9, 24))
    p = _build(daily)
    r = p["readout"]
    day = r["day"]
    # the current season (s=2025) accrues 2/day; base seasons 1..4/day.
    assert r["value"] == 2.0 * (day + 1)
    assert r["pct_of_median"] == round(100 * r["value"] / r["median"], 1)
    assert r["vs_five_year"] == round(r["value"] - p["five_year"]["mean"][day], 1)


# ---------------------------------------------------------------------------
# S9 — between seasons
# ---------------------------------------------------------------------------

def test_s9_hdd_between_seasons():
    daily = {}
    for s in range(1990, 2026):
        _fill(daily, "hdd", s, lambda d, s: 10.0)
    daily[date(2026, 9, 24)] = 0.0            # the frontier, in a shoulder month
    p = _build(daily, var="hdd", area="station:USW00024233")
    assert p["frontier"] == "2026-09-24"
    assert p["this_season"] is None
    assert p["this_season_absence"]["reason"] == "between_seasons"
    assert p["last_season"]["season"] == "2025-26" and p["last_season"]["complete"] is True
    assert p["base"]["n"] == 36 and "2025-26" in p["base"]["seasons"]
    assert p["readout"]["day"] == 150 and p["axis"][150] == "03-31"
    assert p["readout"]["value"] == 1510.0


# ---------------------------------------------------------------------------
# S10 — refusals name the vocabulary
# ---------------------------------------------------------------------------

def test_s10_refusals(client):
    r = client.get("/api/weather/season", params={"area": "lwt:BPAT", "var": "precip"})
    assert r.status_code == 400 and "hdd, cdd" in r.json()["detail"]
    r = client.get("/api/weather/season", params={"area": "station:USW99999999", "var": "hdd"})
    assert r.status_code == 400
    assert "USW00023232" in r.json()["detail"] and "BPAT" in r.json()["detail"]
    r = client.get("/api/weather/season", params={"area": "lwt:BPAT", "var": "snow"})
    assert r.status_code == 400 and "precip, hdd, cdd" in r.json()["detail"]
    r = client.get("/api/weather/season",
                   params={"area": "lwt:BPAT", "var": "hdd", "classifier": "mei"})
    assert r.status_code == 400 and "cpc_oni, roni" in r.json()["detail"]


# ---------------------------------------------------------------------------
# S11 — route smoke against a fake pool
# ---------------------------------------------------------------------------

class _Cur:
    def __init__(self, pool):
        self.pool, self._rows = pool, []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, q, params=None):
        self.pool.statements.append(q)
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


class _SeasonPool:
    """Answers the season routes' statements by identity."""

    def __init__(self):
        self.statements = []
        self.precip = _history("precip", 1990, 2025, _varied, frontier=date(2026, 9, 24))

    def connection(self):
        return _Conn(self)

    def answer(self, q, p):
        if q == season.PRECIP_SQL:
            return [{"obs_date": d, "v": v} for d, v in sorted(self.precip.items())]
        if q == season.STATION_DD_SQL:
            return [{"obs_date": d, "hdd": 5.0, "cdd": 0.0, "basis_complete": True}
                    for d in _days(date(2011, 1, 1), date(2026, 9, 24))]
        if q == season.LWT_SQL:
            return [{"obs_date": d, "v": 3.0} for d in _days(date(2011, 1, 1), date(2026, 9, 24))]
        if q == main._enso.RUN_SQL:
            return [{"classifier": p["c"], "developing": DEVELOPING, "catalog_version": CUR_V,
                     "source": {}, "n_episodes": 0, "n_year_bins": 78,
                     "computed_at": datetime.datetime(2026, 9, 24, tzinfo=UTC)}]
        if q == main._enso.YEAR_BINS_SQL:
            return _bins()
        if q == season.ONI_LAST_SQL:
            return [{"ts": datetime.datetime(2026, 7, 1, tzinfo=UTC)}]
        if q == season.AREAS_PRECIP_SQL:
            return [{"id": "USW00023232", "s": s, "n": season.days_in_window("precip", s)}
                    for s in range(1941, 2026)]
        if q == season.AREAS_STATION_DD_SQL:
            return [{"id": "USW00024233", "var": "hdd", "s": s,
                     "n": season.days_in_window("hdd", s)} for s in range(1948, 2026)]
        if q == season.AREAS_LWT_SQL:
            return [{"id": "BPAT", "var": "hdd", "s": 2011, "n": 151}]
        if q in (season.AREAS_SNOW_SQL, season.AREAS_CA_SNOW_SQL, season.AREAS_RESERVOIR_SQL):
            return []
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
    p = _SeasonPool()
    monkeypatch.setattr(main, "_pool", p)
    return p


@pytest.fixture
def client(pool):
    return TestClient(main.app)


def test_s11_route_key_order_and_areas(client, pool):
    r = client.get("/api/weather/season",
                   params={"area": "station:USW00023232", "var": "precip"})
    assert r.status_code == 200
    body = r.json()
    assert tuple(body) == season.RESPONSE_KEYS
    table = ("area", "var", "units", "season", "frontier", "axis", "base", "percentiles",
             "normal", "five_year", "this_season", "last_season", "enso", "years",
             "curves", "readout", "peak", "source")
    assert tuple(k for k in body if not k.endswith("_absence")) == table
    assert body["units"] == "mm" and body["frontier"] == "2026-09-24"
    assert body["base"]["n"] == 35 and body["percentiles"] is not None
    assert r.headers["cache-control"] == "max-age=900"

    r = client.get("/api/weather/season/areas")
    areas = r.json()["areas"]
    assert len(areas) == 57          # d091522: + 4 California snow areas + 9 reservoir areas
    assert [a["kind"] for a in areas].count("station") == 21
    assert [a["kind"] for a in areas].count("lwt") == 17
    assert areas[0]["area"] == "station:USW00024157" and areas[0]["label"] == "Spokane"
    assert [a["label"] for a in areas[17:21]] == list(season.UNLABELLED_STATIONS)
    assert "state" not in areas[17]
    assert [a["label"] for a in areas[21:38]] == sorted(season.LWT_BAS)
    sac = next(a for a in areas if a["area"] == "station:USW00023232")
    pv = next(v for v in sac["vars"] if v["var"] == "precip")
    assert pv == {"var": "precip", "season": "water_year", "units": "mm",
                  "first_season": "WY1942", "complete_seasons": 85}
    assert [v["var"] for v in areas[37]["vars"]] == ["hdd", "cdd"]


def test_s11_lwt_short_record_with_five_year(client):
    r = client.get("/api/weather/season", params={"area": "lwt:BPAT", "var": "hdd"})
    body = r.json()
    assert body["units"] == "°F·day"
    assert body["percentiles"] is None
    assert body["percentiles_absence"]["reason"] == "short_record"
    assert body["five_year"] is not None
    assert body["this_season"] is None and body["last_season"]["season"] == "2025-26"


def test_s11_station_dd_basis_incomplete_is_missing():
    rows = [{"obs_date": date(2025, 11, 1), "hdd": 12.0, "cdd": 0.0, "basis_complete": False},
            {"obs_date": date(2025, 11, 2), "hdd": None, "cdd": None, "basis_complete": True},
            {"obs_date": date(2025, 11, 3), "hdd": 0.0, "cdd": 0.0, "basis_complete": True}]
    d = season.daily_from_rows("hdd", rows, station_dd=True)
    assert d == {date(2025, 11, 1): None, date(2025, 11, 2): None, date(2025, 11, 3): 0.0}


def test_response_sizes_for_the_handback(capsys):
    """Spokane precip (WY1901..WY2026) and Seattle hdd (1948-49..2025-26) shapes."""
    sp = _history("precip", 1900, 2025, _varied, frontier=date(2026, 9, 24))
    se = {}
    for s in range(1948, 2026):
        _fill(se, "hdd", s, _varied)
    se[date(2026, 9, 24)] = 0.0
    sizes = {}
    for name, daily, var in (("spokane_precip", sp, "precip"), ("seattle_hdd", se, "hdd")):
        body = json.dumps(_build(daily, var=var)).encode()
        sizes[name] = len(body)
    with capsys.disabled():
        print(f"\n[season sizes] {sizes}")
    assert all(v < 2_000_000 for v in sizes.values())
