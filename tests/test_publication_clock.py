"""
Tests for the publication clock (PR-1 of the brief-freshness arc).

Two layers:

  1. The pure compute_status() state machine — every banner state, the strict
     grace boundary in both directions, per-type schedule/offset, and the
     unscheduled hatch (unknown type never raises). No clock, no DB: `now` is
     injected.
  2. The additive `publication` rider on the three brief endpoints
     (/api/briefs/daily/latest, /api/briefs/daily/{date}, /api/joule/chart-brief),
     exercised through the same in-memory fake pool as the other suites with
     main._utcnow monkeypatched so `now` is deterministic.
"""

import datetime

import pytest
from fastapi.testclient import TestClient

import main
import publication_clock as pc

UTC = datetime.timezone.utc


def _dt(y, mo, d, h=0, mi=0):
    return datetime.datetime(y, mo, d, h, mi, tzinfo=UTC)


# ═══════════════════════════════════════════════════════════════════════════
# Layer 1 — pure compute_status()
# ═══════════════════════════════════════════════════════════════════════════

# ---------------------------------------------------------------------------
# One test per banner state (daily: expected 05:00 UTC, grace 60m, offset +1).
# At now = 2026-07-13T06:00 the current trade date is 2026-07-12.
# ---------------------------------------------------------------------------

def test_current_when_brief_covers_todays_trade_date():
    st = pc.compute_status("daily", datetime.date(2026, 7, 12), _dt(2026, 7, 13, 6, 0))
    assert st.status == pc.CURRENT
    assert st.trade_date == "2026-07-12"


def test_pending_when_todays_edition_due_but_within_grace():
    # now = 05:59 (expected + 59m): today's edition (trade date 07-12) is not
    # here yet — latest is the prior edition (07-11) — but grace has not lapsed.
    st = pc.compute_status("daily", datetime.date(2026, 7, 11), _dt(2026, 7, 13, 5, 59))
    assert st.status == pc.PENDING


def test_overdue_when_grace_has_lapsed():
    # now = 06:01 (expected + 61m): today's edition still missing, grace lapsed.
    st = pc.compute_status("daily", datetime.date(2026, 7, 11), _dt(2026, 7, 13, 6, 1))
    assert st.status == pc.OVERDUE


def test_unscheduled_for_rolling_intraday_type():
    st = pc.compute_status(
        "caiso_load_deviation", datetime.date(2026, 7, 12), _dt(2026, 7, 13, 6, 0)
    )
    assert st.status == pc.UNSCHEDULED
    assert st.next_expected_at is None


# ---------------------------------------------------------------------------
# Strict grace boundary, both directions (expected 05:00 + grace 60m).
# +59 -> pending, +61 -> overdue; +60 exactly -> overdue (now < deadline is
# strict, so the deadline instant itself is already overdue).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "minutes, expected",
    [
        (59, pc.PENDING),
        (60, pc.OVERDUE),
        (61, pc.OVERDUE),
    ],
)
def test_grace_boundary_is_strict(minutes, expected):
    now = _dt(2026, 7, 13, 5, 0) + datetime.timedelta(minutes=minutes)
    # Latest edition is the prior day's, so today's is the one being clocked.
    st = pc.compute_status("daily", datetime.date(2026, 7, 11), now)
    assert st.status == expected


# ---------------------------------------------------------------------------
# Per-type offset / expected-time: the SAME (brief_date, now) resolves to
# different states because daily (05:00) and fuel-mix (12:00) run on different
# clocks. At now = 2026-07-13T11:00, before the fuel-mix 12:00 run:
#   * daily     -> current trade date is 07-12; a 07-11 brief is overdue.
#   * fuel-mix  -> current run is still yesterday's 12:00, trade date 07-11;
#                  a 07-11 brief is current.
# ---------------------------------------------------------------------------

def test_per_type_schedule_diverges_on_same_inputs():
    brief_date = datetime.date(2026, 7, 11)
    now = _dt(2026, 7, 13, 11, 0)
    daily = pc.compute_status("daily", brief_date, now)
    fuel = pc.compute_status("caiso_fuel_mix_chart", brief_date, now)
    assert daily.status == pc.OVERDUE
    assert fuel.status == pc.CURRENT


def test_fuel_mix_offset_applies():
    # After the 12:00 run, fuel-mix current trade date is 07-12 (run_date - 1).
    st = pc.compute_status(
        "caiso_fuel_mix_chart", datetime.date(2026, 7, 12), _dt(2026, 7, 13, 13, 0)
    )
    assert st.status == pc.CURRENT
    assert st.trade_date == "2026-07-12"


# ---------------------------------------------------------------------------
# Unscheduled hatch: an unknown/unmapped type must never raise — it resolves
# to `unscheduled`, exactly like the deliberately-unscheduled entries.
# ---------------------------------------------------------------------------

def test_unknown_type_never_raises():
    st = pc.compute_status(
        "totally_made_up_type", datetime.date(2026, 7, 12), _dt(2026, 7, 13, 6, 0)
    )
    assert st.status == pc.UNSCHEDULED
    assert st.next_expected_at is None


# ---------------------------------------------------------------------------
# caiso_hub_lmp is now scheduled (D-07-13-11): expected 10:23 UTC, grace 60m,
# offset 0 (brief_date = run_date — hub briefs write today's date, not prior).
# Same coverage the other scheduled types carry: current / pending / overdue,
# and the strict grace boundary (expected+59 = pending, expected+61 = overdue).
# ---------------------------------------------------------------------------

def test_hub_lmp_current_when_brief_covers_todays_run_date():
    # After the 10:23 run, offset 0 means today's trade date is the run date.
    st = pc.compute_status(
        "caiso_hub_lmp", datetime.date(2026, 7, 13), _dt(2026, 7, 13, 11, 0)
    )
    assert st.status == pc.CURRENT
    assert st.trade_date == "2026-07-13"
    assert st.next_expected_at == "2026-07-14T10:23:00+00:00"


def test_hub_lmp_pending_within_grace():
    # now = 11:22 (expected 10:23 + 59m): today's edition due, grace not lapsed.
    st = pc.compute_status(
        "caiso_hub_lmp", datetime.date(2026, 7, 12), _dt(2026, 7, 13, 11, 22)
    )
    assert st.status == pc.PENDING


def test_hub_lmp_overdue_after_grace():
    # now = 11:24 (expected 10:23 + 61m): today's edition missing, grace lapsed.
    st = pc.compute_status(
        "caiso_hub_lmp", datetime.date(2026, 7, 12), _dt(2026, 7, 13, 11, 24)
    )
    assert st.status == pc.OVERDUE


@pytest.mark.parametrize(
    "minutes, expected",
    [
        (59, pc.PENDING),
        (60, pc.OVERDUE),
        (61, pc.OVERDUE),
    ],
)
def test_hub_lmp_grace_boundary_is_strict(minutes, expected):
    now = _dt(2026, 7, 13, 10, 23) + datetime.timedelta(minutes=minutes)
    st = pc.compute_status("caiso_hub_lmp", datetime.date(2026, 7, 12), now)
    assert st.status == expected


# ---------------------------------------------------------------------------
# Clock fields: before/after expected flips both the current trade date and the
# next_expected_at, and an edition published ahead of schedule reads current.
# ---------------------------------------------------------------------------

def test_next_expected_flips_at_expected_time():
    before = pc.compute_status("daily", datetime.date(2026, 7, 12), _dt(2026, 7, 13, 4, 0))
    after = pc.compute_status("daily", datetime.date(2026, 7, 12), _dt(2026, 7, 13, 6, 0))
    # Before 05:00 the next run is today's 05:00; after, it is tomorrow's.
    assert before.next_expected_at == "2026-07-13T05:00:00+00:00"
    assert after.next_expected_at == "2026-07-14T05:00:00+00:00"


def test_before_expected_prior_edition_is_current():
    # At 04:00, today's run hasn't fired; the current trade date is 07-11, and
    # yesterday's edition (07-11) is still current.
    st = pc.compute_status("daily", datetime.date(2026, 7, 11), _dt(2026, 7, 13, 4, 0))
    assert st.status == pc.CURRENT


def test_edition_ahead_of_expected_is_current():
    # A brief newer than today's trade date (published early) is current, not a
    # phantom "behind" state.
    st = pc.compute_status("daily", datetime.date(2026, 7, 13), _dt(2026, 7, 13, 6, 0))
    assert st.status == pc.CURRENT


def test_no_brief_is_pending_or_overdue_never_current():
    pending = pc.compute_status("daily", None, _dt(2026, 7, 13, 5, 30))
    overdue = pc.compute_status("daily", None, _dt(2026, 7, 13, 7, 0))
    assert pending.status == pc.PENDING
    assert overdue.status == pc.OVERDUE


def test_published_at_echoes_created_at():
    st = pc.compute_status(
        "daily",
        datetime.date(2026, 7, 12),
        _dt(2026, 7, 13, 6, 0),
        published_at=_dt(2026, 7, 13, 5, 2),
    )
    assert st.published_at == "2026-07-13T05:02:00+00:00"


# ═══════════════════════════════════════════════════════════════════════════
# Layer 2 — the additive rider on the endpoints
# ═══════════════════════════════════════════════════════════════════════════

class _FakeCursor:
    def __init__(self, rows, sink):
        self._rows = rows
        self._sink = sink

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, query, params=None):
        self._sink["query"] = query
        self._sink["params"] = params

    async def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeConn:
    def __init__(self, rows, sink):
        self._rows = rows
        self._sink = sink

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def cursor(self):
        return _FakeCursor(self._rows, self._sink)


class FakePool:
    def __init__(self, rows):
        self._rows = rows
        self.sink = {}

    def connection(self):
        return _FakeConn(self._rows, self.sink)


@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.fixture
def frozen_now(monkeypatch):
    """Freeze main._utcnow so the rider's status is deterministic in a request."""

    def _freeze(now):
        monkeypatch.setattr(main, "_utcnow", lambda: now)

    return _freeze


def use_rows(rows):
    pool = FakePool(rows)
    main._pool = pool
    return pool


def _daily_row(brief_date, created_at):
    return {
        "brief_date": brief_date,
        "headline": "Grid tightens into summer",
        "content_md": "# Heading\n\nbody",
        "word_count": 412,
        "voice_version": "v3",
        "created_at": created_at,
    }


def _chart_row(brief_type, brief_date, created_at):
    return {
        "id": 18,
        "brief_type": brief_type,
        "brief_date": brief_date,
        "content_md": "Solar carried the midday peak.",
        "voice_version": "v2.1",
        "word_count": 71,
        "created_at": created_at,
    }


def test_daily_latest_carries_current_rider(client, frozen_now):
    frozen_now(_dt(2026, 7, 13, 6, 0))
    use_rows([_daily_row(datetime.date(2026, 7, 12), _dt(2026, 7, 13, 5, 2))])
    resp = client.get("/api/briefs/daily/latest")
    assert resp.status_code == 200
    pub = resp.json()["publication"]
    assert pub == {
        "status": "current",
        "trade_date": "2026-07-12",
        "published_at": "2026-07-13T05:02:00+00:00",
        "next_expected_at": "2026-07-14T05:00:00+00:00",
    }


def test_daily_latest_overdue_rider(client, frozen_now):
    frozen_now(_dt(2026, 7, 13, 7, 0))
    use_rows([_daily_row(datetime.date(2026, 7, 11), _dt(2026, 7, 12, 5, 2))])
    resp = client.get("/api/briefs/daily/latest")
    assert resp.json()["publication"]["status"] == "overdue"


def test_daily_by_date_carries_rider(client, frozen_now):
    # An explicitly-requested older date reads overdue against today's clock.
    frozen_now(_dt(2026, 7, 13, 6, 0))
    use_rows([_daily_row(datetime.date(2026, 7, 5), _dt(2026, 7, 6, 5, 2))])
    resp = client.get("/api/briefs/daily/2026-07-05")
    assert resp.status_code == 200
    pub = resp.json()["publication"]
    assert pub["status"] == "overdue"
    assert pub["trade_date"] == "2026-07-05"


def test_chart_brief_scheduled_type_carries_rider(client, frozen_now):
    frozen_now(_dt(2026, 7, 13, 13, 0))
    use_rows([_chart_row("caiso_fuel_mix_chart", datetime.date(2026, 7, 12), _dt(2026, 7, 13, 12, 5))])
    resp = client.get(
        "/api/joule/chart-brief", params={"brief_type": "caiso_fuel_mix_chart"}
    )
    assert resp.status_code == 200
    pub = resp.json()["publication"]
    assert pub["status"] == "current"
    assert pub["trade_date"] == "2026-07-12"
    assert pub["next_expected_at"] == "2026-07-14T12:00:00+00:00"


def test_chart_brief_unscheduled_type_rider(client, frozen_now):
    # A rolling intraday deviation type: rider present, status unscheduled.
    frozen_now(_dt(2026, 7, 13, 13, 0))
    use_rows([_chart_row("caiso_load_deviation", datetime.date(2026, 7, 13), _dt(2026, 7, 13, 12, 5))])
    resp = client.get(
        "/api/joule/chart-brief", params={"brief_type": "caiso_load_deviation"}
    )
    assert resp.status_code == 200
    pub = resp.json()["publication"]
    assert pub["status"] == "unscheduled"
    assert pub["next_expected_at"] is None


def test_chart_brief_empty_still_carries_rider(client, frozen_now):
    # No row -> body null, but the rider still reports (overdue: nothing today).
    frozen_now(_dt(2026, 7, 13, 14, 0))
    use_rows([])
    resp = client.get(
        "/api/joule/chart-brief", params={"brief_type": "caiso_fuel_mix_chart"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["body"] is None
    assert body["publication"]["status"] == "overdue"
    assert body["publication"]["trade_date"] is None
