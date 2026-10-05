"""Wind generation outlook — d091590 (API half), D-09-25-126, D-09-25-127.

    GET /api/generation/wind/outlook   one area's issuance, hour by hour
    GET /api/generation/wind/sites     the newest cycle's plants, for the map

READ-ONLY. The writer (pantry d091589) owns every table read here; migration
269 added wind to 264's tables (rows with tech = 'wind') and made
implied_gen_wind_sites for the plant facts.

This is solar_outlook's payload with wind's differences, and it SHARES solar's
code rather than copying it: the parameter parsers, the issuance statements
(both are keyed on %(tech)s), the score statement and its shaping
(`_scores_sql` / `build_scores` with wind's extra columns), the actuals
statement and its HUBSUM rule (`_actuals_sql` / `build_actuals` with wind's
pairs), and, since d091608 (D-09-25-136), the calibration read, the gate, the
calibration seam and the calibration gaps: the fitted leads are the writer's,
stored on the line (pantry migration 272), and nothing here derives them.
Nothing is hard-coded to the lead that is true today (66). What is wind's own
is below, and only that:

  * every hour carries `weather_source` (hrrr_80m leads 1-48, gfs_100m 49-240)
    and `cap_mw_subtracted`; the payload names the WEATHER SEAM, read off the
    rows, never a constant;
  * the days block never adds a calibrated hour to a registry hour: a day across
    that seam is served in parts, the way the solar page serves its lead-120
    day (dashboard solarOutlook.dayCells);
  * scores carry `actual_source` and the MW by `counts_in_hub_actual` class;
    ZP26 has no score row of its own (D-09-25-126 clause 7) and is in HUBSUM;
  * hub areas pair with hub actuals, CISO with fuel-mix wind; never crossed;
  * CAISO's issue-time sentence beside its line, and the turbine-data
    attribution notice (ODbL-1.0 §4.3) on both routes.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

import solar_outlook as so
from solar_outlook import (_f, _iso, pacific_day_bounds, parse_area,  # noqa: F401 (re-exported)
                           parse_day, parse_instant, PT,
                           BEYOND_FIT, NO_LINE, FIT_LEADS_BASIS, _fitted, gate,
                           build_calibration, calibration_ids, calibration_seam,
                           calibration_gaps, _previous_mw)

TECH = "wind"

# D-09-25-109 naming windpowerlib. Verbatim the writer's (pantry
# scripts/implied_wind.py LABEL_WIND); tests/test_wind_outlook.py holds it to
# the banked copy in tests/fixtures/wind_outlook_d091590/.
LABEL = ("Weather-implied generation. What the weather says these plants should "
         "produce, from open-source models (windpowerlib) and public plant data. "
         "Not a forecast of metered output and not any plant's schedule.")

# ODbL-1.0 §4.3: every published figure derived from the curves carries it.
# Verbatim pantry implied_gen/wind_method.py CURVE_DATA_NOTICE.
ATTRIBUTION = ("Contains information from the Wind Turbine Library (OpenEnergy Platform, "
               "supply.wind_turbine_library, (c) Reiner Lemoine Institut), which is made "
               "available under the Open Database License (ODbL-1.0): "
               "https://opendatacommons.org/licenses/odbl/1-0/")

# D-09-25-126 clause 6, as the lane brief words it.
CAISO_ISSUE_SENTENCE = ("CAISO's day-ahead is issued between 06:09 PT the day before "
                        "and 06:09 PT on the day")

# The writer's one model name for the joined issuance (pantry
# implied_gen/wind_store.py MODEL_WIND): HRRR to the weather seam, GFS beyond.
MODELS = ("hrrr_gfs",)
DEFAULT_MODEL = "hrrr_gfs"

# The height each weather source's wind is taken at, for the site card's
# "distance from the weather's height".
WEATHER_HEIGHT_M = {"hrrr_80m": 80, "gfs_100m": 100}

NO_SCORE_AREAS = ("ZP26",)          # clause 7: a footnote, inside HUBSUM
SCORE_EXTRA = ("actual_source", "mw_yes", "mw_unknown", "mw_no")

def parse_model(model: Optional[str]) -> str:
    model = model or DEFAULT_MODEL
    if model not in MODELS:
        raise ValueError(f"model must be one of {list(MODELS)}, got {model!r}")
    return model


# ── SQL: the outlook ────────────────────────────────────────────────────────
#
# ISSUANCE    solar's statements (keyed on %(tech)s): 2 PK lookups
# HOURS       the issuance's rows beside the previous issuance's same target
#             hour, with wind's columns and the previous row's line and lead
# SCORES      solar's statement with wind's extra columns
# CALIBRATION solar's statement: the lines the rows carry, fitted leads stored
# ACTUALS     solar's statement over wind's (dataset, series) pairs
# FLEET       the area's plants from implied_gen_wind_sites (323 rows)

ISSUANCE_NEWEST_SQL = so.ISSUANCE_NEWEST_SQL
ISSUANCE_AT_SQL = so.ISSUANCE_AT_SQL

HOURS_SQL = f"""
    SELECT c.target_ts, c.lead_h, c.lead_band, c.weather_step_h, c.weather_source,
           c.registry_mw, c.calibrated_mw, c.calibration_id,
           c.outage_mw_subtracted, c.cap_mw_subtracted,
           c.scored_registry_mw, c.scored_mw_total,
           c.ac_mw_total, c.n_sites,
           c.source_posted_ts, c.method_version,
           p.registry_mw    AS prev_registry_mw,
           p.calibrated_mw  AS prev_calibrated_mw,
           p.calibration_id AS prev_calibration_id,
           p.lead_h         AS prev_lead_h,
           p.method_version AS prev_method_version
      FROM implied_gen_area_hourly c
      LEFT JOIN implied_gen_area_hourly p
        ON p.tech = c.tech AND p.area_kind = c.area_kind
       AND p.area = c.area AND p.model = c.model
       AND p.init_ts = %(prev_init)s
       AND p.target_ts = c.target_ts
     WHERE {so._AREA_KEY.format(a="c")}
       AND c.init_ts = %(init)s
     ORDER BY c.target_ts
"""

SCORES_SQL = {kind: so._scores_sql(kind, SCORE_EXTRA) for kind in so.AREA_KINDS}

CALIBRATION_SQL = so.CALIBRATION_SQL


def actual_pairs(area_kind: str, area: str) -> list[tuple[str, str, str]]:
    """(role, dataset, series). Hub areas pair with CAISO's hub actuals and
    CISO with the fuel mix's wind (D-09-25-126 clause 5): the two are never
    crossed. CAISO's day-ahead is a hub series, for hub areas only."""
    if area_kind == "hub":
        return [("actual", "caiso_renewables_hourly", f"{area}:Wind"),
                ("caiso_dam", "caiso_renewables_fcst_dam", f"{area}:Wind")]
    if area_kind == "hub_sum":
        return ([("actual", "caiso_renewables_hourly", f"{h}:Wind") for h in so.HUBS]
                + [("caiso_dam", "caiso_renewables_fcst_dam", f"{h}:Wind") for h in so.HUBS])
    if area_kind == "ba" and area == "CISO":
        return [("actual", "caiso_fuel_mix_hourly", "wind")]
    return []


ACTUALS_SQL = {
    key: so._actuals_sql(actual_pairs(*key))
    for key in ([("hub", h) for h in so.HUBS] + [("hub_sum", so.HUBSUM), ("ba", "CISO")])
}

SITE_COLS = """s.plant_code, s.plant_name, s.latitude, s.longitude,
           s.nameplate_mw::float8 AS nameplate_mw, s.hub, s.ba_code, s.state, s.county,
           s.turbine_model, s.turbine_model_basis, s.n_turbines, s.n_turbines_basis,
           s.rotor_m::float8 AS rotor_m, s.rotor_basis,
           s.hub_height_m::float8 AS hub_height_m, s.hub_height_basis,
           s.curve_turbine_type, s.curve_hub_height_m::float8 AS curve_hub_height_m, s.curve_basis,
           s.counts_in_hub_actual, s.counts_in_hub_actual_basis,
           s.export_cap_group, s.export_cap_mw::float8 AS export_cap_mw, s.export_cap_basis,
           s.hrrr_dist_km"""

# implied_gen_wind_sites is the registry, 323 plants, keyed by plant_code and
# refreshed in full. Read whole and filtered here; measured in the handback.
FLEET_SQL = f"""
    SELECT {SITE_COLS}
      FROM implied_gen_wind_sites s
     ORDER BY s.plant_code
"""


# ── SQL: the sites ──────────────────────────────────────────────────────────
#
# implied_gen_site_latest's key is (tech, plant_code, model, target_ts): a
# window on target_ts alone is a walk of the whole table (solar handback §6.3).
# So one LATERAL per plant, each a PK range on (tech, plant_code, model,
# target_ts), aggregated inside the lateral (d091551's shape).
SITES_SQL = f"""
    SELECT {SITE_COLS},
           l.n_hours, l.implied_mw, l.outage_mw_subtracted, l.cap_mw_subtracted,
           l.init_ts, l.init_ts_max, l.lead_h_min, l.lead_h_max,
           l.weather_sources, l.method_version
      FROM implied_gen_wind_sites s
     CROSS JOIN LATERAL (
        SELECT count(*) AS n_hours,
               sum(x.implied_mw)::float8 AS implied_mw,
               sum(x.outage_mw_subtracted)::float8 AS outage_mw_subtracted,
               sum(x.cap_mw_subtracted)::float8 AS cap_mw_subtracted,
               min(x.init_ts) AS init_ts, max(x.init_ts) AS init_ts_max,
               min(x.lead_h) AS lead_h_min, max(x.lead_h) AS lead_h_max,
               array_agg(DISTINCT x.weather_source) AS weather_sources,
               min(x.method_version) AS method_version
          FROM implied_gen_site_latest x
         WHERE x.tech = %(tech)s AND x.plant_code = s.plant_code
           AND x.model = %(model)s
           AND x.target_ts >= %(lo)s AND x.target_ts < %(hi)s
     ) AS l
     WHERE l.n_hours > 0
     ORDER BY s.plant_code
"""


# ── Shaping: seams and days ─────────────────────────────────────────────────

def _figure(h: dict) -> str:
    return "calibrated" if h["calibrated_mw"] is not None else "registry"


def weather_seam(hours: list[dict]) -> Optional[dict]:
    """Where the weather source changes, read off the rows. None when the
    issuance carries one source only."""
    for a, b in zip(hours, hours[1:]):
        if a["weather_source"] != b["weather_source"]:
            return {"last_lead": a["lead_h"], "first_lead": b["lead_h"],
                    "before": a["weather_source"], "after": b["weather_source"],
                    "target_ts_before": a["target_ts"], "target_ts_after": b["target_ts"],
                    "band_before": a["lead_band"], "band_after": b["lead_band"]}
    return None


def _part(hs: list[dict]) -> dict:
    fig = _figure(hs[0])
    vals = [(h["calibrated_mw"] if fig == "calibrated" else h["registry_mw"], h) for h in hs]
    peak_mw, peak_h = max(vals, key=lambda v: v[0])
    return {"figure": fig, "first_lead": hs[0]["lead_h"], "last_lead": hs[-1]["lead_h"],
            "hours": len(hs), "energy_mwh": round(sum(v for v, _h in vals), 3),
            "peak_mw": peak_mw, "peak_ts": peak_h["target_ts"]}


def build_days(hours: list[dict]) -> list[dict]:
    """Pacific days: energy and peak per FIGURE. A day whose hours cross the
    calibration seam is two parts (calibrated, then registry) and has no total
    and no single peak; a calibrated hour is never added to a registry hour.
    `hours` are served hours (target_ts ISO, calibrated_mw already gated)."""
    by_day: dict = {}
    for h in hours:
        t = datetime.fromisoformat(h["target_ts"])
        by_day.setdefault(t.astimezone(PT).date(), []).append(h)
    out = []
    for d in sorted(by_day):
        hs = by_day[d]
        parts, run = [], [hs[0]]
        for h in hs[1:]:
            if _figure(h) == _figure(run[-1]):
                run.append(h)
            else:
                parts.append(_part(run))
                run = [h]
        parts.append(_part(run))
        lo, hi = pacific_day_bounds(d)
        expected = int((hi - lo).total_seconds() // 3600)
        one = len(parts) == 1
        sources = list(dict.fromkeys(h["weather_source"] for h in hs))
        out.append({
            "day": d.isoformat(),
            "hours_in_day": expected,
            "hours_covered": len(hs),
            "complete": len(hs) == expected,
            "first_lead": hs[0]["lead_h"],
            "last_lead": hs[-1]["lead_h"],
            "figure": parts[0]["figure"] if one else "mixed",
            "crosses_calibration_seam": not one,
            "crosses_weather_seam": len(sources) > 1,
            "weather_sources": sources,
            "energy_mwh": parts[0]["energy_mwh"] if one else None,
            "peak_mw": parts[0]["peak_mw"] if one else None,
            "peak_ts": parts[0]["peak_ts"] if one else None,
            "parts": parts,
        })
    return out


# ── Shaping: scores, fleet ──────────────────────────────────────────────────

def build_scores(area_kind: str, area: str, rows: list[dict]) -> tuple[Optional[dict], Optional[dict], Optional[dict]]:
    """(scores, score_progress, scores_absence). ZP26 has no score row of its
    own (clause 7): both blocks are null and the absence says why."""
    if area in NO_SCORE_AREAS:
        return None, None, {"reason": "no_score_row",
                            "detail": f"{area} has no score row of its own "
                                      f"(D-09-25-126 clause 7); it is scored inside {so.HUBSUM}"}
    scores, progress = so.build_scores(area_kind, rows, SCORE_EXTRA)
    return scores, progress, None


def _in_area(s: dict, area_kind: Optional[str], area: Optional[str]) -> bool:
    if area_kind is None:
        return True
    if area_kind == "hub":
        return s["hub"] == area
    if area_kind == "hub_sum":
        return s["hub"] is not None
    if area_kind == "ba":
        return s["ba_code"] == area
    return s["state"] == area


def build_fleet(hours: list[dict], site_rows: list[dict], area_kind: str, area: str) -> dict:
    """The issuance's own totals, and the area's plants by hub-actual class
    and by export cap, from implied_gen_wind_sites."""
    first = hours[0] if hours else {}
    sites = [s for s in site_rows if _in_area(s, area_kind, area)]
    by_class: dict = {}
    for s in sites:
        k = s["counts_in_hub_actual"]
        c = by_class.setdefault(k, {"counts_in_hub_actual": k, "nameplate_mw": 0.0, "n_plants": 0})
        c["nameplate_mw"] += float(s["nameplate_mw"] or 0.0)
        c["n_plants"] += 1
    caps: dict = {}
    for s in sites:
        if s["export_cap_group"] is None:
            continue
        g = caps.setdefault(s["export_cap_group"], {
            "group": s["export_cap_group"], "export_cap_mw": _f(s["export_cap_mw"]),
            "export_cap_basis": s["export_cap_basis"], "plant_codes": [], "nameplate_mw": 0.0})
        g["plant_codes"].append(s["plant_code"])
        g["nameplate_mw"] += float(s["nameplate_mw"] or 0.0)
    return {
        "nameplate_mw_total": _f(first.get("ac_mw_total")),
        "n_sites": first.get("n_sites"),
        "scored_mw_total": _f(first.get("scored_mw_total")),
        "registry_plants": len(sites),
        "registry_nameplate_mw": round(sum(float(s["nameplate_mw"] or 0.0) for s in sites), 3),
        "by_counts_in_hub_actual": [
            {**v, "nameplate_mw": round(v["nameplate_mw"], 3)}
            for _k, v in sorted(by_class.items(), key=lambda kv: (kv[0] is None, kv[0] or ""))],
        "export_caps": [{**v, "nameplate_mw": round(v["nameplate_mw"], 3)}
                        for _k, v in sorted(caps.items())],
    }


# ── The outlook ─────────────────────────────────────────────────────────────

def build_outlook(*, area_kind: str, area: str, model: str,
                  issuance: Optional[dict], hours: list[dict], score_rows: list[dict],
                  lines: list[dict], actual_rows: list[dict],
                  site_rows: list[dict]) -> dict:
    scores, progress, scores_absence = build_scores(area_kind, area, score_rows)
    actuals, dam = so.build_actuals(area_kind, area, actual_rows, actual_pairs(area_kind, area))
    body: dict = {
        "label": LABEL,
        "attribution": ATTRIBUTION,
        "tech": TECH,
        "area_kind": area_kind,
        "area": area,
        "unit": "MW",
        "issuance": None,
        "hours": [],
        "unscaled": True,
        "seams": {"weather": None, "calibration": None},
        "calibration_gaps": [],
        "calibration": build_calibration(hours, lines),
        "calibration_fit_leads_basis": FIT_LEADS_BASIS,
        "days": [],
        "scores": scores,
        "score_progress": progress,
        "scores_absence": scores_absence,
        "actuals": actuals,
    }
    if dam is not None:
        body["caiso_dam"] = dam
        body["caiso_dam_issued"] = CAISO_ISSUE_SENTENCE
    body["weather_height_m"] = WEATHER_HEIGHT_M
    body["fleet"] = build_fleet(hours, site_rows, area_kind, area)
    body["absence"] = None
    if issuance is None or not hours:
        body["absence"] = {"reason": "no_issuance",
                           "detail": f"no {model} issuance is banked for {area_kind}={area}"}
        return body
    gated = gate(hours, lines)
    prev_mv = next((h["prev_method_version"] for h in hours
                    if h.get("prev_method_version") is not None), None)
    posted = [h["source_posted_ts"] for h in hours if h.get("source_posted_ts") is not None]
    body["issuance"] = {
        "model": model,
        "init_ts": _iso(issuance["init_ts"]),
        "method_version": hours[0]["method_version"],
        "previous_init_ts": _iso(issuance.get("prev_init_ts")),
        "previous_method_version": prev_mv,
        "source_posted_ts": _iso(max(posted)) if posted else None,
        "lead_h_first": hours[0]["lead_h"],
        "lead_h_last": hours[-1]["lead_h"],
    }
    body["hours"] = [{
        "target_ts": _iso(h["target_ts"]),
        "lead_h": h["lead_h"],
        "lead_band": h["lead_band"],
        "weather_source": h["weather_source"],
        "weather_step_h": h["weather_step_h"],
        "registry_mw": _f(h["registry_mw"]),
        "calibrated_mw": h["shown_calibrated_mw"],
        "calibrated_absent_reason": h["calibrated_absent_reason"],
        "calibration_id": h["calibration_id"],
        "outage_mw_subtracted": _f(h["outage_mw_subtracted"]),
        "cap_mw_subtracted": _f(h["cap_mw_subtracted"]),
        "scored_registry_mw": _f(h["scored_registry_mw"]),
        "previous_mw": _previous_mw(h),
        "previous_registry_mw": _f(h["prev_registry_mw"]),
        "previous_calibrated_mw": h["shown_prev_calibrated_mw"],
    } for h in gated]
    body["unscaled"] = all(h["calibrated_mw"] is None for h in body["hours"])
    body["seams"] = {"weather": weather_seam(body["hours"]),
                     "calibration": calibration_seam(body["hours"])}
    body["calibration_gaps"] = calibration_gaps(gated)
    body["days"] = build_days(body["hours"])
    return body


# ── The sites ───────────────────────────────────────────────────────────────

def build_sites(*, model: str, target: Optional[datetime], day: Optional[date],
                lo: datetime, hi: datetime, area_kind: Optional[str], area: Optional[str],
                rows: list[dict]) -> dict:
    """One row per plant: the plant's facts with each basis, and the implied
    MW (target hour) or MWh (Pacific day) of the newest cycle, which is the
    REGISTRY figure: no line is fitted per plant. Capacity factor is against
    nameplate."""
    rows = [r for r in rows if _in_area(r, area_kind, area)]
    inits = {r["init_ts"] for r in rows if r["init_ts"] is not None}
    init = max(inits) if inits else None
    hours_expected = int((hi - lo).total_seconds() // 3600)
    plants = []
    for r in rows:
        if r["init_ts"] != init or r["init_ts_max"] != init:
            continue
        name = float(r["nameplate_mw"] or 0.0)
        mw = float(r["implied_mw"] or 0.0)
        row = {
            "plant_code": r["plant_code"],
            "plant_name": r["plant_name"],
            "latitude": _f(r["latitude"]),
            "longitude": _f(r["longitude"]),
            "nameplate_mw": _f(r["nameplate_mw"]),
            "hub": r["hub"], "ba_code": r["ba_code"], "state": r["state"], "county": r["county"],
            "turbine_model": r["turbine_model"], "turbine_model_basis": r["turbine_model_basis"],
            "n_turbines": r["n_turbines"], "n_turbines_basis": r["n_turbines_basis"],
            "rotor_m": _f(r["rotor_m"]), "rotor_basis": r["rotor_basis"],
            "hub_height_m": _f(r["hub_height_m"]), "hub_height_basis": r["hub_height_basis"],
            "curve_turbine_type": r["curve_turbine_type"],
            "curve_hub_height_m": _f(r["curve_hub_height_m"]), "curve_basis": r["curve_basis"],
            "counts_in_hub_actual": r["counts_in_hub_actual"],
            "counts_in_hub_actual_basis": r["counts_in_hub_actual_basis"],
            "export_cap_group": r["export_cap_group"],
            "export_cap_mw": _f(r["export_cap_mw"]),
            "export_cap_basis": r["export_cap_basis"],
            "hrrr_dist_km": _f(r["hrrr_dist_km"]),
            "weather_sources": sorted(r["weather_sources"] or []),
            "lead_h_min": r["lead_h_min"], "lead_h_max": r["lead_h_max"],
        }
        if target is not None:
            row["implied_mw"] = round(mw, 3)
            row["outage_mw_subtracted"] = round(float(r["outage_mw_subtracted"] or 0.0), 3)
            row["cap_mw_subtracted"] = round(float(r["cap_mw_subtracted"] or 0.0), 3)
            row["capacity_factor"] = round(mw / name, 4) if name else None
        else:
            n = int(r["n_hours"])
            row["implied_mwh"] = round(mw, 3)
            row["outage_mwh_subtracted"] = round(float(r["outage_mw_subtracted"] or 0.0), 3)
            row["cap_mwh_subtracted"] = round(float(r["cap_mw_subtracted"] or 0.0), 3)
            row["hours_covered"] = n
            row["capacity_factor"] = round(mw / (name * n), 4) if name and n else None
        plants.append(row)
    body = {
        "label": LABEL,
        "attribution": ATTRIBUTION,
        "tech": TECH,
        "model": model,
        "figure": "registry",
        "init_ts": _iso(init),
        "method_version": next((r["method_version"] for r in rows if r["init_ts"] == init), None),
        "target_ts": _iso(target),
        "day": _iso(day),
        "hours_expected": hours_expected,
        "area_kind": area_kind,
        "area": area,
        "weather_height_m": WEATHER_HEIGHT_M,
        "plant_count": len(plants),
        "nameplate_mw": round(sum(p["nameplate_mw"] or 0.0 for p in plants), 3),
        "plants": plants,
        "absence": None,
    }
    if not plants:
        body["absence"] = {"reason": "no_site_rows",
                           "detail": "implied_gen_site_latest holds no wind row for this "
                                     "window and area (it holds the newest cycle only)"}
    return body
