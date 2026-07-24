"""
Tests for the CAISO derived peak-demand endpoint:

  * GET /api/timeseries/caiso-peak-demand

No live database is required. Like the sibling demand-stack suite, these swap a
tiny in-memory fake pool into `main._pool` so the route's read-time peak
derivation (max MW + argmax hour-ending per TAC per Pacific operating date) is
exercised end-to-end without a real Neon connection. DB rows are faked as
psycopg's `dict_row` yields them for `timeseries_values` (ts / series / value /
meta).

The PINNED fixture below is a REAL slice of today's banked pull
(dataset caiso_load_fcst_7day, 6,912 rows / 36 TAC areas / 8 full PT days,
ingested 2026-07-23): the verbatim 24-hour CA ISO-TAC vector for PT operating
date 2026-07-23, plus the real peaks of the three IOU TACs and one neighbouring
BA on that date. The endpoint must derive exactly those peaks and hour-endings.
"""

import datetime

import pytest
from fastapi.testclient import TestClient

import main


# ---------------------------------------------------------------------------
# In-memory fake pool (same surface as tests/test_demand_stack.py).
# ---------------------------------------------------------------------------

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

    async def fetchall(self):
        return list(self._rows)

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


class BoomPool:
    """A pool whose connection raises — exercises the 503 DB-unavailable path."""

    def connection(self):
        raise RuntimeError("connection refused")


@pytest.fixture
def client():
    # No `with` block => lifespan does not run => the real pool is never opened.
    return TestClient(main.app)


def use_rows(rows):
    """Install a fake pool returning `rows`; return it for query inspection."""
    pool = FakePool(rows)
    main._pool = pool
    return pool


# ---------------------------------------------------------------------------
# Row builder — one timeseries_values row as dict_row yields it.
# ---------------------------------------------------------------------------

_PUBLISH_TIME = "2026-07-16T16:10:00+00:00"  # real meta.publish_time (issue time)


def _row(series, ts_utc, value, publish_time=_PUBLISH_TIME):
    return {
        "ts": datetime.datetime.fromisoformat(ts_utc),
        "series": series,
        "value": value,
        "meta": {
            "product": "7DA",
            "horizon_days": 7,
            "publish_time": publish_time,
            "generated_ts_source": "oasis_convention",
        },
    }


# The verbatim 24-hour CA ISO-TAC forecast for PT operating date 2026-07-23,
# interval-beginning UTC (midnight PT == 07:00 UTC under PDT). Copied from the
# banked pull. Peak is 38454.05 MW at 01:00 UTC (PT hour 18 -> HE19).
_CA_ISO_TAC_2026_07_23 = [
    ("2026-07-23T07:00:00+00:00", 28944.91),
    ("2026-07-23T08:00:00+00:00", 27288.03),
    ("2026-07-23T09:00:00+00:00", 25965.37),
    ("2026-07-23T10:00:00+00:00", 25180.16),
    ("2026-07-23T11:00:00+00:00", 25088.42),
    ("2026-07-23T12:00:00+00:00", 25856.04),
    ("2026-07-23T13:00:00+00:00", 26879.58),
    ("2026-07-23T14:00:00+00:00", 27537.0),
    ("2026-07-23T15:00:00+00:00", 27484.68),
    ("2026-07-23T16:00:00+00:00", 27364.81),
    ("2026-07-23T17:00:00+00:00", 27499.79),
    ("2026-07-23T18:00:00+00:00", 27919.61),
    ("2026-07-23T19:00:00+00:00", 28860.83),
    ("2026-07-23T20:00:00+00:00", 30314.99),
    ("2026-07-23T21:00:00+00:00", 32068.66),
    ("2026-07-23T22:00:00+00:00", 34161.68),
    ("2026-07-23T23:00:00+00:00", 35597.25),
    ("2026-07-24T00:00:00+00:00", 37314.49),
    ("2026-07-24T01:00:00+00:00", 38454.05),  # <- daily peak, HE19
    ("2026-07-24T02:00:00+00:00", 38094.75),
    ("2026-07-24T03:00:00+00:00", 37371.14),
    ("2026-07-24T04:00:00+00:00", 36264.48),
    ("2026-07-24T05:00:00+00:00", 33794.95),
    ("2026-07-24T06:00:00+00:00", 31103.5),
]


def _ca_iso_tac_rows():
    return [_row("CA ISO-TAC", ts, v) for ts, v in _CA_ISO_TAC_2026_07_23]


# ---------------------------------------------------------------------------
# The pinned real-data derivation
# ---------------------------------------------------------------------------

def test_pinned_ca_iso_tac_peak_from_real_banked_day(client):
    # A real 24-hour PT day derives to exactly one peak MW + HE, non-partial.
    use_rows(_ca_iso_tac_rows())
    body = client.get("/api/timeseries/caiso-peak-demand").json()

    assert body["dataset"] == "caiso_load_fcst_7day"
    assert body["tz"] == "America/Los_Angeles"
    assert body["unit"] == "MW"
    assert body["issued_ts"] == _PUBLISH_TIME          # the receipt line
    assert body["operating_dates"] == ["2026-07-23"]
    assert body["count"] == 1

    area = body["areas"][0]
    assert area["series"] == "CA ISO-TAC"
    assert area["label"] == "CA ISO-TAC"
    assert area["role"] == "system"
    assert area["order"] == 0

    day = area["days"][0]
    assert day == {
        "date": "2026-07-23",
        "peak_mw": 38454.05,   # real max over the 24-hour vector
        "peak_he": 19,         # PT hour 18 (01:00 UTC) -> hour-ending 19
        "hours_covered": 24,
        "partial": False,
    }


def test_pinned_primary_tacs_real_peaks_and_ordering(client):
    # The four primary rows, each with its real 2026-07-23 peak + HE, in the
    # ratified order: CA ISO-TAC (system) then SCE / PGE / SDGE. A neighbouring
    # BA (BPAT) rides along last as role "ba" for the "more" affordance.
    # Each IOU TAC / BA is given a single interval at its real argmax hour so
    # peak_he is unambiguous: SCE HE18 -> PT 17 -> 00:00 UTC; PGE HE20 -> PT 19
    # -> 02:00 UTC; SDGE/BPAT HE19 -> PT 18 -> 01:00 UTC.
    rows = _ca_iso_tac_rows() + [
        _row("SCE-TAC", "2026-07-24T00:00:00+00:00", 19468.43),   # PT 17 -> HE18
        _row("PGE-TAC", "2026-07-24T02:00:00+00:00", 15804.8),    # PT 19 -> HE20
        _row("SDGE-TAC", "2026-07-24T01:00:00+00:00", 3355.85),   # PT 18 -> HE19
        _row("BPAT", "2026-07-24T01:00:00+00:00", 8897.65),       # PT 18 -> HE19
    ]
    use_rows(rows)
    body = client.get("/api/timeseries/caiso-peak-demand").json()

    order = [(a["series"], a["label"], a["role"], a["order"]) for a in body["areas"]]
    assert order == [
        ("CA ISO-TAC", "CA ISO-TAC", "system", 0),
        ("SCE-TAC", "SCE", "tac", 1),
        ("PGE-TAC", "PGE", "tac", 2),
        ("SDGE-TAC", "SDGE", "tac", 3),
        ("BPAT", "BPAT", "ba", 4),
    ]

    peak = {a["series"]: (a["days"][0]["peak_mw"], a["days"][0]["peak_he"])
            for a in body["areas"]}
    assert peak["CA ISO-TAC"] == (38454.05, 19)
    assert peak["SCE-TAC"] == (19468.43, 18)
    assert peak["PGE-TAC"] == (15804.8, 20)
    assert peak["SDGE-TAC"] == (3355.85, 19)
    assert peak["BPAT"] == (8897.65, 19)


# ---------------------------------------------------------------------------
# Derivation mechanics
# ---------------------------------------------------------------------------

def test_ties_resolve_to_the_earliest_hour(client):
    # Two intervals share the day's max MW -> the EARLIER hour-ending wins.
    use_rows([
        _row("CA ISO-TAC", "2026-07-23T20:00:00+00:00", 30000.0),  # PT 13 -> HE14
        _row("CA ISO-TAC", "2026-07-24T01:00:00+00:00", 40000.0),  # PT 18 -> HE19
        _row("CA ISO-TAC", "2026-07-24T02:00:00+00:00", 40000.0),  # PT 19 -> HE20 (tie)
    ])
    day = client.get("/api/timeseries/caiso-peak-demand").json()["areas"][0]["days"][0]
    assert day["peak_mw"] == 40000.0
    assert day["peak_he"] == 19          # earliest of the tied maxima
    assert day["hours_covered"] == 3


def test_partial_day_flagged(client):
    # A PT day the issuance only half-spans is flagged partial, never hidden.
    use_rows([
        _row("CA ISO-TAC", "2026-07-24T04:00:00+00:00", 36264.48),  # PT 21 -> HE22
        _row("CA ISO-TAC", "2026-07-24T05:00:00+00:00", 33794.95),
        _row("CA ISO-TAC", "2026-07-24T06:00:00+00:00", 31103.5),
    ])
    day = client.get("/api/timeseries/caiso-peak-demand").json()["areas"][0]["days"][0]
    assert day["hours_covered"] == 3
    assert day["partial"] is True
    assert day["peak_mw"] == 36264.48
    assert day["peak_he"] == 22


def test_multiple_operating_dates_bucket_independently(client):
    # Two PT days -> two operating_dates, each with its own peak.
    use_rows([
        _row("CA ISO-TAC", "2026-07-24T01:00:00+00:00", 38454.05),  # 07-23 PT
        _row("CA ISO-TAC", "2026-07-25T01:00:00+00:00", 37697.0),   # 07-24 PT
    ])
    body = client.get("/api/timeseries/caiso-peak-demand").json()
    assert body["operating_dates"] == ["2026-07-23", "2026-07-24"]
    days = body["areas"][0]["days"]
    assert [d["date"] for d in days] == ["2026-07-23", "2026-07-24"]
    assert days[0]["peak_mw"] == 38454.05
    assert days[1]["peak_mw"] == 37697.0


def test_missing_date_for_one_series_is_a_null_cell(client):
    # CA ISO-TAC carries both days; a BA carries only the first -> the BA's
    # second day is a present-but-null cell, never dropped from the grid.
    use_rows([
        _row("CA ISO-TAC", "2026-07-24T01:00:00+00:00", 38454.05),  # 07-23
        _row("CA ISO-TAC", "2026-07-25T01:00:00+00:00", 37697.0),   # 07-24
        _row("BPAT", "2026-07-24T01:00:00+00:00", 8897.65),         # 07-23 only
    ])
    body = client.get("/api/timeseries/caiso-peak-demand").json()
    assert body["operating_dates"] == ["2026-07-23", "2026-07-24"]
    bpat = next(a for a in body["areas"] if a["series"] == "BPAT")
    assert bpat["days"][0]["peak_mw"] == 8897.65
    assert bpat["days"][1] == {
        "date": "2026-07-24",
        "peak_mw": None,
        "peak_he": None,
        "hours_covered": 0,
        "partial": True,
    }


# ---------------------------------------------------------------------------
# Query shape + honest-empty + DB failure
# ---------------------------------------------------------------------------

def test_query_reads_the_single_dataset_no_vintage_blend(client):
    pool = use_rows(_ca_iso_tac_rows())
    client.get("/api/timeseries/caiso-peak-demand")
    q = pool.sink["query"]
    assert "FROM timeseries_values" in q
    assert "value IS NOT NULL" in q
    # Single immutable issuance — no DISTINCT ON vintage machinery here.
    assert "DISTINCT ON" not in q
    assert pool.sink["params"]["dataset"] == "caiso_load_fcst_7day"


def test_empty_is_honest_200_not_404(client):
    # Deliberately unlike the 404 siblings: an absent feed renders the strip's
    # holding state, so the endpoint returns 200 with an empty areas list.
    use_rows([])
    resp = client.get("/api/timeseries/caiso-peak-demand")
    assert resp.status_code == 200
    body = resp.json()
    assert body["areas"] == []
    assert body["operating_dates"] == []
    assert body["count"] == 0
    assert body["issued_ts"] is None
    assert body["dataset"] == "caiso_load_fcst_7day"


def test_db_unavailable_is_503(client):
    main._pool = BoomPool()
    resp = client.get("/api/timeseries/caiso-peak-demand")
    assert resp.status_code == 503
    assert "db unavailable" in resp.json()["detail"]
