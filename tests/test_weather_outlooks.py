"""
Tests for the outlooks feed (2026-07-29):
  GET /api/weather/outlooks

THE CONTRACT UNDER TEST IS THE CONSUMER'S. The dashboard's OutlooksShelves
(energylake-dashboard, src/components/weather/) reads this endpoint through
useOutlooks() behind the isOutlooks() shape guard, and renders three shelves
keyed by id — "cpc" | "hazards" | "drought". These tests pin that frame:

  {as_of, shelves: [{id, depth, kelvin, products: [OutlookProduct]}]}

and nothing else. Two properties are load-bearing enough to have their own
tests, because breaking either is silent on the server and total on the client:

  * EVERY shelf's `products` is an ARRAY, never null. isOutlooks() requires
    Array.isArray(products) on every shelf; a null there fails the guard, which
    fails the whole read, which blanks ALL THREE shelves behind "The outlook
    feed is unavailable." An empty shelf is [] plus a `reason`.
  * A CPC tile is emitted only for a gate-verified graphic. There is no url-null
    slot in this contract — an unverified url renders as a live <img> and paints
    the client's amber GRAPHIC UNAVAILABLE tile.

The build is the pure assembly in main._assemble_outlooks, so most tests
exercise it directly with hand-built rows and an injected `as_of` — no DB, no
clock. Route-level tests cover the SQL/wiring, the Cache-Control header, and the
DB-down 503 via a fake pool that dispatches rows by query.
"""

import datetime

from fastapi.testclient import TestClient

import main


UTC = datetime.timezone.utc
AS_OF = datetime.datetime(2026, 7, 29, 12, 5, tzinfo=UTC)

ALL_TYPES = ["cpc_6_10_day", "cpc_8_14_day", "cpc_week_3_4",
             "cpc_30_day", "cpc_90_day", "enso"]

# The shelf ids the client joins on, in reading order.
SHELF_IDS = ["cpc", "hazards", "drought"]

# The OutlookProduct interface, field for field (outlooks.ts).
PRODUCT_KEYS = {"product", "label", "measure", "image_url", "alt",
                "issued_date", "valid_start", "valid_end", "artifact_format"}

# Every gate-verified graphic, in registry reading order.
ALL_TILES = ["610temp", "610prcp", "814temp", "814prcp", "wk34temp", "wk34prcp",
             "monthtemp", "monthprcp", "seastemp", "seasprcp"]


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
    return {s["id"]: s for s in body["shelves"]}


def _tiles(body, shelf_id="cpc"):
    return _shelves_by_id(body)[shelf_id]["products"]


def _tiles_by_product(body, shelf_id="cpc"):
    return {p["product"]: p for p in _tiles(body, shelf_id)}


def _passes_is_outlooks(body) -> bool:
    """The Python mirror of the client's isOutlooks() guard. If this returns
    False the dashboard renders NOTHING but the unavailable state."""
    return (
        isinstance(body, dict)
        and isinstance(body.get("shelves"), list)
        and all(
            isinstance(s, dict)
            and isinstance(s.get("id"), str)
            and isinstance(s.get("products"), list)
            for s in body["shelves"]
        )
    )


# ---------------------------------------------------------------------------
# The frame's contract
# ---------------------------------------------------------------------------

def test_top_level_is_exactly_as_of_and_shelves():
    body = main._assemble_outlooks(AS_OF, _all_fresh_climate(), [])
    assert set(body) == {"as_of", "shelves"}
    assert body["as_of"] == AS_OF.isoformat()


def test_three_shelves_in_reading_order_with_the_ids_the_client_joins_on():
    body = main._assemble_outlooks(AS_OF, _all_fresh_climate(), [])
    assert [s["id"] for s in body["shelves"]] == SHELF_IDS


def test_every_shelf_carries_the_shelf_fields():
    body = main._assemble_outlooks(AS_OF, _all_fresh_climate(), [])
    for s in body["shelves"]:
        assert {"id", "depth", "kelvin", "products"} <= set(s)
        # depth: no archive lane to state an envelope from. kelvin: arms at v5.
        assert s["depth"] is None
        assert s["kelvin"] is None


def test_payload_passes_the_clients_shape_guard():
    body = main._assemble_outlooks(AS_OF, _all_fresh_climate(), [])
    assert _passes_is_outlooks(body)


def test_products_is_always_a_list_never_null_even_when_empty():
    # The single most load-bearing property in this file: a null `products` on
    # ANY shelf fails isOutlooks() and blanks all three, including CPC.
    body = main._assemble_outlooks(AS_OF, [], [])
    for s in body["shelves"]:
        assert isinstance(s["products"], list), s["id"]
    assert _passes_is_outlooks(body)


def test_no_legacy_contract_keys_survive():
    # The old shape (shelf_id/title/product_id/freshness/discussion/graphics and
    # a top-level state_outlooks) is gone — the client reads none of it.
    body = main._assemble_outlooks(AS_OF, _all_fresh_climate(),
                                   [_state_row("cpc_6_10_day", AS_OF, "CA")])
    assert "state_outlooks" not in body
    for s in body["shelves"]:
        assert "shelf_id" not in s and "title" not in s
        for p in s["products"]:
            assert not ({"product_id", "freshness", "discussion", "graphics",
                         "url", "link_url", "data"} & set(p))


# ---------------------------------------------------------------------------
# The CPC shelf
# ---------------------------------------------------------------------------

def test_cpc_shelf_carries_every_verified_tile_in_reading_order():
    body = main._assemble_outlooks(AS_OF, _all_fresh_climate(), [])
    assert [p["product"] for p in _tiles(body)] == ALL_TILES


def test_each_tile_is_exactly_the_outlook_product_interface():
    body = main._assemble_outlooks(AS_OF, _all_fresh_climate(), [])
    for p in _tiles(body):
        assert set(p) == PRODUCT_KEYS, p["product"]


def test_tile_label_and_measure_compose_the_clients_title():
    # The client renders `${label} · ${measure}`.
    body = main._assemble_outlooks(AS_OF, _all_fresh_climate(), [])
    t = _tiles_by_product(body)
    assert t["610temp"]["label"] == "6–10 DAY"
    assert t["610temp"]["measure"] == "TEMPERATURE"
    assert t["814prcp"]["label"] == "8–14 DAY"
    assert t["814prcp"]["measure"] == "PRECIPITATION"
    assert t["wk34temp"]["label"] == "WEEK 3–4"
    assert t["monthprcp"]["label"] == "MONTHLY"
    assert t["seastemp"]["label"] == "SEASONAL"


def test_tile_image_url_is_the_gate_verified_registry_url():
    body = main._assemble_outlooks(AS_OF, _all_fresh_climate(), [])
    t = _tiles_by_product(body)
    by_id = {g["graphic_id"]: g for gs in main.OUTLOOK_GRAPHICS.values() for g in gs}
    for pid, tile in t.items():
        assert tile["image_url"] == by_id[pid]["url"]
        assert tile["image_url"].startswith("https://www.cpc.ncep.noaa.gov/")
        assert tile["artifact_format"] == "gif"
        assert tile["alt"]


def test_issued_date_comes_from_the_climate_row():
    body = main._assemble_outlooks(AS_OF, _all_fresh_climate(), [])
    assert _tiles_by_product(body)["610temp"]["issued_date"] == "2026-07-28"


def test_issued_date_is_null_when_the_warehouse_has_no_row_for_the_family():
    # The tile still renders (the graphic is verified and live); the client
    # captions the missing window as "—" rather than inventing one.
    rows = [r for r in _all_fresh_climate() if r["outlook_type"] != "cpc_90_day"]
    body = main._assemble_outlooks(AS_OF, rows, [])
    t = _tiles_by_product(body)
    assert t["seastemp"]["issued_date"] is None
    assert t["610temp"]["issued_date"] == "2026-07-28"


def test_valid_window_comes_from_the_state_batch_for_that_period():
    rows = [_state_row("cpc_6_10_day", _dt(2026, 7, 27, 19), "CA"),
            _state_row("cpc_6_10_day", _dt(2026, 7, 27, 19), "TX")]
    body = main._assemble_outlooks(AS_OF, _all_fresh_climate(), rows)
    t = _tiles_by_product(body)
    assert t["610temp"]["valid_start"] == _dt(2026, 8, 2).isoformat()
    assert t["610temp"]["valid_end"] == _dt(2026, 8, 6).isoformat()
    # the state lane carries no batch for the extended families
    assert t["wk34temp"]["valid_start"] is None
    assert t["wk34temp"]["valid_end"] is None


def test_empty_warehouse_still_serves_every_verified_tile():
    # The graphics are verified and live regardless of what the discussion lane
    # holds — an empty warehouse costs the dates, not the shelf.
    body = main._assemble_outlooks(AS_OF, [], [])
    tiles = _tiles(body)
    assert [p["product"] for p in tiles] == ALL_TILES
    assert all(p["issued_date"] is None for p in tiles)
    assert all(p["valid_start"] is None for p in tiles)


def test_enso_contributes_no_tile():
    body = main._assemble_outlooks(AS_OF, _all_fresh_climate(), [])
    assert main.OUTLOOK_GRAPHICS["enso"] == []
    assert all(not p["product"].startswith("enso") for p in _tiles(body))


# ---------------------------------------------------------------------------
# Honest absence — the empty shelves
# ---------------------------------------------------------------------------

def test_hazards_and_drought_are_empty_with_a_reason_naming_the_unbuilt_lane():
    body = main._assemble_outlooks(AS_OF, _all_fresh_climate(), [])
    shelves = _shelves_by_id(body)
    for shelf_id in ("hazards", "drought"):
        s = shelves[shelf_id]
        assert s["products"] == []
        # Empty because the source lane does not exist — NOT because the source
        # issued nothing today.
        assert s["reason"].startswith("source_lane_not_built:")
    assert "spc_wpc" in shelves["hazards"]["reason"]
    assert "drought" in shelves["drought"]["reason"]


def test_the_cpc_shelf_carries_no_absence_reason_when_it_has_products():
    body = main._assemble_outlooks(AS_OF, _all_fresh_climate(), [])
    assert "reason" not in _shelves_by_id(body)["cpc"]


# ---------------------------------------------------------------------------
# The build-time STOP-gate, carried into the new contract
# ---------------------------------------------------------------------------

def test_verified_set_is_exactly_what_the_stop_gate_passed():
    # Pinned to the 2026-07-29 re-run of scripts/verify_outlook_graphics.py
    # after the six short-lead/week-3-4 urls were re-sourced from the `img src`
    # the CPC product pages declare: all ten returned 200 + image/gif.
    assert main._VERIFIED_GRAPHIC_IDS == set(ALL_TILES)


def test_every_registered_graphic_is_gate_verified():
    # No graphic ships dark today — if CPC moves a path and the gate drops an
    # id, this fails loudly rather than the shelf quietly shrinking.
    registered = {g["graphic_id"] for gs in main.OUTLOOK_GRAPHICS.values() for g in gs}
    assert registered == main._VERIFIED_GRAPHIC_IDS


def test_unverified_graphics_are_omitted_not_served_with_a_dead_url(monkeypatch):
    # This contract has no url-null slot: omission IS the honest absence here,
    # because a dead url would render as the client's amber failure tile.
    monkeypatch.setattr(main, "_VERIFIED_GRAPHIC_IDS", {"610temp", "seasprcp"})
    body = main._assemble_outlooks(AS_OF, _all_fresh_climate(), [])
    assert [p["product"] for p in _tiles(body)] == ["610temp", "seasprcp"]


def test_a_fully_unverified_registry_leaves_an_empty_but_valid_cpc_shelf(monkeypatch):
    # Falls through to the client's own "No CPC outlooks issued." empty state —
    # and, critically, still passes the shape guard.
    monkeypatch.setattr(main, "_VERIFIED_GRAPHIC_IDS", set())
    body = main._assemble_outlooks(AS_OF, _all_fresh_climate(), [])
    assert _shelves_by_id(body)["cpc"]["products"] == []
    assert [s["id"] for s in body["shelves"]] == SHELF_IDS
    assert _passes_is_outlooks(body)


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


def test_route_serves_the_frame_and_cache_header(monkeypatch):
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
    assert set(body) == {"as_of", "shelves"}
    assert body["as_of"] == AS_OF.isoformat()
    assert [s["id"] for s in body["shelves"]] == SHELF_IDS
    assert [p["product"] for p in _tiles(body)] == ALL_TILES
    assert _tiles_by_product(body)["610temp"]["valid_start"] == _dt(2026, 8, 2).isoformat()
    assert resp.headers.get("Cache-Control") == "public, max-age=300"


def test_route_payload_survives_the_json_round_trip_guard(monkeypatch):
    # The guard runs on the DESERIALIZED body — pin it there, not just in-process.
    monkeypatch.setattr(main, "_utcnow", lambda: AS_OF)
    monkeypatch.setattr(main, "_pool", _FakePool({"climate": [], "state": []}))
    client = TestClient(main.app)
    body = client.get("/api/weather/outlooks").json()
    assert _passes_is_outlooks(body)
    assert _shelves_by_id(body)["hazards"]["products"] == []


def test_route_503_on_db_error(monkeypatch):
    monkeypatch.setattr(main, "_utcnow", lambda: AS_OF)
    monkeypatch.setattr(main, "_pool", _FakePool({}, fail=True))
    client = TestClient(main.app)
    resp = client.get("/api/weather/outlooks")
    assert resp.status_code == 503
    assert "db unavailable" in resp.json()["detail"]
