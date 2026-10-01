"""
Tests for daily peak load by balancing area in the season API (d091525):
`peak_load` and `peak_load_7d` on `ba:{code}` (EIA-930 `wecc_load_hourly`),
levels on the water year with d091522's 0.90 floor. season.py (pure) and the
routes in main.py against an in-memory stand-in for the dataset + the ENSO bank.

D1..D6 are the spec's §2 table (energylake-pantry
docs/cc_spec_2026_09_30_season_load_api.md); each test's name carries its
number, and the two reds are asserted in the cells the spec names (local-time
day with DST -> D1; a max over any number of hours -> D2). D6 is the rest of
the suite, unchanged but for the fixture lines named in the handback.
Fixtures are built here, by calendar date and by UTC hour.
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
CUR_V = "cafe0025"
DEVELOPING = {"kind": "nino", "n_seasons": 4, "first_year": 2026, "latest_oni": 1.8,
              "latest_year": 2026, "first_season": "MAM", "latest_season": "JJA"}
SLOTS = season._slot_index("peak_load")
AX = season.axis("peak_load")
NINO_STRONG = (1997, 2009, 2015, 2023)        # the cpc_oni bins at catalog d72f2150…


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

def _days(a: date, b: date):
    d = a
    while d <= b:
        yield d
        d += timedelta(days=1)


def _hour(y, m, d, h, mi=0):
    return datetime.datetime(y, m, d, h, mi, tzinfo=UTC)


def _summer(d, s):
    """A toy summer-peaking area (MW): a cool-season floor, a Jul-Aug hump,
    weekends lower. Seasons differ in size."""
    doy = (d - date(s, 10, 1)).days
    hump = max(0.0, np.cos(2 * np.pi * (doy - 305) / 365.0)) ** 3
    base = 25000.0 + (s * 37 % 90) * 40
    return round(base + 20000.0 * hump - (1500.0 if d.weekday() >= 5 else 0.0), 1)


def _winter(d, s):
    """A toy winter-peaking area (MW): its hump in Dec-Jan."""
    doy = (d - date(s, 10, 1)).days
    hump = max(0.0, np.cos(2 * np.pi * (doy - 100) / 365.0)) ** 3
    return round(6000.0 + 4000.0 * hump + (s % 7) * 30, 1)


def _history(first, last, value=_summer, *, frontier=None):
    """{UTC−8 day: daily peak} for water years `first`..`last` (start years),
    stopping at `frontier` (the current season's newest day)."""
    daily = {}
    for s in range(first, last + 1):
        a, b = season.bounds("peak_load", s)
        for d in _days(a, min(b, frontier) if frontier else b):
            daily[d] = float(value(d, s))
    return daily


def _bins(first=1949, last=2026, overrides=()):
    rows = {y: {"enso_year": y, "kind": "neutral", "strength": None, "flavor": None}
            for y in range(first, last + 1)}
    for y, kind, strength in overrides:
        rows[y] = {"enso_year": y, "kind": kind, "strength": strength, "flavor": None}
    return [rows[y] for y in sorted(rows)]


PROD_BINS = _bins(overrides=[(y, "nino", "strong") for y in NINO_STRONG]
                  + [(y, "nina", "weak") for y in (2021, 2022)]
                  + [(2020, "nina", "moderate")])


def _build(daily, var="peak_load", area="ba:CISO", bins=None, developing=DEVELOPING,
           classifier="cpc_oni"):
    return season.build_season(area, var, daily, classifier=classifier,
                               catalog_version=CUR_V, developing=developing,
                               bins=bins if bins is not None else PROD_BINS,
                               oni_last_centre=(2026, 7))


# ---------------------------------------------------------------------------
# D1 — the day is a fixed UTC−8 day (red: a local-time day with DST)
# ---------------------------------------------------------------------------

def test_d1_the_utc_minus_8_day_boundary():
    # 07:59Z is still the previous day; 08:00Z opens the next.
    assert season.load_day(_hour(2026, 1, 15, 7, 59)) == date(2026, 1, 14)
    assert season.load_day(_hour(2026, 1, 15, 8, 0)) == date(2026, 1, 15)
    # Summer is the same boundary: no daylight-saving shift. Pacific daylight
    # time would put 07:00Z on Jul 15 (00:00 PDT); the fixed day puts it on Jul 14.
    assert season.load_day(_hour(2026, 7, 15, 7, 59)) == date(2026, 7, 14)
    assert season.load_day(_hour(2026, 7, 15, 7, 0)) == date(2026, 7, 14)
    assert season.load_day(_hour(2026, 7, 15, 8, 0)) == date(2026, 7, 15)
    # The DST switch days are 24-hour days like every other.
    for d in (date(2026, 3, 8), date(2026, 11, 1)):
        hours = [{"ts": _hour(d.year, d.month, d.day, 8) + timedelta(hours=k), "value": 1.0}
                 for k in range(24)]
        assert season.load_days_from_hours(hours) == [{"obs_date": d, "peak": 1.0,
                                                       "hours": 24}]
    # A timestamp in another zone is read by its instant.
    pdt = datetime.timezone(timedelta(hours=-7))
    assert season.load_day(datetime.datetime(2026, 7, 15, 0, 30, tzinfo=pdt)) == date(2026, 7, 14)


def test_d1_the_grouped_read_says_utc_minus_8_in_sql():
    q = " ".join(season.LOAD_PEAK_SQL.split())
    assert "((ts AT TIME ZONE 'UTC') - interval '8 hours')::date AS obs_date" in q
    assert "max(value)::float8 AS peak" in q and "count(value)::int AS hours" in q
    assert "GROUP BY 1 ORDER BY 1" in q
    a = " ".join(season.AREAS_LOAD_SQL.split())
    assert "((ts AT TIME ZONE 'UTC') - interval '8 hours')::date AS d" in a
    for sql in (q, a):
        assert "America/" not in sql and "Los_Angeles" not in sql
    meta = _build(_history(2019, 2024))["season"]
    assert meta["day_rule"] == season.DAY_RULE and "no daylight-saving shift" in meta["day_rule"]
    assert "08:00Z" in meta["day_rule"] and ">= 20 of its 24 hours" in meta["day_rule"]


def test_d1_a_day_is_the_max_of_its_own_24_hours():
    # A spike at 07:00Z on Jul 15 is Jul 14's 24th hour, not Jul 15's first.
    hours = []
    for d in (date(2026, 7, 14), date(2026, 7, 15)):
        for k in range(24):
            ts = datetime.datetime(d.year, d.month, d.day, 8, tzinfo=UTC) + timedelta(hours=k)
            hours.append({"ts": ts, "value": 100.0 + k})
    hours[23]["value"] = 999.0                      # 2026-07-15T07:00Z
    rows = season.load_days_from_hours(hours)
    assert rows == [{"obs_date": date(2026, 7, 14), "peak": 999.0, "hours": 24},
                    {"obs_date": date(2026, 7, 15), "peak": 123.0, "hours": 24}]


# ---------------------------------------------------------------------------
# D2 — >= 20 of 24 hours, else null (red: a max over any number of hours)
# ---------------------------------------------------------------------------

def _day_hours(d, n, *, value=lambda k: 1000.0 + 10 * k, nulls=()):
    """The first `n` hours of UTC−8 day `d` (08:00Z on), plus NULL rows at the
    hour offsets in `nulls`."""
    start = datetime.datetime(d.year, d.month, d.day, 8, tzinfo=UTC)
    out = [{"ts": start + timedelta(hours=k), "value": value(k)} for k in range(n)]
    out += [{"ts": start + timedelta(hours=k), "value": None} for k in nulls]
    return out


def test_d2_nineteen_hours_is_null_and_twenty_carries_the_max():
    d19, d20, d24 = date(2026, 7, 1), date(2026, 7, 2), date(2026, 7, 3)
    rows = season.load_days_from_hours(_day_hours(d19, 19) + _day_hours(d20, 20)
                                       + _day_hours(d24, 24))
    assert [r["hours"] for r in rows] == [19, 20, 24]
    daily = season.peak_load_daily(rows)
    assert daily[d19] is None                    # present, not a peak (red: 1180.0)
    assert daily[d20] == 1190.0                  # the max of its 20 hours
    assert daily[d24] == 1230.0


def test_d2_a_null_hour_is_not_an_hour():
    d = date(2026, 7, 1)
    rows = season.load_days_from_hours(_day_hours(d, 19, nulls=(19, 20, 21, 22, 23)))
    assert rows == [{"obs_date": d, "peak": 1180.0, "hours": 19}]
    assert season.peak_load_daily(rows) == {d: None}
    # And from the SQL's own shape: a day of NULL rows only.
    assert season.peak_load_daily([{"obs_date": d, "peak": None, "hours": 0}]) == {d: None}


def test_d2_a_thin_day_is_a_gap_in_the_line_on_its_day_only():
    daily = _history(2018, 2024)
    thin = date(2023, 7, 20)
    daily[thin] = None                            # a morning-only day: a row, no peak
    p = _build(daily)
    wy = p["curves"]["WY2023"]
    assert wy[AX.index("07-20")] is None
    assert wy[AX.index("07-21")] == round(_summer(date(2023, 7, 21), 2022), 1)
    last = p["last_season"]
    assert last["season"] == "WY2025"
    by = {y["season"]: y for y in p["years"]}
    assert by["WY2023"]["complete"] is True       # 364/365 clears the 0.90 floor


# ---------------------------------------------------------------------------
# D3 — peak_load_7d: seven qualifying days, else null
# ---------------------------------------------------------------------------

def test_d3_trailing_seven_day_mean():
    a = date(2026, 1, 1)
    daily = {a + timedelta(days=k): float(100 + 10 * k) for k in range(12)}
    m = season.peak_load_7d(daily)
    assert list(m) == list(daily)
    for k in range(6):
        assert m[a + timedelta(days=k)] is None          # null before seven days
    assert m[a + timedelta(days=6)] == np.mean([100 + 10 * k for k in range(7)]) == 130.0
    assert m[a + timedelta(days=11)] == np.mean([100 + 10 * k for k in range(5, 12)])


def test_d3_a_null_or_absent_day_inside_the_window_makes_it_null():
    a = date(2026, 1, 1)
    daily = {a + timedelta(days=k): 100.0 for k in range(30)}
    daily[a + timedelta(days=10)] = None                 # a thin day
    del daily[a + timedelta(days=20)]                    # a day with no rows
    m = season.peak_load_7d(daily)
    for k in range(10, 17):
        assert m[a + timedelta(days=k)] is None, k
    assert m[a + timedelta(days=9)] == 100.0 and m[a + timedelta(days=17)] == 100.0
    assert a + timedelta(days=20) not in m               # absent stays absent
    for k in range(21, 27):
        assert m[a + timedelta(days=k)] is None, k
    assert m[a + timedelta(days=27)] == 100.0


def test_d3_seven_day_payload():
    daily = season.peak_load_7d(_history(2018, 2024))
    p = _build(daily, var="peak_load_7d")
    assert p["var"] == "peak_load_7d" and p["units"] == "MW"
    assert p["season"]["day_rule"] == season.DAY_RULE_7D
    first = p["years"][0]
    assert first["season"] == "WY2019" and first["complete"] is True    # 359/365
    wy19 = p["curves"]["WY2019"]
    assert wy19[:6] == [None] * 6
    want = np.mean([_summer(date(2018, 10, 1) + timedelta(days=k), 2018) for k in range(7)])
    assert wy19[6] == round(float(want), 1)
    wy20 = p["curves"]["WY2020"]                         # crosses the season boundary
    want = np.mean([_summer(date(2019, 9, 25) + timedelta(days=k), 2018) for k in range(6)]
                   + [_summer(date(2019, 10, 1), 2019)])
    assert wy20[0] == round(float(want), 1)


# ---------------------------------------------------------------------------
# D4 — seven seasons: no cone, a range, a five-year band, small_n outlooks
# ---------------------------------------------------------------------------

def test_d4_seven_seasons_short_record():
    daily = _history(2018, 2024)                         # WY2019..WY2025
    daily.update(_history(2025, 2025, frontier=date(2026, 9, 29)))
    p = _build(daily)
    assert tuple(p) == season.LEVEL_RESPONSE_KEYS
    assert p["units"] == "MW" and p["season"]["mode"] == "level"
    assert p["base"]["n"] == 7 and p["base"]["min_days_frac"] == 0.90
    assert p["base"]["seasons"] == [f"WY{y}" for y in range(2019, 2026)]
    assert p["percentiles"] is None
    assert p["percentiles_absence"] == {"reason": "short_record", "n": 7}
    assert p["normal"] is None
    assert p["range"]["n"] == 7 and p["range_absence"] is None
    assert p["five_year"]["seasons"] == [f"WY{y}" for y in range(2021, 2026)]
    assert p["five_year_absence"] is None
    ns = p["enso"]["categories"]["nino_strong"]
    assert ns["seasons"] == ["WY2024"] and ns["n"] == 1
    assert ns["outlook"] is None and ns["outlook_absence"] == {"reason": "small_n", "n": 1}
    assert ns["median"] is None and ns["absence"] == {"reason": "small_n", "n": 1}
    nina = p["enso"]["categories"]["nina"]               # 2020, 2021, 2022 -> WY2021..23
    assert nina["n"] == 3 and nina["outlook"]["n"] == 3
    assert p["enso"]["now"]["category"] == "nino_strong"
    assert p["this_season"]["season"] == "WY2026"
    assert p["this_season"]["through"] == "2026-09-29"
    assert len(p["curves"]) == 7
    json.dumps(p, allow_nan=False)


def test_d4_the_peak_is_the_max_of_the_daily_peaks_on_its_own_day():
    daily = _history(2019, 2024)
    p = _build(daily)
    for y in p["years"]:
        s = int(y["season"][2:]) - 1
        a, b = season.bounds("peak_load", s)
        best = max(_days(a, b), key=lambda d: (daily[d], -d.toordinal()))
        assert y["peak"] == round(daily[best], 1)
        assert y["peak_md"] == f"{best.month:02d}-{best.day:02d}"
        assert best.month in (6, 7, 8, 9)                # a summer peaker
    w = _build(_history(2019, 2024, _winter), area="ba:BPAT")
    assert all(y["peak_md"][:2] in ("12", "01", "02") for y in w["years"])


def test_d4_six_seasons_and_four():
    """Production on 2026-09-30: most areas have six qualifying past seasons
    (WY2020-WY2025); PSEI four (WY2020 has 92 qualifying days, WY2021 274)."""
    p = _build(_history(2019, 2024))
    assert p["base"]["n"] == 6 and p["range"]["n"] == 6 and p["five_year"] is not None
    daily = _history(2019, 2024)
    for s in (2019, 2020):
        a, b = season.bounds("peak_load", s)
        for d in list(_days(a, b))[100:]:
            del daily[d]
    p = _build(daily, area="ba:PSEI")
    assert p["base"]["n"] == 4 and [e["season"] for e in p["base"]["excluded"]] == [
        "WY2020", "WY2021"]
    assert p["range"] is None and p["range_absence"] == {"reason": "small_n", "n": 4}
    assert p["five_year"] is None
    assert p["five_year_absence"] == {"reason": "fewer_than_five", "n": 4}
    assert p["percentiles_absence"]["reason"] == "short_record"


# ---------------------------------------------------------------------------
# Routes: a stand-in pool for wecc_load_hourly + the ENSO bank
# ---------------------------------------------------------------------------

class _Cur:
    def __init__(self, pool):
        self.pool, self._rows = pool, []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, q, p=None):
        self.pool.statements.append((q, p))
        self._rows = self.pool.answer(q, p or {})

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


def _load_rows(first, last, frontier, value=_summer):
    """LOAD_PEAK_SQL rows for one area: 24 hours a day, but WY2023's Jul 20
    read 19 hours (a thin day) and the frontier day 22."""
    out = []
    for d, v in sorted(_history(first, last, value, frontier=frontier).items()):
        hours = 19 if d == date(2023, 7, 20) else 22 if d == frontier else 24
        out.append({"obs_date": d, "peak": v, "hours": hours})
    return out


class _LoadPool:
    def __init__(self):
        self.statements = []
        self.rows = {"CISO": _load_rows(2018, 2025, date(2026, 9, 29)),
                     "BPAT": _load_rows(2018, 2025, date(2026, 9, 29), _winter),
                     "WACM": _load_rows(2018, 2025, date(2026, 4, 1))}

    def connection(self):
        return _Conn(self)

    def answer(self, q, p):
        if q == season.LOAD_PEAK_SQL:
            assert p["d"] == "wecc_load_hourly"
            return self.rows[p["s"]]
        if q == main._enso.RUN_SQL:
            return [{"classifier": p["c"], "developing": DEVELOPING, "catalog_version": CUR_V,
                     "source": {}, "n_episodes": 0, "n_year_bins": 78,
                     "computed_at": datetime.datetime(2026, 9, 29, tzinfo=UTC)}]
        if q == main._enso.YEAR_BINS_SQL:
            return PROD_BINS
        if q == season.ONI_LAST_SQL:
            return [{"ts": datetime.datetime(2026, 7, 1, tzinfo=UTC)}]
        if q in (season.AREAS_PRECIP_SQL, season.AREAS_STATION_DD_SQL, season.AREAS_LWT_SQL,
                 season.AREAS_SNOW_SQL, season.AREAS_CA_SNOW_SQL, season.AREAS_RESERVOIR_SQL, season.AREAS_RESERVOIR_REGIONS_SQL):
            return []
        if q == season.AREAS_LOAD_SQL:
            assert p == {"d": "wecc_load_hourly", "s": list(season.BAS), "h": 20}
            out = []
            for b in season.BAS:
                if b == "PSEI":
                    ns = {2018: 253, 2019: 92, 2020: 274}
                else:
                    ns = {2018: 273}
                for s in range(2018, 2026):
                    n = ns.get(s, season.days_in_window("peak_load", s))
                    last = min(season.bounds("peak_load", s)[1], date(2026, 9, 29))
                    if s == 2025:
                        n, last = (183, date(2026, 4, 1)) if b == "WACM" else (364, last)
                    out.append({"id": b, "s": s, "n": n, "n7": max(n - 6, 0), "last": last})
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
    p = _LoadPool()
    monkeypatch.setattr(main, "_pool", p)
    monkeypatch.setattr(main, "_season_pool", p)     # d091542: the season routes' own pool
    return p


@pytest.fixture
def client(pool):
    return TestClient(main.app)


# ---------------------------------------------------------------------------
# D5 — /areas lists the 28 with both variables; an early-ending area its frontier
# ---------------------------------------------------------------------------

def test_d5_areas_lists_the_28_after_the_reservoirs(client, pool):
    r = client.get("/api/weather/season/areas")
    assert r.status_code == 200
    areas = r.json()["areas"]
    assert len(areas) == 61 + 28 == 89                # d091536: + col_canada before; d091542: + 3 regions
    assert areas[60]["area"] == "reservoir:san_luis"
    ba = areas[61:]
    assert [a["area"] for a in ba] == [f"ba:{b}" for b in season.BAS]
    assert [a["label"] for a in ba] == list(season.BAS) == sorted(season.BAS)
    assert {a["kind"] for a in ba} == {"ba"}
    for a in ba:
        assert [v["var"] for v in a["vars"]] == ["peak_load", "peak_load_7d"]
        assert all(v["units"] == "MW" and v["season"] == "water_year" for v in a["vars"])
    ciso = next(a for a in ba if a["area"] == "ba:CISO")
    assert ciso["frontier"] == "2026-09-29"
    # d091542: /areas leaves out the season in progress, by the payload's own
    # rule (the frontier 2026-09-29 is before Sep 30, so WY2026 is in progress):
    # WY2026 at 364/365 no longer qualifies here, as the payload's base excludes
    # it. Before d091542 this read 7.
    assert ciso["vars"][0] == {"var": "peak_load", "season": "water_year", "units": "MW",
                               "first_season": "WY2019", "complete_seasons": 6,
                               "qualifying_seasons": 6,
                               "data_type": "peak_load", "measure": "MW, daily peak"}
    wacm = next(a for a in ba if a["area"] == "ba:WACM")
    assert wacm["frontier"] == "2026-04-01"               # the series stopped early
    assert list(wacm) == ["area", "kind", "label", "frontier", "vars",
                          "region", "level", "state", "region_basis"]      # d091542 appended
    psei = next(a for a in ba if a["area"] == "ba:PSEI")
    assert psei["vars"][0]["qualifying_seasons"] == 4          # WY2022..WY2025 (d091542: WY2026 is in progress; was 5)
    assert r.headers["cache-control"] == "max-age=3600"


def test_d5_the_early_frontier_carries_into_the_payload(client, pool):
    body = client.get("/api/weather/season", params={"area": "ba:WACM", "var": "peak_load"}).json()
    assert body["frontier"] == "2026-04-01"
    assert body["this_season"]["season"] == "WY2026"
    assert body["this_season"]["through"] == "2026-04-01"
    assert body["readout"]["value"] is not None and body["readout"]["day"] == AX.index("04-01")


def test_d5_season_reads_and_refusals(client, pool):
    r = client.get("/api/weather/season", params={"area": "ba:CISO", "var": "peak_load"})
    assert r.status_code == 200 and r.headers["cache-control"] == "max-age=900"
    body = r.json()
    assert tuple(body) == season.LEVEL_RESPONSE_KEYS + season.ROUTE_ADDED_KEYS    # d091542
    assert body["units"] == "MW" and body["frontier"] == "2026-09-29"
    assert body["season"]["day_rule"] == season.DAY_RULE
    assert body["source"] == {"dataset": "wecc_load_hourly",
                              "method": season.METHODS[("ba", "peak_load")],
                              "series": "CISO", "min_hours": 20}
    assert body["base"]["n"] == 7                         # WY2019..WY2025 in the stand-in
    assert body["curves"]["WY2023"][AX.index("07-20")] is None      # 19 hours
    assert body["this_season"]["values"][AX.index("09-29")] is not None   # 22 hours
    assert body["percentiles_absence"] == {"reason": "short_record", "n": 7}
    q, p = [s for s in pool.statements if s[0] == season.LOAD_PEAK_SQL][-1]
    assert p == {"d": "wecc_load_hourly", "s": "CISO"}
    json.dumps(body, allow_nan=False)

    r = client.get("/api/weather/season", params={"area": "ba:BPAT", "var": "peak_load_7d"})
    body = r.json()
    assert r.status_code == 200 and body["var"] == "peak_load_7d"
    assert body["season"]["day_rule"] == season.DAY_RULE_7D
    raw = season.peak_load_daily(pool.rows["BPAT"])
    d = date(2025, 1, 10)
    want = np.mean([raw[d - timedelta(days=k)] for k in range(7)])
    assert body["curves"]["WY2025"][AX.index("01-10")] == round(float(want), 1)

    get = lambda **q: client.get("/api/weather/season", params=q)
    r = get(area="ba:CISO", var="swe")
    assert r.status_code == 400
    assert "ba: areas carry peak_load, peak_load_7d" in r.json()["detail"]
    r = get(area="ba:ERCO", var="peak_load")
    assert r.status_code == 400 and "ba:{code} for AVA, AZPS" in r.json()["detail"]
    r = get(area="lwt:BPAT", var="peak_load")
    assert r.status_code == 400 and "not served for lwt:BPAT" in r.json()["detail"]
    r = get(area="ba:CISO", var="load")
    assert r.status_code == 400 and "peak_load, peak_load_7d" in r.json()["detail"]


# ---------------------------------------------------------------------------
# D6 — the rest of the suite; here, the load vars leave every other body alone
# ---------------------------------------------------------------------------

def test_d6_day_rule_rides_on_the_load_vars_only():
    for v in ("precip", "hdd", "cdd", "swe", "storage", "swe_in"):
        assert "day_rule" not in season.season_meta(v)
    assert list(season.season_meta("peak_load"))[-1] == "day_rule"


# ---------------------------------------------------------------------------
# The handback's sizes
# ---------------------------------------------------------------------------

def test_load_payload_sizes_for_the_handback(capsys):
    """ba:CISO, peak_load and peak_load_7d, shaped like production on
    2026-09-30 (WY2019 partial from 2018-12-31, through 2026-09-29)."""
    daily = {d: v for d, v in _history(2018, 2025, frontier=date(2026, 9, 29)).items()
             if d >= date(2018, 12, 31)}
    sizes = {"ba:CISO peak_load": len(json.dumps(_build(daily)).encode()),
             "ba:CISO peak_load_7d": len(json.dumps(_build(season.peak_load_7d(daily),
                                                           var="peak_load_7d")).encode())}
    with capsys.disabled():
        print(f"\n[load sizes] {sizes}")
    assert all(v < 2_000_000 for v in sizes.values())
