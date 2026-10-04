"""Solar generation outlook — d091568 (API half), D-09-25-114.

    GET /api/generation/solar/outlook   one area's issuance, hour by hour
    GET /api/generation/solar/sites     the newest cycle's plants, for the map

READ-ONLY. The writer (pantry d091567) owns every table read here; migration
264 made them:

    implied_gen_area_hourly   every issuance, (tech, area_kind, area, model,
                              init_ts, target_ts), with lead_band and
                              source_posted_ts (writer handback §6.5)
    implied_gen_scores        (tech, area, lead_band, who, window_end,
                              method_version); `scored` = n_days >= 14
    implied_gen_calibration   one row per fit, by calibration_id
    implied_gen_sites         one row per (plant_code, generator_id)
    implied_gen_site_latest   the newest cycle, per plant

This module holds the SQL and the pure shaping; main.py holds the routes, the
memo and the statement timeout (D-09-25-75).

Every read names one (tech, area_kind, area, model) — this lane's (dataset,
series) — or one (dataset, series) of timeseries_values, and every "newest"
is a LIMIT 1 down a primary key, never a max() over a table or a DISTINCT ON.

THE RANKING MISTAKE (the degree-day forecast's, pantry d091557): the previous
issuance is found with `model` in the predicate. Ranked without it, the row
before a GFS run could be another model's, and `previous_mw` would be one
model minus another labelled as a run-over-run change.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

TECH = "solar_pv"
PT = ZoneInfo("America/Los_Angeles")

# D-09-25-109, spec §3.3. Verbatim: the page header, the chart and this API
# carry the same words, and tests/test_solar_outlook.py G5 holds them to the spec.
LABEL = ("Weather-implied generation. What the weather says these plants should "
         "produce, from open-source models (pvlib) and public plant data. Not a "
         "forecast of metered output and not any plant's schedule.")

NOT_YET_SCORED = "not yet scored"

# The models the writer banks. Only GFS today (spec §4.4: IFS once hourly
# irradiance for it is sourced). A name outside this set is a 400, never a read.
MODELS = ("gfs",)
DEFAULT_MODEL = "gfs"

AREA_KINDS = ("hub", "hub_sum", "ba", "state")
HUBS = ("NP15", "ZP26", "SP15")
HUBSUM = "HUBSUM"                       # the writer's name for the hub_sum area
CAISO_DAM_KINDS = ("hub", "hub_sum")    # D-09-25-114 clause 6
_AREA_RE = re.compile(r"^[A-Z0-9]{2,10}$")

LEAD_BANDS = ("h01_06", "h07_24", "h25_48", "h49_120", "h121_240")
DAM = "dam_comparable"
SCORE_BANDS = LEAD_BANDS + (DAM,)
OURS = ("registry", "calibrated")
MIN_SCORED_DAYS = 14                    # D-09-25-109 clause 5, the writer's MIN_DAYS


def score_pairs(area_kind: str) -> list[tuple[str, str]]:
    """Every (lead_band, who) the score card shows for this kind of area.
    CAISO's day-ahead appears beside `dam_comparable` only, and for hub areas
    only (clause 6). The BA-wide total is never set beside a hub forecast."""
    pairs = [(band, who) for band in SCORE_BANDS for who in OURS]
    if area_kind in CAISO_DAM_KINDS:
        pairs.append((DAM, "caiso_dam"))
    return pairs


# ── Params ──────────────────────────────────────────────────────────────────

def parse_area(area_kind: Optional[str], area: Optional[str]) -> tuple[str, str]:
    """(area_kind, area) or ValueError naming what is allowed."""
    if area_kind not in AREA_KINDS:
        raise ValueError(f"area_kind must be one of {list(AREA_KINDS)}, got {area_kind!r}")
    if area_kind == "hub_sum":
        if area not in (None, "", HUBSUM):
            raise ValueError(f"area for hub_sum is {HUBSUM!r} (or omitted), got {area!r}")
        return area_kind, HUBSUM
    if not area:
        raise ValueError(f"area is required for area_kind={area_kind!r}")
    if area_kind == "hub" and area not in HUBS:
        raise ValueError(f"area for hub must be one of {list(HUBS)}, got {area!r}")
    if not _AREA_RE.match(area):
        raise ValueError(f"area must be an upper-case code (a BA or a state), got {area!r}")
    return area_kind, area


def parse_model(model: Optional[str]) -> str:
    model = model or DEFAULT_MODEL
    if model not in MODELS:
        raise ValueError(f"model must be one of {list(MODELS)}, got {model!r}")
    return model


def parse_instant(raw: Optional[str], field: str) -> Optional[datetime]:
    """ISO instant -> tz-aware UTC. A naive value is read as UTC."""
    if raw is None or raw == "":
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"{field} must be an ISO instant (e.g. 2026-10-01T12:00:00Z), got {raw!r}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_day(raw: Optional[str]) -> Optional[date]:
    if raw is None or raw == "":
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise ValueError(f"day must be an ISO date (YYYY-MM-DD, Pacific), got {raw!r}")


def pacific_day_bounds(d: date) -> tuple[datetime, datetime]:
    """[start, end) of Pacific day d in UTC: 23, 24 or 25 hours."""
    lo = datetime.combine(d, time(0), PT).astimezone(timezone.utc)
    hi = datetime.combine(d + timedelta(days=1), time(0), PT).astimezone(timezone.utc)
    return lo, hi


# ── SQL: the outlook ────────────────────────────────────────────────────────
#
# Five statements, run in one transaction under one statement timeout:
#   ISSUANCE   the issuance and the one before it, same model (2 PK lookups)
#   HOURS      that issuance's rows, each beside the previous issuance's row
#              for the same target hour (one PK range + one PK probe per hour)
#   SCORES     newest score row per (lead_band, who), one LIMIT 1 lateral each
#   CALIBRATION the lines the rows carry, by calibration_id (skipped when none)
#   ACTUALS    one lateral per (dataset, series) over the issuance's hours
#              (skipped for areas with no actual)
#   FLEET      MW by mount_basis and dc_basis for the area's registry units

_AREA_KEY = """{a}.tech = %(tech)s AND {a}.area_kind = %(area_kind)s
           AND {a}.area = %(area)s AND {a}.model = %(model)s"""

# The previous issuance: same (tech, area_kind, area, MODEL), the newest
# init_ts before this one. A backward scan of the primary key, LIMIT 1.
_PREV_SUBQUERY = f"""(SELECT p.init_ts
             FROM implied_gen_area_hourly p
            WHERE {_AREA_KEY.format(a="p")}
              AND p.init_ts < cur.init_ts
            ORDER BY p.init_ts DESC
            LIMIT 1)"""

ISSUANCE_NEWEST_SQL = f"""
    SELECT cur.init_ts,
           {_PREV_SUBQUERY} AS prev_init_ts
      FROM (SELECT i.init_ts
              FROM implied_gen_area_hourly i
             WHERE {_AREA_KEY.format(a="i")}
             ORDER BY i.init_ts DESC
             LIMIT 1) AS cur
"""

ISSUANCE_AT_SQL = f"""
    SELECT cur.init_ts,
           {_PREV_SUBQUERY} AS prev_init_ts
      FROM (SELECT i.init_ts
              FROM implied_gen_area_hourly i
             WHERE {_AREA_KEY.format(a="i")}
               AND i.init_ts = %(init)s
             LIMIT 1) AS cur
"""

# p is pinned to the previous issuance found above; with no previous issuance
# (%(prev_init)s NULL) the join matches nothing and every previous_* is null.
HOURS_SQL = f"""
    SELECT c.target_ts, c.lead_h, c.lead_band, c.weather_step_h,
           c.registry_mw, c.calibrated_mw, c.calibration_id,
           c.outage_mw_subtracted, c.ac_mw_total, c.n_sites,
           c.source_posted_ts, c.method_version,
           p.registry_mw    AS prev_registry_mw,
           p.calibrated_mw  AS prev_calibrated_mw,
           p.method_version AS prev_method_version
      FROM implied_gen_area_hourly c
      LEFT JOIN implied_gen_area_hourly p
        ON p.tech = c.tech AND p.area_kind = c.area_kind
       AND p.area = c.area AND p.model = c.model
       AND p.init_ts = %(prev_init)s
       AND p.target_ts = c.target_ts
     WHERE {_AREA_KEY.format(a="c")}
       AND c.init_ts = %(init)s
     ORDER BY c.target_ts
"""


def _scores_sql(area_kind: str, extra_cols: tuple = ()) -> str:
    """`extra_cols`: further implied_gen_scores columns a tech's rows carry
    (wind: actual_source and the MW by class). Solar passes none."""
    values = ",\n            ".join(f"('{b}', '{w}')" for b, w in score_pairs(area_kind))
    outer = "".join(f", s.{c}" for c in extra_cols)
    return f"""
    SELECT v.lead_band, v.who, s.area_kind, s.window_start, s.window_end,
           s.n_hours, s.n_days, s.scored, s.bias_mw, s.mae_mw,
           s.mae_pct_installed, s.rmse_mw, s.r, s.scored_at{outer}
      FROM (VALUES
            {values}
           ) AS v(lead_band, who)
     CROSS JOIN LATERAL (
        SELECT s.area_kind, s.window_start, s.window_end, s.n_hours, s.n_days,
               s.scored, s.bias_mw, s.mae_mw, s.mae_pct_installed, s.rmse_mw,
               s.r, s.scored_at{outer}
          FROM implied_gen_scores s
         WHERE s.tech = %(tech)s AND s.area = %(area)s
           AND s.lead_band = v.lead_band AND s.who = v.who
           AND s.area_kind = %(area_kind)s
           AND s.method_version = %(method_version)s
         ORDER BY s.window_end DESC
         LIMIT 1
     ) AS s
"""


# Built once, from constants only: the VALUES list is never request text.
SCORES_SQL = {kind: _scores_sql(kind) for kind in AREA_KINDS}

CALIBRATION_SQL = """
    SELECT calibration_id, area, lead_band, intercept_mw, slope,
           fit_start, fit_end, n_hours, n_days, fitted_at, method_version
      FROM implied_gen_calibration
     WHERE calibration_id = ANY(%(ids)s)
"""


def actual_pairs(area_kind: str, area: str) -> list[tuple[str, str, str]]:
    """(role, dataset, series) for this area: the actual the writer scores it
    against, and CAISO's day-ahead where clause 6 allows it. [] = no actual."""
    if area_kind == "hub":
        return [("actual", "caiso_renewables_hourly", f"{area}:Solar"),
                ("caiso_dam", "caiso_renewables_fcst_dam", f"{area}:Solar")]
    if area_kind == "hub_sum":
        return ([("actual", "caiso_renewables_hourly", f"{h}:Solar") for h in HUBS]
                + [("caiso_dam", "caiso_renewables_fcst_dam", f"{h}:Solar") for h in HUBS])
    if area_kind == "ba" and area == "CISO":
        # The BA-wide fuel-mix total: scored against, never set beside CAISO's
        # hub forecast (the recon's scope finding, clause 6).
        return [("actual", "caiso_fuel_mix_hourly", "solar")]
    return []


def _actuals_sql(pairs: list[tuple[str, str, str]]) -> str:
    values = ",\n            ".join(f"('{ds}', '{se}')" for _r, ds, se in pairs)
    return f"""
    SELECT d.dataset, d.series, l.ts, l.value
      FROM (VALUES
            {values}
           ) AS d(dataset, series)
     CROSS JOIN LATERAL (
        SELECT t.ts, t.value::float8 AS value
          FROM timeseries_values t
         WHERE t.dataset = d.dataset AND t.series = d.series
           AND t.ts >= %(lo)s AND t.ts <= %(hi)s
         ORDER BY t.ts
     ) AS l
"""


# Every (kind, area) that has an actual, prebuilt; any other area reads nothing.
ACTUALS_SQL = {
    key: _actuals_sql(actual_pairs(*key))
    for key in ([("hub", h) for h in HUBS] + [("hub_sum", HUBSUM), ("ba", "CISO")])
}

# implied_gen_sites is the registry (1,914 units on 2026-10-03), refreshed in
# full; it does not grow with time, so a filter over it is bounded. Its key
# leads with plant_code, so no predicate here is an index lookup — measured in
# the handback, and it is behind the memo.
_FLEET_FILTER = {
    "hub": "hub = %(area)s",
    "hub_sum": "hub IS NOT NULL",
    "ba": "ba_code = %(area)s",
    "state": "state = %(area)s",
}
FLEET_SQL = {
    kind: f"""
    SELECT mount_basis, dc_basis, count(*) AS n_units, sum(ac_mw)::float8 AS ac_mw
      FROM implied_gen_sites
     WHERE tech = %(tech)s AND {pred}
     GROUP BY mount_basis, dc_basis
     ORDER BY mount_basis, dc_basis
"""
    for kind, pred in _FLEET_FILTER.items()
}


# ── SQL: the sites ──────────────────────────────────────────────────────────

# implied_gen_site_latest holds the newest cycle only (the writer replaces it
# in full), so the window on target_ts is the whole bound. See the handback for
# the plan at the full 1,645 plants x 240 hours.
SITE_LATEST_SQL = """
    SELECT l.plant_code, l.init_ts, l.target_ts, l.lead_h, l.implied_mw,
           l.outage_mw_subtracted, l.method_version
      FROM implied_gen_site_latest l
     WHERE l.tech = %(tech)s AND l.model = %(model)s
       AND l.target_ts >= %(lo)s AND l.target_ts < %(hi)s
"""

SITE_UNITS_SQL = """
    SELECT plant_code, generator_id, plant_name, latitude, longitude,
           ac_mw::float8 AS ac_mw, dc_mw::float8 AS dc_mw, dc_basis,
           mount, mount_basis, tilt_deg::float8 AS tilt_deg, tilt_basis,
           azimuth_deg::float8 AS azimuth_deg, azimuth_basis, bifacial,
           hub, ba_code, state
      FROM implied_gen_sites
     WHERE tech = %(tech)s
     ORDER BY plant_code, generator_id
"""


# ── Shaping ─────────────────────────────────────────────────────────────────

def _iso(v) -> Optional[str]:
    return v.isoformat() if v is not None else None


def _f(v) -> Optional[float]:
    return float(v) if v is not None else None


def _score_obj(r: dict, extra: tuple = ()) -> dict:
    return {
        "window_start": _iso(r.get("window_start")),
        "window_end": _iso(r["window_end"]),
        "n_hours": r["n_hours"],
        "n_days": r["n_days"],
        "bias_mw": _f(r.get("bias_mw")),
        "mae_mw": _f(r.get("mae_mw")),
        "mae_pct_installed": _f(r.get("mae_pct_installed")),
        "rmse_mw": _f(r.get("rmse_mw")),
        "r": _f(r.get("r")),
        "scored_at": _iso(r.get("scored_at")),
        **{c: r.get(c) for c in extra},
    }


def build_scores(area_kind: str, rows: list[dict], extra: tuple = ()) -> tuple[dict, dict]:
    """(scores, score_progress).

    scores[band][who] is the newest score row OF THAT BAND AND THAT WHO with
    `scored` true, or "not yet scored". A row is only ever looked up by its own
    (band, who): there is no fallback to a neighbouring band, an older window,
    or the recon's 1-6 h figure (D-09-25-114 clause 2).

    score_progress[band][who] carries the counts of an unscored row, so the
    page can say how far off 14 days it is; null where no row exists at all.
    """
    by_key = {(r["lead_band"], r["who"]): r for r in rows}
    scores: dict = {}
    progress: dict = {}
    for band, who in score_pairs(area_kind):
        r = by_key.get((band, who))
        if r is not None and r.get("area_kind") not in (None, area_kind):
            r = None                    # another kind's row is not this area's
        scored = r is not None and bool(r["scored"]) and r["n_days"] >= MIN_SCORED_DAYS
        scores.setdefault(band, {})[who] = _score_obj(r, extra) if scored else NOT_YET_SCORED
        if not scored:
            progress.setdefault(band, {})[who] = (
                None if r is None else {"n_days": r["n_days"], "n_hours": r["n_hours"],
                                        "window_end": _iso(r["window_end"]),
                                        "min_days": MIN_SCORED_DAYS})
    return scores, progress


def calibration_ids(hours: list[dict]) -> list[int]:
    return sorted({int(h["calibration_id"]) for h in hours if h.get("calibration_id") is not None})


def build_calibration(hours: list[dict], lines: list[dict]) -> dict:
    """Per lead band, the line these rows were scaled by, or null.

    Only a line a row actually carries is reported: a line that exists in
    implied_gen_calibration but was not applied to this issuance is not this
    issuance's scaling, and is not offered as if it were. `dam_comparable` is
    a selection of rows, not a lead range, and has no line of its own (writer
    handback §6.4)."""
    by_id = {int(l["calibration_id"]): l for l in lines}
    out: dict = {band: None for band in LEAD_BANDS}
    for band in LEAD_BANDS:
        ids = {int(h["calibration_id"]) for h in hours
               if h["lead_band"] == band and h.get("calibration_id") is not None}
        used = [by_id[i] for i in ids if i in by_id and by_id[i]["lead_band"] == band]
        if not used:
            continue
        l = max(used, key=lambda x: (x["fit_end"], x["calibration_id"]))
        out[band] = {
            "calibration_id": int(l["calibration_id"]),
            "slope": _f(l["slope"]),
            "intercept_mw": _f(l["intercept_mw"]),
            "fit_start": _iso(l["fit_start"]),
            "fit_end": _iso(l["fit_end"]),
            "n_hours": l["n_hours"],
            "n_days": l["n_days"],
            "fitted_at": _iso(l.get("fitted_at")),
            "lines_on_rows": len(used),
        }
    return out


def build_actuals(area_kind: str, area: str, rows: list[dict],
                  pairs: Optional[list] = None) -> tuple[list, Optional[list]]:
    """(actuals, caiso_dam). hub_sum is the three hubs' sum on hours where all
    three exist (the writer's rule, store.actual_series). caiso_dam is None —
    the key is left out of the body — for any area clause 6 keeps it from.
    `pairs` defaults to solar's; wind passes its own (wind_outlook)."""
    pairs = actual_pairs(area_kind, area) if pairs is None else pairs
    role_of = {(ds, se): role for role, ds, se in pairs}
    per: dict = {"actual": {}, "caiso_dam": {}}
    for r in rows:
        role = role_of.get((r["dataset"], r["series"]))
        if role is None or r["value"] is None:
            continue
        per[role].setdefault(r["ts"], {})[r["series"]] = float(r["value"])

    def _series(role):
        need = {se for ro, _ds, se in pairs if ro == role}
        out = []
        for ts in sorted(per[role]):
            got = per[role][ts]
            if need and set(got) == need:
                out.append({"target_ts": ts.isoformat(), "mw": round(sum(got.values()), 3)})
        return out

    dam = _series("caiso_dam") if area_kind in CAISO_DAM_KINDS else None
    return _series("actual"), dam


def build_fleet(hours: list[dict], fleet_rows: list[dict]) -> dict:
    """The issuance's own totals, and the registry's MW by basis for the area.
    `n_sites` is the writer's count of generating UNITS (rows of
    implied_gen_sites), not plants."""
    first = hours[0] if hours else {}
    def _by(col):
        acc: dict = {}
        for r in fleet_rows:
            a = acc.setdefault(r[col], {"basis": r[col], "ac_mw": 0.0, "n_units": 0})
            a["ac_mw"] += float(r["ac_mw"] or 0.0)
            a["n_units"] += int(r["n_units"])
        return [{**v, "ac_mw": round(v["ac_mw"], 3)} for _k, v in sorted(acc.items())]
    return {
        "ac_mw_total": _f(first.get("ac_mw_total")),
        "n_sites": first.get("n_sites"),
        "by_mount_basis": _by("mount_basis"),
        "by_dc_basis": _by("dc_basis"),
        "registry_units": sum(int(r["n_units"]) for r in fleet_rows),
        "registry_ac_mw": round(sum(float(r["ac_mw"] or 0.0) for r in fleet_rows), 3),
    }


def _previous_mw(h: dict) -> Optional[float]:
    """Like for like with the figure this hour shows: calibrated against
    calibrated where this hour is calibrated, registry against registry where
    it is not. Never a calibrated number set against an unscaled one."""
    if h["calibrated_mw"] is not None:
        return _f(h["prev_calibrated_mw"])
    return _f(h["prev_registry_mw"])


def build_outlook(*, area_kind: str, area: str, model: str,
                  issuance: Optional[dict], hours: list[dict], score_rows: list[dict],
                  lines: list[dict], actual_rows: list[dict],
                  fleet_rows: list[dict]) -> dict:
    scores, progress = build_scores(area_kind, score_rows)
    actuals, dam = build_actuals(area_kind, area, actual_rows)
    body: dict = {
        "label": LABEL,
        "tech": TECH,
        "area_kind": area_kind,
        "area": area,
        "unit": "MW",
        "issuance": None,
        "hours": [],
        "unscaled": True,
        "calibration": build_calibration(hours, lines),
        "scores": scores,
        "score_progress": progress,
        "actuals": actuals,
    }
    if dam is not None:
        body["caiso_dam"] = dam
    body["fleet"] = build_fleet(hours, fleet_rows)
    body["absence"] = None
    if issuance is None or not hours:
        body["absence"] = {"reason": "no_issuance",
                           "detail": f"no {model} issuance is banked for "
                                     f"{area_kind}={area}"}
        return body
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
        "weather_step_h": h["weather_step_h"],
        "registry_mw": _f(h["registry_mw"]),
        "calibrated_mw": _f(h["calibrated_mw"]),
        "calibration_id": h["calibration_id"],
        "outage_mw_subtracted": _f(h["outage_mw_subtracted"]),
        "previous_mw": _previous_mw(h),
        "previous_registry_mw": _f(h["prev_registry_mw"]),
        "previous_calibrated_mw": _f(h["prev_calibrated_mw"]),
    } for h in hours]
    body["unscaled"] = all(h["calibrated_mw"] is None for h in hours)
    return body


def _unit(u: dict) -> dict:
    return {
        "generator_id": u["generator_id"],
        "ac_mw": _f(u["ac_mw"]),
        "dc_mw": _f(u["dc_mw"]),
        "dc_basis": u["dc_basis"],
        "mount": u["mount"],
        "mount_basis": u["mount_basis"],
        "tilt_deg": _f(u["tilt_deg"]),
        "tilt_basis": u["tilt_basis"],
        "azimuth_deg": _f(u["azimuth_deg"]),
        "azimuth_basis": u["azimuth_basis"],
        "bifacial": u["bifacial"],
    }


def _in_area(u: dict, area_kind: Optional[str], area: Optional[str]) -> bool:
    if area_kind is None:
        return True
    if area_kind == "hub":
        return u["hub"] == area
    if area_kind == "hub_sum":
        return u["hub"] is not None
    if area_kind == "ba":
        return u["ba_code"] == area
    return u["state"] == area


def build_sites(*, model: str, target: Optional[datetime], day: Optional[date],
                lo: datetime, hi: datetime, area_kind: Optional[str], area: Optional[str],
                latest_rows: list[dict], unit_rows: list[dict]) -> dict:
    """One row per plant: identity from implied_gen_sites (every unit, with its
    stated equipment and which values are class defaults), the implied MW
    (target hour) or MWh (Pacific day) from implied_gen_site_latest."""
    units: dict = {}
    for u in unit_rows:
        if _in_area(u, area_kind, area):
            units.setdefault(u["plant_code"], []).append(u)
    inits = {r["init_ts"] for r in latest_rows}
    init = max(inits) if inits else None
    per: dict = {}
    for r in latest_rows:
        if r["init_ts"] != init or r["plant_code"] not in units:
            continue
        p = per.setdefault(r["plant_code"], {"mw": 0.0, "out": 0.0, "n": 0, "mv": r["method_version"]})
        p["mw"] += float(r["implied_mw"])
        p["out"] += float(r["outage_mw_subtracted"])
        p["n"] += 1
    hours_expected = int((hi - lo).total_seconds() // 3600)
    plants = []
    for code in sorted(per):
        us = units[code]
        p = per[code]
        row = {
            "plant_code": code,
            "plant_name": us[0]["plant_name"],
            "latitude": _f(us[0]["latitude"]),
            "longitude": _f(us[0]["longitude"]),
            "ac_mw": round(sum(float(u["ac_mw"]) for u in us), 3),
            "hub": us[0]["hub"],
            "ba_code": us[0]["ba_code"],
            "state": us[0]["state"],
        }
        if target is not None:
            row["implied_mw"] = round(p["mw"], 3)
            row["outage_mw_subtracted"] = round(p["out"], 3)
        else:
            row["implied_mwh"] = round(p["mw"], 3)
            row["outage_mwh_subtracted"] = round(p["out"], 3)
            row["hours_covered"] = p["n"]
        row["units"] = [_unit(u) for u in us]
        plants.append(row)
    body = {
        "label": LABEL,
        "tech": TECH,
        "model": model,
        "init_ts": _iso(init),
        "method_version": next(iter(per.values()))["mv"] if per else None,
        "target_ts": _iso(target),
        "day": _iso(day),
        "hours_expected": hours_expected,
        "area_kind": area_kind,
        "area": area,
        "plant_count": len(plants),
        "unit_count": sum(len(p["units"]) for p in plants),
        "plants": plants,
        "absence": None,
    }
    if not plants:
        reason = ("no_site_rows" if not latest_rows
                  else "no_registry_units" if not unit_rows else "no_plants_in_area")
        body["absence"] = {"reason": reason,
                           "detail": {"no_site_rows": "implied_gen_site_latest holds no row for "
                                                      "this window (the backfill writes no site rows)",
                                      "no_registry_units": "implied_gen_sites is empty",
                                      "no_plants_in_area": "no plant of the newest cycle is in this area",
                                      }[reason]}
    return body
