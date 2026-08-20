"""
Node Screener read layer — the pure half.

WHAT THIS LANE SERVES. Migration 182's bank: `node_stats_block` (per-node,
per-block distribution), `node_stats_hourly` (the per-HE rail carrying the
SUFFICIENT STATISTICS), and `zone_breadth_daily` (advance/decline by zone).
main.py owns the SQL and the routes; this module owns the vocabulary, the
validators, and the row shaping, so the shape can be tested without a database.

THE THREE RULES THIS MODULE EXISTS TO ENFORCE
─────────────────────────────────────────────

1. A NULL PERCENTILE IS AN ANSWER, NOT A HOLE.

   The writer refuses to publish a percentile it cannot support at the banked
   depth, and records how far up the ladder it was willing to go in
   `pctl_ceiling_lmp` / `pctl_ceiling_dart`. A node whose ceiling is `p50`
   carries p05/p25/p50 and NULL for p75/p95/p99, and those nulls are the
   refusal. Nothing here computes, interpolates, borrows, or defaults them, and
   `pctl_ceiling` rides on EVERY row so the client can render the refusal rather
   than a blank cell. `percentiles()` below is deliberately a pass-through — it
   exists so there is exactly one place that could ever break this rule, and it
   doesn't.

2. MEAN AND SIGMA ARE RECOMBINED, NEVER AVERAGED.

   `node_stats_block` banks percentiles but NO sums — the sufficient statistics
   (n, Σx, Σx²) live only on `node_stats_hourly`, one row per HE. So a block's
   mean is Σ(sum_x) / Σ(n) over the block's hours, and its sigma comes from
   Σ(sum_x²) by the same route. That is the true pooled moment, and it is NOT
   the mean of the 24 per-HE means (which would silently weight a short hour
   like a full one). The recombination itself is done in Postgres in `numeric`
   — see `_ns_pooled_exprs` in main.py — because float64 catastrophically
   cancels on Σx² − (Σx)²/n for prices at CAISO's magnitudes.

3. A BLOCK WHOSE HOURS ARE NOT AN HE SET GETS NO DERIVED STATISTICS.

   `NS_BLOCK_HOURS` maps the block vocabulary onto HE sets. Six of the eight
   named blocks are pure HE cuts, so the hourly rail recombines them exactly.
   ON_PEAK and OFF_PEAK are NOT: they are NERC 6x16 and its complement, which
   filter by DAY (Mon-Sat ex-holiday), and the hourly rail is aggregated across
   all days of the window with no day dimension left to filter on. There is no
   honest recombination, so `derived` comes back `available: false` with the
   reason attached, in the same spirit as rule 1. Guessing here would be the
   mean-of-means error wearing a different hat.

HOW THE HE SETS WERE ESTABLISHED. Measured, not assumed. For as_of 2026-08-16,
window_days=30, the first 40 pnode_ids in both markets, the per-HE rail summed
over each candidate HE set reproduces the block row's `hours_expected`,
`hours_present` AND `n_lmp` on 474 of 474 comparisons — every block, every node,
both markets, zero misses. The same probe is what DISQUALIFIED ON_PEAK: at
window_days=7 its hours_expected is 96 (16 hours x 6 days), while HE7..HE22 of
the hourly rail expects 112 (16 hours x 7 days), and its n_lmp of 89 matches no
HE set at all.

STRUCTURAL NULLS. DART is RT-minus-DA (FMM(RTPD) - DA, positive = RT above
DA, the sign convention the rest of this repo uses), so it is banked on the RTPD
row and is NULL on DAM by construction. Basis/congestion/loss are DAM-side decompositions
and are NULL on RTPD by the same construction. Measured on 720 hourly rows per
market at as_of 2026-08-16: DAM has 0/720 non-null n_dart and 720/720 non-null
n_basis; RTPD is the exact mirror. These nulls are NOT absence and must never be
reported as missing data — `structural_nulls()` names them per market so the
client can grey the column instead of drawing an empty chart.
"""

from __future__ import annotations

from datetime import date as _date
from typing import Any, Iterable, Optional


# ═══════════════════════════════════════════════════════════════════════════
# Vocabulary
# ═══════════════════════════════════════════════════════════════════════════

NS_BLOCK_TABLE = "node_stats_block"
NS_HOURLY_TABLE = "node_stats_hourly"
NS_BREADTH_TABLE = "zone_breadth_daily"

#: The two markets the bank carries. DAM = day-ahead, RTPD = real-time pre-dispatch.
NS_MARKETS: tuple[str, ...] = ("DAM", "RTPD")

#: Window lengths the writer banks, in days.
NS_WINDOWS: tuple[int, ...] = (3, 7, 14, 30, 90, 365)

#: The stance ladder's rungs — the four short windows, in the order a desk reads
#: them (3d is the twitch, 30d is the regime). 90/365 are banked and reachable
#: on the screener, but the ladder is a stance read, not an archive.
NS_LADDER_WINDOWS: tuple[int, ...] = (3, 7, 14, 30)

NS_DEFAULT_BLOCK = "ALL24"
NS_DEFAULT_WINDOW = 7
NS_DEFAULT_HE_RAIL_WINDOW = 30

#: block_key -> the HE set it is exactly equal to on the hourly rail.
#: HE is hour-ENDING, 1..24, the same convention `node_stats_hourly.he` uses.
#: Established by measurement — see the module docstring.
NS_BLOCK_HOURS: dict[str, tuple[int, ...]] = {
    "ALL24": tuple(range(1, 25)),
    "off_peak_overnight": (1, 2, 3, 4, 5, 6),
    "morning_on_peak": (7, 8),
    "midday_solar": (9, 10, 11, 12, 13, 14, 15, 16),
    "evening_on_peak": (17, 18, 19, 20, 21, 22),
    "late_off_peak": (23, 24),
    **{f"HE{he:02d}": (he,) for he in range(1, 25)},
}

#: The five named intraday blocks partition the day exactly, which is why their
#: recombinations can be trusted to sum back to ALL24. Asserted, not asserted-in-
#: prose: a typo in the table above becomes an import-time failure.
_NS_INTRADAY = (
    "off_peak_overnight", "morning_on_peak", "midday_solar",
    "evening_on_peak", "late_off_peak",
)
assert sorted(h for b in _NS_INTRADAY for h in NS_BLOCK_HOURS[b]) == list(range(1, 25))

#: Blocks the bank carries that are NOT HE sets, with the reason stated once.
#: Their block-level percentiles, coverage and ceiling are served in full — only
#: the recombined moments are withheld, because only those need a day filter the
#: hourly rail does not carry.
NS_DAY_FILTERED_BLOCKS: dict[str, str] = {
    "ON_PEAK": (
        "ON_PEAK is the NERC 6x16 block (HE7-HE22, Mon-Sat, excluding NERC "
        "holidays), so its hours are selected by DAY as well as by hour. "
        "node_stats_hourly is aggregated across every day in the window and "
        "keeps no day dimension, so no exact recombination exists. Measured at "
        "window_days=7: the block row expects 96 hours where HE7-HE22 of the "
        "rail expects 112. Percentiles, coverage and pctl_ceiling are served in "
        "full; only the recombined moments are withheld."
    ),
    "OFF_PEAK": (
        "OFF_PEAK is the complement of the NERC 6x16 block (every hour not in "
        "ON_PEAK, so it includes all of Sunday and all NERC holidays), which "
        "makes it day-selected for the same reason ON_PEAK is. "
        "node_stats_hourly keeps no day dimension to filter on, so no exact "
        "recombination exists. Percentiles, coverage and pctl_ceiling are "
        "served in full; only the recombined moments are withheld."
    ),
}

#: Every block_key the bank holds, in the order a menu should show them.
NS_BLOCK_KEYS: tuple[str, ...] = (
    ("ALL24", "ON_PEAK", "OFF_PEAK") + _NS_INTRADAY
    + tuple(f"HE{he:02d}" for he in range(1, 25))
)

#: The percentile ladder, in ladder order. These names are the wire contract.
NS_PERCENTILES: tuple[str, ...] = ("p05", "p25", "p50", "p75", "p95", "p99")

#: The measures the block table banks, and the market each is REAL on. A measure
#: absent from a market's row is structurally null, never missing.
NS_BLOCK_MEASURES: dict[str, tuple[str, ...]] = {
    "lmp": NS_MARKETS,
    "dart": ("RTPD",),
}

#: The measures the hourly rail banks. Same structural rule, wider set: the DAM
#: row carries the price decomposition, the RTPD row carries the spread.
NS_HOURLY_MEASURES: dict[str, tuple[str, ...]] = {
    "lmp": NS_MARKETS,
    "dart": ("RTPD",),
    "basis": ("DAM",),
    "congestion": ("DAM",),
    "loss": ("DAM",),
}

#: `sort` whitelist: request value -> the banked column it names. Only BANKED
#: statistics are sortable. `mean`/`sigma`/`min`/`max` are deliberately absent:
#: they are recombined from the hourly rail for the page that was already
#: selected, so sorting the UNIVERSE by them would mean recombining ~19k nodes
#: x 24 HEs per request. Offering that as a sort key would be offering a query
#: that cannot be served at this latency; saying so is better than timing out.
NS_SORT_KEYS: dict[str, str] = {
    "pnode_id": "b.pnode_id",
    "hours_expected": "b.hours_expected",
    "hours_present": "b.hours_present",
    "n_lmp": "b.n_lmp",
    "n_dart": "b.n_dart",
    **{f"{p}_lmp": f"b.{p}_lmp" for p in NS_PERCENTILES},
    **{f"{p}_dart": f"b.{p}_dart" for p in NS_PERCENTILES},
}

NS_DEFAULT_SORT = "p50_lmp"
NS_SORT_DIRS: dict[str, str] = {"asc": "ASC", "desc": "DESC"}
NS_DEFAULT_DIR = "desc"

NS_LIMIT_DEFAULT = 100
NS_LIMIT_MAX = 500
NS_OFFSET_MAX = 100_000

NS_BREADTH_DAYS_DEFAULT = 7
NS_BREADTH_DAYS_MAX = 90

#: THE KNOWN ISSUE, CARRIED RATHER THAN MASKED (filed 2026-08-19). The rows
#: stamped as_of 2026-08-13..2026-08-16 were computed BEFORE the writer repair
#: and hold pre-repair values; the restamp re-computes them in place. Nothing
#: here hides, adjusts or suppresses those rows — the API serves exactly what
#: the bank holds and attaches this note when the as_of it served falls in the
#: affected span, so the vintage is legible on the wire instead of in a wiki.
#:
#: The note is data-driven and SELF-CLEARING in the ordinary case: once the
#: writer advances past 2026-08-16 the served as_of leaves the span and the note
#: stops being emitted, with no code change. For the in-span dates themselves
#: the note carries `computed_at`, which is the only field that distinguishes a
#: restamped row from a pre-repair one — so a client can tell them apart even
#: while the span is still being served.
NS_PRE_REPAIR_SPAN: tuple[_date, _date] = (_date(2026, 8, 13), _date(2026, 8, 16))
NS_PRE_REPAIR_NOTE = (
    "This as_of falls in the 2026-08-13..2026-08-16 span whose rows were "
    "computed before the writer repair and may hold pre-repair values until the "
    "restamp lands. The bank is served exactly as it stands — nothing is masked, "
    "adjusted or withheld. Compare `computed_at` against the restamp to tell a "
    "repaired row from a pre-repair one."
)


# ═══════════════════════════════════════════════════════════════════════════
# Validation — every one returns an error STRING, never raises. main.py turns a
# string into the 400. Keeping the messages here keeps them testable and keeps
# them saying the same thing on all three routes.
# ═══════════════════════════════════════════════════════════════════════════

def validate_market(market: str) -> Optional[str]:
    if market not in NS_MARKETS:
        return f"`market` must be one of {list(NS_MARKETS)}; got '{market}'."
    return None


def validate_window(window_days: int) -> Optional[str]:
    if window_days not in NS_WINDOWS:
        return f"`window_days` must be one of {list(NS_WINDOWS)}; got {window_days}."
    return None


def validate_block(block_key: str) -> Optional[str]:
    if block_key not in NS_BLOCK_KEYS:
        return (
            f"`block_key` must be one of {list(NS_BLOCK_KEYS)}; got '{block_key}'."
        )
    return None


def validate_sort(sort: str) -> Optional[str]:
    if sort not in NS_SORT_KEYS:
        return (
            f"`sort` must be one of {sorted(NS_SORT_KEYS)}; got '{sort}'. "
            "Only BANKED statistics are sortable — mean/sigma/min/max are "
            "recombined from node_stats_hourly for the selected page, not "
            "banked per node, so they are not sort keys."
        )
    return None


def validate_dir(dir_: str) -> Optional[str]:
    if dir_ not in NS_SORT_DIRS:
        return f"`dir` must be one of {sorted(NS_SORT_DIRS)}; got '{dir_}'."
    return None


def validate_paging(limit: int, offset: int) -> Optional[str]:
    if not isinstance(limit, int) or limit < 1 or limit > NS_LIMIT_MAX:
        return f"`limit` must be an integer in 1..{NS_LIMIT_MAX}; got {limit}."
    if not isinstance(offset, int) or offset < 0 or offset > NS_OFFSET_MAX:
        return f"`offset` must be an integer in 0..{NS_OFFSET_MAX}; got {offset}."
    return None


def validate_breadth_days(days: int) -> Optional[str]:
    if not isinstance(days, int) or days < 1 or days > NS_BREADTH_DAYS_MAX:
        return f"`days` must be an integer in 1..{NS_BREADTH_DAYS_MAX}; got {days}."
    return None


def order_by_sql(sort: str, dir_: str) -> str:
    """The ORDER BY fragment for a VALIDATED (sort, dir) pair.

    The caller must have run `validate_sort`/`validate_dir` first; this indexes
    the whitelists and would KeyError on anything else, so no request string can
    reach the SQL text. NULLS LAST in both directions is deliberate and is a
    continuation of the ceiling rule: a node whose percentile was refused sinks
    to the bottom of the sort, and is never re-ranked as if its null were a zero
    or hoisted to the top as if it were an infinity. `pnode_id` is appended as
    the tiebreak so paging is stable across requests — without it, ties reorder
    between pages and rows go missing or double.
    """
    expr = NS_SORT_KEYS[sort]
    direction = NS_SORT_DIRS[dir_]
    if sort == "pnode_id":
        return f"{expr} {direction}"
    return f"{expr} {direction} NULLS LAST, b.pnode_id ASC"


# ═══════════════════════════════════════════════════════════════════════════
# Row shaping
# ═══════════════════════════════════════════════════════════════════════════

def f(v: Any) -> Optional[float]:
    """Decimal/int -> float, and None -> None. The ONLY numeric coercion in the
    lane. A null stays a null: there is no `or 0.0` anywhere in this file."""
    return None if v is None else float(v)


def i(v: Any) -> Optional[int]:
    return None if v is None else int(v)


def iso_d(v: Any) -> Optional[str]:
    if v is None:
        return None
    return v.isoformat() if hasattr(v, "isoformat") else str(v)


def iso_ts(v: Any) -> Optional[str]:
    return None if v is None else (v.isoformat() if hasattr(v, "isoformat") else str(v))


def coverage(hours_present: Any, hours_expected: Any) -> Optional[float]:
    """hours_present / hours_expected, or None when the denominator is 0.

    Coverage is a FIRST-CLASS field, not a footnote: a p95 over 40 of 168
    expected hours is a different object from a p95 over 168, and the client
    cannot tell them apart from the percentile alone. Both raw counts ride every
    row alongside this ratio — the ratio is a convenience, the counts are the
    record, and a zero expectation yields null rather than a divide-by-zero or
    a fake 0.0.
    """
    present, expected = i(hours_present), i(hours_expected)
    if not expected:
        return None
    return present / expected if present is not None else None


def percentiles(row: dict, measure: str) -> dict[str, Optional[float]]:
    """The percentile ladder for one measure, PASSED THROUGH.

    Rule 1 of this module lives here. A null in the bank is a null on the wire:
    not filled from a neighbour, not interpolated between the rungs that did
    survive, not defaulted to the median, not dropped from the dict so the key
    goes missing. Every rung is always present as a key; its value is the bank's
    answer, including when that answer is "I would not publish this one".
    """
    return {p: f(row.get(f"{p}_{measure}")) for p in NS_PERCENTILES}


def derived_block(
    agg: Optional[dict],
    block_key: str,
) -> dict:
    """The recombined moments for one measure over one block.

    `agg` is one row of the hourly recombination (n/mean/sigma/min/max already
    pooled in `numeric` by Postgres — see rule 2), or None when the block is not
    an HE set or the rail carries nothing for this node/measure.

    Shape is always a dict with `available`, so a client branches on one field
    rather than on a null. When it is false the reason is attached and says why
    — an unavailable statistic that explains itself is the same contract as a
    null percentile that carries its ceiling.
    """
    reason = NS_DAY_FILTERED_BLOCKS.get(block_key)
    if reason is not None:
        return {"available": False, "reason": reason, "source": None, "he_set": None}

    hours = NS_BLOCK_HOURS.get(block_key)
    if hours is None:
        return {
            "available": False,
            "reason": f"no HE set is known for block_key '{block_key}'.",
            "source": None,
            "he_set": None,
        }

    if agg is None or i(agg.get("n")) in (None, 0):
        return {
            "available": False,
            "reason": (
                "node_stats_hourly banks no rows for this node/measure at this "
                "as_of, window and market, so there are no sufficient statistics "
                "to recombine."
            ),
            "source": NS_HOURLY_TABLE,
            "he_set": list(hours),
        }

    return {
        "available": True,
        "n": i(agg.get("n")),
        "mean": f(agg.get("mean")),
        # Sample sigma, null at n<2 where it is undefined rather than 0.0.
        "sigma": f(agg.get("sigma")),
        "min": f(agg.get("min")),
        "max": f(agg.get("max")),
        "source": NS_HOURLY_TABLE,
        "he_set": list(hours),
        "method": (
            "pooled from sufficient statistics: mean = sum(sum_x)/sum(n), "
            "sigma = sqrt((sum(sum_x2) - sum(sum_x)^2/sum(n))/(sum(n)-1)), "
            "computed in numeric across the block's HE rows. NOT a mean of "
            "per-HE means."
        ),
    }


def measure_block(
    row: dict,
    measure: str,
    *,
    market: str,
    block_key: str,
    agg: Optional[dict],
) -> Optional[dict]:
    """One measure's stanza on a screener/ladder row, or None when structural.

    A measure the market does not carry returns None — that is the structural
    null, and it is the SAME null the bank holds. It is not an empty stanza
    dressed up with zeros, and `structural_nulls()` names it at the envelope
    level so the client knows the difference between "this market has no DART"
    and "this node has no data".
    """
    if market not in NS_BLOCK_MEASURES.get(measure, ()):
        return None
    return {
        "n": i(row.get(f"n_{measure}")),
        # Rides on EVERY row, populated or not — the refusal must be legible
        # even when every rung happens to have survived.
        "pctl_ceiling": row.get(f"pctl_ceiling_{measure}"),
        "percentiles": percentiles(row, measure),
        "derived": derived_block(agg, block_key),
    }


def screener_row(
    row: dict,
    *,
    market: str,
    block_key: str,
    aggs: dict[str, dict],
) -> dict:
    """One node's row: coverage first, then a stanza per measure.

    `aggs` is {measure: recombination row} for THIS node, already keyed by the
    caller. A measure missing from it lands on the "rail banks nothing" branch
    of `derived_block` rather than being silently dropped.
    """
    return {
        "pnode_id": row["pnode_id"],
        "hours_expected": i(row.get("hours_expected")),
        "hours_present": i(row.get("hours_present")),
        "coverage": coverage(row.get("hours_present"), row.get("hours_expected")),
        "first_banked": iso_d(row.get("first_banked")),
        "last_banked": iso_d(row.get("last_banked")),
        "lmp": measure_block(
            row, "lmp", market=market, block_key=block_key, agg=aggs.get("lmp")),
        "dart": measure_block(
            row, "dart", market=market, block_key=block_key, agg=aggs.get("dart")),
    }


def hourly_measure(row: dict, measure: str, *, market: str) -> Optional[dict]:
    """One measure on one HE of the rail, with its moments recombined.

    Same structural-null rule as `measure_block`. The rail has no percentiles
    and no ceiling — it banks n/Σx/Σx²/min/max — so this stanza carries the
    moments and the raw sums BOTH. The sums ride along on purpose: a client that
    wants to pool HEs its own way (a custom block, a subset of hours) can do the
    arithmetic itself from the same numbers the server used, and check the
    server's answer against it.
    """
    if market not in NS_HOURLY_MEASURES.get(measure, ()):
        return None
    n = i(row.get(f"n_{measure}"))
    return {
        "n": n,
        "mean": f(row.get(f"mean_{measure}")),
        "sigma": f(row.get(f"sigma_{measure}")),
        "min": f(row.get(f"min_{measure}")),
        "max": f(row.get(f"max_{measure}")),
        "sufficient_statistics": {
            "n": n,
            "sum": f(row.get(f"sum_{measure}")),
            "sum2": f(row.get(f"sum_{measure}2")),
        },
    }


def hourly_row(row: dict, *, market: str) -> dict:
    """One HE of the rail. Coverage is first-class here too — a 24-row rail with
    one thin hour is a different read from an even one, and the per-HE counts
    are what say so."""
    out = {
        "he": i(row.get("he")),
        "hours_expected": i(row.get("hours_expected")),
        "hours_present": i(row.get("hours_present")),
        "coverage": coverage(row.get("hours_present"), row.get("hours_expected")),
    }
    for measure in NS_HOURLY_MEASURES:
        out[measure] = hourly_measure(row, measure, market=market)
    return out


def breadth_row(row: dict) -> dict:
    """One (zone, market, day) of the advance/decline tape.

    `priced_coverage` is the same first-class coverage field the price rows
    carry: `net_breadth` of +527 means one thing over 537 priced nodes of 710
    and another over 70, and the ratio is what lets the client tell. The four
    counts are passed through exactly as banked.
    """
    return {
        "trade_date": iso_d(row.get("trade_date")),
        "nodes_in_zone": i(row.get("nodes_in_zone")),
        "nodes_priced": i(row.get("nodes_priced")),
        "nodes_up": i(row.get("nodes_up")),
        "nodes_down": i(row.get("nodes_down")),
        "net_breadth": i(row.get("net_breadth")),
        "priced_coverage": coverage(row.get("nodes_priced"), row.get("nodes_in_zone")),
        "computed_at": iso_ts(row.get("computed_at")),
    }


# ═══════════════════════════════════════════════════════════════════════════
# Envelope furniture
# ═══════════════════════════════════════════════════════════════════════════

def structural_nulls(market: str, *, measures: dict[str, tuple[str, ...]]) -> dict:
    """Which measures are null BY CONSTRUCTION for this market, and why.

    Emitted once per response rather than repeated on every row. Without it a
    client cannot distinguish "DART is null here because DAM has no DART" from
    "DART is null here because the bank is thin", and those two nulls call for
    completely different UI: a greyed column versus a coverage warning.
    """
    absent = [m for m, mkts in measures.items() if market not in mkts]
    return {
        "market": market,
        "measures": absent,
        "note": (
            "These measures are NULL BY CONSTRUCTION on this market, not "
            "missing: DART (RT minus DA) is banked on the RTPD "
            "row, and the basis/congestion/loss decomposition is banked on the "
            "DAM row. A null here is the schema, not a gap."
        ) if absent else "Every banked measure is populated on this market.",
    }


def vintage(as_of: Any, computed_at_min: Any = None, computed_at_max: Any = None) -> dict:
    """The data-vintage stamp every response carries.

    `as_of` is the window's end date — the thing the UI must display so a reader
    knows how old the numbers are. `computed_at` is when the writer stamped
    them, and the two are NOT the same clock: at the time of writing, as_of
    2026-08-16 was computed 2026-08-18 while as_of 2026-08-15 was computed
    2026-08-19, so the newest window is not the most recently computed one.
    Both ride the wire because either alone would mislead.
    """
    out: dict[str, Any] = {"as_of": iso_d(as_of)}
    if computed_at_min is not None or computed_at_max is not None:
        out["computed_at"] = {
            "min": iso_ts(computed_at_min),
            "max": iso_ts(computed_at_max),
        }
    note = pre_repair_note(as_of)
    if note is not None:
        out["known_issue"] = note
    return out


def pre_repair_note(as_of: Any) -> Optional[dict]:
    """The known-issue stamp, or None when the served as_of is outside the span.

    See NS_PRE_REPAIR_SPAN. This does not change a single served value; it only
    labels one.
    """
    if as_of is None:
        return None
    d = as_of if isinstance(as_of, _date) else None
    if d is None:
        try:
            d = _date.fromisoformat(str(as_of)[:10])
        except ValueError:
            return None
    lo, hi = NS_PRE_REPAIR_SPAN
    if not (lo <= d <= hi):
        return None
    return {
        "id": "pre_repair_as_of_span",
        "span": {"from": lo.isoformat(), "to": hi.isoformat()},
        "note": NS_PRE_REPAIR_NOTE,
    }


def block_catalog() -> list[dict]:
    """The block vocabulary as the screener will honour it — what each block is,
    which HEs it recombines over, and which ones withhold derived moments."""
    out = []
    for key in NS_BLOCK_KEYS:
        hours = NS_BLOCK_HOURS.get(key)
        out.append({
            "block_key": key,
            "he_set": list(hours) if hours else None,
            "derived_available": key not in NS_DAY_FILTERED_BLOCKS,
            "reason": NS_DAY_FILTERED_BLOCKS.get(key),
        })
    return out


def group_by_first(rows: Iterable[dict], key: str) -> dict[Any, list[dict]]:
    """Stable group-by that preserves input order within each group."""
    out: dict[Any, list[dict]] = {}
    for r in rows:
        out.setdefault(r.get(key), []).append(r)
    return out
