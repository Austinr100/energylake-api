"""
season — the pure layer behind /api/weather/season* (d091503, Season to date lane 1).

Sidecar to main.py in the house idiom (degree_days.py, enso_catalog.py): no DB
handle, no clock, no FastAPI. main.py owns the SQL, the memo and the routes; this
module owns what a season IS and every number the page draws. The page does no
statistics.

═══════════════════════════════════════════════════════════════════════════
THE RULES (spec cc_spec_2026_09_28_season_api.md §2), one per trap
═══════════════════════════════════════════════════════════════════════════

  * WALK BY CALENDAR DATE, NEVER BY ROW. `days_in_window` is arithmetic on the
    season's two endpoints. A day is missing when its row is absent (`absent`)
    or its value is NULL / its degree-day basis is incomplete (`incomplete`) —
    the two modes of the dashboard's stationPrecip.ts contract. There is NO
    `or 0` on any value path: a missing day stops the cumulative, it is never
    summed as dry.
  * CUMULATIVE. values[i] is the running sum from season day 0 through day i,
    and null from the first missing day on.
  * LEAP DAY. The axis has no Feb 29. In a leap year Feb 29 is added INTO the
    Feb 28 day, so a season's final is the sum of all its calendar days.
  * THE BASE is every complete season (full record) except the current one.
    The cone needs n >= 30; the normal n >= 24 inside 1991-2020; a category
    median n >= 3 (the ENSO room's floor).
  * SEASON -> ENSO YEAR. Water year N -> N-1; heating E-(E+1) -> E; cooling
    Y -> Y. In all three that is the calendar year the season STARTS in.
  * OPEN YEAR. An ENSO year is open when it is at or after the catalog run's
    `developing.first_year`, OR when the pantry's rule (enso/composites.py
    `open_enso_years`) says its Aug(E) -> Apr(E+1) peak window is not closed in
    the ONI series — "its last whole season, FMA(E+1), is centred on Mar(E+1)":
    open iff (E+1, 3) > the newest ONI centre month. An open year has
    `categories: null`, whatever its bin row says.

Rounding happens once, at the edge (`_r`): everything is float64 until then.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Iterable, Mapping, Optional, Sequence

import numpy as np

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

VARS = ("precip", "hdd", "cdd")
CLASSIFIERS = ("cpc_oni", "roni")
DEFAULT_CLASSIFIER = "cpc_oni"

# The ENSO room's 13 keys (pantry enso/composites.py ENSO_CATEGORIES), in its order.
CATEGORIES = ("nino", "nina", "neutral",
              "nino_weak", "nino_moderate", "nino_strong",
              "nina_weak", "nina_moderate", "nina_strong",
              "nino_EP", "nino_CP", "nina_EP", "nina_CP")
_FLAVOR_KEYS = ("EP", "CP")          # `mixed` adds no key

# The four GHCNd stations banked since 2011 that station_metadata.json does not
# carry (measured 2026-09-28). They are labelled by their id: nothing invented.
UNLABELLED_STATIONS = ("USW00003145", "USW00023234", "USW00023293", "USW00093138")

# The 17 load regions of `lwt_degree_days_daily` (series `{BA}.HDD` / `{BA}.CDD`).
LWT_BAS = ("AZPS", "BANC", "BPAT", "EPE", "IPCO", "LADWP", "NEVP", "PACE", "PACW",
           "PGE-TAC", "PNM", "PSEI", "SCE-TAC", "SDGE-TAC", "SRP", "TEPC", "VEA-TAC")
LWT_DATASET = "lwt_degree_days_daily"

CONE_MIN_N = 30
NORMAL_MIN_N = 24
NORMAL_WINDOW = (1991, 2020)
CATEGORY_MIN_N = 3
FIVE_YEAR_N = 5
PERCENTILES = (0, 10, 25, 50, 75, 90, 100)

# var -> (kind, start (m, d), end (m, d), units)
_SEASONS = {
    "precip": ("water_year", (10, 1), (9, 30), "mm"),
    "hdd": ("heating", (11, 1), (3, 31), "°F·day"),
    "cdd": ("cooling", (5, 1), (9, 30), "°F·day"),
}

LEAP_RULE = ("the axis has no Feb 29; in a leap year Feb 29's value is added into "
             "the Feb 28 day, so a season's final is the sum of all its calendar days")

MAPPING = {
    "precip": "water year N (Oct 1 N-1 → Sep 30 N) → ENSO year N-1",
    "hdd": "heating season E-(E+1) (Nov 1 E → Mar 31 E+1) → ENSO year E",
    "cdd": "cooling season Y (May 1 → Sep 30 Y) → ENSO year Y (the ENSO room's JJA rule, band_year - 0)",
}

ABSENT = "absent"            # no row
INCOMPLETE = "incomplete"    # a row whose value is NULL (or basis_complete false)


# ---------------------------------------------------------------------------
# Season arithmetic
# ---------------------------------------------------------------------------

def _crosses(var: str) -> bool:
    _, (sm, _), (em, _), _ = _SEASONS[var]
    return em < sm


def bounds(var: str, s: int) -> tuple[date, date]:
    """(first, last) calendar day of the season that starts in year `s`."""
    _, (sm, sd), (em, ed), _ = _SEASONS[var]
    return date(s, sm, sd), date(s + 1 if _crosses(var) else s, em, ed)


def season_of(var: str, d: date) -> Optional[int]:
    """The start year of the season holding `d`, or None in a shoulder month."""
    start, end = bounds(var, d.year)
    if start <= d <= end:
        return d.year
    if _crosses(var):
        start, end = bounds(var, d.year - 1)
        if start <= d <= end:
            return d.year - 1
    return None


def label(var: str, s: int) -> str:
    if var == "precip":
        return f"WY{s + 1}"
    if var == "hdd":
        return f"{s}-{(s + 1) % 100:02d}"
    return str(s)


def label_year(var: str, s: int) -> int:
    """The year a season's label names (WY N -> N; 1991-92 -> 1991; 2016 -> 2016)."""
    return s + 1 if var == "precip" else s


def enso_year(var: str, s: int) -> int:
    """§2.7: WY N -> N-1, heating E-(E+1) -> E, cooling Y -> Y. All = start year."""
    return s


def axis(var: str) -> list[str]:
    """The season's month-day strings on a non-leap reference (no Feb 29)."""
    start, end = bounds(var, 2021)      # 2021-22 holds no Feb 29
    out, d = [], start
    while d <= end:
        out.append(f"{d.month:02d}-{d.day:02d}")
        d += timedelta(days=1)
    return out


def _slot_index(var: str) -> dict[str, int]:
    return {md: i for i, md in enumerate(axis(var))}


def season_meta(var: str) -> dict:
    kind, (sm, sd), (em, ed), _ = _SEASONS[var]
    return {"kind": kind, "start_md": f"{sm:02d}-{sd:02d}",
            "end_md": f"{em:02d}-{ed:02d}", "days": len(axis(var)),
            "leap_rule": LEAP_RULE}


def units(var: str) -> str:
    return _SEASONS[var][3]


def days_in_window(var: str, s: int) -> int:
    a, b = bounds(var, s)
    return (b - a).days + 1


# ---------------------------------------------------------------------------
# Areas
# ---------------------------------------------------------------------------

def station_order(metadata_stations: Sequence[Mapping]) -> list[tuple[str, Optional[Mapping]]]:
    """[(station_id, metadata row or None)]: the metadata's N→S order, then the
    unlabelled four."""
    out = [(m["station_id"], m) for m in metadata_stations]
    known = {sid for sid, _ in out}
    out += [(sid, None) for sid in UNLABELLED_STATIONS if sid not in known]
    return out


def area_vocabulary(metadata_stations: Sequence[Mapping]) -> list[str]:
    return ([f"station:{sid}" for sid, _ in station_order(metadata_stations)]
            + [f"lwt:{ba}" for ba in LWT_BAS])


def vars_for(area: str) -> tuple[str, ...]:
    return VARS if area.startswith("station:") else ("hdd", "cdd")


def parse_request(area: Optional[str], var: Optional[str], classifier: Optional[str],
                  metadata_stations: Sequence[Mapping]) -> tuple[str, str, str]:
    """-> (area, var, classifier), or ValueError naming the vocabulary."""
    vocab = area_vocabulary(metadata_stations)
    if not area or area not in vocab:
        raise ValueError(f"unknown area {area!r}; allowed: station:{{GHCN id}} for "
                         + ", ".join(sid for sid, _ in station_order(metadata_stations))
                         + "; lwt:{BA} for " + ", ".join(LWT_BAS))
    if var not in VARS:
        raise ValueError(f"unknown var {var!r}; allowed: " + ", ".join(VARS))
    if var not in vars_for(area):
        raise ValueError(f"var {var!r} is not served for {area}; lwt: areas carry "
                         "hdd, cdd (precip is stations only)")
    classifier = classifier or DEFAULT_CLASSIFIER
    if classifier not in CLASSIFIERS:
        raise ValueError(f"unknown classifier {classifier!r}; allowed: "
                         + ", ".join(CLASSIFIERS))
    return area, var, classifier


def build_areas(metadata_stations: Sequence[Mapping],
                counts: Mapping[tuple[str, str], Mapping[int, int]]) -> dict:
    """`counts[(area, var)][season start year] = days carrying a value`, over
    every season with at least one row. A season is complete iff that count
    equals its calendar `days_in_window`."""
    areas = []

    def _vars(area: str) -> list[dict]:
        out = []
        for v in vars_for(area):
            per = counts.get((area, v), {})
            first = min(per) if per else None
            out.append({
                "var": v, "season": _SEASONS[v][0], "units": units(v),
                "first_season": label(v, first) if first is not None else None,
                "complete_seasons": sum(1 for s, n in per.items()
                                        if n == days_in_window(v, s)),
            })
        return out

    for sid, meta in station_order(metadata_stations):
        area = f"station:{sid}"
        row = {"area": area, "kind": "station",
               "label": (meta.get("display_name") or meta.get("metro")) if meta else sid}
        if meta and meta.get("state"):
            row["state"] = meta["state"]
        row["vars"] = _vars(area)
        areas.append(row)
    for ba in LWT_BAS:
        area = f"lwt:{ba}"
        areas.append({"area": area, "kind": "lwt", "label": ba, "vars": _vars(area)})
    return {"areas": areas}


# ---------------------------------------------------------------------------
# The walk
# ---------------------------------------------------------------------------

_NO_ROW = object()


class _Walk:
    __slots__ = ("s", "values", "days_complete", "days_in_window", "first_missing",
                 "mode", "through")

    @property
    def gap(self) -> bool:
        return self.first_missing is not None


def _walk(var: str, s: int, daily: Mapping[date, Optional[float]], limit: date,
          slots: Mapping[str, int]) -> _Walk:
    """Walk season `s` by calendar date from its first day through `limit`.

    Every day is looked up; nothing is iterated by row. The running sum is
    written to each day's slot until the first missing day, after which the
    slot stays None. Feb 29 folds into the Feb 28 slot; if Feb 29 is the first
    missing day, the Feb 28 slot it would have completed is withdrawn too."""
    start, end = bounds(var, s)
    w = _Walk()
    w.s = s
    w.values = [None] * len(slots)
    w.days_complete = 0
    w.days_in_window = (end - start).days + 1
    w.first_missing, w.mode, w.through = None, None, None
    total = 0.0
    d = start
    while d <= limit:
        v = daily.get(d, _NO_ROW)
        slot = slots["02-28" if (d.month, d.day) == (2, 29) else f"{d.month:02d}-{d.day:02d}"]
        if v is _NO_ROW or v is None:
            if w.first_missing is None:
                w.first_missing = d
                w.mode = ABSENT if v is _NO_ROW else INCOMPLETE
                if w.values[slot] is not None:          # Feb 29 after a written Feb 28
                    w.values[slot] = None
                    w.through = d - timedelta(days=2) if d - timedelta(days=2) >= start else None
        else:
            w.days_complete += 1
            if w.first_missing is None:
                total += float(v)
                w.values[slot] = total
                w.through = d
        d += timedelta(days=1)
    return w


def _complete(w: _Walk) -> bool:
    return w.first_missing is None and w.days_complete == w.days_in_window


# ---------------------------------------------------------------------------
# ENSO membership
# ---------------------------------------------------------------------------

def open_years(developing: Optional[Mapping], oni_last_centre: Optional[tuple[int, int]],
               enso_years: Iterable[int]) -> set[int]:
    """§2.8 T-open. At/after `developing.first_year`, or (pantry rule,
    enso/composites.py open_enso_years) whose peak window Aug(E)..Apr(E+1) is
    not closed in the ONI series: open iff (E+1, 3) > the newest ONI centre."""
    out = set()
    first = developing.get("first_year") if developing else None
    for e in enso_years:
        if first is not None and e >= int(first):
            out.add(e)
        elif oni_last_centre is not None and (e + 1, 3) > tuple(oni_last_centre):
            out.add(e)
    return out


def categories_of(bin_row: Optional[Mapping]) -> Optional[list[str]]:
    """kind; + kind_strength when set; + kind_flavor for EP / CP only."""
    if bin_row is None or bin_row.get("kind") is None:
        return None
    kind = bin_row["kind"]
    out = [kind]
    if bin_row.get("strength"):
        out.append(f"{kind}_{bin_row['strength']}")
    if bin_row.get("flavor") in _FLAVOR_KEYS:
        out.append(f"{kind}_{bin_row['flavor']}")
    return [c for c in out if c in CATEGORIES]


def classify(e: int, bins: Mapping[int, Mapping], opened: set[int]) -> tuple[Optional[list[str]], Optional[str]]:
    """-> (categories, category_absence)."""
    if e in opened:
        return None, "open"
    if bins and e < min(bins):
        return None, "before_catalog"
    row = bins.get(e)
    if row is None:
        return None, "not_binned"
    cats = categories_of(row)
    if cats is None:
        return None, "unclassified"
    return cats, None


# ---------------------------------------------------------------------------
# The payload
# ---------------------------------------------------------------------------

def _r(x: Optional[float]) -> Optional[float]:
    """The one rounding, at the edge: 0.1 (mm for precip, °F·day for HDD/CDD)."""
    return None if x is None else round(float(x), 1)


def _rv(xs: Iterable[Optional[float]]) -> list[Optional[float]]:
    return [_r(x) for x in xs]


def _season_block(var: str, w: _Walk, *, current: bool) -> dict:
    start, _ = bounds(var, w.s)
    out = {"season": label(var, w.s), "start": start.isoformat(),
           "through": w.through.isoformat() if w.through else None,
           "values": _rv(w.values)}
    if current:
        out["complete_to_date"] = not w.gap
    else:
        out["complete"] = _complete(w)
    out["absence"] = _gap_absence(w) if w.gap else None
    return out


def _gap_absence(w: _Walk) -> dict:
    return {"reason": "gap", "first_missing": w.first_missing.isoformat(),
            "mode": w.mode, "days_complete": w.days_complete}


def cone(B: np.ndarray) -> dict[str, np.ndarray]:
    """Per-day percentiles across the base rows: linear interpolation (NumPy's
    default, Hyndman-Fan type 7). p0 is the minimum, p100 the maximum."""
    return {f"p{q}": np.percentile(B, q, axis=0, method="linear") for q in PERCENTILES}


def mid_rank(value: float, sample: Sequence[float]) -> float:
    """100·(#below + ½·#equal)/n."""
    below = sum(1 for x in sample if x < value)
    equal = sum(1 for x in sample if x == value)
    return 100.0 * (below + 0.5 * equal) / len(sample)


def build_season(area: str, var: str, daily: Mapping[date, Optional[float]], *,
                 classifier: str, catalog_version: Optional[str],
                 developing: Optional[Mapping], bins: Sequence[Mapping],
                 oni_last_centre: Optional[tuple[int, int]] = None) -> dict:
    """The /api/weather/season body, keys in contract order.

    `daily` maps each calendar date that HAS A ROW to its value, None where the
    row carries no usable value (NULL, or basis_complete false). A date with no
    row is simply not a key. `bins` are enso_year_bins rows for `classifier`.
    """
    slots = _slot_index(var)
    ax = axis(var)
    ndays = len(ax)

    valued = [d for d, v in daily.items() if v is not None]
    frontier = max(valued) if valued else None

    # Seasons with any data (a row, valued or not), not starting after the frontier.
    seasons = sorted({s for d in daily for s in [season_of(var, d)]
                      if s is not None and (frontier is None or bounds(var, s)[0] <= frontier)})

    cur = None
    if frontier is not None:
        s = season_of(var, frontier)
        if s is not None and frontier < bounds(var, s)[1]:
            cur = s

    walks: dict[int, _Walk] = {}
    for s in seasons:
        limit = frontier if s == cur else bounds(var, s)[1]
        walks[s] = _walk(var, s, daily, limit, slots)
    past = [s for s in seasons if s != cur]

    # ── base ────────────────────────────────────────────────────────────────
    base = [s for s in past if _complete(walks[s])]
    excluded = [{"season": label(var, s), "days_complete": walks[s].days_complete,
                 "days_in_window": walks[s].days_in_window,
                 "first_missing": walks[s].first_missing.isoformat()
                 if walks[s].first_missing else None}
                for s in past if not _complete(walks[s])]
    B = np.array([walks[s].values for s in base], dtype=np.float64).reshape(len(base), ndays)
    n = len(base)

    if n >= CONE_MIN_N:
        pct = cone(B)
        percentiles = {"method": "linear (type 7)", **{k: _rv(v) for k, v in pct.items()}}
        percentiles_absence = None
        p50 = pct["p50"]
    else:
        percentiles, p50 = None, None
        percentiles_absence = {"reason": "short_record", "n": n}

    # ── normal ──────────────────────────────────────────────────────────────
    lo, hi = NORMAL_WINDOW
    normal_idx = [i for i, s in enumerate(base) if lo <= label_year(var, s) <= hi]
    if len(normal_idx) >= NORMAL_MIN_N:
        normal = {"window": f"{lo}-{hi}", "n": len(normal_idx),
                  "values": _rv(B[normal_idx].mean(axis=0))}
        normal_absence = None
    else:
        normal = None
        normal_absence = {"reason": "short_window", "n": len(normal_idx)}

    # ── five-year band: the five most recent COMPLETE seasons ───────────────
    five = sorted(base)[-FIVE_YEAR_N:]
    if len(five) == FIVE_YEAR_N:
        F = np.array([walks[s].values for s in five], dtype=np.float64)
        five_mean = F.mean(axis=0)
        five_year = {"seasons": [label(var, s) for s in five], "mean": _rv(five_mean),
                     "min": _rv(F.min(axis=0)), "max": _rv(F.max(axis=0))}
        five_year_absence = None
    else:
        five_mean, five_year = None, None
        five_year_absence = {"reason": "fewer_than_five", "n": len(five)}

    # ── this season / last season ───────────────────────────────────────────
    if cur is not None:
        this_season = _season_block(var, walks[cur], current=True)
        this_season_absence = None
        last_s = max(past) if past else None
    else:
        this_season = None
        this_season_absence = {"reason": "between_seasons",
                               "frontier": frontier.isoformat() if frontier else None}
        last_s = max(past) if past else None
    if last_s is not None:
        last_season = _season_block(var, walks[last_s], current=False)
        last_season_absence = None
    else:
        last_season, last_season_absence = None, {"reason": "no_prior_season"}

    # ── ENSO ────────────────────────────────────────────────────────────────
    bins_by_year = {int(b["enso_year"]): b for b in bins}
    opened = open_years(developing, oni_last_centre,
                        set(bins_by_year) | {enso_year(var, s) for s in seasons})
    klass = {s: classify(enso_year(var, s), bins_by_year, opened) for s in seasons}

    cat_medians: dict[str, Optional[np.ndarray]] = {}
    categories = {}
    for c in CATEGORIES:
        idx = [i for i, s in enumerate(base) if klass[s][0] and c in klass[s][0]]
        block = {"seasons": [label(var, base[i]) for i in idx], "n": len(idx)}
        if len(idx) >= CATEGORY_MIN_N:
            med = np.median(B[idx], axis=0)
            cat_medians[c] = med
            block["median"] = _rv(med)
        else:
            cat_medians[c] = None
            block["median"] = None
            block["absence"] = {"reason": "small_n", "n": len(idx)}
        categories[c] = block
    enso = {"classifier": classifier, "catalog_version": catalog_version,
            "mapping": MAPPING[var], "developing": developing, "categories": categories}

    # ── readout ─────────────────────────────────────────────────────────────
    day: Optional[int] = None          # the like-for-like day for years[].to_date
    readout, readout_absence = None, None
    if cur is not None:
        w = walks[cur]
        if w.through is not None:
            day = slots["02-28" if (w.through.month, w.through.day) == (2, 29)
                        else f"{w.through.month:02d}-{w.through.day:02d}"]
        if w.gap or day is None:
            readout_absence = _gap_absence(w) if w.gap else {"reason": "no_data"}
            value = None
        else:
            value = w.values[day]
    elif last_s is not None:
        w = walks[last_s]
        day = ndays - 1
        if _complete(w):
            value = w.values[day]
        else:
            readout_absence = _gap_absence(w) if w.gap else {"reason": "incomplete"}
            value = None
    else:
        value = None
        readout_absence = {"reason": "no_data"}

    if value is not None:
        median = float(p50[day]) if p50 is not None else None
        vs_cat = {c: (_r(value - float(cat_medians[c][day]))
                      if cat_medians[c] is not None else None) for c in CATEGORIES}
        readout = {
            "day": day,
            "value": _r(value),
            "median": _r(median),
            "pct_of_median": (_r(100.0 * value / median)
                              if median is not None and median != 0 else None),
            # The same n >= 30 gate as the cone: no rank against a short record.
            "percentile": _r(mid_rank(value, B[:, day].tolist())) if p50 is not None else None,
            "vs_five_year": _r(value - float(five_mean[day])) if five_mean is not None else None,
            "vs_category": vs_cat,
        }

    # ── years / curves ──────────────────────────────────────────────────────
    years = []
    for s in seasons:
        w = walks[s]
        cats, why = klass[s]
        years.append({
            "season": label(var, s), "enso_year": enso_year(var, s),
            "categories": cats, "category_absence": why,
            "complete": s != cur and _complete(w),
            "to_date": _r(w.values[day]) if day is not None else None,
            "final": _r(w.values[-1]) if s != cur and _complete(w) else None,
        })
    curves = {label(var, s): _rv(walks[s].values) for s in base
              if enso_year(var, s) in bins_by_year and enso_year(var, s) not in opened}

    return {
        "area": area, "var": var, "units": units(var),
        "season": season_meta(var),
        "frontier": frontier.isoformat() if frontier else None,
        "axis": ax,
        "base": {"rule": "every complete season, full record, excluding the current season",
                 "seasons": [label(var, s) for s in base], "n": n, "excluded": excluded},
        "percentiles": percentiles, "percentiles_absence": percentiles_absence,
        "normal": normal, "normal_absence": normal_absence,
        "five_year": five_year, "five_year_absence": five_year_absence,
        "this_season": this_season, "this_season_absence": this_season_absence,
        "last_season": last_season, "last_season_absence": last_season_absence,
        "enso": enso,
        "years": years,
        "curves": curves,
        "readout": readout, "readout_absence": readout_absence,
    }


# Contract order (§1's table; each `*_absence` rides right after its sibling).
RESPONSE_KEYS = ("area", "var", "units", "season", "frontier", "axis", "base",
                 "percentiles", "percentiles_absence", "normal", "normal_absence",
                 "five_year", "five_year_absence", "this_season", "this_season_absence",
                 "last_season", "last_season_absence", "enso", "years", "curves",
                 "readout", "readout_absence")


# ---------------------------------------------------------------------------
# SQL (main.py executes; this module only names it)
# ---------------------------------------------------------------------------

PRECIP_SQL = """
    SELECT obs_date, prcp_mm::float8 AS v
    FROM ghcnd_weather_daily WHERE station_id = %(sid)s
    ORDER BY obs_date
"""

# `basis_complete` false is a missing day, not a value: the route maps it to None.
STATION_DD_SQL = """
    SELECT obs_date, hdd::float8 AS hdd, cdd::float8 AS cdd, basis_complete
    FROM station_degree_days_daily WHERE station_id = %(sid)s
    ORDER BY obs_date
"""

LWT_SQL = """
    SELECT (ts AT TIME ZONE 'UTC')::date AS obs_date, value::float8 AS v
    FROM timeseries_values WHERE dataset = %(d)s AND series = %(s)s
    ORDER BY ts
"""

# The newest ONI / RONI centre month (the pantry's open-year rule's input).
ONI_LAST_SQL = """
    SELECT max(ts) AS ts FROM timeseries_values WHERE dataset = %(d)s AND series = %(s)s
"""

# /areas: days carrying a value per (area, var, season start year), over every
# season holding at least one row. Season start year by date shift: Oct 1 − 9
# months = Jan 1 (water year); Nov 1 − 10 months = Jan 1 (heating).
AREAS_PRECIP_SQL = """
    SELECT station_id AS id,
           extract(year FROM obs_date - interval '9 months')::int AS s,
           count(prcp_mm)::int AS n
    FROM ghcnd_weather_daily GROUP BY 1, 2
"""

AREAS_STATION_DD_SQL = """
    SELECT station_id AS id, 'hdd' AS var,
           extract(year FROM obs_date - interval '10 months')::int AS s,
           count(*) FILTER (WHERE hdd IS NOT NULL AND basis_complete)::int AS n
    FROM station_degree_days_daily
    WHERE extract(month FROM obs_date) IN (11, 12, 1, 2, 3) GROUP BY 1, 2, 3
    UNION ALL
    SELECT station_id, 'cdd', extract(year FROM obs_date)::int,
           count(*) FILTER (WHERE cdd IS NOT NULL AND basis_complete)::int
    FROM station_degree_days_daily
    WHERE extract(month FROM obs_date) BETWEEN 5 AND 9 GROUP BY 1, 2, 3
"""

AREAS_LWT_SQL = """
    WITH t AS (
        SELECT split_part(series, '.', 1) AS id, lower(split_part(series, '.', 2)) AS var,
               (ts AT TIME ZONE 'UTC')::date AS d, value
        FROM timeseries_values
        WHERE dataset = %(d)s AND series ~ '^[A-Z-]+\\.(HDD|CDD)$'
    )
    SELECT id, var,
           CASE WHEN var = 'hdd' THEN extract(year FROM d - interval '10 months')
                ELSE extract(year FROM d) END::int AS s,
           count(value)::int AS n
    FROM t
    WHERE (var = 'hdd' AND extract(month FROM d) IN (11, 12, 1, 2, 3))
       OR (var = 'cdd' AND extract(month FROM d) BETWEEN 5 AND 9)
    GROUP BY 1, 2, 3
"""


def daily_from_rows(var: str, rows: Iterable[Mapping], *, station_dd: bool = False) -> dict[date, Optional[float]]:
    """Rows -> {date: value or None}. A row with a NULL value (or, for station
    degree days, basis_complete false) maps to None — present but missing. No
    `or 0` anywhere."""
    out: dict[date, Optional[float]] = {}
    for r in rows:
        if station_dd:
            v = r[var] if r.get("basis_complete") is True else None
        else:
            v = r["v"]
        out[r["obs_date"]] = None if v is None else float(v)
    return out
