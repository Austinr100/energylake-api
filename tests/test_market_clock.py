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

import asyncio
import datetime
import re
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
    def __init__(self, pool):
        self._pool = pool

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, query, params=None):
        self._pool.statements.append(query)
        if query.lstrip().upper().startswith("SET LOCAL"):
            return
        self._pool.sink["query"], self._pool.sink["params"] = query, params
        self._pool.queries += 1
        if self._pool.delay:
            await asyncio.sleep(self._pool.delay)
        if self._pool.error is not None:
            raise self._pool.error

    async def fetchone(self):
        return self._pool.row


class _FakeTx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, pool):
        self._pool = pool

    async def __aenter__(self):
        self._pool.checkouts += 1
        return self

    async def __aexit__(self, *exc):
        return False

    def transaction(self):
        return _FakeTx()

    def cursor(self):
        return _FakeCursor(self._pool)


class FakePool:
    """Counts checkouts and queries; `delay` holds a query open so concurrent
    callers overlap; `error` makes the read raise."""

    def __init__(self, row, *, delay=0.0, error=None):
        self.row = row
        self.delay = delay
        self.error = error
        self.sink = {}
        self.statements = []
        self.checkouts = 0
        self.queries = 0

    def connection(self):
        return _FakeConn(self)


@pytest.fixture(autouse=True)
def _cold_memo():
    """Every test starts on a cold market-clock memo."""
    main._market_clock_memo["entry"] = None
    main._market_clock_inflight["task"] = None
    yield
    main._market_clock_memo["entry"] = None
    main._market_clock_inflight["task"] = None


@pytest.fixture
def client():
    # No `with` block => lifespan never runs => the real pool is never opened.
    return TestClient(main.app)


def _install(row, now, **kw):
    pool = FakePool(row, **kw)
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


# ═══════════════════════════════════════════════════════════════════════════
# d091546 (D-09-25-75) — the market clock stops holding the pool
# ═══════════════════════════════════════════════════════════════════════════

def _reads(sql):
    """{alias: body} for every parenthesised SELECT that reads
    timeseries_values — scalar subqueries and FROM items alike."""
    out = {}
    for m in re.finditer(r"\(SELECT\b", sql):
        depth, i = 0, m.start()
        for i in range(m.start(), len(sql)):
            depth += {"(": 1, ")": -1}.get(sql[i], 0)
            if depth == 0:
                break
        body = sql[m.start() + 1:i]
        alias = re.match(r"\)\s+AS\s+(\w+)", sql[i:])
        if "FROM timeseries_values" in body and alias:
            out[alias[1]] = body
    return out


def _unnamed_series(sql):
    return [a for a, body in _reads(sql).items()
            if not re.search(r"\bseries\s*=\s*%\(hub\)s", body)]


def test_m1_every_read_names_dataset_and_series():
    reads = _reads(main._MARKET_CLOCK_SQL)
    assert set(reads) == {"da", "sp15_da_val", "fmm"}
    assert main._MARKET_CLOCK_SQL.count("FROM timeseries_values") == len(reads)
    for alias, body in reads.items():
        assert body.count("FROM timeseries_values") == 1, alias
        assert re.search(r"\bdataset\s*=\s*%\((da|fmm)\)s", body), alias
    assert _unnamed_series(main._MARKET_CLOCK_SQL) == []
    # The check itself bites: drop the hub from the day-ahead read (the shape
    # that held the pool on 2026-10-01) and it is caught.
    before = main._MARKET_CLOCK_SQL.replace(
        "WHERE dataset = %(da)s AND series = %(hub)s\n             AND ts >= %(tstart)s",
        "WHERE dataset = %(da)s\n             AND ts >= %(tstart)s")
    assert before != main._MARKET_CLOCK_SQL
    assert _unnamed_series(before) == ["da"]


def test_m1_statement_runs_under_a_3s_timeout(client):
    pool = _install({"da_hours": 0, "da_published_at": None,
                     "da_first_ingested_at": None, "sp15_da_val": None,
                     "fmm_ts": _pt(2026, 7, 16, 7, 55), "fmm_val": 33.0},
                    now=_pt(2026, 7, 16, 8, 0))
    assert client.get("/api/market-clock").status_code == 200
    assert pool.statements[0] == "SET LOCAL statement_timeout = '3s'"
    assert pool.statements[1] is main._MARKET_CLOCK_SQL


_ROW_RUNNING = {"da_hours": 0, "da_published_at": None, "da_first_ingested_at": None,
                "sp15_da_val": None, "fmm_ts": _pt(2026, 7, 16, 10, 55), "fmm_val": 35.0}


def test_m2_twenty_concurrent_requests_on_a_cold_memo_run_one_query():
    pool = _install(_ROW_RUNNING, now=_pt(2026, 7, 16, 11, 0), delay=0.05)

    async def go():
        return await asyncio.gather(*(main.market_clock() for _ in range(20)))

    bodies = asyncio.run(go())
    assert pool.checkouts == 1
    assert pool.queries == 1
    assert len({b["as_of"] for b in bodies}) == 1
    assert all(b["state"] == "DA_MARKET_RUNNING" for b in bodies)


def test_m2_a_fresh_memo_is_served_without_a_checkout(client):
    pool = _install(_ROW_RUNNING, now=_pt(2026, 7, 16, 11, 0))
    first = client.get("/api/market-clock").json()
    main._utcnow = lambda: _pt(2026, 7, 16, 11, 0) + datetime.timedelta(seconds=10)
    second = client.get("/api/market-clock").json()
    assert pool.checkouts == 1
    assert second["as_of"] == first["as_of"]


def _seed(payload, age_s):
    main._market_clock_memo["entry"] = (main.time.monotonic() - age_s, payload)


def test_m3_stale_answer_served_during_rebuild_with_its_own_as_of():
    old = {"state": "DA_BIDDING", "as_of": "2026-07-16T15:59:30+00:00"}
    pool = _install(_ROW_RUNNING, now=_pt(2026, 7, 16, 11, 0), delay=0.05)
    _seed(old, age_s=30)

    async def go():
        served = await asyncio.gather(*(main.market_clock() for _ in range(5)))
        await asyncio.sleep(0.2)               # let the one rebuild land
        return served, await main.market_clock()

    served, after = asyncio.run(go())
    assert all(s is old for s in served)       # stale, with its ORIGINAL as_of
    assert pool.queries == 1                   # one rebuild behind five requests
    assert after["as_of"] == _pt(2026, 7, 16, 11, 0).isoformat()
    assert after["state"] == "DA_MARKET_RUNNING"


def test_m3_past_120s_the_stale_answer_is_not_served(client):
    import psycopg
    _install(_ROW_RUNNING, now=_pt(2026, 7, 16, 11, 0),
             error=psycopg.errors.QueryCanceled("canceling statement due to statement timeout"))
    _seed({"state": "DA_BIDDING", "as_of": "2026-07-16T15:57:00+00:00"}, age_s=121)
    r = client.get("/api/market-clock")
    assert r.status_code == 503
    assert "as_of" not in r.json()


def test_m3_between_20s_and_120s_a_failed_rebuild_keeps_the_stale_answer(client):
    import psycopg
    old = {"state": "DA_BIDDING", "as_of": "2026-07-16T15:59:00+00:00"}
    _install(_ROW_RUNNING, now=_pt(2026, 7, 16, 11, 0),
             error=psycopg.errors.QueryCanceled("canceling statement due to statement timeout"))
    _seed(old, age_s=60)
    r = client.get("/api/market-clock")
    assert r.status_code == 200
    assert r.json() == old


def test_m4_statement_timeout_is_a_503_and_not_memoised(client):
    import psycopg
    pool = _install(_ROW_RUNNING, now=_pt(2026, 7, 16, 11, 0),
                    error=psycopg.errors.QueryCanceled("canceling statement due to statement timeout"))
    r = client.get("/api/market-clock")
    assert r.status_code == 503
    assert r.json()["detail"] == "market clock read exceeded 3 s"
    assert r.headers["Retry-After"] == "5"
    assert main._market_clock_memo["entry"] is None
    # The next request reads again, and a good read is then memoised.
    pool.error = None
    r = client.get("/api/market-clock")
    assert r.status_code == 200
    assert pool.queries == 2
    assert main._market_clock_memo["entry"] is not None


def test_m5_the_two_stamps_are_one_aggregate_over_the_same_rows():
    da = _reads(main._MARKET_CLOCK_SQL)["da"]
    # min and max over the same rows: first <= newest, both null together —
    # and with count() beside them the min/max index shortcut cannot apply.
    for agg in ("count(value)", "max(ingested_ts) AS da_published_at",
                "min(ingested_ts) AS da_first_ingested_at"):
        assert agg in da, agg


def test_m5_both_stamps_null_before_publication(client):
    _install(_ROW_RUNNING, now=_pt(2026, 7, 16, 11, 0))
    body = client.get("/api/market-clock").json()
    assert body["da_published_at"] is None
    assert body["da_first_ingested_at"] is None


def test_m5_first_ingest_is_the_published_label_newest_is_reported(client):
    first, newest = _pt(2026, 7, 15, 13, 5), _pt(2026, 7, 15, 17, 49)
    _install({"da_hours": 24, "da_published_at": newest, "da_first_ingested_at": first,
              "sp15_da_val": 40.05, "fmm_ts": _pt(2026, 7, 15, 15, 55), "fmm_val": 41.2},
             now=_pt(2026, 7, 15, 16, 0))
    body = client.get("/api/market-clock").json()
    assert body["state"] == "DA_PUBLISHED"
    assert body["label"] == "DA awards published 13:05 PT"
    assert body["da_first_ingested_at"] == first.isoformat()
    assert body["da_published_at"] == newest.isoformat()
    assert body["da_first_ingested_at"] <= body["da_published_at"]
