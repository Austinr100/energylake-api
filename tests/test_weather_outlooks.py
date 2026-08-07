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

# The CPC shelf's gate-verified graphics, in registry reading order.
ALL_TILES = ["610temp", "610prcp", "814temp", "814prcp", "wk34temp", "wk34prcp",
             "monthtemp", "monthprcp", "seastemp", "seasprcp"]

# The drought shelf's, likewise. The U.S. Drought Monitor CONUS map is NOT here:
# the USDM page declares only a dated snapshot path and no stable current url.
DROUGHT_TILES = ["cpc_mdo", "cpc_sdo"]


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

def test_hazards_is_empty_with_a_reason_naming_the_unbuilt_lane():
    # SPC and WPC render their outlooks through JS map viewers that declare no
    # product `img src`, so no hazard product could be sourced by the ruled
    # method. Empty because the lane does not exist — NOT because SPC/WPC issued
    # nothing today.
    s = _shelves_by_id(main._assemble_outlooks(AS_OF, _all_fresh_climate(), []))["hazards"]
    assert s["products"] == []
    assert s["reason"] == "source_lane_not_built:spc_wpc_hazard_outlooks"


def test_a_shelf_with_products_never_also_carries_an_absence_reason(monkeypatch):
    # The two claims are mutually exclusive by construction: a reason asserts
    # nothing feeds the shelf. Drive it from the hazards registry so the
    # invariant is tested rather than just observed on today's data.
    monkeypatch.setitem(main.SHELF_GRAPHICS, "hazards", [
        {"graphic_id": "fake_hz", "label": "DAY 1", "measure": "CONVECTIVE",
         "title": "test hazard", "url": "https://example.test/hz.png",
         "attribution": "TEST", "link_url": "https://example.test/"},
    ])
    monkeypatch.setattr(main, "_VERIFIED_GRAPHIC_IDS",
                        main._VERIFIED_GRAPHIC_IDS | {"fake_hz"})
    body = main._assemble_outlooks(AS_OF, _all_fresh_climate(), [])
    for s in body["shelves"]:
        assert not (s["products"] and "reason" in s), s["id"]
    assert _shelves_by_id(body)["hazards"]["products"][0]["product"] == "fake_hz"


def test_shelves_that_carry_products_carry_no_reason():
    body = main._assemble_outlooks(AS_OF, _all_fresh_climate(), [])
    shelves = _shelves_by_id(body)
    assert "reason" not in shelves["cpc"]
    assert "reason" not in shelves["drought"]


# ---------------------------------------------------------------------------
# The drought shelf
# ---------------------------------------------------------------------------

def test_drought_shelf_carries_the_two_cpc_drought_outlooks():
    body = main._assemble_outlooks(AS_OF, _all_fresh_climate(), [])
    assert [p["product"] for p in _tiles(body, "drought")] == DROUGHT_TILES


def test_drought_tiles_satisfy_the_same_product_shape_as_cpc_tiles():
    body = main._assemble_outlooks(AS_OF, _all_fresh_climate(), [])
    for p in _tiles(body, "drought"):
        assert set(p) == PRODUCT_KEYS, p["product"]
        assert p["image_url"].startswith("https://www.cpc.ncep.noaa.gov/")
        assert p["artifact_format"] == "png"
        assert p["alt"]
    t = _tiles_by_product(body, "drought")
    assert t["cpc_mdo"]["label"] == "MONTHLY"
    assert t["cpc_sdo"]["label"] == "SEASONAL"
    assert {p["measure"] for p in _tiles(body, "drought")} == {"DROUGHT OUTLOOK"}


def test_drought_tiles_ship_null_dates_no_warehouse_lane_behind_them():
    # v0 metadata rule: no lane states these issuances, so they ship as the null
    # spelling the frame renders "——". Issuance is neither scraped from page
    # text nor parsed out of a filename.
    body = main._assemble_outlooks(AS_OF, _all_fresh_climate(), [])
    for p in _tiles(body, "drought"):
        assert p["issued_date"] is None
        assert p["valid_start"] is None and p["valid_end"] is None


def test_drought_tiles_are_independent_of_the_warehouse():
    # An empty warehouse costs the CPC shelf its dates; it costs drought nothing.
    empty = _tiles(main._assemble_outlooks(AS_OF, [], []), "drought")
    full = _tiles(main._assemble_outlooks(AS_OF, _all_fresh_climate(), []), "drought")
    assert empty == full


def test_usdm_is_absent_from_the_drought_registry():
    # Pins the omission: the USDM page declares only a DATED snapshot path, so
    # serving it would freeze on one week's map with no way to say which week.
    # If a stable url is ever adopted, this test is the thing that says so.
    urls = " ".join(g["url"] for g in main.SHELF_GRAPHICS["drought"])
    assert "droughtmonitor" not in urls


# ---------------------------------------------------------------------------
# The build-time STOP-gate, carried into the new contract
# ---------------------------------------------------------------------------

def _all_registry_ids() -> list[str]:
    """Every gated graphic_id across every registry the gate walks."""
    ids = []
    for registry in (main.OUTLOOK_GRAPHICS, main.SHELF_GRAPHICS, main.DRIVERS_GRAPHICS):
        ids += [g["graphic_id"] for gs in registry.values() for g in gs]
    return ids


def test_outlooks_verified_ids_are_exactly_what_the_stop_gate_passed():
    # Pinned to the 2026-07-29 gate run: the outlooks surface's twelve urls all
    # returned 200 + image/* (ten CPC gifs, the two CPC drought pngs). Every url
    # was read off its page's declared `img src`. Drivers ids are pinned in
    # test_weather_drivers.py; this asserts the outlooks slice, not the whole
    # set, so the two surfaces stay independently pinned.
    outlooks_ids = {g["graphic_id"] for gs in main.OUTLOOK_GRAPHICS.values() for g in gs}
    outlooks_ids |= {g["graphic_id"] for gs in main.SHELF_GRAPHICS.values() for g in gs}
    assert outlooks_ids == set(ALL_TILES) | set(DROUGHT_TILES)
    assert outlooks_ids <= main._VERIFIED_GRAPHIC_IDS


def test_every_registered_graphic_is_gate_verified():
    # No graphic ships dark today — if a source moves a path and the gate drops
    # an id, this fails loudly rather than the shelf quietly shrinking. Covers
    # EVERY registry, so a new surface cannot skip the gate.
    assert set(_all_registry_ids()) == main._VERIFIED_GRAPHIC_IDS


def test_graphic_ids_are_unique_across_every_registry():
    # `product` is the client's React key and the gate's id — a collision would
    # silently drop a tile, and the two surfaces share one verified set.
    ids = _all_registry_ids()
    assert len(ids) == len(set(ids))


def test_every_shelf_registry_key_is_a_real_shelf():
    assert set(main.SHELF_GRAPHICS) <= set(main._OUTLOOK_SHELF_IDS)


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


# ---------------------------------------------------------------------------
# kelvin_by_horizon — the per-horizon read slot (pantry migration 151)
#
# THE KEY NAME IS THE CONTRACT. The dashboard reads
# `cpc?.kelvin_by_horizon?.[horizon.id] ?? null` with
# HorizonId = "d610"|"d814"|"wk34"|"monthly"|"seasonal", and the pantry stores
# captions under exactly those keys, so serving is a lookup and not a
# translation. These tests bind that end of it.
# ---------------------------------------------------------------------------

def _kelvin_rows(*pairs):
    """[(horizon, caption)] -> the row shape _kelvin_by_horizon consumes."""
    return [{"horizon": h, "caption": c, "read_date": None,
             "issued_date": None} for h, c in pairs]


def test_kelvin_by_horizon_is_absent_when_no_caption_is_served():
    """The field is documented OPTIONAL and today's feed omits it. A behaviour
    change must arrive WITH content, not before it — so an empty caption table
    leaves this endpoint byte-identical to its current output."""
    body = main._assemble_outlooks(AS_OF, _all_fresh_climate(), [], [])
    cpc = _shelves_by_id(body)["cpc"]
    assert "kelvin_by_horizon" not in cpc
    assert _passes_is_outlooks(body)


def test_kelvin_by_horizon_carries_only_the_horizons_with_captions():
    """NO KEY IS INVENTED. A horizon with no caption takes its `?? null` on the
    client and renders the honest dark slot it renders today; writing a
    placeholder would put words under a heading describing a product the desk
    does not have."""
    body = main._assemble_outlooks(
        AS_OF, _all_fresh_climate(), [],
        _kelvin_rows(("d610", "Above normal holds across nearly the whole "
                              "West, likely at its core."),
                     ("d814", "The warm tilt carries but eases.")))
    cpc = _shelves_by_id(body)["cpc"]
    assert set(cpc["kelvin_by_horizon"]) == {"d610", "d814"}
    assert cpc["kelvin_by_horizon"]["d814"] == "The warm tilt carries but eases."
    assert _passes_is_outlooks(body)


def test_blank_captions_are_not_served():
    """An empty string renders as a present-but-empty read — a section that
    looks captioned and says nothing. Drop it and let the dark slot show."""
    body = main._assemble_outlooks(
        AS_OF, _all_fresh_climate(), [],
        _kelvin_rows(("d610", "   "), ("wk34", "")))
    assert "kelvin_by_horizon" not in _shelves_by_id(body)["cpc"]


def test_kelvin_by_horizon_is_only_on_the_cpc_shelf():
    """`kelvin_by_horizon` slices the CPC shelf by lead. hazards and drought
    render WHOLE and take the shelf-level `kelvin`, which has no lane yet."""
    body = main._assemble_outlooks(
        AS_OF, _all_fresh_climate(), [], _kelvin_rows(("d610", "A caption.")))
    for shelf_id in ("hazards", "drought"):
        assert "kelvin_by_horizon" not in _shelves_by_id(body)[shelf_id]


def test_shelf_level_kelvin_stays_null_even_when_horizons_are_captioned():
    """One shelf sentence cannot narrate five leads. Filling `kelvin` from a
    horizon read would print the same sentence under 6-10 DAY and under
    SEASONAL, attributing a read to a lead it was never written about."""
    body = main._assemble_outlooks(
        AS_OF, _all_fresh_climate(), [], _kelvin_rows(("d610", "A caption.")))
    assert _shelves_by_id(body)["cpc"]["kelvin"] is None


def test_horizon_keys_match_the_dashboard_horizon_ids():
    """Drift gate on the serving contract itself."""
    horizons = ("d610", "d814", "wk34", "monthly", "seasonal")
    body = main._assemble_outlooks(
        AS_OF, _all_fresh_climate(), [],
        _kelvin_rows(*((h, f"caption for {h}") for h in horizons)))
    assert set(_shelves_by_id(body)["cpc"]["kelvin_by_horizon"]) == set(horizons)


def test_stale_bound_is_two_days_and_is_stated():
    """A caption describes ONE issuance. Serving an old one under today's map
    is a confident wrong read that nothing turns red over — so the query bounds
    it. Two days tolerates one skipped Actions run and refuses two."""
    assert main._OUTLOOK_KELVIN_STALE_DAYS == 2
    assert "read_date >= CURRENT_DATE" in main._OUTLOOK_KELVIN_SQL
    assert "DISTINCT ON (horizon)" in main._OUTLOOK_KELVIN_SQL
