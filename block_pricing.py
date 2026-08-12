"""
Block pricing — the WECC block calendar and the per-node package arithmetic.

Pure functions only. No DB, no FastAPI, no I/O. `main.py` owns the SQL and the
routes and calls in here for every number it prints, exactly as it does with
`structures.py`. That split is what lets the whole calendar be unit-tested on a
checked-in fixture with no live database.

═══════════════════════════════════════════════════════════════════════════
THE BLOCK DEFINITIONS (WECC conventions)
═══════════════════════════════════════════════════════════════════════════
HE is hour-ending 1..24 in the trade date's PREVAILING LOCAL TIME, which is how
`atlas_node_hourly_stats.he` is already banked and how this file's callers
derive HE for the hub series (EXTRACT(HOUR ... AT TIME ZONE
'America/Los_Angeles') + 1 — the idiom already used throughout main.py).

    6x16   (On-Peak/HLH)  HE7-22, Mon-SAT, EXCLUDING NERC holidays
    off_peak (Wrap/LLH)   every hour not in 6x16
    7x8                   HE1-6, HE23, HE24 — all seven days
    7x16                  HE7-22 — all seven days   (the PPA settlement cut)
    atc    (7x24)         all hours
    2x16h                 HE7-22 on Sundays + NERC holidays

off_peak is defined as the COMPLEMENT of 6x16 and is then proven equal to
7x8 ∪ 2x16h by `assert_block_invariant` (and pinned by the suite). Writing it as
a complement rather than as a union is deliberate: the complement cannot
silently drift out of agreement with 6x16, which is the block everything else
settles against.

═══════════════════════════════════════════════════════════════════════════
THE HOLIDAY CALENDAR IS INJECTED, NEVER RE-IMPLEMENTED HERE
═══════════════════════════════════════════════════════════════════════════
main.py already owns the NERC calendar (`nerc_holidays` / `is_nerc_holiday`,
tests/test_nerc_calendar.py) with the observed-date rule this lane needs:
a fixed-date holiday landing on a SUNDAY is observed the following Monday; one
landing on a SATURDAY is NOT moved. Every function here takes an `is_holiday`
callable so there is exactly ONE calendar in this process. A second copy of the
rule living in this file is precisely how two numbers that should match quietly
stop matching, and main.py says so in its own comments.

RECONCILED AGAINST THE PANTRY, 2026-08-12. The pantry's independently-built
`caiso_blocks_daily` carries 6x16/7x16/7x24 for SP15 back to 2016-01-01. Over
its full depth the ONLY days it excludes from 6x16 but keeps in 7x16 are
Sundays (528) plus exactly the six observed NERC holidays per year — including
2026-07-04, a SATURDAY, which stays excluded and so confirms the no-shift rule
end to end. Block VALUES computed by these hour sets reproduce that table to
0.00000000 on every day checked (2026-07-01..08, 7x16 and 7x24). That is the
arithmetic reconciliation main.py's `blocks_omitted` note said no lane had done
yet, and it is why 6x16 is served here rather than withheld.

═══════════════════════════════════════════════════════════════════════════
DST
═══════════════════════════════════════════════════════════════════════════
The HE sets below are NOMINAL (subsets of 1..24). A real day carries 23, 24 or
25 clock hours across the PST/PDT switch. Nothing here assumes 24: every
aggregate COUNTS the rows that actually landed in the block and reports that
count as `hours`. On the fall-back day two distinct hours share one HE, so rows
are kept as a LIST and never keyed by (date, he) — collapsing them would drop
the 25th hour and quietly disagree with the pantry's own n_hours of 25.
"""

from __future__ import annotations

from datetime import date as _date
from datetime import datetime as _datetime
from datetime import time as _time
from datetime import timedelta as _timedelta
from datetime import timezone as _timezone
from typing import Callable, Iterable, Optional, Sequence
from zoneinfo import ZoneInfo

# ── The block menu ───────────────────────────────────────────────────────────
# Order is the wire order. `main.py` iterates this tuple, so a block cannot
# appear in one endpoint and be missing from another.
BLOCK_IDS = ("6x16", "off_peak", "7x8", "7x16", "atc", "2x16h")

# The hour sets, as nominal HE. Named once; every predicate below reads these.
_HE_ALL = tuple(range(1, 25))
_HE_PEAK = tuple(range(7, 23))          # HE7..HE22 — 16 hours
_HE_WRAP = (1, 2, 3, 4, 5, 6, 23, 24)   # HE1..6 + HE23 + HE24 — 8 hours

# Human-facing definitions, served verbatim on the wire so a consumer can check
# the server's rule against its own without reading this file.
BLOCK_DEFINITIONS = {
    "6x16":     "HE7-22, Mon-Sat, excluding NERC holidays (On-Peak / HLH)",
    "off_peak": "every hour not in 6x16 (Wrap / LLH); equals 7x8 union 2x16h",
    "7x8":      "HE1-6, HE23, HE24, all seven days",
    "7x16":     "HE7-22, all seven days (the PPA settlement cut)",
    "atc":      "all hours (7x24)",
    "2x16h":    "HE7-22 on Sundays and NERC holidays",
}

# Nominal hours/day, for the catalog only. NEVER used as a divisor or as a
# completeness test — `hours` on every row is the counted receipt.
BLOCK_NOMINAL_HOURS = {
    "6x16": 16, "off_peak": 8, "7x8": 8, "7x16": 16, "atc": 24, "2x16h": 0,
}


def is_6x16_day(d: _date, is_holiday: Callable[[_date], bool]) -> bool:
    """True when `d` carries a 6x16 block at all: Mon-Sat and not a NERC holiday.

    Monday..Saturday is weekday() 0..5; Sunday is 6. A holiday on ANY of those
    days removes the block — that includes a Saturday holiday, which the NERC
    observance rule does not move and which therefore genuinely lands as a
    Saturday with no on-peak block (2026-07-04 is the live example).
    """
    return d.weekday() != 6 and not is_holiday(d)


def block_hours(block: str, d: _date, is_holiday: Callable[[_date], bool]) -> tuple:
    """The NOMINAL HE set for `block` on `d`, as an ascending tuple.

    Empty tuple = the block does not exist on that date (6x16 on a Sunday,
    2x16h on an ordinary Tuesday). Empty is a calendar fact, not absence of
    data, and callers render it as such.
    """
    if block not in BLOCK_DEFINITIONS:
        raise ValueError(f"unknown block {block!r}; known: {', '.join(BLOCK_IDS)}")
    peak_day = is_6x16_day(d, is_holiday)
    if block == "6x16":
        return _HE_PEAK if peak_day else ()
    if block == "2x16h":
        return () if peak_day else _HE_PEAK
    if block == "off_peak":
        # THE COMPLEMENT of 6x16 — written as a complement on purpose.
        return _HE_WRAP if peak_day else _HE_ALL
    if block == "7x8":
        return _HE_WRAP
    if block == "7x16":
        return _HE_PEAK
    return _HE_ALL  # atc


def blocks_for_day(d: _date, is_holiday: Callable[[_date], bool]) -> dict:
    """Every block's HE set for one date, as {block: (he, ...)}."""
    return {b: block_hours(b, d, is_holiday) for b in BLOCK_IDS}


def assert_block_invariant(d: _date, is_holiday: Callable[[_date], bool]) -> None:
    """The calendar's own proof, for one date. Raises AssertionError on a break.

    Two properties, both of which a chart cannot survive losing:
      1. |6x16| + |off_peak| == 24 — the day is partitioned, with no hour
         counted twice and none dropped.
      2. off_peak == 7x8 ∪ 2x16h, EXACTLY — the wrap is the union of its two
         named halves and nothing else.
    """
    h = blocks_for_day(d, is_holiday)
    peak, wrap = set(h["6x16"]), set(h["off_peak"])
    assert len(peak) + len(wrap) == 24, (
        f"{d}: 6x16({len(peak)}) + off_peak({len(wrap)}) != 24")
    assert not (peak & wrap), f"{d}: 6x16 and off_peak overlap"
    assert wrap == set(h["7x8"]) | set(h["2x16h"]), (
        f"{d}: off_peak != 7x8 union 2x16h")


# ═══════════════════════════════════════════════════════════════════════════
# Percentiles — Postgres `percentile_cont`, linear interpolation
# ═══════════════════════════════════════════════════════════════════════════
# Byte-identical to main._an_percentile_cont on purpose: this lane's ladders sit
# beside the Nodes room's on the same screen, and two percentile conventions in
# one API is a bug that only shows up in a screenshot. The suite pins the two
# implementations against each other rather than trusting the comment.

def percentile_cont(sorted_vals: Sequence[float], q: float) -> float:
    """`sorted_vals` MUST be sorted ascending and non-empty."""
    n = len(sorted_vals)
    if n == 1:
        return float(sorted_vals[0])
    idx = q * (n - 1)
    lo = int(idx)
    hi = min(lo + 1, n - 1)
    frac = idx - lo
    return float(sorted_vals[lo]) + frac * (float(sorted_vals[hi]) - float(sorted_vals[lo]))


def _r(x: Optional[float], places: int = 4) -> Optional[float]:
    """Round for the wire; absence in, absence out."""
    return None if x is None else round(float(x), places)


def _mean(vals: Sequence[float]) -> Optional[float]:
    return (sum(vals) / len(vals)) if vals else None


PROFILE_QUANTILES = (("p25", 0.25), ("p50", 0.50), ("p75", 0.75))


def profile(rows: Iterable[dict], field: str) -> list:
    """Per-HE p25/p50/p75 (+ mean, n) of `field` over the window.

    HEs with no non-null sample are ABSENT from the returned list — never
    zero-filled and never emitted with null quantiles. An HE the bank never
    carried and an HE that priced at zero must not look alike.
    """
    by_he: dict = {}
    for r in rows:
        v = r.get(field)
        if v is None:
            continue
        by_he.setdefault(int(r["he"]), []).append(float(v))
    out = []
    for he in sorted(by_he):
        s = sorted(by_he[he])
        cell = {"he": he, "n": len(s), "mean": _r(_mean(s))}
        for name, q in PROFILE_QUANTILES:
            cell[name] = _r(percentile_cont(s, q))
        out.append(cell)
    return out


def heatmap(rows: Iterable[dict], field: str) -> list:
    """Per (month, HE) mean of `field`. Only months/HEs the bank actually holds.

    `month` is 'YYYY-MM'. Cells with no sample are absent, so a sparse first
    month renders as a short row rather than a wall of zeros.
    """
    cells: dict = {}
    for r in rows:
        v = r.get(field)
        if v is None:
            continue
        key = (r["trade_date"].strftime("%Y-%m"), int(r["he"]))
        cells.setdefault(key, []).append(float(v))
    return [
        {"month": m, "he": he, "avg": _r(_mean(vals)), "n": len(vals)}
        for (m, he), vals in sorted(cells.items())
    ]


# ═══════════════════════════════════════════════════════════════════════════
# Distribution — GridStatus's bin edges, verbatim
# ═══════════════════════════════════════════════════════════════════════════
# (lo, hi) with lo EXCLUSIVE and hi INCLUSIVE, so a price sits in exactly one
# bin. The open ends are the two the spec writes as inequalities: the first is
# `x <= -40` and the last is `x > 225`. Bin membership is pinned edge-by-edge by
# the suite because an off-by-one on a boundary is invisible in a chart.
DISTRIBUTION_BINS = (
    ("<=-40",     None,  -40.0),
    ("(-40,-20]", -40.0, -20.0),
    ("(-20,0]",   -20.0,   0.0),
    ("(0,10]",      0.0,  10.0),
    ("(10,20]",    10.0,  20.0),
    ("(20,30]",    20.0,  30.0),
    ("(30,50]",    30.0,  50.0),
    ("(50,75]",    50.0,  75.0),
    ("(75,125]",   75.0, 125.0),
    ("(125,175]", 125.0, 175.0),
    ("(175,225]", 175.0, 225.0),
    (">225",      225.0,  None),
)


def bin_of(value: float) -> str:
    """The label of the one bin `value` belongs to."""
    v = float(value)
    for label, lo, hi in DISTRIBUTION_BINS:
        if lo is None:
            if v <= hi:
                return label
        elif hi is None:
            if v > lo:
                return label
        elif lo < v <= hi:
            return label
    raise AssertionError(f"unbinned value {value!r}")  # unreachable: bins tile R


def distribution(rows: Iterable[dict], field: str) -> list:
    """Counts on the GridStatus bin edges.

    EVERY bin is present, including empty ones, with `n: 0`. This is the one
    place zero-fill is correct and absence-stating would be wrong: the bins are
    a fixed axis the client draws, not observed data, and a missing bar would
    silently rescale the chart. Contrast `profile`/`heatmap`, where a missing
    HE is genuinely unobserved.
    """
    counts = {label: 0 for label, _, _ in DISTRIBUTION_BINS}
    total = 0
    for r in rows:
        v = r.get(field)
        if v is None:
            continue
        counts[bin_of(float(v))] += 1
        total += 1
    return [
        {"bin": label, "lo": lo, "hi": hi, "n": counts[label],
         "share": _r(counts[label] / total, 6) if total else None}
        for label, lo, hi in DISTRIBUTION_BINS
    ]


# ═══════════════════════════════════════════════════════════════════════════
# TB2 / TB4 — the battery spread, in $/kW-month
# ═══════════════════════════════════════════════════════════════════════════
# Per DAY:   spread = mean(top N hourly prices) − mean(bottom N hourly prices)
#            day value = spread × N ÷ 1000
# Per MONTH: the SUM of the day values.
#
# UNITS, spelled out because this is where TB numbers usually go wrong:
#   $/MWh × N (MWh per MW of a 1-hour battery cycling N hours) = $/MW-day,
#   ÷ 1000 = $/kW-day. Summed over the month -> $/kW-month. This is the
#   GridStatus convention and the charter's.
#
# A day needs at least 2N priced hours, or the top-N and bottom-N sets would
# OVERLAP and the spread would count the same hour on both legs — inflating it.
# Short days are skipped and counted in `days_skipped`, never silently averaged
# away or zero-filled.

TB_N_VALUES = (2, 4)


def tb_day_value(day_prices: Sequence[float], n: int) -> Optional[float]:
    """One day's TB-N value in $/kW-day, or None when the day is too short."""
    vals = sorted(float(v) for v in day_prices if v is not None)
    if len(vals) < 2 * n:
        return None
    bottom = vals[:n]
    top = vals[-n:]
    spread = (sum(top) / n) - (sum(bottom) / n)
    return spread * n / 1000.0


def tb_monthly(rows: Iterable[dict], field: str, n: int, complete_months: set) -> dict:
    """TB-N by month + a summary over COMPLETE months only.

    `complete_months` is the set of 'YYYY-MM' the caller has determined are
    whole (see `month_completeness`). A partial month still ships — flagged
    `partial: true` — because hiding it would make the newest month vanish from
    a board that is supposed to be current; it is simply kept out of the
    summary, where a half-month would drag an average down for calendar reasons
    rather than market ones.
    """
    by_day: dict = {}
    for r in rows:
        v = r.get(field)
        if v is None:
            continue
        by_day.setdefault(r["trade_date"], []).append(float(v))

    by_month: dict = {}
    skipped: dict = {}
    for d, prices in by_day.items():
        m = d.strftime("%Y-%m")
        val = tb_day_value(prices, n)
        if val is None:
            skipped[m] = skipped.get(m, 0) + 1
            continue
        by_month.setdefault(m, []).append(val)

    months = []
    for m in sorted(set(by_month) | set(skipped)):
        day_vals = by_month.get(m, [])
        months.append({
            "month": m,
            "value": _r(sum(day_vals), 4) if day_vals else None,
            "days": len(day_vals),
            "days_skipped": skipped.get(m, 0),
            "partial": m not in complete_months,
        })
    complete = [m["value"] for m in months
                if not m["partial"] and m["value"] is not None]
    return {"n": n, "unit": "$/kW-month", "months": months,
            "summary": _summary(complete)}


def _summary(vals: Sequence[float]) -> dict:
    """avg/median/max/min over the values handed in, with the count.

    The caller decides WHICH values qualify (complete months only, everywhere in
    this file). `n_months: 0` with null stats is the honest answer before a
    single whole month has banked — never an average of one partial month
    dressed up as a summary.
    """
    if not vals:
        return {"n_months": 0, "avg": None, "median": None, "max": None, "min": None}
    s = sorted(float(v) for v in vals)
    return {
        "n_months": len(s),
        "avg": _r(_mean(s)),
        "median": _r(percentile_cont(s, 0.5)),
        "max": _r(s[-1]),
        "min": _r(s[0]),
    }


# ═══════════════════════════════════════════════════════════════════════════
# Month completeness
# ═══════════════════════════════════════════════════════════════════════════

def month_completeness(first_day: _date, last_day: _date) -> set:
    """The 'YYYY-MM' months FULLY covered by the banked window [first, last].

    A month is complete when the window contains its 1st and its last calendar
    day. The bank's own first and last months are therefore partial unless they
    happen to land exactly on month boundaries. This is a CALENDAR test, not a
    row-count test: a month with a missing mid-month day is still 'complete' by
    this definition and its own per-row `hours`/`days` counts are what expose
    the hole. Two different questions, two different receipts.
    """
    if first_day is None or last_day is None:
        return set()
    out = set()
    y, m = first_day.year, first_day.month
    while (y, m) <= (last_day.year, last_day.month):
        month_start = _date(y, m, 1)
        # The last calendar day of (y, m): the day before the 1st of the next.
        next_first = _date(y + 1, 1, 1) if m == 12 else _date(y, m + 1, 1)
        month_end = next_first - _timedelta(days=1)
        if first_day <= month_start and last_day >= month_end:
            out.add(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def monthly_avg(rows: Iterable[dict], field: str, complete_months: set) -> list:
    """Per-month mean of `field`, with the partial flag and the counts.

    Hour-weighted (a flat mean over the month's hourly samples), matching
    `blocks_monthly` — so the basis/DART monthly cuts and the block monthly cuts
    are the same kind of average and can be read side by side.
    """
    by_month: dict = {}
    for r in rows:
        v = r.get(field)
        if v is None:
            continue
        by_month.setdefault(r["trade_date"].strftime("%Y-%m"), []).append(
            (r["trade_date"], float(v)))
    out = []
    for m in sorted(by_month):
        pairs = by_month[m]
        vals = [v for _, v in pairs]
        out.append({
            "month": m,
            "avg": _r(_mean(vals)),
            "hours": len(vals),
            "days": len({d for d, _ in pairs}),
            "partial": m not in complete_months,
        })
    return out


# ═══════════════════════════════════════════════════════════════════════════
# Block aggregation
# ═══════════════════════════════════════════════════════════════════════════
# `rows` are (trade_date, he, <fields>) dicts — a LIST, never keyed by
# (date, he), so the fall-back day's duplicate HE survives (see the DST note at
# the top of this file).
#
# `hours` IS THE RECEIPT THAT THE CALENDAR RAN. It counts rows that carried a
# non-null price in that block on that date. It is never the nominal 16/8/24 and
# never inferred from the date — if the calendar silently stopped excluding
# holidays, 2x16h would report zero hours and 6x16 would report too many, and
# that is visible on the wire without reading any code.

# ── Day completeness — the frontier flag ────────────────────────────────────
# A daily row is only comparable to the rows beside it when the bank actually
# holds the whole day. The frontier day almost never does: the request lands
# mid-afternoon, the day's later hours have not published, and its block
# averages are computed over the hours that HAVE — a number that looks like a
# settled block price and is not one. `blocks_daily` flags those rows rather
# than dropping them (dropping the newest day from a board whose whole job is to
# be current would be worse) and the client renders the flag.
#
# The test is COUNTED, not clock-based: a date is complete when the bank holds
# as many rows for it as the date has clock hours. Nothing here asks what time
# it is — 'today' is not a concept this module has access to, and a bank that
# lags a day would make a wall-clock test lie in both directions.
#
# 23/24/25, NOT a constant 24 — the DST switch days genuinely carry 23 and 25
# hours (see the DST note at the top of this file), so a fixed 24 would flag
# every spring-forward day as partial forever and would miss a fall-back day
# that is genuinely an hour short.
MARKET_TZ = "America/Los_Angeles"


def hours_in_day(d: _date, tz: str = MARKET_TZ) -> int:
    """The number of CLOCK hours the trade date `d` carries: 23, 24 or 25."""
    zone = ZoneInfo(tz)
    start = _datetime.combine(d, _time(0), tzinfo=zone).astimezone(_timezone.utc)
    end = _datetime.combine(d + _timedelta(days=1), _time(0),
                            tzinfo=zone).astimezone(_timezone.utc)
    return int((end - start).total_seconds() // 3600)


def incomplete_days(rows: Iterable[dict], tz: str = MARKET_TZ) -> set:
    """The dates in `rows` the bank does not hold a full day of hours for.

    Rows are COUNTED, not deduplicated by HE: the fall-back day's two 1am rows
    share one HE and both are real hours, so counting distinct HE would report
    24 against an expected 25 and flag a complete day as partial.

    The frontier day is the usual member of this set. An interior day with a
    hole in the bank is also genuinely incomplete and is flagged the same way —
    the row is short either way, and which end of the window it sits at does not
    change what the average is missing.
    """
    counts: dict = {}
    for r in rows:
        d = r["trade_date"]
        counts[d] = counts.get(d, 0) + 1
    return {d for d, n in counts.items() if n < hours_in_day(d, tz)}


def _block_rows(rows: Iterable[dict], block: str,
                is_holiday: Callable[[_date], bool]) -> list:
    """Rows whose (date, he) falls in `block`."""
    out = []
    cache: dict = {}
    for r in rows:
        d = r["trade_date"]
        hrs = cache.get(d)
        if hrs is None:
            hrs = cache[d] = set(block_hours(block, d, is_holiday))
        if int(r["he"]) in hrs:
            out.append(r)
    return out


def blocks_daily(rows: Iterable[dict], is_holiday: Callable[[_date], bool],
                 fields: Sequence[str] = ("dam_lmp", "rt_avg"),
                 names: Sequence[str] = ("avg_dam", "avg_rt")) -> list:
    """Per (date, block) averages + counted hours, ascending by date then block.

    A date/block pair the calendar says does not exist (6x16 on a Sunday) is
    ABSENT — it is not a zero-hour row. A pair that exists but priced nothing is
    present with `hours: 0` and null averages, which is a different statement:
    the block was open and the bank is empty.

    Every row carries `partial`: true when the bank does not yet hold the whole
    trade date (the frontier day being the usual case — see `incomplete_days`),
    false when it does. The averages on a partial row are computed over the
    hours that landed, which is exactly why the flag has to travel with them.
    """
    out = []
    by_date: dict = {}
    for r in rows:
        by_date.setdefault(r["trade_date"], []).append(r)
    # Off `by_date`, not off `rows` — `rows` is an Iterable and the loop above
    # may have exhausted it.
    short = incomplete_days(r for rs in by_date.values() for r in rs)
    for d in sorted(by_date):
        for block in BLOCK_IDS:
            hrs = set(block_hours(block, d, is_holiday))
            if not hrs:
                continue  # the calendar says this block is not open today
            in_block = [r for r in by_date[d] if int(r["he"]) in hrs]
            cell = {"date": d.isoformat(), "block": block}
            counted = 0
            for field, name in zip(fields, names):
                vals = [float(r[field]) for r in in_block if r.get(field) is not None]
                cell[name] = _r(_mean(vals))
                counted = max(counted, len(vals))
            cell["hours"] = counted
            cell["partial"] = d in short
            out.append(cell)
    return out


def blocks_monthly(rows: Iterable[dict], is_holiday: Callable[[_date], bool],
                   complete_months: set,
                   fields: Sequence[str] = ("dam_lmp", "rt_avg"),
                   names: Sequence[str] = ("avg_dam", "avg_rt")) -> dict:
    """Per-block monthly table + a summary over complete months only.

    The monthly average is the mean over the block's HOURS in the month — a flat
    hourly mean, which is what a block price IS. It is deliberately NOT a mean
    of daily means: those differ whenever days carry unequal hour counts (every
    DST month, and any month with a partial day), and the hour-weighted figure
    is the one that reconciles against the pantry's `caiso_blocks_daily`.
    """
    out = {}
    for block in BLOCK_IDS:
        in_block = _block_rows(rows, block, is_holiday)
        by_month: dict = {}
        for r in in_block:
            by_month.setdefault(r["trade_date"].strftime("%Y-%m"), []).append(r)
        months = []
        for m in sorted(by_month):
            mrows = by_month[m]
            cell = {"month": m}
            counted = 0
            for field, name in zip(fields, names):
                vals = [float(x[field]) for x in mrows if x.get(field) is not None]
                cell[name] = _r(_mean(vals))
                counted = max(counted, len(vals))
            cell["hours"] = counted
            cell["days"] = len({x["trade_date"] for x in mrows})
            cell["partial"] = m not in complete_months
            months.append(cell)
        primary = names[0]
        complete = [c[primary] for c in months
                    if not c["partial"] and c[primary] is not None]
        out[block] = {
            "definition": BLOCK_DEFINITIONS[block],
            "monthly": months,
            "summary": _summary(complete),
        }
    return out


# ═══════════════════════════════════════════════════════════════════════════
# RT coverage
# ═══════════════════════════════════════════════════════════════════════════
# The rule, stated here and echoed verbatim on the wire: RT block averages are
# suppressed when `rt_n` coverage is below 20 of 24 HEs on MORE THAN HALF the
# window's days. A node whose real-time is that thin does not have a real-time
# block price, and printing one from the hours that did land would be a average
# of whichever hours happened to report — a number that looks like a block and
# is not one.

RT_COVERAGE_MIN_HE = 20
RT_COVERAGE_MIN_HE_OF = 24
RT_COVERAGE_RULE = (
    "rt block averages are null when fewer than "
    f"{RT_COVERAGE_MIN_HE} of {RT_COVERAGE_MIN_HE_OF} HEs carry real-time "
    "coverage (rt_n > 0) on more than half the days in the window"
)


def rt_available(rows: Iterable[dict]) -> bool:
    """Whether THIS board's own window can carry a real-time series at all.

    The same rule as `rt_coverage`, reduced to the one boolean a board needs to
    decide between drawing an RT series and drawing the absence. Evaluated over
    the rows the board is actually drawn from, so two boards over two windows
    can honestly disagree.

    An EMPTY window is False, not True. `rt_coverage` reports `suppressed:
    false` on no rows because the rule cannot fire without days to count — that
    is the right answer to "did the rule trip" and the wrong answer to "can this
    board show RT", which is the question here.
    """
    cov = rt_coverage(rows)
    return cov["days"] > 0 and not cov["suppressed"]


def rt_coverage(rows: Iterable[dict]) -> dict:
    """Whether RT is dense enough to price blocks, with the counts behind it."""
    by_day: dict = {}
    for r in rows:
        if (r.get("rt_n") or 0) > 0:
            by_day.setdefault(r["trade_date"], set()).add(int(r["he"]))
    all_days = {r["trade_date"] for r in rows}
    thin = sum(1 for d in all_days
               if len(by_day.get(d, ())) < RT_COVERAGE_MIN_HE)
    n_days = len(all_days)
    suppressed = n_days > 0 and thin > n_days / 2
    return {
        "rule": RT_COVERAGE_RULE,
        "days": n_days,
        "days_below_threshold": thin,
        "suppressed": suppressed,
    }
