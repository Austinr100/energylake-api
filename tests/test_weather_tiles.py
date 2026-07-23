"""
Tests for the Kelvin Tier-1 weather tiles (D-07-20-02, 2026-07-21):
  GET /api/weather/temp-matrix
  GET /api/weather/regime
  GET /api/weather/station-walk

These routes fire more than one query per request (temp-matrix: forecasts then
normals; regime: drivers, cpc, depth), so the fake pool here ROUTES canned rows
by a substring of the SQL rather than returning one static row set. The clock is
frozen via monkeypatching main._utcnow so D1–D7 bucketing and staleness are
deterministic. TestClient is used without its context-manager form, so the real
lifespan (live pool) never runs.
"""

import datetime

import pytest
from fastapi.testclient import TestClient

import main


UTC = datetime.timezone.utc


def _dt(y, mo, d, h=0, mi=0):
    return datetime.datetime(y, mo, d, h, mi, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Routing fake pool: match a query to a canned row set by substring.
# ---------------------------------------------------------------------------

class _FakeCursor:
    def __init__(self, routes, sink):
        self._routes = routes
        self._sink = sink
        self._rows = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, query, params=None):
        self._sink.setdefault("queries", []).append(query)
        self._sink["params"] = params
        self._rows = []
        for needle, rows in self._routes:
            if needle in query:
                self._rows = rows
                break

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def fetchall(self):
        return list(self._rows)


class _FakeConn:
    def __init__(self, routes, sink):
        self._routes = routes
        self._sink = sink

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def cursor(self):
        return _FakeCursor(self._routes, self._sink)


class FakePool:
    def __init__(self, routes):
        # routes: list of (sql_substring, rows) — first match wins.
        self._routes = routes
        self.sink = {}

    def connection(self):
        return _FakeConn(self._routes, self.sink)


def use_routes(routes):
    pool = FakePool(routes)
    main._pool = pool
    return pool


def freeze_now(monkeypatch, when):
    monkeypatch.setattr(main, "_utcnow", lambda: when)


@pytest.fixture
def client():
    return TestClient(main.app)


LAX = "USW00023174"


# ═══════════════════════════════════════════════════════════════════════════
# temp-matrix
# ═══════════════════════════════════════════════════════════════════════════

def _fc(series, issued, target, value):
    return {"series": series, "issued_ts": issued,
            "target_ts": target, "value": value}


def test_matrix_always_17_rows_and_nsouth_order(client, monkeypatch):
    freeze_now(monkeypatch, _dt(2026, 7, 21, 12))
    use_routes([("forecasts_nws", []), ("station_normals_daily", [])])
    d = client.get("/api/weather/temp-matrix").json()
    assert len(d["stations"]) == 17                      # never dropped
    assert [s["order"] for s in d["stations"]] == list(range(1, 18))
    # N→S: Spokane first, San Diego last (ruled station_metadata order).
    assert d["stations"][0]["icao"] == "KGEG"
    assert d["stations"][-1]["icao"] == "KSAN"
    # Every station with no issuance is degraded, cells present-but-null.
    lax = next(s for s in d["stations"] if s["station_id"] == LAX)
    assert lax["degraded"] is True
    assert lax["issued_ts"] is None
    assert len(lax["days"]) == 7
    assert all(c["hi_f"] is None and c["partial"] for c in lax["days"])
    print("ok matrix_always_17_rows_and_nsouth_order")


def test_matrix_station_labels_display_name_and_state(client, monkeypatch):
    """D-07-22-03: every row carries display_name + state from
    station_metadata.json, ADDITIVE to the ratified envelope. Receipt: KSAC →
    'Sacramento', CA. The pre-existing keys are untouched (no rename/reorder)."""
    freeze_now(monkeypatch, _dt(2026, 7, 21, 12))
    use_routes([("forecasts_nws", []), ("station_normals_daily", [])])
    d = client.get("/api/weather/temp-matrix").json()
    # The pinned receipt: Sacramento's label + state.
    ksac = next(s for s in d["stations"] if s["icao"] == "KSAC")
    assert ksac["display_name"] == "Sacramento"
    assert ksac["state"] == "CA"
    # Every station carries both new fields, non-null (all 17 are populated).
    for s in d["stations"]:
        assert isinstance(s["display_name"], str) and s["display_name"]
        assert isinstance(s["state"], str) and len(s["state"]) == 2
    # Spot-check the N and S anchors carry the right states.
    assert next(s for s in d["stations"] if s["icao"] == "KGEG")["state"] == "WA"
    assert next(s for s in d["stations"] if s["icao"] == "KSAN")["state"] == "CA"
    # ADDITIVE: the original envelope keys are all still present and unchanged.
    lax = next(s for s in d["stations"] if s["station_id"] == LAX)
    for k in ("station_id", "name", "metro", "icao", "order", "tz",
              "issued_ts", "degraded", "days"):
        assert k in lax
    print("ok matrix_station_labels_display_name_and_state")


def test_matrix_assert_the_raise_stale_issuance_is_flagged_not_vanished(
        client, monkeypatch):
    """A station whose latest issuance is >24h old must APPEAR, flagged degraded
    with its last issued_ts and null cells — never silently dropped."""
    now = _dt(2026, 7, 21, 12)
    freeze_now(monkeypatch, now)
    stale_issued = now - datetime.timedelta(hours=30)   # outside the 24h window
    rows = [_fc(f"{LAX}_temperature", stale_issued,
                stale_issued + datetime.timedelta(hours=12), 25.0)]
    use_routes([("forecasts_nws", rows), ("station_normals_daily", [])])
    d = client.get("/api/weather/temp-matrix").json()
    assert len(d["stations"]) == 17                      # the raise, not a vanish
    lax = next(s for s in d["stations"] if s["station_id"] == LAX)
    assert lax["degraded"] is True
    assert lax["issued_ts"] == stale_issued.isoformat()  # last issuance surfaced
    assert all(c["hi_f"] is None and c["lo_f"] is None for c in lax["days"])
    print("ok matrix_assert_the_raise")


def test_matrix_timezone_bucketing_local_beats_utc(client, monkeypatch):
    """An evening-local hour whose UTC date is the NEXT day must bucket into the
    LOCAL date. 2026-07-22T04:00Z = 2026-07-21 21:00 PDT → belongs to D1
    (07-21), not D2 (07-22). Proven by a spike value that would otherwise leak
    into D2 under naive UTC bucketing."""
    now = _dt(2026, 7, 21, 12)
    freeze_now(monkeypatch, now)
    issued = _dt(2026, 7, 21, 7)
    rows = [
        # Evening-local spike: 30C, UTC 07-22 but local 07-21 (PDT).
        _fc(f"{LAX}_temperature", issued, _dt(2026, 7, 22, 4), 30.0),
        # A genuine 07-22-local reading (13:00 PDT), cooler.
        _fc(f"{LAX}_temperature", issued, _dt(2026, 7, 22, 20), 25.0),
    ]
    use_routes([("forecasts_nws", rows), ("station_normals_daily", [])])
    d = client.get("/api/weather/temp-matrix").json()
    lax = next(s for s in d["stations"] if s["station_id"] == LAX)
    assert lax["degraded"] is False
    by_date = {c["date_local"]: c for c in lax["days"]}
    # D1 owns the evening spike (30C -> 86F); local bucket won.
    assert by_date["2026-07-21"]["hi_f"] == main._display_f(30.0) == 86
    # D2's max is the real 07-22 reading (25C -> 77F), NOT the 30C spike.
    assert by_date["2026-07-22"]["hi_f"] == main._display_f(25.0) == 77
    print("ok matrix_timezone_bucketing_local_beats_utc")


def test_matrix_partial_flag_and_anomaly_decimal(client, monkeypatch):
    now = _dt(2026, 7, 21, 12)
    freeze_now(monkeypatch, now)
    issued = _dt(2026, 7, 21, 7)
    # 20 hourly points on 07-22 local (>=18 -> not partial). Local 07-22 PDT =
    # UTC [07-22 07:00, 07-23 07:00); 07-22T07:00Z + i hours stays inside it.
    base = _dt(2026, 7, 22, 7)
    tgts = [base + datetime.timedelta(hours=i) for i in range(20)]
    rows = [_fc(f"{LAX}_temperature", issued, t, 20.0 + (i % 7))
            for i, t in enumerate(tgts)]
    normals = [{"station_id": LAX, "month": 7, "day": 22,
                "tmax_norm_f": 75.4, "tmin_norm_f": 64.6,
                "window_label": "2011-2025"}]
    use_routes([("forecasts_nws", rows), ("station_normals_daily", normals)])
    d = client.get("/api/weather/temp-matrix").json()
    assert d["window_label"] == "2011-2025"              # carried, not hardcoded
    lax = next(s for s in d["stations"] if s["station_id"] == LAX)
    cell = next(c for c in lax["days"] if c["date_local"] == "2026-07-22")
    assert cell["hours_covered"] == 20 and cell["partial"] is False
    # hi = 26C -> 78.8 -> 79F; anom_hi = 79 - 75.4 = 3.6 (one decimal).
    assert cell["hi_f"] == main._display_f(26.0)
    assert cell["anom_hi"] == round(cell["hi_f"] - 75.4, 1)
    assert isinstance(cell["anom_hi"], float)
    print("ok matrix_partial_flag_and_anomaly_decimal")


# ═══════════════════════════════════════════════════════════════════════════
# regime
# ═══════════════════════════════════════════════════════════════════════════

def test_regime_drivers_never_dropped_and_oni_band(client, monkeypatch):
    freeze_now(monkeypatch, _dt(2026, 7, 21, 12))
    drivers = [
        {"dataset": "cpc_oni_monthly", "series": "oni",
         "ts": _dt(2026, 5, 1), "value": 0.98},
        # Only ONI present -> the other four must still appear (null), never dropped.
    ]
    cpc = [{"product": "610temp", "issued_date": datetime.date(2026, 7, 19),
            "valid_start": datetime.date(2026, 7, 25),
            "valid_end": datetime.date(2026, 7, 29),
            "artifact_format": "shapefile"}]
    depth = [{"depth": datetime.date(2021, 10, 26)}]
    use_routes([("timeseries_values", drivers),
                ("MIN(issued_date)", depth),      # check depth BEFORE the cpc route
                ("cpc_outlook_vintage", cpc)])
    d = client.get("/api/weather/regime").json()
    keys = [x["key"] for x in d["drivers"]]
    assert keys == ["oni", "roni", "pdo", "qbo", "iod_dmi"]   # all five, in order
    oni = d["drivers"][0]
    assert oni["value"] == 0.98 and oni["band"] == "warm"
    assert oni["as_of"] == "2026-05-01"                # UTC month anchor, not April
    assert oni["staleness_days"] == 81
    # Missing drivers surface as null chips, staleness null — displayed, not filtered.
    roni = d["drivers"][1]
    assert roni["value"] is None and roni["band"] is None
    print("ok regime_drivers_never_dropped_and_oni_band")


def test_regime_oni_band_thresholds(monkeypatch):
    assert main._oni_band(0.5) == "warm"
    assert main._oni_band(0.49) == "neutral"
    assert main._oni_band(-0.5) == "cool"
    assert main._oni_band(-0.49) == "neutral"
    assert main._oni_band(None) is None
    print("ok regime_oni_band_thresholds")


def test_regime_cpc_is_metadata_only_lean_pending(client, monkeypatch):
    """P5: CPC ships as vintage metadata with lean=null; no R2 parsing."""
    freeze_now(monkeypatch, _dt(2026, 7, 21, 12))
    cpc = [
        {"product": p, "issued_date": datetime.date(2026, 7, 19),
         "valid_start": datetime.date(2026, 7, 25),
         "valid_end": datetime.date(2026, 7, 29), "artifact_format": "shapefile"}
        for p in ("610temp", "610prcp", "814temp", "814prcp")
    ]
    depth = [{"depth": datetime.date(2021, 10, 26)}]
    use_routes([("timeseries_values", []),
                ("MIN(issued_date)", depth),
                ("cpc_outlook_vintage", cpc)])
    d = client.get("/api/weather/regime").json()
    assert d["cpc"]["depth"] == "2021-10-26"
    fams = [c["family"] for c in d["cpc"]["chips"]]
    assert fams == ["6-10 temp", "6-10 precip", "8-14 temp", "8-14 precip"]
    for c in d["cpc"]["chips"]:
        assert c["lean"] is None
        assert c["lean_status"] == "pending render leg"
        assert c["artifact_format"] == "shapefile"
    print("ok regime_cpc_is_metadata_only_lean_pending")


# ═══════════════════════════════════════════════════════════════════════════
# station-walk
# ═══════════════════════════════════════════════════════════════════════════

def _walk_row(station_id, obs_date, **over):
    base = {"station_id": station_id, "obs_date": obs_date,
            "tavg_f": 72, "hdd": 0, "cdd": 7, "basis_complete": True,
            "tmax_c": 25.0, "tmin_c": 19.4,
            "hdd_norm": 0.0, "cdd_norm": 5.0, "tavg_norm_f": 69.8,
            "sample_count": 225, "window_label": "2011-2025"}
    base.update(over)
    return base


def test_walk_17_rows_and_asof_grammar(client, monkeypatch):
    """A station missing at the shared latest date still appears at ITS OWN
    latest — 17 rows, as-of grammar, never omission."""
    freeze_now(monkeypatch, _dt(2026, 7, 21, 12))
    # LAX current (07-14); San Diego lags to 07-11; the rest absent entirely.
    rows = [
        _walk_row(LAX, datetime.date(2026, 7, 14)),
        _walk_row("USW00023188", datetime.date(2026, 7, 11)),
    ]
    use_routes([("station_degree_days_daily", rows)])
    d = client.get("/api/weather/station-walk").json()
    assert len(d["stations"]) == 17
    assert d["window_label"] == "2011-2025"
    lax = next(s for s in d["stations"] if s["station_id"] == LAX)
    assert lax["obs_date"] == "2026-07-14" and lax["days_behind"] == 7
    assert lax["tmax_f"] == main._display_f(25.0) == 77
    assert lax["anom_tavg"] == round(72 - 69.8, 1) == 2.2
    assert lax["anom_cdd"] == round(7 - 5.0, 1) == 2.0
    # San Diego at its own lagging date — present, not dropped.
    sd = next(s for s in d["stations"] if s["station_id"] == "USW00023188")
    assert sd["obs_date"] == "2026-07-11" and sd["days_behind"] == 10
    # A station with no row at all still appears, as-of null.
    absent = next(s for s in d["stations"] if s["station_id"] == "USW00024157")
    assert absent["obs_date"] is None and absent["days_behind"] is None
    assert absent["tmax_f"] is None
    print("ok walk_17_rows_and_asof_grammar")


if __name__ == "__main__":
    import sys
    c = TestClient(main.app)

    class _MP:
        def setattr(self, obj, name, val):
            setattr(obj, name, val)

    # Minimal runner (pytest gives real monkeypatch; this is a smoke path).
    print("Run via pytest for full fixtures.")
