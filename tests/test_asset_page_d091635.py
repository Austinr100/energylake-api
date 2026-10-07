"""d091635 (D-09-25-157, D-09-25-109) — the asset page's API.

  A1  plant found: production's banked reads (Solar Star 1, Ocotillo Express;
      the 2026-10-06 18Z cycle, read 2026-10-07 01:17Z) through the route
  A2  plant absent: not in the registry -> 404; in the registry with no hours
      -> 200 with `absence`
  A3  drivers present (migration 280 applied and written): every driver rides
      its hour; wind's gust is null on HRRR hours and the payload says which
  A4  drivers absent: the catalog decides what is selected, never a constant;
      a missing column is "column_absent", an empty one "all_null"; the curve
      answers either way
  A5  the hub's measured error: the hub's REGISTRY score band by band (the
      plant figure is the registry figure), "not yet scored" where unscored,
      no per-plant score said in words; no hub, ZP26 and no run each say why
  A6  /assets: code, then name prefix, then name, then county; at most 20;
      LIKE's wildcards are the query's own characters, never patterns
  C   D-09-25-75: timeout first, one (tech, plant_code) per read, memoised;
      bad params 400; DB down 503
  PG  the statements on a real Postgres, before and after migration 280
  V   the banked payloads (docs/receipts/asset-page-api-d091635/) are what the
      route answers for the banked reads: the dashboard's test vector

Route tests run main.py against test_solar_outlook's FakePool (answers by
substring). PG tests skip where no `initdb` is installed.
"""

import json
import os
import pathlib
import shutil
import subprocess
import tempfile
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import asset_page as ap
import main
from test_solar_outlook import FakePool

UTC = timezone.utc
HERE = pathlib.Path(__file__).resolve().parent
BANK = HERE / "fixtures" / "asset_page_d091635" / "bank_2026_10_07.json"
RECEIPTS = HERE.parent / "docs" / "receipts" / "asset-page-api-d091635"

_TS = ("target_ts", "init_ts", "scored_at")
_DATES = ("window_start", "window_end")


def _typed(rows):
    """The bank is JSON; the pool hands the shaper datetimes and dates."""
    out = []
    for r in rows:
        r = dict(r)
        for k in _TS:
            if r.get(k) is not None:
                r[k] = datetime.fromisoformat(r[k]).astimezone(UTC)
        for k in _DATES:
            if r.get(k) is not None:
                r[k] = date.fromisoformat(r[k])
        out.append(r)
    return out


def bank():
    b = json.loads(BANK.read_text())
    return {k: (_typed(v) if isinstance(v, list) else v) for k, v in b.items()}


@pytest.fixture(autouse=True)
def _cold_memos():
    main._asset_cache.clear()
    main._assets_search_cache.clear()
    yield
    main._asset_cache.clear()
    main._assets_search_cache.clear()


@pytest.fixture
def client():
    return TestClient(main.app)


def get(client, monkeypatch, pool, path):
    monkeypatch.setattr(main, "_pool", pool)
    return client.get(path)


def asset_pool(*, catalog=(), sites=(), hours=(), scores=()):
    return FakePool([
        ("SET LOCAL", []),
        ("information_schema.columns", list(catalog)),
        ("FROM implied_gen_scores", list(scores)),
        ("FROM implied_gen_site_latest", list(hours)),
        ("FROM implied_gen_wind_sites", list(sites)),
        ("FROM implied_gen_sites", list(sites)),
    ])


def solar_pool(b=None, **kw):
    b = b or bank()
    return asset_pool(**{"catalog": b["columns"], "sites": b["units_solar_58388"],
                         "hours": b["hours_solar_58388"], "scores": b["scores_solar_SP15"], **kw})


def wind_pool(b=None, **kw):
    b = b or bank()
    return asset_pool(**{"catalog": b["columns"], "sites": b["site_wind_57514"],
                         "hours": b["hours_wind_57514"], "scores": b["scores_wind_SP15"], **kw})


SOLAR = "/api/generation/asset?plant_code=58388&tech=solar"
WIND = "/api/generation/asset?plant_code=57514&tech=wind"


# ═══════════════════════════════════════════════════════════════════════════
# A1 — plant found, production's reads
# ═══════════════════════════════════════════════════════════════════════════

def test_A1_solar_star_1(client, monkeypatch):
    r = get(client, monkeypatch, solar_pool(), SOLAR)
    assert r.status_code == 200, r.text
    b = r.json()
    p = b["plant"]
    assert (p["plant_name"], p["county"], p["state"], p["hub"], p["ba_code"]) == \
        ("Solar Star 1", "Kern", "CA", "SP15", "CISO")
    assert (p["latitude"], p["longitude"]) == (34.8181, -118.4036)
    assert (p["mw"], p["mw_kind"], p["dc_mw"]) == (318.0, "ac", 397.9)
    assert [u["generator_id"] for u in p["units"]] == ["AVS1", "SS11", "SS12", "SS13", "SS14", "SS15", "SS16"]
    assert {(u["mount"], u["mount_basis"], u["azimuth_basis"]) for u in p["units"]} == \
        {("single_axis", "stated", "class_default")}
    assert p["outage_resource_ids"] == ["SLSTR1_2_SOLAR1", "SLSTR1_2_SOLR1A", "SLSTR1_2_SOLR1B"]
    run = b["run"]
    assert (run["model"], run["init_ts"], run["method_version"], run["figure"]) == \
        ("gfs", "2026-10-06T18:00:00+00:00", "solar_pv_v1", "registry")
    assert (run["lead_h_first"], run["lead_h_last"], len(b["hours"])) == (1, 240, 240)
    assert run["weather_seam"] is None
    assert max(h["implied_mw"] for h in b["hours"]) == 272.2738
    assert b["label"] == ap.so.LABEL and "attribution" not in b
    assert b["absence"] is None


def test_A1_ocotillo_express(client, monkeypatch):
    b = get(client, monkeypatch, wind_pool(), WIND).json()
    p = b["plant"]
    assert (p["plant_name"], p["county"], p["mw"], p["mw_kind"]) == \
        ("Ocotillo Express LLC", "Imperial", 265.4, "nameplate")
    assert (p["turbine_model"], p["n_turbines"], p["rotor_m"], p["hub_height_m"]) == \
        ("Siemens SWT-2.3-108", 112, 108.0, 80.0)
    assert (p["rotor_basis"], p["turbine_model_basis"]) == ("uswtdb", "eia860_sch3")
    assert p["curve"]["turbine_type"] == "V90/2000" and p["curve"]["basis"] == "vintage_band"
    assert p["counts_in_hub_actual"] == "yes" and p["hrrr_dist_km"] == 0.476
    seam = b["run"]["weather_seam"]
    assert (seam["last_lead"], seam["first_lead"], seam["before"], seam["after"]) == \
        (48, 49, "hrrr_80m", "gfs_100m")
    assert b["run"]["weather_sources"] == ["hrrr_80m", "gfs_100m"]
    assert b["label"] == ap.wo.LABEL and b["attribution"] == ap.wo.ATTRIBUTION


def test_A1_every_hour_names_its_lead_band():
    assert [ap.band_of(x) for x in (1, 6, 7, 24, 25, 48, 49, 120, 121, 240, 0, 241)] == \
        ["h01_06", "h01_06", "h07_24", "h07_24", "h25_48", "h25_48", "h49_120", "h49_120",
         "h121_240", "h121_240", None, None]


def test_A1_only_the_newest_cycle_is_served(client, monkeypatch):
    b = bank()
    stale = [{**h, "init_ts": h["init_ts"] - timedelta(hours=6), "implied_mw": 999.0}
             for h in b["hours_wind_57514"][:3]]
    body = get(client, monkeypatch, wind_pool(b, hours=stale + b["hours_wind_57514"]), WIND).json()
    assert len(body["hours"]) == 240 and body["run"]["hours_of_other_cycles_dropped"] == 3
    assert 999.0 not in {h["implied_mw"] for h in body["hours"]}


# ═══════════════════════════════════════════════════════════════════════════
# A2 — plant absent
# ═══════════════════════════════════════════════════════════════════════════

def test_A2_not_in_the_registry_is_404(client, monkeypatch):
    r = get(client, monkeypatch, asset_pool(), "/api/generation/asset?plant_code=1&tech=wind")
    assert r.status_code == 404 and "plant_code=1" in r.json()["detail"]


def test_A2_wrong_tech_is_404_not_a_guess(client, monkeypatch):
    # Ocotillo is a wind plant: asked as solar, the solar registry has no such
    # plant, and the route says so rather than serving the other tech's rows.
    pool = asset_pool(catalog=[], sites=[], hours=bank()["hours_wind_57514"])
    assert get(client, monkeypatch, pool, "/api/generation/asset?plant_code=57514&tech=solar").status_code == 404


def test_A2_registered_with_no_hours_is_200_with_absence(client, monkeypatch):
    body = get(client, monkeypatch, wind_pool(hours=[]), WIND).json()
    assert body["hours"] == [] and body["run"] is None
    assert body["absence"]["reason"] == "no_site_rows"
    assert body["plant"]["plant_name"] == "Ocotillo Express LLC"
    assert body["trust"]["absence"]["reason"] == "no_run"


# ═══════════════════════════════════════════════════════════════════════════
# A3 — drivers present
# ═══════════════════════════════════════════════════════════════════════════

def _catalog(tech, curve=True):
    rows = [{"table_name": "implied_gen_site_latest", "column_name": c} for c in ap.HOUR_DRIVERS[tech]]
    if tech == "wind" and curve:
        rows += [{"table_name": "implied_gen_wind_sites", "column_name": c} for c in ap.CURVE_DRIVERS]
    return rows


def test_A3_solar_drivers_ride_their_hours(client, monkeypatch):
    b = bank()
    hours = [{**h, "ghi_wm2": 500.0, "clearsky_ghi_wm2": 800.0, "clearsky_mw": 300.0,
              "tcc_pct": 40.0, "precip_mm": 0.2} for h in b["hours_solar_58388"]]
    pool = solar_pool(b, catalog=_catalog("solar"), hours=hours)
    body = get(client, monkeypatch, pool, SOLAR).json()
    assert body["drivers"]["present"] == list(ap.HOUR_DRIVERS["solar"])
    assert body["drivers"]["absent"] == [] and body["drivers"]["absence"] is None
    h = body["hours"][0]
    assert (h["ghi_wm2"], h["clearsky_ghi_wm2"], h["clearsky_mw"], h["tcc_pct"], h["precip_mm"]) == \
        (500.0, 800.0, 300.0, 40.0, 0.2)
    hours_sql = next(q for q in pool.sql_run() if "implied_gen_site_latest" in q)
    for c in ap.HOUR_DRIVERS["solar"]:
        assert f"l.{c}" in hours_sql


def test_A3_wind_hub_wind_gust_and_curve(client, monkeypatch):
    b = bank()
    hours = [{**h, "hub_ws_ms": 9.5, "gust_ms": None if h["weather_source"] == "hrrr_80m" else 14.0}
             for h in b["hours_wind_57514"]]
    sites = [{**b["site_wind_57514"][0], "cut_in_ms": 4.0, "rated_ms": 12.0, "cut_out_ms": 25.0}]
    pool = wind_pool(b, catalog=_catalog("wind"), hours=hours, sites=sites)
    body = get(client, monkeypatch, pool, WIND).json()
    assert body["drivers"]["present"] == ["hub_ws_ms", "gust_ms"]
    assert body["drivers"]["gust_null_sources"] == ["hrrr_80m"]
    by = {h["lead_h"]: h for h in body["hours"]}
    assert by[48]["gust_ms"] is None and by[49]["gust_ms"] == 14.0 and by[1]["hub_ws_ms"] == 9.5
    c = body["plant"]["curve"]
    assert (c["cut_in_ms"], c["rated_ms"], c["cut_out_ms"]) == (4.0, 12.0, 25.0)
    assert body["curve_absence"] is None
    site_sql = next(q for q in pool.sql_run() if "FROM implied_gen_wind_sites" in q)
    assert "s.cut_in_ms::float8" in site_sql and "s.cut_out_ms::float8" in site_sql


# ═══════════════════════════════════════════════════════════════════════════
# A4 — drivers absent
# ═══════════════════════════════════════════════════════════════════════════

def test_A4_before_280_no_driver_column_is_selected_and_each_is_named(client, monkeypatch):
    pool = wind_pool()                                  # production's catalog: none of them
    r = get(client, monkeypatch, pool, WIND)
    assert r.status_code == 200
    body = r.json()
    for q in pool.sql_run():
        for c in ap.HOUR_DRIVERS["wind"] + ap.CURVE_DRIVERS:
            assert c not in q or "information_schema" in q, (c, q[:60])
    d = body["drivers"]
    assert d["present"] == [] and d["absence"]["reason"] == "drivers_absent"
    assert d["absent"] == [{"driver": "hub_ws_ms", "reason": "column_absent"},
                           {"driver": "gust_ms", "reason": "column_absent"}]
    assert "d091634" in d["absence"]["detail"]
    assert "hub_ws_ms" not in body["hours"][0]
    assert body["curve_absence"]["missing"] == ["cut_in_ms", "rated_ms", "cut_out_ms"]
    assert body["curve_absence"]["reason"] == "column_absent"
    assert len(body["hours"]) == 240                     # the curve, regardless


def test_A4_applied_but_not_yet_written_is_all_null(client, monkeypatch):
    b = bank()
    hours = [{**h, **{c: None for c in ap.HOUR_DRIVERS["solar"]}} for h in b["hours_solar_58388"]]
    body = get(client, monkeypatch, solar_pool(b, catalog=_catalog("solar"), hours=hours), SOLAR).json()
    assert {a["reason"] for a in body["drivers"]["absent"]} == {"all_null"}
    assert body["drivers"]["absence"]["reason"] == "drivers_absent"
    assert body["hours"][0]["clearsky_mw"] is None


def test_A4_partial_is_named_partial(client, monkeypatch):
    b = bank()
    cat = [r for r in _catalog("solar") if r["column_name"] in ("tcc_pct", "precip_mm")]
    hours = [{**h, "tcc_pct": 10.0, "precip_mm": 0.0} for h in b["hours_solar_58388"]]
    body = get(client, monkeypatch, solar_pool(b, catalog=cat, hours=hours), SOLAR).json()
    assert body["drivers"]["present"] == ["tcc_pct", "precip_mm"]
    assert body["drivers"]["absence"]["reason"] == "drivers_partial"
    assert [a["driver"] for a in body["drivers"]["absent"]] == ["ghi_wm2", "clearsky_ghi_wm2", "clearsky_mw"]


def test_A4_no_request_text_reaches_the_select():
    assert "evil" not in ap.hours_sql(("ghi_wm2", "evil; DROP TABLE x"))
    assert "evil" not in ap.wind_site_sql(("cut_in_ms", "evil"))


# ═══════════════════════════════════════════════════════════════════════════
# A5 — how far to trust it
# ═══════════════════════════════════════════════════════════════════════════

def test_A5_the_hubs_registry_score_band_by_band(client, monkeypatch):
    t = get(client, monkeypatch, wind_pool(), WIND).json()["trust"]
    assert (t["area_kind"], t["area"], t["who"], t["method_version"]) == ("hub", "SP15", "registry", "wind_v1")
    assert t["per_plant"] == "no per-plant score: there are no public hourly per-plant actuals"
    # the draft's figures, from the hub's own rows (SP15 wind, registry)
    got = {b: round(t["scores"][b]["mae_pct_installed"], 1) for b in ("h01_06", "h07_24", "h25_48", "h49_120")}
    assert got == {"h01_06": 8.8, "h07_24": 9.4, "h25_48": 12.9, "h49_120": 13.3}
    assert t["scores"]["h121_240"] == "not yet scored"
    assert t["score_progress"] == {"h121_240": {"n_days": 0, "n_hours": t["score_progress"]["h121_240"]["n_hours"],
                                                "window_end": t["score_progress"]["h121_240"]["window_end"],
                                                "min_days": 14}}
    assert t["band_leads"]["h49_120"] == [49, 120]
    assert t["scores"]["h01_06"]["actual_source"] is not None   # wind's extra columns ride along


def test_A5_solar_reads_solar_scores(client, monkeypatch):
    pool = solar_pool()
    t = get(client, monkeypatch, pool, SOLAR).json()["trust"]
    assert round(t["scores"]["h07_24"]["mae_pct_installed"], 1) == 6.6
    q, p = next((q, p) for q, p in pool.calls if "implied_gen_scores" in q)
    assert (p["tech"], p["area_kind"], p["area"], p["method_version"]) == ("solar_pv", "hub", "SP15", "solar_pv_v1")


def test_A5_no_hub_reads_no_score_and_says_why(client, monkeypatch):
    b = bank()
    sites = [{**u, "hub": None} for u in b["units_solar_58388"]]
    pool = solar_pool(b, sites=sites)
    t = get(client, monkeypatch, pool, SOLAR).json()["trust"]
    assert t["absence"]["reason"] == "no_hub" and t["scores"] is None
    assert not any("implied_gen_scores" in q for q in pool.sql_run())


def test_A5_zp26_wind_has_no_score_row(client, monkeypatch):
    b = bank()
    pool = wind_pool(b, sites=[{**b["site_wind_57514"][0], "hub": "ZP26"}])
    t = get(client, monkeypatch, pool, WIND).json()["trust"]
    assert t["absence"]["reason"] == "no_score_row" and "HUBSUM" in t["absence"]["detail"]
    assert not any("implied_gen_scores" in q for q in pool.sql_run())


def test_A5_an_unscored_row_is_never_quoted(client, monkeypatch):
    b = bank()
    scores = [{**s, "scored": False} if s["lead_band"] == "h25_48" else s for s in b["scores_wind_SP15"]]
    t = get(client, monkeypatch, wind_pool(b, scores=scores), WIND).json()["trust"]
    assert t["scores"]["h25_48"] == "not yet scored"
    assert t["scores"]["h49_120"] != "not yet scored"


# ═══════════════════════════════════════════════════════════════════════════
# A6 — /assets
# ═══════════════════════════════════════════════════════════════════════════

def search_pool(solar, wind):
    return FakePool([("SET LOCAL", []), ("FROM implied_gen_wind_sites", list(wind)),
                     ("FROM implied_gen_sites", list(solar))])


def test_A6_searches_both_registries_best_first(client, monkeypatch):
    b = bank()
    body = get(client, monkeypatch, search_pool(b["search_solar_ocotillo"], b["search_wind_ocotillo"]),
               "/api/generation/assets?q=Ocotillo").json()
    assert [(r["plant_code"], r["tech"], r["matched"]) for r in body["results"]] == \
        [(57514, "wind", "name_prefix"), (65820, "solar", "name_prefix"), (60822, "solar", "name")]
    assert body["q"] == "ocotillo" and body["count"] == 3 and body["truncated"] is False


def test_A6_a_plant_code_ranks_first(client, monkeypatch):
    b = bank()
    body = get(client, monkeypatch, search_pool(b["search_solar_57514"], b["search_wind_57514"]),
               "/api/generation/assets?q=57514").json()
    assert body["results"][0]["plant_code"] == 57514 and body["results"][0]["matched"] == "plant_code"


def test_A6_at_most_twenty(client, monkeypatch):
    b = bank()
    body = get(client, monkeypatch, search_pool(b["search_solar_kern"], b["search_wind_kern"]),
               "/api/generation/assets?q=kern").json()
    assert body["count"] == 20 and body["truncated"] is True
    keys = [(ap.MATCHED.index(r["matched"]), -r["mw"]) for r in body["results"]]
    assert keys == sorted(keys)                          # a name match outranks a county
    assert body["results"][0]["plant_name"] == "Kern Valley SP"   # "Kern..." beats 500 MW in Kern County
    assert body["results"][-1]["matched"] == "county"


def test_A6_wildcards_are_the_querys_own_characters():
    p = ap.search_params("50%_a\\b")
    assert p["pat"] == "%50\\%\\_a\\\\b%" and p["prefix"] == "50\\%\\_a\\\\b%" and p["code"] is None
    assert ap.search_params("0057514")["code"] == 57514


@pytest.mark.parametrize("qs", ["", "q=", "q=a", "q=" + "x" * 65])
def test_A6_bad_queries_are_400(client, monkeypatch, qs):
    assert get(client, monkeypatch, search_pool([], []), f"/api/generation/assets?{qs}").status_code == 400


def test_A6_the_case_is_one_memo_key(client, monkeypatch):
    pool = search_pool([], [])
    get(client, monkeypatch, pool, "/api/generation/assets?q=Kern")
    n = len(pool.calls)
    r = get(client, monkeypatch, pool, "/api/generation/assets?q=KERN")
    assert len(pool.calls) == n and r.json()["cache"]["state"] == "fresh"


# ═══════════════════════════════════════════════════════════════════════════
# C — D-09-25-75 and the contract
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("path,pool", [(SOLAR, solar_pool), (WIND, wind_pool),
                                       ("/api/generation/assets?q=kern", lambda: search_pool([], []))])
def test_C_timeout_first_then_memoised(client, monkeypatch, path, pool):
    pl = pool()
    get(client, monkeypatch, pl, path)
    assert pl.sql_run()[0].startswith("SET LOCAL statement_timeout = '5s'")
    n = len(pl.calls)
    r = get(client, monkeypatch, pl, path)
    assert len(pl.calls) == n and r.json()["cache"]["state"] == "fresh"
    assert r.headers["X-Cache"] == "hit"


def test_C_every_plant_read_names_one_tech_and_plant(client, monkeypatch):
    for path, pool, tech in ((SOLAR, solar_pool(), "solar_pv"), (WIND, wind_pool(), "wind")):
        get(client, monkeypatch, pool, path)
        for q, p in pool.calls:
            if "implied_gen_site_latest" in q or "FROM implied_gen_sites" in q:
                assert (p["tech"], p["plant_code"]) == (tech, 58388 if tech == "solar_pv" else 57514)
            if "FROM implied_gen_wind_sites" in q:
                assert p["plant_code"] == 57514
        main._asset_cache.clear()


def test_C_the_plant_reads_walk_the_primary_key():
    sql = " ".join(ap.hours_sql(()).split())
    assert "WHERE l.tech = %(tech)s AND l.plant_code = %(plant_code)s AND l.model = %(model)s" in sql
    assert "max(" not in sql.lower() and "distinct on" not in sql.lower()
    assert "WHERE tech = %(tech)s AND plant_code = %(plant_code)s" in " ".join(ap.SOLAR_UNITS_SQL.split())
    assert "WHERE s.plant_code = %(plant_code)s" in " ".join(ap.wind_site_sql(()).split())


@pytest.mark.parametrize("qs", ["plant_code=58388", "tech=solar", "plant_code=58388&tech=hydro",
                                "plant_code=abc&tech=wind", "plant_code=-4&tech=wind",
                                "plant_code=0&tech=wind", "plant_code=123456789&tech=wind",
                                "plant_code=58388&tech=solar_pv"])
def test_C_bad_params_are_400(client, monkeypatch, qs):
    assert get(client, monkeypatch, solar_pool(), f"/api/generation/asset?{qs}").status_code == 400


@pytest.mark.parametrize("path", [SOLAR, "/api/generation/assets?q=kern"])
def test_C_db_down_is_503(client, monkeypatch, path):
    class Down:
        def connection(self):
            raise RuntimeError("no route to host")
    assert get(client, monkeypatch, Down(), path).status_code == 503


# ═══════════════════════════════════════════════════════════════════════════
# V — the banked payloads are the dashboard's vector (D-09-05-T)
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("name,path,pool", [
    ("asset_solar_58388", SOLAR, solar_pool),
    ("asset_wind_57514", WIND, wind_pool),
])
def test_V_route_answers_the_banked_payload(client, monkeypatch, name, path, pool):
    body = get(client, monkeypatch, pool(), path).json()
    body.pop("cache")
    banked = json.loads((RECEIPTS / f"{name}.json").read_text())
    banked.pop("cache", None)
    assert body == banked


def test_V_search_answers_the_banked_payload(client, monkeypatch):
    b = bank()
    body = get(client, monkeypatch, search_pool(b["search_solar_solar_star"], b["search_wind_solar_star"]),
               "/api/generation/assets?q=solar%20star").json()
    body.pop("cache")
    banked = json.loads((RECEIPTS / "assets_solar_star.json").read_text())
    banked.pop("cache", None)
    assert body == banked


# ═══════════════════════════════════════════════════════════════════════════
# PG — the statements on a real Postgres, before and after migration 280
# ═══════════════════════════════════════════════════════════════════════════

_DDL = """
CREATE TABLE implied_gen_sites (
    tech text NOT NULL, plant_code integer NOT NULL, generator_id text NOT NULL,
    plant_name text, ac_mw numeric, dc_mw numeric, dc_basis text, mount text, mount_basis text,
    tilt_deg numeric, tilt_basis text, azimuth_deg numeric, azimuth_basis text,
    tilt_stated numeric, azimuth_stated numeric, bifacial boolean, hub text, hub_method text,
    ba_code text, state text, county text, latitude double precision, longitude double precision,
    outage_resource_id text, equipment_vintage text, registry_vintage text, method_version text,
    refreshed_at timestamptz, PRIMARY KEY (tech, plant_code, generator_id));
CREATE TABLE implied_gen_wind_sites (
    plant_code integer PRIMARY KEY, plant_name text, nameplate_mw numeric, op_year_mw_wtd integer,
    turbine_model text, turbine_model_basis text, n_turbines integer, n_turbines_basis text,
    rotor_m numeric, rotor_basis text, hub_height_m numeric, hub_height_basis text,
    curve_turbine_type text, curve_hub_height_m numeric, curve_basis text, hub text, hub_method text,
    counts_in_hub_actual text, counts_in_hub_actual_basis text, export_cap_group text,
    export_cap_mw numeric, export_cap_basis text, ba_code text, state text, county text,
    latitude double precision, longitude double precision, hrrr_i integer, hrrr_j integer,
    hrrr_dist_km double precision, outage_resource_id text, method_version text,
    refreshed_at timestamptz);
CREATE TABLE implied_gen_site_latest (
    tech text NOT NULL, plant_code integer NOT NULL, model text NOT NULL,
    target_ts timestamptz NOT NULL, init_ts timestamptz NOT NULL, lead_h smallint NOT NULL,
    implied_mw double precision NOT NULL, outage_mw_subtracted double precision NOT NULL,
    method_version text NOT NULL, weather_source text, cap_mw_subtracted double precision,
    PRIMARY KEY (tech, plant_code, model, target_ts));
"""

# pantry migration 280, as the brief states its columns (d091634).
_MIGRATION_280 = """
ALTER TABLE implied_gen_site_latest
    ADD COLUMN ghi_wm2 double precision, ADD COLUMN clearsky_ghi_wm2 double precision,
    ADD COLUMN clearsky_mw double precision, ADD COLUMN tcc_pct double precision,
    ADD COLUMN precip_mm double precision, ADD COLUMN hub_ws_ms double precision,
    ADD COLUMN gust_ms double precision;
ALTER TABLE implied_gen_wind_sites
    ADD COLUMN cut_in_ms numeric, ADD COLUMN rated_ms numeric, ADD COLUMN cut_out_ms numeric;
"""


@pytest.fixture(scope="module")
def pg():
    initdb = shutil.which("initdb") or "/usr/lib/postgresql/16/bin/initdb"
    if not os.path.exists(initdb):
        pytest.skip("no local Postgres (initdb) on this box")
    psycopg = pytest.importorskip("psycopg")
    bindir = os.path.dirname(initdb)
    d = tempfile.mkdtemp(prefix="pg_asset_")
    as_pg = ["runuser", "-u", "postgres", "--"] if os.geteuid() == 0 else []
    if as_pg:
        shutil.chown(d, "postgres")
    data = os.path.join(d, "data")
    subprocess.run(as_pg + [initdb, "-D", data, "-A", "trust", "-U", "postgres"],
                   check=True, capture_output=True)
    subprocess.run(as_pg + [os.path.join(bindir, "pg_ctl"), "-D", data, "-w",
                            "-l", os.path.join(d, "pg.log"), "-o",
                            f"-k {d} -c listen_addresses='' -c timezone=UTC", "start"],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
    conn = psycopg.connect(f"host={d} user=postgres dbname=postgres", autocommit=True,
                           row_factory=psycopg.rows.dict_row)
    try:
        conn.execute(_DDL)
        _seed(conn)
        yield conn
    finally:
        conn.close()
        subprocess.run(as_pg + [os.path.join(bindir, "pg_ctl"), "-D", data, "-m", "immediate",
                                "stop"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
        shutil.rmtree(d, ignore_errors=True)


def _seed(conn):
    b = bank()
    with conn.cursor() as cur:
        for u in b["units_solar_58388"]:
            cur.execute("INSERT INTO implied_gen_sites (tech, plant_code, generator_id, plant_name, ac_mw, "
                        "dc_mw, dc_basis, mount, mount_basis, azimuth_deg, azimuth_basis, bifacial, hub, "
                        "hub_method, ba_code, state, county, latitude, longitude, outage_resource_id, "
                        "equipment_vintage, registry_vintage, method_version) VALUES ('solar_pv', %(plant_code)s, "
                        "%(generator_id)s, %(plant_name)s, %(ac_mw)s, %(dc_mw)s, %(dc_basis)s, %(mount)s, "
                        "%(mount_basis)s, %(azimuth_deg)s, %(azimuth_basis)s, %(bifacial)s, %(hub)s, "
                        "%(hub_method)s, %(ba_code)s, %(state)s, %(county)s, %(latitude)s, %(longitude)s, "
                        "%(outage_resource_id)s, %(equipment_vintage)s, %(registry_vintage)s, "
                        "%(method_version)s)", u)
        # a 100% plant, so the LIKE escaping is tested against a real '%' and '_'
        cur.execute("INSERT INTO implied_gen_sites (tech, plant_code, generator_id, plant_name, ac_mw, county) "
                    "VALUES ('solar_pv', 9, 'G', 'Hundred_100% Solar', 5, 'Kern'), "
                    "('solar_pv', 10, 'G', 'Hundred 100 Solar', 6, 'Kern')")
        s = b["site_wind_57514"][0]
        cur.execute("INSERT INTO implied_gen_wind_sites (plant_code, plant_name, nameplate_mw, county, state, "
                    "hub, ba_code, turbine_model, curve_turbine_type, latitude, longitude) VALUES "
                    "(%(plant_code)s, %(plant_name)s, %(nameplate_mw)s, %(county)s, %(state)s, %(hub)s, "
                    "%(ba_code)s, %(turbine_model)s, %(curve_turbine_type)s, %(latitude)s, %(longitude)s)", s)
        for h in b["hours_wind_57514"]:
            cur.execute("INSERT INTO implied_gen_site_latest VALUES ('wind', 57514, 'hrrr_gfs', %(target_ts)s, "
                        "%(init_ts)s, %(lead_h)s, %(implied_mw)s, %(outage_mw_subtracted)s, "
                        "%(method_version)s, %(weather_source)s, %(cap_mw_subtracted)s)", h)
        # another plant and another model on the same key, which the read must not see
        cur.execute("INSERT INTO implied_gen_site_latest VALUES ('wind', 57515, 'hrrr_gfs', now(), now(), 1, "
                    "1, 0, 'wind_v1', 'hrrr_80m', 0), ('wind', 57514, 'gfs', now(), now(), 1, 1, 0, "
                    "'wind_v1', 'gfs_100m', 0)")


def test_PG_before_280_every_statement_runs_and_names_the_absence(pg):
    found = ap.detect("wind", pg.execute(ap.COLUMNS_SQL, ap.columns_params("wind")).fetchall())
    assert found == {"hour": (), "curve": ()}
    site = pg.execute(ap.wind_site_sql(found["curve"]), {"plant_code": 57514}).fetchall()
    hours = pg.execute(ap.hours_sql(found["hour"]),
                       {"tech": "wind", "plant_code": 57514, "model": "hrrr_gfs"}).fetchall()
    assert len(hours) == 240 and site[0]["plant_name"] == "Ocotillo Express LLC"
    body = ap.build_asset(tech="wind", plant_code=57514, found=found, site_rows=site,
                          hour_rows=hours, score_rows=[])
    assert body["drivers"]["absence"]["reason"] == "drivers_absent"
    units = pg.execute(ap.SOLAR_UNITS_SQL, {"tech": "solar_pv", "plant_code": 58388}).fetchall()
    assert len(units) == 7


def test_PG_after_280_the_catalog_finds_the_columns_and_values_flow(pg):
    pg.execute(_MIGRATION_280)
    try:
        pg.execute("UPDATE implied_gen_site_latest SET hub_ws_ms = 7.5, "
                   "gust_ms = CASE WHEN weather_source = 'gfs_100m' THEN 11 END WHERE plant_code = 57514")
        pg.execute("UPDATE implied_gen_wind_sites SET cut_in_ms = 3, rated_ms = 13, cut_out_ms = 25")
        found = ap.detect("wind", pg.execute(ap.COLUMNS_SQL, ap.columns_params("wind")).fetchall())
        assert found == {"hour": ("hub_ws_ms", "gust_ms"), "curve": ap.CURVE_DRIVERS}
        assert ap.detect("solar", pg.execute(ap.COLUMNS_SQL, ap.columns_params("solar")).fetchall())["hour"] == \
            ap.HOUR_DRIVERS["solar"]
        site = pg.execute(ap.wind_site_sql(found["curve"]), {"plant_code": 57514}).fetchall()
        hours = pg.execute(ap.hours_sql(found["hour"]),
                           {"tech": "wind", "plant_code": 57514, "model": "hrrr_gfs"}).fetchall()
        body = ap.build_asset(tech="wind", plant_code=57514, found=found, site_rows=site,
                              hour_rows=hours, score_rows=[])
        assert body["drivers"]["present"] == ["hub_ws_ms", "gust_ms"]
        assert body["drivers"]["gust_null_sources"] == ["hrrr_80m"]
        assert (body["plant"]["curve"]["cut_in_ms"], body["plant"]["curve"]["rated_ms"]) == (3.0, 13.0)
    finally:
        pg.execute("ALTER TABLE implied_gen_site_latest DROP COLUMN ghi_wm2, DROP COLUMN clearsky_ghi_wm2, "
                   "DROP COLUMN clearsky_mw, DROP COLUMN tcc_pct, DROP COLUMN precip_mm, "
                   "DROP COLUMN hub_ws_ms, DROP COLUMN gust_ms")
        pg.execute("ALTER TABLE implied_gen_wind_sites DROP COLUMN cut_in_ms, DROP COLUMN rated_ms, "
                   "DROP COLUMN cut_out_ms")


def test_PG_search_ranks_and_escapes(pg):
    rows = pg.execute(ap.SEARCH_SOLAR_SQL, {**ap.search_params("100%"), "tech": "solar_pv"}).fetchall()
    assert [r["plant_code"] for r in rows] == [9]            # '%' is a character, not "anything"
    rows = pg.execute(ap.SEARCH_SOLAR_SQL, {**ap.search_params("solar"), "tech": "solar_pv"}).fetchall()
    assert rows[0]["plant_code"] == 58388 and rows[0]["rank"] == 1 and rows[0]["mw"] == 318.0
    rows = pg.execute(ap.SEARCH_SOLAR_SQL, {**ap.search_params("kern"), "tech": "solar_pv"}).fetchall()
    assert [r["plant_code"] for r in rows] == [58388, 10, 9]   # county matches, by MW
    rows = pg.execute(ap.SEARCH_WIND_SQL, ap.search_params("57514")).fetchall()
    assert rows[0]["rank"] == 0
