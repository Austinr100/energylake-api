"""
Tests for snowpack in the season API (d091520): `swe` on `snow:{basin}` areas,
a LEVEL, in season.py (pure) and the routes in main.py against an in-memory
stand-in for `snow_basin_index_daily` + the ENSO bank.

N1..N11 are the spec's §2 table (energylake-pantry
docs/cc_spec_2026_09_30_season_snow_api.md); each test's name carries its
number. N12 is tests/test_season.py, unchanged but for the appended keys and
the areas count. Fixtures are built here, by calendar date.
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
SLOTS = season._slot_index("swe")
AX = season.axis("swe")


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

def _days(a: date, b: date):
    d = a
    while d <= b:
        yield d
        d += timedelta(days=1)


def _fill(daily, s, value, *, through=None):
    a, b = season.bounds("swe", s)
    for d in _days(a, min(b, through) if through else b):
        daily[d] = float(value(d, s))


def _history(first, last, value=lambda d, s: 1.0, *, frontier=None):
    daily = {}
    for s in range(first, last + 1):
        _fill(daily, s, value, through=frontier)
    return daily


def _snowish(d, s):
    """A toy level: 0 through Nov, a ramp to a mid-March peak, melt to 0 by Jul.
    Seasons differ in size; a few days of the ramp are flat (ties)."""
    doy = (d - date(s, 10, 1)).days
    scale = 60.0 + (s * 37 % 90)
    if doy < 60 or doy > 280:
        return 0.0
    if doy <= 165:
        return round(scale * (doy - 60) / 105.0, 2)
    return round(scale * max(0.0, (280 - doy) / 115.0), 2)


def _bins(first=1949, last=2026):
    return [{"enso_year": y, "kind": "neutral", "strength": None, "flavor": None}
            for y in range(first, last + 1)]


def _build(daily, var="swe", area="snow:snake", meta=None):
    return season.build_season(area, var, daily, classifier="cpc_oni",
                               catalog_version=CUR_V, developing=DEVELOPING,
                               bins=_bins(), oni_last_centre=(2026, 7),
                               source_meta=meta)


def _walk(s, daily, limit=None):
    return season._walk("swe", s, daily, limit or season.bounds("swe", s)[1], SLOTS)


# ---------------------------------------------------------------------------
# N1 — the level walk
# ---------------------------------------------------------------------------

def test_n1_level_values_are_the_days_own_and_a_gap_is_one_day():
    daily = {}
    _fill(daily, 2020, lambda d, s: d.day)                 # WY2021
    del daily[date(2021, 1, 15)]
    w = _walk(2020, daily)
    i14, i15, i16 = (AX.index(md) for md in ("01-14", "01-15", "01-16"))
    assert w.values[i14] == 14.0                           # its own value, not a sum
    assert w.values[i15] is None                           # null on that day only
    assert w.values[i16] == 16.0                           # the next day still stands
    assert w.values[-1] == 30.0                            # Sep 30's own value
    assert w.first_missing == date(2021, 1, 15) and w.days_missing == 1
    assert w.through == date(2021, 9, 30)
    assert not season._complete(w)

    p = _build(daily)
    assert p["base"]["n"] == 0
    assert p["base"]["excluded"] == [{"season": "WY2021", "days_complete": 364,
                                      "days_in_window": 365,
                                      "first_missing": "2021-01-15"}]


def test_n1_this_season_gap_keeps_later_days_and_the_readout():
    daily = _history(1990, 2025, _snowish, frontier=date(2026, 4, 1))
    del daily[date(2026, 2, 10)]
    del daily[date(2026, 2, 11)]
    p = _build(daily)
    ts = p["this_season"]
    assert ts["season"] == "WY2026" and ts["through"] == "2026-04-01"
    assert ts["complete_to_date"] is False
    assert ts["absence"] == {"reason": "gap", "first_missing": "2026-02-10",
                             "mode": "absent", "days_complete": 181, "days_missing": 2}
    assert ts["values"][AX.index("02-10")] is None
    assert ts["values"][AX.index("02-12")] == round(_snowish(date(2026, 2, 12), 2025), 1)
    assert p["readout"] is not None and p["readout_absence"] is None
    assert p["readout"]["value"] == round(_snowish(date(2026, 4, 1), 2025), 1)


# ---------------------------------------------------------------------------
# N2 — Feb 29 is on no slot and is added to nothing; it still counts
# ---------------------------------------------------------------------------

def test_n2_leap_day_is_not_added_into_feb_28():
    daily = {}
    _fill(daily, 2023, lambda d, s: 100.0 + d.day)          # WY2024 holds 2024-02-29
    w = _walk(2023, daily)
    assert len(AX) == 365 and "02-29" not in AX
    assert w.values[AX.index("02-28")] == 128.0             # not 128 + 129
    assert w.values[AX.index("03-01")] == 101.0
    assert w.days_in_window == 366 and w.days_complete == 366
    assert season._complete(w)

    del daily[date(2024, 2, 29)]
    w = _walk(2023, daily)
    assert w.values[AX.index("02-28")] == 128.0             # Feb 28 is not withdrawn
    assert w.first_missing == date(2024, 2, 29)
    assert w.days_complete == 365 and not season._complete(w)
    p = _build(daily)
    assert p["base"]["excluded"][0]["season"] == "WY2024"
    assert p["season"]["mode"] == "level"
    assert "added to nothing" in p["season"]["leap_rule"]
    assert "366 valued days" in p["season"]["leap_rule"]


# ---------------------------------------------------------------------------
# N3 — cone, normal, five-year band on a level base
# ---------------------------------------------------------------------------

def test_n3_statistics_equal_numpy_on_the_raw_values():
    rng = np.random.default_rng(20)
    noise = {s: rng.gamma(2.0, 20.0, size=366) for s in range(1984, 2026)}
    value = lambda d, s: round(float(noise[s][(d - date(s, 10, 1)).days]), 2)
    daily = _history(1984, 2025, value, frontier=date(2026, 3, 1))
    p = _build(daily)
    base = list(range(1984, 2025))                          # WY1985..WY2025, 41
    assert p["base"]["n"] == 41
    B = np.array([_walk(s, daily).values for s in base], dtype=np.float64)
    assert not np.isnan(B).any()
    r1 = season._rv                                         # the edge's one rounding
    for q in season.PERCENTILES:
        want = r1(np.percentile(B, q, axis=0, method="linear"))
        assert p["percentiles"][f"p{q}"] == want
    normal = [i for i, s in enumerate(base) if 1991 <= s + 1 <= 2020]
    assert p["normal"]["n"] == 30
    assert p["normal"]["values"] == r1(B[normal].mean(axis=0))
    F = B[-5:]
    assert p["five_year"]["seasons"] == ["WY2021", "WY2022", "WY2023", "WY2024", "WY2025"]
    assert p["five_year"]["mean"] == r1(F.mean(axis=0))
    assert p["five_year"]["min"] == r1(F.min(axis=0))
    assert p["five_year"]["max"] == r1(F.max(axis=0))
    # The WY2024 leap day is on no slot: the base row is Feb 28's own value.
    i28 = AX.index("02-28")
    assert B[base.index(2023), i28] == value(date(2024, 2, 28), 2023)


# ---------------------------------------------------------------------------
# N4 — the lone first day
# ---------------------------------------------------------------------------

def test_n4_a_lone_first_row_is_excluded_with_its_counts():
    daily = _history(1983, 2025, _snowish, frontier=date(2026, 9, 28))
    daily[date(1982, 9, 30)] = 3.1                          # WY1982, one row
    p = _build(daily)
    assert "WY1982" not in p["base"]["seasons"]
    assert p["base"]["excluded"] == [{"season": "WY1982", "days_complete": 1,
                                      "days_in_window": 365,
                                      "first_missing": "1981-10-01"}]
    assert p["base"]["n"] == 42
    wy82 = next(y for y in p["years"] if y["season"] == "WY1982")
    assert wy82["complete"] is False and wy82["peak"] is None


# ---------------------------------------------------------------------------
# N5 — a zero median
# ---------------------------------------------------------------------------

def test_n5_zero_median_has_no_pct_but_a_percentile():
    daily = _history(1990, 2025, _snowish, frontier=date(2026, 9, 28))
    daily[date(2026, 9, 28)] = 0.3                          # an early flake
    p = _build(daily)
    r = p["readout"]
    assert r["day"] == AX.index("09-28")
    assert r["value"] == 0.3 and r["median"] == 0.0
    assert r["pct_of_median"] is None
    assert r["percentile"] == 100.0                         # above every base season
    assert p["percentiles"]["p50"][r["day"]] == 0.0


# ---------------------------------------------------------------------------
# N6 — the peak
# ---------------------------------------------------------------------------

def test_n6_peak_first_occurrence_and_base_median_day():
    daily = {}
    peak_days = {}
    for i, s in enumerate(range(1990, 2025)):               # 35 complete seasons
        pd = date(s + 1, 3, 1) + timedelta(days=i)          # peaks Mar 1, Mar 2, …
        peak_days[s] = pd
        _fill(daily, s, lambda d, s, pd=pd: 100.0 + s if d == pd else 10.0)
    _fill(daily, 2025, lambda d, s: 10.0, through=date(2026, 4, 1))
    daily[date(2026, 3, 10)] = 70.0                         # a tie: the first wins
    daily[date(2026, 3, 20)] = 70.0
    p = _build(daily)
    pk = p["peak"]
    assert p["peak_absence"] is None
    assert pk["this_season"] == {"value": 70.0, "date": "2026-03-10"}
    assert pk["last_season"] == {"value": 2124.0, "date": peak_days[2024].isoformat()}
    idx = sorted(SLOTS[f"{d.month:02d}-{d.day:02d}"] for d in peak_days.values())
    assert pk["base"]["n"] == 35
    assert pk["base"]["median_md"] == AX[int(np.median(idx))] == "03-18"
    vals = np.array([100.0 + s for s in range(1990, 2025)])
    assert pk["base"]["median"] == round(float(np.median(vals)), 1)
    assert pk["base"]["p10"] == round(float(np.percentile(vals, 10)), 1)
    assert pk["base"]["p90"] == round(float(np.percentile(vals, 90)), 1)
    wy26 = next(y for y in p["years"] if y["season"] == "WY2026")
    assert wy26["peak"] == 70.0 and wy26["peak_md"] == "03-10"


def test_n6_cumulative_variables_carry_no_peak():
    daily = {}
    a, b = season.bounds("precip", 2020)
    for d in _days(a, b):
        daily[d] = 1.0
    p = _build(daily, var="precip", area="station:USW00023232")
    assert p["peak"] is None and p["peak_absence"] == {"reason": "not_a_level"}
    assert p["season"]["mode"] == "cumulative"


# ---------------------------------------------------------------------------
# N7 — years[]: final vs peak
# ---------------------------------------------------------------------------

def test_n7_years_final_null_and_peak_for_swe_reverse_for_precip():
    p = _build(_history(2015, 2025, _snowish, frontier=date(2026, 4, 1)))
    wy25 = next(y for y in p["years"] if y["season"] == "WY2025")
    assert wy25["complete"] is True and wy25["final"] is None
    assert wy25["peak"] == round(max(_snowish(d, 2024) for d in _days(
        date(2024, 10, 1), date(2025, 9, 30))), 1)
    assert wy25["peak_md"] == "03-15"
    assert list(wy25)[-2:] == ["peak", "peak_md"]

    daily = {}
    for s in range(2015, 2026):
        a, b = season.bounds("precip", s)
        for d in _days(a, min(b, date(2026, 4, 1))):
            daily[d] = 1.0
    q = _build(daily, var="precip", area="station:USW00023232")
    wy25 = next(y for y in q["years"] if y["season"] == "WY2025")
    assert wy25["final"] == 365.0
    assert wy25["peak"] is None and wy25["peak_md"] is None


# ---------------------------------------------------------------------------
# Route stand-in
# ---------------------------------------------------------------------------

_META = {"unit": "pct_of_normal_peak", "n_index": 163, "n_reporting": 155,
         "normals_sha256": "2f85c863", "source_dataset": "snotel_columbia_daily",
         "normals_version": "v1"}
N_INDEX = {"columbia_above_the_dalles": 163, "col_above_grand_coulee": 44,
           "col_mid_tributaries": 34, "snake": 85, "snake_upper": 61, "snake_lower": 24}


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


class _SnowPool:
    def __init__(self):
        self.statements = []
        self.snow = {b: _history(1983, 2025, _snowish, frontier=date(2026, 9, 28))
                     for b in season.SNOW_BASINS}

    def connection(self):
        return _Conn(self)

    def answer(self, q, p):
        if q == season.SNOW_SQL:
            assert p["d"] == "snow_basin_index_daily"
            b = p["s"].removesuffix(".SWE_PCT")
            rows = [{"obs_date": d, "v": v, "meta": None} for d, v in sorted(self.snow[b].items())]
            rows[-1]["meta"] = {**_META, "basin": b, "n_index": N_INDEX[b],
                                "n_reporting": N_INDEX[b] - 1}
            return rows
        if q == main._enso.RUN_SQL:
            return [{"classifier": p["c"], "developing": DEVELOPING, "catalog_version": CUR_V,
                     "source": {}, "n_episodes": 0, "n_year_bins": 78,
                     "computed_at": datetime.datetime(2026, 9, 24, tzinfo=UTC)}]
        if q == main._enso.YEAR_BINS_SQL:
            return _bins()
        if q == season.ONI_LAST_SQL:
            return [{"ts": datetime.datetime(2026, 7, 1, tzinfo=UTC)}]
        if q in (season.AREAS_PRECIP_SQL, season.AREAS_STATION_DD_SQL, season.AREAS_LWT_SQL):
            return []
        if q == season.AREAS_SNOW_SQL:
            assert p["d"] == "snow_basin_index_daily"
            out = [{"id": b, "s": 1982, "n": 1} for b in season.SNOW_BASINS]
            out += [{"id": b, "s": s, "n": season.days_in_window("swe", s)}
                    for b in season.SNOW_BASINS for s in range(1983, 2025)]
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
    p = _SnowPool()
    monkeypatch.setattr(main, "_pool", p)
    return p


@pytest.fixture
def client(pool):
    return TestClient(main.app)


# ---------------------------------------------------------------------------
# N8 — refusals name the vocabulary
# ---------------------------------------------------------------------------

def test_n8_refusals(client):
    get = lambda **q: client.get("/api/weather/season", params=q)
    r = get(area="station:USW00023232", var="swe")
    assert r.status_code == 400 and "snow: areas carry swe" in r.json()["detail"]
    assert "station: areas carry precip, hdd, cdd" in r.json()["detail"]
    r = get(area="lwt:BPAT", var="swe")
    assert r.status_code == 400 and "snow: areas carry swe" in r.json()["detail"]
    r = get(area="snow:snake", var="precip")
    assert r.status_code == 400 and "snow: areas carry swe" in r.json()["detail"]
    r = get(area="snow:yakima", var="swe")
    assert r.status_code == 400
    assert "snow:{basin} for " + ", ".join(season.SNOW_BASINS) in r.json()["detail"]
    r = get(area="snow:snake", var="snow")
    assert r.status_code == 400 and "precip, hdd, cdd, swe" in r.json()["detail"]


# ---------------------------------------------------------------------------
# N9 — route smoke: /areas and the swe payload's key order
# ---------------------------------------------------------------------------

LABELS = [  # pantry snow/basins.py LABELS, verbatim, in §1's order
    ("snow:columbia_above_the_dalles", "Columbia above The Dalles (US)"),
    ("snow:col_above_grand_coulee", "Columbia above Grand Coulee (US)"),
    ("snow:col_mid_tributaries", "Mid-Columbia tributaries (Grand Coulee → The Dalles)"),
    ("snow:snake", "Snake"),
    ("snow:snake_upper", "Upper Snake (above Hells Canyon)"),
    ("snow:snake_lower", "Lower Snake (Salmon, Clearwater, Grande Ronde)"),
]

OLD_KEYS = ("area", "var", "units", "season", "frontier", "axis", "base",
            "percentiles", "percentiles_absence", "normal", "normal_absence",
            "five_year", "five_year_absence", "this_season", "this_season_absence",
            "last_season", "last_season_absence", "enso", "years", "curves",
            "readout", "readout_absence")


def test_n9_areas_and_swe_key_order(client, pool):
    areas = client.get("/api/weather/season/areas").json()["areas"]
    assert len(areas) == 44
    snow = [a for a in areas if a["kind"] == "snow"]
    assert areas[38:] == snow
    assert [(a["area"], a["label"]) for a in snow] == LABELS
    assert snow[0]["vars"] == [{"var": "swe", "season": "water_year",
                                "units": "% of normal peak", "first_season": "WY1983",
                                "complete_seasons": 42}]

    r = client.get("/api/weather/season",
                   params={"area": "snow:columbia_above_the_dalles", "var": "swe"})
    assert r.status_code == 200 and r.headers["cache-control"] == "max-age=900"
    body = r.json()
    assert season.RESPONSE_KEYS == OLD_KEYS + ("peak", "peak_absence", "source")
    assert tuple(body) == season.RESPONSE_KEYS
    assert body["units"] == "% of normal peak" and body["season"]["mode"] == "level"
    assert body["source"] == {"dataset": "snow_basin_index_daily",
                              "method": season.METHODS[("snow", "swe")],
                              "n_index": 163, "n_reporting": 162, "normals_version": "v1"}
    assert body["source"]["method"] == (
        "Snow water equivalent at the basin's index stations, summed and divided by the "
        "sum of their 1991–2020 median peaks; a day needs 80 % of the index stations "
        "reporting.")
    q, p = next(s for s in pool.statements if s[0] == season.SNOW_SQL)
    assert p == {"d": "snow_basin_index_daily", "s": "columbia_above_the_dalles.SWE_PCT"}


# ---------------------------------------------------------------------------
# N10 — the test vector (§0, read from Neon 2026-09-30 12:46Z)
# ---------------------------------------------------------------------------

VECTOR = {  # series: (1997-04-01, 2015-04-01, 2026-04-01, (WY2026 peak, day))
    "col_above_grand_coulee": (153.98, 58.83, 72.32, (78.12, date(2026, 3, 17))),
    "col_mid_tributaries": (141.88, 20.81, 35.15, (46.44, date(2026, 3, 13))),
    "snake_upper": (144.60, 48.40, 39.05, (61.30, date(2026, 3, 14))),
    "snake_lower": (152.18, 54.68, 62.32, (69.29, date(2026, 3, 16))),
    "snake": (147.35, 50.68, 47.50, (63.89, date(2026, 3, 14))),
    "columbia_above_the_dalles": (148.04, 46.35, 51.88, (63.82, date(2026, 3, 16))),
}
PEAKS_1DP = {"col_above_grand_coulee": 78.1, "col_mid_tributaries": 46.4,
             "snake_upper": 61.3, "snake_lower": 69.3, "snake": 63.9,
             "columbia_above_the_dalles": 63.8}


def test_n10_the_test_vector():
    for basin, (v97, v15, v26, (pk, pk_day)) in VECTOR.items():
        daily = {date(1997, 4, 1): v97, date(2015, 4, 1): v15,
                 date(2026, 4, 1): v26, pk_day: pk}
        p = _build(daily, area=f"snow:{basin}")
        assert p["readout"]["day"] == AX.index("04-01")
        by = {y["season"]: y for y in p["years"]}
        assert by["WY1997"]["to_date"] == round(v97, 1)
        assert by["WY2015"]["to_date"] == round(v15, 1)
        assert by["WY2026"]["to_date"] == round(v26, 1)
        assert p["readout"]["value"] == round(v26, 1)
        # The pure walk carries the banked values exactly; rounding is the edge's.
        assert _walk(1996, daily).values[AX.index("04-01")] == v97
        assert _walk(2014, daily).values[AX.index("04-01")] == v15
        assert p["peak"]["this_season"] == {"value": PEAKS_1DP[basin],
                                            "date": pk_day.isoformat()}


# ---------------------------------------------------------------------------
# N11 — the board
# ---------------------------------------------------------------------------

def test_n11_board_six_rows_and_a_missing_frontier(client, pool):
    del pool.snow["snake_lower"][date(2026, 9, 28)]        # a day behind the rest
    r = client.get("/api/weather/snow/board")
    assert r.status_code == 200 and r.headers["cache-control"] == "max-age=900"
    body = r.json()
    assert body["as_of"] == "2026-09-28"
    rows = body["basins"]
    assert [(b["area"], b["label"]) for b in rows] == LABELS
    assert all(tuple(b) == season.BOARD_KEYS for b in rows)

    sl = rows[5]
    assert sl["absence"] == {"reason": "frontier_missing", "date": "2026-09-28",
                             "last_valued": "2026-09-27"}
    assert sl["value"] is None and sl["median"] is None and sl["n_reporting"] is None
    assert sl["n_index"] == 24 and sl["peak_this_season"] is not None

    cd = rows[0]
    season_p = main._season_cache[("snow:columbia_above_the_dalles", "swe", "cpc_oni")][1]
    r0 = season_p["readout"]
    assert cd["absence"] is None and cd["date"] == "2026-09-28"
    assert (cd["value"], cd["median"], cd["pct_of_median"], cd["percentile"]) == (
        r0["value"], r0["median"], r0["pct_of_median"], r0["percentile"])
    wy25 = next(y for y in season_p["years"] if y["season"] == "WY2025")
    assert cd["last_year"] == wy25["to_date"]
    assert cd["five_year_mean"] == season_p["five_year"]["mean"][r0["day"]]
    assert cd["peak_this_season"] == season_p["peak"]["this_season"]
    assert all(b["n_reporting"] <= b["n_index"] for b in rows if b["n_reporting"] is not None)
    # Built from the same memo: a second read issues no statement.
    n = len(pool.statements)
    client.get("/api/weather/snow/board")
    client.get("/api/weather/season", params={"area": "snow:snake", "var": "swe"})
    assert len(pool.statements) == n


# ---------------------------------------------------------------------------
# The handback's size
# ---------------------------------------------------------------------------

def test_swe_payload_size_for_the_handback(capsys):
    """snow:columbia_above_the_dalles' shape: rows 1983-09-13 → 2026-09-28."""
    daily = {d: 0.0 for d in _days(date(1983, 9, 13), date(1983, 9, 30))}
    daily.update(_history(1983, 2025, _snowish, frontier=date(2026, 9, 28)))
    body = json.dumps(_build(daily, area="snow:columbia_above_the_dalles", meta=_META)).encode()
    with capsys.disabled():
        print(f"\n[swe size] snow:columbia_above_the_dalles {len(body)} bytes")
    assert len(body) < 2_000_000
