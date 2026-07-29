"""
Tests for the CPC climate-outlook tab (2026-07-29):
  GET /api/weather/outlooks

The build is the pure assembly in main._assemble_outlooks (+ the small pure
helpers: per-product slot, graphics publishing, state-period grouping,
days_behind), so most tests exercise those directly with hand-built rows and an
injected `as_of` — no DB, no clock. Route-level tests cover the SQL/wiring, the
Cache-Control header, and the DB-down 503 via a fake pool that dispatches rows
by query, mirroring test_weather_meteogram.
"""

import datetime

import pytest
from fastapi.testclient import TestClient

import main


UTC = datetime.timezone.utc
AS_OF = datetime.datetime(2026, 7, 29, 12, 5, tzinfo=UTC)

ALL_TYPES = ["cpc_6_10_day", "cpc_8_14_day", "cpc_week_3_4",
             "cpc_30_day", "cpc_90_day", "enso"]


def _dt(y, mo, dd, h=0, mi=0):
    return datetime.datetime(y, mo, dd, h, mi, tzinfo=UTC)


def _climate_row(outlook_type, generated_ts, text="…discussion…",
                 source_url="https://cpc.example/disc"):
    return {"outlook_type": outlook_type, "generated_ts": generated_ts,
            "discussion_text": text, "source_url": source_url,
            "char_count": len(text)}


def _all_fresh_climate(generated_ts=None):
    """One latest row per outlook_type, all fresh."""
    generated_ts = generated_ts or _dt(2026, 7, 28, 12, 15)
    return [_climate_row(t, generated_ts) for t in ALL_TYPES]


def _state_row(period, generated_ts, state_code, temp="A", pcpn="N",
               vs=None, ve=None):
    return {"outlook_period": period, "generated_ts": generated_ts,
            "valid_start": vs or _dt(2026, 8, 2), "valid_end": ve or _dt(2026, 8, 6),
            "state_code": state_code, "temp_anomaly": temp, "pcpn_anomaly": pcpn}


def _shelves_by_id(body):
    return {s["shelf_id"]: s for s in body["shelves"]}


def _products_by_id(body):
    out = {}
    for s in body["shelves"]:
        for p in s["products"]:
            out[p["product_id"]] = p
    return out


# ---------------------------------------------------------------------------
# Shelf / product structure + freshness
# ---------------------------------------------------------------------------

def test_all_six_products_present_in_ruled_shelves():
    body = main._assemble_outlooks(AS_OF, _all_fresh_climate(), [])
    shelves = _shelves_by_id(body)
    assert set(shelves) == {"short_lead", "extended", "seasonal"}
    assert [p["product_id"] for p in shelves["short_lead"]["products"]] == \
        ["cpc_6_10_day", "cpc_8_14_day"]
    assert [p["product_id"] for p in shelves["extended"]["products"]] == \
        ["cpc_week_3_4", "cpc_30_day"]
    assert [p["product_id"] for p in shelves["seasonal"]["products"]] == \
        ["cpc_90_day", "enso"]
    # every declared type resolved to a real slot (no data:null here)
    prods = _products_by_id(body)
    assert set(prods) == set(ALL_TYPES)
    assert all("freshness" in prods[t] for t in ALL_TYPES)


def test_as_of_and_discussion_passthrough():
    body = main._assemble_outlooks(AS_OF, _all_fresh_climate(), [])
    assert body["as_of"] == AS_OF.isoformat()
    p = _products_by_id(body)["cpc_6_10_day"]
    assert p["discussion"]["text"] == "…discussion…"
    assert p["discussion"]["source_url"] == "https://cpc.example/disc"
    assert p["discussion"]["generated_ts"] == _dt(2026, 7, 28, 12, 15).isoformat()


def test_days_behind_two_decimals_and_fresh():
    body = main._assemble_outlooks(AS_OF, _all_fresh_climate(), [])
    fr = _products_by_id(body)["cpc_6_10_day"]["freshness"]
    # 2026-07-28 12:15 → 2026-07-29 12:05 == 0.9931 days → 0.99
    assert fr["days_behind"] == 0.99
    assert fr["status"] == "FRESH"


def test_stale_when_days_behind_exceeds_threshold():
    old = _dt(2026, 7, 23, 12, 0)  # ~6.0 days behind AS_OF (> 5.0)
    rows = [_climate_row("cpc_6_10_day", old)]
    body = main._assemble_outlooks(AS_OF, rows, [])
    fr = _products_by_id(body)["cpc_6_10_day"]["freshness"]
    assert fr["days_behind"] > main._OUTLOOK_STALE_AFTER_DAYS
    assert fr["status"] == "STALE"


def test_exactly_at_threshold_is_fresh():
    at = AS_OF - datetime.timedelta(days=main._OUTLOOK_STALE_AFTER_DAYS)
    rows = [_climate_row("cpc_6_10_day", at)]
    body = main._assemble_outlooks(AS_OF, rows, [])
    assert _products_by_id(body)["cpc_6_10_day"]["freshness"]["status"] == "FRESH"


# ---------------------------------------------------------------------------
# Honest absence
# ---------------------------------------------------------------------------

def test_missing_type_renders_data_null_not_omitted():
    # Warehouse returns everything EXCEPT cpc_90_day.
    rows = [r for r in _all_fresh_climate() if r["outlook_type"] != "cpc_90_day"]
    body = main._assemble_outlooks(AS_OF, rows, [])
    prods = _products_by_id(body)
    assert "cpc_90_day" in prods, "slot must exist even with no rows"
    slot = prods["cpc_90_day"]
    assert slot["data"] is None
    assert slot["reason"] == "no_rows_in_warehouse"
    assert "freshness" not in slot
    # it still sits on the seasonal shelf, in order, before enso
    seasonal = _shelves_by_id(body)["seasonal"]
    assert [p["product_id"] for p in seasonal["products"]] == ["cpc_90_day", "enso"]


def test_empty_warehouse_yields_all_data_null_slots():
    body = main._assemble_outlooks(AS_OF, [], [])
    prods = _products_by_id(body)
    assert set(prods) == set(ALL_TYPES)
    assert all(prods[t]["data"] is None for t in ALL_TYPES)


# ---------------------------------------------------------------------------
# Published graphic registry
# ---------------------------------------------------------------------------

def test_enso_is_discussion_only_no_graphics():
    body = main._assemble_outlooks(AS_OF, _all_fresh_climate(), [])
    assert _products_by_id(body)["enso"]["graphics"] == []


def test_graphics_ship_unverified_by_default_with_link_url():
    # _VERIFIED_GRAPHIC_IDS is empty in this build → every graphic is honest
    # absence: url null, reason set, link_url still populated.
    assert main._VERIFIED_GRAPHIC_IDS == set()
    body = main._assemble_outlooks(AS_OF, _all_fresh_climate(), [])
    g = _products_by_id(body)["cpc_6_10_day"]["graphics"]
    assert [x["graphic_id"] for x in g] == ["610temp", "610prcp"]
    for x in g:
        assert x["url"] is None
        assert x["reason"] == "source_url_unverified"
        assert x["link_url"] and x["link_url"].startswith("https://")
        assert x["attribution"] == "NOAA CPC"
    assert {x["kind"] for x in g} == {"temp", "pcpn"}


def test_graphics_serve_live_url_when_verified(monkeypatch):
    monkeypatch.setattr(main, "_VERIFIED_GRAPHIC_IDS", {"610temp"})
    body = main._assemble_outlooks(AS_OF, _all_fresh_climate(), [])
    g = {x["graphic_id"]: x for x in _products_by_id(body)["cpc_6_10_day"]["graphics"]}
    assert g["610temp"]["url"] == main.OUTLOOK_GRAPHICS["cpc_6_10_day"][0]["url"]
    assert "reason" not in g["610temp"]
    # its sibling stays honest-absence
    assert g["610prcp"]["url"] is None
    assert g["610prcp"]["reason"] == "source_url_unverified"


def test_registry_covers_every_non_enso_product_with_temp_and_pcpn():
    for pid in ALL_TYPES:
        graphics = main.OUTLOOK_GRAPHICS[pid]
        if pid == "enso":
            assert graphics == []
            continue
        kinds = sorted(g["kind"] for g in graphics)
        assert kinds == ["pcpn", "temp"], pid
        for g in graphics:
            assert g["url"].startswith("https://www.cpc.ncep.noaa.gov/")
            assert g["link_url"].startswith("https://www.cpc.ncep.noaa.gov/")
            assert g["attribution"] == "NOAA CPC"


# ---------------------------------------------------------------------------
# State outlooks
# ---------------------------------------------------------------------------

def test_state_outlooks_group_by_period_with_states():
    gts = _dt(2026, 7, 27, 19)
    rows = [
        _state_row("cpc_6_10_day", gts, "CA", temp="A", pcpn="B"),
        _state_row("cpc_6_10_day", gts, "TX", temp="N", pcpn="N"),
        _state_row("cpc_8_14_day", gts, "CA", temp="B", pcpn="A"),
    ]
    body = main._assemble_outlooks(AS_OF, _all_fresh_climate(), rows)
    so = body["state_outlooks"]
    assert [p["outlook_period"] for p in so["periods"]] == ["cpc_6_10_day", "cpc_8_14_day"]
    p0 = so["periods"][0]
    assert p0["generated_ts"] == gts.isoformat()
    assert p0["valid_start"] == _dt(2026, 8, 2).isoformat()
    assert p0["valid_end"] == _dt(2026, 8, 6).isoformat()
    assert [s["state_code"] for s in p0["states"]] == ["CA", "TX"]
    assert p0["states"][0] == {"state_code": "CA", "temp_anomaly": "A", "pcpn_anomaly": "B"}


def test_state_outlooks_empty_is_honest_absence():
    body = main._assemble_outlooks(AS_OF, _all_fresh_climate(), [])
    assert body["state_outlooks"]["periods"] == []
    assert body["state_outlooks"]["reason"] == "no_rows_in_warehouse"


def test_state_outlook_freshness_stale():
    old = _dt(2026, 7, 20, 19)  # > 5 days behind
    rows = [_state_row("cpc_6_10_day", old, "CA")]
    body = main._assemble_outlooks(AS_OF, _all_fresh_climate(), rows)
    fr = body["state_outlooks"]["periods"][0]["freshness"]
    assert fr["status"] == "STALE"


# ---------------------------------------------------------------------------
# Route wiring: header + 503, via a fake pool that dispatches rows by query
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
        if "forecasts_climate_outlook" in query:
            self._next = self._plan.get("climate", [])
        elif "forecasts_state_outlook" in query:
            self._next = self._plan.get("state", [])
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


def test_route_returns_payload_and_cache_header(monkeypatch):
    monkeypatch.setattr(main, "_utcnow", lambda: AS_OF)
    plan = {
        "climate": _all_fresh_climate(),
        "state": [_state_row("cpc_6_10_day", _dt(2026, 7, 27, 19), "CA")],
    }
    monkeypatch.setattr(main, "_pool", _FakePool(plan))
    client = TestClient(main.app)
    resp = client.get("/api/weather/outlooks")
    assert resp.status_code == 200
    body = resp.json()
    assert body["as_of"] == AS_OF.isoformat()
    assert len(body["shelves"]) == 3
    assert _products_by_id(body).keys() == set(ALL_TYPES)
    assert body["state_outlooks"]["periods"][0]["outlook_period"] == "cpc_6_10_day"
    assert resp.headers.get("Cache-Control") == "public, max-age=300"


def test_route_missing_type_still_a_slot(monkeypatch):
    monkeypatch.setattr(main, "_utcnow", lambda: AS_OF)
    climate = [r for r in _all_fresh_climate() if r["outlook_type"] != "enso"]
    monkeypatch.setattr(main, "_pool", _FakePool({"climate": climate, "state": []}))
    client = TestClient(main.app)
    body = client.get("/api/weather/outlooks").json()
    enso = _products_by_id(body)["enso"]
    assert enso["data"] is None and enso["reason"] == "no_rows_in_warehouse"


def test_route_503_on_db_error(monkeypatch):
    monkeypatch.setattr(main, "_utcnow", lambda: AS_OF)
    monkeypatch.setattr(main, "_pool", _FakePool({}, fail=True))
    client = TestClient(main.app)
    resp = client.get("/api/weather/outlooks")
    assert resp.status_code == 503
    assert "db unavailable" in resp.json()["detail"]
