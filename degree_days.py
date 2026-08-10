"""
degree_days — the pure shaping layer behind /api/weather/dd/* (Phase B, 2026-08-10).

Sidecar to main.py in the house idiom (see almanac.py, structures.py,
paper_desk.py): every function here is a PURE composition over rows already
fetched. No DB handle, no clock, no FastAPI. main.py owns the SQL, the cache and
the routes; this module owns what a row MEANS on the way out.

═══════════════════════════════════════════════════════════════════════════
THE ONE RULE THIS MODULE EXISTS TO ENFORCE
═══════════════════════════════════════════════════════════════════════════

**A NULL total is not a zero, and it is never filled.**

Migration 164's `dd_region_mtd` / `dd_region_std` already answer completeness
for themselves: `complete`, `days_complete`, `days_in_window`,
`normals_days_complete`, `normals_complete`. When a window is short a day, the
view returns `hdd`/`cdd` as NULL *on purpose* — that is the product working, not
a gap to patch. This layer therefore:

  * passes a NULL straight through as JSON `null`, never `0`, never `0.0`;
  * NEVER re-derives a cumulative by summing daily rows (that is the 402.75
    defect, and doing it here would put it one layer deeper than any post-check
    could reach — see the lane spec's R3);
  * attaches an `absence` object beside every null total, carrying the view's
    OWN counts, so a client can render "89 of 92 days" without a second call
    and without inventing a completeness rule of its own.

`absence` is descriptive, not computed: every number in it is copied from the
view. The only judgment this module makes is WHICH of the view's own booleans
explains the null, and that judgment is a lookup, not arithmetic.
"""

from __future__ import annotations

from datetime import date as _date
from datetime import datetime as _datetime
from decimal import Decimal
from typing import Any, Iterable, Optional


# ---------------------------------------------------------------------------
# Scalars
# ---------------------------------------------------------------------------

def _f(v: Any) -> Optional[float]:
    """Numeric -> float, and NULL -> None. The `or 0` that is not here.

    main.py casts to float8 in SQL, so this is normally a passthrough; it stays
    tolerant of Decimal so tests can hand in raw view rows. The point of the
    function is the branch it REFUSES to have: there is no `or 0.0`, no
    `float(v or 0)`, no default. A missing total leaves as None and is
    serialized as JSON null.
    """
    if v is None:
        return None
    if isinstance(v, Decimal):
        return float(v)
    return float(v)


def _i(v: Any) -> Optional[int]:
    return None if v is None else int(v)


def _iso(v: Any) -> Optional[str]:
    """date/datetime -> ISO-8601 string, None -> None."""
    if v is None:
        return None
    if isinstance(v, (_date, _datetime)):
        return v.isoformat()
    return str(v)


def _stations(v: Any) -> list[str]:
    """A text[] column -> a list. SQL NULL means "none missing", i.e. []."""
    return list(v) if v else []


# ---------------------------------------------------------------------------
# Absence — the shape a NULL wears on the way out
# ---------------------------------------------------------------------------
#
# Four reasons a cumulative total can be null, in the order they are checked.
# Each is decided by a boolean or a NULL the VIEW already published; none is
# computed here.
#
#   no_row          the view returned no row at all for this (region, as_of) —
#                   the requested date is outside the composite's span.
#   no_season       STD only: `season` is NULL, so the captain's ruled anchors
#                   place this date in a shoulder month (Apr, Oct). There is no
#                   season to date, which is different from an incomplete one.
#   incomplete_obs  `complete` is false: days_complete < days_in_window. The
#                   observation basis is short. THIS is the "89 of 92 days" case.
#   incomplete_norm `normals_complete` is false: the departure has no normal to
#                   sit against, even where the observed total is whole.
#
# `message` is a rendering convenience, not a contract the client must parse —
# the counts beside it are the machine-readable form.

ABSENCE_NO_ROW = "no_row"
ABSENCE_NO_SEASON = "no_season"
ABSENCE_INCOMPLETE_OBS = "incomplete_obs"
ABSENCE_INCOMPLETE_NORM = "incomplete_norm"


def _absence(reason: str, message: str, **counts: Any) -> dict:
    out = {"reason": reason, "message": message}
    out.update(counts)
    return out


def _window_absence(row: dict, *, kind: str) -> Optional[dict]:
    """The stated reason a cumulative window's totals are null — or None.

    `kind` is "mtd" or "std"; it only selects the season branch and the wording.
    Every count returned is lifted verbatim from the view row.
    """
    if kind == "std" and row.get("season") is None:
        return _absence(
            ABSENCE_NO_SEASON,
            "no ruled season anchor covers this date (shoulder month)",
            days_complete=None, days_in_window=None,
        )

    dc, diw = _i(row.get("days_complete")), _i(row.get("days_in_window"))
    if row.get("complete") is False:
        return _absence(
            ABSENCE_INCOMPLETE_OBS,
            f"{dc} of {diw} days" if dc is not None and diw is not None
            else "observation basis incomplete",
            days_complete=dc, days_in_window=diw,
        )
    if row.get("normals_complete") is False:
        ndc = _i(row.get("normals_days_complete"))
        # The "X of Y" phrasing is only used when the two counts are actually
        # commensurate. They are on dd_region_mtd (6 and 6 on 2026-08-06); they
        # are NOT on dd_region_std, which publishes normals_days_complete=219
        # against days_in_window=98 for every region on the same date (measured,
        # production 2026-08-10). Rather than render "normals cover 219 of 98
        # days", the message stays plain and the raw counts ride as fields — a
        # number whose denominator we cannot vouch for is not made truer by being
        # formatted into a sentence.
        commensurate = (ndc is not None and diw is not None and ndc <= diw)
        return _absence(
            ABSENCE_INCOMPLETE_NORM,
            f"normals cover {ndc} of {diw} days" if commensurate
            else "normals basis incomplete for this window",
            days_complete=dc, days_in_window=diw, normals_days_complete=ndc,
        )
    return None


# ---------------------------------------------------------------------------
# /api/weather/dd/regions — the ratified vectors
# ---------------------------------------------------------------------------

def regions_payload(rows: Iterable[dict]) -> dict:
    """dd_region_weights_active rows -> the five vectors with their members.

    `weight_sum` is reported rather than asserted. All five ratified vectors sum
    to 1.00 today; if one ever does not, the number says so instead of a
    normalization silently hiding it.
    """
    by_region: dict[str, dict] = {}
    for r in rows:
        reg = r["region"]
        block = by_region.setdefault(reg, {
            "region": reg,
            "version": r.get("version"),
            "vintage": _iso(r.get("vintage")),
            "note": r.get("note"),
            "member_stations": 0,
            "weight_sum": 0.0,
            "members": [],
        })
        w = _f(r.get("weight")) or 0.0
        block["members"].append({"station_id": r["station_id"], "weight": _f(r.get("weight"))})
        block["member_stations"] += 1
        block["weight_sum"] = round(block["weight_sum"] + w, 6)

    for block in by_region.values():
        block["members"].sort(key=lambda m: (-(m["weight"] or 0.0), m["station_id"]))

    regions = [by_region[k] for k in sorted(by_region)]
    versions = sorted({b["version"] for b in regions if b["version"]})
    return {
        # One vector version across the fleet is the normal case; the list form
        # is what makes a split visible instead of arbitrary.
        "vector_version": versions[0] if len(versions) == 1 else None,
        "vector_versions": versions,
        "region_count": len(regions),
        "regions": regions,
    }


# ---------------------------------------------------------------------------
# /api/weather/dd/daily — departures, one row per (region, obs_date)
# ---------------------------------------------------------------------------

def daily_row(r: dict) -> dict:
    """One dd_region_daily_vs_normal row, shaped.

    Every completeness field the view publishes rides along on EVERY row —
    `basis_complete`, `normals_complete`, `members_present`/`member_stations`,
    `missing_stations`, `normals_missing_stations`, `min_sample_count`. A client
    that wants to say WHY a cell is thin never needs a second call.
    """
    return {
        "region": r["region"],
        "obs_date": _iso(r["obs_date"]),
        "version": r.get("version"),
        "basis_complete": r.get("basis_complete"),
        "normals_complete": r.get("normals_complete"),
        "tavg_f": _f(r.get("tavg_f")),
        "hdd": _f(r.get("hdd")),
        "cdd": _f(r.get("cdd")),
        "tavg_norm_f": _f(r.get("tavg_norm_f")),
        "hdd_norm": _f(r.get("hdd_norm")),
        "cdd_norm": _f(r.get("cdd_norm")),
        "tavg_departure_f": _f(r.get("tavg_departure_f")),
        "hdd_departure": _f(r.get("hdd_departure")),
        "cdd_departure": _f(r.get("cdd_departure")),
        "member_stations": _i(r.get("member_stations")),
        "members_present": _i(r.get("members_present")),
        "missing_stations": _stations(r.get("missing_stations")),
        "normals_missing_stations": _stations(r.get("normals_missing_stations")),
        "min_sample_count": _i(r.get("min_sample_count")),
        "normals_window_label": r.get("window_label"),
    }


def daily_payload(rows: Iterable[dict], *, start, end, regions_requested) -> dict:
    shaped = [daily_row(r) for r in rows]
    present = sorted({s["region"] for s in shaped})
    versions = sorted({s["version"] for s in shaped if s["version"]})
    return {
        "start": _iso(start),
        "end": _iso(end),
        "regions_requested": regions_requested,
        "regions_present": present,
        "vector_version": versions[0] if len(versions) == 1 else None,
        "row_count": len(shaped),
        # The right edge of the ledger, measured — NOT today. GHCND actuals lag
        # NOAA's publication schedule by 2-4 days; the surface labels this seam.
        "obs_through": max((s["obs_date"] for s in shaped), default=None),
        "rows": shaped,
    }


# ---------------------------------------------------------------------------
# /api/weather/dd/cumulative — MTD + STD, the slow pair
# ---------------------------------------------------------------------------

def _last_year(r: dict, *, kind: str) -> dict:
    """The view's own last-year comparison block, passed through.

    `hdd_vs_ly` / `cdd_vs_ly` are the VIEW's subtraction, not ours. Where the
    view left them NULL (because either side of the comparison is incomplete)
    they stay NULL — recomputing them from the two totals we happen to be
    holding is exactly the kind of client-side fill this lane refuses.
    """
    block = {
        "obs_date": _iso(r.get("ly_obs_date")),
        "days_in_window": _i(r.get("ly_days_in_window")),
        "days_complete": _i(r.get("ly_days_complete")),
        "complete": r.get("ly_complete"),
        "hdd": _f(r.get("ly_hdd")),
        "cdd": _f(r.get("ly_cdd")),
        "hdd_vs_ly": _f(r.get("hdd_vs_ly")),
        "cdd_vs_ly": _f(r.get("cdd_vs_ly")),
    }
    if kind == "std":
        block["season_start"] = _iso(r.get("ly_season_start"))
    return block


def cumulative_block(r: Optional[dict], *, kind: str) -> dict:
    """One MTD or STD window for one region.

    A missing row is NOT an error and NOT an empty dict: it is a block whose
    totals are null with `absence.reason = "no_row"` — the requested `as_of`
    lies outside the composite's span. Same shape either way, so the client has
    one branch, not two.
    """
    if r is None:
        return {
            "present": False,
            "window_start": None, "window_end": None,
            "window_label": None, "normals_window_label": None,
            "days_in_window": None, "days_complete": None,
            "normals_days_complete": None,
            "complete": None, "normals_complete": None,
            "hdd": None, "cdd": None,
            "hdd_norm": None, "cdd_norm": None,
            "hdd_departure": None, "cdd_departure": None,
            **({"season": None, "season_start": None} if kind == "std" else {}),
            "last_year": None,
            "absence": _absence(
                ABSENCE_NO_ROW,
                "no composite row for this region on this date",
                days_complete=None, days_in_window=None,
            ),
        }

    block = {
        "present": True,
        # MTD's window opens on `window_start`; STD's opens on `season_start`.
        "window_start": _iso(r.get("window_start") or r.get("season_start")),
        "window_end": _iso(r.get("obs_date")),
        # `window_label` on these views labels the NORMALS baseline ("2011-2025"),
        # not the accumulation window — named explicitly so no one reads it as
        # the date range they just asked for.
        "window_label": r.get("window_label"),
        "normals_window_label": r.get("window_label"),
        "days_in_window": _i(r.get("days_in_window")),
        "days_complete": _i(r.get("days_complete")),
        "normals_days_complete": _i(r.get("normals_days_complete")),
        "complete": r.get("complete"),
        "normals_complete": r.get("normals_complete"),
        # ── The totals. NULL in, null out. No coalesce lives on this path. ──
        "hdd": _f(r.get("hdd")),
        "cdd": _f(r.get("cdd")),
        "hdd_norm": _f(r.get("hdd_norm")),
        "cdd_norm": _f(r.get("cdd_norm")),
        "hdd_departure": _f(r.get("hdd_departure")),
        "cdd_departure": _f(r.get("cdd_departure")),
        "last_year": _last_year(r, kind=kind),
    }
    if kind == "std":
        block["season"] = r.get("season")
        block["season_start"] = _iso(r.get("season_start"))
    block["absence"] = _window_absence(r, kind=kind)
    return block


def cumulative_payload(
    mtd_rows: Iterable[dict],
    std_rows: Iterable[dict],
    *,
    as_of,
    as_of_source: str,
    obs_through=None,
) -> dict:
    """The whole board: every region the views returned, MTD and STD side by side.

    Board-shaped on purpose. Migration 164's views cost the same whether one
    region is asked for or five (see main.py's cost note), so the API builds all
    five once and filters the CACHED board — never the query.
    """
    mtd_by = {r["region"]: r for r in mtd_rows}
    std_by = {r["region"]: r for r in std_rows}
    regions = sorted(set(mtd_by) | set(std_by))

    versions = sorted({
        r.get("version") for r in list(mtd_by.values()) + list(std_by.values())
        if r.get("version")
    })

    return {
        "as_of": _iso(as_of),
        # "requested" when the caller pinned a date; "latest_obs" when the API
        # resolved it to the composite's right edge. A client that renders a date
        # it did not ask for deserves to know the API chose it.
        "as_of_source": as_of_source,
        "obs_through": _iso(obs_through),
        "vector_version": versions[0] if len(versions) == 1 else None,
        "region_count": len(regions),
        "regions": [
            {
                "region": reg,
                "mtd": cumulative_block(mtd_by.get(reg), kind="mtd"),
                "std": cumulative_block(std_by.get(reg), kind="std"),
            }
            for reg in regions
        ],
    }


def filter_board(board: dict, region: Optional[str]) -> dict:
    """Narrow a cached board to one region — in memory, never in SQL.

    An unknown region yields `regions: []` with `region_count: 0` rather than an
    error: the board states which regions exist, and the caller's own filter
    coming back empty is a legible answer. main.py validates the vocabulary
    before this is reached, so in practice this is the identity function.
    """
    if region is None:
        return board
    out = dict(board)
    out["regions"] = [r for r in board["regions"] if r["region"] == region]
    out["region_count"] = len(out["regions"])
    out["region_requested"] = region
    return out


# ---------------------------------------------------------------------------
# /api/weather/dd/socalgas-grade — the arbiter
# ---------------------------------------------------------------------------

def grade_row(r: dict) -> dict:
    delta = _f(r.get("delta_f"))
    return {
        "obs_date": _iso(r["obs_date"]),
        "version": r.get("version"),
        # delta_f = ours - theirs. Positive means OUR composite reads warmer.
        "el_composite_temp_f": _f(r.get("el_composite_temp_f")),
        "scg_composite_temp_f": _f(r.get("scg_composite_temp_f")),
        "delta_f": delta,
        "abs_delta_f": None if delta is None else abs(delta),
        "basis_complete": r.get("basis_complete"),
        "arbiter_present": r.get("arbiter_present"),
        "member_stations": _i(r.get("member_stations")),
        "members_present": _i(r.get("members_present")),
        "missing_stations": _stations(r.get("missing_stations")),
    }


def grade_summary(r: Optional[dict]) -> dict:
    """The graded-days summary, computed in SQL over `arbiter_present` rows only.

    A day SoCalGas never published is not a zero-delta day; it is an ungraded
    day. `n_days` counts every row in scope, `n_graded` only the ones with an
    arbiter — the difference is the honest denominator.
    """
    if r is None:
        return {"n_days": 0, "n_graded": 0, "mean_delta_f": None,
                "sd_delta_f": None, "mean_abs_delta_f": None,
                "min_delta_f": None, "max_delta_f": None,
                "first_graded": None, "last_graded": None}
    return {
        "n_days": _i(r.get("n_days")) or 0,
        "n_graded": _i(r.get("n_graded")) or 0,
        "mean_delta_f": _f(r.get("mean_delta_f")),
        # NULL for n_graded < 2 — a sample standard deviation of one point does
        # not exist, and 0.0 would claim perfect agreement.
        "sd_delta_f": _f(r.get("sd_delta_f")),
        "mean_abs_delta_f": _f(r.get("mean_abs_delta_f")),
        "min_delta_f": _f(r.get("min_delta_f")),
        "max_delta_f": _f(r.get("max_delta_f")),
        "first_graded": _iso(r.get("first_graded")),
        "last_graded": _iso(r.get("last_graded")),
    }


GRADE_NOTE = (
    "delta_f = EnergyLake composite minus SoCalGas's own published composite, "
    "in degrees F. Divergence is a finding about OUR station weights, not an "
    "error in theirs: SoCalGas is the arbiter here, not the subject."
)


def grade_payload(rows, window_summary, all_time_summary, *, start, end) -> dict:
    shaped = [grade_row(r) for r in rows]
    return {
        "start": _iso(start),
        "end": _iso(end),
        "row_count": len(shaped),
        "summary": {
            "window": grade_summary(window_summary),
            "all_time": grade_summary(all_time_summary),
        },
        "note": GRADE_NOTE,
        "rows": shaped,
    }


# ---------------------------------------------------------------------------
# /api/weather/dd/forecast — newest issuance + the run-over-run delta
# ---------------------------------------------------------------------------
#
# THE DELTA IS THE TRADER NUMBER. A forecast board that shows only the current
# issuance shows a level; what moves a position is how that level CHANGED since
# the last run. Migration 161 keeps ALL issuances precisely so this subtraction
# is possible, and indexes (station_id, target_date, issued_ts DESC) precisely so
# it is cheap.
#
# `station_degree_days_forecast` is EMPTY today (0 rows, measured 2026-08-10 —
# the builder has not had its maiden run). That is why the board reports
# `board_state` and an `absence` rather than 404-ing or serving `[]` bare: an
# empty board and a filtered-to-nothing board are different answers, and the
# surface must be able to say which one it got.

FORECAST_EMPTY_NOTE = (
    "no issuances banked for this scope. station_degree_days_forecast keeps "
    "every issuance (migration 161); it holds no rows for this station/horizon "
    "yet. This is an absence, not a zero forecast."
)

DELTA_NO_PRIOR = "no_prior_issuance"


def _forecast_cell(cur: dict, prior: Optional[dict]) -> dict:
    """One target date: the newest issuance, plus its change against the prior.

    Where no prior issuance exists (a target date seen for the first time, or a
    maiden run) every delta is null with `delta_absence` naming the reason. A
    first-sighting is NOT a zero-change day.
    """
    def _d(field: str) -> Optional[float]:
        if prior is None:
            return None
        a, b = _f(cur.get(field)), _f(prior.get(field))
        if a is None or b is None:
            return None
        return round(a - b, 6)

    return {
        "station_id": cur["station_id"],
        "target_date": _iso(cur["target_date"]),
        "issued_ts": _iso(cur.get("issued_ts")),
        "tmax_f": _f(cur.get("tmax_f")),
        "tmin_f": _f(cur.get("tmin_f")),
        "tavg_f": _f(cur.get("tavg_f")),
        "hdd": _f(cur.get("hdd")),
        "cdd": _f(cur.get("cdd")),
        "basis_complete": cur.get("basis_complete"),
        "hours_covered": _i(cur.get("hours_covered")),
        "hours_required": _i(cur.get("hours_required")),
        "source_product": cur.get("source_product"),
        "gridpoint_id": cur.get("gridpoint_id"),
        "icao": cur.get("icao"),
        "prior_issued_ts": _iso(prior.get("issued_ts")) if prior else None,
        "prior_hdd": _f(prior.get("hdd")) if prior else None,
        "prior_cdd": _f(prior.get("cdd")) if prior else None,
        "prior_tavg_f": _f(prior.get("tavg_f")) if prior else None,
        "delta_hdd": _d("hdd"),
        "delta_cdd": _d("cdd"),
        "delta_tavg_f": _d("tavg_f"),
        "delta_basis": "run_over_run" if prior is not None else None,
        "delta_absence": None if prior is not None else _absence(
            DELTA_NO_PRIOR,
            "first issuance for this target date — no prior run to difference",
        ),
    }


def forecast_payload(rows: Iterable[dict], *, station, from_date, days) -> dict:
    """Rows ranked 1-2 per (station_id, target_date) -> the board.

    Expects main.py's query to have already cut each (station, target_date) to
    its two newest issuances via `row_number()`; rank 1 is current, rank 2 is
    prior. Ordering is re-established here so the shape does not depend on the
    server's row order.
    """
    by_key: dict[tuple[str, Any], dict[int, dict]] = {}
    for r in rows:
        by_key.setdefault((r["station_id"], r["target_date"]), {})[int(r["rn"])] = r

    cells = [
        _forecast_cell(ranks[1], ranks.get(2))
        for ranks in by_key.values()
        if 1 in ranks
    ]
    cells.sort(key=lambda c: (c["station_id"], c["target_date"]))

    stations = sorted({c["station_id"] for c in cells})
    issuances = sorted({c["issued_ts"] for c in cells if c["issued_ts"]})
    with_delta = sum(1 for c in cells if c["delta_basis"] == "run_over_run")

    return {
        "station": station,
        "from_date": _iso(from_date),
        "days": days,
        "board_state": "populated" if cells else "empty",
        "absence": None if cells else _absence("no_rows", FORECAST_EMPTY_NOTE),
        "stations_present": stations,
        "row_count": len(cells),
        "rows_with_run_over_run_delta": with_delta,
        "newest_issued_ts": issuances[-1] if issuances else None,
        "rows": cells,
    }
