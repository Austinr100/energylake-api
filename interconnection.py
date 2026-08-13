"""
interconnection — the pure shaping layer behind /api/interconnection/* (v0, 2026-08-12).

Sidecar to main.py in the house idiom (see degree_days.py, almanac.py,
structures.py, paper_desk.py): every function here is a PURE composition over
rows already fetched. No DB handle, no clock, no FastAPI. main.py owns the SQL,
the cache and the routes; this module owns what a row MEANS on the way out.

═══════════════════════════════════════════════════════════════════════════
THE ONE RULE THIS MODULE EXISTS TO ENFORCE
═══════════════════════════════════════════════════════════════════════════

**The fuel rollup is a PARSE, and a parse is never allowed to be silent.**

`caiso_interconnection_queue.generation_type` is a free-text column carrying 41
distinct strings as of the 2026-08-07 snapshot — "Photovoltaic", "Storage +
Photovoltaic", "Combustion Turbine + Storage + Steam Turbine". Rolling those up
into nine display fuels is an editorial act, not a lookup, and every editorial
act on this surface has to be visible:

  * a raw string no keyword recognises maps to `Other` and its RAW LABEL rides
    the response (`by_fuel["Other"].raw_types`) — unmapped is shown, never
    swallowed;
  * any raw string containing a component no keyword recognises is named in
    `unmapped_types`, whatever bucket it finally lands in, each flagged
    `known: true|false` against the 41 strings banked at recon time. A 42nd
    string appearing in the pantry tomorrow shows up as `known: false` in the
    response the same day, without a deploy;
  * NOTHING is dropped. Every row is in exactly one bucket, and
    sum(by_fuel.count) == project_count is asserted by the test suite.

═══════════════════════════════════════════════════════════════════════════
TWO JUDGMENT CALLS, STATED
═══════════════════════════════════════════════════════════════════════════

R1. **"Steam Turbine" is NOT mapped to Natural Gas.** It is 158 rows and 38.3
    GW — the sixth-largest raw string in the bank — and it is genuinely
    ambiguous: CAISO writes "Steam Turbine" for gas steam, for geothermal, for
    biomass and for solar-thermal blocks alike. Mapping it to Natural Gas would
    manufacture 38 GW of fuel knowledge the column does not contain. It maps to
    `Other` with its raw label attached, which is the honest shape of "we know
    the prime mover, not the fuel". If the captain rules otherwise it is one
    line in `_COMPONENT_KEYWORDS`.

R2. **A multi-type string naming ONE distinct technology is not a hybrid.** The
    lane spec says "any other multi-type string" -> Other Hybrid. Read
    literally that sends "Storage + Storage" (4 rows, 3,292 MW), "Steam Turbine
    + Steam Turbine" and "Combined Cycle + Combined Cycle" into a hybrid bucket
    they do not belong in — those are duplicate-component artifacts of CAISO's
    own string building, not mixed-technology projects. So "multi-type" here
    means *more than one DISTINCT component*, and `Storage + Storage` rolls up
    to Battery Storage. This is a deliberate deviation from the literal wording
    and it is named in the handback; the full 41-row mapping table is emitted
    by `mapping_table()` so the call can be reviewed value by value.

`Geothermal` is in the vocabulary and matches ZERO rows in the 2026-08-07 bank:
no raw string contains the keyword. The bucket is kept in the display order and
reported with count 0 rather than dropped, because an empty bucket is a fact
about the queue and a missing bucket is a fact about our code.
"""

from __future__ import annotations

from datetime import date as _date
from datetime import datetime as _datetime
from decimal import Decimal
from typing import Any, Iterable, Optional


# ---------------------------------------------------------------------------
# Scalars — the `or 0` that is not here
# ---------------------------------------------------------------------------

def _f(v: Any) -> Optional[float]:
    """Numeric -> float, NULL -> None. No default, no coalesce."""
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


def _mw(v: Any) -> float:
    """A capacity summand. NULL capacity contributes 0.0 TO A SUM.

    This is the one place a null becomes a zero, and it is not a coalesce of a
    reported value: it is the additive identity used while accumulating. The
    COUNT beside every MW total is what tells a reader how many projects stand
    behind it, and `capacity_mw` is NOT NULL on all 2,278 banked rows anyway
    (verified 2026-08-12). A row's own `capacity_mw` on the way out stays null
    if it is null — see `project_row`.
    """
    f = _f(v)
    return 0.0 if f is None else f


def _round_mw(v: float) -> float:
    """MW to one decimal. Float accumulation over 2,278 numeric(?,3) values
    leaves noise in the 10th place; the bank's own precision is 3 decimals and
    nobody reads a queue total past 0.1 MW."""
    return round(v + 0.0, 1)


# ---------------------------------------------------------------------------
# The fuel rollup
# ---------------------------------------------------------------------------

FUEL_SOLAR = "Solar"
FUEL_BATTERY = "Battery Storage"
FUEL_WIND = "Wind"
FUEL_SOLAR_STORAGE = "Solar+Storage Hybrid"
FUEL_OTHER_HYBRID = "Other Hybrid"
FUEL_GAS = "Natural Gas"
FUEL_GEOTHERMAL = "Geothermal"
FUEL_HYDRO = "Hydro"
FUEL_OTHER = "Other"

#: Display order. Every response emits every bucket in this order, including
#: the ones with count 0 — see the Geothermal note in the module docstring.
FUEL_ORDER: tuple[str, ...] = (
    FUEL_SOLAR,
    FUEL_BATTERY,
    FUEL_WIND,
    FUEL_SOLAR_STORAGE,
    FUEL_OTHER_HYBRID,
    FUEL_GAS,
    FUEL_GEOTHERMAL,
    FUEL_HYDRO,
    FUEL_OTHER,
)

FUEL_SET = frozenset(FUEL_ORDER)

#: The component separator CAISO uses. Splitting on it is what makes
#: "Wind Turbine" and "Combustion Turbine" distinguishable at all — a bare
#: substring scan over the whole string would let "Turbine" collide.
_SEPARATOR = "+"

#: (keyword, fuel), checked IN ORDER against each lowercased component. First
#: match wins, so the more specific keyword comes first where two overlap
#: ("gas turbine" before any bare "gas" would; there is no bare "gas").
#:
#: Deliberately ABSENT: "steam turbine" and "turbine". See R1 in the module
#: docstring — an unrecognised prime mover is worth more as a visible Other
#: than as an invented fuel.
_COMPONENT_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("photovoltaic", FUEL_SOLAR),
    ("solar", FUEL_SOLAR),               # "Solar Thermal"
    ("battery", FUEL_BATTERY),
    ("storage", FUEL_BATTERY),
    ("wind", FUEL_WIND),
    ("geothermal", FUEL_GEOTHERMAL),     # zero rows in the 2026-08-07 bank
    ("hydro", FUEL_HYDRO),
    ("combined cycle", FUEL_GAS),
    ("gas turbine", FUEL_GAS),
    ("combustion turbine", FUEL_GAS),
    ("reciprocating engine", FUEL_GAS),
    ("cogeneration", FUEL_GAS),
)

#: Every distinct generation_type string in caiso_interconnection_queue as of
#: the 2026-08-07 snapshot, read live 2026-08-12 (SELECT DISTINCT, 41 values).
#: The mirror of tests/fixtures/caiso_generation_types_2026_08_07.json, which
#: also carries each value's live count and MW.
#:
#: This is NOT a whitelist — nothing is rejected for being absent from it. It is
#: the drift sensor: a raw string outside this set is reported `known: false` in
#: `unmapped_types` so a new CAISO vocabulary word announces itself in the
#: response body rather than waiting to be noticed in a chart.
KNOWN_GENERATION_TYPES: frozenset[str] = frozenset({
    "Cogeneration",
    "Combined Cycle",
    "Combined Cycle + Combined Cycle",
    "Combined Cycle + Storage",
    "Combustion Turbine",
    "Combustion Turbine + Photovoltaic",
    "Combustion Turbine + Storage",
    "Combustion Turbine + Storage + Steam Turbine",
    "Gas Turbine",
    "Gas Turbine + Storage",
    "Hydro",
    "Other",
    "Photovoltaic",
    "Photovoltaic + Combustion Turbine",
    "Photovoltaic + Steam Turbine",
    "Photovoltaic + Storage",
    "Photovoltaic + Storage + Combustion Turbine",
    "Photovoltaic + Wind Turbine",
    "Photovoltaic + Wind Turbine + Storage",
    "Reciprocating Engine",
    "Solar Thermal",
    "Steam Turbine",
    "Steam Turbine + Steam Turbine",
    "Steam Turbine + Storage",
    "Storage",
    "Storage + Combustion Turbine",
    "Storage + Gas Turbine",
    "Storage + Other",
    "Storage + Photovoltaic",
    "Storage + Photovoltaic + Wind Turbine",
    "Storage + Solar Thermal",
    "Storage + Steam Turbine + Combustion Turbine",
    "Storage + Storage",
    "Storage + Wind Turbine",
    "Storage + Wind Turbine + Photovoltaic",
    "Wind Turbine",
    "Wind Turbine + Photovoltaic",
    "Wind Turbine + Photovoltaic + Storage",
    "Wind Turbine + Storage",
    "Wind Turbine + Storage + Photovoltaic",
    "Wind Turbine + Storage + Storage",
})


def _match_component(component: str) -> Optional[str]:
    """One component of a raw string -> a display fuel, or None if unrecognised.

    Case-insensitive keyword containment, first match in `_COMPONENT_KEYWORDS`
    wins. None is a real answer here, not a failure: it is what makes
    "Steam Turbine" visible downstream.
    """
    low = component.strip().lower()
    if not low:
        return None
    for keyword, fuel in _COMPONENT_KEYWORDS:
        if keyword in low:
            return fuel
    return None


def classify_fuel(raw: Optional[str]) -> tuple[str, tuple[str, ...]]:
    """Raw generation_type -> (display fuel, unmapped components).

    The whole parse, in one place, with every branch stated:

      * NULL / blank            -> Other, no components to name.
      * one distinct component  -> that component's fuel, or Other when no
                                   keyword matched it (R1's "Steam Turbine").
                                   "Storage + Storage" is ONE distinct
                                   component and takes this branch (R2).
      * >1 distinct component,
        Solar AND Battery among -> Solar+Storage Hybrid. Per the lane spec's
                                   literal wording this holds even when a third
                                   technology is present, so
                                   "Storage + Photovoltaic + Wind Turbine" is
                                   Solar+Storage Hybrid, not Other Hybrid.
      * >1 distinct component,
        otherwise               -> Other Hybrid.

    The second element is every component no keyword matched, in the order the
    raw string names them. It is empty for a fully-recognised string.

    Total by construction: there is no input for which this returns nothing.
    """
    if raw is None or not raw.strip():
        return FUEL_OTHER, ()

    components = [c.strip() for c in raw.split(_SEPARATOR) if c.strip()]
    if not components:
        return FUEL_OTHER, ()

    unmapped: list[str] = []
    # Ordered-unique over the components' identities: a matched component is
    # identified by its fuel, an unmatched one by its own lowercased text. That
    # is what collapses "Storage + Storage" to one and keeps "Steam Turbine +
    # Storage" at two.
    identities: list[str] = []
    fuels: list[str] = []
    for component in components:
        fuel = _match_component(component)
        if fuel is None:
            unmapped.append(component)
            identity = "raw:" + component.strip().lower()
        else:
            fuels.append(fuel)
            identity = "fuel:" + fuel
        if identity not in identities:
            identities.append(identity)

    if len(identities) == 1:
        # Single distinct technology — hybrid does not apply.
        return (fuels[0] if fuels else FUEL_OTHER), tuple(unmapped)

    fuel_set = set(fuels)
    if FUEL_SOLAR in fuel_set and FUEL_BATTERY in fuel_set:
        return FUEL_SOLAR_STORAGE, tuple(unmapped)
    return FUEL_OTHER_HYBRID, tuple(unmapped)


def mapping_table(raw_types: Iterable[str]) -> list[dict]:
    """Every raw string with the fuel it rolls up to — the reviewable artifact.

    Used by the handback and by the tests; not served by any route. Sorted by
    fuel display order then raw string, so the table reads as the rollup rather
    than as the alphabet.
    """
    rows = []
    for raw in raw_types:
        fuel, unmapped = classify_fuel(raw)
        rows.append({
            "generation_type": raw,
            "fuel": fuel,
            "unmapped_components": list(unmapped),
            "known": raw in KNOWN_GENERATION_TYPES,
        })
    rows.sort(key=lambda r: (FUEL_ORDER.index(r["fuel"]), r["generation_type"]))
    return rows


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

#: The three status values caiso_interconnection_queue actually holds, verified
#: live 2026-08-12: ACTIVE (268), COMPLETED (250), WITHDRAWN (1,760).
STATUS_ACTIVE = "ACTIVE"
STATUS_COMPLETED = "COMPLETED"
STATUS_WITHDRAWN = "WITHDRAWN"

KNOWN_STATUSES: tuple[str, ...] = (STATUS_ACTIVE, STATUS_COMPLETED, STATUS_WITHDRAWN)

#: "Active" for the by_fuel default view means the queue as a live pipeline:
#: ACTIVE only. COMPLETED projects are built and WITHDRAWN ones are gone; both
#: stay available through the `all_statuses` variant and through ?status=.
ACTIVE_STATUSES: frozenset[str] = frozenset({STATUS_ACTIVE})


def _is_withdrawn(row: dict) -> bool:
    """The lane's ruled definition: withdrawn IS `withdrawn_date IS NOT NULL`.

    NOT `status = 'WITHDRAWN'`. The two disagree on 39 rows / 10,857.1 MW in the
    2026-08-07 bank — projects CAISO marks WITHDRAWN while publishing no
    withdrawal date. The attrition view uses the DATE because it is a per-vintage
    time series and a withdrawal with no date cannot be placed on it; the
    disagreement is not hidden, it is counted and reported as
    `withdrawn_status_without_date` on the attrition block.
    """
    return row.get("withdrawn_date") is not None


# ---------------------------------------------------------------------------
# Accumulators
# ---------------------------------------------------------------------------

class _Bucket:
    """count + MW, and nothing else. The unit every facet is made of."""

    __slots__ = ("count", "mw")

    def __init__(self) -> None:
        self.count = 0
        self.mw = 0.0

    def add(self, capacity_mw: Any) -> None:
        self.count += 1
        self.mw += _mw(capacity_mw)

    def out(self) -> dict:
        return {"count": self.count, "mw": _round_mw(self.mw)}


def _bucketed(rows: Iterable[dict], key) -> dict:
    out: dict = {}
    for r in rows:
        k = key(r)
        b = out.get(k)
        if b is None:
            b = out[k] = _Bucket()
        b.add(r.get("capacity_mw"))
    return out


def _year(v: Any) -> Optional[int]:
    if v is None:
        return None
    if isinstance(v, (_date, _datetime)):
        return v.year
    return None


# ---------------------------------------------------------------------------
# by_fuel
# ---------------------------------------------------------------------------

def _fuel_block(rows: list[dict]) -> dict:
    """Fuel rollup over the rows handed in. Every bucket, in display order.

    `Other` additionally carries `raw_types`: the raw strings that landed in it,
    each with its own count and MW. That is the "unmapped is visible" guarantee
    at the bucket level — a reader who sees 38 GW of Other can see immediately
    that 38.3 GW of it is the string "Steam Turbine".
    """
    buckets: dict[str, _Bucket] = {f: _Bucket() for f in FUEL_ORDER}
    other_raw: dict[str, _Bucket] = {}

    for r in rows:
        raw = r.get("generation_type")
        fuel = r.get("_fuel") or classify_fuel(raw)[0]
        buckets[fuel].add(r.get("capacity_mw"))
        if fuel == FUEL_OTHER:
            label = raw if (raw is not None and raw.strip()) else None
            b = other_raw.get(label)
            if b is None:
                b = other_raw[label] = _Bucket()
            b.add(r.get("capacity_mw"))

    out: dict = {}
    for fuel in FUEL_ORDER:
        entry = buckets[fuel].out()
        if fuel == FUEL_OTHER:
            entry["raw_types"] = [
                {"generation_type": label, **b.out()}
                for label, b in sorted(
                    other_raw.items(),
                    key=lambda kv: (-kv[1].mw, kv[0] or ""),
                )
            ]
        out[fuel] = entry
    return out


def _unmapped_block(rows: list[dict]) -> list[dict]:
    """Every raw string containing a component no keyword recognised.

    Bucket-independent on purpose: "Steam Turbine + Storage" lands in Other
    Hybrid, not in Other, and would be invisible if this only looked at the
    Other bucket. `known` is False for a string the 2026-08-12 recon never saw —
    that is the runtime drift alarm the lane asked for.
    """
    agg: dict[str, dict] = {}
    for r in rows:
        raw = r.get("generation_type")
        unmapped = r.get("_unmapped")
        if unmapped is None:
            unmapped = classify_fuel(raw)[1]
        if not unmapped:
            continue
        entry = agg.get(raw)
        if entry is None:
            entry = agg[raw] = {
                "generation_type": raw,
                "unmapped_components": list(unmapped),
                "fuel": r.get("_fuel") or classify_fuel(raw)[0],
                "known": raw in KNOWN_GENERATION_TYPES,
                "_b": _Bucket(),
            }
        entry["_b"].add(r.get("capacity_mw"))

    out = []
    for entry in sorted(agg.values(), key=lambda e: (-e["_b"].mw, e["generation_type"] or "")):
        b = entry.pop("_b")
        entry.update(b.out())
        out.append(entry)
    return out


# ---------------------------------------------------------------------------
# The summary payload
# ---------------------------------------------------------------------------

#: by_county is a top-N with a named remainder — never a silent truncation.
COUNTY_TOP_N = 25


def _snapshot_block(rows: list[dict], snapshot_date: Any) -> dict:
    """The snapshot chip: what generation this is, and how big it is.

    ONE definition, shared by `summary_payload` and `rollups_payload`, so the
    two surfaces cannot disagree about the universe they describe. Byte-for-byte
    what /summary has served since v0 — extracted, not changed.
    """
    status_buckets = _bucketed(rows, lambda r: r.get("status"))
    total = _Bucket()
    for r in rows:
        total.add(r.get("capacity_mw"))
    return {
        "snapshot_date": _iso(snapshot_date),
        "project_count": total.count,
        "total_mw": _round_mw(total.mw),
        "statuses": [
            {"status": s, **status_buckets[s].out()}
            for s in sorted(status_buckets, key=lambda s: (-status_buckets[s].mw, s or ""))
        ],
    }


def summary_payload(
    rows: list[dict],
    *,
    snapshot_date: Any,
    data_note: str,
    county_top_n: int = COUNTY_TOP_N,
) -> dict:
    """Every aggregate on /api/interconnection/summary, from ONE row set.

    ONE READ, ALL FACETS. The rollup is a Python parse, so `by_fuel` cannot be
    a GROUP BY; and once one facet is computed in process, computing the rest
    anywhere else would let two facets in the same response disagree about the
    universe they describe. 2,278 slim rows is a ~1 MB read and a few
    milliseconds of arithmetic — cheaper than the five round trips the SQL
    version would cost, and internally consistent by construction.

    Shape (this is Lane D's contract):

        { snapshot_date, data_note, generated_from_rows,
          snapshot: { snapshot_date, project_count, total_mw,
                      statuses: [{status, count, mw}] },
          by_fuel: { active: {...}, all_statuses: {...}, active_statuses: [...] },
          by_queue_year:        [{year, count, mw}],
          by_proposed_cod_year: {years: [{year, count, mw}], undated: {...}},
          by_county:            {top: [{county, state, count, mw}], other: {...},
                                 group_count, top_n},
          by_transmission_owner:[{to, count, mw}],
          attrition:            {years: [...], totals: {...},
                                 withdrawn_status_without_date: {...},
                                 definition: "..."},
          unmapped_types:       [{generation_type, fuel, unmapped_components,
                                  known, count, mw}] }
    """
    # Classify once. Every downstream facet reads `_fuel` off the row rather
    # than re-parsing, so one row can never be two fuels in one response.
    for r in rows:
        fuel, unmapped = classify_fuel(r.get("generation_type"))
        r["_fuel"] = fuel
        r["_unmapped"] = unmapped

    active_rows = [r for r in rows if r.get("status") in ACTIVE_STATUSES]

    # ── snapshot ────────────────────────────────────────────────────────────
    snapshot = _snapshot_block(rows, snapshot_date)

    # ── by_queue_year ───────────────────────────────────────────────────────
    qy = _bucketed(
        [r for r in rows if _year(r.get("queue_date")) is not None],
        lambda r: _year(r.get("queue_date")),
    )
    by_queue_year = [{"year": y, **qy[y].out()} for y in sorted(qy)]

    # ── by_proposed_cod_year ────────────────────────────────────────────────
    # A null proposed COD is `undated` — a project with no stated in-service
    # date. It is counted in its own block and NEVER folded into a year, never
    # dropped, and never given a placeholder year.
    dated = [r for r in rows if _year(r.get("proposed_completion_date")) is not None]
    undated = _Bucket()
    for r in rows:
        if _year(r.get("proposed_completion_date")) is None:
            undated.add(r.get("capacity_mw"))
    cy = _bucketed(dated, lambda r: _year(r.get("proposed_completion_date")))
    by_cod_year = {
        "years": [{"year": y, **cy[y].out()} for y in sorted(cy)],
        "undated": undated.out(),
    }

    # ── by_county ───────────────────────────────────────────────────────────
    counties = _bucketed(rows, lambda r: (r.get("county"), r.get("state")))
    ranked = sorted(
        counties.items(),
        key=lambda kv: (-kv[1].mw, -kv[1].count, kv[0][0] or ""),
    )
    top = [
        {"county": c, "state": s, **b.out()}
        for (c, s), b in ranked[:county_top_n]
    ]
    rest = ranked[county_top_n:]
    other_county = {
        "count": sum(b.count for _, b in rest),
        "mw": _round_mw(sum(b.mw for _, b in rest)),
        "group_count": len(rest),
    }
    by_county = {
        "top": top,
        "other": other_county,
        "group_count": len(ranked),
        "top_n": county_top_n,
    }

    # ── by_transmission_owner ───────────────────────────────────────────────
    tos = _bucketed(rows, lambda r: r.get("transmission_owner"))
    by_to = [
        {"to": t, **tos[t].out()}
        for t in sorted(tos, key=lambda t: (-tos[t].mw, t or ""))
    ]

    # ── attrition ───────────────────────────────────────────────────────────
    attrition = _attrition_block(rows)

    return {
        "snapshot_date": _iso(snapshot_date),
        "data_note": data_note,
        "generated_from_rows": len(rows),
        "snapshot": snapshot,
        "by_fuel": {
            "active": _fuel_block(active_rows),
            "all_statuses": _fuel_block(rows),
            "active_statuses": sorted(ACTIVE_STATUSES),
        },
        "by_queue_year": by_queue_year,
        "by_proposed_cod_year": by_cod_year,
        "by_county": by_county,
        "by_transmission_owner": by_to,
        "attrition": attrition,
        "unmapped_types": _unmapped_block(rows),
    }


def _attrition_block(rows: list[dict]) -> dict:
    """Per queue-date vintage: what entered, and how much of it has since left.

    THE SURVIVAL VIEW. For each year(queue_date):

        enqueued_count / enqueued_mw  every project that entered that year
        withdrawn_count / withdrawn_mw  those of them with a withdrawn_date
        surviving_count / surviving_mw  the arithmetic complement
        withdrawn_mw_share              withdrawn_mw / enqueued_mw, or null
                                        when the vintage enqueued 0 MW

    A vintage with nothing withdrawn reports 0 / 0.0, not null: zero withdrawn
    out of a known enqueued set is a measurement, not an absence. The share is
    null ONLY when the denominator is zero, which is the one case where the
    ratio genuinely does not exist.

    `withdrawn_status_without_date` sits beside the series, not inside it: the
    39 rows / 10,857.1 MW that CAISO calls WITHDRAWN while publishing no date
    are invisible to a date-keyed series by definition, so their total is
    reported once, plainly, rather than being silently absorbed into
    `surviving`.
    """
    years: dict[int, dict] = {}
    for r in rows:
        y = _year(r.get("queue_date"))
        if y is None:
            continue
        e = years.get(y)
        if e is None:
            e = years[y] = {"enq": _Bucket(), "wd": _Bucket()}
        e["enq"].add(r.get("capacity_mw"))
        if _is_withdrawn(r):
            e["wd"].add(r.get("capacity_mw"))

    series = []
    for y in sorted(years):
        enq, wd = years[y]["enq"], years[y]["wd"]
        series.append({
            "year": y,
            "enqueued_count": enq.count,
            "enqueued_mw": _round_mw(enq.mw),
            "withdrawn_count": wd.count,
            "withdrawn_mw": _round_mw(wd.mw),
            "surviving_count": enq.count - wd.count,
            "surviving_mw": _round_mw(enq.mw - wd.mw),
            "withdrawn_mw_share": (
                round(wd.mw / enq.mw, 4) if enq.mw > 0 else None
            ),
        })

    total_enq = _Bucket()
    total_wd = _Bucket()
    ghost = _Bucket()
    undated_vintage = _Bucket()
    for r in rows:
        if _year(r.get("queue_date")) is None:
            undated_vintage.add(r.get("capacity_mw"))
            continue
        total_enq.add(r.get("capacity_mw"))
        if _is_withdrawn(r):
            total_wd.add(r.get("capacity_mw"))
    for r in rows:
        if r.get("status") == STATUS_WITHDRAWN and r.get("withdrawn_date") is None:
            ghost.add(r.get("capacity_mw"))

    return {
        "years": series,
        "totals": {
            "enqueued_count": total_enq.count,
            "enqueued_mw": _round_mw(total_enq.mw),
            "withdrawn_count": total_wd.count,
            "withdrawn_mw": _round_mw(total_wd.mw),
            "surviving_count": total_enq.count - total_wd.count,
            "surviving_mw": _round_mw(total_enq.mw - total_wd.mw),
            "withdrawn_mw_share": (
                round(total_wd.mw / total_enq.mw, 4) if total_enq.mw > 0 else None
            ),
        },
        "no_queue_date": undated_vintage.out(),
        "withdrawn_status_without_date": {
            **ghost.out(),
            "note": (
                "rows with status='WITHDRAWN' and withdrawn_date IS NULL. They "
                "cannot be placed on a per-vintage withdrawal series and are "
                "therefore counted as surviving by the date-based definition "
                "above. Reported here so the disagreement between the status "
                "column and the date column is visible rather than absorbed."
            ),
        },
        "definition": (
            "withdrawn = withdrawn_date IS NOT NULL; vintage = year(queue_date)"
        ),
    }


# ---------------------------------------------------------------------------
# Full-cardinality rollups (v0.1, 2026-08-13)
# ---------------------------------------------------------------------------
#
# /summary's `by_county` is a top-25 with an aggregated remainder — a bounded
# list, correct for a chip, useless for a map or a full table. These blocks are
# the OTHER answer: EVERY key at the grain, none folded, none truncated. They
# live on their own endpoint under their own names precisely so /summary's
# `by_county` keeps the exact shape the dashboard already captured.
#
# THREE RULES, ENFORCED HERE:
#
#   1. NOTHING IS FOLDED. There is no "other" bucket at any grain. A grain with
#      101 keys emits 101 keys. `key_count` states the cardinality so a client
#      can prove for itself that nothing was dropped.
#
#   2. MISSING IS A NAMED KEY, NOT AN ABSENCE. A NULL or blank county is the key
#      `"unlocated"`; a NULL or blank study_process is `"unspecified"`. Those
#      rows are counted, carry their MW, and sit in the ordering with everybody
#      else. They are never dropped and never merged into a neighbour.
#
#   3. DIMMED AT ZERO, NOT MISSING AT ZERO. `active` carries EVERY key that
#      `all_statuses` carries, in the SAME order, including the 58 counties with
#      zero ACTIVE rows — as {"count": 0, "mw": 0.0}. A key that vanishes from
#      the active view would read as "no such county"; a key at zero reads as
#      "this county has nothing live", which is the true statement.
#
# The strings are keys AS BANKED. `KINGS/KERN` and `USA` are real county values
# in this table and they ride out exactly as CAISO wrote them — no title-casing,
# no splitting, no lookup, no repair. Same for the 22 study_process strings.

#: NULL/blank county or state. Named, never dropped, never folded (rule 2).
UNLOCATED_KEY = "unlocated"

#: NULL/blank study_process. 1 row carries it in the 2026-08-13 bank.
UNSPECIFIED_KEY = "unspecified"


def _dim_key(value: Any, missing_key: str) -> str:
    """A dimension value -> its rollup key. Missing becomes NAMED, not dropped.

    Only None and all-whitespace are "missing". Everything else rides out
    byte-identical to the bank — no strip, no case fold, no normalization. The
    2026-08-13 read finds 0 blank (non-null) values in county/state/
    study_process, so the whitespace branch is dormant today and correct anyway.
    """
    if value is None:
        return missing_key
    if isinstance(value, str) and not value.strip():
        return missing_key
    return value


def grain_block(
    rows: Iterable[dict],
    column: str,
    *,
    grain: str,
    missing_key: str,
) -> dict:
    """One dimension, FULLY rolled up: every key, all-statuses and active-only.

        { grain, key_count, missing_key, active_statuses, keys_with_zero_active,
          keys:         [key, ...]                 # the canonical order
          all_statuses: {key: {count, mw}},
          active:       {key: {count, mw}} }

    `keys` is the order both maps are built in — descending all-statuses MW,
    ties broken by the key string. It is emitted as a LIST because JSON object
    key order is a convention, not a guarantee, and a client that rebuilds the
    ordering from the list cannot be surprised by a parser that sorts.

    `active` is keyed identically to `all_statuses` — same keys, same order,
    zeros where a key has no ACTIVE rows (rule 3). The two maps are therefore
    zippable, and `sum(all_statuses[k].count) == len(rows)` at every grain.
    """
    all_b: dict[str, _Bucket] = {}
    act_b: dict[str, _Bucket] = {}
    for r in rows:
        k = _dim_key(r.get(column), missing_key)
        b = all_b.get(k)
        if b is None:
            b = all_b[k] = _Bucket()
            act_b[k] = _Bucket()
        b.add(r.get("capacity_mw"))
        if r.get("status") in ACTIVE_STATUSES:
            act_b[k].add(r.get("capacity_mw"))

    keys = sorted(all_b, key=lambda k: (-all_b[k].mw, k))
    return {
        "grain": grain,
        "key_count": len(keys),
        "missing_key": missing_key,
        "active_statuses": sorted(ACTIVE_STATUSES),
        "keys_with_zero_active": sum(1 for k in keys if act_b[k].count == 0),
        "keys": keys,
        "all_statuses": {k: all_b[k].out() for k in keys},
        "active": {k: act_b[k].out() for k in keys},
    }


def _county_state_pairing(rows: Iterable[dict]) -> dict:
    """Does each county key pair with exactly ONE state in the bank? MEASURED.

    It does NOT, as of the 2026-08-13 read: 100 named counties produce 106
    (county, state) pairs. `LINCOLN` appears under ID, NM and NV; `MARICOPA`
    under AZ and CA; `SAN BENITO` under CA and NV; and the 5 `unlocated` rows
    split 1 CA / 4 with no state at all. County names are not unique across
    states and this bank spans seven of them, so that is CAISO being correct,
    not CAISO being dirty.

    So no county key gets a single invented `state`. Each gets:

        key_states: [state, ...]   every state observed for it, always a list
        key_state:  state | null   the state ONLY when there is exactly one

    A null `key_state` means "this county spans more than one state in the
    bank", never "unknown" — `key_states` always names them. Picking one, or
    silently keying on (county, state), would have manufactured geography, and
    this lane does not infer location.
    """
    observed: dict[str, set] = {}
    for r in rows:
        c = _dim_key(r.get("county"), UNLOCATED_KEY)
        s = _dim_key(r.get("state"), UNLOCATED_KEY)
        observed.setdefault(c, set()).add(s)

    key_states = {c: sorted(observed[c]) for c in observed}
    ambiguous = sorted(c for c in key_states if len(key_states[c]) > 1)
    return {
        "key_states": key_states,
        "key_state": {c: (v[0] if len(v) == 1 else None) for c, v in key_states.items()},
        "state_pairing": {
            "clean": not ambiguous,
            "ambiguous_keys": ambiguous,
            "note": (
                "MEASURED, not assumed: county -> state is NOT a function in "
                "this bank. " + (
                    f"{len(ambiguous)} county key(s) appear under more than one "
                    f"state ({', '.join(ambiguous)}), so `key_state` is null for "
                    f"those and `key_states` names every state observed. County "
                    f"names are not unique across the seven states the queue "
                    f"spans; no state is picked, inferred or repaired."
                ) if ambiguous else (
                    "Every county key pairs with exactly one state on this read, "
                    "so `key_state` is populated for all of them. It is still "
                    "derived from the rows, not from a lookup table."
                )
            ),
        },
    }


def county_block(rows: Iterable[dict]) -> dict:
    """`by_county` — the full rollup, plus the measured state pairing.

    `grain_block` keys on county ALONE (not on (county, state)) so the key set
    is the county set: 101 keys today = 100 named + `unlocated`. The state
    information rides alongside as `key_states` / `key_state` rather than inside
    the key, because a compound key would silently split `MARICOPA` into two
    counties on a page that asked for counties.
    """
    rows = list(rows)
    block = grain_block(rows, "county", grain="county", missing_key=UNLOCATED_KEY)
    block.update(_county_state_pairing(rows))
    return block


# ---------------------------------------------------------------------------
# generations — the trend read, from the SNAPSHOTS table
# ---------------------------------------------------------------------------

def generations_block(agg_rows: Iterable[dict], *, source_table: str) -> dict:
    """One entry per banked snapshot_date, ascending. Depth stated, never faked.

        { source_table, depth, snapshot_dates: [...], note,
          entries: [ {snapshot_date, rows, mw_total,
                      by_status: {status: {count, mw}}} ] }

    `agg_rows` is the GROUP BY (snapshot_date, status) result — the whole read,
    already reduced in the database. Shaping it here rather than fetching every
    snapshot row keeps this O(dates x statuses) in Python no matter how deep the
    bank gets.

    DEPTH IS A FIRST-CLASS FIELD BECAUSE IT IS 1 TODAY. One generation is a
    POINT, not a trend, and `depth: 1` is what lets the page say so out loud
    instead of drawing a flat line through a single measurement and letting a
    reader infer stability that was never observed. The note says it in words
    too. Nothing here interpolates, back-fills, or borrows a second point from
    `caiso_interconnection_queue` — that table is upsert-in-place and holds the
    same single generation.
    """
    per_date: dict[Any, dict] = {}
    for r in agg_rows:
        d = r.get("snapshot_date")
        e = per_date.get(d)
        if e is None:
            e = per_date[d] = {"rows": 0, "mw": 0.0, "statuses": {}}
        n = _i(r.get("row_count")) or 0
        mw = _mw(r.get("mw"))
        e["rows"] += n
        e["mw"] += mw
        status = r.get("status")
        s = e["statuses"].get(status)
        if s is None:
            s = e["statuses"][status] = {"count": 0, "mw": 0.0}
        s["count"] += n
        s["mw"] += mw

    entries = []
    for d in sorted(per_date, key=lambda x: (x is None, x)):
        e = per_date[d]
        statuses = sorted(
            e["statuses"].items(), key=lambda kv: (-kv[1]["mw"], str(kv[0])))
        entries.append({
            "snapshot_date": _iso(d),
            "rows": e["rows"],
            "mw_total": _round_mw(e["mw"]),
            "by_status": {
                k: {"count": v["count"], "mw": _round_mw(v["mw"])}
                for k, v in statuses
            },
        })

    depth = len(entries)
    if depth == 0:
        note = (
            f"{source_table} holds no rows: depth 0. This is an absence, not an "
            f"error, and nothing is reconstructed from "
            f"caiso_interconnection_queue to fill it."
        )
    elif depth == 1:
        note = (
            f"depth 1 — exactly ONE generation is banked in {source_table} "
            f"({entries[0]['snapshot_date']}). That is a POINT, not a trend: no "
            f"change over time is measurable from it, and none is implied here. "
            f"A second generation makes this a series with no code change."
        )
    else:
        note = (
            f"depth {depth} — generations banked in {source_table} from "
            f"{entries[0]['snapshot_date']} to {entries[-1]['snapshot_date']}, "
            f"ascending. Each entry is that generation as banked; gaps between "
            f"dates are gaps in the bank and are not interpolated."
        )

    return {
        "source_table": source_table,
        "depth": depth,
        "snapshot_dates": [e["snapshot_date"] for e in entries],
        "note": note,
        "entries": entries,
    }


# ---------------------------------------------------------------------------
# The rollups payload
# ---------------------------------------------------------------------------

ROLLUP_NOTE = (
    "Full-cardinality rollups: every key at every grain, computed from the live "
    "caiso_interconnection_queue (the current generation). Nothing is folded "
    "into an 'other' bucket and nothing is truncated — `key_count` states the "
    "cardinality so a client can verify that. A NULL/blank county or state is "
    "the named key 'unlocated' and a NULL/blank study_process is 'unspecified'; "
    "those rows are counted with their MW, never dropped. `active` carries every "
    "key `all_statuses` carries, in the same order, with {count: 0, mw: 0.0} "
    "where a key has no ACTIVE rows — dimmed at zero, not missing at zero. Keys "
    "are the strings AS BANKED (county 'KINGS/KERN' and 'USA' are real values "
    "here); no normalization, geocoding or location inference happens anywhere "
    "on this path. `generations` is the only block read from "
    "caiso_interconnection_queue_snapshots."
)


def rollups_payload(
    rows: list[dict],
    *,
    generation_rows: Iterable[dict],
    generations_source: str,
    snapshot_date: Any,
    data_note: str,
) -> dict:
    """`/api/interconnection/rollups` — the full-cardinality grains + the trend.

        { snapshot_date, data_note, rollup_note, generated_from_rows,
          snapshot:    { snapshot_date, project_count, total_mw,
                         statuses: [{status, count, mw}] },   # == /summary's
          by_county:   { grain, key_count, missing_key, active_statuses,
                         keys_with_zero_active, keys, all_statuses, active,
                         key_states, key_state, state_pairing },
          by_state:    { ...same minus the county-only keys... },
          by_process:  { ...same... },
          generations: { source_table, depth, snapshot_dates, note, entries } }

    `snapshot` is the SAME block /summary serves, from the same helper and the
    same table, so a page rendering a rollup beside a summary chip cannot show
    two different totals. The three grain blocks are all derived from ONE row
    set for the same reason: computing them in three places would let three
    facets of one response describe three different universes.

    ADDITIVE ONLY. Nothing on /summary, /projects or /events is read, renamed or
    reshaped by this function; it is a new surface with new keys.
    """
    return {
        "snapshot_date": _iso(snapshot_date),
        "data_note": data_note,
        "rollup_note": ROLLUP_NOTE,
        "generated_from_rows": len(rows),
        "snapshot": _snapshot_block(rows, snapshot_date),
        "by_county": county_block(rows),
        "by_state": grain_block(
            rows, "state", grain="state", missing_key=UNLOCATED_KEY),
        "by_process": grain_block(
            rows, "study_process", grain="study_process",
            missing_key=UNSPECIFIED_KEY),
        "generations": generations_block(
            generation_rows, source_table=generations_source),
    }


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

#: `sort` whitelist: request value -> (SQL expression, default direction).
#: The VALUE is a code-side constant; the request string only ever selects a
#: key. Nothing a caller sends reaches the SQL text. A miss is a 400 in main.py,
#: never a silent fallback to a default sort.
SORT_WHITELIST: dict[str, tuple[str, str]] = {
    "capacity_mw": ("capacity_mw", "desc"),
    "queue_date": ("queue_date", "desc"),
    "proposed_completion_date": ("proposed_completion_date", "desc"),
    "project_name": ("project_name", "asc"),
}

DEFAULT_SORT = "queue_date"

PAGE_SIZE_DEFAULT = 50
PAGE_SIZE_MAX = 100


def project_row(r: dict) -> dict:
    """One table row. Raw generation_type AND the rollup fuel, both present.

    A client that disagrees with our rollup can always re-derive its own from
    `generation_type`; a client that trusts it reads `fuel`. Neither is hidden
    behind the other. `capacity_mw` is passed through as-is — a null capacity
    stays null on a ROW even though it contributes 0.0 to a SUM.
    """
    raw = r.get("generation_type")
    fuel, unmapped = (r.get("_fuel"), r.get("_unmapped"))
    if fuel is None:
        fuel, unmapped = classify_fuel(raw)
    return {
        "queue_position": r.get("queue_position"),
        "project_name": r.get("project_name"),
        "generation_type": raw,
        "fuel": fuel,
        "unmapped_components": list(unmapped or ()),
        "capacity_mw": _f(r.get("capacity_mw")),
        "status": r.get("status"),
        "study_process": r.get("study_process"),
        "interconnection_location": r.get("interconnection_location"),
        "county": r.get("county"),
        "state": r.get("state"),
        "transmission_owner": r.get("transmission_owner"),
        "deliverability": r.get("deliverability"),
        "queue_date": _iso(r.get("queue_date")),
        "proposed_completion_date": _iso(r.get("proposed_completion_date")),
        "actual_completion_date": _iso(r.get("actual_completion_date")),
        "withdrawn": r.get("withdrawn_date") is not None,
        "withdrawn_date": _iso(r.get("withdrawn_date")),
        "withdrawal_comment": r.get("withdrawal_comment"),
        "first_seen_at": _iso(r.get("first_seen_at")),
        "last_updated": _iso(r.get("last_updated")),
        "snapshot_date": _iso(r.get("snapshot_date")),
        "present_in_snapshot": r.get("present_in_snapshot"),
    }


def paginate(rows: list[dict], *, page: int, page_size: int) -> tuple[list[dict], dict]:
    """Slice, and state the slice. Page numbers are 1-based.

    A page past the end is an EMPTY page, not a 404 and not a clamp to the last
    page: the caller asked a well-formed question about a region of the result
    set that happens to be empty, and `total` + `page_count` in the meta say so.
    """
    total = len(rows)
    page_count = (total + page_size - 1) // page_size if page_size else 0
    start = (page - 1) * page_size
    window = rows[start:start + page_size]
    return window, {
        "page": page,
        "page_size": page_size,
        "total": total,
        "page_count": page_count,
        "has_more": start + len(window) < total,
    }


def projects_payload(
    rows: list[dict],
    *,
    page: int,
    page_size: int,
    sort: str,
    order: str,
    filters: dict,
    snapshot_date: Any,
    data_note: str,
) -> dict:
    """The paged table.

        { snapshot_date, data_note, sort, order, filters, page: {...},
          unmapped_types: [...], rows: [...] }

    `unmapped_types` describes the FILTERED result set, not the page — a reader
    paging through 158 Steam Turbine rows should not see the alarm blink in and
    out depending on which page they are on.
    """
    for r in rows:
        if "_fuel" not in r:
            fuel, unmapped = classify_fuel(r.get("generation_type"))
            r["_fuel"] = fuel
            r["_unmapped"] = unmapped

    window, meta = paginate(rows, page=page, page_size=page_size)
    return {
        "snapshot_date": _iso(snapshot_date),
        "data_note": data_note,
        "sort": sort,
        "order": order,
        "filters": filters,
        "page": meta,
        "unmapped_types": _unmapped_block(rows),
        "rows": [project_row(r) for r in window],
    }


def filter_by_fuel(rows: list[dict], fuel: Optional[str]) -> list[dict]:
    """The one filter SQL cannot do, because the rollup is a Python parse."""
    if fuel is None:
        return rows
    out = []
    for r in rows:
        f = r.get("_fuel")
        if f is None:
            f, unmapped = classify_fuel(r.get("generation_type"))
            r["_fuel"] = f
            r["_unmapped"] = unmapped
        if f == fuel:
            out.append(r)
    return out


# ---------------------------------------------------------------------------
# Events (the tape join)
# ---------------------------------------------------------------------------

def event_row(r: dict) -> dict:
    """One tape_interconnection_events row, with the change spelled out.

    `changed_fields` is derived from the keys present in old_values/new_values —
    it names WHAT moved without the client having to diff two jsonb blobs.
    """
    old = r.get("old_values") or {}
    new = r.get("new_values") or {}
    changed = sorted(set(old) | set(new))
    raw = r.get("generation_type")
    fuel, _unmapped = classify_fuel(raw)
    return {
        "id": _i(r.get("id")),
        "event_type": r.get("event_type"),
        "queue_position": r.get("queue_position"),
        "project_name": r.get("project_name"),
        "generation_type": raw,
        "fuel": fuel,
        "mw": _f(r.get("mw")),
        "changed_fields": changed,
        "old_values": old,
        "new_values": new,
        "detected_at": _iso(r.get("detected_at")),
        "snapshot_date": _iso(r.get("snapshot_date")),
    }


def events_payload(rows: list[dict], *, limit: int, note: Optional[str] = None) -> dict:
    """Newest-first tape events, or an HONEST EMPTY.

        { count, limit, event_types, rows: [...], note }

    An empty table is not an error and not a 404: it is a populated response
    with `rows: []`, `count: 0` and a `note` saying the ledger holds nothing
    yet. Nothing on this path invents an event, back-fills one from the queue
    table, or reconstructs history the bank does not hold.
    """
    shaped = [event_row(r) for r in rows]
    types = sorted({e["event_type"] for e in shaped if e["event_type"]})
    return {
        "count": len(shaped),
        "limit": limit,
        "event_types": types,
        "rows": shaped,
        "note": note if note is not None else (
            "tape_interconnection_events is empty: no queue changes have been "
            "banked yet. This is an absence, not an error — nothing is "
            "reconstructed from the queue snapshot to fill it."
            if not shaped else None
        ),
    }
