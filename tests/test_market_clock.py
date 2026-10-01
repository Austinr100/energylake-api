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

    def transaction(self):
        return _NullTx()


class _NullTx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakePool:
    def __init__(self, row):
        self._row = row
        self.sink = {}

    def connection(self):
        return _FakeConn(self._row, self.sink)


@pytest.fixture(autouse=True)
def _cold_market_clock_memo():
    """d091551 (§1.4.3): every test in this file starts, and leaves, the
    market-clock memo cold — the memo is module state the tests share."""
    main._market_clock_entry = None
    main._market_clock_inflight = None
    yield
    main._market_clock_entry = None
    main._market_clock_inflight = None


@pytest.fixture
def client():
    # No `with` block => lifespan never runs => the real pool is never opened.
    return TestClient(main.app)


def _install(row, now):
    pool = FakePool(row)
    main._pool = pool
    # A new install is a new lake: tests that install twice read twice.
    main._market_clock_entry = None
    main._market_clock_inflight = None
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
# M1..M5 are d091546's (D-09-25-75): a polled route holds a connection for
# milliseconds, or it does not hold one. Every subquery names its series; the
# answer comes from a 20 s single-flight memo that serves stale for at most
# 120 s from its build; the statement carries a 3 s timeout, and a timeout is
# a 503 that is not memoised. The concurrent ones run the app in ONE event loop
# (httpx over ASGI), as uvicorn does.
# ═══════════════════════════════════════════════════════════════════════════

import asyncio  # noqa: E402
import re  # noqa: E402

import httpx  # noqa: E402
import psycopg  # noqa: E402

_SUBQUERY = re.compile(r"\(SELECT\b(.*?)\)\s+AS\s+(\w+)", re.S | re.I)


def _subqueries_without_series(sql):
    """Every `(SELECT …) AS name` that reads timeseries_values without both a
    dataset and a series equality -> [name]."""
    subs = _SUBQUERY.findall(sql)
    assert subs, "no subqueries parsed"
    bad = []
    for body, name in subs:
        if "timeseries_values" not in body:
            continue
        if not (re.search(r"\bdataset\s*=", body) and re.search(r"\bseries\s*=", body)):
            bad.append(name)
    return bad


def test_M1_every_subquery_names_its_series():
    sql = main.MARKET_CLOCK_SQL
    names = [n for _, n in _SUBQUERY.findall(sql)]
    assert names == ["da_hours", "da_published_at", "da_first_ingested_at",
                     "sp15_da_val", "fmm_ts", "fmm_val"]
    assert _subqueries_without_series(sql) == []


def test_M1_rehearsed_red_drops_one_series_and_is_caught():
    # The shipped shape (d091542 Gate 0): da_published_at without `series`.
    shipped = main.MARKET_CLOCK_SQL.replace(
        """(SELECT max(ingested_ts) FROM timeseries_values
         WHERE dataset = %(da)s AND series = %(hub)s""",
        """(SELECT max(ingested_ts) FROM timeseries_values
         WHERE dataset = %(da)s""")
    assert shipped != main.MARKET_CLOCK_SQL
    assert _subqueries_without_series(shipped) == ["da_published_at"]


def test_M1_the_route_runs_that_statement(client):
    pool = _install({"da_hours": 0}, now=_pt(2026, 7, 16, 8, 0))
    assert client.get("/api/market-clock").status_code == 200
    assert pool.sink["query"] == main.MARKET_CLOCK_SQL


class _CountingPool:
    """Counts checkouts and statements. The main statement waits on `gate`
    (when set), then raises `fail` (when set) or returns `row`."""

    def __init__(self, row):
        self.row, self.checkouts, self.statements = row, 0, []
        self.gate, self.fail = None, None
        # d091551: (statement, inside conn.transaction()?) and the tx events.
        self.in_tx, self.tx_log, self.tx_events = False, [], []

    def connection(self):
        pool = self

        class _Cur:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def execute(self, query, params=None):
                pool.statements.append(query)
                pool.tx_log.append((query, pool.in_tx))
                if "timeseries_values" in query:
                    if pool.gate is not None:
                        await pool.gate.wait()
                    if pool.fail is not None:
                        raise pool.fail

            async def fetchone(self):
                return dict(pool.row)

        class _Tx:
            async def __aenter__(self):
                pool.in_tx = True
                pool.tx_events.append("begin")
                return self

            async def __aexit__(self, exc_type, *exc):
                pool.in_tx = False
                pool.tx_events.append("rollback" if exc_type else "commit")
                return False

        class _Conn:
            async def __aenter__(self):
                pool.checkouts += 1
                return self

            async def __aexit__(self, *exc):
                return False

            def cursor(self):
                return _Cur()

            def transaction(self):
                return _Tx()

        return _Conn()


class _Mono:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def cold(monkeypatch):
    """A cold memo, a counting pool, a hand-driven monotonic clock."""
    pool = _CountingPool({"da_hours": 0, "fmm_ts": _pt(2026, 7, 16, 7, 55),
                          "fmm_val": 33.0})
    mono = _Mono()
    monkeypatch.setattr(main, "_pool", pool)
    # raising=False: on a main without the memo (the rehearsed red) the test
    # fails on what the route does, not on setup.
    monkeypatch.setattr(main, "_market_clock_entry", None, raising=False)
    monkeypatch.setattr(main, "_market_clock_inflight", None, raising=False)
    monkeypatch.setattr(main, "_market_clock_mono", mono, raising=False)
    monkeypatch.setattr(main, "_utcnow", lambda: _pt(2026, 7, 16, 8, 0))
    return pool, mono


def _aclient():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),
                             base_url="http://t")


def test_M2_twenty_concurrent_requests_on_a_cold_memo_run_one_query(cold):
    pool, _ = cold

    async def go():
        pool.gate = asyncio.Event()
        async with _aclient() as c:
            reqs = [asyncio.create_task(c.get("/api/market-clock")) for _ in range(20)]
            await asyncio.sleep(0.05)               # all twenty are waiting
            assert pool.checkouts == 1
            pool.gate.set()
            return await asyncio.gather(*reqs)

    rs = asyncio.run(go())
    assert [r.status_code for r in rs] == [200] * 20
    assert len({r.json()["as_of"] for r in rs}) == 1
    assert pool.checkouts == 1
    assert pool.statements.count(main.MARKET_CLOCK_SQL) == 1


def test_M3_stale_during_a_rebuild_keeps_its_as_of_and_dies_at_120s(cold):
    pool, mono = cold

    async def go():
        async with _aclient() as c:
            first = (await c.get("/api/market-clock")).json()
            # 10 s: fresh, no query
            mono.t += 10
            assert (await c.get("/api/market-clock")).json() == first
            assert pool.checkouts == 1
            # 30 s, the wall clock moved, the rebuild hangs: the stale answer,
            # unchanged, at once, and exactly one rebuild behind it
            mono.t += 20
            main._utcnow = lambda: _pt(2026, 7, 16, 8, 1)
            pool.gate = asyncio.Event()
            r = await asyncio.wait_for(c.get("/api/market-clock"), 1.0)
            assert r.status_code == 200 and r.json() == first
            r = await asyncio.wait_for(c.get("/api/market-clock"), 1.0)
            assert r.json()["as_of"] == first["as_of"]
            await asyncio.sleep(0.01)
            assert pool.checkouts == 2
            # 121 s from the build, the rebuild still out, then it fails: 503
            mono.t = 1000.0 + 121
            late = asyncio.create_task(c.get("/api/market-clock"))
            await asyncio.sleep(0.01)
            assert not late.done()                  # it waits on THE rebuild
            assert pool.checkouts == 2
            pool.fail = psycopg.errors.QueryCanceled(
                "canceling statement due to statement timeout")
            pool.gate.set()
            r = await late
            assert r.status_code == 503
            assert r.json()["detail"].startswith("db unavailable:")
            # still past 120 s and failing: still a 503, never the old answer
            r = await c.get("/api/market-clock")
            assert r.status_code == 503
            # the read comes back: a fresh answer with a new as_of
            pool.fail = None
            r = await c.get("/api/market-clock")
            assert r.status_code == 200
            assert r.json()["as_of"] != first["as_of"]
            return first

    first = asyncio.run(go())
    assert first["as_of"] == _pt(2026, 7, 16, 8, 0).isoformat()


def test_M4_a_statement_timeout_is_a_503_and_not_memoised(cold):
    pool, _ = cold
    pool.fail = psycopg.errors.QueryCanceled(
        "canceling statement due to statement timeout")

    async def go():
        async with _aclient() as c:
            a = await c.get("/api/market-clock")
            b = await c.get("/api/market-clock")
            pool.fail = None
            ok = await c.get("/api/market-clock")
            return a, b, ok

    a, b, ok = asyncio.run(go())
    assert a.status_code == b.status_code == 503
    assert a.json()["detail"] == \
        "db unavailable: canceling statement due to statement timeout"
    assert pool.checkouts == 3                       # each one queried: nothing kept
    assert ok.status_code == 200
    # the timeout is set first, in the same transaction as the read
    assert pool.statements[:2] == ["SET LOCAL statement_timeout = '3s'",
                                   main.MARKET_CLOCK_SQL]
    assert main.MARKET_CLOCK_STATEMENT_TIMEOUT == "3s"


def _agg_row(ingests, **extra):
    """The row Postgres returns for SP15's target-day ingest stamps."""
    return {"da_hours": len(ingests) and 24,
            "da_published_at": max(ingests) if ingests else None,
            "da_first_ingested_at": min(ingests) if ingests else None, **extra}


def test_M5_first_ingest_precedes_newest_and_both_are_null_before(client):
    _install(_agg_row([]), now=_pt(2026, 7, 16, 8, 0))
    body = client.get("/api/market-clock").json()
    assert body["state"] == "DA_BIDDING"
    assert body["da_published_at"] is None and body["da_first_ingested_at"] is None

    # published 13:36 PT, then re-ingested at 17:49 PT (d091546 §0)
    ingests = [_pt(2026, 7, 15, 13, 36), _pt(2026, 7, 15, 15, 2), _pt(2026, 7, 15, 17, 49)]
    _install(_agg_row(ingests, sp15_da_val=40.05, fmm_ts=_pt(2026, 7, 15, 13, 55),
                      fmm_val=41.2),
             now=_pt(2026, 7, 15, 14, 0))
    body = client.get("/api/market-clock").json()
    first = datetime.datetime.fromisoformat(body["da_first_ingested_at"])
    newest = datetime.datetime.fromisoformat(body["da_published_at"])
    assert first <= newest
    assert first == ingests[0] and newest == ingests[-1]
    assert body["state"] == "DA_PUBLISHED"
    assert body["label"] == "DA awards published 13:36 PT"   # the first, not the newest


# ═══════════════════════════════════════════════════════════════════════════
# d091551 (§1.4, D-09-25-75) — the market clock's hardening. M1 = §1.4.1
# (SET LOCAL inside an explicit transaction), M2 = §1.4.3 (the cold-memo
# fixture), M3 = §1.4.4 (a failed rebuild keeps the previous answer and is not
# memoised), M4 = §1.4.5 (a 503 carries Retry-After). Each was rehearsed red:
# docs/receipts/polled-routes-d091551/reds.txt.
# ═══════════════════════════════════════════════════════════════════════════

def test_d091551_M1_set_local_runs_inside_an_explicit_transaction(cold):
    pool, _ = cold

    async def go():
        async with _aclient() as c:
            return await c.get("/api/market-clock")

    assert asyncio.run(go()).status_code == 200
    assert pool.tx_log == [("SET LOCAL statement_timeout = '3s'", True),
                           (main.MARKET_CLOCK_SQL, True)]
    assert pool.tx_events == ["begin", "commit"]      # it ends with the read


def test_d091551_M1_a_timeout_rolls_the_transaction_back(cold):
    pool, _ = cold
    pool.fail = psycopg.errors.QueryCanceled(
        "canceling statement due to statement timeout")

    async def go():
        async with _aclient() as c:
            return await c.get("/api/market-clock")

    assert asyncio.run(go()).status_code == 503
    assert all(in_tx for _, in_tx in pool.tx_log)
    assert pool.tx_events == ["begin", "rollback"]


def test_d091551_M2a_this_test_leaves_the_memo_warm(monkeypatch):
    # Deliberately no cleanup of the memo (and not the `cold` fixture, whose
    # monkeypatch undo would reset it): the next test must still start cold.
    monkeypatch.setattr(main, "_pool", _CountingPool(
        {"da_hours": 0, "fmm_ts": _pt(2026, 7, 16, 7, 55), "fmm_val": 33.0}))
    monkeypatch.setattr(main, "_utcnow", lambda: _pt(2026, 7, 16, 8, 0))

    async def go():
        async with _aclient() as c:
            return await c.get("/api/market-clock")

    assert asyncio.run(go()).status_code == 200
    assert main._market_clock_entry is not None


def test_d091551_M2b_and_this_one_starts_cold():
    # Runs after M2a (file order). Only the autouse fixture clears the memo
    # between them; without it this sees M2a's entry.
    assert main._market_clock_entry is None
    assert main._market_clock_inflight is None


def test_d091551_M3_a_rebuild_that_raises_keeps_the_previous_answer(cold):
    pool, mono = cold

    async def go():
        async with _aclient() as c:
            first = await c.get("/api/market-clock")
            entry = main._market_clock_entry
            # 60 s on, the lake fails: the stale answer is served at once, and
            # the one rebuild behind it raises.
            mono.t += 60
            main._utcnow = lambda: _pt(2026, 7, 16, 8, 1)
            pool.fail = psycopg.errors.QueryCanceled(
                "canceling statement due to statement timeout")
            stale = await c.get("/api/market-clock")
            for _ in range(5):
                await asyncio.sleep(0)               # let the rebuild fail
            after_fail = main._market_clock_entry
            # Still inside 120 s: the same answer again, and another attempt
            # (the failure was not kept).
            mono.t += 50
            again = await c.get("/api/market-clock")
            for _ in range(5):
                await asyncio.sleep(0)
            return first, entry, stale, after_fail, again

    first, entry, stale, after_fail, again = asyncio.run(go())
    assert first.status_code == stale.status_code == again.status_code == 200
    assert stale.json() == first.json() == again.json()
    assert after_fail is entry                       # nothing memoised by the failure
    assert main._market_clock_entry is entry
    assert main._market_clock_inflight is None
    assert pool.checkouts == 3                       # the build, then two rebuilds


def test_d091551_M4_a_503_carries_retry_after(cold):
    pool, _ = cold
    pool.fail = psycopg.errors.QueryCanceled(
        "canceling statement due to statement timeout")

    async def go():
        async with _aclient() as c:
            return await c.get("/api/market-clock")

    r = asyncio.run(go())
    assert r.status_code == 503
    assert r.headers["Retry-After"] == "5"
    assert r.headers["Retry-After"] == str(main.MARKET_CLOCK_RETRY_AFTER)
