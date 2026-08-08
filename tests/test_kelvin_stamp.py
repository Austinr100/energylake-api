"""
Tests for kelvin_meta_by_horizon — the caption provenance stamp (2026-08-08).

THE FIELD EXISTS FOR ONE QUESTION. A caption is a sentence written about ONE
issuance of a daily CPC product. The dashboard's trail-warning lane needs to
ask: "was this sentence written about the issuance rendered under it?" — which
requires the caption's `issued_date` (and `read_date`) to travel WITH the
caption. `kelvin_by_horizon` ships the sentences; `kelvin_meta_by_horizon`
ships their stamps. This file pins the stamp's whole contract:

  * IDENTICAL KEY SETS, BY CONSTRUCTION. A stamp without a caption asserts
    provenance for words that are not being served; a caption without a stamp
    leaves the trail check unable to run. Same rows, same blank-caption filter,
    one attach condition — the two fields appear together or not at all.
  * DATES ARE ISO STRINGS; NULL IS SERVED AS NULL. A caption row written
    before the stamp landed carries no issued_date — the trail check must
    treat that as UNVERIFIABLE, never as fresh. Absence stated, not filled.
  * BYTE-IDENTICAL WHEN DARK. An uncaptioned feed omits both fields, so
    today's output does not change shape before content arrives.

Additive file on purpose: tests/test_weather_outlooks.py pins the caption
contract and is not touched — one concern per PR, and the existing suite
keeps proving the stamp changed nothing it covers.
"""

import datetime

from fastapi.testclient import TestClient

import main


UTC = datetime.timezone.utc
AS_OF = datetime.datetime(2026, 8, 8, 15, 0, tzinfo=UTC)

ALL_TYPES = ["cpc_6_10_day", "cpc_8_14_day", "cpc_week_3_4",
             "cpc_30_day", "cpc_90_day", "enso"]


def _dt(y, mo, dd, h=0, mi=0):
    return datetime.datetime(y, mo, dd, h, mi, tzinfo=UTC)


def _climate_row(outlook_type, generated_ts):
    return {"outlook_type": outlook_type, "generated_ts": generated_ts,
            "discussion_text": "…", "source_url": "https://cpc.example/d",
            "char_count": 1}


def _all_fresh_climate():
    ts = _dt(2026, 8, 7, 12, 15)
    return [_climate_row(t, ts) for t in ALL_TYPES]


def _row(horizon, caption, read_date=None, issued_date=None):
    """One kelvin_outlook_reads row as the DB serves it: DATE columns arrive as
    datetime.date objects (dict_row), captions as text."""
    return {"horizon": horizon, "caption": caption,
            "read_date": read_date, "issued_date": issued_date}


def _cpc(body):
    return next(s for s in body["shelves"] if s["id"] == "cpc")


# ---------------------------------------------------------------------------
# The helper's own contract
# ---------------------------------------------------------------------------

def test_meta_is_none_when_no_rows():
    assert main._kelvin_meta_by_horizon([]) is None


def test_meta_is_none_not_empty_dict_when_all_captions_blank():
    rows = [_row("d610", "   ", datetime.date(2026, 8, 8), datetime.date(2026, 8, 8)),
            _row("wk34", "", datetime.date(2026, 8, 8), None)]
    assert main._kelvin_meta_by_horizon(rows) is None


def test_meta_keys_equal_caption_keys_always():
    """THE INVARIANT. Same rows through both helpers -> same key set, for every
    mix of blank/served captions. A drift here is the bug this test exists
    to make loud."""
    rows = [
        _row("d610", "Served.", datetime.date(2026, 8, 8), datetime.date(2026, 8, 8)),
        _row("d814", "  ", datetime.date(2026, 8, 8), datetime.date(2026, 8, 8)),
        _row("wk34", "Also served.", datetime.date(2026, 8, 7), None),
        _row("monthly", "", None, None),
    ]
    captions = main._kelvin_by_horizon(rows)
    meta = main._kelvin_meta_by_horizon(rows)
    assert set(captions) == set(meta) == {"d610", "wk34"}


def test_dates_serve_as_iso_strings():
    rows = [_row("d610", "A read.", datetime.date(2026, 8, 8),
                 datetime.date(2026, 8, 8))]
    meta = main._kelvin_meta_by_horizon(rows)
    assert meta["d610"] == {"read_date": "2026-08-08",
                            "issued_date": "2026-08-08"}


def test_null_issued_date_is_served_null_never_filled():
    """A pre-stamp caption row is UNVERIFIABLE, not fresh. Substituting
    read_date (or today) for a missing issued_date would manufacture exactly
    the false 'caption matches map' verdict the trail lane exists to catch."""
    rows = [_row("d610", "Old-style row.", datetime.date(2026, 8, 8), None)]
    meta = main._kelvin_meta_by_horizon(rows)
    assert meta["d610"]["read_date"] == "2026-08-08"
    assert meta["d610"]["issued_date"] is None


def test_null_read_date_also_stays_null():
    rows = [_row("d610", "A read.", None, datetime.date(2026, 8, 8))]
    meta = main._kelvin_meta_by_horizon(rows)
    assert meta["d610"] == {"read_date": None, "issued_date": "2026-08-08"}


# ---------------------------------------------------------------------------
# Assembly: the two fields travel together on the CPC shelf only
# ---------------------------------------------------------------------------

def test_uncaptioned_feed_omits_both_fields_byte_identical():
    body = main._assemble_outlooks(AS_OF, _all_fresh_climate(), [], [])
    cpc = _cpc(body)
    assert "kelvin_by_horizon" not in cpc
    assert "kelvin_meta_by_horizon" not in cpc


def test_captioned_feed_carries_both_fields_with_matching_keys():
    rows = [_row("d610", "Above normal holds.", datetime.date(2026, 8, 8),
                 datetime.date(2026, 8, 8)),
            _row("d814", "The tilt eases.", datetime.date(2026, 8, 8),
                 datetime.date(2026, 8, 7))]
    body = main._assemble_outlooks(AS_OF, _all_fresh_climate(), [], rows)
    cpc = _cpc(body)
    assert set(cpc["kelvin_by_horizon"]) == set(cpc["kelvin_meta_by_horizon"]) \
        == {"d610", "d814"}
    assert cpc["kelvin_meta_by_horizon"]["d814"]["issued_date"] == "2026-08-07"


def test_stamp_never_appears_on_hazards_or_drought():
    rows = [_row("d610", "A read.", datetime.date(2026, 8, 8),
                 datetime.date(2026, 8, 8))]
    body = main._assemble_outlooks(AS_OF, _all_fresh_climate(), [], rows)
    for s in body["shelves"]:
        if s["id"] != "cpc":
            assert "kelvin_meta_by_horizon" not in s, s["id"]


def test_shelf_level_kelvin_untouched_by_the_stamp():
    rows = [_row("d610", "A read.", datetime.date(2026, 8, 8),
                 datetime.date(2026, 8, 8))]
    body = main._assemble_outlooks(AS_OF, _all_fresh_climate(), [], rows)
    assert _cpc(body)["kelvin"] is None


def test_blank_caption_horizons_are_absent_from_both_fields():
    rows = [_row("d610", "Served.", datetime.date(2026, 8, 8),
                 datetime.date(2026, 8, 8)),
            _row("wk34", "   ", datetime.date(2026, 8, 8),
                 datetime.date(2026, 8, 8))]
    body = main._assemble_outlooks(AS_OF, _all_fresh_climate(), [], rows)
    cpc = _cpc(body)
    assert set(cpc["kelvin_by_horizon"]) == {"d610"}
    assert set(cpc["kelvin_meta_by_horizon"]) == {"d610"}


# ---------------------------------------------------------------------------
# Route: the stamp survives the wire (dates -> JSON strings) and the SQL
# already selects what it serves
# ---------------------------------------------------------------------------

class _FakeCursor:
    def __init__(self, plan, fail=False):
        self._plan, self._fail = plan, fail
        self._next: list = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, query, params=None):
        if self._fail:
            raise RuntimeError("boom")
        if "forecasts_climate_outlook" in query:
            self._next = self._plan.get("climate", [])
        elif "forecasts_state_outlook" in query:
            self._next = self._plan.get("state", [])
        elif "kelvin_outlook_reads" in query:
            self._next = self._plan.get("kelvin", [])
        else:
            self._next = []

    async def fetchall(self):
        return list(self._next)


class _FakeConn:
    def __init__(self, plan):
        self._plan = plan

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def cursor(self):
        return _FakeCursor(self._plan)


class _FakePool:
    def __init__(self, plan):
        self._plan = plan

    def connection(self):
        return _FakeConn(self._plan)


def test_route_serves_the_stamp_as_json_date_strings(monkeypatch):
    monkeypatch.setattr(main, "_utcnow", lambda: AS_OF)
    plan = {
        "climate": _all_fresh_climate(),
        "state": [],
        "kelvin": [_row("d610", "Above normal holds across the West.",
                        datetime.date(2026, 8, 8), datetime.date(2026, 8, 8)),
                   _row("wk34", "Pre-stamp row.",
                        datetime.date(2026, 8, 8), None)],
    }
    monkeypatch.setattr(main, "_pool", _FakePool(plan))
    client = TestClient(main.app)
    resp = client.get("/api/weather/outlooks")
    assert resp.status_code == 200
    cpc = _cpc(resp.json())
    assert cpc["kelvin_meta_by_horizon"]["d610"] == {
        "read_date": "2026-08-08", "issued_date": "2026-08-08"}
    assert cpc["kelvin_meta_by_horizon"]["wk34"]["issued_date"] is None
    assert set(cpc["kelvin_by_horizon"]) == set(cpc["kelvin_meta_by_horizon"])


def test_route_uncaptioned_omits_the_stamp(monkeypatch):
    monkeypatch.setattr(main, "_utcnow", lambda: AS_OF)
    monkeypatch.setattr(main, "_pool",
                        _FakePool({"climate": [], "state": [], "kelvin": []}))
    client = TestClient(main.app)
    cpc = _cpc(client.get("/api/weather/outlooks").json())
    assert "kelvin_meta_by_horizon" not in cpc
    assert "kelvin_by_horizon" not in cpc


def test_the_serving_sql_already_selects_both_dates():
    """The stamp adds NO new SQL — the read the captions ride already carries
    read_date and issued_date. If a refactor ever drops them from the SELECT,
    this is the loud failure instead of a silently null stamp."""
    assert "read_date" in main._OUTLOOK_KELVIN_SQL
    assert "issued_date" in main._OUTLOOK_KELVIN_SQL
