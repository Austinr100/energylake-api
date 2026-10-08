"""dd_board — d091666: the degree-day board's scores, blend, band and desk.

    GET /api/weather/dd/scores   every current member score, by place, member, lead
    GET /api/weather/dd/blend    the EnergyLake blend per station, where drawable
    GET /api/weather/dd/band     the GEFS band: a stated absence (STOP-B)
    GET /api/weather/dd/desk     the Forecast desk's morning numbers, per region

READ-ONLY. The SQL and the shaping are here; main.py holds the routes, the
memos and the statement timeouts (D-09-25-75), as for vintages.py.

What the bank holds (measured on Neon 2026-10-08, docs/handback_2026_10_08_dd_
scores_blend_band_routes.md):

  * v_dd_member_scores_current: 2,880 cells, 2,038 scored, one vintage. A cell
    is (member, lead_day 0-15, place_kind, place, weighting); weighting is ''
    at a station. Pantry's CHECK nulls every metric of an unscored cell.
  * v_dd_blend_drawable: the blend rows that are drawable under el_blend_v2,
    per STATION. Pantry's comment on dd_blend_forecast: "Read blends ONLY
    through v_dd_blend_drawable. Nothing downstream reads dd_blend_forecast
    directly." So no_blend_reason, members, scored, beats_best_member and
    drawable, which the view does not carry, are not served here; the
    payload says so and names the view pantry would have to add.
  * No region blend forecast is banked anywhere. EL_BLEND has region SCORES
    (dd_member_scores), but no region blend to draw, so none is served and
    the forecast/regions board gains no blend source.
  * forecasts_gefs holds GEFS statistics (mean, p10, p50, p90) per balancing
    authority, not member values at our stations or region vectors: the band
    cannot be formed without inventing it (STOP-B).

Nothing here differences two rows. The desk's change is the delta view's, its
levels and period sums are the region board's (degree_days.region_forecast_
payload, under degree_days.PERIOD_RULE), and its score cell is the view's.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Iterable, Optional

import degree_days as _dd

# ── Reads ───────────────────────────────────────────────────────────────────

PLACE_KINDS = ("station", "region")

# One place_kind per read. place_kind is a DISTINCT ON column of the view, so
# the predicate reaches the table scan (plans S01-S04).
SCORES_SQL = """
    SELECT as_of_date, ghcn_frontier, member, lead_day, place_kind, place,
           weighting, window_start, window_end, min_target_days, n_target_days,
           n_pairs, n_provisional_days, scored, bias_tavg_f, mae_tavg_f,
           rmse_tavg_f, mae_hdd, mae_cdd, excluded, scorer_version, scorer_hash,
           blend_method_version, created_at
      FROM v_dd_member_scores_current
     WHERE place_kind = %(place_kind)s
     ORDER BY place, weighting, member, lead_day
"""

# The drawable view only (pantry 282/283). A target-date window reaches
# idx_dbf_target; one station reaches the primary key (plans B01-B05).
BLEND_SQL = """
    SELECT station_id, target_date, issue_date, lead_day, method_version,
           method_hash, tavg_f, hdd, cdd, n_members_used, blend_mae,
           best_member, best_member_mae, blend_n_target_days, computed_ts
      FROM v_dd_blend_drawable
     WHERE target_date >= %(from_date)s AND target_date < %(to_date)s
       AND (%(station)s::text IS NULL OR station_id = %(station)s)
     ORDER BY station_id, target_date, lead_day
"""


def _iso(v) -> Optional[str]:
    return None if v is None else v.isoformat()


def _sources(tables: list[str], rows: Iterable[dict]) -> dict:
    rows = list(rows)
    vals = lambda k: sorted({r[k] for r in rows if r.get(k) is not None})
    return {"tables": tables,
            "scorer_versions": vals("scorer_version"),
            "scorer_hashes": vals("scorer_hash"),
            "blend_method_versions": vals("blend_method_version")}


# ── /scores ─────────────────────────────────────────────────────────────────

# Pantry's own definition, quoted from its comment on dd_member_scores.
LEAD_RULE = ("lead_day = target_date minus the issuance's UTC date, 0..15 "
             "(dd_member_scores, migration 282). A cell with fewer than "
             "min_target_days scored target days is not yet scored: scored "
             "is false and every metric is null.")

SCORE_METRICS = ("bias_tavg_f", "mae_tavg_f", "rmse_tavg_f", "mae_hdd", "mae_cdd")

_VINTAGE_KEYS = ("as_of_date", "ghcn_frontier", "window_start", "window_end",
                 "min_target_days", "scorer_version", "scorer_hash",
                 "blend_method_version", "created_at")


def _vintage(r: dict) -> tuple:
    return tuple(r.get(k) for k in _VINTAGE_KEYS)


def scores_payload(by_kind: dict[str, list[dict]], *, place_kinds: Iterable[str]) -> dict:
    """SCORES_SQL rows per place_kind -> places, each with its cells.

    The fields a vintage shares (as_of_date, frontier, window, versions) are
    listed once in `vintages`; a cell names its vintage by index. Every value
    is the view's, unscored cells included, with nothing filled.
    """
    place_kinds = list(place_kinds)
    rows = [r for k in place_kinds for r in by_kind.get(k, [])]
    vintages = sorted({_vintage(r) for r in rows},
                      key=lambda v: tuple("" if x is None else str(x) for x in v))
    vix = {v: i for i, v in enumerate(vintages)}

    places: dict[tuple, dict] = {}
    for r in rows:
        key = (r["place_kind"], r["place"], r["weighting"])
        p = places.setdefault(key, {"place_kind": r["place_kind"], "place": r["place"],
                                    "weighting": r["weighting"], "cells": []})
        p["cells"].append({
            "member": r["member"],
            "lead_day": r["lead_day"],
            "vintage": vix[_vintage(r)],
            "n_target_days": r["n_target_days"],
            "n_pairs": r["n_pairs"],
            "n_provisional_days": r["n_provisional_days"],
            "scored": r["scored"],
            **{k: r[k] for k in SCORE_METRICS},
            "excluded": r["excluded"],
        })
    ordered = [places[k] for k in sorted(places, key=lambda k: (
        PLACE_KINDS.index(k[0]) if k[0] in PLACE_KINDS else 99, k[1], k[2]))]
    for p in ordered:
        p["cells"].sort(key=lambda c: (c["member"], c["lead_day"]))
    members = sorted({r["member"] for r in rows})
    return {
        "place_kinds": place_kinds,
        "sources": _sources(["v_dd_member_scores_current"], rows),
        "lead_rule": LEAD_RULE,
        "vintages": [{"vintage": i, **{k: (_iso(x) if isinstance(x, (date, datetime)) else x)
                                       for k, x in zip(_VINTAGE_KEYS, v)}}
                     for i, v in enumerate(vintages)],
        "source_labels": dict(_dd.SOURCE_LABELS),
        "members": [{"member": m, "label": _dd.source_label(m)} for m in members],
        "cell_count": len(rows),
        "scored_count": sum(1 for r in rows if r["scored"]),
        "places": ordered,
        "absence": None if rows else {
            "reason": "no_scores",
            "detail": "v_dd_member_scores_current holds no cell for "
                      f"place_kind {' / '.join(place_kinds)}"},
    }


# ── /blend ──────────────────────────────────────────────────────────────────

BLEND_ROW_KEYS = ("station_id", "target_date", "issue_date", "lead_day", "tavg_f",
                  "hdd", "cdd", "n_members_used", "blend_mae", "best_member",
                  "best_member_label", "best_member_mae", "blend_n_target_days",
                  "method_version", "method_hash", "computed_ts")

BLEND_NOT_SERVED = {
    "rows": "every dd_blend_forecast row that is not drawable",
    "fields": ["no_blend_reason", "members", "scored", "beats_best_member", "drawable"],
    "why": ("pantry's contract (comment on dd_blend_forecast, migrations 282/283): "
            "\"Read blends ONLY through v_dd_blend_drawable. Nothing downstream reads "
            "dd_blend_forecast directly.\" The view carries drawable rows of the "
            "served method only, without these fields."),
    "pantry_proposal": (
        "a view beside v_dd_blend_drawable, one row per dd_blend_forecast row of the "
        "served method: station_id, target_date, issue_date, lead_day, method_version, "
        "drawable, scored, beats_best_member, no_blend_reason, n_members_used, members, "
        "blend_mae, best_member, best_member_mae, and tavg_f/hdd/cdd only where "
        "drawable. Measured 2026-10-08: of 13,146 rows, 5,369 not drawable carry a "
        "NULL no_blend_reason (5,119 unscored with a value, 250 scored that do not "
        "beat the best member), so the view must also say which gate failed."),
}

REGION_BLEND = {
    "banked": False,
    "why": ("dd_blend_forecast is keyed by station_id (21 stations); no region blend "
            "forecast is banked anywhere. EL_BLEND rows at place_kind region in "
            "dd_member_scores are scores of a blend pantry forms to score, not a "
            "banked forecast, so this API serves none and computes none."),
}


def _blend_row(r: dict) -> dict:
    return {
        "station_id": r["station_id"],
        "target_date": _iso(r["target_date"]),
        "issue_date": _iso(r["issue_date"]),
        "lead_day": r["lead_day"],
        "tavg_f": r["tavg_f"],
        "hdd": r["hdd"],
        "cdd": r["cdd"],
        "n_members_used": r["n_members_used"],
        "blend_mae": r["blend_mae"],
        "best_member": r["best_member"],
        "best_member_label": _dd.source_label(r["best_member"]),
        "best_member_mae": r["best_member_mae"],
        "blend_n_target_days": r["blend_n_target_days"],
        "method_version": r["method_version"],
        "method_hash": r["method_hash"],
        "computed_ts": _iso(r["computed_ts"]),
    }


def blend_payload(rows: Iterable[dict], *, from_date: date, days: int,
                  station: Optional[str]) -> dict:
    shaped = [_blend_row(r) for r in rows]
    return {
        "from": _iso(from_date),
        "days": days,
        "station": station,
        "served_from": "v_dd_blend_drawable",
        "sources": {"tables": ["v_dd_blend_drawable"],
                    "method_versions": sorted({r["method_version"] for r in shaped}),
                    "method_hashes": sorted({r["method_hash"] for r in shaped})},
        "stations_present": sorted({r["station_id"] for r in shaped}),
        "newest_issue_date": max((r["issue_date"] for r in shaped), default=None),
        "computed_ts": sorted({r["computed_ts"] for r in shaped}),
        "row_count": len(shaped),
        "rows": shaped,
        "not_served": BLEND_NOT_SERVED,
        "region_blend": REGION_BLEND,
        "absence": None if shaped else {
            "reason": "no_drawable_blend",
            "detail": "v_dd_blend_drawable holds no row for this window"
                      + (f" at {station}" if station else "")},
    }


# ── /band ───────────────────────────────────────────────────────────────────

def band_payload() -> dict:
    """STOP-B. Measured on Neon 2026-10-08; nothing is read at request time."""
    return {
        "band": None,
        "percentile_rule": None,
        "absence": {
            "reason": "not_banked",
            "detail": ("no GEFS member value is banked at our stations or region "
                       "vectors, so a P5-P95 degree-day band cannot be formed from "
                       "members (D-09-25-163: a band is members or history, never a "
                       "guess). forecasts_gefs holds statistics already reduced over "
                       "31 members, per balancing authority, not the members."),
        },
        "banked": {
            "table": "forecasts_gefs",
            "datasets": ["gefs_dd_region_daily", "gefs_t2m_region_6h"],
            "places": "17 balancing authorities (AZPS, BANC, BPAT, ...), not the 21 "
                      "stations or the 5 region vectors",
            "statistics": ["mean", "p10", "p50", "p90"],
            "degree_days": "cdd and gwdd per BA day; no hdd; day boundary 06Z from "
                           "four 6-hourly samples, not 24 local-standard hours",
            "cycles": "00, 06, 12, 18Z; 49 cycles 2026-09-26 00Z to 2026-10-08 12Z",
        },
        "requires": [
            "per-member daily tavg_f, hdd and cdd for each of the 31 GEFS members, "
            "per (cycle, member, station_id, target_date), on the 24 local-standard-"
            "hour day the scorer pairs against (D-09-25-165), with hours_covered and "
            "basis_complete",
            "the same per region vector (degree_day_region_weights), or a view that "
            "weights the station rows",
            "a score for the band (coverage of P5-P95 against truth by lead) before "
            "it is shown (D-09-25-163: nothing is shown before it is scored)",
        ],
    }


# ── /desk ───────────────────────────────────────────────────────────────────

# The region board's sources (measured in v_degree_days_region_forecast,
# 2026-10-08). EL_BLEND is not one: no region blend is banked.
DESK_SOURCES = ("AIFS", "GFS", "IFS", "NBM", "gridpoints_raw")


def lead_day(target_date: str, issued_ts: str) -> int:
    """The scorer's key for a cell: target date minus the UTC date of the
    issuance (LEAD_RULE). It picks which score cell applies; it is not a
    number the desk prints as a forecast."""
    ts = datetime.fromisoformat(issued_ts).astimezone(timezone.utc)
    return (date.fromisoformat(target_date) - ts.date()).days


def _score_cell(cell: Optional[dict]) -> dict:
    if cell is None:
        return {"state": "no_score_cell", **{k: None for k in SCORE_METRICS},
                "n_target_days": None, "n_pairs": None, "n_provisional_days": None,
                "vintage": None}
    return {"state": "scored" if cell["scored"] else "not_yet_scored",
            "n_target_days": cell["n_target_days"], "n_pairs": cell["n_pairs"],
            "n_provisional_days": cell["n_provisional_days"],
            **{k: cell[k] for k in SCORE_METRICS}, "vintage": cell["vintage"]}


def desk_payload(board: dict, scores: dict, *, source: str) -> dict:
    """The region board (degree_days.region_forecast_payload) and the region
    scores (scores_payload) -> one source's morning numbers per region."""
    cells = {(p["place"], p["weighting"], c["member"], c["lead_day"]): c
             for p in scores["places"] if p["place_kind"] == "region"
             for c in p["cells"]}
    out = []
    for reg in board["regions"]:
        src = next((s for s in reg["sources"] if s["source_product"] == source), None)
        days = []
        for d in reg["days"]:
            c = d["by_source"].get(source)
            if c is None:
                days.append({"target_date": d["target_date"], "issued_ts": None,
                             "lead_day": None, "hdd": None, "cdd": None,
                             "basis_complete": None, "normal": d["normal"],
                             "change": None, "change_absence": None, "score": None,
                             "absence": {"reason": "no_issuance",
                                         "detail": f"no {source} issuance holds this "
                                                   "target date in the window"}})
                continue
            lead = lead_day(d["target_date"], c["issued_ts"])
            days.append({
                "target_date": d["target_date"],
                "issued_ts": c["issued_ts"],
                "lead_day": lead,
                "hdd": c["hdd"],
                "cdd": c["cdd"],
                "basis_complete": c["basis_complete"],
                "normal": d["normal"],
                "change": c["change"],
                "change_absence": c["change_absence"],
                "score": _score_cell(cells.get((reg["region"], reg["weighting"],
                                                source, lead))),
                "absence": None,
            })
        out.append({
            "region": reg["region"],
            "weighting": reg["weighting"],
            "newest_issued_ts": src["newest_issued_ts"] if src else None,
            "prior_issued_ts": src["prior_issued_ts"] if src else None,
            "period_anchor_issued_ts": reg["period_anchor_issued_ts"],
            "days": days,
            "periods": [{"window": p["window"], "from": p["from"], "to": p["to"],
                         **p["by_source"][source]}
                        for p in reg["periods"] if source in p["by_source"]],
        })
    return {
        "source_product": source,
        "label": _dd.source_label(source),
        "weighting": board["weighting"],
        "from": board["from"],
        "days": board["days"],
        "tz": board["tz"],
        "period_rule": board["period_rule"],
        "lead_rule": LEAD_RULE,
        "sources": {**scores["sources"],
                    "tables": ["v_degree_days_region_forecast", "v_degree_days_model_delta",
                               "v_degree_days_model_spread", "v_dd_member_scores_current"]},
        "vintages": scores["vintages"],
        "region_count": len(out),
        "regions": out,
    }
