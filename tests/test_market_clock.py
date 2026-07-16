"""
Tests for the market clock (Ticker v1, Lane 1).

Two layers, mirroring the publication-clock suite:

  1. The pure compute_clock() state machine — every state, both boundaries
     (bid close, the fresh-headline relax), the publication-detected flip, the
     weekend/holiday block vocabulary, the degraded path, and next_expected /
     prints shape. No clock, no DB: `now` is injected.
  2. The /api/market-clock endpoint, exercised through the same in-memory fake
     pool as the sibling suites with main._utcnow monkeypatched so `now` is
     deterministic and publication detection is driven by the faked row.
"""

import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

import main
import market_clock as mc

UTC = datetime.timezone.utc
PT = ZoneInfo("America/Los_Angeles")


def _pt(y, mo, d, h=0, mi=0):
    """A PT wall-clock instant as a tz-aware UTC datetime (July = PDT, -7)."""
    return datetime.datetime(y, mo, d, h, mi, tzinfo=PT).astimezone(UTC)


# ═══════════════════════════════════════════════════════════════════════════
# Layer 1 — pure compute_clock()
# ═══════════════════════════════════════════════════════════════════════════

TARGET = datetime.date(2026, 7, 16)  # a Thursday (on-peak block day)
FMM = {"hub": "SP15", "market": "rtpd", "ts": _pt(2026, 7, 15, 19, 45).isoformat(),
       "price": 41.2}
SP15_PRINT = {"hub": "SP15", "ts": _pt(2026, 7, 16, 16, 0).isoformat(),
              "price": 40.05, "he": 17}


# ---------------------------------------------------------------------------
# One test per state.
# ---------------------------------------------------------------------------

def test_da_bidding_before_bid_close_not_published():
    c = mc.compute_clock(_pt(2026, 7, 15, 8, 0), target_date=TARGET,
                         target_published=False, latest_fmm=FMM)
    assert c.state == mc.DA_BIDDING
    assert c.trade_date == "2026-07-16"
    assert "bidding open" in c.label
    assert c.next_expected["event"] == "bid_close"


def test_da_market_running_after_bid_close_not_published():
    c = mc.compute_clock(_pt(2026, 7, 15, 11, 0), target_date=TARGET,
                         target_published=False, latest_fmm=FMM)
    assert c.state == mc.DA_MARKET_RUNNING
    assert "awards pending" in c.label
    assert c.next_expected["event"] == "da_publish"


def test_da_published_when_rows_present_within_fresh_window():
    c = mc.compute_clock(_pt(2026, 7, 15, 16, 0), target_date=TARGET,
                         target_published=True, da_published_at=_pt(2026, 7, 15, 15, 36),
                         sp15_da_print=SP15_PRINT, latest_fmm=FMM)
    assert c.state == mc.DA_PUBLISHED
    assert "DA awards published 15:36 PT" == c.label
    assert "SP15 DA $40.05 HE17" in c.detail
    assert c.prints["sp15_da"] == SP15_PRINT
    assert c.next_expected["event"] == "bid_close"


def test_rt_live_when_published_and_fresh_window_elapsed():
    c = mc.compute_clock(_pt(2026, 7, 15, 19, 0), target_date=TARGET,
                         target_published=True, da_published_at=_pt(2026, 7, 15, 15, 36),
                         sp15_da_print=SP15_PRINT, latest_fmm=FMM)
    assert c.state == mc.RT_LIVE
    assert c.label == "Real-time market live"
    assert "FMM SP15 $41.20" in c.detail
    assert "as-of 19:45 PT" in c.detail


# ---------------------------------------------------------------------------
# Boundaries (both directions).
# ---------------------------------------------------------------------------

def test_bid_close_boundary_is_strict():
    # 09:59 -> still bidding; 10:00 exactly -> running.
    assert mc.compute_clock(_pt(2026, 7, 15, 9, 59), target_date=TARGET,
                            target_published=False).state == mc.DA_BIDDING
    assert mc.compute_clock(_pt(2026, 7, 15, 10, 0), target_date=TARGET,
                            target_published=False).state == mc.DA_MARKET_RUNNING


def test_fresh_headline_boundary_is_strict():
    # published: 17:59 -> still the DA_PUBLISHED headline; 18:00 -> relaxes to RT_LIVE.
    assert mc.compute_clock(_pt(2026, 7, 15, 17, 59), target_date=TARGET,
                            target_published=True, latest_fmm=FMM).state == mc.DA_PUBLISHED
    assert mc.compute_clock(_pt(2026, 7, 15, 18, 0), target_date=TARGET,
                            target_published=True, latest_fmm=FMM).state == mc.RT_LIVE


def test_publication_detection_dominates_the_clock():
    # Same instant (16:00, past bid close): the ONLY difference is whether the lake
    # holds the rows. Detection flips running -> published — the honesty rail.
    now = _pt(2026, 7, 15, 16, 0)
    assert mc.compute_clock(now, target_date=TARGET,
                            target_published=False).state == mc.DA_MARKET_RUNNING
    assert mc.compute_clock(now, target_date=TARGET, target_published=True,
                            latest_fmm=FMM).state == mc.DA_PUBLISHED


# ---------------------------------------------------------------------------
# Weekend / holiday — block vocabulary in the detail, cycle NEVER suppressed.
# ---------------------------------------------------------------------------

def test_weekend_holiday_flavours_detail_but_keeps_the_cycle():
    # Sunday target: off-peak all day, but bidding still runs (rows prove it later).
    sun = datetime.date(2026, 7, 5)
    c = mc.compute_clock(_pt(2026, 7, 4, 8, 0), target_date=sun,
                         target_published=False, latest_fmm=FMM,
                         target_is_offpeak_all_day=True)
    assert c.state == mc.DA_BIDDING              # cycle NOT suppressed
    assert "off-peak all hours" in c.detail
    assert "Sun" in c.detail


def test_weekday_carries_the_onpeak_block():
    c = mc.compute_clock(_pt(2026, 7, 15, 8, 0), target_date=TARGET,
                         target_published=False, target_is_offpeak_all_day=False)
    assert "on-peak HE7–HE22" in c.detail
    assert "Thu" in c.detail


# ---------------------------------------------------------------------------
# Degraded path + prints shape.
# ---------------------------------------------------------------------------

def test_degraded_names_the_stale_feed():
    c = mc.compute_clock(_pt(2026, 7, 15, 19, 0), target_date=TARGET,
                         target_published=True, latest_fmm=None,
                         degraded_feeds=["rtpd"])
    assert c.degraded is True
    assert c.degraded_feeds == ["rtpd"]


def test_overdue_dam_annotated_when_flagged():
    c = mc.compute_clock(_pt(2026, 7, 15, 15, 0), target_date=TARGET,
                         target_published=False, degraded_feeds=["da"])
    assert c.state == mc.DA_MARKET_RUNNING
    assert "overdue" in c.detail
    assert c.degraded is True


def test_latest_fmm_always_present_sp15_da_only_when_published():
    running = mc.compute_clock(_pt(2026, 7, 15, 11, 0), target_date=TARGET,
                               target_published=False, latest_fmm=FMM)
    assert running.prints["latest_fmm"] == FMM
    assert running.prints["sp15_da"] is None      # awards not out


def test_next_expected_at_is_null_when_publish_time_has_passed():
    c = mc.compute_clock(_pt(2026, 7, 15, 15, 0), target_date=TARGET,
                         target_published=False)
    assert c.state == mc.DA_MARKET_RUNNING
    assert c.next_expected["event"] == "da_publish"
    assert c.next_expected["at"] is None          # 15:00 > expected 13:00


def test_unknown_type_never_raised_as_of_is_utc():
    c = mc.compute_clock(_pt(2026, 7, 15, 19, 0), target_date=TARGET,
                         target_published=True, latest_fmm=FMM)
    assert c.as_of.endswith("+00:00")


# ═══════════════════════════════════════════════════════════════════════════
# Layer 2 — the /api/market-clock endpoint (fake pool, monkeypatched clock)
# ═══════════════════════════════════════════════════════════════════════════

class _FakeCursor:
    def __init__(self, row, sink):
        self._row, self._sink = row, sink

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, query, params=None):
        self._sink["query"], self._sink["params"] = query, params

    async def fetchone(self):
        return self._row


class _FakeConn:
    def __init__(self, row, sink):
        self._row, self._sink = row, sink

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def cursor(self):
        return _FakeCursor(self._row, self._sink)


class FakePool:
    def __init__(self, row):
        self._row = row
        self.sink = {}

    def connection(self):
        return _FakeConn(self._row, self.sink)


@pytest.fixture
def client():
    # No `with` block => lifespan never runs => the real pool is never opened.
    return TestClient(main.app)


def _install(row, now):
    pool = FakePool(row)
    main._pool = pool
    main._utcnow = lambda: now
    return pool


def test_endpoint_rt_live_at_the_real_dispatch_moment(client):
    # 2026-07-15 19:27 PT: tomorrow (07-16) has 24 DA hours, fresh FMM -> RT_LIVE.
    _install(
        {
            "da_hours": 24,
            "da_published_at": _pt(2026, 7, 15, 15, 36),
            "sp15_da_val": 40.05,
            "fmm_ts": _pt(2026, 7, 15, 19, 45),
            "fmm_val": 41.2,
        },
        now=_pt(2026, 7, 15, 19, 27),
    )
    r = client.get("/api/market-clock")
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "RT_LIVE"
    assert body["trade_date"] == "2026-07-16"
    assert body["prints"]["sp15_da"]["price"] == 40.05
    assert body["prints"]["sp15_da"]["he"] == 17
    assert body["prints"]["latest_fmm"]["price"] == 41.2
    assert body["degraded"] is False
    assert body["sources"] == ["caiso_lmp_da_hourly", "caiso_lmp_rt_15min"]


def test_endpoint_da_bidding_before_bid_close(client):
    # 08:00 PT, tomorrow's rows absent -> bidding.
    _install(
        {"da_hours": 0, "da_published_at": None, "sp15_da_val": None,
         "fmm_ts": _pt(2026, 7, 16, 7, 55), "fmm_val": 33.0},
        now=_pt(2026, 7, 16, 8, 0),
    )
    body = client.get("/api/market-clock").json()
    assert body["state"] == "DA_BIDDING"
    assert body["trade_date"] == "2026-07-17"
    assert body["prints"]["sp15_da"] is None


def test_endpoint_da_market_running_after_close(client):
    _install(
        {"da_hours": 0, "da_published_at": None, "sp15_da_val": None,
         "fmm_ts": _pt(2026, 7, 16, 10, 55), "fmm_val": 35.0},
        now=_pt(2026, 7, 16, 11, 0),
    )
    body = client.get("/api/market-clock").json()
    assert body["state"] == "DA_MARKET_RUNNING"


def test_endpoint_flags_stale_fmm(client):
    # FMM two hours old -> rtpd degraded (state still resolves normally).
    _install(
        {"da_hours": 24, "da_published_at": _pt(2026, 7, 15, 15, 36),
         "sp15_da_val": 40.05, "fmm_ts": _pt(2026, 7, 15, 17, 0), "fmm_val": 41.2},
        now=_pt(2026, 7, 15, 19, 0),
    )
    body = client.get("/api/market-clock").json()
    assert body["degraded"] is True
    assert "rtpd" in body["degraded_feeds"]


def test_endpoint_partial_da_write_is_not_published(client):
    # 12 of 24 hours present -> below the floor -> still running, not published.
    _install(
        {"da_hours": 12, "da_published_at": _pt(2026, 7, 15, 15, 36),
         "sp15_da_val": None, "fmm_ts": _pt(2026, 7, 15, 15, 55), "fmm_val": 41.2},
        now=_pt(2026, 7, 15, 15, 0),
    )
    body = client.get("/api/market-clock").json()
    assert body["state"] == "DA_MARKET_RUNNING"
