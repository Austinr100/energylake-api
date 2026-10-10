"""cpc_outlooks — d091679: CPC's outlooks as the bank states them.

    GET /api/weather/cpc/curves            a place's newest curve, 6-10 and 8-14
    GET /api/weather/cpc/curves/vintages   one product's n newest issuances, oldest first
    GET /api/weather/cpc/places            every place, its verdict per product, season, strength
    GET /api/weather/cpc/outlooks          the banked features of each outlook's newest issuance

READ-ONLY. The SQL and the shaping are here; main.py holds the routes, the
memos and the statement timeouts (D-09-25-75), as for dd_board.py.

What the bank holds (first read on Neon 2026-10-09 ~22Z; the backfill was
still writing older issuances; docs/handback_2026_10_09_cpc_outlooks_api.md):

  * cpc_outlook_curves (pantry 286, d091654): 238,000 rows, 1,000 issuances
    per product (2024-01-04 .. 2026-10-08), 17 places (15 stations, pnw at
    both weightings). Per place and issuance one window row (day_index NULL)
    and 5 (610temp) or 7 (814temp) day rows. member_odds, season, strength,
    the history years, empty_class, notes and the provenance never differ
    across one (issuance, place) (measured over all 34,000).
  * cpc_curve_verdicts: one backtest_version (cpcv_2026-10-03_d0a93d5811),
    720 cells (2 products x 30 places x 4 seasons x 3 strengths), 396
    drawable. Not drawable: 312 cells at 13 places under 30 base years, 12 at
    KLAX and KSFO in JJA (the season does not beat equal odds).
  * v_cpc_curves_drawable: curve rows whose cell is drawable in the newest
    backtest_version of their method_hash, with the label and the band claim.
  * cpc_outlook_features: 610/814 temp and prcp 2026-07-29 .. 10-08, weeks
    3-4 temp and prcp 2022-01-07 .. 2026-10-02. No seasonal, no monthly.

WHERE THIS READS, AND THE ONE DEPARTURE FROM PANTRY'S CONTRACT. Pantry's
comment on v_cpc_curves_drawable says nothing downstream reads
cpc_outlook_curves or cpc_curve_verdicts directly. Every curve VALUE served
here (percentiles, equal-odds columns, label, band) comes from the view and
nowhere else, so nothing undrawable is ever drawn. But the brief asks for the
verdict of every place (drawable or not) and its reason, and for reading,
history and notes verbatim; the view carries none of those. So two narrow
reads go past it, named in every payload's `sources`:
  - cpc_curve_verdicts, the version in force only: the newest scored_at per
    method_hash, the view's own rule (its `newest` CTE), and
  - cpc_outlook_curves WINDOW rows (day_index NULL), identity and provenance
    columns only: never a percentile, an equal-odds value or member_odds.
The view pantry would add to end both is in the handback (§8).

ONE METHOD VERSION (d091691). Pantry 298 lets cpc_curves_v2 rows and verdicts
stand beside v1's in the same two tables, and the view returns the newest
verdict per method_hash, so it serves both vintages. Every statement here that
names cpc_outlook_curves, cpc_curve_verdicts or v_cpc_curves_drawable filters
each reference on CPC_METHOD_VERSION, so a route never mixes two versions. The
flip to v2 is this constant (docs/handback_2026_10_10_cpc_api_method_pin.md).

Nothing here decides whether a curve may be drawn: a curve is the view's rows
or nothing, and `drawable` is the verdict's own column. `reasons` names which
term of pantry's CHECK ccv_drawable_ck is false for a cell the bank already
says is not drawable; it never makes a cell drawable or not.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Iterable, Optional

# ── Vocabularies (the bank's) ───────────────────────────────────────────────

# cpc_outlook_curves / cpc_curve_verdicts CHECK coc_domain_ck / ccv_domain_ck.
CURVE_PRODUCTS = ("610temp", "814temp")
PLACE_KINDS = ("station", "region")
WEIGHTINGS = ("population", "load_share_365d")   # degree_day_region_weights
SEASONS = ("DJF", "MAM", "JJA", "SON")            # ccv_domain_ck
STRENGTHS = ("weak", "moderate", "strong")        # ccv_domain_ck
DEFAULT_WEIGHTING = "population"                  # the degree-day board's default

VINTAGES_DEFAULT_N = 7
VINTAGES_MAX_N = 14

STATION_RE = re.compile(r"^[A-Z0-9]{11}$")        # a GHCN station id
REGION_RE = re.compile(r"^[a-z0-9_]{1,64}$")

# Pantry's CHECK, quoted (migration 286). The API reads `drawable`; this is
# what the column means, for the page and for `reasons`.
DRAWABLE_RULE = ("drawable = (verdict = 'beats' AND history_years_in_base >= 30 "
                 "AND band_claim IS NOT NULL): cpc_curve_verdicts CHECK "
                 "ccv_drawable_ck, migration 286, D-09-25-172 clauses 3, 5 and 4. "
                 "verdict is the product x place x season cell's skill test over "
                 "equal-odds history; band_claim is judged per strength of CPC's odds.")

# ── Reads ───────────────────────────────────────────────────────────────────

# d091691: the one method version every read of the three relations names. A
# literal in the SQL (not a bind parameter), so the planner sees it in every plan.
CPC_METHOD_VERSION = "cpc_curves_v1"
assert re.fullmatch(r"cpc_curves_v\d+", CPC_METHOD_VERSION)
_PIN = f"'{CPC_METHOD_VERSION}'"

# One read per (product, place): the product's n newest written issuances
# (bank-wide, so an unwritten place never walks the table: CLAUDE.md, the
# trap), each with the place's window row, the verdict in force for its cell
# and the view's rows. One statement, so the verdict and the view are read in
# one snapshot. The view is the only source of a value. The view is a LATERAL
# per issuance, fenced by OFFSET 0 so the planner cannot flatten it back into
# a hash join: its issued_date then reaches uniq_coc_row as an equality per
# loop. Unfenced, it walked all 12,104 rows of the place (112 ms warm, plan
# C00b in docs/receipts/cpc-outlooks-d091679/plans_raw.txt). Every reference
# is pinned to CPC_METHOD_VERSION (d091691): the issuances too, so a newer
# issuance of another version is not this read's newest.
CURVES_SQL = f"""
    WITH iss AS (
        SELECT DISTINCT issued_date
          FROM cpc_outlook_curves c
         WHERE c.product = %(product)s AND c.method_version = {_PIN}
         ORDER BY issued_date DESC
         LIMIT %(n)s
    )
    SELECT i.issued_date AS issuance,
           w.valid_start AS w_valid_start, w.valid_end AS w_valid_end,
           w.season AS w_season, w.strength AS w_strength, w.reading AS w_reading,
           w.history AS w_history, w.notes AS w_notes,
           w.method_version AS w_method_version, w.method_hash AS w_method_hash,
           w.writer_version AS w_writer_version, w.source_r2_key AS w_source_r2_key,
           w.source_format_epoch AS w_source_format_epoch, w.written_at AS w_written_at,
           vv.backtest_version AS v_backtest_version, vv.scored_at AS v_scored_at,
           vv.truth_frontier AS v_truth_frontier, vv.method_version AS v_method_version,
           vv.method_hash AS v_method_hash, vv.rules_version AS v_rules_version,
           vv.rules_hash AS v_rules_hash, vv.min_n_eff AS v_min_n_eff,
           vv.n AS v_n, vv.n_eff AS v_n_eff, vv.skill AS v_skill, vv.t AS v_t,
           vv.strength_verdict AS v_strength_verdict,
           vv.share_p5_p95 AS v_share_p5_p95, vv.share_p25_p75 AS v_share_p25_p75,
           vv.season_n AS v_season_n, vv.season_n_eff AS v_season_n_eff,
           vv.season_skill AS v_season_skill, vv.season_t AS v_season_t,
           vv.verdict AS v_verdict, vv.history_years_in_base AS v_history_years_in_base,
           vv.band_basis AS v_band_basis, vv.band_basis_n AS v_band_basis_n,
           vv.band_basis_n_eff AS v_band_basis_n_eff, vv.band_share AS v_band_share,
           vv.band_claim AS v_band_claim, vv.band_sentence AS v_band_sentence,
           vv.drawable AS v_drawable,
           d.day_index, d.target_date,
           d.tavg_p05, d.tavg_p25, d.tavg_p50, d.tavg_p75, d.tavg_p95,
           d.hdd_p05, d.hdd_p25, d.hdd_p50, d.hdd_p75, d.hdd_p95,
           d.cdd_p05, d.cdd_p25, d.cdd_p50, d.cdd_p75, d.cdd_p95,
           d.eq_tavg_p05, d.eq_tavg_p50, d.eq_tavg_p95,
           d.eq_hdd_p05, d.eq_hdd_p50, d.eq_hdd_p95,
           d.eq_cdd_p05, d.eq_cdd_p50, d.eq_cdd_p95,
           d.n_history_years, d.history_first, d.history_last, d.empty_class,
           d.member_odds, d.season, d.strength, d.method_version, d.method_hash,
           d.source_content_sha256, d.label, d.band_claim, d.band_share,
           d.band_sentence, d.band_basis, d.backtest_version
      FROM iss i
      LEFT JOIN cpc_outlook_curves w
             ON w.product = %(product)s AND w.issued_date = i.issued_date
            AND w.place_kind = %(place_kind)s AND w.place = %(place)s
            AND w.weighting = %(weighting)s AND w.day_index IS NULL
            AND w.method_version = {_PIN}
      LEFT JOIN LATERAL (
            SELECT backtest_version
              FROM cpc_curve_verdicts cv
             WHERE cv.method_hash = w.method_hash AND cv.method_version = {_PIN}
             ORDER BY scored_at DESC, backtest_version DESC
             LIMIT 1) nv ON TRUE
      LEFT JOIN cpc_curve_verdicts vv
             ON vv.backtest_version = nv.backtest_version AND vv.method_hash = w.method_hash
            AND vv.product = w.product AND vv.place_kind = w.place_kind
            AND vv.place = w.place AND vv.weighting = w.weighting
            AND vv.season = w.season AND vv.strength = w.strength
            AND vv.method_version = {_PIN}
      LEFT JOIN LATERAL (
            SELECT *
              FROM v_cpc_curves_drawable dv
             WHERE dv.product = %(product)s AND dv.issued_date = i.issued_date
               AND dv.place_kind = %(place_kind)s AND dv.place = %(place)s
               AND dv.weighting = %(weighting)s
               AND dv.method_version = w.method_version
               AND dv.method_version = {_PIN}
            OFFSET 0) d ON TRUE
     ORDER BY i.issued_date, d.day_index NULLS FIRST
"""

# Every verdict cell of the version in force, per method_hash: the newest
# scored_at, as v_cpc_curves_drawable picks it, one LATERAL ... LIMIT 1 per
# method_hash (d091551), not DISTINCT ON. Each reference pinned (d091691).
PLACES_SQL = f"""
    WITH hashes AS (
        SELECT DISTINCT method_hash FROM cpc_curve_verdicts hv
         WHERE hv.method_version = {_PIN}
    ), inforce AS (
        SELECT h.method_hash, l.backtest_version
          FROM hashes h
          CROSS JOIN LATERAL (
                SELECT backtest_version
                  FROM cpc_curve_verdicts v
                 WHERE v.method_hash = h.method_hash AND v.method_version = {_PIN}
                 ORDER BY scored_at DESC, backtest_version DESC
                 LIMIT 1) l
    )
    SELECT v.backtest_version, v.scored_at, v.truth_frontier, v.method_version,
           v.method_hash, v.rules_version, v.rules_hash, v.min_n_eff, v.product,
           v.place_kind, v.place, v.weighting, v.season, v.strength, v.n, v.n_eff,
           v.skill, v.t, v.strength_verdict, v.share_p5_p95, v.share_p25_p75,
           v.season_n, v.season_n_eff, v.season_skill, v.season_t, v.verdict,
           v.history_years_in_base, v.band_basis, v.band_basis_n, v.band_basis_n_eff,
           v.band_share, v.band_claim, v.band_sentence, v.drawable
      FROM cpc_curve_verdicts v
      JOIN inforce i ON i.method_hash = v.method_hash
                    AND i.backtest_version = v.backtest_version
     WHERE v.method_version = {_PIN}
     ORDER BY v.place_kind, v.place, v.weighting, v.product, v.season, v.strength
"""

# Which places the newest written issuance of each product holds: one
# LATERAL ... LIMIT 1 per product, then that issuance's window rows (the
# unique key's prefix). Identity columns only. Both references pinned
# (d091691): the newest issuance is the newest of CPC_METHOD_VERSION.
PLACES_NEWEST_SQL = f"""
    WITH p AS (
        SELECT unnest(%(products)s::text[]) AS product
    ), iss AS (
        SELECT p.product, l.issued_date
          FROM p
          CROSS JOIN LATERAL (
                SELECT issued_date
                  FROM cpc_outlook_curves c
                 WHERE c.product = p.product AND c.method_version = {_PIN}
                 ORDER BY issued_date DESC
                 LIMIT 1) l
    )
    SELECT c.product, c.issued_date, c.valid_start, c.valid_end, c.place_kind,
           c.place, c.weighting, c.season, c.strength, c.method_version
      FROM iss
      JOIN cpc_outlook_curves c
        ON c.product = iss.product AND c.issued_date = iss.issued_date
       AND c.day_index IS NULL AND c.method_version = {_PIN}
     ORDER BY c.product, c.place_kind, c.place, c.weighting
"""

# The outlooks. pantry's registry (cpc_archive/products.py) codes; the page
# asks by family. seastemp/seasprcp hold both the monthly (layer lead14) and
# the seasonal leads (lead1..lead13) in one zip (migration 289).
OUTLOOK_FAMILIES = {
    "610": {"products": ("610temp", "610prcp"), "layers": "single"},
    "814": {"products": ("814temp", "814prcp"), "layers": "single"},
    "wk34": {"products": ("wk34temp", "wk34prcp"), "layers": "single"},
    "monthly": {"products": ("seastemp", "seasprcp", "monthupd_temp_e1",
                             "monthupd_prcp_e1", "monthupd_temp_e2", "monthupd_prcp_e2"),
                "layers": "lead14_15"},
    "seasonal": {"products": ("seastemp", "seasprcp"), "layers": "lead1_13"},
}
# CPC's titles, as pantry's registry gives them.
CPC_TITLES = {
    "610temp": "6-10 Day Outlook — Temperature",
    "610prcp": "6-10 Day Outlook — Precipitation",
    "814temp": "8-14 Day Outlook — Temperature",
    "814prcp": "8-14 Day Outlook — Precipitation",
    "wk34temp": "Week 3-4 Outlook — Temperature",
    "wk34prcp": "Week 3-4 Outlook — Precipitation",
    "seastemp": "Seasonal Outlook — Temperature (leads 1-13) + Monthly 0.5-month lead (lead14)",
    "seasprcp": "Seasonal Outlook — Precipitation (leads 1-13) + Monthly 0.5-month lead (lead14)",
    "monthupd_temp_e1": "Monthly Outlook Update (end of month) — Temperature, era 1",
    "monthupd_prcp_e1": "Monthly Outlook Update (end of month) — Precipitation, era 1",
    "monthupd_temp_e2": "Monthly Outlook Update (end of month) — Temperature, era 2",
    "monthupd_prcp_e2": "Monthly Outlook Update (end of month) — Precipitation, era 2",
}
OUTLOOK_PRODUCTS = tuple(dict.fromkeys(p for f in OUTLOOK_FAMILIES.values()
                                       for p in f["products"]))

# Every feature of each product's newest parsed issuance: one LATERAL ...
# LIMIT 1 per product on idx_cpc_features_product_issued. A product with no
# row stops at once (product leads the index). Geometry is not read here.
OUTLOOK_FEATURES_SQL = """
    WITH p AS (
        SELECT unnest(%(products)s::text[]) AS product
    ), newest AS (
        SELECT p.product, l.issued_date
          FROM p
          CROSS JOIN LATERAL (
                SELECT issued_date
                  FROM cpc_outlook_features f
                 WHERE f.product = p.product
                 ORDER BY issued_date DESC
                 LIMIT 1) l
    )
    SELECT f.product, f.issued_date, f.layer, f.feature_index, f.category,
           f.category_source, f.prob, f.valid_start, f.valid_end, f.intersects_west,
           f.west_area_fraction, ST_X(f.centroid) AS centroid_lon,
           ST_Y(f.centroid) AS centroid_lat, f.regions, f.r2_key, f.content_sha256,
           f.format_epoch, f.parser_version, f.parsed_at
      FROM newest n
      JOIN cpc_outlook_features f
        ON f.product = n.product AND f.issued_date = n.issued_date
     ORDER BY f.product, f.layer, f.feature_index
"""

# The same features with their West-clipped polygon, as GeoJSON, for one
# family on request (about 0.75 MB a product: never on the polled path).
OUTLOOK_GEOMETRY_SQL = """
    WITH p AS (
        SELECT unnest(%(products)s::text[]) AS product
    ), newest AS (
        SELECT p.product, l.issued_date
          FROM p
          CROSS JOIN LATERAL (
                SELECT issued_date
                  FROM cpc_outlook_features f
                 WHERE f.product = p.product
                 ORDER BY issued_date DESC
                 LIMIT 1) l
    )
    SELECT f.product, f.issued_date, f.layer, f.feature_index,
           ST_AsGeoJSON(f.west_geom) AS west_geojson
      FROM newest n
      JOIN cpc_outlook_features f
        ON f.product = n.product AND f.issued_date = n.issued_date
     ORDER BY f.product, f.layer, f.feature_index
"""

# The newest banked shapefile per product in the bytes index, so an absence
# can say whether the bytes are banked and not yet parsed, or not banked.
OUTLOOK_VINTAGE_SQL = """
    WITH p AS (
        SELECT unnest(%(products)s::text[]) AS product
    )
    SELECT p.product, l.issued_date, l.valid_start, l.valid_end, l.format_epoch
      FROM p
      LEFT JOIN LATERAL (
            SELECT issued_date, valid_start, valid_end, format_epoch
              FROM cpc_outlook_vintage v
             WHERE v.product = p.product AND v.artifact_format = 'shapefile'
             ORDER BY issued_date DESC
             LIMIT 1) l ON TRUE
     ORDER BY p.product
"""

# ── Shaping helpers ─────────────────────────────────────────────────────────


def _iso(v) -> Optional[str]:
    return v.isoformat() if isinstance(v, (date, datetime)) else v


VALUE_COLS = ("tavg_p05", "tavg_p25", "tavg_p50", "tavg_p75", "tavg_p95",
              "hdd_p05", "hdd_p25", "hdd_p50", "hdd_p75", "hdd_p95",
              "cdd_p05", "cdd_p25", "cdd_p50", "cdd_p75", "cdd_p95",
              "eq_tavg_p05", "eq_tavg_p50", "eq_tavg_p95",
              "eq_hdd_p05", "eq_hdd_p50", "eq_hdd_p95",
              "eq_cdd_p05", "eq_cdd_p50", "eq_cdd_p95")

VERSION_KEYS = ("backtest_version", "scored_at", "truth_frontier", "method_version",
                "method_hash", "rules_version", "rules_hash", "min_n_eff")

CELL_KEYS = ("n", "n_eff", "skill", "t", "strength_verdict", "share_p5_p95",
             "share_p25_p75", "season_n", "season_n_eff", "season_skill", "season_t",
             "verdict", "history_years_in_base", "band_basis", "band_basis_n",
             "band_basis_n_eff", "band_share", "band_claim", "band_sentence")


def reasons(cell: dict) -> list[dict]:
    """Which term of ccv_drawable_ck is false, for a cell the bank says is not
    drawable. Empty when the cell is drawable. Reads `drawable` first and
    never changes it: a cell is not drawable because its column says so."""
    if cell["drawable"]:
        return []
    out = []
    if cell["verdict"] == "does_not_beat":
        out.append({"code": "season_does_not_beat_equal_odds", "clause": 3,
                    "verdict": cell["verdict"], "season_t": cell["season_t"],
                    "season_skill": cell["season_skill"]})
    elif cell["verdict"] == "too_thin":
        out.append({"code": "season_too_thin", "clause": 3, "verdict": cell["verdict"],
                    "season_n_eff": cell["season_n_eff"]})
    if cell["history_years_in_base"] < 30:
        out.append({"code": "under_30_base_years", "clause": 5,
                    "history_years_in_base": cell["history_years_in_base"]})
    if cell["band_claim"] is None:
        out.append({"code": "no_band_claim", "clause": 4, "band_basis": cell["band_basis"]})
    if not out:
        out.append({"code": "unstated", "clause": None})
    return out


def _version(r: dict, prefix: str = "") -> dict:
    return {k: _iso(r[prefix + k]) for k in VERSION_KEYS}


def _cell(r: dict, prefix: str = "") -> dict:
    c = {"season": r[prefix + "season"], "strength": r[prefix + "strength"],
         "drawable": r[prefix + "drawable"],
         **{k: r[prefix + k] for k in CELL_KEYS},
         "backtest_version": r[prefix + "backtest_version"]}
    c["reasons"] = reasons(c)
    return c


def _versions_sorted(vs: dict) -> list[dict]:
    return [vs[k] for k in sorted(vs)]


def _values(r: dict) -> dict:
    return {k: r[k] for k in VALUE_COLS}


# ── /curves and /curves/vintages ────────────────────────────────────────────

def vintage(rows: list[dict], *, product: str) -> dict:
    """CURVES_SQL rows for ONE issuance -> what the bank states for the place.

    The curve is the view's rows or null. The window row (identity, reading,
    history, notes) and the verdict cell ride beside it either way."""
    r0 = rows[0]
    written = r0["w_season"] is not None
    has_verdict = r0["v_backtest_version"] is not None
    view = [r for r in rows if r["day_index"] is not None or r["label"] is not None]

    verdict = None
    if has_verdict:
        verdict = {"season": r0["w_season"], "strength": r0["w_strength"],
                   "drawable": r0["v_drawable"],
                   **{k: r0["v_" + k] for k in CELL_KEYS},
                   "backtest_version": r0["v_backtest_version"]}
        verdict["reasons"] = reasons(verdict)

    curve = None
    if view:
        win = next((r for r in view if r["day_index"] is None), None)
        head = win or view[0]
        curve = {
            "label": head["label"],
            "band_claim": head["band_claim"],
            "band_share": head["band_share"],
            "band_sentence": head["band_sentence"],
            "band_basis": head["band_basis"],
            "backtest_version": head["backtest_version"],
            "season": head["season"],
            "strength": head["strength"],
            "n_history_years": head["n_history_years"],
            "history_first": head["history_first"],
            "history_last": head["history_last"],
            "member_odds": head["member_odds"],
            "method_version": head["method_version"],
            "method_hash": head["method_hash"],
            "source_content_sha256": head["source_content_sha256"],
            "window": None if win is None else {
                "valid_start": _iso(r0["w_valid_start"]), "valid_end": _iso(r0["w_valid_end"]),
                "empty_class": win["empty_class"], **_values(win)},
            "days": [{"day_index": r["day_index"], "target_date": _iso(r["target_date"]),
                      "empty_class": r["empty_class"], **_values(r)}
                     for r in view if r["day_index"] is not None],
        }

    if not written:
        absence = {"reason": "not_written",
                   "detail": f"cpc_outlook_curves holds no {product} curve for this "
                             f"place on the {_iso(r0['issuance'])} issuance"}
    elif curve is not None:
        absence = None
    elif not has_verdict:
        absence = {"reason": "no_verdict",
                   "detail": f"no verdict cell ({r0['w_season']}, {r0['w_strength']}) "
                             "in the backtest_version in force, so the view returns "
                             "no row"}
    else:
        absence = {"reason": "not_drawable",
                   "detail": "the verdict in force says this cell may not be drawn; "
                             "see verdict.reasons",
                   "reasons": verdict["reasons"]}

    return {
        "issued_date": _iso(r0["issuance"]),
        "written": written,
        "valid_start": _iso(r0["w_valid_start"]),
        "valid_end": _iso(r0["w_valid_end"]),
        "season": r0["w_season"],
        "strength": r0["w_strength"],
        "reading": r0["w_reading"],
        "history": r0["w_history"],
        "notes": r0["w_notes"],
        "writer_version": r0["w_writer_version"],
        "source_r2_key": r0["w_source_r2_key"],
        "source_format_epoch": r0["w_source_format_epoch"],
        "written_at": _iso(r0["w_written_at"]),
        "drawable": None if verdict is None else verdict["drawable"],
        "verdict": verdict,
        "curve": curve,
        "absence": absence,
    }


def _by_issuance(rows: Iterable[dict]) -> list[list[dict]]:
    groups: dict = {}
    for r in rows:
        groups.setdefault(r["issuance"], []).append(r)
    return [groups[k] for k in sorted(groups)]


def _versions_of(rows: Iterable[dict]) -> dict:
    vs = {}
    for r in rows:
        if r["v_backtest_version"] is not None:
            vs[r["v_backtest_version"]] = _version(r, "v_")
    return vs


SOURCES = {
    "curve_values": "v_cpc_curves_drawable (the only source of a percentile, an "
                    "equal-odds value, member_odds, the label and the band)",
    "verdicts": "cpc_curve_verdicts, the backtest_version in force: newest scored_at "
                "per method_hash, the view's own rule",
    "window_rows": "cpc_outlook_curves window rows (day_index NULL): identity, season, "
                   "strength, reading, history, notes, writer_version, source_r2_key, "
                   "source_format_epoch, written_at. No value column.",
    "contract": "pantry's comment on v_cpc_curves_drawable says nothing downstream "
                "reads the two tables directly; the verdict and window-row reads above "
                "go past it because the view holds no verdict for an undrawable cell and "
                "no reading, history or notes (d091679 handback, section 8).",
}


def _place(place_kind: str, place: str, weighting: str) -> dict:
    return {"place_kind": place_kind, "place": place, "weighting": weighting}


def place_verdict(place_entry: Optional[dict], product: str) -> Optional[dict]:
    """A place's cells for one product, summed up from /places (its memo):
    how many are drawable, and the reason codes of those that are not. This is
    what says why a place with no curve (under 30 base years: the writer
    writes none) is absent, where no cell can be picked by a curve's season
    and strength."""
    if place_entry is None:
        return None
    pr = next((x for x in place_entry["products"] if x["product"] == product), None)
    if pr is None:
        return None
    cells = [c for s in pr["seasons"] for c in s["cells"]]
    return {
        "drawable_cells": pr["drawable_cells"],
        "cell_count": pr["cell_count"],
        "reason_codes": sorted({r["code"] for c in cells for r in c["reasons"]}),
        "history_years_in_base": sorted({c["history_years_in_base"] for c in cells}),
        "verdicts": sorted({c["verdict"] for c in cells}),
    }


def curves_payload(by_product: dict[str, list[dict]], *, place_kind: str, place: str,
                   weighting: str, place_entry: Optional[dict] = None) -> dict:
    """CURVES_SQL (n=1) per product -> the place's newest curve of each.
    `place_entry` is the place's /places entry, for `place_verdict`."""
    products, versions = [], {}
    for product in CURVE_PRODUCTS:
        rows = by_product.get(product, [])
        versions.update(_versions_of(rows))
        groups = _by_issuance(rows)
        pv = place_verdict(place_entry, product)
        if not groups:
            products.append({"product": product, "cpc_title": CPC_TITLES[product],
                             "newest_written_issued_date": None, "vintage": None,
                             "place_verdict": pv,
                             "absence": {"reason": "no_issuance_written",
                                         "detail": f"cpc_outlook_curves holds no "
                                                   f"{product} issuance"}})
            continue
        v = vintage(groups[-1], product=product)
        absence = v["absence"]
        if absence is not None and absence["reason"] == "not_written" and pv:
            absence = {**absence, "place_reason_codes": pv["reason_codes"]}
        products.append({"product": product, "cpc_title": CPC_TITLES[product],
                         "newest_written_issued_date": v["issued_date"],
                         "vintage": v, "place_verdict": pv, "absence": absence})
    drawn = [p["product"] for p in products if p["vintage"] and p["vintage"]["curve"]]
    return {
        **_place(place_kind, place, weighting),
        "drawable_rule": DRAWABLE_RULE,
        "verdict_versions": _versions_sorted(versions),
        "sources": SOURCES,
        "products_drawn": drawn,
        "products": products,
        "absence": None if drawn else {
            "reason": "no_curve",
            "detail": "neither product's newest issuance has a drawable curve at this "
                      "place; each product's absence says why"},
    }


def vintages_payload(rows: list[dict], *, product: str, n: int, place_kind: str,
                     place: str, weighting: str) -> dict:
    """CURVES_SQL (n) for one product -> its n newest issuances, oldest first."""
    groups = _by_issuance(rows)
    vs = [vintage(g, product=product) for g in groups]
    return {
        **_place(place_kind, place, weighting),
        "product": product,
        "cpc_title": CPC_TITLES[product],
        "n": n,
        "n_cap": VINTAGES_MAX_N,
        "order": "oldest first",
        "drawable_rule": DRAWABLE_RULE,
        "verdict_versions": _versions_sorted(_versions_of(rows)),
        "sources": SOURCES,
        "issued_dates": [v["issued_date"] for v in vs],
        "drawn_count": sum(1 for v in vs if v["curve"] is not None),
        "vintages": vs,
        "absence": None if vs else {
            "reason": "no_issuance_written",
            "detail": f"cpc_outlook_curves holds no {product} issuance"},
    }


# ── /places ─────────────────────────────────────────────────────────────────

def known_places(verdict_rows: Iterable[dict]) -> set[tuple[str, str, str]]:
    return {(r["place_kind"], r["place"], r["weighting"]) for r in verdict_rows}


def places_payload(verdict_rows: list[dict], newest_rows: list[dict]) -> dict:
    """PLACES_SQL + PLACES_NEWEST_SQL -> every place, its cells, its newest curve."""
    versions = {r["backtest_version"]: _version(r) for r in verdict_rows}
    newest_date = {}
    newest = {}
    for r in newest_rows:
        newest_date[r["product"]] = _iso(r["issued_date"])
        newest[(r["product"], r["place_kind"], r["place"], r["weighting"])] = r

    places: dict = {}
    for r in verdict_rows:
        key = (r["place_kind"], r["place"], r["weighting"])
        p = places.setdefault(key, {**_place(*key), "products": {}})
        prod = p["products"].setdefault(r["product"], {"product": r["product"],
                                                       "seasons": {}})
        prod["seasons"].setdefault(r["season"], []).append(_cell(r))

    out = []
    for key in sorted(places, key=lambda k: (PLACE_KINDS.index(k[0])
                                             if k[0] in PLACE_KINDS else 99, k[1], k[2])):
        p = places[key]
        prods = []
        for product in sorted(p["products"], key=lambda x: (
                CURVE_PRODUCTS.index(x) if x in CURVE_PRODUCTS else 99, x)):
            pr = p["products"][product]
            cells = {(c["season"], c["strength"]): c
                     for cs in pr["seasons"].values() for c in cs}
            nw = newest.get((product, *key))
            if nw is not None:
                cell = cells.get((nw["season"], nw["strength"]))
                newest_curve = {
                    "issued_date": _iso(nw["issued_date"]),
                    "valid_start": _iso(nw["valid_start"]),
                    "valid_end": _iso(nw["valid_end"]),
                    "season": nw["season"], "strength": nw["strength"],
                    "method_version": nw["method_version"],
                    "drawable": None if cell is None else cell["drawable"],
                    "reasons": None if cell is None else cell["reasons"]}
                newest_absence = None
            else:
                newest_curve = None
                nd = newest_date.get(product)
                newest_absence = {
                    "reason": "not_written",
                    "detail": (f"the newest written {product} issuance ({nd}) holds no "
                               "curve at this place" if nd else
                               f"cpc_outlook_curves holds no {product} issuance")}
            seasons = [{"season": s,
                        "cells": sorted(pr["seasons"][s], key=lambda c: (
                            STRENGTHS.index(c["strength"]) if c["strength"] in STRENGTHS
                            else 99, c["strength"]))}
                       for s in sorted(pr["seasons"], key=lambda s: (
                           SEASONS.index(s) if s in SEASONS else 99, s))]
            prods.append({
                "product": product,
                "drawable_cells": sum(1 for c in cells.values() if c["drawable"]),
                "cell_count": len(cells),
                "newest_curve": newest_curve,
                "newest_curve_absence": newest_absence,
                "seasons": seasons,
            })
        out.append({**{k: p[k] for k in ("place_kind", "place", "weighting")},
                    "products": prods})

    return {
        "drawable_rule": DRAWABLE_RULE,
        "verdict_versions": _versions_sorted(versions),
        "sources": SOURCES,
        "newest_written_issued_date": {p: newest_date.get(p) for p in CURVE_PRODUCTS},
        "place_count": len(out),
        "cell_count": len(verdict_rows),
        "drawable_count": sum(1 for r in verdict_rows if r["drawable"]),
        "places": out,
        "absence": None if out else {
            "reason": "no_verdicts",
            "detail": "cpc_curve_verdicts holds no cell"},
    }


# ── /outlooks ───────────────────────────────────────────────────────────────

_LEAD_RE = re.compile(r"^lead(\d+)_")


def _layer_in(family: str, layer: str) -> bool:
    rule = OUTLOOK_FAMILIES[family]["layers"]
    if rule == "single":
        return layer == ""
    m = _LEAD_RE.match(layer)
    lead = int(m.group(1)) if m else None
    if rule == "lead14_15":
        return lead in (14, 15)
    return lead is not None and 1 <= lead <= 13


def _feature(r: dict, geometry: Optional[dict]) -> dict:
    f = {
        "layer": r["layer"],
        "feature_index": r["feature_index"],
        "category": r["category"],
        "category_source": r["category_source"],
        "prob": r["prob"],
        "valid_start": _iso(r["valid_start"]),
        "valid_end": _iso(r["valid_end"]),
        "intersects_west": r["intersects_west"],
        "west_area_fraction": r["west_area_fraction"],
        "centroid": (None if r["centroid_lon"] is None
                     else [r["centroid_lon"], r["centroid_lat"]]),
        "regions": r["regions"],
    }
    if geometry is not None:
        f["west_geojson"] = geometry.get((r["product"], r["layer"], r["feature_index"]))
    return f


def outlooks_payload(feature_rows: list[dict], vintage_rows: list[dict], *,
                     families: Iterable[str], geometry_rows: Optional[list[dict]] = None
                     ) -> dict:
    """OUTLOOK_FEATURES_SQL + OUTLOOK_VINTAGE_SQL (+ geometry) -> per family,
    per product, the newest parsed issuance's features, or a stated absence."""
    geo = None
    if geometry_rows is not None:
        geo = {(g["product"], g["layer"], g["feature_index"]): g["west_geojson"]
               for g in geometry_rows}
    by_product: dict = {}
    for r in feature_rows:
        by_product.setdefault(r["product"], []).append(r)
    banked = {r["product"]: r for r in vintage_rows}

    fams = []
    for fam in families:
        prods = []
        for product in OUTLOOK_FAMILIES[fam]["products"]:
            rows = [r for r in by_product.get(product, []) if _layer_in(fam, r["layer"])]
            vt = banked.get(product) or {}
            bytes_newest = _iso(vt.get("issued_date"))
            if rows:
                r0 = rows[0]
                layers = sorted({r["layer"] for r in rows},
                                key=lambda l: (int(_LEAD_RE.match(l).group(1))
                                               if _LEAD_RE.match(l) else 0, l))
                prods.append({
                    "product": product, "cpc_title": CPC_TITLES[product],
                    "issued_date": _iso(r0["issued_date"]),
                    "layers": layers,
                    "content_sha256": sorted({r["content_sha256"] for r in rows}),
                    "r2_key": sorted({r["r2_key"] for r in rows}),
                    "format_epoch": sorted({r["format_epoch"] for r in rows}),
                    "parser_version": sorted({r["parser_version"] for r in rows}),
                    "parsed_at": max(_iso(r["parsed_at"]) for r in rows),
                    "bytes_newest_issued_date": bytes_newest,
                    "feature_count": len(rows),
                    "features": [_feature(r, geo) for r in rows],
                    "absence": None,
                })
                continue
            if by_product.get(product):
                reason, detail = ("no_layer_in_family",
                                  f"the newest parsed {product} issuance holds no "
                                  f"{fam} layer")
            elif bytes_newest:
                reason, detail = ("not_parsed",
                                  f"cpc_outlook_vintage banks {product} bytes (newest "
                                  f"{bytes_newest}) but cpc_outlook_features holds no "
                                  "row for it")
            else:
                reason, detail = ("not_banked",
                                  f"neither cpc_outlook_vintage nor cpc_outlook_features "
                                  f"holds any {product} issuance")
            prods.append({"product": product, "cpc_title": CPC_TITLES[product],
                          "issued_date": None, "layers": [], "feature_count": 0,
                          "features": [], "bytes_newest_issued_date": bytes_newest,
                          "absence": {"reason": reason, "detail": detail}})
        fams.append({
            "family": fam,
            "products": prods,
            "absence": None if any(p["absence"] is None for p in prods) else {
                "reason": "nothing_banked" if all(p["absence"]["reason"] == "not_banked"
                                                  for p in prods) else "no_features",
                "detail": f"no {fam} product has a parsed feature"},
        })
    return {
        "families": fams,
        "geometry": geometry_rows is not None,
        "geometry_note": ("west_geojson is cpc_outlook_features.west_geom as stored "
                          "(clipped to the West window, SRID 4269), as GeoJSON; null "
                          "where the polygon does not reach the West"),
        "sources": {"tables": ["cpc_outlook_features", "cpc_outlook_vintage"]},
    }
