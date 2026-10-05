"""Load outlook and CAISO net demand — d091611 (API half), D-09-25-139, D-09-25-140.

    GET /api/load/outlook?area=<CAISO area>   CAISO's own forecast, day by day,
                                              with the product and issue date of
                                              each day, actuals and scores
    GET /api/load/areas                       the 36 areas CAISO forecasts
    GET /api/load/net-demand?area=CISO        load - calibrated solar - calibrated
                                              wind, CAISO's DA reference, truth

READ-ONLY. Every table is a pantry writer's: the three OASIS SLD_FCST products
(ingesters/caiso_load_forecast.py), EIA-930 D and DF, CAISO's native load and
fuel mix, CAISO's DAM renewables by hub, and implied_gen_* (d091567, d091589).

This module holds the SQL and the pure shaping; main.py holds the routes, the
memo and the statement timeout (D-09-25-75). Every read names one
(dataset, series) per LATERAL (this repo's d091551 rule), or one
(tech, area_kind, area, model) key of implied_gen_area_hourly.

D-09-25-139, the load outlook:
  * the number is the publisher's. For each Pacific day the freshest product
    CAISO has published for it: DAM, else 2DA, else 7DA. Each day carries its
    product, its issue time and its lead. Nothing beyond today + 7;
  * the 7DA is ONE issuance per target hour, published seven days before it
    and never revised (the ingester's 2026-07-23 tests). A day three to six
    days out is therefore CAISO's view from four to one days ago, and every
    day says how old its view is;
  * the BA's own EIA-930 day-ahead (DF) is a second line where EIA has the BA.
    For the CAISO system it is not drawn: EIA's CISO DF is CAISO's DAM
    rounded to the MW (measured, docs/handback_2026_10_05_load_and_net_demand_api.md);
  * each product is scored over the trailing 28 Pacific days against the
    area's actual: CAISO's native load for the system, EIA-930 D for a BA ONLY
    where it passes USABILITY_RULE. Fewer than 14 scored days is "not yet
    scored"; no usable actual is "not scored: <reason>".

D-09-25-140, CAISO net demand:
  * net demand = 'CA ISO-TAC' load - CISO calibrated solar - CISO calibrated
    wind, and only where all three exist. A part is calibrated only where its
    line was fitted on the hour's lead (D-09-25-127; the line's fit_lead_min /
    fit_lead_max, as the writer now stores them). Never a registry figure;
  * truth = native load - fuel-mix solar - fuel-mix wind; CAISO's day-ahead
    net demand = DAM load - the three hubs' DAM solar and wind (the reference);
  * scored at day-ahead by PAIRING_RULE.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from solar_outlook import PT, _f, _iso, pacific_day_bounds

UTC = timezone.utc

# ── Words ───────────────────────────────────────────────────────────────────

LABEL = ("CAISO's load forecast as CAISO publishes it (OASIS), with its measured "
         "error. Not a forecast of ours.")

NET_DEMAND_LABEL = ("Net demand: CAISO's load forecast less weather-implied solar and "
                    "wind, drawn only where each part has a scored figure. Not CAISO's "
                    "net load forecast and not a forecast of metered output.")

ATTRIBUTION_CAISO = ("Load forecasts: California ISO, OASIS (SLD_FCST). Actual load, fuel mix "
                     "and hub renewables forecasts: California ISO.")
ATTRIBUTION_EIA = "Balancing-authority forecasts and actuals: U.S. Energy Information Administration, Form EIA-930."

NOT_YET_SCORED = "not yet scored"

# ── Products ────────────────────────────────────────────────────────────────

PRODUCTS = ("DAM", "2DA", "7DA")
PRODUCT_DATASET = {"DAM": "caiso_load_fcst_dam",
                   "2DA": "caiso_load_fcst_2day",
                   "7DA": "caiso_load_fcst_7day"}
PRODUCT_HORIZON_DAYS = {"DAM": 1, "2DA": 2, "7DA": 7}
PRODUCT_NAME = {"DAM": "CAISO day-ahead (DAM)",
                "2DA": "CAISO two-day-ahead (2DA)",
                "7DA": "CAISO seven-day-ahead (7DA)"}
DF = "EIA_DF"
DF_DATASET = "wecc_load_forecast_da_hourly"
D_DATASET = "wecc_load_hourly"
NATIVE = ("caiso_load_hourly", "actual")
MAX_DAYS_AHEAD = 7                     # CAISO publishes nothing beyond D+7
WINDOW_DAYS = 28
MIN_SCORED_DAYS = 14

# publish_time is gridstatus-derived from the OASIS publications schedule
# (meta.generated_ts_source = 'oasis_convention'), not a field CAISO stamps.
ISSUE_SENTENCE = {
    "DAM": ("CAISO's day-ahead load forecast for a day is published at 09:10 PT the "
            "day before (OASIS publication schedule; the time is derived, not stamped "
            "by CAISO)."),
    "2DA": ("CAISO's two-day-ahead load forecast for a day is published at 09:10 PT "
            "two days before (OASIS publication schedule; the time is derived, not "
            "stamped by CAISO)."),
    "7DA": ("CAISO's seven-day-ahead load forecast for a day is published once, at "
            "09:10 PT seven days before, and never revised. A day three to six days "
            "out is CAISO's view from four to one days ago."),
    DF: ("The balancing authority's own day-ahead forecast as reported to EIA-930 "
         "(DF). EIA does not report when it was issued."),
}

# ── The 36 areas ────────────────────────────────────────────────────────────
#
# The SLD_FCST area set (pantry ingesters/caiso_load_forecast.py TAC_AREAS),
# with the EIA-930 respondent each one is measured by, where EIA has one we
# bank (wecc_load_hourly / wecc_load_forecast_da_hourly, 26 respondents).
# kind: system | tac | weim_ba | sub_area. group is the page's picker group.
# A sub-area is a piece of a BA that CAISO forecasts separately; EIA reports
# only the BA, so it has no counterpart.

SYSTEM = "CA ISO-TAC"

_A = [
    # code,        name,                                             kind,       eia,    parent
    ("CA ISO-TAC", "CAISO system",                                   "system",   "CISO", None),
    ("PGE-TAC",    "PG&E transmission access charge area",           "tac",      None,   None),
    ("SCE-TAC",    "SCE transmission access charge area",            "tac",      None,   None),
    ("SDGE-TAC",   "SDG&E transmission access charge area",          "tac",      None,   None),
    ("VEA-TAC",    "Valley Electric transmission access charge area", "tac",     None,   None),
    ("MWD-TAC",    "Metropolitan Water District transmission access charge area", "tac", None, None),
    ("AVA",        "Avista",                                         "weim_ba",  "AVA",  None),
    ("AVRN",       "Avangrid Renewables",                            "weim_ba",  None,   None),
    ("AZPS",       "Arizona Public Service",                         "weim_ba",  "AZPS", None),
    ("BANC",       "Balancing Authority of Northern California",     "weim_ba",  "BANC", None),
    ("BANCMID",    "BANC: Modesto Irrigation District",              "sub_area", None,   "BANC"),
    ("BANCRDNG",   "BANC: Redding",                                  "sub_area", None,   "BANC"),
    ("BANCRSVL",   "BANC: Roseville",                                "sub_area", None,   "BANC"),
    ("BANCSMUD",   "BANC: SMUD",                                     "sub_area", None,   "BANC"),
    ("BANCWASN",   "BANC: WAPA Sierra Nevada",                       "sub_area", None,   "BANC"),
    ("BHBA",       "BHBA",                                           "weim_ba",  None,   None),
    ("BPAT",       "Bonneville Power Administration",                "weim_ba",  "BPAT", None),
    ("EPE",        "El Paso Electric",                               "weim_ba",  "EPE",  None),
    ("GWA",        "GWA",                                            "weim_ba",  None,   None),
    ("IPCO",       "Idaho Power",                                    "weim_ba",  "IPCO", None),
    ("LADWP",      "Los Angeles Department of Water and Power",      "weim_ba",  "LDWP", None),
    ("NEVP",       "NV Energy (Nevada Power)",                       "weim_ba",  "NEVP", None),
    ("NWMT",       "NorthWestern Energy (Montana)",                  "weim_ba",  "NWMT", None),
    ("PACE",       "PacifiCorp East",                                "weim_ba",  "PACE", None),
    ("PACW",       "PacifiCorp West",                                "weim_ba",  "PACW", None),
    ("PGE",        "Portland General Electric",                      "weim_ba",  "PGE",  None),
    ("PNM",        "Public Service Company of New Mexico",           "weim_ba",  "PNM",  None),
    ("PSEI",       "Puget Sound Energy",                             "weim_ba",  "PSEI", None),
    ("SCL",        "Seattle City Light",                             "weim_ba",  "SCL",  None),
    ("SRP",        "Salt River Project",                             "weim_ba",  "SRP",  None),
    ("TEPC",       "Tucson Electric Power",                          "weim_ba",  "TEPC", None),
    ("TIDC",       "Turlock Irrigation District",                    "weim_ba",  "TIDC", None),
    ("TPWR",       "Tacoma Power",                                   "weim_ba",  "TPWR", None),
    ("WALC",       "WAPA Desert Southwest",                          "weim_ba",  "WALC", None),
    ("WALCAEPCO",  "WALC: Arizona Electric Power Cooperative",       "sub_area", None,   "WALC"),
    ("WALCDSW",    "WALC: Desert Southwest",                         "sub_area", None,   "WALC"),
]
AREAS = {a[0]: {"code": a[0], "name": a[1], "kind": a[2], "eia": a[3], "parent": a[4],
                "group": {"system": "caiso", "tac": "tac"}.get(a[2], "western")}
         for a in _A}
AREA_ORDER = [a[0] for a in _A]

# EIA-930 respondents we bank (both D and DF), with EIA's own names
# (row meta.respondent_name, read 2026-10-05).
EIA_RESPONDENTS = {
    "AVA": "Avista Corporation",
    "AZPS": "Arizona Public Service Company",
    "BANC": "Balancing Authority of Northern California",
    "BPAT": "Bonneville Power Administration",
    "CISO": "California Independent System Operator",
    "EPE": "El Paso Electric Company",
    "IPCO": "Idaho Power Company",
    "LDWP": "Los Angeles Department of Water and Power",
    "NEVP": "Nevada Power Company",
    "NWMT": "NorthWestern Corporation",
    "PACE": "PacifiCorp East",
    "PACW": "PacifiCorp West",
    "PGE": "Portland General Electric Company",
    "PNM": "Public Service Company of New Mexico",
    "PSEI": "Puget Sound Energy, Inc.",
    "SCL": "Seattle City Light",
    "SRP": "Salt River Project Agricultural Improvement and Power District",
    "TEPC": "Tucson Electric Power",
    "TIDC": "Turlock Irrigation District",
    "TPWR": "City of Tacoma, Department of Public Utilities, Light Division",
    "WALC": "Western Area Power Administration - Desert Southwest Region",
}
# Banked respondents CAISO does not forecast: CHPD, DOPD, GCPD, IID, PSCO.

# The BAs whose EIA-930 D is tested as an actual (the system's actual is native).
BA_ACTUALS = tuple((code, a["eia"]) for code, a in AREAS.items()
                   if a["kind"] == "weim_ba" and a["eia"] is not None)

NO_ACTUAL_REASON = {
    "tac": "CAISO publishes no actual load by TAC area that is banked",
    "sub_area": "EIA-930 reports only the whole balancing authority, not this sub-area, "
                "and CAISO publishes no actual for it that is banked",
    "weim_ba": "no EIA-930 respondent is banked for this area",
}
SYSTEM_DF_REASON = ("EIA-930 DF for CISO is CAISO's own DAM forecast rounded to the MW, "
                    "so it is not drawn as a second line")


def parse_area(area: Optional[str]) -> str:
    if not area:
        raise ValueError("area is required: one of the 36 CAISO areas (GET /api/load/areas)")
    if area not in AREAS:
        raise ValueError(f"area must be one of the CAISO areas {AREA_ORDER}, got {area!r}")
    return area


def df_code(area: str) -> Optional[str]:
    """The EIA respondent whose DF is this area's second line, or None.
    L3: only where EIA has the BA; the system's DF is CAISO's DAM again."""
    a = AREAS[area]
    if a["kind"] != "weim_ba":
        return None
    return a["eia"]


# ── The usability rule for EIA-930 D as an actual (Step 1) ─────────────────
#
# Computed over the same trailing 28 Pacific days as the scores, from
# aggregates USABILITY_SQL returns, so /areas and /outlook apply one rule.
#   coverage   hours of D present / hours in the window          >= 0.95
#   dropouts   hours of D below half the area's median D / hours <= 0.005
#   agreement  Pearson r of D against CAISO's DAM for the area   >= 0.90
#   clock      r at shift 0 is the best of shifts -1, 0, +1 h
#   scale      median DAM / median D                              in [0.90, 1.10]
# "scale" is a footprint test: CAISO's forecast area and EIA's respondent must
# measure the same load, or every error is the footprint's.

USABILITY_RULE = {
    "window_days": WINDOW_DAYS,
    "min_coverage": 0.95,
    "dropout_fraction_of_median": 0.5,
    "max_dropout_share": 0.005,
    "min_r_vs_caiso_dam": 0.90,
    "clock_shifts_h": [-1, 0, 1],
    "scale_min": 0.90,
    "scale_max": 1.10,
}

_BA_VALUES = ",\n            ".join(f"('{c}', '{e}')" for c, e in BA_ACTUALS)

# One lateral per (dataset, series): EIA D for the respondent, CAISO's DAM for
# the area. The DAM window is an hour wider each side for the clock shifts.
# The shifted hour is a column of its own (ds.fts), so the join to the DAM is a
# hash on (area, ts). As a range join, or with the shift as an expression in
# the join, the planner hashed on area alone and filtered 9-27 M pairs
# (0.9-6.8 s; plans.md).
USABILITY_SQL = f"""
    WITH m AS (
        SELECT v.area, v.eia
          FROM (VALUES
            {_BA_VALUES}
               ) AS v(area, eia)
         WHERE v.area = ANY(%(areas)s)
    ),
    d AS (
        SELECT m.area, l.ts, l.v
          FROM m
         CROSS JOIN LATERAL (
            SELECT t.ts, t.value::float8 AS v
              FROM timeseries_values t
             WHERE t.dataset = '{D_DATASET}' AND t.series = m.eia
               AND t.ts >= %(lo)s AND t.ts < %(hi)s
               AND t.value IS NOT NULL
         ) AS l
    ),
    f AS (
        SELECT m.area, l.ts, l.v
          FROM m
         CROSS JOIN LATERAL (
            SELECT t.ts, t.value::float8 AS v
              FROM timeseries_values t
             WHERE t.dataset = '{PRODUCT_DATASET["DAM"]}' AND t.series = m.area
               AND t.ts >= %(lo)s - interval '1 hour'
               AND t.ts < %(hi)s + interval '1 hour'
               AND t.value IS NOT NULL
         ) AS l
    ),
    dm AS (
        SELECT area, count(*) AS n_hours,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY v) AS d_median
          FROM d GROUP BY area
    ),
    dr AS (
        SELECT d.area, count(*) FILTER (WHERE d.v < %(dropout)s * dm.d_median) AS n_dropout
          FROM d JOIN dm USING (area) GROUP BY d.area
    ),
    fm AS (
        SELECT area, percentile_cont(0.5) WITHIN GROUP (ORDER BY v) AS dam_median
          FROM f WHERE ts >= %(lo)s AND ts < %(hi)s GROUP BY area
    ),
    ds AS MATERIALIZED (
        SELECT d.area, s.k, d.ts + s.k * interval '1 hour' AS fts, d.v
          FROM d CROSS JOIN (VALUES (-1), (0), (1)) AS s(k)
    ),
    cs AS (
        SELECT ds.area, ds.k, corr(ds.v, f.v) AS r
          FROM ds JOIN f ON f.area = ds.area AND f.ts = ds.fts
         GROUP BY ds.area, ds.k
    ),
    c AS (
        SELECT area, max(r) FILTER (WHERE k = -1) AS r_m1,
               max(r) FILTER (WHERE k = 0) AS r_0,
               max(r) FILTER (WHERE k = 1) AS r_p1
          FROM cs GROUP BY area
    )
    SELECT m.area, m.eia, dm.n_hours, dm.d_median, dr.n_dropout, fm.dam_median,
           c.r_m1, c.r_0, c.r_p1
      FROM m
      LEFT JOIN dm USING (area)
      LEFT JOIN dr USING (area)
      LEFT JOIN fm USING (area)
      LEFT JOIN c USING (area)
     ORDER BY m.area
"""


def usability(row: Optional[dict], hours_expected: int) -> dict:
    """The rule's verdict on one BA's EIA-930 D, every test with its number."""
    R = USABILITY_RULE
    row = row or {}
    n = int(row.get("n_hours") or 0)
    med, dam_med = _f(row.get("d_median")), _f(row.get("dam_median"))
    r = {k: _f(row.get(k)) for k in ("r_m1", "r_0", "r_p1")}
    drop = int(row.get("n_dropout") or 0)
    cov = n / hours_expected if hours_expected else 0.0
    share = drop / n if n else None
    scale = dam_med / med if med and dam_med is not None else None
    best = max((v for v in r.values() if v is not None), default=None)
    tests = {
        "coverage": {"value": round(cov, 4), "hours": n, "hours_expected": hours_expected,
                     "pass": cov >= R["min_coverage"]},
        "dropouts": {"value": None if share is None else round(share, 4), "hours": drop,
                     "pass": share is not None and share <= R["max_dropout_share"]},
        "agreement": {"value": None if r["r_0"] is None else round(r["r_0"], 4),
                      "pass": r["r_0"] is not None and r["r_0"] >= R["min_r_vs_caiso_dam"]},
        "clock": {"value": {"-1": _round(r["r_m1"]), "0": _round(r["r_0"]), "+1": _round(r["r_p1"])},
                  "pass": r["r_0"] is not None and best is not None and r["r_0"] >= best},
        "scale": {"value": None if scale is None else round(scale, 4),
                  "d_median_mw": med, "dam_median_mw": dam_med,
                  "pass": scale is not None and R["scale_min"] <= scale <= R["scale_max"]},
    }
    failed = [k for k, t in tests.items() if not t["pass"]]
    return {"usable": not failed, "failed": failed, "tests": tests}


def _round(v, n=4):
    return None if v is None else round(v, n)


_FAIL_WORDS = {
    "coverage": "fewer than 95% of the window's hours are present",
    "dropouts": "more than 0.5% of its hours fall below half its median",
    "agreement": "its correlation with CAISO's DAM forecast is under 0.90",
    "clock": "it lines up best with CAISO's forecast shifted by an hour",
    "scale": "its median is outside 90-110% of CAISO's forecast for the area (a different footprint)",
}


def fail_reason(eia: str, verdict: dict) -> str:
    return (f"EIA-930 D for {eia} fails the usability rule: "
            + "; ".join(_FAIL_WORDS[k] for k in verdict["failed"]))


def actual_basis(area: str, verdict: Optional[dict]) -> dict:
    a = AREAS[area]
    if a["kind"] == "system":
        return {"source": "caiso_native", "dataset": NATIVE[0], "series": NATIVE[1],
                "usable": True, "rule": None, "verdict": None,
                "note": ("CAISO's own actual load, net of behind-the-meter solar. "
                         "EIA-930 D for CISO is not used (D-09-25-112 clause 3).")}
    if a["eia"] is None:
        return {"source": None, "dataset": None, "series": None, "usable": False,
                "rule": None, "verdict": None,
                "reason": NO_ACTUAL_REASON[a["kind"]]}
    v = verdict or usability(None, 0)
    out = {"source": "eia930_d", "dataset": D_DATASET, "series": a["eia"],
           "usable": v["usable"], "rule": USABILITY_RULE, "verdict": v}
    if not v["usable"]:
        out["reason"] = fail_reason(a["eia"], v)
    return out


# ── Windows ─────────────────────────────────────────────────────────────────

def today_pt(now: datetime) -> date:
    return now.astimezone(PT).date()


def score_days(now: datetime) -> list[date]:
    """The trailing 28 Pacific days, ending yesterday (today is not complete)."""
    t = today_pt(now)
    return [t - timedelta(days=k) for k in range(WINDOW_DAYS, 0, -1)]


def windows(now: datetime) -> dict:
    """UTC bounds: the score window [score_lo, score_hi) and the outlook
    [today_lo, outlook_hi), today .. today + 7."""
    t = today_pt(now)
    days = score_days(now)
    return {"today": t,
            "score_days": days,
            "score_lo": pacific_day_bounds(days[0])[0],
            "score_hi": pacific_day_bounds(days[-1])[1],
            "today_lo": pacific_day_bounds(t)[0],
            "outlook_hi": pacific_day_bounds(t + timedelta(days=MAX_DAYS_AHEAD))[1]}


def day_hours(d: date) -> list[datetime]:
    lo, hi = pacific_day_bounds(d)
    return [lo + timedelta(hours=k) for k in range(int((hi - lo).total_seconds() // 3600))]


# ── SQL: the outlook ────────────────────────────────────────────────────────

_PRODUCT_VALUES = ",\n            ".join(f"('{p}', '{PRODUCT_DATASET[p]}')" for p in PRODUCTS)

# The three products for one area, one lateral per (dataset, series), over
# the score window and the outlook together ([lo, hi) is one PK range each).
FCST_SQL = f"""
    SELECT p.product, l.ts, l.value, l.publish_time
      FROM (VALUES
            {_PRODUCT_VALUES}
           ) AS p(product, dataset)
     CROSS JOIN LATERAL (
        SELECT t.ts, t.value::float8 AS value, t.meta->>'publish_time' AS publish_time
          FROM timeseries_values t
         WHERE t.dataset = p.dataset AND t.series = %(area)s
           AND t.ts >= %(lo)s AND t.ts < %(hi)s
           AND t.value IS NOT NULL
         ORDER BY t.ts
     ) AS l
"""

# One (dataset, series): a plain range down idx_tsv_series_ts.
SERIES_SQL = """
    SELECT t.ts, t.value::float8 AS value
      FROM timeseries_values t
     WHERE t.dataset = %(dataset)s AND t.series = %(series)s
       AND t.ts >= %(lo)s AND t.ts < %(hi)s
       AND t.value IS NOT NULL
     ORDER BY t.ts
"""


def _pt_instant(raw) -> Optional[datetime]:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=UTC)
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def split_products(rows: list[dict]) -> dict:
    """{product: {ts: (value, issued_at)}}."""
    out: dict = {p: {} for p in PRODUCTS}
    for r in rows:
        if r["product"] in out and r["value"] is not None:
            out[r["product"]][r["ts"]] = (float(r["value"]), _pt_instant(r.get("publish_time")))
    return out


def series_map(rows: list[dict]) -> dict:
    return {r["ts"]: float(r["value"]) for r in rows if r["value"] is not None}


# ── Shaping: the day's product ──────────────────────────────────────────────

def _fmt_day(d: date) -> str:
    return f"{d.strftime('%a %b')} {d.day}"


def _ago(n: int) -> str:
    return "today" if n == 0 else "yesterday" if n == 1 else f"{n} days ago"


def age_sentence(product: str, day: date, issued: Optional[datetime], today: date) -> str:
    if issued is None:
        return f"{PRODUCT_NAME[product]} for {_fmt_day(day)}; its issue time is not banked."
    loc = issued.astimezone(PT)
    ago = (today - loc.date()).days
    s = (f"{PRODUCT_NAME[product]} for {_fmt_day(day)}, issued {_fmt_day(loc.date())} "
         f"at {loc.strftime('%H:%M')} PT, {_ago(ago)}")
    if product == "7DA" and ago > 0:
        s += ". CAISO does not re-issue it"
    return s + "."


def choose_days(products: dict, today: date) -> list[dict]:
    """Per Pacific day, today .. today + 7, the freshest product CAISO has
    published for it: the shortest horizon that covers every hour of the day,
    else the one covering most hours. A day no product reaches is left out."""
    out = []
    for k in range(MAX_DAYS_AHEAD + 1):
        d = today + timedelta(days=k)
        hrs = day_hours(d)
        best = None
        for p in PRODUCTS:
            n = sum(1 for h in hrs if h in products[p])
            if n == len(hrs):
                best = (p, n)
                break
            if n and (best is None or n > best[1]):
                best = (p, n)
        if best is None:
            continue
        p = best[0]
        got = [(h, *products[p][h]) for h in hrs if h in products[p]]
        issued = max((i for _h, _v, i in got if i is not None), default=None)
        issue_date = issued.astimezone(PT).date() if issued else None
        out.append({"day": d, "product": p, "hours": got, "issued_at": issued,
                    "issue_date": issue_date, "hours_in_day": len(hrs)})
    return out


def build_hours_and_days(products: dict, df: dict, today: date) -> tuple[list, list]:
    hours, days = [], []
    for c in choose_days(products, today):
        d, p = c["day"], c["product"]
        for h, v, issued in c["hours"]:
            idate = issued.astimezone(PT).date() if issued else None
            hours.append({
                "target_ts": _iso(h),
                "mw": round(v, 3),
                "product": p,
                "issued_at": _iso(issued),
                "lead_days": (d - idate).days if idate else None,
                "days_ahead": (d - today).days,
                "df_mw": _f(df.get(h)),
            })
        vals = [(v, h) for h, v, _i in c["hours"]]
        pk_mw, pk_ts = max(vals, key=lambda x: (x[0], -x[1].timestamp()))
        idate = c["issue_date"]
        days.append({
            "day": d.isoformat(),
            "product": p,
            "product_name": PRODUCT_NAME[p],
            "issued_at": _iso(c["issued_at"]),
            "issue_date": _iso(idate),
            "lead_days": (d - idate).days if idate else None,
            "days_ahead": (d - today).days,
            "issued_days_ago": (today - idate).days if idate else None,
            "hours_in_day": c["hours_in_day"],
            "hours_covered": len(c["hours"]),
            "complete": len(c["hours"]) == c["hours_in_day"],
            "energy_mwh": round(sum(v for v, _h in vals), 3),
            "peak_mw": round(pk_mw, 3),
            "peak_ts": _iso(pk_ts),
            "peak_he": pk_ts.astimezone(PT).hour + 1,
            "age_sentence": age_sentence(p, d, c["issued_at"], today),
        })
    for a, b in zip(days, days[1:]):
        b["product_changes"] = a["product"] != b["product"]
    if days:
        days[0]["product_changes"] = False
    return hours, days


# ── Shaping: scores ─────────────────────────────────────────────────────────

def _pearson(x: list[float], y: list[float]) -> Optional[float]:
    n = len(x)
    if n < 3:
        return None
    mx, my = sum(x) / n, sum(y) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    sxx = sum((a - mx) ** 2 for a in x)
    syy = sum((b - my) ** 2 for b in y)
    if sxx == 0 or syy == 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def score_series(fc: dict, act: dict, days: list[date]) -> dict:
    """One forecast against one actual over `days`. A day counts only where
    every one of its hours has both; fewer than 14 such days is not yet scored.
    Peak MAPE and the peak-hour hit are per day (ties: the earliest hour)."""
    pairs, peaks, scored = [], [], []
    for d in days:
        hrs = day_hours(d)
        if not all(h in fc and h in act for h in hrs):
            continue
        scored.append(d)
        day = [(fc[h], act[h], h) for h in hrs]
        pairs.extend(day)
        fpk = max(day, key=lambda x: (x[0], -x[2].timestamp()))
        apk = max(day, key=lambda x: (x[1], -x[2].timestamp()))
        peaks.append((fpk[0], apk[1], fpk[2] == apk[2]))
    base = {"window_start": days[0].isoformat(), "window_end": days[-1].isoformat(),
            "n_days": len(scored), "n_hours": len(pairs), "min_days": MIN_SCORED_DAYS}
    if len(scored) < MIN_SCORED_DAYS:
        return {"status": "not_yet_scored", "text": NOT_YET_SCORED, **base}
    e = [f - a for f, a, _h in pairs]
    pos = [(f, a) for f, a, _h in pairs if a > 0]
    pk = [(f, a, hit) for f, a, hit in peaks if a > 0]
    r = _pearson([f for f, _a, _h in pairs], [a for _f, a, _h in pairs])
    return {
        "status": "scored", **base,
        "mape_pct": round(100 * sum(abs(f - a) / a for f, a in pos) / len(pos), 3) if pos else None,
        "mae_mw": round(sum(abs(x) for x in e) / len(e), 3),
        "bias_mw": round(sum(e) / len(e), 3),
        "r": None if r is None else round(r, 5),
        "peak_mape_pct": round(100 * sum(abs(f - a) / a for f, a, _ in pk) / len(pk), 3) if pk else None,
        "peak_hour_hit_pct": round(100 * sum(1 for *_x, hit in peaks if hit) / len(peaks), 2),
    }


def not_scored(reason: str, days: list[date]) -> dict:
    return {"status": "not_scored", "text": f"not scored: {reason}", "reason": reason,
            "window_start": days[0].isoformat(), "window_end": days[-1].isoformat()}


def build_scores(area: str, products: dict, df: dict, act: dict, basis: dict,
                 days: list[date]) -> dict:
    """Per product (and DF where it is drawn): a score, "not yet scored", or
    "not scored: <reason>". Only a usable actual scores anything (L2)."""
    keys = list(PRODUCTS) + ([DF] if df_code(area) else [])
    if not basis["usable"]:
        return {k: not_scored(basis["reason"], days) for k in keys}
    out = {}
    for p in PRODUCTS:
        out[p] = score_series({h: v for h, (v, _i) in products[p].items()}, act, days)
    if DF in keys:
        out[DF] = score_series(df, act, days)
    return out


# ── The outlook ─────────────────────────────────────────────────────────────

def build_outlook(*, area: str, now: datetime, fcst_rows: list[dict],
                  df_rows: list[dict], actual_rows: list[dict],
                  usability_row: Optional[dict]) -> dict:
    w = windows(now)
    a = AREAS[area]
    verdict = None
    if a["kind"] == "weim_ba" and a["eia"] is not None:
        verdict = usability(usability_row, len(_window_hours(w)))
    basis = actual_basis(area, verdict)
    products = split_products(fcst_rows)
    dfc = df_code(area)
    df = series_map(df_rows) if dfc else {}
    act = series_map(actual_rows) if basis["usable"] else {}
    hours, days = build_hours_and_days(products, df, w["today"])
    hours = [h for h in hours if datetime.fromisoformat(h["target_ts"]) < w["outlook_hi"]]   # L4
    newest = {p: _iso(max((i for _v, i in products[p].values() if i is not None), default=None))
              for p in PRODUCTS}
    elapsed = [{"target_ts": _iso(t), "mw": round(v, 3)} for t, v in sorted(act.items())
               if w["today_lo"] <= t < now]
    eia = a["eia"]
    body = {
        "label": LABEL,
        "attributions": [ATTRIBUTION_CAISO] + ([ATTRIBUTION_EIA] if eia else []),
        "area": _area_obj(area),
        "unit": "MW",
        "today": w["today"].isoformat(),
        "tz": "America/Los_Angeles",
        "max_days_ahead": MAX_DAYS_AHEAD,
        "hours": hours,
        "days": days,
        "actuals": elapsed,
        "df": ({"respondent": dfc, "respondent_name": EIA_RESPONDENTS.get(dfc),
                "label": f"{EIA_RESPONDENTS.get(dfc, dfc)}'s own day-ahead forecast (EIA-930 DF)",
                "dataset": DF_DATASET}
               if dfc else None),
        "df_absent_reason": (None if dfc else SYSTEM_DF_REASON if a["kind"] == "system"
                             else "EIA-930 has no respondent for this area"),
        "scores": build_scores(area, products, df, act, basis, w["score_days"]),
        "actual_basis": basis,
        "issue_sentence": {**{p: ISSUE_SENTENCE[p] for p in PRODUCTS},
                           **({DF: ISSUE_SENTENCE[DF]} if dfc else {})},
        "newest_issues": newest,
        "absence": None,
    }
    if not hours:
        body["absence"] = {"reason": "no_forecast",
                           "detail": f"no CAISO load forecast is banked for {area} from today on"}
    return body


def _window_hours(w: dict) -> list[datetime]:
    out = []
    for d in w["score_days"]:
        out.extend(day_hours(d))
    return out


def _area_obj(area: str) -> dict:
    a = AREAS[area]
    return {"code": a["code"], "name": a["name"], "kind": a["kind"], "group": a["group"],
            "parent": a["parent"], "eia": a["eia"],
            "eia_name": EIA_RESPONDENTS.get(a["eia"]) if a["eia"] else None}


def build_areas(*, now: datetime, usability_rows: list[dict]) -> dict:
    w = windows(now)
    n = len(_window_hours(w))
    by = {r["area"]: r for r in usability_rows}
    out = []
    for code in AREA_ORDER:
        a = AREAS[code]
        verdict = usability(by.get(code), n) if (a["kind"] == "weim_ba" and a["eia"]) else None
        basis = actual_basis(code, verdict)
        out.append({**_area_obj(code),
                    "df_available": df_code(code) is not None,
                    "actual_source": basis["source"],
                    "scored": basis["usable"],
                    "not_scored_reason": None if basis["usable"] else basis["reason"],
                    "usability": verdict})
    return {"label": LABEL, "count": len(out), "window_start": w["score_days"][0].isoformat(),
            "window_end": w["score_days"][-1].isoformat(), "usability_rule": USABILITY_RULE,
            "groups": {"caiso": "CAISO", "tac": "CAISO TAC areas",
                       "western": "Western balancing areas"},
            "areas": out}


# ═══════════════════════════════════════════════════════════════════════════
# Net demand (D-09-25-140)
# ═══════════════════════════════════════════════════════════════════════════

ND_AREAS = ("CISO",)
GEN = {"solar": ("solar_pv", "gfs"), "wind": ("wind", "hrrr_gfs")}
HUB_DAM = tuple((h, tech) for h in ("NP15", "ZP26", "SP15") for tech in ("Solar", "Wind"))
HUB_DAM_DATASET = "caiso_renewables_fcst_dam"
FUEL_MIX = ("caiso_fuel_mix_hourly", ("solar", "wind"))

PAIRING_RULE = (
    "Scored at day-ahead. For each target Pacific day D in the trailing 28: ours = "
    "D's CAISO DAM load less the calibrated CISO solar and wind of the 12Z issuance "
    "of D-1 (each hour's figure only where its line was fitted on that lead); "
    "CAISO's = D's DAM load less the DAM solar and wind of NP15, ZP26 and SP15. "
    "Both against the truth: CAISO's actual load less fuel-mix solar and wind. "
    "A day missing any hour of any part is not scored. Fewer than 14 scored days "
    "is not yet scored. Percent error is WAPE (sum of |error| over sum of truth): "
    "midday net demand nears zero, so an hourly MAPE is undefined.")

WIND_ATTRIBUTION = (
    "Contains information from the Wind Turbine Library (OpenEnergy Platform, "
    "supply.wind_turbine_library, (c) Reiner Lemoine Institut), which is made "
    "available under the Open Database License (ODbL-1.0): "
    "https://opendatacommons.org/licenses/odbl/1-0/")

REFERENCE_SCOPE_NOTE = (
    "CAISO's hub DAM solar and wind (NP15, ZP26, SP15) do not cover all of the "
    "fuel mix's solar and wind, so CAISO's DA net demand sits above the truth "
    "by about the uncovered generation; its bias is mostly that.")

_HUB_VALUES = ",\n            ".join(f"('{h}:{t}')" for h, t in HUB_DAM)
HUB_DAM_SQL = f"""
    SELECT s.series, l.ts, l.value
      FROM (VALUES
            {_HUB_VALUES}
           ) AS s(series)
     CROSS JOIN LATERAL (
        SELECT t.ts, t.value::float8 AS value
          FROM timeseries_values t
         WHERE t.dataset = '{HUB_DAM_DATASET}' AND t.series = s.series
           AND t.ts >= %(lo)s AND t.ts < %(hi)s
           AND t.value IS NOT NULL
         ORDER BY t.ts
     ) AS l
"""

TRUTH_SQL = f"""
    SELECT s.part, l.ts, l.value
      FROM (VALUES
            ('load', '{NATIVE[0]}', '{NATIVE[1]}'),
            ('solar', '{FUEL_MIX[0]}', 'solar'),
            ('wind', '{FUEL_MIX[0]}', 'wind')
           ) AS s(part, dataset, series)
     CROSS JOIN LATERAL (
        SELECT t.ts, t.value::float8 AS value
          FROM timeseries_values t
         WHERE t.dataset = s.dataset AND t.series = s.series
           AND t.ts >= %(lo)s AND t.ts < %(hi)s
           AND t.value IS NOT NULL
         ORDER BY t.ts
     ) AS l
"""

_GEN_KEY = """g.tech = %(tech)s AND g.area_kind = 'ba' AND g.area = %(area)s
           AND g.model = %(model)s"""

# The newest issuance: LIMIT 1 down the primary key.
GEN_NEWEST_SQL = f"""
    SELECT g.init_ts
      FROM implied_gen_area_hourly g
     WHERE {_GEN_KEY}
     ORDER BY g.init_ts DESC
     LIMIT 1
"""

# Rows of the named issuances (the newest; or the backtest's 12Z of each D-1),
# a PK probe per init_ts.
GEN_ROWS_SQL = f"""
    SELECT g.init_ts, g.target_ts, g.lead_h, g.lead_band,
           g.registry_mw, g.calibrated_mw, g.calibration_id
      FROM implied_gen_area_hourly g
     WHERE {_GEN_KEY}
       AND g.init_ts = ANY(%(inits)s)
       AND g.target_ts >= %(lo)s AND g.target_ts < %(hi)s
     ORDER BY g.init_ts, g.target_ts
"""

# The lines the rows carry, with the leads each was fitted on (stored by the
# writer since pantry d091607; null means the line is in force at no lead).
LINES_SQL = """
    SELECT c.calibration_id, c.tech, c.lead_band, c.fit_end,
           c.fit_lead_min, c.fit_lead_max, c.fitted_at
      FROM implied_gen_calibration c
     WHERE c.calibration_id = ANY(%(ids)s)
"""

ABSENT = {
    "registry_only": "the hour carries no calibrated figure (registry only)",
    "beyond_fitted_leads": "the hour's lead is outside the leads its line was fitted on",
    "no_line": "the hour's calibration line is not banked",
    "no_issuance_hour": "the issuance does not reach this hour",
    "no_load_forecast": "CAISO publishes no load forecast for this hour",
    "no_12z_issuance": "no 12Z issuance of D-1 is banked",
    "no_dam_load": "CAISO's DAM load is not banked for every hour",
    "no_truth": "the truth is not banked for every hour",
}


def parse_nd_area(area: Optional[str]) -> str:
    area = area or "CISO"
    if area not in ND_AREAS:
        raise ValueError(f"net demand is served for {list(ND_AREAS)} only "
                         f"(D-09-25-140 clause 5), got {area!r}")
    return area


def gate(row: dict, lines: dict) -> tuple[Optional[float], Optional[str]]:
    """(calibrated MW the hour may show, or None with the reason). The line the
    row carries must have been fitted on the row's lead (D-09-25-127). An hour
    whose sites imply nothing (registry 0: night for solar) is the line's 0 by
    the writer's rule (apply_line) wherever the line reaches that far."""
    cal, cid = row.get("calibrated_mw"), row.get("calibration_id")
    if cal is None or cid is None:
        return None, "registry_only"
    line = lines.get(int(cid))
    if line is None:
        return None, "no_line"
    lo, hi, lead = line.get("fit_lead_min"), line.get("fit_lead_max"), row["lead_h"]
    if lo is None or hi is None:
        return None, "beyond_fitted_leads"
    if lo <= lead <= hi:
        return float(cal), None
    if (row.get("registry_mw") or 0) == 0 and lead <= hi:
        return 0.0, None
    return None, "beyond_fitted_leads"


def _gen_by_ts(rows: list[dict], lines: dict, init) -> dict:
    """{target_ts: (mw | None, reason, lead_h)} for one issuance."""
    out = {}
    for r in rows:
        if r["init_ts"] != init:
            continue
        mw, why = gate(r, lines)
        out[r["target_ts"]] = (mw, why, r["lead_h"])
    return out


def _absent_spans(hours: list[dict], present) -> tuple[Optional[dict], list[list[dict]]]:
    """(first present hour, [runs of absent hours after it])."""
    first, spans, run = None, [], []
    for h in hours:
        if present(h):
            if first is None:
                first = h
            if run:
                spans.append(run)
                run = []
        elif first is not None:
            run.append(h)
    if run:
        spans.append(run)
    return first, spans


def _span(run: list[dict], part: Optional[str]) -> dict:
    h0 = run[0]
    out = {"first_absent_ts": h0["target_ts"], "last_absent_ts": run[-1]["target_ts"],
           "hours": len(run)}
    if part is not None:
        why = h0[f"{part}_absent_reason"]
        out.update({"first_absent_lead_h": h0.get(f"{part}_lead_h"), "reason": why,
                    "detail": ABSENT.get(why)})
    else:
        parts: dict = {}
        for h in run:
            for a in h["absent_part"] or []:
                parts.setdefault(a["part"], a["reason"])
        out["parts"] = [{"part": k, "reason": v, "detail": ABSENT.get(v)} for k, v in parts.items()]
    return out


def _stop(hours: list[dict], part: str) -> dict:
    """Where the part stops: the absent run that reaches the end of the
    outlook, after its last present hour; and the gaps (absent runs it comes
    back from). A part present to the end has no stop; one never present
    stops at its first hour."""
    first, spans = _absent_spans(hours, lambda h: h[f"{part}_mw"] is not None)
    if first is None:
        return {"part": part, "present": False, "last_ts": None, "last_lead_h": None,
                "stop": _span(hours, part) if hours else None, "gaps": []}
    end = hours[-1]["target_ts"]
    stop = spans[-1] if spans and spans[-1][-1]["target_ts"] == end else None
    gaps = spans[:-1] if stop else spans
    last = None
    if stop:
        i = next(k for k, h in enumerate(hours) if h["target_ts"] == stop[0]["target_ts"])
        last = hours[i - 1]
    return {"part": part, "present": True,
            "last_ts": last["target_ts"] if last else None,
            "last_lead_h": last.get(f"{part}_lead_h") if last else None,
            "stop": _span(stop, part) if stop else None,
            "gaps": [_span(g, part) for g in gaps]}


def _nd_stop(hours: list[dict]) -> dict:
    first, spans = _absent_spans(hours, lambda h: h["net_demand_mw"] is not None)
    if first is None:
        return {"first_ts": None, "last_ts": None, "stop": None, "gaps": [], "sentence":
                "Net demand is not drawn: no hour has all three parts with a scored figure."}
    end = hours[-1]["target_ts"]
    stop = spans[-1] if spans and spans[-1][-1]["target_ts"] == end else None
    gaps = spans[:-1] if stop else spans
    st = _span(stop, None) if stop else None
    if st:
        h0 = stop[0]
        words = "; ".join(
            f"{a['part']} ({a['detail']}"
            + (f", lead {h0.get(a['part'] + '_lead_h')} h" if h0.get(a['part'] + '_lead_h') else "")
            + ")" for a in st["parts"] if h0[f"{a['part']}_mw"] is None)
        sentence = f"Net demand stops at {h0['target_ts']}: {words}."
    else:
        sentence = "Net demand runs to the end of the outlook."
    last_present = max(h["target_ts"] for h in hours if h["net_demand_mw"] is not None)
    return {"first_ts": first["target_ts"], "last_ts": last_present, "stop": st,
            "gaps": [_span(g, None) for g in gaps], "sentence": sentence}


def caiso_da_net(dam_load: dict, hub: dict) -> dict:
    """{ts: DAM load - the six hub DAM series}, only where all seven exist (N3)."""
    need = {f"{h}:{t}" for h, t in HUB_DAM}
    out = {}
    for ts, load in dam_load.items():
        got = hub.get(ts, {})
        if set(got) >= need:
            out[ts] = load - sum(got[s] for s in need)
    return out


def truth_series(truth_rows: list[dict]) -> dict:
    per: dict = {}
    for r in truth_rows:
        per.setdefault(r["ts"], {})[r["part"]] = float(r["value"])
    return {ts: p["load"] - p["solar"] - p["wind"] for ts, p in per.items()
            if {"load", "solar", "wind"} <= set(p)}


def hub_map(hub_rows: list[dict]) -> dict:
    out: dict = {}
    for r in hub_rows:
        out.setdefault(r["ts"], {})[r["series"]] = float(r["value"])
    return out


def backtest_inits(days: list[date]) -> list[datetime]:
    """The 12Z issuance of D-1 for each target day D (the pairing rule)."""
    return [datetime.combine(d - timedelta(days=1), datetime.min.time(), UTC) + timedelta(hours=12)
            for d in days]


def _nd_metrics(pairs: list[tuple]) -> dict:
    """pairs: (forecast, truth, ts)."""
    e = [f - t for f, t, _ in pairs]
    by_hod: dict = {}
    for f, t, ts in pairs:
        by_hod.setdefault(ts.astimezone(PT).hour, []).append((f - t, t))
    return {
        "mae_mw": round(sum(abs(x) for x in e) / len(e), 3),
        "bias_mw": round(sum(e) / len(e), 3),
        "wape_pct": round(100 * sum(abs(x) for x in e) / sum(abs(t) for _f, t, _ in pairs), 3),
        "by_hour_pt": [{"hour_pt": k, "n": len(v),
                        "mae_mw": round(sum(abs(x) for x, _t in v) / len(v), 3),
                        "bias_mw": round(sum(x for x, _t in v) / len(v), 3)}
                       for k, v in sorted(by_hod.items())],
    }


def backtest(*, days: list[date], dam_load: dict, caiso_net: dict, truth: dict,
             gen_rows: dict, lines: dict) -> dict:
    """Day-ahead scores, ours and CAISO's, by PAIRING_RULE. gen_rows: {part: rows}."""
    inits = dict(zip(days, backtest_inits(days)))
    ours_pairs, caiso_pairs, ours_days, caiso_days, unscored = [], [], [], [], []
    for d in days:
        hrs = day_hours(d)
        init = inits[d]
        stops = []
        if not all(h in dam_load for h in hrs):
            stops.append({"part": "load", "reason": "no_dam_load"})
        parts = {}
        for part in GEN:
            g = _gen_by_ts(gen_rows.get(part, []), lines, init)
            if not g:
                stops.append({"part": part, "reason": "no_12z_issuance",
                              "init_ts": _iso(init)})
                continue
            miss = [(h, g.get(h)) for h in hrs if h not in g or g[h][0] is None]
            if miss:
                h, got = miss[0]
                why = "no_issuance_hour" if got is None else got[1]
                stops.append({"part": part, "reason": why, "init_ts": _iso(init),
                              "first_ts": _iso(h), "lead_h": None if got is None else got[2],
                              "hours_absent": len(miss)})
                continue
            parts[part] = g
        truth_ok = all(h in truth for h in hrs)
        if not truth_ok:
            stops.append({"part": "truth", "reason": "no_truth"})
        if not stops:
            ours_days.append(d)
            ours_pairs.extend((dam_load[h] - parts["solar"][h][0] - parts["wind"][h][0],
                               truth[h], h) for h in hrs)
        else:
            unscored.append({"day": d.isoformat(), "stopped_by": [
                {**s, "detail": ABSENT.get(s["reason"])} for s in stops]})
        if truth_ok and all(h in caiso_net for h in hrs):
            caiso_days.append(d)
            caiso_pairs.extend((caiso_net[h], truth[h], h) for h in hrs)

    def _score(pairs, sdays):
        base = {"window_start": days[0].isoformat(), "window_end": days[-1].isoformat(),
                "n_days": len(sdays), "n_hours": len(pairs), "min_days": MIN_SCORED_DAYS}
        if len(sdays) < MIN_SCORED_DAYS:
            return {"status": "not_yet_scored", "text": NOT_YET_SCORED, **base}
        return {"status": "scored", **base, **_nd_metrics(pairs)}

    return {"ours": _score(ours_pairs, ours_days),
            "caiso_da": {**_score(caiso_pairs, caiso_days), "scope_note": REFERENCE_SCOPE_NOTE},
            "unscored_days": unscored}


def build_net_demand(*, area: str, now: datetime, fcst_rows: list[dict],
                     hub_rows: list[dict], truth_rows: list[dict],
                     newest: dict, gen_rows: dict, bt_gen_rows: dict,
                     line_rows: list[dict]) -> dict:
    """newest: {part: init_ts | None}; gen_rows / bt_gen_rows: {part: rows}."""
    w = windows(now)
    products = split_products(fcst_rows)
    lines = {int(l["calibration_id"]): l for l in line_rows}
    _hours, days = build_hours_and_days(products, {}, w["today"])
    load = {}
    for c in choose_days(products, w["today"]):
        for h, v, issued in c["hours"]:
            load[h] = (v, c["product"], issued)
    dam = {h: v for h, (v, _i) in products["DAM"].items()}
    hub = hub_map(hub_rows)
    cnet = caiso_da_net(dam, hub)
    truth = truth_series(truth_rows)
    gen = {p: (_gen_by_ts(gen_rows.get(p, []), lines, newest.get(p)) if newest.get(p) else {})
           for p in GEN}

    hours = []
    t = w["today_lo"]
    while t < w["outlook_hi"]:
        row = {"target_ts": _iso(t)}
        lv = load.get(t)
        row.update({"load_mw": None if lv is None else round(lv[0], 3),
                    "load_product": None if lv is None else lv[1],
                    "load_issued_at": None if lv is None else _iso(lv[2]),
                    "load_absent_reason": None if lv else "no_load_forecast"})
        for p in GEN:
            got = gen[p].get(t)
            mw, why, lead = (None, "no_issuance_hour", None) if got is None else got
            row.update({f"{p}_mw": None if mw is None else round(mw, 3),
                        f"{p}_lead_h": lead, f"{p}_absent_reason": why if mw is None else None})
        absent = [{"part": p, "reason": row[f"{p}_absent_reason"]}
                  for p in ("load", "solar", "wind") if row[f"{p}_mw"] is None]
        row["net_demand_mw"] = (None if absent else
                                round(row["load_mw"] - row["solar_mw"] - row["wind_mw"], 3))
        row["absent_part"] = absent or None
        row["caiso_da_net_demand_mw"] = None if t not in cnet else round(cnet[t], 3)
        row["truth_mw"] = round(truth[t], 3) if (t in truth and t < now) else None
        hours.append(row)
        t += timedelta(hours=1)

    stops = {p: _stop(hours, p) for p in ("load", "solar", "wind")}
    nd_stop = _nd_stop(hours)

    bt = backtest(days=w["score_days"], dam_load=dam, caiso_net=cnet, truth=truth,
                  gen_rows=bt_gen_rows, lines=lines)
    body = {
        "label": NET_DEMAND_LABEL,
        "area": area,
        "load_area": SYSTEM,
        "unit": "MW",
        "today": w["today"].isoformat(),
        "tz": "America/Los_Angeles",
        "definition": ("net demand = CAISO's load forecast for CA ISO-TAC - CISO "
                       "calibrated weather-implied solar - CISO calibrated weather-implied wind"),
        "truth_definition": "CAISO actual load (caiso_load_hourly) - fuel-mix solar - fuel-mix wind",
        "reference_definition": ("CAISO's day-ahead net demand = DAM load - DAM solar and wind "
                                 "of NP15, ZP26 and SP15 (caiso_renewables_fcst_dam)"),
        "issuances": {p: {"tech": GEN[p][0], "model": GEN[p][1], "init_ts": _iso(newest.get(p))}
                      for p in GEN},
        "hours": hours,
        "days": days,
        "stops": stops,
        "net_demand_stop": nd_stop,
        "scores": {"ours": bt["ours"], "caiso_da": bt["caiso_da"]},
        "unscored_days": bt["unscored_days"],
        "pairing_rule": PAIRING_RULE,
        "reference_scope_note": REFERENCE_SCOPE_NOTE,
        "attributions": [ATTRIBUTION_CAISO, WIND_ATTRIBUTION],
        "issue_sentence": {p: ISSUE_SENTENCE[p] for p in PRODUCTS},
        "absence": None,
    }
    if all(h["net_demand_mw"] is None for h in hours):
        body["absence"] = {"reason": "no_net_demand",
                           "detail": "no hour has all three parts with a scored figure"}
    return body
