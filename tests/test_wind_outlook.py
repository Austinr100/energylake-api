"""d091590 (D-09-25-126, D-09-25-127) — the wind outlook API.

  W1  D-09-25-127: calibrated_mw is served only inside the leads its line was
      fitted on; beyond, null with "beyond_fitted_leads". The boundary is the
      line's fit_lead_max, never a constant
  W2  previous_mw is like for like with what each issuance may SHOW
  W3  the days block never adds a calibrated hour to a registry hour
  W4  both seams are read off the rows and named with their leads
  W5  the fitted leads are the writer's, stored on the line (pantry migration
      272); d091608 deleted the route's derivation (CALIBRATION_SQL is solar's)
  W6  hub areas pair with hub actuals, CISO with fuel-mix wind; CAISO's
      day-ahead and its issue-time sentence for hub areas only
  W7  ZP26 has no score row (clause 7): no score read, scores null, said why
  W8  the label, the attribution notice and the CAISO sentence, verbatim
  W9  /sites: one lateral per plant, facts with bases, capacity factor
  S   solar's payload is unchanged by the sharing (its own suite, plus here)
  P1  production's cycle (init 2026-10-05 00Z, re-banked by d091608) through the route

Route tests run main.py against test_solar_outlook's FakePool (answers by
substring). PG tests run wind_outlook's SQL on a real Postgres; they skip where
no `initdb` is installed. Reds: docs/receipts/wind-outlook-api-d091590/reds.txt.
"""

import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import main
import solar_outlook as so
import wind_outlook as wo
from test_solar_outlook import FakePool

UTC = timezone.utc
INIT = datetime(2026, 10, 4, 12, tzinfo=UTC)
PREV = datetime(2026, 10, 4, 6, tzinfo=UTC)
HERE = pathlib.Path(__file__).resolve().parent
FIXDIR = HERE / "fixtures" / "wind_outlook_d091590"
RECEIPTS = HERE.parent / "docs" / "receipts" / "wind-outlook-api-d091590"


@pytest.fixture(autouse=True)
def _cold_memos():
    for c in (main._wind_outlook_cache, main._wind_sites_cache,
              main._solar_outlook_cache, main._solar_sites_cache):
        c.clear()
    yield
    for c in (main._wind_outlook_cache, main._wind_sites_cache,
              main._solar_outlook_cache, main._solar_sites_cache):
        c.clear()


@pytest.fixture
def client():
    return TestClient(main.app)


def band_of(lead):
    for name, lo, hi in (("h01_06", 1, 6), ("h07_24", 7, 24), ("h25_48", 25, 48),
                         ("h49_120", 49, 120), ("h121_240", 121, 240)):
        if lo <= lead <= hi:
            return name


LINE_IDS = {"h01_06": 1, "h07_24": 2, "h25_48": 3, "h49_120": 4}


def hour_rows(n=240, *, seam=48, calibrated_through=120, prev_shift=6, prev_last=240):
    """A wind issuance. The writer applies the band's line to every hour of
    the band (as production does): calibrated through lead 120."""
    out = []
    for lead in range(1, n + 1):
        band = band_of(lead)
        reg = 1000.0 + lead
        cal = round(reg * 0.5 + 100, 3) if lead <= calibrated_through else None
        pl = lead + prev_shift
        has_prev = pl <= prev_last
        out.append({
            "target_ts": INIT + timedelta(hours=lead - 1), "lead_h": lead, "lead_band": band,
            "weather_step_h": 1 if lead <= 114 else 3,
            "weather_source": "hrrr_80m" if lead <= seam else "gfs_100m",
            "registry_mw": reg, "calibrated_mw": cal,
            "calibration_id": LINE_IDS.get(band) if cal is not None else None,
            "outage_mw_subtracted": 3.0, "cap_mw_subtracted": 0.0,
            "scored_registry_mw": reg - 10, "scored_mw_total": 10024.0,
            "ac_mw_total": 10024.0, "n_sites": 113,
            "source_posted_ts": INIT + timedelta(hours=2), "method_version": "wind_v1",
            "prev_registry_mw": (reg - 50.0) if has_prev else None,
            "prev_calibrated_mw": (round((reg - 50.0) * 0.5 + 100, 3)
                                   if has_prev and pl <= calibrated_through else None),
            "prev_calibration_id": (LINE_IDS.get(band_of(pl)) if has_prev and pl <= calibrated_through
                                    else None),
            "prev_lead_h": pl if has_prev else None,
            "prev_method_version": "wind_v1" if has_prev else None,
        })
    return out


def line(band, lo, hi, cid=None):
    return {"calibration_id": cid or LINE_IDS[band], "area": "HUBSUM", "lead_band": band,
            "intercept_mw": 100.0, "slope": 0.5, "fit_start": date(2026, 9, 5),
            "fit_end": date(2026, 10, 2), "n_hours": 400, "n_days": 28,
            "fitted_at": datetime(2026, 10, 4, 15, 9, tzinfo=UTC), "method_version": "wind_v1",
            "fit_lead_min": lo, "fit_lead_max": hi, "fit_rows": 404, "fit_issuances": 28}


def lines_today(h49_max=66):
    return [line("h01_06", 1, 6), line("h07_24", 7, 24), line("h25_48", 25, 48),
            line("h49_120", 49, h49_max)]


def score_row(band, who, *, area_kind="hub_sum", mae=9.24):
    return {"lead_band": band, "who": who, "area_kind": area_kind,
            "window_start": date(2026, 9, 16), "window_end": date(2026, 10, 2),
            "n_hours": 302, "n_days": 17, "scored": True, "bias_mw": 1.0, "mae_mw": 9.0,
            "mae_pct_installed": mae, "rmse_mw": 11.0, "r": 0.3,
            "scored_at": datetime(2026, 10, 4, 15, tzinfo=UTC),
            "actual_source": "hub_actual" if area_kind != "ba" else "fuel_mix",
            "mw_yes": 7183.3, "mw_unknown": 2840.7, "mw_no": 0.0}


def site(code, *, hub="SP15", ba="CISO", state="CA", cls="yes", cap=None, name_mw=100.0):
    return {"plant_code": code, "plant_name": f"P{code}", "latitude": 35.0, "longitude": -118.0,
            "nameplate_mw": name_mw, "hub": hub, "ba_code": ba, "state": state, "county": "Kern",
            "turbine_model": None, "turbine_model_basis": "none", "n_turbines": None,
            "n_turbines_basis": "none", "rotor_m": None, "rotor_basis": "none",
            "hub_height_m": 80.0, "hub_height_basis": "eia860_sch3",
            "curve_turbine_type": "V90/2000", "curve_hub_height_m": 80.0, "curve_basis": "vintage_band",
            "counts_in_hub_actual": cls if hub else None,
            "counts_in_hub_actual_basis": "basis" if hub else None,
            "export_cap_group": "grp" if cap else None, "export_cap_mw": cap,
            "export_cap_basis": "its line" if cap else None, "hrrr_dist_km": 1.0}


def outlook_pool(*, hours=None, scores=(), lines=None, actuals=(), sites=None, issuance=None):
    hours = hour_rows() if hours is None else hours
    lines = lines_today() if lines is None else lines
    sites = [site(1), site(2, cls="unknown"), site(3, hub=None, ba="BPAT", state="WA")] if sites is None else sites
    iss = [issuance or {"init_ts": INIT, "prev_init_ts": PREV}] if hours else []
    return FakePool([
        ("SET LOCAL", []),
        ("AS prev_init_ts", iss),
        ("AS prev_calibration_id", hours),
        ("FROM implied_gen_scores", list(scores)),
        ("FROM implied_gen_calibration", list(lines)),
        ("FROM timeseries_values", list(actuals)),
        ("FROM implied_gen_wind_sites", list(sites)),
    ])


def get(client, monkeypatch, pool, path):
    monkeypatch.setattr(main, "_pool", pool)
    return client.get(path)


def hubsum(client, monkeypatch, **kw):
    r = get(client, monkeypatch, outlook_pool(**kw), "/api/generation/wind/outlook?area_kind=hub_sum")
    assert r.status_code == 200, r.text
    return r.json()


# ═══════════════════════════════════════════════════════════════════════════
# W1 — D-09-25-127: calibrated only where the fit has data
# ═══════════════════════════════════════════════════════════════════════════

def test_W1_beyond_the_fitted_leads_the_hour_is_registry_only(client, monkeypatch):
    body = hubsum(client, monkeypatch)
    hours = {h["lead_h"]: h for h in body["hours"]}
    for lead in (1, 48, 49, 66):
        assert hours[lead]["calibrated_mw"] is not None, lead
        assert hours[lead]["calibrated_absent_reason"] is None
    for lead in (67, 100, 120):
        # the writer stored a calibrated figure here; it is not shown
        assert hours[lead]["calibrated_mw"] is None, lead
        assert hours[lead]["calibrated_absent_reason"] == "beyond_fitted_leads"
        assert hours[lead]["registry_mw"] == 1000.0 + lead
    for lead in (121, 240):
        assert hours[lead]["calibrated_mw"] is None
        assert hours[lead]["calibrated_absent_reason"] == "no_line"
    # nothing of a suppressed calibrated figure survives anywhere in the body
    assert str(round((1000.0 + 120) * 0.5 + 100, 3)) not in json.dumps(body)
    assert body["unscaled"] is False


@pytest.mark.parametrize("fit_max", [52, 66, 90, 120])
def test_W1_the_boundary_moves_with_the_fit_and_nothing_is_hard_coded(client, monkeypatch, fit_max):
    body = hubsum(client, monkeypatch, lines=lines_today(h49_max=fit_max))
    cal = [h["lead_h"] for h in body["hours"] if h["calibrated_mw"] is not None]
    assert cal == list(range(1, fit_max + 1))
    seam = body["seams"]["calibration"]
    assert seam["last_calibrated_lead"] == fit_max
    assert seam["first_registry_lead"] == fit_max + 1
    # at 120 the next hour has no line at all: the seam says so
    assert seam["reason"] == ("no_line" if fit_max == 120 else "beyond_fitted_leads")
    assert body["calibration"]["h49_120"]["fit_lead_max"] == fit_max


@pytest.mark.parametrize("mod", [wo, so])        # d091608: the gate now lives in solar_outlook
def test_W1_no_constant_for_todays_boundary_in_the_module(mod):
    src = pathlib.Path(mod.__file__).read_text()
    code = "\n".join(l.split("#")[0] for l in src.splitlines())
    code = re.sub(r'"""[\s\S]*?"""', "", code)
    assert not re.search(r"\b66\b", code)
    assert not re.search(r"\b67\b", code)


def test_W1_a_line_whose_fitted_leads_are_unknown_covers_nothing(client, monkeypatch):
    lines = lines_today()
    lines[3] = {**lines[3], "fit_lead_min": None, "fit_lead_max": None, "fit_rows": 0}
    body = hubsum(client, monkeypatch, lines=lines)
    h49 = [h for h in body["hours"] if h["lead_band"] == "h49_120"]
    assert all(h["calibrated_mw"] is None and h["calibrated_absent_reason"] == "beyond_fitted_leads"
               for h in h49)
    assert body["seams"]["calibration"]["last_calibrated_lead"] == 48


def test_W1_each_line_states_its_fitted_and_applied_leads(client, monkeypatch):
    c = hubsum(client, monkeypatch)["calibration"]
    assert c["h49_120"]["fit_lead_min"] == 49 and c["h49_120"]["fit_lead_max"] == 66
    assert c["h49_120"]["applied_lead_min"] == 49 and c["h49_120"]["applied_lead_max"] == 120
    assert c["h49_120"]["fit_leads_source"] == "stored"
    assert c["h121_240"] is None


# ═══════════════════════════════════════════════════════════════════════════
# W2 — previous_mw like for like with what each issuance may show
# ═══════════════════════════════════════════════════════════════════════════

def test_W2_previous_is_gated_by_its_own_line_at_its_own_lead(client, monkeypatch):
    hours = {h["lead_h"]: h for h in hubsum(client, monkeypatch)["hours"]}
    # lead 60: shown calibrated; the previous run's same hour was its lead 66 -> fitted
    assert hours[60]["previous_mw"] == hours[60]["previous_calibrated_mw"] is not None
    # lead 61: shown calibrated; previous run's lead 67 is beyond ITS fit -> no like for like
    assert hours[61]["calibrated_mw"] is not None
    assert hours[61]["previous_calibrated_mw"] is None and hours[61]["previous_mw"] is None
    # lead 67: shown registry -> registry against registry
    assert hours[67]["previous_mw"] == hours[67]["previous_registry_mw"] == 1000.0 + 67 - 50


# ═══════════════════════════════════════════════════════════════════════════
# W3 — days: never a calibrated hour added to a registry hour
# ═══════════════════════════════════════════════════════════════════════════

def test_W3_a_day_across_the_calibration_seam_is_parts_without_a_total(client, monkeypatch):
    body = hubsum(client, monkeypatch)
    days = body["days"]
    crossing = [d for d in days if d["crosses_calibration_seam"]]
    assert len(crossing) == 1
    d = crossing[0]
    assert d["figure"] == "mixed" and d["energy_mwh"] is None and d["peak_mw"] is None
    assert [p["figure"] for p in d["parts"]] == ["calibrated", "registry"]
    assert d["parts"][0]["last_lead"] == 66 and d["parts"][1]["first_lead"] == 67
    by_lead = {h["lead_h"]: h for h in body["hours"]}
    cal_sum = sum(by_lead[l]["calibrated_mw"] for l in range(d["parts"][0]["first_lead"], 67))
    assert d["parts"][0]["energy_mwh"] == round(cal_sum, 3)


def test_W3_a_one_figure_day_is_the_plain_sum_and_its_peak(client, monkeypatch):
    body = hubsum(client, monkeypatch)
    d = next(x for x in body["days"] if x["figure"] == "registry" and x["complete"])
    hs = [h for h in body["hours"] if d["first_lead"] <= h["lead_h"] <= d["last_lead"]]
    assert d["energy_mwh"] == round(sum(h["registry_mw"] for h in hs), 3)
    assert d["peak_mw"] == max(h["registry_mw"] for h in hs)
    assert d["hours_covered"] == d["hours_in_day"] == 24
    assert body["days"][0]["complete"] is False          # 12Z = 05:00 PDT


# ═══════════════════════════════════════════════════════════════════════════
# W4 — both seams, read off the rows
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("seam", [48, 36])
def test_W4_the_weather_seam_is_where_the_rows_change_source(client, monkeypatch, seam):
    body = hubsum(client, monkeypatch, hours=hour_rows(seam=seam))
    w = body["seams"]["weather"]
    assert (w["last_lead"], w["first_lead"]) == (seam, seam + 1)
    assert (w["before"], w["after"]) == ("hrrr_80m", "gfs_100m")
    assert all(h["weather_source"] == ("hrrr_80m" if h["lead_h"] <= seam else "gfs_100m")
               for h in body["hours"])


def test_W4_unscaled_area_has_no_calibration_seam(client, monkeypatch):
    hours = hour_rows(calibrated_through=0)
    body = hubsum(client, monkeypatch, hours=hours, lines=[])
    assert body["unscaled"] is True and body["seams"]["calibration"] is None
    assert all(h["calibrated_absent_reason"] == "no_line" for h in body["hours"])


# ═══════════════════════════════════════════════════════════════════════════
# W5 — the derivation of the fitted leads
# ═══════════════════════════════════════════════════════════════════════════

def test_W5_the_fitted_leads_are_read_from_the_line_not_derived():
    # d091608 (D-09-25-136 clause 1): was "the derivation follows the writer's
    # fit rule"; the derivation is deleted and the line's stored columns read.
    sql = " ".join(wo.CALIBRATION_SQL.split())
    assert wo.CALIBRATION_SQL is so.CALIBRATION_SQL
    assert "fit_lead_min, fit_lead_max, fit_rows FROM implied_gen_calibration" in sql
    assert "calibration_id = ANY(%(ids)s) AND tech = %(tech)s AND area = %(area)s" in sql
    for gone in ("implied_gen_area_hourly", "LATERAL", "written_at", "AT TIME ZONE"):
        assert gone not in sql, gone
    assert "derived" not in wo.FIT_LEADS_BASIS
    assert wo.FIT_LEADS_BASIS == "stored by the writer (pantry migration 272)"
    assert not hasattr(wo, "_MAX_LEAD_H")


def test_W5_previous_issuances_lines_are_read_too(client, monkeypatch):
    pool = outlook_pool(hours=hour_rows(prev_shift=6))
    get(client, monkeypatch, pool, "/api/generation/wind/outlook?area_kind=hub_sum")
    (_q, params), = [(q, p) for q, p in pool.calls if "FROM implied_gen_calibration" in q]
    assert params["ids"] == [1, 2, 3, 4]
    assert params["tech"] == "wind" and params["area"] == "HUBSUM"


# ═══════════════════════════════════════════════════════════════════════════
# W6 — pairing; CAISO's day-ahead for hub areas only
# ═══════════════════════════════════════════════════════════════════════════

def test_W6_pairs_are_never_crossed():
    assert wo.actual_pairs("hub", "SP15") == [
        ("actual", "caiso_renewables_hourly", "SP15:Wind"),
        ("caiso_dam", "caiso_renewables_fcst_dam", "SP15:Wind")]
    assert wo.actual_pairs("ba", "CISO") == [("actual", "caiso_fuel_mix_hourly", "wind")]
    assert wo.actual_pairs("ba", "BPAT") == [] and wo.actual_pairs("state", "CA") == []
    for kind, area in (("hub", "NP15"), ("hub_sum", "HUBSUM")):
        sql = wo.ACTUALS_SQL[(kind, area)]
        assert "caiso_fuel_mix_hourly" not in sql and ":Solar" not in sql
    ciso = wo.ACTUALS_SQL[("ba", "CISO")]
    assert "caiso_renewables" not in ciso and "'wind'" in ciso


@pytest.mark.parametrize("kind,area", [("hub", "SP15"), ("hub_sum", "HUBSUM")])
def test_W6_hub_areas_carry_caiso_dam_and_its_issue_sentence(client, monkeypatch, kind, area):
    acts = [{"dataset": "caiso_renewables_fcst_dam", "series": f"{h}:Wind",
             "ts": INIT, "value": 100.0} for h in so.HUBS]
    r = get(client, monkeypatch, outlook_pool(actuals=acts),
            f"/api/generation/wind/outlook?area_kind={kind}&area={area}")
    body = r.json()
    assert body["caiso_dam"] and body["caiso_dam_issued"] == wo.CAISO_ISSUE_SENTENCE


@pytest.mark.parametrize("kind,area", [("ba", "CISO"), ("ba", "BPAT"), ("state", "CA")])
def test_W6_no_caiso_dam_outside_hub_areas_even_if_offered(client, monkeypatch, kind, area):
    hostile = [{"dataset": "caiso_renewables_fcst_dam", "series": "SP15:Wind", "ts": INIT, "value": 1.0},
               {"dataset": "caiso_renewables_hourly", "series": "SP15:Wind", "ts": INIT, "value": 1.0}]
    pool = outlook_pool(actuals=hostile)
    body = get(client, monkeypatch, pool, f"/api/generation/wind/outlook?area_kind={kind}&area={area}").json()
    assert "caiso_dam" not in body and "caiso_dam_issued" not in body
    assert body["actuals"] == []        # a hub series offered to CISO is not its actual
    assert not any("caiso_renewables" in q for q in pool.sql_run())


# ═══════════════════════════════════════════════════════════════════════════
# W7 — ZP26: in HUBSUM, no score row of its own
# ═══════════════════════════════════════════════════════════════════════════

def test_W7_zp26_reads_no_score_and_says_why(client, monkeypatch):
    pool = outlook_pool(scores=[score_row("h01_06", "registry", area_kind="hub")])
    body = get(client, monkeypatch, pool, "/api/generation/wind/outlook?area_kind=hub&area=ZP26").json()
    assert not any("FROM implied_gen_scores" in q for q in pool.sql_run())
    assert body["scores"] is None and body["score_progress"] is None
    assert body["scores_absence"]["reason"] == "no_score_row"
    assert "HUBSUM" in body["scores_absence"]["detail"]


def test_W7_scores_carry_the_mw_by_class_and_the_actual(client, monkeypatch):
    body = hubsum(client, monkeypatch, scores=[score_row("h49_120", "calibrated")])
    s = body["scores"]["h49_120"]["calibrated"]
    assert (s["mw_yes"], s["mw_unknown"], s["mw_no"]) == (7183.3, 2840.7, 0.0)
    assert s["actual_source"] == "hub_actual"
    assert body["scores"]["h49_120"]["registry"] == "not yet scored"


# ═══════════════════════════════════════════════════════════════════════════
# W8 — the words, verbatim
# ═══════════════════════════════════════════════════════════════════════════

WRITER = json.loads((FIXDIR / "writer_strings.json").read_text())


def test_W8_label_and_attribution_are_the_writers_verbatim():
    assert wo.LABEL == WRITER["LABEL_WIND (scripts/implied_wind.py)"]
    assert wo.ATTRIBUTION == WRITER["CURVE_DATA_NOTICE (implied_gen/wind_method.py)"]
    assert "windpowerlib" in wo.LABEL and "ODbL-1.0" in wo.ATTRIBUTION


def test_W8_caiso_sentence_is_the_briefs_words():
    assert wo.CAISO_ISSUE_SENTENCE == ("CAISO's day-ahead is issued between 06:09 PT the day "
                                       "before and 06:09 PT on the day")


def test_W8_on_both_routes_full_and_empty(client, monkeypatch):
    full = hubsum(client, monkeypatch)
    empty = get(client, monkeypatch, outlook_pool(hours=[]),
                "/api/generation/wind/outlook?area_kind=state&area=CA").json()
    assert empty["absence"]["reason"] == "no_issuance"
    pool = FakePool([("SET LOCAL", []), ("FROM implied_gen_wind_sites", [])])
    sites = get(client, monkeypatch, pool, "/api/generation/wind/sites?target=2026-10-04T20:00:00Z").json()
    for b in (full, empty, sites):
        assert b["label"] == wo.LABEL and b["attribution"] == wo.ATTRIBUTION
    assert sites["absence"]["reason"] == "no_site_rows"


# ═══════════════════════════════════════════════════════════════════════════
# D-09-25-75 and the contract
# ═══════════════════════════════════════════════════════════════════════════

def test_reads_run_after_the_statement_timeout_and_are_memoised(client, monkeypatch):
    pool = outlook_pool()
    get(client, monkeypatch, pool, "/api/generation/wind/outlook?area_kind=hub_sum")
    assert pool.sql_run()[0].startswith("SET LOCAL statement_timeout")
    n = len(pool.calls)
    r = get(client, monkeypatch, pool, "/api/generation/wind/outlook?area_kind=hub_sum")
    assert len(pool.calls) == n and r.json()["cache"]["state"] == "fresh"


@pytest.mark.parametrize("qs", ["area_kind=hub&area=XX", "area_kind=planet", "area_kind=hub_sum&model=gfs",
                                "area_kind=hub_sum&init=yesterday"])
def test_bad_params_are_400(client, monkeypatch, qs):
    assert get(client, monkeypatch, outlook_pool(), f"/api/generation/wind/outlook?{qs}").status_code == 400


def test_unbanked_init_is_404(client, monkeypatch):
    pool = FakePool([("SET LOCAL", []), ("AS prev_init_ts", [])])
    r = get(client, monkeypatch, pool, "/api/generation/wind/outlook?area_kind=hub_sum&init=2026-10-01T00:00:00Z")
    assert r.status_code == 404


def test_db_down_is_503(client, monkeypatch):
    class Down:
        def connection(self):
            raise RuntimeError("no route to host")
    assert get(client, monkeypatch, Down(), "/api/generation/wind/outlook?area_kind=hub_sum").status_code == 503


def test_every_read_names_tech_wind(client, monkeypatch):
    pool = outlook_pool()
    get(client, monkeypatch, pool, "/api/generation/wind/outlook?area_kind=hub_sum")
    for q, p in pool.calls:
        if "implied_gen_area_hourly" in q or "implied_gen_scores" in q or "implied_gen_calibration" in q:
            assert p["tech"] == "wind", q[:80]


# ═══════════════════════════════════════════════════════════════════════════
# W9 — /sites
# ═══════════════════════════════════════════════════════════════════════════

def latest(code, n=1, mw=50.0, **kw):
    return {**site(code, **kw), "n_hours": n, "implied_mw": mw, "outage_mw_subtracted": 0.0,
            "cap_mw_subtracted": 7.0, "init_ts": INIT, "init_ts_max": INIT, "lead_h_min": 9,
            "lead_h_max": 9 + n - 1, "weather_sources": ["hrrr_80m"], "method_version": "wind_v1"}


def test_W9_site_read_is_one_lateral_per_plant_on_the_key():
    sql = " ".join(wo.SITES_SQL.split())
    assert "FROM implied_gen_wind_sites s CROSS JOIN LATERAL" in sql
    assert "x.tech = %(tech)s AND x.plant_code = s.plant_code AND x.model = %(model)s" in sql
    assert "x.target_ts >= %(lo)s AND x.target_ts < %(hi)s" in sql


def test_W9_target_hour_capacity_factor_and_facts(client, monkeypatch):
    pool = FakePool([("SET LOCAL", []), ("FROM implied_gen_wind_sites",
                     [latest(1, mw=50.0), latest(2, mw=30.0, cap=2131.0, name_mw=200.0),
                      latest(3, hub=None, ba="BPAT", state="WA")])])
    b = get(client, monkeypatch, pool,
            "/api/generation/wind/sites?target=2026-10-04T20:00:00Z&area_kind=hub&area=SP15").json()
    assert b["figure"] == "registry" and b["plant_count"] == 2
    p1, p2 = b["plants"]
    assert p1["capacity_factor"] == 0.5 and p2["capacity_factor"] == 0.15
    assert p2["export_cap_mw"] == 2131.0 and p2["export_cap_basis"] == "its line"
    assert p1["counts_in_hub_actual"] == "yes" and p1["counts_in_hub_actual_basis"] == "basis"
    assert p1["hub_height_m"] == 80.0 and p1["hub_height_basis"] == "eia860_sch3"
    assert p1["cap_mw_subtracted"] == 7.0
    (_q, params), = [(q, p) for q, p in pool.calls if "implied_gen_site_latest" in q]
    assert params["lo"] == datetime(2026, 10, 4, 20, tzinfo=UTC)
    assert params["hi"] == datetime(2026, 10, 4, 21, tzinfo=UTC)


def test_W9_day_energy_and_capacity_factor(client, monkeypatch):
    pool = FakePool([("SET LOCAL", []), ("FROM implied_gen_wind_sites", [latest(1, n=24, mw=1200.0)])])
    b = get(client, monkeypatch, pool, "/api/generation/wind/sites?day=2026-10-05").json()
    p = b["plants"][0]
    assert p["implied_mwh"] == 1200.0 and p["hours_covered"] == 24
    assert p["capacity_factor"] == 0.5 and b["hours_expected"] == 24


@pytest.mark.parametrize("qs", ["", "target=2026-10-04T20:30:00Z", "day=2026-10-05&target=2026-10-04T20:00:00Z",
                                "day=10/05", "day=2026-10-05&model=gfs"])
def test_W9_sites_bad_params_are_400(client, monkeypatch, qs):
    pool = FakePool([("SET LOCAL", [])])
    assert get(client, monkeypatch, pool, f"/api/generation/wind/sites?{qs}").status_code == 400


# ═══════════════════════════════════════════════════════════════════════════
# S — solar's statements are byte-identical after the sharing
# ═══════════════════════════════════════════════════════════════════════════

def test_S_solar_score_statement_unchanged_by_the_extra_columns_hook():
    for kind in so.AREA_KINDS:
        assert so.SCORES_SQL[kind] == so._scores_sql(kind)
        assert "actual_source" not in so.SCORES_SQL[kind]
        assert "s.actual_source, s.mw_yes" in wo.SCORES_SQL[kind]
    assert ":Solar" in so.ACTUALS_SQL[("hub", "SP15")]
    assert so.build_actuals("ba", "CISO", [{"dataset": "caiso_fuel_mix_hourly", "series": "solar",
                                            "ts": INIT, "value": 5.0}]) == (
        [{"target_ts": INIT.isoformat(), "mw": 5.0}], None)


# ═══════════════════════════════════════════════════════════════════════════
# PG — wind_outlook's SQL on a real Postgres
# ═══════════════════════════════════════════════════════════════════════════

_DDL = """
CREATE TABLE implied_gen_area_hourly (
    tech text NOT NULL, area_kind text NOT NULL, area text NOT NULL, model text NOT NULL,
    init_ts timestamptz NOT NULL, target_ts timestamptz NOT NULL, lead_h smallint NOT NULL,
    lead_band text NOT NULL, weather_step_h smallint NOT NULL,
    registry_mw double precision NOT NULL, calibrated_mw double precision,
    calibration_id bigint, outage_mw_subtracted double precision NOT NULL,
    ac_mw_total double precision NOT NULL, n_sites integer NOT NULL,
    source_posted_ts timestamptz, method_version text NOT NULL,
    written_at timestamptz NOT NULL DEFAULT now(),
    weather_source text, scored_registry_mw double precision,
    scored_mw_total double precision, cap_mw_subtracted double precision,
    PRIMARY KEY (tech, area_kind, area, model, init_ts, target_ts));
CREATE TABLE implied_gen_calibration (
    calibration_id bigint PRIMARY KEY, tech text NOT NULL, area text NOT NULL,
    lead_band text NOT NULL, intercept_mw double precision NOT NULL, slope double precision NOT NULL,
    fit_start date NOT NULL, fit_end date NOT NULL, n_hours integer NOT NULL, n_days integer NOT NULL,
    fitted_at timestamptz NOT NULL DEFAULT now(), method_version text NOT NULL,
    fit_lead_min smallint, fit_lead_max smallint, fit_rows integer);   -- pantry migration 272
CREATE TABLE implied_gen_wind_sites (
    plant_code integer PRIMARY KEY, plant_name text, nameplate_mw numeric, turbine_model text,
    turbine_model_basis text, n_turbines integer, n_turbines_basis text, rotor_m numeric,
    rotor_basis text, hub_height_m numeric, hub_height_basis text, curve_turbine_type text,
    curve_hub_height_m numeric, curve_basis text, hub text, counts_in_hub_actual text,
    counts_in_hub_actual_basis text, export_cap_group text, export_cap_mw numeric,
    export_cap_basis text, ba_code text, state text, county text, latitude double precision,
    longitude double precision, hrrr_dist_km double precision);
CREATE TABLE implied_gen_site_latest (
    tech text NOT NULL, plant_code integer NOT NULL, model text NOT NULL,
    target_ts timestamptz NOT NULL, init_ts timestamptz NOT NULL, lead_h smallint NOT NULL,
    implied_mw double precision NOT NULL, outage_mw_subtracted double precision NOT NULL,
    method_version text NOT NULL, weather_source text, cap_mw_subtracted double precision,
    PRIMARY KEY (tech, plant_code, model, target_ts));
"""

FITTED_AT = datetime(2026, 10, 4, 15, 9, tzinfo=UTC)


@pytest.fixture(scope="module")
def pg():
    initdb = shutil.which("initdb") or "/usr/lib/postgresql/16/bin/initdb"
    if not os.path.exists(initdb):
        pytest.skip("no local Postgres (initdb) on this box")
    psycopg = pytest.importorskip("psycopg")
    bindir = os.path.dirname(initdb)
    d = tempfile.mkdtemp(prefix="pg_wind_")
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
    """Backfill: one 06Z issuance a day, leads 1-66, for 2026-09-01..10-02
    (as production's). Then the live 12Z cycle of 10-04, leads 1-240, written
    AFTER the line was fitted. Plus traps the derivation must not count:
      - an hrrr_gfs row of the right band written after fitted_at, inside the window
      - another model's rows at lead 100 inside the window
      - rows with scored_registry_mw = 0 (night/no scored set) at lead 70
      - another area_kind's rows (hub NP15) at lead 110
    """
    ins = ("INSERT INTO implied_gen_area_hourly (tech, area_kind, area, model, init_ts, target_ts, "
           "lead_h, lead_band, weather_step_h, registry_mw, outage_mw_subtracted, ac_mw_total, n_sites, "
           "method_version, written_at, weather_source, scored_registry_mw, scored_mw_total, cap_mw_subtracted) "
           "VALUES ('wind',%s,%s,%s,%s,%s,%s,%s,1,%s,0,10024,113,'wind_v1',%s,%s,%s,10024,0)")
    rows = []
    early = datetime(2026, 10, 3, 18, tzinfo=UTC)
    d = datetime(2026, 9, 1, 6, tzinfo=UTC)
    while d <= datetime(2026, 10, 2, 6, tzinfo=UTC):
        for lead in range(1, 67):
            rows.append(("hub_sum", "HUBSUM", "hrrr_gfs", d, d + timedelta(hours=lead - 1), lead,
                         band_of(lead), 1000.0, early, "hrrr_80m" if lead <= 48 else "gfs_100m", 900.0))
        d += timedelta(days=1)
    live = datetime(2026, 10, 4, 12, tzinfo=UTC)
    for lead in range(1, 241):
        rows.append(("hub_sum", "HUBSUM", "hrrr_gfs", live, live + timedelta(hours=lead - 1), lead,
                     band_of(lead), 1000.0, datetime(2026, 10, 4, 16, 2, tzinfo=UTC),
                     "hrrr_80m" if lead <= 48 else "gfs_100m", 900.0))
    late = datetime(2026, 9, 20, 0, tzinfo=UTC)                    # trap 1: written after the fit
    rows.append(("hub_sum", "HUBSUM", "hrrr_gfs", late, late + timedelta(hours=89), 90, "h49_120",
                 1.0, FITTED_AT + timedelta(minutes=1), "gfs_100m", 900.0))
    rows.append(("hub_sum", "HUBSUM", "gfs", late, late + timedelta(hours=99), 100, "h49_120",  # trap 2
                 1.0, early, "gfs_100m", 900.0))
    rows.append(("hub_sum", "HUBSUM", "hrrr_gfs", late + timedelta(hours=1),                  # trap 3
                 late + timedelta(hours=70), 70, "h49_120", 1.0, early, "gfs_100m", 0.0))
    rows.append(("hub", "NP15", "hrrr_gfs", late, late + timedelta(hours=109), 110, "h49_120",  # trap 4
                 1.0, early, "gfs_100m", 900.0))
    with conn.cursor() as cur:
        cur.executemany(ins, rows)
        cur.execute("INSERT INTO implied_gen_calibration VALUES "
                    "(676,'wind','HUBSUM','h49_120',1551.8214,0.644592,'2026-09-05','2026-10-02',499,28,%s,'wind_v1',49,66,499),"
                    "(675,'wind','HUBSUM','h25_48',82.2611,0.638316,'2026-09-05','2026-10-02',667,28,%s,'wind_v1',25,48,667)",
                    (FITTED_AT, FITTED_AT))
        cur.executemany(
            "INSERT INTO implied_gen_wind_sites (plant_code, plant_name, nameplate_mw, hub, ba_code, state, "
            "counts_in_hub_actual, export_cap_group, export_cap_mw) VALUES (%s,%s,%s,%s,'CISO','CA',%s,%s,%s)",
            [(1, "A", 100, "SP15", "yes", None, None), (2, "B", 200, "NP15", "unknown", None, None),
             (3, "C", 50, None, None, None, None)])
        cur.executemany(
            "INSERT INTO implied_gen_site_latest VALUES ('wind',%s,'hrrr_gfs',%s,%s,%s,%s,0,'wind_v1',%s,0)",
            [(code, live + timedelta(hours=k), live, k + 1, 10.0 * code, "hrrr_80m" if k < 48 else "gfs_100m")
             for code in (1, 2) for k in range(0, 72)])


def test_PG_W5_fit_leads_are_the_lines_stored_columns(pg):
    # d091608: was "derived fit leads are the leads the fit used" (504 rows
    # counted by the LATERAL); now the line's own columns, whatever the rows say
    rows = pg.execute(wo.CALIBRATION_SQL, {"tech": "wind", "area_kind": "hub_sum", "area": "HUBSUM",
                                           "model": "hrrr_gfs", "ids": [675, 676]}).fetchall()
    by = {r["lead_band"]: r for r in rows}
    assert (by["h49_120"]["fit_lead_min"], by["h49_120"]["fit_lead_max"]) == (49, 66)
    assert (by["h25_48"]["fit_lead_min"], by["h25_48"]["fit_lead_max"]) == (25, 48)
    assert by["h49_120"]["fit_rows"] == by["h49_120"]["n_hours"] == 499


def test_PG_W5_hours_and_issuance_read_the_live_cycle(pg):
    key = {"tech": "wind", "area_kind": "hub_sum", "area": "HUBSUM", "model": "hrrr_gfs"}
    iss = pg.execute(wo.ISSUANCE_NEWEST_SQL, key).fetchone()
    assert iss["init_ts"] == datetime(2026, 10, 4, 12, tzinfo=UTC)
    assert iss["prev_init_ts"] == datetime(2026, 10, 2, 6, tzinfo=UTC)
    hours = pg.execute(wo.HOURS_SQL, {**key, "init": iss["init_ts"], "prev_init": iss["prev_init_ts"]}).fetchall()
    assert len(hours) == 240
    assert {h["weather_source"] for h in hours if h["lead_h"] <= 48} == {"hrrr_80m"}
    assert hours[0]["prev_lead_h"] == 55 and hours[12]["prev_lead_h"] is None  # 54 h earlier, to lead 66


def test_PG_W9_sites_lateral_reads_the_window(pg):
    t = datetime(2026, 10, 4, 20, tzinfo=UTC)
    rows = pg.execute(wo.SITES_SQL, {"tech": "wind", "model": "hrrr_gfs", "lo": t,
                                     "hi": t + timedelta(hours=1)}).fetchall()
    assert [(r["plant_code"], r["n_hours"], r["implied_mw"]) for r in rows] == [(1, 1, 10.0), (2, 1, 20.0)]
    lo, hi = wo.pacific_day_bounds(date(2026, 10, 5))
    day = pg.execute(wo.SITES_SQL, {"tech": "wind", "model": "hrrr_gfs", "lo": lo, "hi": hi}).fetchall()
    assert [r["n_hours"] for r in day] == [24, 24]
    assert day[0]["weather_sources"] == ["hrrr_80m"]          # leads 20-43
    seam_day = pg.execute(wo.SITES_SQL, {"tech": "wind", "model": "hrrr_gfs", "lo": hi,
                                         "hi": hi + timedelta(hours=24)}).fetchall()
    assert seam_day[0]["weather_sources"] == ["gfs_100m", "hrrr_80m"]   # leads 44-67


# ═══════════════════════════════════════════════════════════════════════════
# P1 — production's live cycle (init 2026-10-04 12Z), through the route
# ═══════════════════════════════════════════════════════════════════════════

def _sample():
    import importlib.util
    spec = importlib.util.spec_from_file_location("wind_sample", RECEIPTS / "sample.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_P1_production_hubsum(client, monkeypatch):
    s = _sample()
    b = s.load()
    body = get(client, monkeypatch, s.outlook_pool(b, "HUBSUM"),
               "/api/generation/wind/outlook?area_kind=hub_sum").json()
    assert body["issuance"]["init_ts"] == "2026-10-05T00:00:00+00:00"
    assert body["issuance"]["previous_init_ts"] == "2026-10-04T18:00:00+00:00"
    assert len(body["hours"]) == 240
    h = {x["lead_h"]: x for x in body["hours"]}
    assert round(h[48]["registry_mw"]) == 947 and round(h[48]["calibrated_mw"]) == 712
    assert round(h[49]["registry_mw"]) == 681 and round(h[49]["calibrated_mw"]) == 1959
    # the wind writer is gated (pantry d091599): beyond lead 66 it stores no
    # figure and no line, so the hour says no_line and nothing is withheld here
    raw = {r["lead_h"]: r for r in b["areas"]["HUBSUM"]["hours"]}
    assert raw[67]["calibrated_mw"] is None and raw[67]["calibration_id"] is None
    assert h[67]["calibrated_mw"] is None and h[67]["calibrated_absent_reason"] == "no_line"
    assert body["calibration_gaps"] == []
    c = body["calibration"]["h49_120"]
    assert (c["calibration_id"], c["intercept_mw"], c["slope"], c["n_hours"]) == (696, 1483.7487, 0.699056, 499)
    assert (c["fit_lead_min"], c["fit_lead_max"], c["fit_rows"]) == (49, 66, 499)
    assert c["fit_leads_source"] == "stored" and "fit_issuances" not in c
    assert body["seams"]["weather"]["last_lead"] == WRITER["SEAM_LEAD (implied_gen/wind_method.py)"] == 48
    assert body["seams"]["calibration"]["last_calibrated_lead"] == 66
    assert body["seams"]["calibration"]["reason"] == "no_line"
    sc = body["scores"]
    assert [sc[b_]["calibrated"]["mae_pct_installed"] for b_ in ("h01_06", "h07_24", "h25_48")] == [
        5.1817, 6.0636, 7.1714]
    assert (sc["h49_120"]["calibrated"]["lead_min"], sc["h49_120"]["calibrated"]["lead_max"]) == (49, 66)
    assert round(sc["dam_comparable"]["caiso_dam"]["mae_pct_installed"], 2) == 4.73
    crossing = [d for d in body["days"] if d["crosses_calibration_seam"]]
    assert [d["day"] for d in crossing] == ["2026-10-07"] and crossing[0]["energy_mwh"] is None
    assert body["fleet"]["export_caps"][0]["export_cap_mw"] == 2131.0


def test_P1_production_ciso_and_zp26(client, monkeypatch):
    s = _sample()
    b = s.load()
    ciso = get(client, monkeypatch, s.outlook_pool(b, "CISO"),
               "/api/generation/wind/outlook?area_kind=ba&area=CISO").json()
    assert "caiso_dam" not in ciso
    assert ciso["calibration"]["h49_120"]["calibration_id"] == 684
    assert ciso["calibration"]["h49_120"]["fit_lead_max"] == 66
    assert {v["registry"]["actual_source"] for v in ciso["scores"].values()
            if isinstance(v["registry"], dict)} == {"fuel_mix"}
    main._wind_outlook_cache.clear()
    zp = get(client, monkeypatch, s.outlook_pool(b, "ZP26"),
             "/api/generation/wind/outlook?area_kind=hub&area=ZP26").json()
    assert zp["scores"] is None and zp["unscaled"] is True and zp["seams"]["calibration"] is None
    assert zp["calibration_gaps"] == []


def test_P1_production_sites(client, monkeypatch):
    s = _sample()
    b = s.load()
    body = get(client, monkeypatch, s.sites_pool(b, "target_2026_10_05T20Z"),
               "/api/generation/wind/sites?target=2026-10-05T20:00:00Z").json()
    assert body["plant_count"] == 323 and body["init_ts"] == "2026-10-05T00:00:00+00:00"
    sz = [p for p in body["plants"] if p["export_cap_group"] == "sunzia"]
    assert [p["plant_code"] for p in sz] == [66923, 66924]
    assert all(p["export_cap_mw"] == 2131.0 and p["export_cap_basis"] for p in sz)
