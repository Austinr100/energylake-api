"""
Tests for the Weather Phase 2 delta board (2026-07-26):
  GET /api/weather/delta-board

The board's whole build is the pure composition in main._compute_delta_board
(synoptic bucketing, per-region walk-back, shared-hours daily-max delta, and the
re-issue-neutral streak), so most tests exercise that function directly with
hand-built forecasts_nws region rows and an injected `now` — no DB, no clock.
Two route-level tests cover the SQL/cache wiring and the DB-down 503 via a fake
pool, mirroring test_weather_tiles.py.
"""

import datetime

import pytest
from fastapi.testclient import TestClient
from zoneinfo import ZoneInfo

import main


UTC = datetime.timezone.utc
NOW = datetime.datetime(2026, 7, 26, 15, 0, tzinfo=UTC)  # 08:00 PDT → LA today 07-26
LA = "America/Los_Angeles"


def _d(y, mo, dd):
    return datetime.date(y, mo, dd)


def _rows(region, issued_ts, date_values, tz_name=LA,
          coverage=1.0, max_age=2.0, hours=range(0, 24)):
    """Build hourly forecasts_nws rows for one region issuance.

    `date_values` maps a region-local date → the constant °F value assigned to
    every hour of that local day (so the day's max is exactly that value, and two
    issuances built the same way share their full hour grid). `hours` selects
    which local hours-of-day to emit (trim it to force a thin shared overlap)."""
    tz = ZoneInfo(tz_name)
    out = []
    for d, v in date_values.items():
        local_midnight = datetime.datetime(d.year, d.month, d.day, tzinfo=tz)
        for h in hours:
            target = (local_midnight + datetime.timedelta(hours=h)).astimezone(UTC)
            out.append({
                "series": region, "issued_ts": issued_ts, "target_ts": target,
                "value": float(v), "coverage": coverage, "max_age_hours": max_age,
            })
    return out


def _seven(base_value):
    """A flat 7-day date→value map for D1..D7 (07-26 … 08-01)."""
    return {_d(2026, 7, 26 + i) if 26 + i <= 31 else _d(2026, 8, 26 + i - 31): base_value
            for i in range(7)}


def _dt(y, mo, dd, h=0, mi=0):
    return datetime.datetime(y, mo, dd, h, mi, tzinfo=UTC)


def _region(payload, name):
    return next(r for r in payload["regions"] if r["region"] == name)


def _day(region_obj, iso_date):
    return next(d for d in region_obj["days"] if d["date"] == iso_date)


# ---------------------------------------------------------------------------
# Structural guarantees
# ---------------------------------------------------------------------------

def test_always_17_regions_in_yaml_order():
    payload = main._compute_delta_board([], NOW)
    names = [r["region"] for r in payload["regions"]]
    assert names == [r for r, _ in main._LWT_REGIONS]
    assert len(names) == 17
    # CAISO TACs lead, then WECC BAs (matches the yaml ordering contract).
    assert names[:4] == ["PGE-TAC", "SCE-TAC", "SDGE-TAC", "VEA-TAC"]


def test_empty_region_degrades_to_no_data_not_error():
    payload = main._compute_delta_board([], NOW)
    reg = _region(payload, "SCE-TAC")
    assert reg["current_bucket"] is None
    assert reg["prior_bucket"] is None
    assert len(reg["days"]) == 7
    assert all(d["delta_f"] is None for d in reg["days"])
    assert all(d["streak"] == 0 for d in reg["days"])


def test_single_bucket_region_has_no_prior_all_no_data():
    rows = _rows("BPAT", _dt(2026, 7, 26, 12, 25), _seven(80))
    reg = _region(main._compute_delta_board(rows, NOW), "BPAT")
    assert reg["current_bucket"] == _dt(2026, 7, 26, 12).isoformat()
    assert reg["current_issued_ts"] == _dt(2026, 7, 26, 12, 25).isoformat()
    assert reg["prior_bucket"] is None
    assert all(d["delta_f"] is None for d in reg["days"])


# ---------------------------------------------------------------------------
# Bucketing
# ---------------------------------------------------------------------------

def test_latest_event_in_a_bucket_wins():
    # Two events inside the same 12Z window; the later (12:50) must win.
    early = _rows("BPAT", _dt(2026, 7, 26, 12, 5), _seven(80))
    late = _rows("BPAT", _dt(2026, 7, 26, 12, 50), _seven(83))
    prior = _rows("BPAT", _dt(2026, 7, 26, 6, 30), _seven(80))
    reg = _region(main._compute_delta_board(early + late + prior, NOW), "BPAT")
    assert reg["current_issued_ts"] == _dt(2026, 7, 26, 12, 50).isoformat()
    # D2 (full day 07-27): current 83 − prior 80 = +3.
    assert _day(reg, "2026-07-27")["delta_f"] == 3.0


def test_synoptic_bucket_floors_to_00_06_12_18():
    assert main._synoptic_bucket(_dt(2026, 7, 26, 13, 41)) == _dt(2026, 7, 26, 12)
    assert main._synoptic_bucket(_dt(2026, 7, 26, 5, 59)) == _dt(2026, 7, 26, 0)
    assert main._synoptic_bucket(_dt(2026, 7, 26, 18, 0)) == _dt(2026, 7, 26, 18)
    assert main._synoptic_bucket(_dt(2026, 7, 26, 23, 30)) == _dt(2026, 7, 26, 18)


# ---------------------------------------------------------------------------
# Cell delta + shared-hours honesty
# ---------------------------------------------------------------------------

def test_cell_delta_is_daily_max_over_shared_hours():
    cur = _rows("BPAT", _dt(2026, 7, 26, 12, 25), _seven(88))
    pri = _rows("BPAT", _dt(2026, 7, 26, 6, 30), _seven(85))
    reg = _region(main._compute_delta_board(cur + pri, NOW), "BPAT")
    d = _day(reg, "2026-07-28")  # a full, non-leading day
    assert d["delta_f"] == 3.0
    assert d["current_max_f"] == 88.0
    assert d["prior_max_f"] == 85.0
    assert d["shared_hours"] == 24


def test_reissue_is_delta_zero_not_no_data():
    # Byte-identical current & prior compositions (the NWS updateTime re-stamp):
    # every cell is an honest 0.0, distinct from a null no-data cell.
    cur = _rows("BPAT", _dt(2026, 7, 26, 12, 25), _seven(85))
    pri = _rows("BPAT", _dt(2026, 7, 26, 6, 30), _seven(85))
    reg = _region(main._compute_delta_board(cur + pri, NOW), "BPAT")
    for day in reg["days"]:
        assert day["delta_f"] == 0.0
        assert day["delta_f"] is not None


def test_thin_overlap_on_full_day_is_no_data_leading_day_exempt():
    # Current covers only hours 0..5 of each day; prior covers 0..23. The shared
    # overlap is 6h — below the 12h full-day floor. D1 (07-26, the leading
    # partial day) is EXEMT and still returns a delta; the full days go no-data.
    cur = _rows("BPAT", _dt(2026, 7, 26, 12, 25), _seven(88), hours=range(0, 6))
    pri = _rows("BPAT", _dt(2026, 7, 26, 6, 30), _seven(85), hours=range(0, 24))
    reg = _region(main._compute_delta_board(cur + pri, NOW), "BPAT")
    assert _day(reg, "2026-07-26")["delta_f"] == 3.0     # leading day, exempt
    assert _day(reg, "2026-07-26")["shared_hours"] == 6
    assert _day(reg, "2026-07-27")["delta_f"] is None    # full day, thin → no-data
    assert _day(reg, "2026-07-28")["delta_f"] is None


# ---------------------------------------------------------------------------
# Walk-back cap
# ---------------------------------------------------------------------------

def test_prior_beyond_24h_walkback_is_no_data():
    # Prior populated bucket is 30h before current (> the 4-bucket / 24h cap).
    cur = _rows("BPAT", _dt(2026, 7, 26, 12, 25), _seven(88))
    old = _rows("BPAT", _dt(2026, 7, 25, 6, 25), _seven(80))
    reg = _region(main._compute_delta_board(cur + old, NOW), "BPAT")
    assert reg["current_bucket"] == _dt(2026, 7, 26, 12).isoformat()
    assert reg["prior_bucket"] is None
    assert all(d["delta_f"] is None for d in reg["days"])


# ---------------------------------------------------------------------------
# Streak
# ---------------------------------------------------------------------------

def _warming_series(region="BPAT", target=_d(2026, 7, 29)):
    """Five buckets 6h apart; `target` warms 80→82→85(→85 re-issue)→88.
    Every other date is flat 70 so only `target` carries a streak."""
    def dv(v):
        m = _seven(70)
        m[target] = v
        return m
    return (
        _rows(region, _dt(2026, 7, 25, 6, 30), dv(80))
        + _rows(region, _dt(2026, 7, 25, 12, 30), dv(82))   # +2 warmer
        + _rows(region, _dt(2026, 7, 25, 18, 30), dv(85))   # +3 warmer
        + _rows(region, _dt(2026, 7, 26, 0, 30), dv(85))    # 0 re-issue (neutral)
        + _rows(region, _dt(2026, 7, 26, 6, 30), dv(88))    # +3 warmer
    )


def test_streak_counts_same_sign_skipping_reissue_zero():
    reg = _region(main._compute_delta_board(_warming_series(), NOW), "BPAT")
    d = _day(reg, "2026-07-29")
    # current (06Z, 88) vs prior (00Z, 85) = +3.
    assert d["delta_f"] == 3.0
    # Run back: +3, [0 skipped], +3, +2 → 3 consecutive warmer.
    assert d["streak"] == 3
    assert d["streak_dir"] == "warmer"


def test_streak_survives_a_reissue_current_bucket():
    # Append a 12Z re-issue (88 again) as the current bucket → cell Δ0, but the
    # standing 3-run warming trend still shows through the neutral re-issue.
    target = _d(2026, 7, 29)
    def dv(v):
        m = _seven(70)
        m[target] = v
        return m
    rows = _warming_series() + _rows("BPAT", _dt(2026, 7, 26, 12, 30), dv(88))
    reg = _region(main._compute_delta_board(rows, NOW), "BPAT")
    d = _day(reg, "2026-07-29")
    assert d["delta_f"] == 0.0        # re-issue: honest no-change this run
    assert d["streak"] == 3           # trend intact
    assert d["streak_dir"] == "warmer"


def test_streak_resets_on_sign_flip():
    target = _d(2026, 7, 29)
    def dv(v):
        m = _seven(70)
        m[target] = v
        return m
    rows = (
        _rows("BPAT", _dt(2026, 7, 25, 6, 30), dv(80))
        + _rows("BPAT", _dt(2026, 7, 25, 12, 30), dv(85))   # +5 warmer
        + _rows("BPAT", _dt(2026, 7, 25, 18, 30), dv(90))   # +5 warmer
        + _rows("BPAT", _dt(2026, 7, 26, 0, 30), dv(87))    # −3 cooler (flip)
    )
    d = _day(_region(main._compute_delta_board(rows, NOW), "BPAT"), "2026-07-29")
    assert d["delta_f"] == -3.0
    assert d["streak"] == 1
    assert d["streak_dir"] == "cooler"


def test_streak_display_capped_at_five():
    target = _d(2026, 7, 29)
    def dv(v):
        m = _seven(70)
        m[target] = v
        return m
    # Eight strictly-warming buckets, 6h apart, ending at 07-26 06Z.
    starts = [
        _dt(2026, 7, 24, 12, 30), _dt(2026, 7, 24, 18, 30),
        _dt(2026, 7, 25, 0, 30), _dt(2026, 7, 25, 6, 30),
        _dt(2026, 7, 25, 12, 30), _dt(2026, 7, 25, 18, 30),
        _dt(2026, 7, 26, 0, 30), _dt(2026, 7, 26, 6, 30),
    ]
    rows = []
    for i, iss in enumerate(starts):
        rows += _rows("BPAT", iss, dv(70 + i))   # +1 each run
    d = _day(_region(main._compute_delta_board(rows, NOW), "BPAT"), "2026-07-29")
    assert d["streak"] == 5            # capped from 7 to the 5+ display cap
    assert d["streak_dir"] == "warmer"


def test_gap_over_24h_ends_streak():
    target = _d(2026, 7, 29)
    def dv(v):
        m = _seven(70)
        m[target] = v
        return m
    rows = (
        _rows("BPAT", _dt(2026, 7, 24, 6, 30), dv(70))
        + _rows("BPAT", _dt(2026, 7, 25, 18, 30), dv(80))   # 36h gap before this
        + _rows("BPAT", _dt(2026, 7, 26, 0, 30), dv(83))    # +3 warmer
        + _rows("BPAT", _dt(2026, 7, 26, 6, 30), dv(86))    # +3 warmer
    )
    d = _day(_region(main._compute_delta_board(rows, NOW), "BPAT"), "2026-07-29")
    # Only the two post-gap deltas count; the 36h gap severs the older record.
    assert d["streak"] == 2


# ---------------------------------------------------------------------------
# Region-local day boundary + passthrough
# ---------------------------------------------------------------------------

def test_region_local_tz_used_for_day_boundary():
    # AZPS is America/Phoenix (UTC-7, no DST). The same wall-clock instant lands
    # in a different local column than a Pacific region would only near midnight;
    # here we simply assert the region carries its own tz and 7 local dates.
    rows = _rows("AZPS", _dt(2026, 7, 26, 12, 25), _seven(100), tz_name="America/Phoenix")
    rows += _rows("AZPS", _dt(2026, 7, 26, 6, 25), _seven(98), tz_name="America/Phoenix")
    reg = _region(main._compute_delta_board(rows, NOW), "AZPS")
    assert reg["tz"] == "America/Phoenix"
    assert reg["days"][0]["date"] == "2026-07-26"
    assert _day(reg, "2026-07-27")["delta_f"] == 2.0


def test_coverage_and_max_age_passthrough_from_current_bucket():
    cur = _rows("BPAT", _dt(2026, 7, 26, 12, 25), _seven(88), coverage=0.7, max_age=9.5)
    pri = _rows("BPAT", _dt(2026, 7, 26, 6, 30), _seven(85), coverage=1.0, max_age=2.0)
    reg = _region(main._compute_delta_board(cur + pri, NOW), "BPAT")
    assert reg["coverage"] == 0.7
    assert reg["max_age_hours"] == 9.5


def test_header_current_and_prior_bucket_labels():
    # Region A latest at 12Z (prior 06Z); region B only reaches 06Z. The board's
    # current bucket is the freshest (12Z) and its prior the modal 06Z.
    a = (_rows("BPAT", _dt(2026, 7, 26, 12, 25), _seven(88))
         + _rows("BPAT", _dt(2026, 7, 26, 6, 25), _seven(85)))
    b = (_rows("PACE", _dt(2026, 7, 26, 6, 20), _seven(90), tz_name="America/Denver")
         + _rows("PACE", _dt(2026, 7, 26, 0, 20), _seven(88), tz_name="America/Denver"))
    payload = main._compute_delta_board(a + b, NOW)
    assert payload["current_bucket"] == _dt(2026, 7, 26, 12).isoformat()
    assert payload["prior_bucket"] == _dt(2026, 7, 26, 6).isoformat()


# ---------------------------------------------------------------------------
# Route wiring: cache + 503, via a fake pool (test_weather_tiles idiom)
# ---------------------------------------------------------------------------

class _FakeCursor:
    def __init__(self, rows, fail):
        self._rows, self._fail = rows, fail

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, query, params=None):
        if self._fail:
            raise RuntimeError("boom")

    async def fetchall(self):
        return list(self._rows)


class _FakeConn:
    def __init__(self, rows, fail):
        self._rows, self._fail = rows, fail

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def cursor(self):
        return _FakeCursor(self._rows, self._fail)


class _FakePool:
    def __init__(self, rows, fail=False):
        self._rows, self._fail = rows, fail

    def connection(self):
        return _FakeConn(self._rows, self._fail)


@pytest.fixture(autouse=True)
def _clear_cache():
    main._delta_board_cache.clear()
    yield
    main._delta_board_cache.clear()


def test_route_returns_board_and_caches(monkeypatch):
    monkeypatch.setattr(main, "_utcnow", lambda: NOW)
    rows = (_rows("BPAT", _dt(2026, 7, 26, 12, 25), _seven(88))
            + _rows("BPAT", _dt(2026, 7, 26, 6, 30), _seven(85)))
    monkeypatch.setattr(main, "_pool", _FakePool(rows))
    client = TestClient(main.app)
    resp = client.get("/api/weather/delta-board")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["regions"]) == 17
    assert _day(_region(body, "BPAT"), "2026-07-28")["delta_f"] == 3.0
    assert resp.headers.get("Cache-Control") == "max-age=60"
    # Second call served from cache even if the pool would now fail.
    monkeypatch.setattr(main, "_pool", _FakePool([], fail=True))
    assert client.get("/api/weather/delta-board").status_code == 200


def test_route_503_on_db_error(monkeypatch):
    monkeypatch.setattr(main, "_utcnow", lambda: NOW)
    monkeypatch.setattr(main, "_pool", _FakePool([], fail=True))
    client = TestClient(main.app)
    assert client.get("/api/weather/delta-board").status_code == 503
