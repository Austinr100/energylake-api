"""
d091542 — GET /api/weather/season/snapshot: one row per area carrying a data
type on one season day, every number read off the /season build for that
(area, var, classifier). S1..S5 are the spec's §2.8 table; each test's name
carries its id. The stand-in pool is the house pattern; the numbers are built
here, by calendar date, so the test can do the arithmetic by hand.
"""

import datetime
from datetime import date, timedelta

import numpy as np
import pytest
from fastapi.testclient import TestClient

import main
import season

UTC = datetime.timezone.utc
DEVELOPING = {"kind": "nino", "n_seasons": 4, "first_year": 2026, "latest_oni": 1.8,
              "latest_year": 2026, "first_season": "MAM", "latest_season": "JJA"}
NINO = (1986, 1991, 1994, 1997, 2002, 2004, 2009, 2014, 2015, 2018, 2023)
SMALL = (1987,)                                   # nino_strong: one season, small n
FRONTIER = date(2026, 3, 5)                       # the Columbia's newest day
CA_FRONTIER = date(2026, 3, 4)
AX = season.axis("swe")


def _days(a: date, b: date):
    d = a
    while d <= b:
        yield d
        d += timedelta(days=1)


def _shape(d: date, s: int) -> float:
    """A snowpack: 0 on Oct 1, up to 100 by Apr 1, down by Jul."""
    doy = (d - date(s, 10, 1)).days
    return max(0.0, 100.0 * np.sin(np.pi * min(doy, 300) / 300.0))


def _k(s: int) -> float:
    return 0.5 + (s * 7 % 11) / 10.0


def _swe(d: date, s: int) -> float:
    return round(_shape(d, s) * _k(s), 3)


def _bins():
    rows = []
    for y in range(1949, 2027):
        kind = "nino" if y in NINO + SMALL else "neutral"
        rows.append({"enso_year": y, "kind": kind,
                     "strength": "strong" if y in SMALL else ("weak" if kind == "nino" else None),
                     "flavor": None})
    return rows


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


class _Pool:
    """Columbia snow: WY1985..WY2025 whole, WY2026 to Mar 5 (41 seasons: the
    cone). California swe_in: WY2007..WY2025 inside Dec 1 – May 31 (19: the
    range, no cone), WY2026 to Mar 4, and a hole on 2026-02-10."""

    def __init__(self):
        self.statements = []
        self.snow = {}
        for s in range(1984, 2026):
            for d in _days(date(s, 10, 1), min(date(s + 1, 9, 30), FRONTIER)):
                self.snow[d] = _swe(d, s)
        self.ca = {}
        for s in range(2006, 2026):
            a, b = season.count_window("swe_in", s)
            for d in _days(a, min(b, CA_FRONTIER)):
                self.ca[d] = round(_swe(d, s) / 4.0, 3)
        del self.ca[date(2026, 2, 10)]

    def connection(self):
        return _Conn(self)

    def answer(self, q, p):
        if q == season.SNOW_SQL:
            last = max(self.snow)
            return [{"obs_date": d, "v": v,
                     "meta": {"n_index": 40, "n_reporting": 40, "normals_version": "v1"}
                     if d == last else None} for d, v in sorted(self.snow.items())]
        if q == season.LWT_SQL and p["d"] == "cdec_snowpack_swe_daily":
            return [{"obs_date": d, "v": v} for d, v in sorted(self.ca.items())]
        if q == main._enso.RUN_SQL:
            return [{"classifier": p["c"], "developing": DEVELOPING, "catalog_version": "v"}]
        if q == main._enso.YEAR_BINS_SQL:
            return _bins()
        if q == season.ONI_LAST_SQL:
            return [{"ts": datetime.datetime(2026, 7, 1, tzinfo=UTC)}]
        raise AssertionError(f"unexpected statement: {q[:60]}")


@pytest.fixture(autouse=True)
def _clear_memos():
    for c in (main._season_cache, main._season_areas_cache, main._season_snapshot_cache):
        c.clear()
    main._season_inflight.clear()
    yield
    for c in (main._season_cache, main._season_areas_cache, main._season_snapshot_cache):
        c.clear()
    main._season_inflight.clear()


@pytest.fixture
def pool(monkeypatch):
    p = _Pool()
    monkeypatch.setattr(main, "_pool", None)
    monkeypatch.setattr(main, "_season_pool", p)
    return p


@pytest.fixture
def client(pool):
    return TestClient(main.app)


def _snap(client, **q):
    r = client.get("/api/weather/season/snapshot",
                   params={"data_type": "snow_water_equivalent", **q})
    assert r.status_code == 200, r.text
    return r.json()


def _row(body, area):
    return next(r for r in body["rows"] if r["area"] == area)


# ---------------------------------------------------------------------------
# S1 — every field is the same field off the /season payload
# ---------------------------------------------------------------------------

def test_s1_row_equals_the_season_payload(client):
    body = _snap(client)
    assert body["data_type"] == "snow_water_equivalent" and body["n_areas"] == 11
    assert [r["area"] for r in body["rows"]] == (
        [f"snow:{b}" for b in season.SNOW_BASINS + season.CANADA_SNOW]
        + [f"snow:{r}" for r in season.CA_SNOW])
    for row in body["rows"]:
        assert tuple(row) == season.SNAPSHOT_KEYS
        var = "swe_in" if row["area"].startswith("snow:ca_") else "swe"
        p = client.get("/api/weather/season", params={"area": row["area"], "var": var}).json()
        r = p["readout"]
        assert row["var"] == var and row["units"] == p["units"]
        assert row["date"] == p["frontier"] == p["this_season"]["through"]
        assert row["season"] == p["this_season"]["season"] == "WY2026"
        assert row["season_day"] == r["day"]
        assert row["value"] == r["value"]
        assert row["pct_of_median_peak"] == r["pct_of_median_peak"]
        assert row["median_peak"] == r["median_peak"] == p["peak"]["base"]["median"]
        assert row["median_peak_md"] == p["peak"]["base"]["median_md"]
        assert row["n"] == p["base"]["n"] and row["frontier"] == p["frontier"]
        if p["percentiles"] is not None:      # the cone: the readout's own numbers
            assert row["median"] == r["median"] == p["percentiles"]["p50"][r["day"]]
            assert row["pct_of_median"] == r["pct_of_median"]
            assert row["percentile"] == r["percentile"]
            assert row["median_basis"].startswith("the cone's p50")
        else:                                 # a short base: the range's median
            # d091550 §2.3: the readout reads the same range median now
            assert row["median"] == r["median"] == p["range"]["median"][r["day"]]
            assert row["pct_of_median"] == r["pct_of_median"]
            assert row["percentile"] is None and r["percentile"] is None
            assert abs(row["pct_of_median"] - 100.0 * row["value"] / row["median"]) < 0.5
            assert row["median_basis"].startswith("the base's range median")
        assert row["median_basis"] == r["median_basis"]
        assert row["absence"] is None
    dalles = _row(body, "snow:columbia_above_the_dalles")
    assert (dalles["region"], dalles["level"], dalles["measure"]) == (
        "pacific_northwest", "aggregate", "percent of normal peak")
    ca = _row(body, "snow:ca_central")
    assert (ca["region"], ca["level"], ca["measure"], ca["units"]) == (
        "california", "region", "inches", "in")
    assert ca["geo"] == {"lat": 38.5, "lon": -120.2}
    assert ca["n"] == 19


def test_s1_readout_gained_two_keys_on_a_level_only(client):
    # d091550 §2.3 appended a third, `median_basis`.
    p = client.get("/api/weather/season", params={"area": "snow:snake", "var": "swe"}).json()
    keys = list(p["readout"])
    assert keys[-3:] == list(season.READOUT_ADDED_KEYS)
    assert keys[:-3] == ["day", "value", "median", "pct_of_median", "percentile",
                         "vs_five_year", "vs_category"]
    peak = p["peak"]["base"]["median"]
    assert p["readout"]["pct_of_median_peak"] == round(100.0 * p["readout"]["value"] / peak, 1)


# ---------------------------------------------------------------------------
# S2 — the default date is each area's own frontier; an earlier date answers
#      on that season's curve
# ---------------------------------------------------------------------------

def test_s2_default_frontier_and_earlier_season(client):
    body = _snap(client)
    assert body["date"] is None and body["date_rule"] == "each area's own frontier"
    assert _row(body, "snow:snake")["date"] == "2026-03-05"
    assert _row(body, "snow:ca_north")["date"] == "2026-03-04"      # its own frontier

    old = _snap(client, date="2023-04-01")
    row = _row(old, "snow:snake")
    assert (row["date"], row["season"], row["season_day"]) == ("2023-04-01", "WY2023",
                                                               AX.index("04-01"))
    assert row["value"] == round(_swe(date(2023, 4, 1), 2022), 1)
    p = client.get("/api/weather/season", params={"area": "snow:snake", "var": "swe"}).json()
    assert row["value"] == p["curves"]["WY2023"][AX.index("04-01")]
    assert row["median"] == p["percentiles"]["p50"][AX.index("04-01")]      # the same base
    base = [_swe(date(s + 1, 4, 1), s) for s in range(1984, 2025)
            if season.label("swe", s) in p["base"]["seasons"]]
    assert row["percentile"] == round(season.mid_rank(_swe(date(2023, 4, 1), 2022), base), 1)
    # California on the same day of WY2023: inside its window, on its own curve
    ca = _row(old, "snow:ca_state")
    assert ca["value"] == round(_swe(date(2023, 4, 1), 2022) / 4.0, 1)


# ---------------------------------------------------------------------------
# S3 — cat: the category's median on the day against the all-years median
# ---------------------------------------------------------------------------

def test_s3_category_against_all_years_by_hand(client):
    day = date(2026, 3, 5)
    body = _snap(client, cat="nino")
    row = _row(body, "snow:snake")
    # By hand: the base is WY1985..WY2025 (season starts 1984..2024); nino
    # seasons are those whose ENSO year (the start year) is a nino bin.
    base = list(range(1984, 2025))
    vals = {s: _swe(date(s + 1, 3, 5), s) for s in base}
    nino = [s for s in base if s in NINO + SMALL]
    cat_med = float(np.median([vals[s] for s in nino]))
    all_med = float(np.percentile([vals[s] for s in base], 50, method="linear"))
    assert row["category"] == {"cat": "nino", "n": len(nino), "median": round(cat_med, 1),
                               "pct_of_all_years_median": round(100.0 * cat_med / all_med, 1)}
    assert row["median"] == round(all_med, 1) and row["category_absence"] is None
    assert day.isoformat() == row["date"]

    short = _row(_snap(client, cat="nino_strong"), "snow:snake")
    assert short["category"] is None
    assert short["category_absence"] == {"cat": "nino_strong", "reason": "small_n", "n": 1}
    assert short["value"] is not None                     # the row itself still stands


# ---------------------------------------------------------------------------
# S4 — absence rows, never a zero
# ---------------------------------------------------------------------------

def test_s4_absence_rows(client):
    hole = _snap(client, date="2026-02-10")
    ca = _row(hole, "snow:ca_south")
    assert ca["value"] is None and ca["median"] is None and ca["percentile"] is None
    assert ca["absence"] == "no value on this day"
    assert ca["frontier"] == "2026-03-04" and ca["date"] == "2026-02-10"
    assert _row(hole, "snow:snake")["value"] is not None

    summer = _row(_snap(client, date="2025-08-01"), "snow:ca_state")
    assert summer["value"] is None
    assert summer["absence"].startswith("no value on this day: outside the series' "
                                        "reporting window (12-01 – 05-31)")

    later = _row(_snap(client, date="2026-04-01"), "snow:snake")     # after the frontier
    assert later["value"] is None and later["absence"] == "no value on this day"

    before = _row(_snap(client, date="1950-01-01"), "snow:snake")    # before the record
    assert before["value"] is None and before["absence"] == "no value on this day"


def test_s4_a_short_base_says_so():
    """Fewer than five qualifying seasons: no median (neither the cone nor the
    range), and the row says why; the value still stands."""
    daily = {}
    for s in range(2021, 2026):
        a, b = season.count_window("swe_in", s)
        for d in _days(a, b if s < 2025 else date(2026, 3, 1)):
            daily[d] = 3.0
    extras = {}
    p = season.build_season("snow:ca_state", "swe_in", daily, classifier="cpc_oni",
                            catalog_version="v", developing=DEVELOPING, bins=_bins(),
                            oni_last_centre=(2026, 7), extras=extras)
    row = season.snapshot_row("snow:ca_state", "swe_in", p, extras, on=None, cat=None,
                              area_label="California statewide",
                              place=season.area_place("snow:ca_state"), geo=None)
    assert row["value"] == 3.0 and row["median"] is None and row["n"] == 4
    assert row["absence"] == ("short base: n = 4, no median on this day (the cone needs "
                              "n >= 30, a level's range n >= 5)")


# ---------------------------------------------------------------------------
# S5 — 400s; ETag stable, changes with a value; 304
# ---------------------------------------------------------------------------

def test_s5_refusals(client):
    get = lambda **q: client.get("/api/weather/season/snapshot", params=q)
    r = get()
    assert r.status_code == 400 and "unknown data_type None; allowed: precipitation, " \
        "snow_water_equivalent, reservoir_storage" in r.json()["detail"]
    r = get(data_type="snowpack")
    assert r.status_code == 400 and "allowed:" in r.json()["detail"]
    r = get(data_type="snow_water_equivalent", date="2026-3-5")
    assert r.status_code == 400 and r.json()["detail"] == \
        "malformed date '2026-3-5'; allowed: YYYY-MM-DD"
    r = get(data_type="snow_water_equivalent", cat="elnino")
    assert r.status_code == 400 and "nino, nina, neutral" in r.json()["detail"]
    r = get(data_type="snow_water_equivalent", classifier="mei")
    assert r.status_code == 400 and "cpc_oni, roni" in r.json()["detail"]


def test_s5_etag_and_304(client, pool):
    r1 = client.get("/api/weather/season/snapshot",
                    params={"data_type": "snow_water_equivalent"})
    tag = r1.headers["etag"]
    assert tag.startswith('W/"') and r1.headers["cache-control"] == "max-age=900"
    r2 = client.get("/api/weather/season/snapshot",
                    params={"data_type": "snow_water_equivalent"})
    assert r2.headers["etag"] == tag
    r3 = client.get("/api/weather/season/snapshot",
                    params={"data_type": "snow_water_equivalent"},
                    headers={"If-None-Match": tag})
    assert r3.status_code == 304 and r3.content == b"" and r3.headers["etag"] == tag

    # a value changes -> a new tag
    pool.snow[FRONTIER] += 5.0
    for c in (main._season_cache, main._season_snapshot_cache):
        c.clear()
    r4 = client.get("/api/weather/season/snapshot",
                    params={"data_type": "snow_water_equivalent"},
                    headers={"If-None-Match": tag})
    assert r4.status_code == 200 and r4.headers["etag"] != tag


def test_s5_snapshot_reads_nothing_of_its_own(client, pool):
    """D-09-25-72: the snapshot is built from the /season memo. Warm the eleven
    payloads, then ask: not one statement reaches the pool."""
    for area, var in season.snapshot_areas("snow_water_equivalent", main._WEATHER_STATIONS):
        client.get("/api/weather/season", params={"area": area, "var": var})
    n = len(pool.statements)
    _snap(client)
    _snap(client, date="2024-04-01", cat="nino")
    assert len(pool.statements) == n
