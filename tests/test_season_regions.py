"""
d091542 — the Season API names what every area measures and where it is, adds
California's three regional reservoir composites, counts seasons without the
one in progress, and gives the season routes their own connections.

A1..A4 and P1..P4 are the spec's §2.8 table
(cc spec d091542 "the Season API: named data types, regions, a snapshot for
the map, and reads that never wait 30 s"); each test's name carries its id.
The stand-in pool is the house pattern (tests/test_season_hydro.py).
"""

import asyncio
import datetime
import json
import re
import time
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from psycopg_pool import PoolTimeout

import main
import season

UTC = datetime.timezone.utc
FIXTURE = Path(__file__).parent / "fixtures" / "season_areas_counts_d091542.json"
DEVELOPING = {"kind": "nino", "n_seasons": 4, "first_year": 2026, "latest_oni": 1.8,
              "latest_year": 2026, "first_season": "MAM", "latest_season": "JJA"}


def _days(a: date, b: date):
    d = a
    while d <= b:
        yield d
        d += timedelta(days=1)


def _bins():
    return [{"enso_year": y, "kind": "nino" if y % 4 == 0 else "neutral",
             "strength": "weak" if y % 4 == 0 else None, "flavor": None}
            for y in range(1949, 2027)]


# ---------------------------------------------------------------------------
# A stand-in for the bank: eight reservoirs, every day WY1996..WY2026 to Sep 28
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


FRONTIER = date(2026, 9, 28)
HOLE = date(2026, 3, 3)            # shasta has no row: ca_north and the eight null


class _Pool:
    def __init__(self, areas_rows=None):
        self.statements = []
        self.areas_rows = areas_rows or {}
        self.res = {i: {d: 1000.0 * (k + 1) + d.timetuple().tm_yday
                        for d in _days(date(1995, 10, 1), FRONTIER)}
                    for k, i in enumerate(season.RESERVOIR_IDS)}
        del self.res["shasta"][HOLE]

    def connection(self):
        return _Conn(self)

    def answer(self, q, p):
        fam = {season.AREAS_PRECIP_SQL: "precip", season.AREAS_STATION_DD_SQL: "station_dd",
               season.AREAS_LWT_SQL: "lwt", season.AREAS_SNOW_SQL: "snow",
               season.AREAS_CA_SNOW_SQL: "ca_snow", season.AREAS_RESERVOIR_SQL: "reservoir",
               season.AREAS_RESERVOIR_REGIONS_SQL: "reservoir_regions",
               season.AREAS_LOAD_SQL: "load"}.get(q)
        if fam is not None:
            return self.areas_rows.get(fam, [])
        if q == season.RESERVOIRS_SQL:
            assert p["d"] == "cdec_reservoir_storage_daily"
            rows = [(d, i, v) for i in p["s"] for d, v in self.res[i].items()]
            return [{"obs_date": d, "series": i, "v": v} for d, i, v in sorted(rows)]
        if q == season.LWT_SQL and p["d"] == "cdec_reservoir_storage_daily":
            return [{"obs_date": d, "v": v} for d, v in sorted(self.res[p["s"]].items())]
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
    monkeypatch.setattr(main, "_pool", None)          # the season routes never need it
    monkeypatch.setattr(main, "_season_pool", p)
    return p


@pytest.fixture
def client(pool):
    return TestClient(main.app)


# ---------------------------------------------------------------------------
# A1 — every area says what it measures and where it is
# ---------------------------------------------------------------------------

def test_a1_every_area_has_region_level_and_every_var_a_data_type(client):
    body = client.get("/api/weather/season/areas").json()
    areas = body["areas"]
    assert len(areas) == 89
    for a in areas:
        assert a["level"] in season.LEVELS, a["area"]
        assert a["region"] in season.REGIONS or (a["region"] is None
                                                  and a["region_absence"]), a["area"]
        for v in a["vars"]:
            assert v["data_type"] in season.DATA_TYPES and v["measure"], (a["area"], v)
            assert (v["data_type"], v["measure"]) == season.VAR_DATA_TYPES[v["var"]]
    swe = next(a for a in areas if a["area"] == "snow:col_canada")["vars"][0]
    swe_in = next(a for a in areas if a["area"] == "snow:ca_state")["vars"][0]
    assert swe["data_type"] == swe_in["data_type"] == "snow_water_equivalent"
    assert (swe["measure"], swe_in["measure"]) == ("percent of normal peak", "inches")
    # the existing keys come first, in their order; d091542's after them
    ba = next(a for a in areas if a["area"] == "ba:BPAT")
    assert list(ba) == ["area", "kind", "label", "frontier", "vars",
                        "region", "level", "state", "region_basis"]
    assert (ba["region"], ba["state"], ba["region_basis"]) == (
        "pacific_northwest", "OR", "home state")
    assert list(swe) == ["var", "season", "units", "first_season", "complete_seasons",
                         "qualifying_seasons", "data_type", "measure"]
    spokane = areas[0]
    assert list(spokane)[:4] == ["area", "kind", "label", "state"]   # state where it was
    assert (spokane["region"], spokane["level"]) == ("pacific_northwest", "station")


def test_a1_levels_follow_the_table():
    lv = {a: season.area_place(a, main._WEATHER_STATIONS)["level"]
          for a in season.area_vocabulary(main._WEATHER_STATIONS)}
    assert lv["snow:ca_state"] == lv["reservoir:ca_major8"] == "aggregate"
    assert lv["snow:columbia_above_the_dalles"] == lv["snow:col_canada"] == "aggregate"
    assert {lv[f"snow:{r}"] for r in ("ca_north", "ca_central", "ca_south")} == {"region"}
    assert {lv[f"reservoir:{r}"] for r in season.RESERVOIR_REGION_IDS} == {"region"}
    assert {lv[f"reservoir:{r}"] for r in season.RESERVOIR_IDS} == {"reservoir"}
    assert {lv[f"snow:{b}"] for b in season.SNOW_BASINS[1:]} == {"basin"}
    assert {lv[a] for a in lv if a.startswith(("lwt:", "ba:"))} == {"balancing_area"}
    assert {lv[a] for a in lv if a.startswith("station:")} == {"station"}


def test_a1_stop_r_every_area_has_a_region_or_says_why():
    """The pinned table covers every area the list serves; what it cannot
    place (STOP-R) is listed here, exactly."""
    missing = []
    for a in season.area_vocabulary(main._WEATHER_STATIONS):
        place = season.area_place(a, main._WEATHER_STATIONS)
        if place.get("region") is None:
            missing.append((a, place["region_absence"]))
    assert missing == [(f"station:{s}", "no state is banked for this area")
                       for s in season.UNLABELLED_STATIONS] + [
        ("lwt:EPE", "state TX is not in the region table"),
        ("ba:EPE", "state TX is not in the region table")]
    assert set(season.BA_HOME_STATES) == set(season.BAS)
    assert set(season.LWT_HOME_STATES) == set(season.LWT_BAS)


# ---------------------------------------------------------------------------
# A2 — the regions block
# ---------------------------------------------------------------------------

def test_a2_regions_block(client):
    reg = client.get("/api/weather/season/areas").json()["regions"]
    assert list(reg) == ["california", "pacific_northwest", "oregon", "canada",
                         "southwest", "rockies"]
    ca, pnw, orr = reg["california"], reg["pacific_northwest"], reg["oregon"]
    assert ca["aggregates"]["snow_water_equivalent"] == "snow:ca_state"
    assert ca["aggregates"]["reservoir_storage"] == "reservoir:ca_major8"
    assert pnw["aggregates"]["snow_water_equivalent"] == "snow:columbia_above_the_dalles"
    assert "The Dalles" in pnw["aggregate_notes"]["snow_water_equivalent"]
    assert pnw["aggregates"]["reservoir_storage"] is None
    assert orr["aggregates"]["snow_water_equivalent"] is None
    assert orr["aggregate_notes"]["snow_water_equivalent"] == \
        "no Oregon snow index is banked (d091544)"
    assert (orr["kind"], orr["within"]) == ("state", "pacific_northwest")
    assert "station:USW00024229" in orr["members"]                       # Portland
    assert all(m.startswith(("station:", "lwt:", "ba:")) for m in orr["members"])
    assert reg["canada"]["aggregates"]["snow_water_equivalent"] == "snow:col_canada"
    assert "reservoir:ca_north" in ca["members"] and "snow:ca_south" in ca["members"]
    assert "snow:snake" in pnw["members"] and "ba:BPAT" in pnw["members"]


# ---------------------------------------------------------------------------
# A3 — counts leave out the season in progress
# ---------------------------------------------------------------------------

def _banked_rows():
    fx = json.loads(FIXTURE.read_text())
    for fam in ("snow", "ca_snow"):
        for r in fx[fam]:
            r["last"] = date.fromisoformat(r["last"]) if r["last"] else None
    return {"snow": fx["snow"], "ca_snow": fx["ca_snow"]}


def _counts(monkeypatch):
    monkeypatch.setattr(main, "_season_pool", _Pool(_banked_rows()))
    main._season_areas_cache.clear()
    areas = TestClient(main.app).get("/api/weather/season/areas").json()["areas"]
    by = {a["area"]: a["vars"][0] for a in areas}
    return by["snow:col_canada"], by["snow:ca_state"]


def test_a3_counts_exclude_the_season_in_progress(monkeypatch):
    canada, ca = _counts(monkeypatch)
    # Production rows, 2026-10-01: Canada's frontier is 2026-09-29, before
    # Sep 30, so WY2026 is in progress; California's is 2026-06-05.
    assert (canada["complete_seasons"], canada["qualifying_seasons"]) == (25, 25)
    assert ca["qualifying_seasons"] == 19
    assert ca["complete_seasons"] == 17        # WY2011 and WY2020 fall a day short
    # The payload's base uses the same function.
    assert season.in_progress_season("swe", date(2026, 9, 29)) == 2025
    assert season.in_progress_season("swe", date(2026, 9, 30)) is None


def test_a3_rehearsed_red_the_old_sum_counts_the_season_in_progress(monkeypatch):
    """Restore the old sum (no season is in progress) and the counts read
    what the list read before d091542: the test above would fail."""
    monkeypatch.setattr(season, "in_progress_season", lambda var, frontier: None)
    canada, ca = _counts(monkeypatch)
    assert canada["complete_seasons"] == 26 and ca["qualifying_seasons"] == 20


# ---------------------------------------------------------------------------
# A4 — the three California reservoir composites
# ---------------------------------------------------------------------------

def test_a4_composites_members_capacity_labels(client):
    areas = client.get("/api/weather/season/areas").json()["areas"]
    ids = [a["area"] for a in areas]
    i = ids.index("reservoir:ca_major8")
    assert ids[i:i + 4] == ["reservoir:ca_major8", "reservoir:ca_north",
                            "reservoir:ca_central", "reservoir:ca_south"]
    assert ids[i + 4] == "reservoir:trinity"
    rows = {a["area"]: a for a in areas}
    want = {"ca_north": ("trinity", "shasta", "oroville"),
            "ca_central": ("folsom", "new_melones", "don_pedro"),
            "ca_south": ("millerton",)}
    labels = {"trinity": "Trinity", "shasta": "Shasta", "oroville": "Oroville",
              "folsom": "Folsom", "new_melones": "New Melones", "don_pedro": "Don Pedro",
              "millerton": "Millerton"}
    for c, members in want.items():
        r = rows[f"reservoir:{c}"]
        assert r["members"] == [f"reservoir:{m}" for m in members]
        assert r["capacity_taf"] == sum(season.CAPACITY_AF[m] for m in members) / 1000.0
        assert all(labels[m] in r["label"] for m in members)        # the label names them
        assert (r["region"], r["level"]) == ("california", "region")
    assert rows["reservoir:ca_north"]["capacity_taf"] == 10537.227
    in_any = {m for ms in want.values() for m in ms}
    assert "san_luis" not in in_any
    assert in_any | {"san_luis"} == set(season.RESERVOIR_IDS)
    assert "San Luis" in rows["reservoir:ca_major8"]["members_note"]
    assert "off-stream" in rows["reservoir:ca_major8"]["members_note"]


def test_a4_a_composite_is_the_sum_and_null_when_a_member_is_missing(client, pool):
    r = client.get("/api/weather/season", params={"area": "reservoir:ca_north",
                                                  "var": "storage"})
    assert r.status_code == 200
    body = r.json()
    q, p = next(s for s in pool.statements if s[0] == season.RESERVOIRS_SQL)
    assert p["s"] == ["trinity", "shasta", "oroville"]
    ax = season.axis("storage")
    day = date(2026, 3, 4)
    want = sum(pool.res[m][day] for m in ("trinity", "shasta", "oroville")) / 1000.0
    assert body["this_season"]["values"][ax.index("03-04")] == round(want, 1)
    assert body["this_season"]["values"][ax.index("03-03")] is None     # shasta absent
    src = body["source"]
    assert src["capacity_taf"] == 10537.227
    assert src["capacity_af_by_reservoir"] == {m: season.CAPACITY_AF[m]
                                               for m in ("trinity", "shasta", "oroville")}
    assert src["series_frontiers"] == {"trinity": "2026-09-28", "shasta": "2026-09-28",
                                       "oroville": "2026-09-28"}
    assert src["method"] == season.METHODS[("reservoir_region", "storage")]
    # the south's one member: the composite is that reservoir
    south = client.get("/api/weather/season", params={"area": "reservoir:ca_south",
                                                      "var": "storage"}).json()
    mill = client.get("/api/weather/season", params={"area": "reservoir:millerton",
                                                     "var": "storage"}).json()
    assert south["this_season"]["values"] == mill["this_season"]["values"]


def test_a4_major8_daily_rule_on_members():
    d = date(2026, 1, 1)
    rows = [{"obs_date": d, "series": "folsom", "v": 100.0},
            {"obs_date": d, "series": "new_melones", "v": 200.0},
            {"obs_date": d, "series": "don_pedro", "v": None},
            {"obs_date": d + timedelta(1), "series": "folsom", "v": 1.0},
            {"obs_date": d + timedelta(1), "series": "new_melones", "v": 2.0},
            {"obs_date": d + timedelta(1), "series": "don_pedro", "v": 3.0}]
    daily, fronts = season.composite_daily("reservoir:ca_central", rows)
    assert daily == {d: None, d + timedelta(1): 0.006}
    assert fronts == {"folsom": "2026-01-02", "new_melones": "2026-01-02",
                      "don_pedro": "2026-01-02"}


# ---------------------------------------------------------------------------
# P1 — the season routes check out of _season_pool, and no other route does
# ---------------------------------------------------------------------------

SEASON_ROUTES = ("/api/weather/season/areas", "/api/weather/season",
                 "/api/weather/snow/board", "/api/weather/season/snapshot")


def test_p1_source_sweep():
    src = Path(main.__file__).read_text(encoding="utf-8")
    start = src.index("import season as _season")
    block, rest = src[start:], src[:start]
    # inside the block: never the shared pool
    assert not re.search(r"(?<![\w])_pool\.connection\(", block)
    code = re.sub(r"`[^`\n]*`", "", block)          # prose that names a pool in backticks
    assert not re.search(r"(?<![\w])_pool\b(?!_)", code.replace("_season_pool", ""))
    # outside it: _season_pool only where the lifespan opens and closes it
    outside = [ln.strip() for ln in rest.splitlines()
               if "_season_pool" in ln and not ln.lstrip().startswith("#")]
    assert outside == ["global _pool, _season_pool",
                       "_season_pool = _season_make_pool()",
                       "await _season_pool.open()",
                       "await _season_pool.close()"]
    # every season route is in the block
    for path in SEASON_ROUTES:
        assert f'@app.get("{path}")' in block and f'@app.get("{path}")' not in rest
    # and the block's reads go through _season_connection
    assert block.count("async with _season_connection() as conn:") == 2


def test_p1_the_pool_is_the_spec(monkeypatch):
    monkeypatch.setattr(main, "DATABASE_URL", "postgresql://u@h/db")
    p = main._season_make_pool()
    assert (p.min_size, p.max_size, p.timeout) == (1, 3, 5.0)
    assert main._SEASON_POOL_TIMEOUT == 5.0 and main._SEASON_RETRY_AFTER == 5


# ---------------------------------------------------------------------------
# P2 — an expired key is served stale while exactly one rebuild runs
# ---------------------------------------------------------------------------

def test_p2_stale_while_one_rebuild():
    async def run():
        cache, calls = {}, []
        gate = asyncio.Event()

        async def build():
            calls.append(1)
            await gate.wait()
            return {"v": len(calls)}, {}

        cache["k"] = (time.monotonic() - 1000, {"v": 0}, {}, "2026-10-01T00:00:00+00:00")
        got = await asyncio.gather(*(main._season_memo(cache, "k", 900, build, 8)
                                     for _ in range(5)))
        assert all(g[0] == {"v": 0} and g[2] is True for g in got)
        assert all(g[3] == "2026-10-01T00:00:00+00:00" for g in got)
        await asyncio.sleep(0)
        assert len(calls) == 1                       # one rebuild for five requests
        gate.set()
        for _ in range(5):
            await asyncio.sleep(0)
        fresh = await main._season_memo(cache, "k", 900, build, 8)
        assert fresh[0] == {"v": 1} and fresh[2] is False and len(calls) == 1

    asyncio.run(run())


def test_p2_a_miss_is_single_flight():
    async def run():
        cache, calls = {}, []

        async def build():
            calls.append(1)
            await asyncio.sleep(0.01)
            return {"v": 1}, {}

        got = await asyncio.gather(*(main._season_memo(cache, "k", 900, build, 8)
                                     for _ in range(4)))
        assert len(calls) == 1 and all(g[0] == {"v": 1} and g[2] is False for g in got)

    asyncio.run(run())


def test_p2_the_route_says_stale(client, pool):
    params = {"area": "reservoir:ca_south", "var": "storage"}
    first = client.get("/api/weather/season", params=params).json()
    assert first["stale"] is False and first["built_at"]
    key = ("reservoir:ca_south", "storage", "cpc_oni")
    t, payload, extras, built = main._season_cache[key]
    main._season_cache[key] = (t - main._SEASON_MEMO_TTL - 1, payload, extras, built)
    again = client.get("/api/weather/season", params=params).json()
    assert again["stale"] is True and again["built_at"] == built
    assert {k: v for k, v in again.items() if k not in season.ROUTE_ADDED_KEYS} == \
        {k: v for k, v in first.items() if k not in season.ROUTE_ADDED_KEYS}


# ---------------------------------------------------------------------------
# P3 — a checkout that waits 5 s is a 503 that says so
# ---------------------------------------------------------------------------

class _Exhausted:
    """A pool whose every connection is held: checkout waits its timeout and
    raises PoolTimeout, as psycopg_pool's does."""

    def __init__(self, wait):
        self.wait, self.waits = wait, 0

    def connection(self):
        pool = self

        class _CM:
            async def __aenter__(self):
                pool.waits += 1
                await asyncio.sleep(pool.wait)
                raise PoolTimeout(f"couldn't get a connection after {pool.wait:.2f} sec")

            async def __aexit__(self, *a):
                return False
        return _CM()


def test_p3_checkout_timeout_is_a_503_with_retry_after(monkeypatch):
    monkeypatch.setattr(main, "_season_pool", _Exhausted(main._SEASON_POOL_TIMEOUT))
    t0 = time.monotonic()
    r = TestClient(main.app).get("/api/weather/season",
                                 params={"area": "reservoir:shasta", "var": "storage"})
    elapsed = time.monotonic() - t0
    assert r.status_code == 503 and elapsed < 6.0
    assert r.headers["retry-after"] == "5"
    assert r.json() == {"detail": "season read waited 5 s for a connection", "retry_after": 5}


# ---------------------------------------------------------------------------
# P4 — five connections held on the main pool: a cold /season still answers
# ---------------------------------------------------------------------------

def test_p4_main_pool_held_season_answers(monkeypatch, pool):
    held = _Exhausted(2.0)               # the shared pool: everything held
    monkeypatch.setattr(main, "_pool", held)
    client = TestClient(main.app)
    t0 = time.monotonic()
    r = client.get("/api/weather/season", params={"area": "reservoir:ca_major8",
                                                  "var": "storage"})
    assert r.status_code == 200 and time.monotonic() - t0 < 1.5
    assert held.waits == 0                # nothing so much as asked the shared pool
    r = client.get("/api/weather/season/areas")
    assert r.status_code == 200 and held.waits == 0

    # Rehearsed red: put the season reads back on the held pool and the cold
    # read waits out the checkout and fails, as it did on 2026-10-01.
    main._season_cache.clear()
    monkeypatch.setattr(main, "_season_pool", held)
    t0 = time.monotonic()
    r = client.get("/api/weather/season", params={"area": "reservoir:ca_major8",
                                                  "var": "storage"})
    assert r.status_code == 503 and time.monotonic() - t0 >= 2.0 and held.waits == 1
