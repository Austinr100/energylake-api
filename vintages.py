"""Vintages — d091644, D-09-25-167: every held run of one curve, in one read.

    GET /api/generation/solar/vintages            one area's N newest issuances
    GET /api/generation/wind/vintages             the same, wind
    GET /api/weather/dd/forecast/regions/vintages one region, every source,
                                                  every held issuance

The vintage player (dashboard, a later lane) steps through runs with no round
trip per step, so it needs every run it can step to before the first step.
These routes ship the curves, as arrays, and nothing else. D-09-25-167
clause 4: the page does the arithmetic (the run before, the shared hours, the
change, the gap), so no change, sum or spacing is computed here.

READ-ONLY. The SQL and the shaping are here; main.py holds the routes, the
memos and the statement timeouts (D-09-25-75).

Implied generation (recon d091639 §2.1 a, §3.2): one read names one (tech,
area_kind, area, model). A recursive walk down the primary key finds the N
newest inits, then one PK range per init; no max(), no DISTINCT ON. `cal` is
gated by solar_outlook.gate, the outlook routes' own gate (D-09-25-136), over
the lines the outlook reads with its own statement (solar_outlook.
CALIBRATION_SQL): there is no second gate.

Degree days: one read per (region, weighting, source_product) of
v_degree_days_region_forecast, run concurrently. An issuance is the view's
issued_ts; for the NWS leg that is a fold (max member issued_ts over one
fold_ts), as the view defines it.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable, Optional

import degree_days as _dd
import solar_outlook as so

# ── Rounding ────────────────────────────────────────────────────────────────

_ONE = Decimal(1)
_CENT = Decimal("0.01")


def half_away(v, exp: Decimal) -> Optional[Decimal]:
    """`v` rounded to `exp`, half away from zero. A float is taken at its
    exact binary value, a Decimal (numeric) as it is. None stays None."""
    if v is None:
        return None
    d = v if isinstance(v, Decimal) else Decimal(v)
    return d.quantize(exp, rounding=ROUND_HALF_UP)   # ROUND_HALF_UP is away from zero


def whole_mw(v) -> Optional[int]:
    q = half_away(v, _ONE)
    return None if q is None else int(q)


def dd01(v) -> Optional[float]:
    q = half_away(v, _CENT)
    return None if q is None else float(q)


# ── Implied generation: params ──────────────────────────────────────────────

N_DEFAULT = 28          # recon §3.3: ~7 days of 6-hourly runs
N_MAX = 120
STEP_H = 1
_HOUR = timedelta(hours=1)


def parse_n(raw: Optional[str]) -> int:
    """n or ValueError. Above N_MAX is refused, never trimmed."""
    if raw is None or raw == "":
        return N_DEFAULT
    try:
        n = int(raw)
    except ValueError:
        raise ValueError(f"n must be an integer 1-{N_MAX}, got {raw!r}")
    if not 1 <= n <= N_MAX:
        raise ValueError(f"n must be 1-{N_MAX} (cap {N_MAX}), got {n}. "
                         "This endpoint never silently truncates.")
    return n


# ── Implied generation: the read ────────────────────────────────────────────
#
# Recon §2.1 (a). The first branch is the newest init (a backward PK scan,
# LIMIT 1); each recursive step is the next older init of the SAME (tech,
# area_kind, area, model), so another model's run is never a vintage of this
# one (the d091557 ranking mistake). The walk stops at n, or one step past the
# oldest held init (that step's NULL init joins nothing). Then one PK range per
# init. Oldest first: the newest is last, the right edge.

VINTAGES_SQL = f"""
    WITH RECURSIVE inits AS (
        (SELECT i.init_ts, 1 AS k
           FROM implied_gen_area_hourly i
          WHERE {so._AREA_KEY.format(a="i")}
          ORDER BY i.init_ts DESC
          LIMIT 1)
        UNION ALL
        SELECT (SELECT p.init_ts
                  FROM implied_gen_area_hourly p
                 WHERE {so._AREA_KEY.format(a="p")}
                   AND p.init_ts < w.init_ts
                 ORDER BY p.init_ts DESC
                 LIMIT 1), w.k + 1
          FROM inits w
         WHERE w.k < %(n)s AND w.init_ts IS NOT NULL
    )
    SELECT h.init_ts, h.target_ts, h.lead_h, h.registry_mw, h.calibrated_mw,
           h.calibration_id, h.method_version
      FROM inits w
      JOIN implied_gen_area_hourly h
        ON {so._AREA_KEY.format(a="h")}
       AND h.init_ts = w.init_ts
     ORDER BY h.init_ts, h.target_ts
"""

# The gate's lines: the outlook's own statement, by calibration_id.
CALIBRATION_SQL = so.CALIBRATION_SQL


def calibration_ids(rows: list[dict]) -> list[int]:
    return so.calibration_ids(rows)


# ── Implied generation: the shaping ─────────────────────────────────────────

def _issuance(rows: list[dict], lines: list[dict]) -> dict:
    """One issuance's rows (ordered by target_ts) -> two arrays from t0.

    Index i is target_ts t0 + i h and lead_h lead0 + i. An hour the issuance
    does not hold, between its first and last, is null in both arrays: never
    0, and never closed up. `cal` is what the outlook would SHOW for the hour
    (solar_outlook.gate), and null for the whole issuance when no hour shows
    a calibrated figure."""
    t0, lead0 = rows[0]["target_ts"], rows[0]["lead_h"]
    width = (rows[-1]["target_ts"] - t0) // _HOUR + 1
    reg: list = [None] * width
    cal: list = [None] * width
    for h in so.gate(rows, lines):
        i = (h["target_ts"] - t0) // _HOUR
        reg[i] = whole_mw(h["registry_mw"])
        cal[i] = whole_mw(h["shown_calibrated_mw"])
    return {
        "init": so._iso(rows[0]["init_ts"]),
        "t0": so._iso(t0),
        "lead0": lead0,
        "reg": reg,
        "cal": cal if any(c is not None for c in cal) else None,
        "method_version": rows[0]["method_version"],
    }


def build_vintages(*, tech: str, area_kind: str, area: str, model: str,
                   rows: list[dict], lines: list[dict]) -> dict:
    """The body. `rows` are VINTAGES_SQL's, ordered (init_ts, target_ts)."""
    by_init: dict = {}
    for r in rows:
        by_init.setdefault(r["init_ts"], []).append(r)
    body = {
        "tech": tech,
        "area_kind": area_kind,
        "area": area,
        "model": model,
        "unit": "MW",
        "step_h": STEP_H,
        "issuances": [_issuance(by_init[i], lines) for i in sorted(by_init)],
        "absence": None,
    }
    if not body["issuances"]:
        body["absence"] = {"reason": "no_issuance",
                           "detail": f"no {model} issuance is banked for "
                                     f"{area_kind}={area}"}
    return body


# ── Degree days ─────────────────────────────────────────────────────────────

# The sources the forecast views carry (pantry 202; recon §1 rows 5-5e). One
# read each. A source added upstream is served once it is named here.
DD_SOURCES = ("GFS", "IFS", "AIFS", "gridpoints_raw")

# One (region, weighting, source_product): every held issuance, all target
# dates. The view cannot push an issued_ts filter (recon §2.3), so none is
# given; every predicate is a column the view groups by.
DD_VINTAGES_SQL = """
    SELECT issued_ts, target_date, hdd_wtd AS hdd, cdd_wtd AS cdd,
           basis_complete, sample_spacing_hours
      FROM v_degree_days_region_forecast
     WHERE region = %(region)s AND weighting = %(weighting)s
       AND source_product = %(source)s
     ORDER BY issued_ts, target_date
"""


def _as_date(v) -> date:
    return v.date() if isinstance(v, datetime) else v


def _dd_issuance(rows: list[dict]) -> dict:
    """One issuance's days (ordered) -> arrays from d0. A day the issuance
    does not hold, between its first and last, is null in all four. An
    incomplete day is served as the view serves it, with its false."""
    d0 = _as_date(rows[0]["target_date"])
    width = (_as_date(rows[-1]["target_date"]) - d0).days + 1
    hdd: list = [None] * width
    cdd: list = [None] * width
    complete: list = [None] * width
    step: list = [None] * width
    for r in rows:
        i = (_as_date(r["target_date"]) - d0).days
        hdd[i] = dd01(r["hdd"])
        cdd[i] = dd01(r["cdd"])
        complete[i] = r["basis_complete"]
        step[i] = r["sample_spacing_hours"]
    return {"issued_ts": so._iso(rows[0]["issued_ts"]), "d0": d0.isoformat(),
            "hdd": hdd, "cdd": cdd, "basis_complete": complete, "sample_step_h": step}


def build_dd_vintages(*, region: str, weighting: str,
                      by_source: dict[str, Iterable[dict]]) -> dict:
    """`by_source[src]` are DD_VINTAGES_SQL's rows for that source."""
    sources = []
    for src in DD_SOURCES:
        per: dict = {}
        for r in by_source.get(src, ()):
            per.setdefault(r["issued_ts"], []).append(r)
        sources.append({"source_product": src, "label": _dd.source_label(src),
                        "issuances": [_dd_issuance(per[i]) for i in sorted(per)]})
    body = {"region": region, "weighting": weighting, "sources": sources, "absence": None}
    if not any(s["issuances"] for s in sources):
        body["absence"] = {"reason": "no_issuance",
                           "detail": f"no forecast issuance is held for "
                                     f"{region} / {weighting}"}
    return body
