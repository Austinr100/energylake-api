"""
Tests for the climate-drivers feed (2026-07-29):
  GET /api/weather/drivers

Part B of the Reserved Shelves + Drivers spec. The endpoint reuses the outlooks
GRAMMAR exactly — {as_of, shelves: [{id, depth, kelvin, products}]} — so the
dashboard assembles it with the shipped GraphicsShelf machinery and the same
isOutlooks-shaped guard passes. These tests pin that, plus the two properties
that are silent on the server and total on the client:

  * every shelf's `products` is an ARRAY, never null (a null fails the guard,
    which blanks the whole page);
  * a tile exists only for a gate-verified graphic — this contract has no
    url-null slot, so an unverified url would render as a dead <img>.

The endpoint reads NO database: the registry is the whole payload. There is no
503 path to test and no fake pool here.
"""

import datetime

import main


UTC = datetime.timezone.utc
AS_OF = datetime.datetime(2026, 7, 29, 12, 5, tzinfo=UTC)

SHELF_IDS = ["mjo", "teleconnections", "enso"]

# The OutlookProduct interface, field for field — shared with the outlooks feed.
PRODUCT_KEYS = {"product", "label", "measure", "image_url", "alt",
                "issued_date", "valid_start", "valid_end", "artifact_format"}

MJO_TILES = ["mjo_rmm_obs", "mjo_rmm_gefs", "mjo_rmm_verif7", "mjo_olr_hov",
             "mjo_olr_spatial", "mjo_u850_hov", "mjo_u200_hov", "mjo_vpot200_hov"]

TELE_TILES = ["tele_ao_obs", "tele_ao_gefs", "tele_nao_obs", "tele_nao_gefs",
              "tele_pna_obs", "tele_pna_gefs", "tele_aao_obs", "tele_aao_gefs"]

ENSO_TILES = ["enso_ssta_week", "enso_nino_ts", "enso_ssta_tlon",
              "enso_subsurface", "enso_olr_tlon", "enso_uv850", "enso_uv200"]


def _body():
    return main._assemble_drivers(AS_OF)


def _shelves_by_id(body):
    return {s["id"]: s for s in body["shelves"]}


def _tiles(body, shelf_id):
    return _shelves_by_id(body)[shelf_id]["products"]


def _passes_is_outlooks(body) -> bool:
    """The Python mirror of the client's shape guard — the same one the outlooks
    tests use. The drivers page rides the identical grammar, so it rides the
    identical guard."""
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
# The frame's contract — identical grammar to /api/weather/outlooks
# ---------------------------------------------------------------------------

def test_top_level_is_exactly_as_of_and_shelves():
    body = _body()
    assert set(body) == {"as_of", "shelves"}
    assert body["as_of"] == AS_OF.isoformat()


def test_three_shelves_in_reading_order():
    assert [s["id"] for s in _body()["shelves"]] == SHELF_IDS


def test_every_shelf_carries_the_shelf_fields():
    for s in _body()["shelves"]:
        assert {"id", "depth", "kelvin", "products"} <= set(s)
        # No archive lane to state an envelope depth; the drivers stanza that
        # fills Kelvin's per-shelf read does not exist yet.
        assert s["depth"] is None
        assert s["kelvin"] is None


def test_payload_passes_the_clients_shape_guard():
    assert _passes_is_outlooks(_body())


def test_products_is_always_a_list_never_null(monkeypatch):
    # Even with nothing gate-verified, every shelf must still be an array or the
    # guard fails and the whole page goes dark.
    monkeypatch.setattr(main, "_VERIFIED_GRAPHIC_IDS", set())
    body = _body()
    assert [s["id"] for s in body["shelves"]] == SHELF_IDS
    assert all(s["products"] == [] for s in body["shelves"])
    assert _passes_is_outlooks(body)


def test_grammar_matches_the_outlooks_feed_exactly():
    # The dashboard reuses one component family across both surfaces; if these
    # two ever diverge, that reuse silently breaks.
    drivers = _body()
    outlooks = main._assemble_outlooks(AS_OF, [], [])
    assert set(drivers) == set(outlooks)
    d_shelf = drivers["shelves"][0]
    o_shelf = next(s for s in outlooks["shelves"] if s["products"])
    assert set(d_shelf) == set(o_shelf)
    assert set(d_shelf["products"][0]) == set(o_shelf["products"][0])


# ---------------------------------------------------------------------------
# The shelves
# ---------------------------------------------------------------------------

def test_mjo_shelf_tiles_in_registry_order():
    assert [p["product"] for p in _tiles(_body(), "mjo")] == MJO_TILES


def test_teleconnections_shelf_is_four_indices_times_obs_and_forecast():
    body = _body()
    assert [p["product"] for p in _tiles(body, "teleconnections")] == TELE_TILES
    # Each CPC index page declares exactly two panels: observed + GEFS forecast.
    labels = [p["label"] for p in _tiles(body, "teleconnections")]
    assert labels == ["AO", "AO", "NAO", "NAO", "PNA", "PNA", "AAO", "AAO"]
    measures = {p["measure"] for p in _tiles(body, "teleconnections")}
    assert measures == {"OBSERVED", "GEFS FORECAST"}


def test_enso_shelf_tiles_in_registry_order():
    assert [p["product"] for p in _tiles(_body(), "enso")] == ENSO_TILES


def test_every_tile_is_exactly_the_outlook_product_interface():
    body = _body()
    for shelf_id in SHELF_IDS:
        for p in _tiles(body, shelf_id):
            assert set(p) == PRODUCT_KEYS, p["product"]
            assert p["label"] and p["measure"] and p["alt"]
            assert p["artifact_format"] in {"gif", "png"}


def test_every_tile_is_a_cpc_hosted_graphic():
    body = _body()
    for shelf_id in SHELF_IDS:
        for p in _tiles(body, shelf_id):
            assert p["image_url"].startswith("https://www.cpc.ncep.noaa.gov/"), p["product"]


def test_every_tile_ships_null_dates_no_issuance_lane_in_v0():
    # v0 metadata rule: the frame renders these as "——". Issuance is neither
    # scraped from page text nor parsed out of a filename.
    body = _body()
    for shelf_id in SHELF_IDS:
        for p in _tiles(body, shelf_id):
            assert p["issued_date"] is None
            assert p["valid_start"] is None and p["valid_end"] is None


# ---------------------------------------------------------------------------
# The STOP-gate
# ---------------------------------------------------------------------------

def test_every_drivers_graphic_is_gate_verified():
    registered = {g["graphic_id"] for gs in main.DRIVERS_GRAPHICS.values() for g in gs}
    assert registered == set(MJO_TILES) | set(TELE_TILES) | set(ENSO_TILES)
    assert registered <= main._VERIFIED_GRAPHIC_IDS


def test_unverified_graphics_are_omitted_not_served_with_a_dead_url(monkeypatch):
    monkeypatch.setattr(main, "_VERIFIED_GRAPHIC_IDS", {"mjo_rmm_obs", "enso_uv200"})
    body = _body()
    assert [p["product"] for p in _tiles(body, "mjo")] == ["mjo_rmm_obs"]
    assert _tiles(body, "teleconnections") == []
    assert [p["product"] for p in _tiles(body, "enso")] == ["enso_uv200"]


def test_registry_keys_are_exactly_the_shelves():
    assert list(main.DRIVERS_GRAPHICS) == SHELF_IDS


# ---------------------------------------------------------------------------
# Pinned omissions — the products the ruled method could not source
# ---------------------------------------------------------------------------

def test_ecmwf_rmm_is_absent_and_its_omission_is_stated():
    # CPC's MJO page declares no ECMWF graphic at all. Treated as expected-fail
    # per the ruling: one attempt, omitted, moved on. If CPC ever declares one,
    # this test is what says the omission was deliberate.
    urls = " ".join(g["url"].lower() for g in main.DRIVERS_GRAPHICS["mjo"])
    assert "ecmwf" not in urls and "ecmf" not in urls
    assert "ecmwf_rmm" in main._DRIVERS_OMITTED
    assert main._DRIVERS_OMITTED["ecmwf_rmm"].startswith("source_page_declares_no_src:")


def test_enso_probability_chart_is_absent_and_its_omission_is_stated():
    # The CPC ENSO advisory page declares no product image (chrome only).
    ids = {g["graphic_id"] for g in main.DRIVERS_GRAPHICS["enso"]}
    assert not any("prob" in gid for gid in ids)
    assert "enso_probability" in main._DRIVERS_OMITTED
    assert main._DRIVERS_OMITTED["enso_probability"].startswith("source_page_declares_no_src:")


def test_the_shelf_family_that_works_is_not_stalled_by_the_omissions():
    # The ruling: an honest absence on one product must not stall the shelf
    # family that works. MJO and ENSO both still ship a full shelf.
    body = _body()
    assert len(_tiles(body, "mjo")) == 8
    assert len(_tiles(body, "enso")) == 7


# ---------------------------------------------------------------------------
# Route wiring — no DB, so no pool and no 503 path
# ---------------------------------------------------------------------------

def test_route_serves_the_frame_and_cache_header(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setattr(main, "_utcnow", lambda: AS_OF)
    client = TestClient(main.app)
    resp = client.get("/api/weather/drivers")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"as_of", "shelves"}
    assert body["as_of"] == AS_OF.isoformat()
    assert [s["id"] for s in body["shelves"]] == SHELF_IDS
    assert _passes_is_outlooks(body)
    assert resp.headers.get("Cache-Control") == "public, max-age=300"


def test_route_needs_no_database(monkeypatch):
    # The registry is the whole payload. Pin it: with the pool torn out the
    # endpoint still serves 200, so a DB outage can never dark this tab.
    from fastapi.testclient import TestClient

    monkeypatch.setattr(main, "_utcnow", lambda: AS_OF)
    monkeypatch.setattr(main, "_pool", None)
    client = TestClient(main.app)
    resp = client.get("/api/weather/drivers")
    assert resp.status_code == 200
    assert len(resp.json()["shelves"]) == 3
