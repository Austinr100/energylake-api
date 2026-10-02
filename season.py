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
    summed as dry (but see CARRIED below for station precipitation).
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

SNOW (d091520, spec cc_spec_2026_09_30_season_snow_api.md §1). `swe` on a
`snow:{basin}` area is a LEVEL, not an amount: the basin's snow water
equivalent that day as a percent of its normal peak. A level changes four
rules and nothing else:

  * values[i] is the day's own value; nothing is summed.
  * A missing day is null on that day only; later days keep their values.
  * Feb 29 is on no slot and is added into nothing: the Feb 28 slot holds
    Feb 28's value. Completeness still counts it (a leap water year needs 366
    valued days).
  * years[].final is null (Sep 30 is bare ground); each season carries its
    `peak` instead, and the payload a top-level `peak` block.

Base, cone, normal, five-year band, ENSO medians and their gates are the same
rules on the new values.

HYDRO (d091522, spec cc_spec_2026_09_30_season_hydro_api.md). Two more levels
on the water year: `storage` (TAF) on `reservoir:{id}` and `reservoir:ca_major8`
(the eight summed, a day valued only when all eight report, never
renormalised), and `swe_in` (inches of snow water, CDEC's regional average) on
`snow:ca_{region}`. Their records are short and holed, so for these two only:

  * A FLOOR, not strict completeness: a season qualifies when >= 0.90 of the
    days in its count window carry a value (storage: every calendar day of the
    season; swe_in: Dec 1 -> May 31, outside which a missing day is the feed's
    off-season and is not counted anywhere, gap or absence).
  * PER-DAY STATISTICS IGNORE NULLS: each day's cone / normal / five-year band /
    category median / range is over the qualifying seasons valued that day; a
    day with fewer than 3 is null in every statistic. Blocks carry `n` and
    `n_by_day_min`. Nothing is ever read as 0.
  * `curves` carries every qualifying season.

Every level (swe too) gains `range` (per-day min / median / max over the base,
n >= 5), each ENSO category's `outlook` (its members' peaks and Apr 1 / Jul 1
values: min / median / max, never a percentile), and every payload `enso.now`
(the category the catalog run's `developing` reads today; a label, not a
forecast).

LOAD (d091525, spec cc_spec_2026_09_30_season_load_api.md). `peak_load` on
`ba:{code}` (the 28 balancing areas of EIA-930's `wecc_load_hourly`) is a
level on the water year: the day's maximum hourly load, MW. Two rules of its
own, the rest is the hydro arithmetic above (0.90 floor over every day of the
season, null-aware statistics, `range`, `outlook`, `enso.now`):

  * THE DAY IS A FIXED UTC−8 DAY for every area, no daylight-saving shift:
    day D runs D 08:00Z → D+1 07:59Z (`season.day_rule`). The West's daily
    peak falls in the afternoon or evening, far from that boundary.
  * A day qualifies with >= 20 of its 24 hours reporting; otherwise it is null
    (a peak read from a morning-only day is not a peak).

`peak_load_7d` is the trailing 7-day mean of the daily peak (D−6 … D), null
unless all seven days qualify: the daily line is weekday noise.

CANADA (d091536, spec cc_spec_2026_09_30_season_canada_snow_api.md).
`snow:col_canada`, the Canadian Columbia over BC's ASWS pillows, is `swe` like
the six US basins, with one rule of its own, carried by the same FLOORS table
keyed (area, var): a season qualifies when every day Nov 1 -> May 31 carries a
value. The BC stations stop reporting when the pillow is bare, so a summer day
with no row is a gap on the chart (null, never 0, never carried forward) and
does not disqualify the season. Its statistics are the floored, null-aware
ones; a member with no Jul 1 value is left out of Jul 1 (its `n` says so). It
is not on the board and is summed into nothing.

CARRIED (d091550, spec cc_spec_2026_10_01_season_missing_days.md, D-09-25-86).
Station precipitation, `("station", "precip")` only, is summed over the days
that reported and says how many did not:

  * A missing day adds nothing and does not stop the sum: its slot carries the
    running total unchanged (0 before the first reported day) and later days
    keep adding. A missing Feb 29 leaves the Feb 28 slot as Feb 28 wrote it.
  * The season block says so: `days_missing`, `missing_days` (the first 31),
    `first_missing`, `within_tolerance`; `through` is the newest valued day.
    `complete_to_date` stays strictly "no missing day".
  * PRECIP_MISSING_TOLERANCE = 5 missing days, chosen, not measured. At or
    under it the readout is given with `basis: "sum of reported days; n
    missing"`; over it the curve is still drawn and the readout is withheld,
    `reason: "too_many_missing"`.
  * STOP-B fired (handback_2026_10_01_season_missing_days.md): the rule is on
    for the SEASON IN PROGRESS only. Past seasons walk and qualify exactly as
    before (strict) while PRECIP_TOLERANT_BASE is False; set True, a past
    season with at most 5 missing days is carried, qualifies for the base and
    counts in /areas' `qualifying_seasons`.

HDD, CDD and every level walk as before.

Rounding happens once, at the edge (`_r`): everything is float64 until then.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Optional, Sequence

import numpy as np

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

VARS = ("precip", "hdd", "cdd", "swe", "storage", "swe_in", "peak_load", "peak_load_7d")
CUMULATIVE, LEVEL = "cumulative", "level"
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

# The six snow basins of `snow_basin_index_daily` (series `{basin}.SWE_PCT`), in
# the contract's order: whole basin first, then upstream to downstream, a union
# before its parts. Labels are the pantry's snow/basins.py LABELS, verbatim.
# Pinned here, never discovered from the database.
SNOW_BASINS = ("columbia_above_the_dalles", "col_above_grand_coulee",
               "col_mid_tributaries", "snake", "snake_upper", "snake_lower")
SNOW_LABELS = {
    "col_above_grand_coulee": "Columbia above Grand Coulee (US)",
    "col_mid_tributaries": "Mid-Columbia tributaries (Grand Coulee → The Dalles)",
    "snake_upper": "Upper Snake (above Hells Canyon)",
    "snake_lower": "Lower Snake (Salmon, Clearwater, Grande Ronde)",
    "snake": "Snake",
    "columbia_above_the_dalles": "Columbia above The Dalles (US)",
}
# d091536: the Canadian Columbia, the pantry's `col_canada` leaf over BC's ASWS
# network (six stations, its own 1991–2020 normals; label verbatim from
# snow/basins.py). A seventh `swe` area, listed after the six, but NOT a
# SNOW_BASINS member: the board walks the six US basins, and no union sums
# Canada in (D-09-25-54).
CANADA_SNOW = ("col_canada",)
CANADA_SNOW_LABELS = {"col_canada": "Canadian Columbia (BC)"}
SNOW_DATASET = "snow_basin_index_daily"
SNOW_SERIES_SUFFIX = ".SWE_PCT"

# California (d091522). The eight reservoirs of `cdec_reservoir_storage_daily`,
# north to south, with the dashboard's names and capacities (energylake-dashboard
# src/app/almanac/california-water-storage/page.tsx, `RESERVOIRS`, capacityAF),
# copied, not discovered. Storage is served in TAF (acre-feet / 1000).
RESERVOIR_DATASET = "cdec_reservoir_storage_daily"
RESERVOIRS = (  # (id, label, capacity in acre-feet)
    ("trinity", "Trinity Lake", 2447650),
    ("shasta", "Shasta Lake", 4552000),
    ("oroville", "Lake Oroville", 3537577),
    ("folsom", "Folsom Lake", 977000),
    ("new_melones", "New Melones Lake", 2400000),
    ("don_pedro", "Don Pedro Reservoir", 2030000),
    ("millerton", "Millerton Lake", 520500),
    ("san_luis", "San Luis Reservoir", 2041000),
)
RESERVOIR_IDS = tuple(r[0] for r in RESERVOIRS)
MAJOR8 = "ca_major8"
MAJOR8_LABEL = "California, eight major reservoirs"
# d091542 (D-09-25-71): three regional composites, each the sum of its members
# by `major8_daily`'s rule. The grouping is by the CDEC snow region that feeds
# each reservoir, checked against CDEC's own region definitions
# (cdec.water.ca.gov/snowapp/sweq.action, 2026-10-01): North "Trinity through
# Feather & Truckee"; Central "Yuba & Tahoe through Merced & Walker"; South
# "San Joaquin & Mono through Kern". Folsom (American), New Melones
# (Stanislaus) and Don Pedro (Tuolumne) are Central; Millerton (San Joaquin)
# is South. San Luis is in no region: it is off-stream and fills by pumping.
RESERVOIR_REGIONS = (  # (id, label, members)
    ("ca_north", "Northern California reservoirs (Trinity, Shasta, Oroville)",
     ("trinity", "shasta", "oroville")),
    ("ca_central", "Central Sierra reservoirs (Folsom, New Melones, Don Pedro)",
     ("folsom", "new_melones", "don_pedro")),
    ("ca_south", "Southern Sierra reservoir (Millerton)", ("millerton",)),
)
RESERVOIR_REGION_IDS = tuple(r[0] for r in RESERVOIR_REGIONS)
RESERVOIR_MEMBERS = {MAJOR8: RESERVOIR_IDS, **{i: m for i, _, m in RESERVOIR_REGIONS}}
MAJOR8_MEMBERS_NOTE = ("San Luis Reservoir is in the eight and in no regional composite: "
                       "it is off-stream and fills by pumping, not from its own snow region")
RESERVOIR_AREAS = (MAJOR8,) + RESERVOIR_REGION_IDS + RESERVOIR_IDS          # /areas order
RESERVOIR_LABELS = {MAJOR8: MAJOR8_LABEL, **{i: lab for i, lab, _ in RESERVOIR_REGIONS},
                    **{i: lab for i, lab, _ in RESERVOIRS}}
CAPACITY_AF = {i: af for i, _, af in RESERVOIRS}
for _c, _m in RESERVOIR_MEMBERS.items():
    CAPACITY_AF[_c] = sum(CAPACITY_AF[i] for i in _m)

# California's snow: CDEC's four regional averages (`{region}_avg_swc`, inches).
CA_SNOW_DATASET = "cdec_snowpack_swe_daily"
CA_SNOW = ("ca_state", "ca_north", "ca_central", "ca_south")
CA_SNOW_LABELS = {"ca_state": "California statewide",
                  "ca_north": "Northern Sierra / Trinity",
                  "ca_central": "Central Sierra",
                  "ca_south": "Southern Sierra"}


# Load (d091525). The 28 balancing areas of EIA-930's `wecc_load_hourly` (MW,
# hourly, interval-start UTC), measured 2026-09-30 and pinned here, never
# discovered from the database. Labelled by code; /areas lists them A→Z after
# the reservoirs.
LOAD_DATASET = "wecc_load_hourly"
BAS = ("AVA", "AZPS", "BANC", "BPAT", "CHPD", "CISO", "DOPD", "EPE", "GCPD", "IID",
       "IPCO", "LDWP", "NEVP", "NWMT", "PACE", "PACW", "PGE", "PNM", "PSCO", "PSEI",
       "SCL", "SRP", "TEPC", "TIDC", "TPWR", "WACM", "WALC", "WAUW")
LOAD_DAY_OFFSET = timedelta(hours=8)      # the day is UTC−8, fixed: no DST
LOAD_MIN_HOURS = 20                       # of 24, for a day to carry its peak
LOAD_WINDOW = 7                           # peak_load_7d: D−6 … D
LOAD_VARS = ("peak_load", "peak_load_7d")
DAY_RULE = ("a fixed UTC−8 day for every area, no daylight-saving shift: day D runs "
            "D 08:00Z → D+1 07:59Z; it carries the maximum hourly load when >= 20 of "
            "its 24 hours report, and is null otherwise")
DAY_RULE_7D = (DAY_RULE + "; peak_load_7d on day D is the mean of the daily peaks "
               "D−6 … D, null unless all seven carry one")


# ---------------------------------------------------------------------------
# What an area measures and where it is (d091542, D-09-25-70)
# ---------------------------------------------------------------------------
# Pinned here, never derived from the id. `data_type` is a property of the
# VAR; `measure` keeps the two snow measures apart. "Snowpack" is the reader's
# word; the quantity is snow water equivalent, and the API says so.
DATA_TYPES = ("precipitation", "snow_water_equivalent", "reservoir_storage",
              "heating_degree_days", "cooling_degree_days", "peak_load")
VAR_DATA_TYPES = {  # var -> (data_type, measure)
    "precip": ("precipitation", "water-year total, mm"),
    "swe": ("snow_water_equivalent", "percent of normal peak"),
    "swe_in": ("snow_water_equivalent", "inches"),
    "storage": ("reservoir_storage", "thousand acre-feet"),
    "hdd": ("heating_degree_days", "°F·day, season total"),
    "cdd": ("cooling_degree_days", "°F·day, season total"),
    "peak_load": ("peak_load", "MW, daily peak"),
    "peak_load_7d": ("peak_load", "MW, 7-day mean of daily peaks"),
}
# The var a snapshot reads for a data type on an area that carries it (an area
# carries at most one var per data type, but for the load pair: the daily peak).
SNAPSHOT_VAR_ORDER = ("precip", "swe", "swe_in", "storage", "hdd", "cdd", "peak_load")

REGIONS = ("california", "pacific_northwest", "canada", "southwest", "rockies")
REGION_LABELS = {"california": "California", "pacific_northwest": "Pacific Northwest",
                 "canada": "Canada (Columbia)", "southwest": "Southwest",
                 "rockies": "Rockies"}
LEVELS = ("aggregate", "region", "basin", "station", "reservoir", "balancing_area")
# A station's or a balancing area's two-letter state -> its region (§2.3).
STATE_REGIONS = {"CA": "california",
                 "OR": "pacific_northwest", "WA": "pacific_northwest",
                 "ID": "pacific_northwest", "MT": "pacific_northwest",
                 "AZ": "southwest", "NM": "southwest", "NV": "southwest",
                 "CO": "rockies", "UT": "rockies", "WY": "rockies"}
# The balancing areas' home states: the state of the utility's or the
# authority's headquarters (EIA-930's BA list; EIA Form 861 for the utility's
# address). A BA spanning states takes its home state, and the row says
# `region_basis: "home state"`. EPE (El Paso Electric) is headquartered in
# Texas, which the table does not map: STOP-R, region null with a reason.
BA_HOME_STATES = {
    "AVA": "WA", "AZPS": "AZ", "BANC": "CA", "BPAT": "OR", "CHPD": "WA", "CISO": "CA",
    "DOPD": "WA", "EPE": "TX", "GCPD": "WA", "IID": "CA", "IPCO": "ID", "LDWP": "CA",
    "NEVP": "NV", "NWMT": "MT", "PACE": "UT", "PACW": "OR", "PGE": "OR", "PNM": "NM",
    "PSCO": "CO", "PSEI": "WA", "SCL": "WA", "SRP": "AZ", "TEPC": "AZ", "TIDC": "CA",
    "TPWR": "WA", "WACM": "CO", "WALC": "AZ", "WAUW": "MT",
}
# The 17 load regions of lwt_degree_days_daily, keyed like the BAs; the -TAC
# regions are CAISO's transmission access charge areas (PG&E, SCE, SDG&E in
# California; Valley Electric in Nevada).
LWT_HOME_STATES = {
    "AZPS": "AZ", "BANC": "CA", "BPAT": "OR", "EPE": "TX", "IPCO": "ID", "LADWP": "CA",
    "NEVP": "NV", "PACE": "UT", "PACW": "OR", "PGE-TAC": "CA", "PNM": "NM", "PSEI": "WA",
    "SCE-TAC": "CA", "SDGE-TAC": "CA", "SRP": "AZ", "TEPC": "AZ", "VEA-TAC": "NV",
}
# Snow and reservoir areas, pinned: (region, level, state or None, members).
# The Columbia unions are the pantry's (snow/basins.py): the index station
# counts banked on 2026-09-28 add up (Grand Coulee 44 + Mid-Columbia 34 +
# Snake 85 = The Dalles 163; Upper Snake 61 + Lower Snake 24 = Snake 85).
_SNOW_PLACES = {
    "columbia_above_the_dalles": ("pacific_northwest", "aggregate", None,
                                  ("snow:col_above_grand_coulee", "snow:col_mid_tributaries",
                                   "snow:snake")),
    "col_above_grand_coulee": ("pacific_northwest", "basin", None, None),
    "col_mid_tributaries": ("pacific_northwest", "basin", None, None),
    "snake": ("pacific_northwest", "basin", None, ("snow:snake_upper", "snow:snake_lower")),
    "snake_upper": ("pacific_northwest", "basin", None, None),
    "snake_lower": ("pacific_northwest", "basin", None, None),
    "col_canada": ("canada", "aggregate", None, None),
    "ca_state": ("california", "aggregate", "CA",
                 ("snow:ca_north", "snow:ca_central", "snow:ca_south")),
    "ca_north": ("california", "region", "CA", None),
    "ca_central": ("california", "region", "CA", None),
    "ca_south": ("california", "region", "CA", None),
}
# A region's aggregate area per data type, or None with the reason. "oregon" is
# a state inside the Pacific Northwest, listed because the captain names it.
REGION_VIEWS = (
    ("california", "California", None),
    ("pacific_northwest", "Pacific Northwest", None),
    ("oregon", "Oregon", "pacific_northwest"),
    ("canada", "Canada (Columbia)", None),
    ("southwest", "Southwest", None),
    ("rockies", "Rockies", None),
)
REGION_AGGREGATES = {
    ("california", "snow_water_equivalent"): ("snow:ca_state", None),
    ("california", "reservoir_storage"): ("reservoir:ca_major8", None),
    ("pacific_northwest", "snow_water_equivalent"): (
        "snow:columbia_above_the_dalles",
        "the Columbia above The Dalles, US side: the Pacific Northwest's snow as banked; "
        "Oregon west of the Cascades and Washington's coast are not in it"),
    ("pacific_northwest", "reservoir_storage"): (None, "no Pacific Northwest reservoir "
                                                       "storage is banked"),
    ("oregon", "snow_water_equivalent"): (None, "no Oregon snow index is banked (d091544)"),
    ("oregon", "reservoir_storage"): (None, "no Oregon reservoir storage is banked"),
    ("canada", "snow_water_equivalent"): ("snow:col_canada", None),
}
NO_AGGREGATE = "no aggregate area is served for this data type in this region"


def area_place(area: str, metadata_stations: Sequence[Mapping] = ()) -> dict:
    """{region, level, state?, members?, members_note?, region_basis?,
    region_absence?} for an area, from the pinned tables above (never the id)."""
    kind, ident = area.split(":", 1)
    out: dict[str, Any] = {}

    def _by_state(st: Optional[str], basis: Optional[str] = None) -> None:
        reg = STATE_REGIONS.get(st) if st else None
        out["region"] = reg
        if reg is None:
            out["region_absence"] = (f"state {st} is not in the region table" if st
                                     else "no state is banked for this area")
        if basis and reg is not None:
            out["region_basis"] = basis

    if kind == "station":
        meta = next((m for m in metadata_stations if m["station_id"] == ident), None)
        st = meta.get("state") if meta else None
        _by_state(st)
        out["level"] = "station"
        if st:
            out["state"] = st
    elif kind in ("lwt", "ba"):
        st = (LWT_HOME_STATES if kind == "lwt" else BA_HOME_STATES).get(ident)
        _by_state(st, "home state")
        out["level"] = "balancing_area"
        if st:
            out["state"] = st
    elif kind == "snow":
        reg, lvl, st, members = _SNOW_PLACES[ident]
        out.update(region=reg, level=lvl)
        if st:
            out["state"] = st
        if members:
            out["members"] = list(members)
    elif kind == "reservoir":
        out["region"] = "california"
        if ident == MAJOR8:
            out["level"] = "aggregate"
        elif ident in RESERVOIR_REGION_IDS:
            out["level"] = "region"
        else:
            out["level"] = "reservoir"
        out["state"] = "CA"
        if ident in RESERVOIR_MEMBERS:
            out["members"] = [f"reservoir:{i}" for i in RESERVOIR_MEMBERS[ident]]
        if ident == MAJOR8:
            out["members_note"] = MAJOR8_MEMBERS_NOTE
    return {k: out[k] for k in AREA_ADDED_KEYS if k in out}     # one key order


# The keys d091542 added to /areas, after each row's existing ones (a station's
# `state` was already there, before `vars`).
AREA_ADDED_KEYS = ("region", "level", "state", "members", "members_note", "region_basis",
                   "region_absence")
VAR_ADDED_KEYS = ("data_type", "measure")


def area_label(area: str, metadata_stations: Sequence[Mapping] = ()) -> str:
    """The label /areas gives an area (a station: its display name, or its id
    when station_metadata.json does not carry it)."""
    kind, ident = area.split(":", 1)
    if kind == "station":
        m = next((m for m in metadata_stations if m["station_id"] == ident), None)
        return (m.get("display_name") or m.get("metro")) if m else ident
    if kind == "snow":
        return {**SNOW_LABELS, **CANADA_SNOW_LABELS, **CA_SNOW_LABELS}[ident]
    if kind == "reservoir":
        return RESERVOIR_LABELS[ident]
    return ident


def var_type(var: str) -> dict:
    data_type, measure = VAR_DATA_TYPES[var]
    return {"data_type": data_type, "measure": measure}


def build_regions(areas: Sequence[Mapping]) -> dict:
    """The top-level `regions` block of /areas: each region's label, its
    aggregate area per data type (null with a reason), and its member areas."""
    out = {}
    for reg, lab, within in REGION_VIEWS:
        if within is None:
            members = [a["area"] for a in areas if a.get("region") == reg]
        else:   # a state inside a region: the region's areas in that state
            members = [a["area"] for a in areas
                       if a.get("region") == within and a.get("state") == "OR"]
        aggregates, notes = {}, {}
        for dt in DATA_TYPES:
            area, why = REGION_AGGREGATES.get((reg, dt), (None, NO_AGGREGATE))
            aggregates[dt] = area
            if why is not None:
                notes[dt] = why
        out[reg] = {"label": lab, "kind": "state" if within else "region",
                    "within": within, "aggregates": aggregates,
                    "aggregate_notes": notes, "members": members}
    return out


def in_progress_season(var: str, frontier: Optional[date]) -> Optional[int]:
    """The season in progress on a series whose newest valued day is
    `frontier`: the season holding the frontier, unless the frontier is that
    season's last day (or in no season). The ONE rule the payload's base and
    /areas' counts both use: a count over "every season with a row" otherwise
    counts the season in progress (d091542)."""
    if frontier is None:
        return None
    s = season_of(var, frontier)
    if s is not None and frontier < bounds(var, s)[1]:
        return s
    return None


def reservoir_series(area: str) -> str:
    """`reservoir:{id}` -> `{id}` (the series name)."""
    return area.split(":", 1)[1]


def ca_snow_series(area: str) -> str:
    """`snow:ca_{region}` -> `{region}_avg_swc`."""
    return area.split(":", 1)[1].removeprefix("ca_") + "_avg_swc"


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
    "swe": ("water_year", (10, 1), (9, 30), "% of normal peak"),
    "storage": ("water_year", (10, 1), (9, 30), "TAF"),
    "swe_in": ("water_year", (10, 1), (9, 30), "in"),
    "peak_load": ("water_year", (10, 1), (9, 30), "MW"),
    "peak_load_7d": ("water_year", (10, 1), (9, 30), "MW"),
}

_MODES = {"precip": CUMULATIVE, "hdd": CUMULATIVE, "cdd": CUMULATIVE, "swe": LEVEL,
          "storage": LEVEL, "swe_in": LEVEL, "peak_load": LEVEL, "peak_load_7d": LEVEL}

# §2 (d091522): the per-variable floor. var -> (min_days_frac, count window as
# ((m, d), (m, d)) or None for the whole season). A var not here is strict:
# every calendar day of the season valued.
# d091536: the same table may key one area's var, (area, var), which wins over
# the var's own entry (`floor`). The Canadian Columbia's `swe` counts Nov 1 ->
# May 31, every day of it: the BC stations report nothing while the pillow is
# bare, so a summer day without a row is the source's off-season. The US
# basins' `swe` stays strict (no entry).
FLOORS = {
    "storage": (0.90, None),
    "swe_in": (0.90, ((12, 1), (5, 31))),
    "peak_load": (0.90, None),            # d091525: d091522's rule, every day counts
    "peak_load_7d": (0.90, None),
    ("snow:col_canada", "swe"): (1.0, ((11, 1), (5, 31))),
}
# What the payload says, in words, about an area's own window (`base.rule`,
# `source.method`).
WINDOW_NOTES = {
    ("snow:col_canada", "swe"): ("a season qualifies when every day from Nov 1 through "
                                 "May 31 carries a value; summer days with no value are "
                                 "not reported by the source (the snow pillows are bare) "
                                 "and are gaps on the chart, not zeros"),
}


def floor(var: str, area: Optional[str] = None) -> Optional[tuple]:
    """FLOORS' entry for (area, var), else for var, else None (strict)."""
    return FLOORS.get((area, var)) or FLOORS.get(var)


# d091550 (D-09-25-86): the (area kind, var) pairs summed over the days that
# reported. A missing day there is most often a dry day nobody wrote down, so
# the sum is a close lower bound; HDD and CDD carry degrees every day and are
# not here.
CARRIED = {("station", "precip")}
# Chosen, not measured: nothing banked says 5 rather than 4 (§1.4).
PRECIP_MISSING_TOLERANCE = 5
MISSING_DAYS_LISTED = 31
# STOP-B (§2.5) fired on 2026-10-01: three stations' Sep 30 median moves more
# than 5 % when the 1-5-day seasons join the base. The rule is therefore on for
# the season in progress only; True also carries and admits past seasons.
PRECIP_TOLERANT_BASE = False


def carried(var: str, area: Optional[str]) -> bool:
    """Is (area, var) summed over its reported days (CARRIED)?"""
    return area is not None and (area_kind(area), var) in CARRIED


def tolerant_base(var: str, area: Optional[str]) -> bool:
    """Do past seasons of (area, var) carry and qualify by the tolerance?"""
    return PRECIP_TOLERANT_BASE and carried(var, area)


def carried_basis(days_missing: int) -> str:
    return f"sum of reported days; {days_missing} missing"


STAT_MIN_DAY_N = 3          # fewer contributing seasons on a day -> null that day
RANGE_MIN_N = 5
OUTLOOK_DAYS = ("04-01", "07-01")

LEAP_RULE = ("the axis has no Feb 29; in a leap year Feb 29's value is added into "
             "the Feb 28 day, so a season's final is the sum of all its calendar days")
LEAP_RULE_LEVEL = ("the axis has no Feb 29; the Feb 28 slot holds Feb 28's own value and "
                   "Feb 29 is added to nothing, but it still counts toward completeness "
                   "(a leap water year needs 366 valued days)")

MAPPING = {
    "precip": "water year N (Oct 1 N-1 → Sep 30 N) → ENSO year N-1",
    "hdd": "heating season E-(E+1) (Nov 1 E → Mar 31 E+1) → ENSO year E",
    "cdd": "cooling season Y (May 1 → Sep 30 Y) → ENSO year Y (the ENSO room's JJA rule, band_year - 0)",
    "swe": "water year N (Oct 1 N-1 → Sep 30 N) → ENSO year N-1",
    "storage": "water year N (Oct 1 N-1 → Sep 30 N) → ENSO year N-1",
    "swe_in": "water year N (Oct 1 N-1 → Sep 30 N) → ENSO year N-1",
    "peak_load": "water year N (Oct 1 N-1 → Sep 30 N) → ENSO year N-1",
    "peak_load_7d": "water year N (Oct 1 N-1 → Sep 30 N) → ENSO year N-1",
}

# `source.method`, one sentence per (area kind, var).
METHODS = {
    ("station", "precip"): ("GHCNd daily precipitation at the station, summed from the "
                            "season's first day over the days that reported. In the season "
                            "in progress a missing or null day adds nothing and does not "
                            "stop the sum; the season says how many days are missing, and "
                            "with more than 5 the readout is withheld. A past season joins "
                            "the base only when every day reported."),
    ("station", "hdd"): ("Station daily heating degree days (base 65 °F, GHCNd basis), summed "
                         "from the season's first day; a day without both extremes stops the sum."),
    ("station", "cdd"): ("Station daily cooling degree days (base 65 °F, GHCNd basis), summed "
                         "from the season's first day; a day without both extremes stops the sum."),
    ("lwt", "hdd"): ("The load region's daily heating degree days as banked in "
                     "lwt_degree_days_daily, summed from the season's first day; a missing "
                     "day stops the sum."),
    ("lwt", "cdd"): ("The load region's daily cooling degree days as banked in "
                     "lwt_degree_days_daily, summed from the season's first day; a missing "
                     "day stops the sum."),
    ("snow", "swe"): ("Snow water equivalent at the basin's index stations, summed and "
                      "divided by the sum of their 1991–2020 median peaks; a day needs 80 % "
                      "of the index stations reporting."),
    ("reservoir", "storage"): ("The reservoir's daily storage as CDEC reports it, in thousand "
                               "acre-feet; a missing day is a gap in the line on that day only."),
    ("major8", "storage"): ("The sum of the eight reservoirs' daily storage, in thousand "
                            "acre-feet; a day carries a value only when all eight report "
                            "(never renormalised)."),
    ("reservoir_region", "storage"): ("The sum of the region's member reservoirs' daily "
                                      "storage, in thousand acre-feet; a day carries a value "
                                      "only when every member reports (never renormalised)."),
    ("snow", "swe_in"): ("CDEC's regional average snow water content, in inches; the feed "
                         "reports the snow season only, so a summer day has no row."),
    ("ba", "peak_load"): ("The balancing area's maximum hourly load (EIA-930) on each UTC−8 "
                          "day, in MW; a day with fewer than 20 of its 24 hours is null."),
    ("ba", "peak_load_7d"): ("The mean of the balancing area's daily peak load over the "
                             "trailing seven UTC−8 days, in MW; null unless all seven "
                             "days carry a peak."),
}
# ... and its sentence when PRECIP_TOLERANT_BASE admits past seasons too.
METHOD_PRECIP_TOLERANT = ("GHCNd daily precipitation at the station, summed from the "
                          "season's first day over the days that reported: a missing or "
                          "null day adds nothing and does not stop the sum. The season says "
                          "how many days are missing; with more than 5 the readout is "
                          "withheld, and a past season joins the base only with 5 or fewer.")
_DATASETS = {("station", "precip"): "ghcnd_weather_daily",
             ("station", "hdd"): "station_degree_days_daily",
             ("station", "cdd"): "station_degree_days_daily",
             ("lwt", "hdd"): LWT_DATASET, ("lwt", "cdd"): LWT_DATASET,
             ("snow", "swe"): SNOW_DATASET,
             ("reservoir", "storage"): RESERVOIR_DATASET,
             ("major8", "storage"): RESERVOIR_DATASET,
             ("reservoir_region", "storage"): RESERVOIR_DATASET,
             ("snow", "swe_in"): CA_SNOW_DATASET,
             ("ba", "peak_load"): LOAD_DATASET, ("ba", "peak_load_7d"): LOAD_DATASET}
# The frontier row's meta keys `source` carries for a snow area.
SNOW_SOURCE_META = ("n_index", "n_reporting", "normals_version")

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


def mode(var: str) -> str:
    return _MODES[var]


def label(var: str, s: int) -> str:
    if _SEASONS[var][0] == "water_year":
        return f"WY{s + 1}"
    if var == "hdd":
        return f"{s}-{(s + 1) % 100:02d}"
    return str(s)


def label_year(var: str, s: int) -> int:
    """The year a season's label names (WY N -> N; 1991-92 -> 1991; 2016 -> 2016)."""
    return s + 1 if _SEASONS[var][0] == "water_year" else s


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
    out = {"kind": kind, "start_md": f"{sm:02d}-{sd:02d}",
           "end_md": f"{em:02d}-{ed:02d}", "days": len(axis(var)),
           "leap_rule": LEAP_RULE_LEVEL if mode(var) == LEVEL else LEAP_RULE,
           "mode": mode(var)}
    if var in LOAD_VARS:            # d091525: what a load day is, on the load vars only
        out["day_rule"] = DAY_RULE_7D if var == "peak_load_7d" else DAY_RULE
    return out


def units(var: str) -> str:
    return _SEASONS[var][3]


def days_in_window(var: str, s: int) -> int:
    a, b = bounds(var, s)
    return (b - a).days + 1


def count_window(var: str, s: int, area: Optional[str] = None) -> tuple[date, date]:
    """(first, last) day of the days that count toward a season's completeness:
    the whole season, or a floored var's own window (swe_in: Dec 1 -> May 31;
    snow:col_canada's swe: Nov 1 -> May 31)."""
    win = (floor(var, area) or (None, None))[1]
    if win is None:
        return bounds(var, s)
    start, end = bounds(var, s)
    (am, ad), (bm, bd) = win
    a = date(s, am, ad) if date(s, am, ad) >= start else date(s + 1, am, ad)
    b = date(a.year if (bm, bd) >= (am, ad) else a.year + 1, bm, bd)
    return a, b


def window_days(var: str, s: int, area: Optional[str] = None) -> int:
    a, b = count_window(var, s, area)
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
    """/areas order (§1): stations, LWT, the Columbia snow basins, the Canadian
    Columbia (d091536), California's
    four snow areas, the eight reservoirs' sum, the eight north to south, then
    the 28 balancing areas A→Z (d091525)."""
    return ([f"station:{sid}" for sid, _ in station_order(metadata_stations)]
            + [f"lwt:{ba}" for ba in LWT_BAS]
            + [f"snow:{b}" for b in SNOW_BASINS + CANADA_SNOW]
            + [f"snow:{r}" for r in CA_SNOW]
            + [f"reservoir:{r}" for r in RESERVOIR_AREAS]
            + [f"ba:{b}" for b in BAS])


_KIND_VARS = {"station": ("precip", "hdd", "cdd"), "lwt": ("hdd", "cdd"), "snow": ("swe",),
              "reservoir": ("storage",), "ba": LOAD_VARS}
_VARS_SERVED = ("station: areas carry precip, hdd, cdd; lwt: areas carry hdd, cdd; "
                "snow: areas carry swe on the Columbia basins and swe_in on California's "
                "(snow:ca_*); reservoir: areas carry storage; ba: areas carry "
                "peak_load, peak_load_7d")


def vars_for(area: str) -> tuple[str, ...]:
    kind, ident = area.split(":", 1)
    if kind == "snow" and ident in CA_SNOW:
        return ("swe_in",)
    return _KIND_VARS[kind]


def area_kind(area: str) -> str:
    """The (kind) key of METHODS / _DATASETS: the eight's sum is its own kind,
    and so is a regional composite (d091542)."""
    kind, ident = area.split(":", 1)
    if kind == "reservoir" and ident == MAJOR8:
        return "major8"
    if kind == "reservoir" and ident in RESERVOIR_REGION_IDS:
        return "reservoir_region"
    return kind


def parse_request(area: Optional[str], var: Optional[str], classifier: Optional[str],
                  metadata_stations: Sequence[Mapping]) -> tuple[str, str, str]:
    """-> (area, var, classifier), or ValueError naming the vocabulary."""
    vocab = area_vocabulary(metadata_stations)
    if not area or area not in vocab:
        raise ValueError(f"unknown area {area!r}; allowed: station:{{GHCN id}} for "
                         + ", ".join(sid for sid, _ in station_order(metadata_stations))
                         + "; lwt:{BA} for " + ", ".join(LWT_BAS)
                         + "; snow:{basin} for " + ", ".join(SNOW_BASINS + CANADA_SNOW)
                         + "; snow:{California region} for " + ", ".join(CA_SNOW)
                         + "; reservoir:{id} for " + ", ".join(RESERVOIR_AREAS)
                         + "; ba:{code} for " + ", ".join(BAS))
    if var not in VARS:
        raise ValueError(f"unknown var {var!r}; allowed: " + ", ".join(VARS))
    if var not in vars_for(area):
        raise ValueError(f"var {var!r} is not served for {area}; {_VARS_SERVED}")
    classifier = classifier or DEFAULT_CLASSIFIER
    if classifier not in CLASSIFIERS:
        raise ValueError(f"unknown classifier {classifier!r}; allowed: "
                         + ", ".join(CLASSIFIERS))
    return area, var, classifier


def build_areas(metadata_stations: Sequence[Mapping],
                counts: Mapping[tuple[str, str], Mapping[int, int]],
                frontiers: Optional[Mapping[str, Optional[str]]] = None,
                lasts: Optional[Mapping[tuple[str, str], Optional[date]]] = None) -> dict:
    """`counts[(area, var)][season start year] = days carrying a value` (inside
    the count window: swe_in counts Dec 1 -> May 31 only, snow:col_canada's
    swe Nov 1 -> May 31), over every
    season with at least one row. A season is complete iff that count equals
    its window's days; a floored var (§2) also says how many qualify.
    `frontiers[area]` is a balancing area's newest qualifying day (d091525), so
    a series that stopped early says where.

    d091542: `lasts[(area, var)]` is the series' newest valued day; the season
    it holds, when in progress (`in_progress_season`, the payload's own rule),
    is left out of `complete_seasons` and `qualifying_seasons`. Each var gains
    `data_type` and `measure`, each area `region`, `level` and where one
    applies `state`, `members`, `members_note`, `region_basis` or
    `region_absence`, after its existing keys; the body gains `regions`.

    d091550: with PRECIP_TOLERANT_BASE, station precipitation also says
    `qualifying_seasons` (at most 5 days missing); `complete_seasons` is
    unchanged either way."""
    areas = []
    lasts = lasts or {}

    def _vars(area: str) -> list[dict]:
        out = []
        for v in vars_for(area):
            per = counts.get((area, v), {})
            first = min(per) if per else None
            fl = floor(v, area)
            cur = in_progress_season(v, lasts.get((area, v)))
            done = {s: n for s, n in per.items() if s != cur}
            row = {
                "var": v, "season": _SEASONS[v][0], "units": units(v),
                "first_season": label(v, first) if first is not None else None,
                "complete_seasons": sum(1 for s, n in done.items()
                                        if n == window_days(v, s, area)),
            }
            if fl is not None:
                row["qualifying_seasons"] = sum(1 for s, n in done.items()
                                                if n >= fl[0] * window_days(v, s, area))
            elif tolerant_base(v, area):    # d091550: at most 5 days missing
                row["qualifying_seasons"] = sum(
                    1 for s, n in done.items()
                    if window_days(v, s, area) - n <= PRECIP_MISSING_TOLERANCE)
            row.update(var_type(v))
            out.append(row)
        return out

    def _add(row: dict) -> None:
        row.update(area_place(row["area"], metadata_stations))
        areas.append(row)

    for sid, meta in station_order(metadata_stations):
        area = f"station:{sid}"
        row = {"area": area, "kind": "station",
               "label": (meta.get("display_name") or meta.get("metro")) if meta else sid}
        if meta and meta.get("state"):
            row["state"] = meta["state"]
        row["vars"] = _vars(area)
        _add(row)
    for ba in LWT_BAS:
        area = f"lwt:{ba}"
        _add({"area": area, "kind": "lwt", "label": ba, "vars": _vars(area)})
    for b in SNOW_BASINS:
        area = f"snow:{b}"
        _add({"area": area, "kind": "snow", "label": SNOW_LABELS[b], "vars": _vars(area)})
    for b in CANADA_SNOW:
        area = f"snow:{b}"
        _add({"area": area, "kind": "snow", "label": CANADA_SNOW_LABELS[b],
              "vars": _vars(area)})
    for r in CA_SNOW:
        area = f"snow:{r}"
        _add({"area": area, "kind": "snow", "label": CA_SNOW_LABELS[r], "vars": _vars(area)})
    for r in RESERVOIR_AREAS:
        area = f"reservoir:{r}"
        _add({"area": area, "kind": "reservoir", "label": RESERVOIR_LABELS[r],
              "capacity_taf": CAPACITY_AF[r] / 1000.0, "vars": _vars(area)})
    for b in BAS:
        area = f"ba:{b}"
        _add({"area": area, "kind": "ba", "label": b,
              "frontier": (frontiers or {}).get(area), "vars": _vars(area)})
    return {"areas": areas, "regions": build_regions(areas)}


# ---------------------------------------------------------------------------
# The walk
# ---------------------------------------------------------------------------

_NO_ROW = object()


class _Walk:
    __slots__ = ("s", "values", "days_complete", "days_in_window", "first_missing",
                 "mode", "through", "days_missing", "peak", "peak_date",
                 "window", "window_complete", "window_days", "carried", "missing")

    @property
    def gap(self) -> bool:
        return self.first_missing is not None


def _walk(var: str, s: int, daily: Mapping[date, Optional[float]], limit: date,
          slots: Mapping[str, int], area: Optional[str] = None, *,
          carry: bool = False) -> _Walk:
    """Walk season `s` by calendar date from its first day through `limit`.

    Every day is looked up; nothing is iterated by row. CUMULATIVE: the running
    sum is written to each day's slot until the first missing day, after which
    the slot stays None. Feb 29 folds into the Feb 28 slot; if Feb 29 is the
    first missing day, the Feb 28 slot it would have completed is withdrawn too.
    `carry` (d091550, a CARRIED pair): `_walk_carried` instead.
    LEVEL: `_walk_level`. `area` picks an area's own count window (`floor`)."""
    start, end = bounds(var, s)
    w = _Walk()
    w.s = s
    w.values = [None] * len(slots)
    w.days_complete = 0
    w.days_in_window = (end - start).days + 1
    w.first_missing, w.mode, w.through = None, None, None
    w.days_missing, w.peak, w.peak_date = 0, None, None
    w.window = count_window(var, s, area)
    w.window_days = (w.window[1] - w.window[0]).days + 1
    w.window_complete = 0
    w.carried, w.missing = False, []
    if mode(var) == LEVEL:
        return _walk_level(w, start, daily, limit, slots)
    if carry:
        return _walk_carried(w, start, daily, limit, slots)
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


def _walk_carried(w: _Walk, start: date, daily: Mapping[date, Optional[float]],
                  limit: date, slots: Mapping[str, int]) -> _Walk:
    """D-09-25-86 §1.2-§1.3: a cumulative summed over the days that reported.
    A missing day adds nothing and does not stop the sum: its slot carries the
    running total unchanged (0.0 before any day has reported), and it is
    counted (`days_missing`) and listed (`missing`). Feb 29 folds into Feb 28
    as ever; a missing Feb 29 leaves the Feb 28 slot as Feb 28 wrote it.
    `through` is the newest valued day."""
    w.carried = True
    total = 0.0
    d = start
    while d <= limit:
        v = daily.get(d, _NO_ROW)
        leap = (d.month, d.day) == (2, 29)
        slot = slots["02-28" if leap else f"{d.month:02d}-{d.day:02d}"]
        if v is _NO_ROW or v is None:
            w.days_missing += 1
            w.missing.append(d)
            if w.first_missing is None:
                w.first_missing = d
                w.mode = ABSENT if v is _NO_ROW else INCOMPLETE
            if not leap:
                w.values[slot] = total
        else:
            w.days_complete += 1
            total += float(v)
            w.values[slot] = total
            w.through = d
        d += timedelta(days=1)
    return w


def _walk_level(w: _Walk, start: date, daily: Mapping[date, Optional[float]],
                limit: date, slots: Mapping[str, int]) -> _Walk:
    """A level: each valued day writes its own value to its own slot; a missing
    day leaves its slot None and nothing else. Feb 29 is on no slot (its value
    is not added into Feb 28) but counts: valued, it is a complete day; missing,
    it is a missing day. `through` is the newest valued day; `peak` the
    season's maximum over every valued calendar day (Feb 29 included), first
    occurrence on a tie.

    d091522: only a day inside the count window (`w.window`; the whole season
    but for swe_in's Dec 1 -> May 31 and snow:col_canada's Nov 1 -> May 31)
    can be missing. Outside it an absent day
    is the feed's off-season: it is not a gap and is counted nowhere."""
    a, b = w.window
    d = start
    while d <= limit:
        v = daily.get(d, _NO_ROW)
        inside = a <= d <= b
        if v is _NO_ROW or v is None:
            if not inside:
                d += timedelta(days=1)
                continue
            w.days_missing += 1
            if w.first_missing is None:
                w.first_missing = d
                w.mode = ABSENT if v is _NO_ROW else INCOMPLETE
        else:
            v = float(v)
            w.days_complete += 1
            w.window_complete += inside
            w.through = d
            if (d.month, d.day) != (2, 29):
                w.values[slots[f"{d.month:02d}-{d.day:02d}"]] = v
            if w.peak is None or v > w.peak:
                w.peak, w.peak_date = v, d
        d += timedelta(days=1)
    return w


def _complete(w: _Walk) -> bool:
    return w.first_missing is None and w.days_complete == w.days_in_window


def _qualifies(var: str, w: _Walk, area: Optional[str] = None) -> bool:
    """§2: a floored season (`floor`: the var's entry, or its area's (area,
    var) one) qualifies when >= min_days_frac of its count window's days carry
    a value; every other is strict (`_complete`).

    d091550: a carried walk of a CARRIED pair, with PRECIP_TOLERANT_BASE,
    qualifies with at most PRECIP_MISSING_TOLERANCE missing days."""
    fl = floor(var, area)
    if tolerant_base(var, area) and w.carried:
        return w.days_missing <= PRECIP_MISSING_TOLERANCE
    if fl is None:
        return _complete(w)
    return w.window_complete >= fl[0] * w.window_days


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
    """The one rounding, at the edge: 0.1 (mm for precip, °F·day for HDD/CDD,
    TAF, inches). NaN — a null-aware statistic's empty day — is null."""
    if x is None:
        return None
    x = float(x)
    return None if np.isnan(x) else round(x, 1)


def _rv(xs: Iterable[Optional[float]]) -> list[Optional[float]]:
    return [_r(x) for x in xs]


def _season_block(var: str, w: _Walk, *, current: bool, area: Optional[str] = None) -> dict:
    level = mode(var) == LEVEL
    start, _ = bounds(var, w.s)
    out = {"season": label(var, w.s), "start": start.isoformat(),
           "through": w.through.isoformat() if w.through else None,
           "values": _rv(w.values)}
    if current:
        out["complete_to_date"] = not w.gap
    else:
        out["complete"] = _qualifies(var, w, area)
    if w.carried:   # d091550 §1.3: the season says how many days did not report
        out["within_tolerance"] = w.days_missing <= PRECIP_MISSING_TOLERANCE
        out["days_missing"] = w.days_missing
        out["missing_days"] = [d.isoformat() for d in w.missing[:MISSING_DAYS_LISTED]]
        out["first_missing"] = w.first_missing.isoformat() if w.first_missing else None
    out["absence"] = _gap_absence(w, level or w.carried) if w.gap else None
    return out


def _too_many_missing(w: _Walk) -> dict:
    return {"reason": "too_many_missing", "days_missing": w.days_missing,
            "tolerance": PRECIP_MISSING_TOLERANCE,
            "first_missing": w.first_missing.isoformat() if w.first_missing else None}


def _gap_absence(w: _Walk, level: bool = False) -> dict:
    out = {"reason": "gap", "first_missing": w.first_missing.isoformat(),
           "mode": w.mode, "days_complete": w.days_complete}
    if level:   # a level's (and a carried sum's) later days still stand
        out["days_missing"] = w.days_missing
    return out


def cone(B: np.ndarray) -> dict[str, np.ndarray]:
    """Per-day percentiles across the base rows: linear interpolation (NumPy's
    default, Hyndman-Fan type 7). p0 is the minimum, p100 the maximum."""
    return {f"p{q}": np.percentile(B, q, axis=0, method="linear") for q in PERCENTILES}


def mid_rank(value: float, sample: Sequence[float]) -> float:
    """100·(#below + ½·#equal)/n."""
    below = sum(1 for x in sample if x < value)
    equal = sum(1 for x in sample if x == value)
    return 100.0 * (below + 0.5 * equal) / len(sample)


def _nan_stats(B: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(per-day contributing count, mask of days with >= STAT_MIN_DAY_N)."""
    cnt = (~np.isnan(B)).sum(axis=0) if B.size else np.zeros(B.shape[1], dtype=int)
    return cnt, cnt >= STAT_MIN_DAY_N


def _masked(fn, B: np.ndarray, ok: np.ndarray, **kw) -> np.ndarray:
    """fn (a NumPy nan-function) per day where `ok`, NaN elsewhere. The empty
    days are never handed to NumPy, so no all-NaN warning and no 0."""
    out = np.full(B.shape[1], np.nan)
    if ok.any():
        out[ok] = fn(B[:, ok], axis=0, **kw)
    return out


def _n_by_day_min(cnt: np.ndarray, ok: np.ndarray) -> Optional[int]:
    """The fewest seasons contributing on any drawn day (a day with a value)."""
    return int(cnt[ok].min()) if ok.any() else None


def _at(arr: Optional[np.ndarray], i: int) -> Optional[float]:
    if arr is None:
        return None
    x = float(arr[i])
    return None if np.isnan(x) else x


def day_median(day: int, *, n: int, p50: Optional[np.ndarray],
               range_n: Optional[int] = None,
               range_median: Optional[np.ndarray] = None) -> tuple[Optional[float], Optional[str]]:
    """The median a readout and a snapshot row both read on a day, unrounded,
    and its basis in words (d091550 §2.3, one function so they cannot
    disagree): the cone's p50 when the base has n >= 30; else, on a level
    with 5 <= n < 30, the base's range median; else (None, None). A ranked
    percentile needs the cone, whichever median is read."""
    if p50 is not None:
        return _at(p50, day), f"the cone's p50 (n = {n} >= {CONE_MIN_N})"
    if range_median is not None:
        return _at(range_median, day), (f"the base's range median (n = {range_n}, "
                                        f"{RANGE_MIN_N} <= n < {CONE_MIN_N}); no percentile "
                                        f"below n = {CONE_MIN_N}")
    return None, None


def build_season(area: str, var: str, daily: Mapping[date, Optional[float]], *,
                 classifier: str, catalog_version: Optional[str],
                 developing: Optional[Mapping], bins: Sequence[Mapping],
                 oni_last_centre: Optional[tuple[int, int]] = None,
                 source_meta: Optional[Mapping] = None,
                 extras: Optional[dict] = None) -> dict:
    """The /api/weather/season body, keys in contract order.

    `daily` maps each calendar date that HAS A ROW to its value, None where the
    row carries no usable value (NULL, or basis_complete false). A date with no
    row is simply not a key. `bins` are enso_year_bins rows for `classifier`.
    `source_meta` is the frontier row's `meta` (Columbia snow areas), or for
    `reservoir:ca_major8` {"series_frontiers": {...}}.

    `extras` (d091542), when a dict, is filled with what the same build knows
    and the body does not carry, for the snapshot: `base` (the base seasons'
    labels), `B` (their per-day values, unrounded) and `values` (every
    season's walk by label, unrounded). The body is unchanged by it.
    """
    level = mode(var) == LEVEL
    fl = floor(var, area)
    floored = fl is not None         # §2: null-aware per-day statistics
    slots = _slot_index(var)
    ax = axis(var)
    ndays = len(ax)

    valued = [d for d, v in daily.items() if v is not None]
    frontier = max(valued) if valued else None

    # Seasons with any data (a row, valued or not), not starting after the frontier.
    seasons = sorted({s for d in daily for s in [season_of(var, d)]
                      if s is not None and (frontier is None or bounds(var, s)[0] <= frontier)})

    cur = in_progress_season(var, frontier)

    walks: dict[int, _Walk] = {}
    carries = carried(var, area)
    tolerant = tolerant_base(var, area)
    for s in seasons:
        limit = frontier if s == cur else bounds(var, s)[1]
        walks[s] = _walk(var, s, daily, limit, slots, area,
                         carry=carries and (s == cur or tolerant))
    past = [s for s in seasons if s != cur]

    # ── base ────────────────────────────────────────────────────────────────
    base = [s for s in past if _qualifies(var, walks[s], area)]
    if tolerant:
        excluded = [{"season": label(var, s), "days_complete": walks[s].days_complete,
                     "days_in_window": walks[s].days_in_window,
                     "first_missing": walks[s].first_missing.isoformat()
                     if walks[s].first_missing else None,
                     "days_missing": walks[s].days_missing}
                    for s in past if not _qualifies(var, walks[s], area)]
    elif floored:
        excluded = [{"season": label(var, s), "days_complete": walks[s].window_complete,
                     "days_in_window": walks[s].window_days,
                     "first_missing": walks[s].first_missing.isoformat()
                     if walks[s].first_missing else None}
                    for s in past if not _qualifies(var, walks[s], area)]
    else:
        excluded = [{"season": label(var, s), "days_complete": walks[s].days_complete,
                     "days_in_window": walks[s].days_in_window,
                     "first_missing": walks[s].first_missing.isoformat()
                     if walks[s].first_missing else None}
                    for s in past if not _complete(walks[s])]
    B = np.array([walks[s].values for s in base], dtype=np.float64).reshape(len(base), ndays)
    n = len(base)
    cnt, ok = _nan_stats(B) if floored else (None, None)

    if n >= CONE_MIN_N:
        if floored:
            pct = {f"p{q}": _masked(np.nanpercentile, B, ok, q=q, method="linear")
                   for q in PERCENTILES}
            percentiles = {"method": "linear (type 7)", "n": n,
                           "n_by_day_min": _n_by_day_min(cnt, ok),
                           **{k: _rv(v) for k, v in pct.items()}}
        else:
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
        if floored:
            Bn = B[normal_idx]
            ncnt, nok = _nan_stats(Bn)
            normal = {"window": f"{lo}-{hi}", "n": len(normal_idx),
                      "n_by_day_min": _n_by_day_min(ncnt, nok),
                      "values": _rv(_masked(np.nanmean, Bn, nok))}
        else:
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
        if floored:
            fcnt, fok = _nan_stats(F)
            five_mean = _masked(np.nanmean, F, fok)
            five_year = {"seasons": [label(var, s) for s in five], "n": FIVE_YEAR_N,
                         "n_by_day_min": _n_by_day_min(fcnt, fok), "mean": _rv(five_mean),
                         "min": _rv(_masked(np.nanmin, F, fok)),
                         "max": _rv(_masked(np.nanmax, F, fok))}
        else:
            five_mean = F.mean(axis=0)
            five_year = {"seasons": [label(var, s) for s in five], "mean": _rv(five_mean),
                         "min": _rv(F.min(axis=0)), "max": _rv(F.max(axis=0))}
        five_year_absence = None
    else:
        five_mean, five_year = None, None
        five_year_absence = {"reason": "fewer_than_five", "n": len(five)}

    # ── range (a level only): what happened, not a percentile ───────────────
    range_median = None             # unrounded, for `day_median`
    if level:
        range_, range_absence = range_block(var, B, [label(var, s) for s in base])
        if range_ is not None:
            _, rok = _nan_stats(B)
            range_median = _masked(np.nanmedian, B, rok)
    else:
        range_, range_absence = None, None

    # ── this season / last season ───────────────────────────────────────────
    if cur is not None:
        this_season = _season_block(var, walks[cur], current=True, area=area)
        this_season_absence = None
        last_s = max(past) if past else None
    else:
        this_season = None
        this_season_absence = {"reason": "between_seasons",
                               "frontier": frontier.isoformat() if frontier else None}
        last_s = max(past) if past else None
    if last_s is not None:
        last_season = _season_block(var, walks[last_s], current=False, area=area)
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
            if floored:
                ccnt, cok = _nan_stats(B[idx])
                med = _masked(np.nanmedian, B[idx], cok)
                block["n_by_day_min"] = _n_by_day_min(ccnt, cok)
            else:
                med = np.median(B[idx], axis=0)
            cat_medians[c] = med
            block["median"] = _rv(med)
        else:
            cat_medians[c] = None
            block["median"] = None
            block["absence"] = {"reason": "small_n", "n": len(idx)}
        if level:
            ol = outlook([walks[base[i]] for i in idx], var, slots)
            block["outlook"] = ol
            if ol is None:
                block["outlook_absence"] = {"reason": "small_n", "n": len(idx)}
        categories[c] = block
    enso = {"classifier": classifier, "catalog_version": catalog_version,
            "mapping": MAPPING[var], "developing": developing, "categories": categories,
            "now": enso_now(developing, classifier)}

    # ── readout ─────────────────────────────────────────────────────────────
    day: Optional[int] = None          # the like-for-like day for years[].to_date
    readout, readout_absence = None, None
    if cur is not None:
        w = walks[cur]
        if w.through is not None:
            day = slots["02-28" if (w.through.month, w.through.day) == (2, 29)
                        else f"{w.through.month:02d}-{w.through.day:02d}"]
        if level and day is not None:
            # A level's readout is the frontier's own value (its own day, even a
            # Feb 29, whose statistics are read on the Feb 28 slot). The
            # frontier is by definition valued; an earlier gap withholds nothing.
            value = float(daily[w.through])
        elif w.carried and day is not None:
            # §1.4: summed over the reported days, given up to the tolerance.
            if w.days_missing > PRECIP_MISSING_TOLERANCE:
                readout_absence, value = _too_many_missing(w), None
            else:
                value = w.values[day]
        elif w.gap or day is None:
            readout_absence = _gap_absence(w) if w.gap else {"reason": "no_data"}
            value = None
        else:
            value = w.values[day]
    elif last_s is not None:
        w = walks[last_s]
        day = ndays - 1
        if w.carried:
            if w.days_missing > PRECIP_MISSING_TOLERANCE:
                readout_absence, value = _too_many_missing(w), None
            else:
                value = w.values[day]
        elif level:
            value = w.values[day]
            if value is None:           # withheld only when that day is missing
                readout_absence = (_gap_absence(w, level) if w.gap
                                   else {"reason": "incomplete"})
        elif _complete(w):
            value = w.values[day]
        else:
            readout_absence = _gap_absence(w) if w.gap else {"reason": "incomplete"}
            value = None
    else:
        value = None
        readout_absence = {"reason": "no_data"}

    if value is not None:
        # §2.3 (d091550): the snapshot's median rule, one function for both.
        median, median_basis = day_median(day, n=n, p50=p50,
                                          range_n=range_["n"] if range_ else None,
                                          range_median=range_median)
        five_at = _at(five_mean, day)
        sample = [x for x in B[:, day].tolist() if not np.isnan(x)]
        vs_cat = {c: (_r(value - m) if (m := _at(cat_medians[c], day)) is not None
                      else None) for c in CATEGORIES}
        readout = {
            "day": day,
            "value": _r(value),
            "median": _r(median),
            "pct_of_median": (_r(100.0 * value / median)
                              if median is not None and median != 0 else None),
            # The same n >= 30 gate as the cone: no rank against a short record.
            "percentile": (_r(mid_rank(value, sample))
                           if p50 is not None and median is not None and sample else None),
            "vs_five_year": _r(value - five_at) if five_at is not None else None,
            "vs_category": vs_cat,
        }
        if level:   # d091542 §2.6: the day against the median peak, in one read
            mp = peak_base([walks[s] for s in base], slots)["median"]
            readout["pct_of_median_peak"] = (_r(100.0 * value / mp)
                                             if mp is not None and mp != 0 else None)
            readout["median_peak"] = mp
            readout["median_basis"] = median_basis if median is not None else None
        if w.carried:   # d091550 §1.4
            readout["days_missing"] = w.days_missing
            readout["basis"] = carried_basis(w.days_missing)

    # ── years / curves ──────────────────────────────────────────────────────
    years = []
    for s in seasons:
        w = walks[s]
        cats, why = klass[s]
        complete = s != cur and _qualifies(var, w, area)
        has_peak = level and (complete or s == cur) and w.peak is not None
        years.append({
            "season": label(var, s), "enso_year": enso_year(var, s),
            "categories": cats, "category_absence": why,
            "complete": complete,
            "to_date": _r(w.values[day]) if day is not None else None,
            # A level's last day (Sep 30: bare ground) says nothing: no final.
            "final": _r(w.values[-1]) if complete and not level else None,
            "peak": _r(w.peak) if has_peak else None,
            "peak_md": _md(w.peak_date) if has_peak else None,
        })
    if floored:     # §2.5: every qualifying season, the member paths the page draws
        curves = {label(var, s): _rv(walks[s].values) for s in base}
    else:
        curves = {label(var, s): _rv(walks[s].values) for s in base
                  if enso_year(var, s) in bins_by_year and enso_year(var, s) not in opened}

    # ── peak (a level only) ─────────────────────────────────────────────────
    if level:
        peak = {"this_season": _peak_of(walks[cur]) if cur is not None else None,
                "last_season": (_peak_of(walks[last_s])
                                if last_s is not None and _qualifies(var, walks[last_s], area)
                                else None),
                "base": peak_base([walks[s] for s in base], slots)}
        peak_absence = None
    else:
        peak, peak_absence = None, {"reason": "not_a_level"}

    if floored:
        lo_frac, win = fl
        note = WINDOW_NOTES.get((area, var))
        base_block = {
            "rule": ("every qualifying season, full record, excluding the current season: "
                     f">= {lo_frac:.2f} of the days in the window carry a value"
                     + (f"; {note}" if note else "")),
            "min_days_frac": lo_frac,
            "window": ({"start_md": f"{win[0][0]:02d}-{win[0][1]:02d}",
                        "end_md": f"{win[1][0]:02d}-{win[1][1]:02d}"} if win
                       else {"start_md": season_meta(var)["start_md"],
                             "end_md": season_meta(var)["end_md"]}),
            "seasons": [label(var, s) for s in base], "n": n,
            "n_by_day_min": _n_by_day_min(cnt, ok), "excluded": excluded}
    elif tolerant:
        base_block = {"rule": ("every season with at most "
                               f"{PRECIP_MISSING_TOLERANCE} missing days, summed over the "
                               "days that reported, full record, excluding the current season"),
                      "tolerance": PRECIP_MISSING_TOLERANCE,
                      "seasons": [label(var, s) for s in base], "n": n, "excluded": excluded}
    else:
        base_block = {"rule": "every complete season, full record, excluding the current season",
                      "seasons": [label(var, s) for s in base], "n": n, "excluded": excluded}

    if extras is not None:
        extras["base"] = [label(var, s) for s in base]
        extras["B"] = B
        extras["values"] = {label(var, s): list(walks[s].values) for s in seasons}
        # the day statistics unrounded, so a ratio is taken before the one rounding
        extras["p50"] = p50
        extras["range_median"] = range_median
        extras["cat_medians"] = cat_medians
        # d091550: each carried season's missing days, for the snapshot's count
        extras["missing"] = {label(var, s): list(walks[s].missing) for s in seasons
                             if walks[s].carried}

    body = {
        "area": area, "var": var, "units": units(var),
        "season": season_meta(var),
        "frontier": frontier.isoformat() if frontier else None,
        "axis": ax,
        "base": base_block,
        "percentiles": percentiles, "percentiles_absence": percentiles_absence,
        "normal": normal, "normal_absence": normal_absence,
        "five_year": five_year, "five_year_absence": five_year_absence,
    }
    if level:       # §2.4: `range` rides after five_year_absence, on a level only
        body["range"], body["range_absence"] = range_, range_absence
    body.update({
        "this_season": this_season, "this_season_absence": this_season_absence,
        "last_season": last_season, "last_season_absence": last_season_absence,
        "enso": enso,
        "years": years,
        "curves": curves,
        "readout": readout, "readout_absence": readout_absence,
        "peak": peak, "peak_absence": peak_absence,
        "source": source(area, var, source_meta),
    })
    return body


def range_block(var: str, B: np.ndarray, labels: Sequence[str]) -> tuple[Optional[dict], Optional[dict]]:
    """§2.4: per-day min / median / max over the base seasons valued that day
    (null where fewer than 3), with n and the seasons' labels; null below
    n = 5. A range of what happened, never called a percentile."""
    n = len(labels)
    if n < RANGE_MIN_N:
        return None, {"reason": "small_n", "n": n}
    cnt, ok = _nan_stats(B)
    return ({"n": n, "n_by_day_min": _n_by_day_min(cnt, ok), "seasons": list(labels),
             "min": _rv(_masked(np.nanmin, B, ok)),
             "median": _rv(_masked(np.nanmedian, B, ok)),
             "max": _rv(_masked(np.nanmax, B, ok))}, None)


def _mmm(xs: Sequence[float]) -> dict:
    """min / median / max, nulls below STAT_MIN_DAY_N values. Never a percentile."""
    if len(xs) < STAT_MIN_DAY_N:
        return {"min": None, "median": None, "max": None}
    a = np.array(xs, dtype=np.float64)
    return {"min": _r(a.min()), "median": _r(np.median(a)), "max": _r(a.max())}


def outlook(members: Sequence[_Walk], var: str, slots: Mapping[str, int]) -> Optional[dict]:
    """§3: a category's range — its member seasons' peaks and their values on
    Apr 1 and Jul 1, as min / median / max (whatever n: never a percentile).
    None when n < 3 (the caller states `small_n`)."""
    if len(members) < CATEGORY_MIN_N:
        return None
    ax = {i: md for md, i in slots.items()}
    peaked = [w for w in members if w.peak is not None]
    pk = _mmm([w.peak for w in peaked])
    if pk["median"] is not None:
        idx = sorted(slots["02-28" if (w.peak_date.month, w.peak_date.day) == (2, 29)
                           else _md(w.peak_date)] for w in peaked)
        pk["median_md"] = ax[idx[(len(idx) - 1) // 2]]      # the lower median: a whole day
    else:
        pk["median_md"] = None
    on_day = {}
    for md in OUTLOOK_DAYS:
        xs = [w.values[slots[md]] for w in members if w.values[slots[md]] is not None]
        on_day[md] = {**_mmm(xs), "n": len(xs)}
    return {"n": len(members), "seasons": [label(var, w.s) for w in members],
            "peak": pk, "on_day": on_day,
            "members": [{"season": label(var, w.s), "peak": _r(w.peak),
                         "peak_md": _md(w.peak_date) if w.peak_date else None,
                         "apr1": _r(w.values[slots["04-01"]]),
                         "jul1": _r(w.values[slots["07-01"]])} for w in members]}


_KIND_NAMES = {"nino": "El Niño", "nina": "La Niña"}
_INDEX_NAMES = {"cpc_oni": "ONI", "roni": "RONI"}
NOW_CAVEAT = ("a label for the current state, read from CPC's index as the catalog "
              "banks it; not a forecast")


def enso_now(developing: Optional[Mapping], classifier: str) -> dict:
    """§3 `enso.now`: from the catalog run's `developing` and nothing else.
    |latest_oni| >= 1.5 strong, >= 1.0 moderate, >= 0.5 weak; no `developing`
    -> neutral. (A `developing` below 0.5, or without an ONI, is its bare kind.)"""
    if not developing or not developing.get("kind"):
        return {"category": "neutral",
                "basis": f"no developing event in the catalog run; {NOW_CAVEAT}"}
    kind = developing["kind"]
    oni = developing.get("latest_oni")
    cat = kind
    if oni is not None:
        mag = abs(float(oni))
        for floor, strength in ((1.5, "strong"), (1.0, "moderate"), (0.5, "weak")):
            if mag >= floor:
                cat = f"{kind}_{strength}"
                break
    parts = [f"{_KIND_NAMES.get(kind, kind)} developing"]
    if oni is not None:
        season_ = developing.get("latest_season")
        parts.append(f"latest {_INDEX_NAMES.get(classifier, 'ONI')} {float(oni):g}"
                     + (f" ({season_})" if season_ else ""))
    if developing.get("n_seasons") is not None:
        parts.append(f"{developing['n_seasons']} seasons")
    return {"category": cat, "basis": ", ".join(parts) + f"; {NOW_CAVEAT}"}


def _md(d: date) -> str:
    return f"{d.month:02d}-{d.day:02d}"


def _peak_of(w: _Walk) -> Optional[dict]:
    if w.peak is None:
        return None
    return {"value": _r(w.peak), "date": w.peak_date.isoformat()}


def peak_base(ws: Sequence[_Walk], slots: Mapping[str, int]) -> dict:
    """Statistics over the complete base seasons' peaks: the median, p10 and p90
    of the peak values (linear, as the cone) and `median_md`, the median peak
    day: the median of the peaks' axis slots (Feb 29 reads as Feb 28), rounded
    down to a whole day when n is even."""
    ws = [w for w in ws if w.peak is not None]
    n = len(ws)
    if n == 0:
        return {"median": None, "p10": None, "p90": None, "median_md": None, "n": 0}
    vals = np.array([w.peak for w in ws], dtype=np.float64)
    idx = sorted(slots["02-28" if (w.peak_date.month, w.peak_date.day) == (2, 29)
                       else _md(w.peak_date)] for w in ws)
    mid = idx[(n - 1) // 2]            # the lower median: a whole day
    ax = {i: md for md, i in slots.items()}
    return {"median": _r(np.median(vals)),
            "p10": _r(np.percentile(vals, 10, method="linear")),
            "p90": _r(np.percentile(vals, 90, method="linear")),
            "median_md": ax[mid], "n": n}


def source(area: str, var: str, meta: Optional[Mapping] = None) -> dict:
    """{dataset, method}; a Columbia snow area adds the frontier row's n_index,
    n_reporting and normals_version (None where the row does not carry one); a
    reservoir area its capacity in TAF (the eight: their total, plus each
    series' newest valued day, so a stopped feed is visible)."""
    kind = area_kind(area)
    out = {"dataset": _DATASETS[(kind, var)],
           "method": METHOD_PRECIP_TOLERANT if tolerant_base(var, area) else METHODS[(kind, var)]}
    if (area, var) in WINDOW_NOTES:         # d091536: the area's own window, in words
        out["method"] += f" For this area, {WINDOW_NOTES[(area, var)]}."
    if kind == "snow" and var == "swe":
        m = meta or {}
        for k in SNOW_SOURCE_META:
            out[k] = m.get(k)
    elif kind == "ba":
        out["series"] = reservoir_series(area)
        out["min_hours"] = LOAD_MIN_HOURS
    elif kind in ("reservoir", "major8", "reservoir_region"):
        out["capacity_taf"] = CAPACITY_AF[reservoir_series(area)] / 1000.0
        if kind in ("major8", "reservoir_region"):
            members = RESERVOIR_MEMBERS[reservoir_series(area)]
            out["capacity_af_by_reservoir"] = {i: CAPACITY_AF[i] for i in members}
            out["series_frontiers"] = dict((meta or {}).get("series_frontiers") or {})
        out["capacity_source"] = ("energylake-dashboard src/app/almanac/"
                                  "california-water-storage/page.tsx RESERVOIRS (capacityAF)")
    return out


# Contract order (§1's table; each `*_absence` rides right after its sibling).
# d091520 appended `peak`, `peak_absence` and `source`; nothing before them moved.
RESPONSE_KEYS = ("area", "var", "units", "season", "frontier", "axis", "base",
                 "percentiles", "percentiles_absence", "normal", "normal_absence",
                 "five_year", "five_year_absence", "this_season", "this_season_absence",
                 "last_season", "last_season_absence", "enso", "years", "curves",
                 "readout", "readout_absence", "peak", "peak_absence", "source")
# A level's body (swe, storage, swe_in): d091522 put `range` and `range_absence`
# after `five_year_absence` (§2.4). The cumulative body above is unchanged.
LEVEL_RESPONSE_KEYS = (RESPONSE_KEYS[:RESPONSE_KEYS.index("five_year_absence") + 1]
                       + ("range", "range_absence")
                       + RESPONSE_KEYS[RESPONSE_KEYS.index("five_year_absence") + 1:])


# d091542 §2.6: what the ROUTE adds at the top level, after the payload's own
# keys: whether the memo served an expired payload while its replacement
# builds, and when the served payload was built.
ROUTE_ADDED_KEYS = ("stale", "built_at")
# ... and what a level's readout gained, after its existing keys (d091550 §2.3
# appended `median_basis`, the snapshot row's words for the same median).
READOUT_ADDED_KEYS = ("pct_of_median_peak", "median_peak", "median_basis")
# d091550 §1.4: what a CARRIED pair's readout (station precipitation) gained.
READOUT_CARRIED_KEYS = ("days_missing", "basis")


def response_keys(var: str) -> tuple[str, ...]:
    return LEVEL_RESPONSE_KEYS if mode(var) == LEVEL else RESPONSE_KEYS


# ---------------------------------------------------------------------------
# The snapshot (/api/weather/season/snapshot, d091542 D-09-25-69, -72)
# ---------------------------------------------------------------------------

SNAPSHOT_KEYS = ("area", "label", "region", "level", "var", "units", "measure",
                 "date", "season", "season_day", "value", "median", "median_basis",
                 "pct_of_median", "percentile", "pct_of_median_peak", "median_peak",
                 "median_peak_md", "n", "frontier", "category", "category_absence",
                 "geo", "absence")
# d091550: a CARRIED pair's row (station precipitation) adds, after `absence`,
# the season's missing days to the row's date and the sum's basis (null when
# the season walked strictly, or the row has no value).
SNAPSHOT_CARRIED_KEYS = ("days_missing", "basis")

# Map points (§2.5), pinned, each with its source. Polygons are not this
# lane's. Reservoirs: the dam's location (USGS GNIS / the National Inventory of
# Dams, to 0.01°); a composite: the mean of its members' points. Snow regions
# and basins: a hand-placed point inside the area, near its middle, read off
# the USGS Watershed Boundary Dataset (Columbia, Snake: HUC4 1701-1706) and
# CDEC's snow-region map (California); not a computed centroid, good to ~0.5°.
DAM_POINTS = {
    "trinity": (40.80, -122.76),      # Trinity Dam
    "shasta": (40.72, -122.42),       # Shasta Dam
    "oroville": (39.54, -121.49),     # Oroville Dam
    "folsom": (38.71, -121.16),       # Folsom Dam
    "new_melones": (37.95, -120.53),  # New Melones Dam
    "don_pedro": (37.70, -120.42),    # Don Pedro Dam
    "millerton": (37.00, -119.70),    # Friant Dam
    "san_luis": (37.06, -121.07),     # B. F. Sisk (San Luis) Dam
}
SNOW_POINTS = {
    "columbia_above_the_dalles": (46.0, -117.5),   # WBD 1701-1707 (US), hand-placed
    "col_above_grand_coulee": (48.3, -116.0),      # WBD 1701 + 1702 (US), hand-placed
    "col_mid_tributaries": (46.8, -120.5),         # WBD 1702-1703, 1707, hand-placed
    "snake": (44.0, -115.5),                       # WBD 1704-1706, hand-placed
    "snake_upper": (43.5, -113.5),                 # WBD 1704-1705, hand-placed
    "snake_lower": (45.5, -115.8),                 # WBD 1706, hand-placed
    "col_canada": (50.5, -117.5),                  # BC Columbia above the border, hand-placed
    "ca_state": (38.0, -119.8),                    # the Sierra's middle, CDEC map
    "ca_north": (40.2, -121.5),                    # CDEC North, hand-placed
    "ca_central": (38.5, -120.2),                  # CDEC Central, hand-placed
    "ca_south": (36.8, -118.6),                    # CDEC South, hand-placed
}


def area_geo(area: str, metadata_stations: Sequence[Mapping] = ()) -> Optional[dict]:
    """A point for the map, or None (a balancing area, or a station
    station_metadata.json does not carry)."""
    kind, ident = area.split(":", 1)
    if kind == "station":
        m = next((m for m in metadata_stations if m["station_id"] == ident), None)
        return {"lat": m["lat"], "lon": m["lon"]} if m and m.get("lat") is not None else None
    if kind == "reservoir":
        pts = [DAM_POINTS[i] for i in RESERVOIR_MEMBERS.get(ident, (ident,))]
        return {"lat": round(sum(p[0] for p in pts) / len(pts), 2),
                "lon": round(sum(p[1] for p in pts) / len(pts), 2)}
    if kind == "snow":
        lat, lon = SNOW_POINTS[ident]
        return {"lat": lat, "lon": lon}
    return None


def snapshot_areas(data_type: str, metadata_stations: Sequence[Mapping]) -> list[tuple[str, str]]:
    """[(area, var)] in /areas order: every area carrying the data type, with
    the var the snapshot reads (the daily peak for the load pair)."""
    out = []
    for area in area_vocabulary(metadata_stations):
        for v in SNAPSHOT_VAR_ORDER:
            if v in vars_for(area) and VAR_DATA_TYPES[v][0] == data_type:
                out.append((area, v))
                break
    return out


def _slot_of(d: date) -> str:
    return "02-28" if (d.month, d.day) == (2, 29) else _md(d)


def snapshot_row(area: str, var: str, payload: Mapping, extras: Mapping, *,
                 on: Optional[date], cat: Optional[str], area_label: str,
                 place: Mapping, geo: Optional[Mapping]) -> dict:
    """One area's row on one season day, every number read off the /season
    build for (area, var, classifier): `payload` and its `extras`.

    The frame is the season day (D-09-25-69): `on` (default: the area's own
    frontier) answers on the curve of the season holding it, against the same
    base. `value` is that season's value on the day (a level's own value, a
    cumulative's running sum); `median` is the cone's p50 on the day (n >= 30,
    the readout's rule) or, on a level with a shorter base, the base's range
    median (n >= 5; `median_basis` says which); `percentile` is the mid-rank
    against the base on the day and needs the cone; the peak fields are the
    base's median peak. `category` is the category's median on the day
    against the all-years median on the day. Absence is a sentence, never a
    zero."""
    level = mode(var) == LEVEL
    row = dict.fromkeys(SNAPSHOT_KEYS + (SNAPSHOT_CARRIED_KEYS if carried(var, area) else ()))
    vt = VAR_DATA_TYPES[var]
    row.update(area=area, label=area_label, region=place.get("region"),
               level=place.get("level"), var=var, units=units(var), measure=vt[1],
               frontier=payload["frontier"], n=payload["base"]["n"], geo=geo)
    pk = (payload.get("peak") or {}).get("base") if level else None
    if pk:
        row.update(median_peak=pk["median"], median_peak_md=pk["median_md"])
    if on is None:
        if payload["frontier"] is None:
            row["absence"] = "no value is banked for this area"
            return row
        on = date.fromisoformat(payload["frontier"])
    row["date"] = on.isoformat()
    s = season_of(var, on)
    if s is None:
        row["absence"] = "the date is outside this data type's season"
        return row
    slots = _slot_index(var)
    day = slots[_slot_of(on)]
    row.update(season=label(var, s), season_day=day)
    frontier = date.fromisoformat(payload["frontier"]) if payload["frontier"] else None
    curve = extras["values"].get(label(var, s))
    value = curve[day] if curve is not None and (frontier is None or on <= frontier) else None
    if value is None:
        a, b = count_window(var, s, area)
        if floor(var, area) and not (a <= on <= b):
            row["absence"] = ("no value on this day: outside the series' reporting window "
                              f"({_md(a)} – {_md(b)})")
        else:
            row["absence"] = "no value on this day"
        return row
    missing = (extras.get("missing") or {}).get(label(var, s))
    if missing is not None:     # d091550 §1.4: a carried season, counted to the day
        k = sum(1 for d in missing if d <= on)
        row["days_missing"] = k
        if k > PRECIP_MISSING_TOLERANCE:
            row["absence"] = (f"{k} days missing in the season to date, more than the "
                              f"tolerance of {PRECIP_MISSING_TOLERANCE}: the sum is withheld")
            return row
        row["basis"] = carried_basis(k)
    row["value"] = _r(value)

    # The day's statistics, unrounded (`extras`), are the payload's own numbers
    # before its one rounding: `median` below equals the payload's p50 (or
    # range median) on the day, and a ratio is taken before rounding, as the
    # readout takes it.
    pct = payload.get("percentiles")
    rng = payload.get("range") if level else None
    median, basis = day_median(day, n=payload["base"]["n"],
                               p50=extras["p50"] if pct is not None else None,
                               range_n=rng["n"] if rng else None,
                               range_median=extras["range_median"] if rng else None)
    if median is None:
        row["absence"] = (f"short base: n = {payload['base']['n']}, no median on this day "
                          f"(the cone needs n >= {CONE_MIN_N}"
                          + (f", a level's range n >= {RANGE_MIN_N}" if level else "") + ")")
    else:
        row["median"], row["median_basis"] = _r(median), basis
        row["pct_of_median"] = _r(100.0 * value / median) if median != 0 else None
        if pct is not None:
            sample = [x for x in extras["B"][:, day].tolist() if not np.isnan(x)]
            row["percentile"] = _r(mid_rank(value, sample)) if sample else None
    if pk and pk["median"]:
        row["pct_of_median_peak"] = _r(100.0 * value / pk["median"])

    if cat is not None:
        block = payload["enso"]["categories"][cat]
        cm = _at(extras["cat_medians"].get(cat), day)
        if block.get("median") is None:
            row["category_absence"] = {"cat": cat, **(block.get("absence")
                                                      or {"reason": "small_n", "n": block["n"]})}
        elif cm is None:
            row["category_absence"] = {"cat": cat, "reason": "no category median on this day",
                                       "n": block["n"]}
        else:
            row["category"] = {"cat": cat, "n": block["n"], "median": _r(cm),
                               "pct_of_all_years_median": (_r(100.0 * cm / median)
                                                           if median else None)}
            if median is None:
                row["category_absence"] = {"cat": cat, "reason": "no all-years median on this day"}
    return row


def build_snapshot(data_type: str, on: Optional[date], classifier: str, cat: Optional[str],
                   rows: Sequence[Mapping]) -> dict:
    return {"data_type": data_type, "date": on.isoformat() if on else None,
            "date_rule": ("each area's own frontier" if on is None else
                          "the requested day, on the curve of the season holding it, "
                          "in each area's own season"),
            "classifier": classifier, "cat": cat, "n_areas": len(rows), "rows": list(rows)}


def parse_snapshot(data_type: Optional[str], on: Optional[str], classifier: Optional[str],
                   cat: Optional[str]) -> tuple[str, Optional[date], str, Optional[str]]:
    """-> (data_type, date or None, classifier, cat or None), or ValueError
    naming the allowed set."""
    if data_type not in DATA_TYPES:
        raise ValueError(f"unknown data_type {data_type!r}; allowed: " + ", ".join(DATA_TYPES))
    d = None
    if on:
        try:
            d = date.fromisoformat(on)
        except ValueError:
            raise ValueError(f"malformed date {on!r}; allowed: YYYY-MM-DD") from None
        if len(on) != 10:
            raise ValueError(f"malformed date {on!r}; allowed: YYYY-MM-DD")
    classifier = classifier or DEFAULT_CLASSIFIER
    if classifier not in CLASSIFIERS:
        raise ValueError(f"unknown classifier {classifier!r}; allowed: " + ", ".join(CLASSIFIERS))
    if cat is not None and cat not in CATEGORIES:
        raise ValueError(f"unknown cat {cat!r}; allowed: " + ", ".join(CATEGORIES))
    return data_type, d, classifier, cat


# ---------------------------------------------------------------------------
# The snow board (/api/weather/snow/board)
# ---------------------------------------------------------------------------

BOARD_KEYS = ("area", "label", "date", "value", "median", "pct_of_median", "percentile",
              "last_year", "five_year_mean", "peak_this_season", "n_reporting", "n_index",
              "absence")


def build_board(payloads: Mapping[str, Mapping]) -> dict:
    """The six basins at a glance, from their `swe` season payloads (keyed by
    basin, any order; rows come out in SNOW_BASINS order).

    `as_of` is the newest frontier among the basins. Each row is its basin's
    readout on that day — the same numbers, the same gates (no median or
    percentile without the cone) — plus last season's value on the same axis
    day and this season's peak to date. A basin whose frontier is not `as_of`
    (its newest day is missing) carries `absence: {reason: "frontier_missing"}`
    and nulls; one whose readout is withheld carries its `readout_absence`.
    Neither is dropped."""
    fronts = [p["frontier"] for p in payloads.values() if p.get("frontier")]
    as_of = max(fronts) if fronts else None
    rows = []
    for b in SNOW_BASINS:
        p = payloads[b]
        r, src = p["readout"], p["source"]
        peak = p["peak"]["this_season"] if p.get("peak") else None
        row = dict.fromkeys(BOARD_KEYS)
        row.update(area=f"snow:{b}", label=SNOW_LABELS[b], date=as_of,
                   peak_this_season=peak, n_index=src.get("n_index"))
        if p["frontier"] is None or p["frontier"] != as_of:
            row["absence"] = {"reason": "frontier_missing", "date": as_of,
                              "last_valued": p["frontier"]}
        elif r is None:
            row["absence"] = p["readout_absence"]
            row["n_reporting"] = src.get("n_reporting")
        else:
            day = r["day"]
            held = p["this_season"] or p["last_season"]
            prev = label("swe", int(held["season"][2:]) - 2)     # WY N -> WY N-1
            ly = next((y for y in p["years"] if y["season"] == prev), None)
            fy = p["five_year"]
            row.update(value=r["value"], median=r["median"],
                       pct_of_median=r["pct_of_median"], percentile=r["percentile"],
                       last_year=ly["to_date"] if ly else None,
                       five_year_mean=fy["mean"][day] if fy else None,
                       n_reporting=src.get("n_reporting"))
        rows.append(row)
    return {"as_of": as_of, "basins": rows}


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

# A snow basin: the LWT_SQL shape, plus the frontier row's `meta` (on that row
# only; every other row's is NULL), in one read.
SNOW_SQL = """
    SELECT (ts AT TIME ZONE 'UTC')::date AS obs_date, value::float8 AS v,
           CASE WHEN ts = max(ts) FILTER (WHERE value IS NOT NULL) OVER ()
                THEN meta END AS meta
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
           count(prcp_mm)::int AS n,
           max(obs_date) FILTER (WHERE prcp_mm IS NOT NULL) AS last
    FROM ghcnd_weather_daily GROUP BY 1, 2
"""

AREAS_STATION_DD_SQL = """
    SELECT station_id AS id, 'hdd' AS var,
           extract(year FROM obs_date - interval '10 months')::int AS s,
           count(*) FILTER (WHERE hdd IS NOT NULL AND basis_complete)::int AS n,
           max(obs_date) FILTER (WHERE hdd IS NOT NULL AND basis_complete) AS last
    FROM station_degree_days_daily
    WHERE extract(month FROM obs_date) IN (11, 12, 1, 2, 3) GROUP BY 1, 2, 3
    UNION ALL
    SELECT station_id, 'cdd', extract(year FROM obs_date)::int,
           count(*) FILTER (WHERE cdd IS NOT NULL AND basis_complete)::int,
           max(obs_date) FILTER (WHERE cdd IS NOT NULL AND basis_complete)
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
           count(value)::int AS n,
           max(d) FILTER (WHERE value IS NOT NULL) AS last
    FROM t
    WHERE (var = 'hdd' AND extract(month FROM d) IN (11, 12, 1, 2, 3))
       OR (var = 'cdd' AND extract(month FROM d) BETWEEN 5 AND 9)
    GROUP BY 1, 2, 3
"""


# /areas: days valued per (basin, water year) for the snow series. Water
# year start by the same shift as precip (Oct 1 − 9 months = Jan 1). `nw`
# counts only Nov 1 -> May 31, snow:col_canada's count window (d091536).
AREAS_SNOW_SQL = """
    SELECT split_part(series, '.', 1) AS id,
           extract(year FROM (ts AT TIME ZONE 'UTC')::date - interval '9 months')::int AS s,
           count(value)::int AS n,
           count(value) FILTER (WHERE extract(month FROM ts AT TIME ZONE 'UTC')
                                IN (11, 12, 1, 2, 3, 4, 5))::int AS nw,
           max((ts AT TIME ZONE 'UTC')::date) FILTER (WHERE value IS NOT NULL) AS last
    FROM timeseries_values
    WHERE dataset = %(d)s AND series LIKE '%%.SWE_PCT'
    GROUP BY 1, 2
"""


def snow_series(area: str) -> str:
    """`snow:{basin}` -> `{basin}.SWE_PCT`."""
    return area.split(":", 1)[1] + SNOW_SERIES_SUFFIX


def frontier_meta(rows: Iterable[Mapping]) -> Optional[Mapping]:
    """The one `meta` SNOW_SQL carries (the frontier row's), or None."""
    for r in rows:
        if r.get("meta") is not None:
            return r["meta"]
    return None


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


# ---------------------------------------------------------------------------
# California hydro (d091522): SQL and the pure row maps
# ---------------------------------------------------------------------------

# The eight reservoirs' rows in one read (the sum needs every series).
RESERVOIRS_SQL = """
    SELECT (ts AT TIME ZONE 'UTC')::date AS obs_date, series, value::float8 AS v
    FROM timeseries_values WHERE dataset = %(d)s AND series = ANY(%(s)s)
    ORDER BY ts, series
"""

# /areas: valued days per (reservoir, water year), and for the eight's sum the
# days on which all eight carry a value.
AREAS_RESERVOIR_SQL = """
    WITH t AS (
        SELECT series, (ts AT TIME ZONE 'UTC')::date AS d, value
        FROM timeseries_values WHERE dataset = %(d)s AND series = ANY(%(s)s)
    )
    SELECT series AS id, extract(year FROM d - interval '9 months')::int AS s,
           count(value)::int AS n, max(d) FILTER (WHERE value IS NOT NULL) AS last
    FROM t GROUP BY 1, 2
    UNION ALL
    SELECT %(m)s AS id, s, count(*) FILTER (WHERE k = %(k)s)::int AS n,
           max(d) FILTER (WHERE k = %(k)s) AS last
    FROM (SELECT d, extract(year FROM d - interval '9 months')::int AS s,
                 count(DISTINCT series) FILTER (WHERE value IS NOT NULL) AS k
          FROM t GROUP BY d) x
    GROUP BY 2
"""

# /areas: valued days per (California region, water year) INSIDE swe_in's
# count window (Dec 1 -> May 31); a season with only off-season rows still
# lists (n = 0).
AREAS_CA_SNOW_SQL = """
    SELECT split_part(series, '_', 1) AS id,
           extract(year FROM (ts AT TIME ZONE 'UTC')::date - interval '9 months')::int AS s,
           count(value) FILTER (WHERE extract(month FROM ts AT TIME ZONE 'UTC')
                                IN (12, 1, 2, 3, 4, 5))::int AS n,
           max((ts AT TIME ZONE 'UTC')::date) FILTER (WHERE value IS NOT NULL) AS last
    FROM timeseries_values WHERE dataset = %(d)s AND series = ANY(%(s)s)
    GROUP BY 1, 2
"""

# d091542: the three regional composites' valued days per water year (a day
# counts when every member carries a value, `major8_daily`'s rule) and their
# newest such day. `g` / `gs` are the (composite, member series) pairs.
AREAS_RESERVOIR_REGIONS_SQL = """
    WITH m AS (
        SELECT * FROM unnest(%(g)s::text[], %(gs)s::text[]) AS m(id, series)
    ), need AS (
        SELECT id, count(*) AS need FROM m GROUP BY 1
    ), k AS (
        SELECT m.id, (t.ts AT TIME ZONE 'UTC')::date AS d,
               count(DISTINCT t.series) FILTER (WHERE t.value IS NOT NULL) AS k
        FROM timeseries_values t JOIN m ON m.series = t.series
        WHERE t.dataset = %(d)s AND t.series = ANY(%(s)s)
        GROUP BY 1, 2
    )
    SELECT k.id, extract(year FROM k.d - interval '9 months')::int AS s,
           count(*) FILTER (WHERE k.k = need.need)::int AS n,
           max(k.d) FILTER (WHERE k.k = need.need) AS last
    FROM k JOIN need ON need.id = k.id
    GROUP BY 1, 2
"""


def reservoir_region_params() -> dict:
    """AREAS_RESERVOIR_REGIONS_SQL's parameters (bar the dataset)."""
    pairs = [(c, i) for c in RESERVOIR_REGION_IDS for i in RESERVOIR_MEMBERS[c]]
    return {"g": [c for c, _ in pairs], "gs": [i for _, i in pairs],
            "s": sorted({i for _, i in pairs})}


CA_SNOW_SERIES = tuple(r.removeprefix("ca_") + "_avg_swc" for r in CA_SNOW)


def storage_daily(rows: Iterable[Mapping]) -> dict[date, Optional[float]]:
    """One reservoir's rows -> {date: TAF or None}: acre-feet / 1000, NULL stays None."""
    return {r["obs_date"]: None if r["v"] is None else float(r["v"]) / 1000.0 for r in rows}


def major8_daily(rows: Iterable[Mapping], members: Sequence[str] = RESERVOIR_IDS
                 ) -> tuple[dict[date, Optional[float]], dict[str, Optional[str]]]:
    """The members' rows (default: the eight) -> ({date: TAF or None}, {id:
    newest valued day}).

    A date any member's row carries is a key. Its value is the sum of the
    members' acre-feet / 1000 when EVERY MEMBER carries a value that day, and
    None otherwise: never a partial sum, never renormalised. d091542's three
    regional composites are this rule on their own members."""
    by_day: dict[date, dict[str, float]] = {}
    last: dict[str, Optional[date]] = dict.fromkeys(members)
    for r in rows:
        if r["series"] not in last:
            continue
        day = by_day.setdefault(r["obs_date"], {})
        if r["v"] is not None:
            day[r["series"]] = float(r["v"])
            if last[r["series"]] is None or r["obs_date"] > last[r["series"]]:
                last[r["series"]] = r["obs_date"]
    out = {d: (sum(v[i] for i in members) / 1000.0
               if all(i in v for i in members) else None)
           for d, v in by_day.items()}
    return out, {i: (d.isoformat() if d else None) for i, d in last.items()}


def composite_daily(area: str, rows: Iterable[Mapping]
                    ) -> tuple[dict[date, Optional[float]], dict[str, Optional[str]]]:
    """`reservoir:ca_major8` or a regional composite: `major8_daily` on its members."""
    return major8_daily(rows, RESERVOIR_MEMBERS[reservoir_series(area)])


# ---------------------------------------------------------------------------
# Load (d091525): SQL and the pure row maps
# ---------------------------------------------------------------------------

# One grouped read per balancing area: each UTC−8 day's maximum hourly load and
# how many hours carried a value (the primary key (ts, dataset, series) makes an
# hour one row, so count(value) is the hour count). `ts AT TIME ZONE 'UTC'` first, so
# the day is UTC−8 whatever the session's TimeZone (the spec's
# `date_trunc('day', ts - interval '8 hours')` truncates in the session zone).
LOAD_PEAK_SQL = """
    SELECT ((ts AT TIME ZONE 'UTC') - interval '8 hours')::date AS obs_date,
           max(value)::float8 AS peak,
           count(value)::int AS hours
    FROM timeseries_values WHERE dataset = %(d)s AND series = %(s)s
    GROUP BY 1 ORDER BY 1
"""

# /areas: per (balancing area, water year) the qualifying days (`n`, >= h
# hours), the days whose trailing seven all qualify (`n7`, peak_load_7d's
# count) and the newest qualifying day. The 7-day window is by calendar date
# (RANGE), so a day with no rows breaks it, as it does in peak_load_7d.
AREAS_LOAD_SQL = """
    WITH h AS (
        SELECT series AS id, ((ts AT TIME ZONE 'UTC') - interval '8 hours')::date AS d,
               count(value) >= %(h)s AS ok
        FROM timeseries_values WHERE dataset = %(d)s AND series = ANY(%(s)s)
        GROUP BY 1, 2
    ), w AS (
        SELECT id, d, ok,
               count(*) FILTER (WHERE ok) OVER (PARTITION BY id ORDER BY d
                   RANGE BETWEEN interval '6 days' PRECEDING AND CURRENT ROW) AS k
        FROM h
    )
    SELECT id, extract(year FROM d - interval '9 months')::int AS s,
           count(*) FILTER (WHERE ok)::int AS n,
           count(*) FILTER (WHERE k = 7)::int AS n7,
           max(d) FILTER (WHERE ok) AS last,
           max(d) FILTER (WHERE k = 7) AS last7
    FROM w GROUP BY 1, 2
"""


def load_day(ts: datetime) -> date:
    """The UTC−8 day an hour belongs to (an aware timestamp): 07:59Z is the
    previous day, 08:00Z the next. A fixed offset, never daylight time."""
    return (ts.astimezone(timezone.utc) - LOAD_DAY_OFFSET).date()


def load_days_from_hours(hours: Iterable[Mapping]) -> list[dict]:
    """Hourly rows {ts, value} (one per hour, as the primary key holds them) ->
    LOAD_PEAK_SQL's rows {obs_date, peak, hours} by the same rules: the SQL's
    reference, in Python. A NULL hour is a row but not an hour."""
    by_day: dict[date, list[float]] = {}
    for r in hours:
        day = by_day.setdefault(load_day(r["ts"]), [])
        if r["value"] is not None:
            day.append(float(r["value"]))
    return [{"obs_date": d, "peak": max(v) if v else None, "hours": len(v)}
            for d, v in sorted(by_day.items())]


def peak_load_daily(rows: Iterable[Mapping]) -> dict[date, Optional[float]]:
    """LOAD_PEAK_SQL rows -> {date: MW or None}: the day's maximum when >= 20
    hours report, else None (a row, but not a peak). Never a partial-day max."""
    return {r["obs_date"]: (float(r["peak"])
                            if r["peak"] is not None and r["hours"] >= LOAD_MIN_HOURS
                            else None)
            for r in rows}


def peak_load_7d(daily: Mapping[date, Optional[float]]) -> dict[date, Optional[float]]:
    """The trailing 7-day mean of the daily peak, on every date `daily` holds:
    the mean of D−6 … D when all seven carry a peak, else None (the record's
    first six days, or any window touching a null or absent day)."""
    out: dict[date, Optional[float]] = {}
    for d in daily:
        xs = [daily.get(d - timedelta(days=k)) for k in range(LOAD_WINDOW)]
        out[d] = (float(np.mean(np.array(xs, dtype=np.float64)))
                  if all(x is not None for x in xs) else None)
    return out
