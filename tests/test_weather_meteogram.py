"""
Tests for the Weather Phase 2.5 meteogram (2026-07-26):
  GET /api/weather/meteogram?series=<SERIES>

The build is the pure composition in main._compute_meteogram (+ the small pure
helpers: series resolution, synoptic issuance bucketing/walk-back shared with the
delta board, the ghcnh-authority/nws-trailing actuals compose, and the station
normals band), so most tests exercise those directly with hand-built rows and an
injected `now` — no DB, no clock. Route-level tests cover the SQL/cache wiring
and the DB-down 503 via a fake pool that dispatches rows by query (the region
path fires 3 queries — actuals, forecasts, normals — the station path 4),
mirroring test_weather_delta_board.
"""

import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

import main


UTC = datetime.timezone.utc
NOW = datetime.datetime(2026, 7, 26, 19, 0, tzinfo=UTC)  # 12:00 PDT
LA = "America/Los_Angeles"
LA_TZ = ZoneInfo(LA)


def _dt(y, mo, dd, h=0, mi=0):
    return datetime.datetime(y, mo, dd, h, mi, tzinfo=UTC)


def _fc_rows(series, issued_ts, targets, coverage=None, max_age=None):
    """forecasts_nws rows for one issuance. `targets` is [(target_ts, value)];
    value is °F for a region series, °C for a station series (the caller picks)."""
    rows = []
    for target_ts, value in targets:
        row = {"series": series, "issued_ts": issued_ts, "target_ts": target_ts,
               "value": float(value)}
        if coverage is not None:
            row["coverage"] = coverage
        if max_age is not None:
            row["max_age_hours"] = max_age
        rows.append(row)
    return rows


def _hourly(start, hours, value):
    """[(ts, value)] hourly from `start` for `hours` steps at a constant value."""
    return [(start + datetime.timedelta(hours=h), value) for h in range(hours)]


def _norm_rows(region, entries, tz=LA_TZ):
    """lwt_normals_daily rows for a region: a TMAX_NORM + TMIN_NORM pair per entry.
    `entries` is [(month, day, tmax_f, tmin_f)]; ts is local midnight in `tz` on the
    2012 leap reference year (Feb 29 keyed there), stored as the UTC instant the way
    a timestamptz round-trips from the DB."""
    rows = []
    for mo, dd, tmax, tmin in entries:
        ts = datetime.datetime(2012, mo, dd, 0, 0, tzinfo=tz).astimezone(UTC)
        rows.append({"series": f"{region}.TMAX_NORM", "ts": ts, "value": float(tmax)})
        rows.append({"series": f"{region}.TMIN_NORM", "ts": ts, "value": float(tmin)})
    return rows


# ---------------------------------------------------------------------------
# Series resolution
# ---------------------------------------------------------------------------

def test_resolve_region():
    kind, tz, sid, fc = main._resolve_meteogram_series("PGE-TAC")
    assert (kind, tz, sid, fc) == ("region", LA, None, "PGE-TAC")


def test_resolve_station_by_ghcn_id_and_icao():
    s = main._WEATHER_STATIONS[0]
    by_id = main._resolve_meteogram_series(s["station_id"])
    assert by_id == ("station", s["tz"], s["station_id"], f"{s['station_id']}_temperature")
    by_icao = main._resolve_meteogram_series(s["icao"])
    assert by_icao == ("station", s["tz"], s["station_id"], f"{s['station_id']}_temperature")


def test_resolve_station_by_temperature_series_string():
    s = main._WEATHER_STATIONS[0]
    kind, tz, sid, fc = main._resolve_meteogram_series(f"{s['station_id']}_temperature")
    assert (kind, sid, fc) == ("station", s["station_id"], f"{s['station_id']}_temperature")


def test_resolve_offbasket_station_falls_back_to_pacific():
    kind, tz, sid, fc = main._resolve_meteogram_series("USW00099999")
    assert kind == "station"
    assert tz == LA  # Western-desk fallback, not an error
    assert fc == "USW00099999_temperature"


# ---------------------------------------------------------------------------
# Issuance bucketing — agrees with the delta board (acceptance #1)
# ---------------------------------------------------------------------------

def test_current_and_first_ghost_are_board_current_and_prior():
    # Two synoptic buckets, 12Z (newest) and 06Z; the solid line must be 12Z and
    # the first ghost 06Z — the exact runs the board compares.
    cur = _fc_rows("PGE-TAC", _dt(2026, 7, 26, 12, 25),
                   _hourly(_dt(2026, 7, 26, 20), 12, 100))
    pri = _fc_rows("PGE-TAC", _dt(2026, 7, 26, 6, 30),
                   _hourly(_dt(2026, 7, 26, 20), 12, 96))
    current, ghosts = main._meteo_build_issuances(cur + pri, is_region=True, seam=NOW)
    assert current["bucket"] == _dt(2026, 7, 26, 12).isoformat()
    assert current["model"] == "nws"
    assert len(ghosts) == 1
    assert ghosts[0]["bucket"] == _dt(2026, 7, 26, 6).isoformat()


def test_latest_event_in_a_bucket_wins():
    early = _fc_rows("PGE-TAC", _dt(2026, 7, 26, 12, 5), _hourly(_dt(2026, 7, 26, 20), 6, 90))
    late = _fc_rows("PGE-TAC", _dt(2026, 7, 26, 12, 50), _hourly(_dt(2026, 7, 26, 20), 6, 93))
    current, _ = main._meteo_build_issuances(early + late, is_region=True, seam=NOW)
    assert current["issued_ts"] == _dt(2026, 7, 26, 12, 50).isoformat()
    assert current["points"][0]["temp_f"] == 93.0


def test_two_ghosts_walk_back_but_stop_at_24h_gap():
    # current 12Z, ghost 06Z, then a 00Z that is > 24h before 06Z → cut.
    cur = _fc_rows("PGE-TAC", _dt(2026, 7, 26, 12, 25), _hourly(_dt(2026, 7, 26, 20), 6, 100))
    g1 = _fc_rows("PGE-TAC", _dt(2026, 7, 26, 6, 25), _hourly(_dt(2026, 7, 26, 20), 6, 98))
    old = _fc_rows("PGE-TAC", _dt(2026, 7, 24, 18, 25), _hourly(_dt(2026, 7, 26, 20), 6, 90))
    current, ghosts = main._meteo_build_issuances(cur + g1 + old, is_region=True, seam=NOW)
    assert current["bucket"] == _dt(2026, 7, 26, 12).isoformat()
    assert [g["bucket"] for g in ghosts] == [_dt(2026, 7, 26, 6).isoformat()]  # old cut


def test_forecast_points_clip_to_seam_forward():
    # A 12Z issuance carries targets from 18:00Z; the seam (last actual) is 19:00Z,
    # so the 18:00 point is dropped and the line reads now→horizon.
    rows = _fc_rows("PGE-TAC", _dt(2026, 7, 26, 12, 25),
                    [(_dt(2026, 7, 26, 18), 95), (_dt(2026, 7, 26, 19), 96), (_dt(2026, 7, 26, 20), 97)])
    current, _ = main._meteo_build_issuances(rows, is_region=True, seam=_dt(2026, 7, 26, 19))
    ts = [p["ts"] for p in current["points"]]
    assert _dt(2026, 7, 26, 18).isoformat() not in ts
    assert ts[0] == _dt(2026, 7, 26, 19).isoformat()


def test_station_forecast_celsius_converts_to_f():
    rows = _fc_rows("KXXX_temperature", _dt(2026, 7, 26, 12, 25),
                    _hourly(_dt(2026, 7, 26, 20), 4, 20.0))  # 20°C
    current, _ = main._meteo_build_issuances(rows, is_region=False, seam=NOW)
    assert current["points"][0]["temp_f"] == 68.0  # 20°C → 68°F
    assert current["coverage"] is None  # stations carry no composition coverage


def test_region_coverage_and_max_age_passthrough():
    rows = _fc_rows("PGE-TAC", _dt(2026, 7, 26, 12, 25),
                    _hourly(_dt(2026, 7, 26, 20), 6, 100), coverage=0.7, max_age=9.5)
    current, _ = main._meteo_build_issuances(rows, is_region=True, seam=NOW)
    assert current["coverage"] == 0.7
    assert current["max_age_hours"] == 9.5


# ---------------------------------------------------------------------------
# Actuals composition
# ---------------------------------------------------------------------------

def test_region_actuals_are_fahrenheit_passthrough():
    rows = [{"ts": _dt(2026, 7, 26, 17), "value": 81.59},
            {"ts": _dt(2026, 7, 26, 18), "value": 82.4}]
    out = main._meteo_region_actuals(rows)
    assert out == [(_dt(2026, 7, 26, 17), 81.6), (_dt(2026, 7, 26, 18), 82.4)]


def test_station_actuals_ghcnh_authority_nws_trailing_edge():
    # ghcnh (authority) covers through 15:00Z; nws_obs overlaps AND extends to
    # 19:00Z. Where both exist ghcnh wins; nws fills only past ghcnh's latest.
    ghcnh = [{"ts": _dt(2026, 7, 26, 14), "value": 10.0},
             {"ts": _dt(2026, 7, 26, 15), "value": 15.0}]
    nws = [{"ts": _dt(2026, 7, 26, 15), "value": 99.0},   # overlaps ghcnh → ignored
           {"ts": _dt(2026, 7, 26, 16), "value": 20.0},
           {"ts": _dt(2026, 7, 26, 19), "value": 25.0}]
    out = main._meteo_station_actuals(ghcnh, nws)
    by_ts = dict(out)
    assert by_ts[_dt(2026, 7, 26, 15)] == main._c_to_f(15.0)  # ghcnh authority, not 99
    assert by_ts[_dt(2026, 7, 26, 16)] == round(main._c_to_f(20.0), 1)  # nws trailing edge
    assert by_ts[_dt(2026, 7, 26, 19)] == round(main._c_to_f(25.0), 1)
    assert list(by_ts) == sorted(by_ts)  # ascending


# ---------------------------------------------------------------------------
# Full payload composition
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Region normals band — maps plotted local dates onto the 2012 (month, day) keys
# ---------------------------------------------------------------------------

def test_region_normals_map_local_dates_onto_2012_keys():
    rows = _norm_rows("PGE-TAC", [(7, 26, 90.0, 58.0), (7, 27, 91.0, 59.0)])
    dates = [datetime.date(2026, 7, 26), datetime.date(2026, 7, 27)]
    band = main._meteo_region_normals(rows, dates, LA_TZ)
    by_date = {b["date"]: b for b in band}
    assert by_date["2026-07-26"] == {"date": "2026-07-26", "normal_max_f": 90.0, "normal_min_f": 58.0}
    assert by_date["2026-07-27"]["normal_max_f"] == 91.0


def test_region_normals_feb29_maps_to_2012_reference():
    # Feb 29 keyed on the 2012 reference year; a leap plotted year (2028) lands on it.
    rows = _norm_rows("PGE-TAC", [(2, 29, 62.0, 44.0)])
    band = main._meteo_region_normals(rows, [datetime.date(2028, 2, 29)], LA_TZ)
    assert band == [{"date": "2028-02-29", "normal_max_f": 62.0, "normal_min_f": 44.0}]


def test_region_normals_omit_day_missing_a_bound():
    # A TMAX row with no matching TMIN → half-band, omitted (same rule as stations).
    ts = datetime.datetime(2012, 7, 26, 0, 0, tzinfo=LA_TZ).astimezone(UTC)
    rows = [{"series": "PGE-TAC.TMAX_NORM", "ts": ts, "value": 90.0}]
    assert main._meteo_region_normals(rows, [datetime.date(2026, 7, 26)], LA_TZ) == []


def test_region_normals_empty_rows_yield_empty_band():
    assert main._meteo_region_normals([], [datetime.date(2026, 7, 26)], LA_TZ) == []


def test_region_payload_omits_band_with_region_pending_basis():
    # Empty dataset (pantry hasn't written, or the region failed the coverage gate):
    # None and [] both fall to region_pending — not faked, not borrowed.
    actuals = [(_dt(2026, 7, 26, 18), 80.0)]
    fc = _fc_rows("PGE-TAC", _dt(2026, 7, 26, 12, 25), _hourly(_dt(2026, 7, 26, 19), 6, 100))
    for empty in (None, []):
        payload = main._compute_meteogram("PGE-TAC", "region", LA, NOW, actuals, fc, empty)
        assert payload["kind"] == "region"
        assert payload["tz"] == LA
        assert payload["current"]["bucket"] == _dt(2026, 7, 26, 12).isoformat()
        assert payload["normals"] == []
        assert payload["normals_basis"] == "region_pending"


def test_region_payload_builds_band_with_region_basis():
    actuals = [(_dt(2026, 7, 26, 18), 80.0)]
    fc = _fc_rows("PGE-TAC", _dt(2026, 7, 26, 12, 25), _hourly(_dt(2026, 7, 26, 19), 6, 100))
    normals = _norm_rows("PGE-TAC", [(7, 26, 90.0, 58.0), (7, 27, 91.0, 59.0)])
    payload = main._compute_meteogram("PGE-TAC", "region", LA, NOW, actuals, fc, normals)
    assert payload["kind"] == "region"
    assert payload["normals_basis"] == "region"
    days = {n["date"]: n for n in payload["normals"]}
    assert "2026-07-26" in days
    assert days["2026-07-26"]["normal_max_f"] == 90.0
    assert days["2026-07-26"]["normal_min_f"] == 58.0


def test_station_payload_builds_band_from_normals():
    actuals = [(_dt(2026, 7, 26, 18), 25.0)]
    fc = _fc_rows("KXXX_temperature", _dt(2026, 7, 26, 12, 25), _hourly(_dt(2026, 7, 26, 19), 8, 25.0))
    normals = [{"month": 7, "day": 26, "tmax_norm_f": 90.0, "tmin_norm_f": 58.0},
               {"month": 7, "day": 27, "tmax_norm_f": 91.0, "tmin_norm_f": 59.0}]
    payload = main._compute_meteogram("KXXX", "station", LA, NOW, actuals, fc, normals)
    assert payload["kind"] == "station"
    assert payload["normals_basis"] == "station"
    days = {n["date"]: n for n in payload["normals"]}
    assert "2026-07-26" in days
    assert days["2026-07-26"]["normal_max_f"] == 90.0
    assert days["2026-07-26"]["normal_min_f"] == 58.0


def test_station_with_no_normals_omits_band_basis_none():
    # KSFO-style: the station flows forecasts but has no normals rows.
    fc = _fc_rows("KXXX_temperature", _dt(2026, 7, 26, 12, 25), _hourly(_dt(2026, 7, 26, 19), 6, 25.0))
    payload = main._compute_meteogram("KXXX", "station", LA, NOW, [(_dt(2026, 7, 26, 18), 25.0)], fc, [])
    assert payload["normals"] == []
    assert payload["normals_basis"] == "none"


def test_seam_join_actuals_meet_forecast_without_gap():
    # Last actual at 19:00Z; the current issuance carries a 19:00Z target, so the
    # forecast starts exactly at the seam — one continuous timeline.
    actuals = [(_dt(2026, 7, 26, 18), 80.0), (_dt(2026, 7, 26, 19), 82.0)]
    fc = _fc_rows("PGE-TAC", _dt(2026, 7, 26, 12, 25),
                  [(_dt(2026, 7, 26, 19), 100), (_dt(2026, 7, 26, 20), 101)])
    payload = main._compute_meteogram("PGE-TAC", "region", LA, NOW, actuals, fc, None)
    assert payload["actuals"][-1]["ts"] == _dt(2026, 7, 26, 19).isoformat()
    assert payload["current"]["points"][0]["ts"] == _dt(2026, 7, 26, 19).isoformat()


# ---------------------------------------------------------------------------
# Route wiring: cache + 503, via a fake pool that dispatches rows by query
# ---------------------------------------------------------------------------

class _FakeCursor:
    def __init__(self, plan, fail):
        self._plan, self._fail = plan, fail
        self._next: list = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, query, params=None):
        if self._fail:
            raise RuntimeError("boom")
        params = params or {}
        if "lwt_temp_hourly" in query:
            self._next = self._plan.get("region_actuals", [])
        elif "lwt_normals_daily" in query:
            self._next = self._plan.get("region_normals", [])
        elif "agg_version" in query:
            self._next = self._plan.get("region_fc", [])
        elif "station_normals_daily" in query:
            self._next = self._plan.get("normals", [])
        elif "%(ds)s" in query:
            self._next = self._plan.get(params.get("ds"), [])
        elif "forecasts_nws" in query:
            self._next = self._plan.get("station_fc", [])
        else:
            self._next = []

    async def fetchall(self):
        return list(self._next)


class _FakeConn:
    def __init__(self, plan, fail):
        self._plan, self._fail = plan, fail

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def cursor(self):
        return _FakeCursor(self._plan, self._fail)


class _FakePool:
    def __init__(self, plan, fail=False):
        self._plan, self._fail = plan, fail

    def connection(self):
        return _FakeConn(self._plan, self._fail)


@pytest.fixture(autouse=True)
def _clear_cache():
    main._meteogram_cache.clear()
    yield
    main._meteogram_cache.clear()


def test_route_region_returns_payload_and_caches(monkeypatch):
    monkeypatch.setattr(main, "_utcnow", lambda: NOW)
    plan = {
        "region_actuals": [{"ts": _dt(2026, 7, 26, 18), "value": 80.0}],
        "region_fc": _fc_rows("PGE-TAC", _dt(2026, 7, 26, 12, 25),
                              _hourly(_dt(2026, 7, 26, 19), 6, 100),
                              coverage=1.0, max_age=3.0),
        "region_normals": _norm_rows("PGE-TAC", [(7, 26, 90.0, 58.0), (7, 27, 91.0, 59.0)]),
    }
    monkeypatch.setattr(main, "_pool", _FakePool(plan))
    client = TestClient(main.app)
    resp = client.get("/api/weather/meteogram", params={"series": "PGE-TAC"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["kind"] == "region"
    assert body["current"]["bucket"] == _dt(2026, 7, 26, 12).isoformat()
    assert body["normals_basis"] == "region"
    assert any(n["date"] == "2026-07-26" for n in body["normals"])
    assert resp.headers.get("Cache-Control") == "max-age=60"
    # Second call served from cache even if the pool would now fail.
    monkeypatch.setattr(main, "_pool", _FakePool({}, fail=True))
    assert client.get("/api/weather/meteogram", params={"series": "PGE-TAC"}).status_code == 200


def test_route_region_empty_normals_is_pending(monkeypatch):
    # The pantry hasn't published this region's normals yet: the normals query
    # returns no rows and the band stays region_pending (empty ≠ error).
    monkeypatch.setattr(main, "_utcnow", lambda: NOW)
    plan = {
        "region_actuals": [{"ts": _dt(2026, 7, 26, 18), "value": 80.0}],
        "region_fc": _fc_rows("PGE-TAC", _dt(2026, 7, 26, 12, 25),
                              _hourly(_dt(2026, 7, 26, 19), 6, 100)),
    }
    monkeypatch.setattr(main, "_pool", _FakePool(plan))
    client = TestClient(main.app)
    resp = client.get("/api/weather/meteogram", params={"series": "PGE-TAC"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["normals"] == []
    assert body["normals_basis"] == "region_pending"


def test_route_station_builds_band(monkeypatch):
    monkeypatch.setattr(main, "_utcnow", lambda: NOW)
    s = main._WEATHER_STATIONS[0]
    fc_series = f"{s['station_id']}_temperature"
    plan = {
        "station_fc": _fc_rows(fc_series, _dt(2026, 7, 26, 12, 25),
                              _hourly(_dt(2026, 7, 26, 20), 6, 25.0)),
        "ghcnh_weather_hourly": [{"ts": _dt(2026, 7, 26, 14), "value": 15.0}],
        "nws_obs_hourly": [{"ts": _dt(2026, 7, 26, 18), "value": 25.0}],
        "normals": [{"month": 7, "day": 26, "tmax_norm_f": 90.0, "tmin_norm_f": 58.0}],
    }
    monkeypatch.setattr(main, "_pool", _FakePool(plan))
    client = TestClient(main.app)
    resp = client.get("/api/weather/meteogram", params={"series": s["station_id"]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["kind"] == "station"
    assert body["normals_basis"] == "station"
    assert body["actuals"], "station actuals composed from ghcnh + nws_obs"
    assert any(n["date"] == "2026-07-26" for n in body["normals"])


def test_route_503_on_db_error(monkeypatch):
    monkeypatch.setattr(main, "_utcnow", lambda: NOW)
    monkeypatch.setattr(main, "_pool", _FakePool({}, fail=True))
    client = TestClient(main.app)
    assert client.get("/api/weather/meteogram", params={"series": "PGE-TAC"}).status_code == 503


def test_route_requires_series_param():
    client = TestClient(main.app)
    assert client.get("/api/weather/meteogram").status_code == 422
