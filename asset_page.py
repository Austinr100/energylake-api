"""The asset page — d091635 (API half), D-09-25-157, D-09-25-109.

    GET /api/generation/asset?plant_code=&tech=   one plant, the newest cycle's hours
    GET /api/generation/assets?q=                 solar and wind plants by name,
                                                  code or county (at most 20)

READ-ONLY. The writers own every table read here: implied_gen_sites (solar,
one row per generator), implied_gen_wind_sites (wind, one row per plant),
implied_gen_site_latest (the newest cycle, per plant) and implied_gen_scores
(per area, per lead band; there is NO per-plant score). This module holds the
SQL and the pure shaping; main.py holds the routes, the memo and the statement
timeout (D-09-25-75), and reuses the solar section's memo plumbing.

ONE PLANT, ONE KEY. Every read names one (tech, plant_code) — this lane's
(dataset, series) — and walks the primary key:

    implied_gen_sites        (tech, plant_code, generator_id)   PK prefix
    implied_gen_wind_sites   (plant_code)                       PK lookup
    implied_gen_site_latest  (tech, plant_code, model, target_ts) PK range
    implied_gen_scores       (tech, area, lead_band, who, ...)  one LIMIT 1 per
                                                                (band, who): solar's
                                                                statement, unchanged

THE DRIVERS ARE DETECTED, NOT ASSUMED (D-09-25-157, sibling lane d091634,
pantry migration 280). Until 280 is applied and its writer has run, the driver
columns either do not exist or are null. The route reads the catalog for the
columns it knows by name (a fixed list, never request text), selects only the
ones that exist, and names the rest in `drivers.absent` with the reason. A
plant with no drivers answers 200 with its curve; it never fails.

THE SEARCH (/assets). /atlas's "Search node / plant..." box was measured first
(dashboard src/components/atlas/AtlasMap.tsx `buildNodeIndex` / `NodeSearch`):
it reads no API at all. It indexes the static CAISO pnode GeoJSON in memory,
`pnode_id` + the `plant_name` joined onto it, so it has no EIA plant_code, no
county, no tech, and no plant outside CAISO's pnodes. It cannot answer "which
solar or wind plant is this", so /assets reads the two registries. Both are
small and refreshed in full (1,914 solar units, 323 wind plants on 2026-10-06),
so a predicate over them is a bounded scan; plans in
docs/receipts/asset-page-api-d091635/plans.md.
"""

from __future__ import annotations

import re
from typing import Optional

import solar_outlook as so
import wind_outlook as wo
from solar_outlook import _f, _iso

TECHS = {"solar": so.TECH, "wind": wo.TECH}
MODELS = {"solar": so.DEFAULT_MODEL, "wind": wo.DEFAULT_MODEL}

# The plant figure is the REGISTRY figure: no line is fitted per plant. So the
# hub's measured error it is set beside is the hub's registry score, like for
# like, band by band (the outlook routes' own rows, never a neighbour's band).
SCORE_WHO = "registry"
SCORE_BANDS = so.LEAD_BANDS

# The lead bands' leads, for an hour of implied_gen_site_latest (which stores
# lead_h and not the band). The writer's edges (pantry implied_gen bands).
BAND_LEADS = {"h01_06": (1, 6), "h07_24": (7, 24), "h25_48": (25, 48),
              "h49_120": (49, 120), "h121_240": (121, 240)}

NO_PER_PLANT_SCORE = "no per-plant score: there are no public hourly per-plant actuals"

# D-09-25-157: the drivers d091634 adds under pantry migration 280. Fixed names;
# the SELECT is built from the intersection of these and the catalog, so no
# request text ever reaches it.
HOUR_DRIVERS = {
    "solar": ("ghi_wm2", "clearsky_ghi_wm2", "clearsky_mw", "tcc_pct", "precip_mm"),
    "wind": ("hub_ws_ms", "gust_ms"),
}
CURVE_DRIVERS = ("cut_in_ms", "rated_ms", "cut_out_ms")
DRIVERS_LANE = "d091634 (pantry migration 280)"

SEARCH_MAX = 20
# What each rank matched, in rank order (the SQL's CASE).
MATCHED = ("plant_code", "name_prefix", "name", "county")
SEARCH_MIN_LEN = 2
SEARCH_MAX_LEN = 64
_PLANT_CODE_RE = re.compile(r"^\d{1,7}$")


# ── Params ──────────────────────────────────────────────────────────────────

def parse_tech(tech: Optional[str]) -> str:
    if tech not in TECHS:
        raise ValueError(f"tech must be one of {sorted(TECHS)}, got {tech!r}")
    return tech


def parse_plant_code(raw: Optional[str]) -> int:
    if raw is None or not _PLANT_CODE_RE.match(raw.strip()):
        raise ValueError(f"plant_code must be an EIA plant code (1-7 digits), got {raw!r}")
    code = int(raw.strip())
    if code <= 0:
        raise ValueError(f"plant_code must be positive, got {raw!r}")
    return code


def parse_query(q: Optional[str]) -> str:
    q = (q or "").strip()
    if len(q) < SEARCH_MIN_LEN:
        raise ValueError(f"q must be at least {SEARCH_MIN_LEN} characters, got {q!r}")
    if len(q) > SEARCH_MAX_LEN:
        raise ValueError(f"q must be at most {SEARCH_MAX_LEN} characters")
    return q


def band_of(lead: Optional[int]) -> Optional[str]:
    if lead is None:
        return None
    for band, (lo, hi) in BAND_LEADS.items():
        if lo <= lead <= hi:
            return band
    return None


# ── SQL: the plant ──────────────────────────────────────────────────────────

# The catalog, for the columns this route knows by name. One statement; its
# parameters are this module's constants.
COLUMNS_SQL = """
    SELECT table_name, column_name
      FROM information_schema.columns
     WHERE table_schema = current_schema()
       AND table_name = ANY(%(tables)s)
       AND column_name = ANY(%(columns)s)
"""


def columns_params(tech: str) -> dict:
    return {"tables": ["implied_gen_site_latest", "implied_gen_wind_sites"],
            "columns": list(HOUR_DRIVERS[tech]) + (list(CURVE_DRIVERS) if tech == "wind" else [])}


SOLAR_UNITS_SQL = """
    SELECT plant_code, generator_id, plant_name, latitude, longitude,
           ac_mw::float8 AS ac_mw, dc_mw::float8 AS dc_mw, dc_basis,
           mount, mount_basis, tilt_deg::float8 AS tilt_deg, tilt_basis,
           azimuth_deg::float8 AS azimuth_deg, azimuth_basis, bifacial,
           hub, hub_method, ba_code, state, county, outage_resource_id,
           equipment_vintage, registry_vintage, method_version
      FROM implied_gen_sites
     WHERE tech = %(tech)s AND plant_code = %(plant_code)s
     ORDER BY generator_id
"""


def wind_site_sql(curve_cols: tuple = ()) -> str:
    """`curve_cols`: the power-curve thresholds present in the catalog (280)."""
    extra = "".join(f",\n           s.{c}::float8 AS {c}" for c in curve_cols if c in CURVE_DRIVERS)
    return f"""
    SELECT {wo.SITE_COLS},
           s.hub_method, s.op_year_mw_wtd, s.outage_resource_id, s.method_version{extra}
      FROM implied_gen_wind_sites s
     WHERE s.plant_code = %(plant_code)s
"""


def hours_sql(driver_cols: tuple = ()) -> str:
    """The newest cycle's hours for one plant: a PK range on
    (tech, plant_code, model, target_ts). `driver_cols` are the drivers present
    in the catalog; each is one of HOUR_DRIVERS' fixed names."""
    known = {c for cs in HOUR_DRIVERS.values() for c in cs}
    extra = "".join(f", l.{c}" for c in driver_cols if c in known)
    return f"""
    SELECT l.target_ts, l.init_ts, l.lead_h, l.weather_source, l.implied_mw,
           l.outage_mw_subtracted, l.cap_mw_subtracted, l.method_version{extra}
      FROM implied_gen_site_latest l
     WHERE l.tech = %(tech)s AND l.plant_code = %(plant_code)s
       AND l.model = %(model)s
     ORDER BY l.target_ts
"""


# The hub's score: solar's statement for area_kind 'hub', with wind's extra
# columns for wind. Read once per plant, for its hub, at its method_version.
SCORES_SQL = {"solar": so.SCORES_SQL["hub"], "wind": wo.SCORES_SQL["hub"]}


# ── SQL: the search ─────────────────────────────────────────────────────────
#
# One statement per registry. %(pat)s is the query with LIKE's wildcards
# escaped; %(code)s is the query as a plant code, or null. Ranked in SQL so
# the LIMIT keeps the best: code match, name prefix, name, county; then MW.

_RANK = """CASE WHEN %(code)s::int IS NOT NULL AND {code} = %(code)s::int THEN 0
                WHEN {name} ILIKE %(prefix)s ESCAPE '\\' THEN 1
                WHEN {name} ILIKE %(pat)s ESCAPE '\\' THEN 2
                ELSE 3 END"""

SEARCH_SOLAR_SQL = f"""
    SELECT plant_code, min(plant_name) AS plant_name, min(county) AS county,
           min(state) AS state, min(hub) AS hub, min(ba_code) AS ba_code,
           sum(ac_mw)::float8 AS mw, min(latitude) AS latitude,
           min(longitude) AS longitude,
           min({_RANK.format(code="plant_code", name="plant_name")}) AS rank
      FROM implied_gen_sites
     WHERE tech = %(tech)s
       AND (plant_name ILIKE %(pat)s ESCAPE '\\' OR county ILIKE %(pat)s ESCAPE '\\'
            OR plant_code = %(code)s::int)
     GROUP BY plant_code
     ORDER BY rank, mw DESC NULLS LAST, plant_code
     LIMIT {SEARCH_MAX}
"""

SEARCH_WIND_SQL = f"""
    SELECT plant_code, plant_name, county, state, hub, ba_code,
           nameplate_mw::float8 AS mw, latitude, longitude,
           {_RANK.format(code="plant_code", name="plant_name")} AS rank
      FROM implied_gen_wind_sites
     WHERE plant_name ILIKE %(pat)s ESCAPE '\\' OR county ILIKE %(pat)s ESCAPE '\\'
           OR plant_code = %(code)s::int
     ORDER BY rank, mw DESC NULLS LAST, plant_code
     LIMIT {SEARCH_MAX}
"""


def _like_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def search_params(q: str) -> dict:
    esc = _like_escape(q)
    code = int(q) if _PLANT_CODE_RE.match(q) else None
    return {"pat": f"%{esc}%", "prefix": f"{esc}%", "code": code}


# ── Shaping: the plant ──────────────────────────────────────────────────────

def detect(tech: str, catalog_rows: list[dict]) -> dict:
    """{"hour": (present hour drivers, in HOUR_DRIVERS order),
        "curve": (present curve thresholds)}."""
    have = {(r["table_name"], r["column_name"]) for r in catalog_rows}
    hour = tuple(c for c in HOUR_DRIVERS[tech] if ("implied_gen_site_latest", c) in have)
    curve = tuple(c for c in CURVE_DRIVERS if ("implied_gen_wind_sites", c) in have) if tech == "wind" else ()
    return {"hour": hour, "curve": curve}


def _plant_solar(units: list[dict]) -> dict:
    u0 = units[0]
    outage_ids = sorted({x for u in units for x in (u["outage_resource_id"] or "").split("|") if x})
    return {
        "plant_code": u0["plant_code"],
        "plant_name": u0["plant_name"],
        "tech": "solar",
        "latitude": _f(u0["latitude"]),
        "longitude": _f(u0["longitude"]),
        "county": u0["county"],
        "state": u0["state"],
        "ba_code": u0["ba_code"],
        "hub": u0["hub"],
        "hub_method": u0["hub_method"],
        "mw": round(sum(float(u["ac_mw"] or 0.0) for u in units), 3),
        "mw_kind": "ac",
        "dc_mw": (round(sum(float(u["dc_mw"]) for u in units), 3)
                  if all(u["dc_mw"] is not None for u in units) else None),
        "units": [{**so._unit(u)} for u in units],
        "outage_resource_ids": outage_ids,
        "equipment_vintage": u0["equipment_vintage"],
        "registry_vintage": u0["registry_vintage"],
    }


def _plant_wind(s: dict, curve_cols: tuple) -> dict:
    curve = {
        "turbine_type": s["curve_turbine_type"],
        "hub_height_m": _f(s["curve_hub_height_m"]),
        "basis": s["curve_basis"],
        **{c: _f(s.get(c)) for c in CURVE_DRIVERS},
    }
    return {
        "plant_code": s["plant_code"],
        "plant_name": s["plant_name"],
        "tech": "wind",
        "latitude": _f(s["latitude"]),
        "longitude": _f(s["longitude"]),
        "county": s["county"],
        "state": s["state"],
        "ba_code": s["ba_code"],
        "hub": s["hub"],
        "hub_method": s.get("hub_method"),
        "mw": _f(s["nameplate_mw"]),
        "mw_kind": "nameplate",
        "turbine_model": s["turbine_model"], "turbine_model_basis": s["turbine_model_basis"],
        "n_turbines": s["n_turbines"], "n_turbines_basis": s["n_turbines_basis"],
        "rotor_m": _f(s["rotor_m"]), "rotor_basis": s["rotor_basis"],
        "hub_height_m": _f(s["hub_height_m"]), "hub_height_basis": s["hub_height_basis"],
        "curve": curve,
        "counts_in_hub_actual": s["counts_in_hub_actual"],
        "counts_in_hub_actual_basis": s["counts_in_hub_actual_basis"],
        "export_cap_group": s["export_cap_group"],
        "export_cap_mw": _f(s["export_cap_mw"]),
        "export_cap_basis": s["export_cap_basis"],
        "hrrr_dist_km": _f(s["hrrr_dist_km"]),
        "op_year_mw_wtd": s.get("op_year_mw_wtd"),
        "outage_resource_ids": sorted(x for x in (s.get("outage_resource_id") or "").split("|") if x),
    }


def _hour(h: dict, drivers: tuple) -> dict:
    out = {
        "target_ts": _iso(h["target_ts"]),
        "lead_h": h["lead_h"],
        "lead_band": band_of(h["lead_h"]),
        "weather_source": h["weather_source"],
        "implied_mw": _f(h["implied_mw"]),
        "outage_mw_subtracted": _f(h["outage_mw_subtracted"]),
        "cap_mw_subtracted": _f(h["cap_mw_subtracted"]),
    }
    for c in drivers:
        out[c] = _f(h.get(c))
    return out


def weather_seam(hours: list[dict]) -> Optional[dict]:
    """Where the weather source changes, read off the served rows (wind's
    HRRR -> GFS). None when the run carries one source."""
    for a, b in zip(hours, hours[1:]):
        if a["weather_source"] != b["weather_source"]:
            return {"last_lead": a["lead_h"], "first_lead": b["lead_h"],
                    "before": a["weather_source"], "after": b["weather_source"],
                    "target_ts_before": a["target_ts"], "target_ts_after": b["target_ts"]}
    return None


def build_drivers(tech: str, found: dict, hours: list[dict]) -> dict:
    """What the run says beside the curve, and what it does not say yet.

    `present`: driver columns that exist AND carry a value on at least one
    hour. `absent`: every other expected driver, each with its reason —
    "column_absent" (280 not applied) or "all_null" (applied, not yet written).
    `null_by_source` (wind's gust) is a driver that is null on some sources by
    design: HRRR carries no gust, so those hours say so rather than read zero."""
    expected = list(HOUR_DRIVERS[tech])
    present, absent = [], []
    for c in expected:
        if c not in found["hour"]:
            absent.append({"driver": c, "reason": "column_absent"})
        elif not any(h.get(c) is not None for h in hours):
            absent.append({"driver": c, "reason": "all_null"})
        else:
            present.append(c)
    out = {"expected": expected, "present": present, "absent": absent,
           "lane": DRIVERS_LANE, "absence": None}
    if tech == "wind":
        null_src = sorted({h["weather_source"] for h in hours
                           if "gust_ms" in present and h.get("gust_ms") is None and h["weather_source"]})
        out["gust_null_sources"] = null_src
    if absent:
        reasons = {a["reason"] for a in absent}
        why = ("the driver columns do not exist yet" if reasons == {"column_absent"}
               else "the driver columns exist but this cycle wrote none" if reasons == {"all_null"}
               else "some driver columns do not exist yet and others are empty this cycle")
        out["absence"] = {
            "reason": "drivers_absent" if not present else "drivers_partial",
            "detail": f"{why}; they arrive with {DRIVERS_LANE} and its writer's next cycle. "
                      "The curve is the run's; nothing beside it is derived from it.",
        }
    return out


def build_curve_absence(plant: dict, found: dict) -> Optional[dict]:
    c = plant.get("curve")
    if c is None:
        return None
    missing = [k for k in CURVE_DRIVERS if c.get(k) is None]
    if not missing:
        return None
    return {"missing": missing,
            "reason": "column_absent" if any(k not in found["curve"] for k in missing) else "all_null",
            "detail": f"the power curve's thresholds arrive with {DRIVERS_LANE}"}


def build_trust(tech: str, plant: dict, score_rows: list[dict],
                method_version: Optional[str]) -> dict:
    """The hub's measured error for the plant's hub, band by band (registry,
    like for like with the plant's figure), or why there is none."""
    hub = plant.get("hub")
    out = {"per_plant": NO_PER_PLANT_SCORE, "area_kind": "hub", "area": hub,
           "who": SCORE_WHO, "method_version": method_version,
           "scores": None, "score_progress": None, "absence": None}
    if hub is None:
        out["absence"] = {"reason": "no_hub",
                          "detail": "this plant is in no CAISO trading hub, so no hub's "
                                    "measured error stands beside it"}
        return out
    if tech == "wind" and hub in wo.NO_SCORE_AREAS:
        out["absence"] = {"reason": "no_score_row",
                          "detail": f"{hub} has no score row of its own (D-09-25-126 clause 7); "
                                    f"it is scored inside {so.HUBSUM}"}
        return out
    if method_version is None:
        out["absence"] = {"reason": "no_run", "detail": "no cycle is banked for this plant"}
        return out
    extra = wo.SCORE_EXTRA if tech == "wind" else ()
    scores, progress = so.build_scores("hub", score_rows, extra)
    out["scores"] = {b: scores[b][SCORE_WHO] for b in SCORE_BANDS}
    out["score_progress"] = {b: progress.get(b, {}).get(SCORE_WHO)
                             for b in SCORE_BANDS if scores[b][SCORE_WHO] == so.NOT_YET_SCORED}
    out["band_leads"] = {b: list(BAND_LEADS[b]) for b in SCORE_BANDS}
    return out


def build_asset(*, tech: str, plant_code: int, found: dict, site_rows: list[dict],
                hour_rows: list[dict], score_rows: list[dict]) -> Optional[dict]:
    """The page's payload, or None when the plant is not in the registry."""
    if not site_rows:
        return None
    plant = _plant_solar(site_rows) if tech == "solar" else _plant_wind(site_rows[0], found["curve"])
    inits = {r["init_ts"] for r in hour_rows}
    init = max(inits) if inits else None
    kept = [r for r in hour_rows if r["init_ts"] == init]
    hours = [_hour(r, found["hour"]) for r in kept]
    mv = kept[0]["method_version"] if kept else None
    run = None
    if hours:
        run = {
            "model": MODELS[tech],
            "init_ts": _iso(init),
            "method_version": mv,
            "figure": "registry",
            "lead_h_first": hours[0]["lead_h"],
            "lead_h_last": hours[-1]["lead_h"],
            "weather_sources": list(dict.fromkeys(h["weather_source"] for h in hours
                                                  if h["weather_source"] is not None)),
            "weather_seam": weather_seam(hours),
            "weather_height_m": wo.WEATHER_HEIGHT_M if tech == "wind" else None,
            "hours_of_other_cycles_dropped": len(hour_rows) - len(kept),
            "outage_mw_max": max((h["outage_mw_subtracted"] or 0.0) for h in hours),
            "cap_mw_max": max((h["cap_mw_subtracted"] or 0.0) for h in hours),
        }
    body = {
        "label": wo.LABEL if tech == "wind" else so.LABEL,
        "tech": tech,
        "plant_code": plant_code,
        "plant": plant,
        "run": run,
        "hours": hours,
        "drivers": build_drivers(tech, found, hours),
        "curve_absence": build_curve_absence(plant, found) if tech == "wind" else None,
        "trust": build_trust(tech, plant, score_rows, mv),
        "absence": None,
    }
    if tech == "wind":
        body["attribution"] = wo.ATTRIBUTION
    if not hours:
        body["absence"] = {"reason": "no_site_rows",
                           "detail": "implied_gen_site_latest holds no row for this plant "
                                     "(it holds the newest cycle only)"}
    return body


# ── Shaping: the search ─────────────────────────────────────────────────────

def build_search(q: str, solar_rows: list[dict], wind_rows: list[dict]) -> dict:
    rows = ([{**r, "tech": "solar"} for r in solar_rows]
            + [{**r, "tech": "wind"} for r in wind_rows])
    rows.sort(key=lambda r: (r["rank"], -(r["mw"] or 0.0), r["plant_code"]))
    out = [{
        "plant_code": r["plant_code"],
        "plant_name": r["plant_name"],
        "tech": r["tech"],
        "county": r["county"],
        "state": r["state"],
        "hub": r["hub"],
        "ba_code": r["ba_code"],
        "mw": round(r["mw"], 3) if r["mw"] is not None else None,
        "mw_kind": "ac" if r["tech"] == "solar" else "nameplate",
        "latitude": _f(r["latitude"]),
        "longitude": _f(r["longitude"]),
        "matched": MATCHED[r["rank"]],
    } for r in rows[:SEARCH_MAX]]
    return {
        "q": q,
        "max": SEARCH_MAX,
        "count": len(out),
        "truncated": len(rows) > SEARCH_MAX,
        "results": out,
        "source": "implied_gen_sites (solar) and implied_gen_wind_sites (wind): the "
                  "writers' registries. Not /atlas's box, which indexes CAISO pnodes.",
    }

