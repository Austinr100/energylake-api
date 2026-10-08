"""The asset page — d091635 (API half), D-09-25-157, D-09-25-109.

    GET /api/generation/asset?plant_code=&tech=   one plant, the newest cycle's hours
    GET /api/generation/assets?q=                 solar and wind plants by name,
                                                  code or county (at most 20)
    GET /api/generation/asset/runs?plant_code=&tech=&n=
                                                  d091667: one plant's newest runs,
                                                  run by run (implied_gen_site_history)

d091667 also adds `plant.equipment` to the wind asset body: the equipment as
implied_gen_wind_sites states it, every basis token verbatim beside one label
from BASIS_LABELS, and whether a rotor animation has what it needs.

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
from datetime import timedelta
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

# d091647 (pantry migration 284): the evidence behind any of its basis tokens,
# or why a rotor stays empty. Detected like the drivers: read where it exists.
EQUIPMENT_SOURCE = "equipment_source"
EQUIPMENT_LANE = "d091647 (pantry migration 284)"

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
            "columns": list(HOUR_DRIVERS[tech])
            + (list(CURVE_DRIVERS) + [EQUIPMENT_SOURCE] if tech == "wind" else [])}


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


def wind_site_sql(curve_cols: tuple = (), equipment_source: bool = False) -> str:
    """`curve_cols`: the power-curve thresholds present in the catalog (280);
    `equipment_source`: whether 284's column is (d091647)."""
    extra = "".join(f",\n           s.{c}::float8 AS {c}" for c in curve_cols if c in CURVE_DRIVERS)
    if equipment_source:
        extra += f",\n           s.{EQUIPMENT_SOURCE}"
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


def detect_equipment_source(catalog_rows: list[dict]) -> bool:
    """Whether implied_gen_wind_sites carries 284's equipment_source."""
    return any((r["table_name"], r["column_name"]) == ("implied_gen_wind_sites", EQUIPMENT_SOURCE)
               for r in catalog_rows)


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
        "equipment": build_equipment(s, curve_cols),
    }


# ── Shaping: the wind equipment (d091667) ───────────────────────────────────
#
# THE API STATES WHAT THE BANK STATES. Every basis token is served verbatim
# beside the one printed label below; a token not in this table is served as
# "unknown", never mapped to a near one. The labels are the writer's own
# definitions (pantry implied_gen/wind.py header and implied_gen/
# wind_equipment.py, d091589 / d091647; tokens per migration 284's CHECKs).
# This is the only place a basis is worded.

BASIS_LABELS = {
    "turbine_model_basis": {
        "eia860_sch3": "EIA-860 Schedule 3: the predominant model of the plant's largest generator",
        "uswtdb": "USWTDB: the most common model listed at the plant's EIA id",
        "public_record": "a named public document (equipment_source quotes it)",
        "none": "no source the writer reads states it",
    },
    "n_turbines_basis": {
        "eia860_sch3": "EIA-860 Schedule 3: summed over the plant's generators",
        "uswtdb": "USWTDB: the turbines listed at the plant's EIA id",
        "public_record": "a named public document (equipment_source quotes it)",
        "none": "no source the writer reads states it",
    },
    "rotor_basis": {
        "uswtdb": "USWTDB: the capacity-weighted mean rotor of the turbines listed at the "
                  "plant's EIA id (a blend where they differ)",
        "same_model_uswtdb": "USWTDB: the one rotor every turbine of the stated model carries, "
                             "at any plant (equipment_source counts them)",
        "model_designation": "read from the stated model's name, by the maker's naming convention",
        "public_record": "a named public document (equipment_source quotes it)",
        "none": "no source the writer reads states it (equipment_source says why)",
    },
    "hub_height_basis": {
        "eia860_sch3": "EIA-860 Schedule 3: MW-weighted over the plant's generators",
        "uswtdb": "USWTDB: the turbines listed at the plant's EIA id",
        "none": "no source the writer reads states it",
    },
    "curve_basis": {
        "vintage_band": "the named power curve of the plant's vintage band, at the band's hub "
                        "height: the curve the figure is converted with",
        "vintage_band_default_2010": "the 2010 vintage band's curve: the plant's vintage is unknown",
    },
}
UNKNOWN_BASIS_LABEL = "unknown basis token: served as the bank states it"

# The speed band is read off the CONVERTING curve (d091634: curve_turbine_type at
# curve_hub_height_m, nominal density 1.225), not the stated turbine. Where the
# curve's table ends below the maker's cut-out, the column says so (its comment).
SPEED_BAND_OF = ("the converting curve (curve.turbine_type at curve.hub_height_m, nominal "
                 "density), not the stated turbine")
_TABLE_END = "cut_out_ms is where this curve's table ends, not the maker's cut-out"
CUT_OUT_IS_TABLE_END = {"V90/2000": _TABLE_END, "V100/1800": _TABLE_END}

# What a rotor animation needs, by name: a diameter and a speed band.
ANIMATE_NEEDS = ("rotor_m", "cut_in_ms", "rated_ms", "cut_out_ms")

FLEET_DETAIL = ("implied_gen_wind_sites holds one machine per plant: one model, one count, one "
                "rotor, one hub height. The bank states no plant's fleet as parts (pantry "
                "d091647 §4 proposes a per-machine table; not built). Where rotor_basis is "
                "'uswtdb' the rotor is USWTDB's capacity-weighted mean, a blend at a plant whose "
                "turbines differ.")


def basis(column: str, token: Optional[str]) -> dict:
    """One basis as served: the token verbatim and its printed label."""
    label = BASIS_LABELS.get(column, {}).get(token) if token is not None else None
    if label is not None:
        return {"token": token, "status": "known", "label": label}
    if token is None:
        return {"token": None, "status": "absent", "label": "no basis stated"}
    return {"token": token, "status": "unknown", "label": UNKNOWN_BASIS_LABEL}


def _stated(value, column: str, token: Optional[str], unit: Optional[str]) -> dict:
    b = basis(column, token)
    out = {"value": value, "unit": unit, "basis": b, "absence": None}
    if value is None:
        out["absence"] = {"reason": "not_stated" if token == "none" else "value_null",
                          "detail": b["label"] if token == "none" else
                          f"the bank holds no value beside basis {token!r}"}
    return out


def build_equipment(s: dict, curve_cols: tuple) -> dict:
    """The plant's equipment as implied_gen_wind_sites states it (d091647)."""
    rotor = _f(s["rotor_m"])
    hub_h = _f(s["hub_height_m"])
    speeds = {c: _f(s.get(c)) for c in CURVE_DRIVERS}
    have_src = EQUIPMENT_SOURCE in s
    src = s.get(EQUIPMENT_SOURCE)
    curve = {
        "turbine_type": s["curve_turbine_type"],
        "hub_height_m": _f(s["curve_hub_height_m"]),
        "basis": basis("curve_basis", s["curve_basis"]),
        **speeds,
        "speed_band_of": SPEED_BAND_OF,
        "cut_out_note": CUT_OUT_IS_TABLE_END.get(s["curve_turbine_type"]),
        "absence": None,
    }
    missing_speeds = [c for c in CURVE_DRIVERS if speeds[c] is None]
    if missing_speeds:
        curve["absence"] = {
            "missing": missing_speeds,
            "reason": "column_absent" if any(c not in curve_cols for c in missing_speeds) else "all_null",
            "detail": f"the curve's speed band arrives with {DRIVERS_LANE}"}
    missing = [k for k in ANIMATE_NEEDS if (rotor if k == "rotor_m" else speeds[k]) is None]
    why = []
    if rotor is None:
        why.append(f"no rotor diameter ({basis('rotor_basis', s['rotor_basis'])['label']})")
    if missing_speeds:
        why.append(f"no speed band ({curve['absence']['reason']}: {', '.join(missing_speeds)})")
    return {
        "source_table": "implied_gen_wind_sites",
        "turbine_model": _stated(s["turbine_model"], "turbine_model_basis", s["turbine_model_basis"], None),
        "n_turbines": _stated(s["n_turbines"], "n_turbines_basis", s["n_turbines_basis"], None),
        "rotor_m": _stated(rotor, "rotor_basis", s["rotor_basis"], "m"),
        "hub_height_m": _stated(hub_h, "hub_height_basis", s["hub_height_basis"], "m"),
        "curve": curve,
        # verbatim, or null; nothing is derived from it (D-09-25-76's rule for source text)
        "equipment_source": src,
        "equipment_source_absence": None if have_src else {
            "reason": "column_absent", "detail": f"equipment_source arrives with {EQUIPMENT_LANE}"},
        "fleet": {
            "parts": [{"turbine_model": s["turbine_model"], "n_turbines": s["n_turbines"],
                       "rotor_m": rotor, "hub_height_m": hub_h}],
            "stated_as": "one_machine",
            "detail": FLEET_DETAIL,
        },
        "animate": {
            "can": not missing,
            "needs": list(ANIMATE_NEEDS),
            "missing": missing,
            "why_not": "; ".join(why) or None,
            "speed_band_of": SPEED_BAND_OF,
        },
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



# ── The runs (d091667): one plant's newest runs, run by run ─────────────────
#
#   GET /api/generation/asset/runs?plant_code=&tech=&n=
#
# implied_gen_site_history (pantry migration 287, D-09-25-171) keeps every
# plant x hour of the newest EIGHT runs per (tech, model), one partition per
# run (range (tech, model, init_ts)); implied_gen_site_history_runs is its
# ledger, one row per kept run, and never a ninth. A run that leaves is
# dropped whole, partition and ledger row, in one transaction.
#
# THE RUN BEFORE has one meaning (D-09-25-167 clause 1, the area vintages
# route's): `issuances` is oldest first and the run before issuances[k] is
# issuances[k-1], the next older run of the same (tech, plant, model). As on
# the area route, the server computes no change, sum or gap (clause 4): the
# page does that arithmetic on the arrays.
#
# A SWITCH OF WEATHER SOURCE IS A NEW SERIES (D-09-25-173). A wind run is HRRR
# to its seam and GFS beyond, under one model name, so each issuance's hours
# are split into one series per weather_source and are never one array across
# the seam. Runs are compared series by series: hrrr_80m with hrrr_80m. Solar
# rows carry no weather_source (null), so a solar run is one series.
#
# AN ABSENT RUN IS AN ABSENCE WITH A REASON, read off the ledger: the window is
# the newest n cycles of the writers' 6-hour ladder, ending at the newest run
# the ledger holds, and every cycle in it that is not served says why.

RUNS_KEEP = 8                  # D-09-25-171: the newest eight per (tech, model)
RUNS_N_DEFAULT = RUNS_KEEP
RUNS_N_MAX = RUNS_KEEP         # nothing older than the eighth is held, so none is asked for
RUN_STEP_H = 6                 # both writers: one run per 00/06/12/18Z cycle (ledger, 2026-10-08)
_RUN_STEP = timedelta(hours=RUN_STEP_H)
_HOUR = timedelta(hours=1)

ABSENT_REASONS = {
    "not_yet_landed": "the next cycle after the newest run the ledger holds: it has not landed",
    "not_in_ledger": "the ledger holds no run for this cycle, and a newer one has landed",
    "plant_not_in_run": "the ledger holds this run, and it has no row for this plant",
    "before_history": "older than the oldest run the ledger holds, which is not yet full: the "
                      "history began with that run (migration 287; earlier runs are not rebuilt)",
    "evicted": "older than the newest eight (D-09-25-171): the history no longer holds this "
               "cycle's run; its partition and ledger row leave together when a new run lands",
}


def parse_runs_n(raw: Optional[str]) -> int:
    """n or ValueError. Above RUNS_N_MAX is refused, never trimmed."""
    if raw is None or raw == "":
        return RUNS_N_DEFAULT
    try:
        n = int(raw)
    except ValueError:
        raise ValueError(f"n must be an integer 1-{RUNS_N_MAX}, got {raw!r}")
    if not 1 <= n <= RUNS_N_MAX:
        raise ValueError(f"n must be 1-{RUNS_N_MAX} (the history keeps {RUNS_KEEP} runs), got {n}")
    return n


# The plant's registry row, for its name and the 404: one PK lookup per tech.
RUNS_PLANT_SQL = {
    "solar": """
    SELECT plant_code, min(plant_name) AS plant_name
      FROM implied_gen_sites
     WHERE tech = %(tech)s AND plant_code = %(plant_code)s
     GROUP BY plant_code
""",
    "wind": """
    SELECT plant_code, plant_name
      FROM implied_gen_wind_sites
     WHERE plant_code = %(plant_code)s
""",
}


def runs_sql(tech: str) -> str:
    """One statement: the ledger's held runs for (tech, model), newest first,
    LIMIT keep (a backward PK scan), then, for the newest n of them, the
    plant's rows of that run (one PK range per run).

    The LATERAL is fenced with OFFSET 0 so that r.init_ts stays a parameter
    of each loop: the executor then prunes the Append to that run's one
    partition (measured: each partition loops once in three). Unfenced, the
    planner flattens it, keeps init_ts as a join filter and scans every
    held partition per run (docs/receipts/asset-runs-d091667/plans.md)."""
    inner = "".join(f", x.{c}" for c in HOUR_DRIVERS[tech])
    outer = "".join(f", h.{c}" for c in HOUR_DRIVERS[tech])
    return f"""
    WITH r AS MATERIALIZED (
        SELECT init_ts, n_rows, n_plants, method_version, landed_at,
               row_number() OVER (ORDER BY init_ts DESC) AS k
          FROM implied_gen_site_history_runs
         WHERE tech = %(tech)s AND model = %(model)s
         ORDER BY init_ts DESC
         LIMIT %(keep)s
    )
    SELECT r.k, r.init_ts AS run_init_ts, r.n_rows AS run_n_rows,
           r.n_plants AS run_n_plants, r.method_version AS run_method_version,
           r.landed_at AS run_landed_at,
           h.target_ts, h.lead_h, h.weather_source, h.implied_mw,
           h.outage_mw_subtracted, h.cap_mw_subtracted, h.method_version{outer}
      FROM r
      LEFT JOIN LATERAL (
          SELECT x.target_ts, x.lead_h, x.weather_source, x.implied_mw,
                 x.outage_mw_subtracted, x.cap_mw_subtracted, x.method_version{inner}
            FROM implied_gen_site_history x
           WHERE x.tech = %(tech)s AND x.plant_code = %(plant_code)s
             AND x.model = %(model)s AND x.init_ts = r.init_ts
             AND r.k <= %(n)s
          OFFSET 0
      ) h ON true
     ORDER BY r.init_ts, h.target_ts
"""


def runs_params(tech: str, plant_code: int, n: int) -> dict:
    return {"tech": TECHS[tech], "plant_code": plant_code, "model": MODELS[tech],
            "keep": RUNS_KEEP, "n": n}


def _series(rows: list[dict], tech: str) -> list[dict]:
    """One run's rows (ordered by target_ts) -> one series per weather_source,
    in lead order. Index i of every array is target_ts t0 + i h and lead_h
    lead0 + i; an hour the series does not hold, between its first and last,
    is null in every array (never 0, never closed up). Values as stored."""
    by_src: dict = {}
    for r in rows:
        by_src.setdefault(r["weather_source"], []).append(r)
    out = []
    for src, rs in by_src.items():
        t0, lead0 = rs[0]["target_ts"], rs[0]["lead_h"]
        width = (rs[-1]["target_ts"] - t0) // _HOUR + 1
        cols = ("implied_mw", "outage_mw_subtracted", "cap_mw_subtracted") + HOUR_DRIVERS[tech]
        arrays = {c: [None] * width for c in cols}
        for r in rs:
            i = (r["target_ts"] - t0) // _HOUR
            for c in cols:
                arrays[c][i] = _f(r.get(c))
        out.append({"weather_source": src, "t0": _iso(t0), "lead0": lead0,
                    "n_hours": len(rs), **arrays})
    return out


def _ladder(newest, n: int) -> list:
    return [newest - j * _RUN_STEP for j in range(n - 1, -1, -1)]


def build_runs(*, tech: str, plant_code: int, plant_name: Optional[str], n: int,
               rows: list[dict]) -> dict:
    """The body. `rows` are runs_sql's, ordered (run_init_ts, target_ts)."""
    held: dict = {}
    for r in rows:
        run = held.setdefault(r["run_init_ts"], {"ledger": r, "rows": []})
        if r["target_ts"] is not None:
            run["rows"].append(r)
    inits = sorted(held)
    body = {
        "label": wo.LABEL if tech == "wind" else so.LABEL,
        "tech": tech,
        "plant_code": plant_code,
        "plant_name": plant_name,
        "model": MODELS[tech],
        "figure": "registry",
        "unit": "MW",
        "step_h": 1,
        "run_step_h": RUN_STEP_H,
        "n": n,
        "drivers": list(HOUR_DRIVERS[tech]),
        "ledger": {"table": "implied_gen_site_history_runs", "held": len(inits), "keep": RUNS_KEEP,
                   "full": len(inits) >= RUNS_KEEP,
                   "oldest_init_ts": _iso(inits[0]) if inits else None,
                   "newest_init_ts": _iso(inits[-1]) if inits else None},
        "issuances": [],
        "absent": [],
        "before_oldest": None,
        "absence": None,
    }
    if tech == "wind":
        body["attribution"] = wo.ATTRIBUTION
        body["weather_height_m"] = wo.WEATHER_HEIGHT_M
    if not inits:
        body["absence"] = {"reason": "no_run_held",
                           "detail": f"implied_gen_site_history_runs holds no {MODELS[tech]} run"}
        return body
    full = len(inits) >= RUNS_KEEP
    oldest = inits[0]

    def why_older(slot) -> str:
        return "evicted" if full else "before_history"

    window = _ladder(inits[-1], n)
    lo = window[0]
    for slot in window:
        if slot in held:
            continue
        reason = why_older(slot) if slot < oldest else "not_in_ledger"
        body["absent"].append({"init_ts": _iso(slot), "reason": reason, "detail": ABSENT_REASONS[reason]})
    # a held run off the ladder is still a held run: served if inside the window
    for i in inits:
        if i < lo:
            continue
        run = held[i]
        led = run["ledger"]
        if not run["rows"]:
            body["absent"].append({"init_ts": _iso(i), "reason": "plant_not_in_run",
                                   "detail": ABSENT_REASONS["plant_not_in_run"]
                                   + f" (the run holds {led['run_n_plants']} plants)"})
            continue
        series = _series(run["rows"], tech)
        body["issuances"].append({
            "init_ts": _iso(i),
            "model": MODELS[tech],
            "method_version": led["run_method_version"],
            "landed_at": _iso(led["run_landed_at"]),
            "n_plants": led["run_n_plants"],
            "weather_sources": [s["weather_source"] for s in series],
            "series": series,
        })
    nxt = inits[-1] + _RUN_STEP
    body["absent"].append({"init_ts": _iso(nxt), "reason": "not_yet_landed",
                           "detail": ABSENT_REASONS["not_yet_landed"]})
    body["absent"].sort(key=lambda a: a["init_ts"])
    if not body["issuances"]:
        body["absence"] = {"reason": "plant_in_no_held_run",
                           "detail": f"no run the ledger holds in the newest {n} cycles has a "
                                     f"row for plant {plant_code}"}
        return body
    # The run before issuances[0], which the page cannot step to: why.
    first = min(i for i in inits if i >= lo and held[i]["rows"])
    beyond = [i for i in inits if i < lo]
    if beyond:
        body["before_oldest"] = {
            "reason": "outside_window", "init_ts": _iso(beyond[-1]),
            "detail": f"the ledger holds an older run, outside the newest {n} cycles asked for"}
    elif any(i < first for i in inits):
        body["before_oldest"] = {
            "reason": "plant_not_in_run", "init_ts": None,
            "detail": "every older run the ledger holds has no row for this plant"}
    else:
        reason = why_older(first)
        body["before_oldest"] = {"reason": reason, "init_ts": None, "detail": ABSENT_REASONS[reason]}
    return body
