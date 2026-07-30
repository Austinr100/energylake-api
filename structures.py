"""
structures.py — payoff arithmetic for the Analytics Department, Room 2.

THE POSTURE (load-bearing — this module is a CALCULATOR and a REPLAY ENGINE,
not a pricing desk and not advice). Three rails, verbatim from the spec:

  1. NO FORWARD PRICING. Everything here computes payoff *given* user inputs
     and *realized* history. There are no vol surfaces, no premium valuation,
     and no forecast of any settle anywhere in this file. Wherever history is
     shown the caller says "would have paid".

  2. DEPTH-HONEST EVERYWHERE. Every replay states its window. Hub strips run
     years deep; nodal legs run weeks. A month is either BANKED WHOLE or it is
     not in the replay — there is no partial month, no interpolation, no fill
     of a missing power hour. The gate is derived per date from the tz
     database, never from a hardcoded calendar.

  3. RANKINGS ARE DESCRIPTIVE SORTS of historical arithmetic ("ranked by
     realized payoff over N banked months"), never a recommendation register.

Everything in this module is pure: no I/O, no clock, no database. The route
layer in main.py reads banked rows and hands them here. That split is what
makes the hand-derived HRCO fixture in tests/test_structures.py a test of the
arithmetic rather than a test of a query.

VOCABULARY
    block          a set of hour-endings, e.g. 7x16 = HE7..HE22 every day
    block average  the hour-weighted mean price over a block across a month
    leg            what settles: a CAISO trading hub or a pricing node
    MWh            size_mw x block hours in the month (derived, never assumed)
    banked month   a calendar month where every day carries every block hour
"""

from __future__ import annotations

from datetime import date as _date
from datetime import datetime as _datetime
from datetime import timedelta as _timedelta
from datetime import timezone as _timezone
from zoneinfo import ZoneInfo

# CAISO is a Pacific market — every block boundary is a Pacific wall-clock
# hour, and every day boundary is a Pacific calendar day. Same constant the
# rest of the API uses; restated here so this module stays import-free of main.
MARKET_TZ = "America/Los_Angeles"
_PT = ZoneInfo(MARKET_TZ)


class StructureError(ValueError):
    """A structure definition the calculator refuses to evaluate.

    Carries the offending field so the route layer can return a 400 that names
    it. Never raised for a data shortfall — a missing month is honest absence
    in the payload, not an error.
    """

    def __init__(self, field: str, message: str):
        self.field = field
        self.message = message
        super().__init__(f"{field}: {message}")


# ═══════════════════════════════════════════════════════════════════════════
# Blocks
# ═══════════════════════════════════════════════════════════════════════════
#
# Hours are PT hour-STARTS (0..23). HE (hour-ending) = hour-start + 1, so
# HE7 is the hour spanning 06:00-07:00 PT and its hour-start is 6.
#
# ARBITERED, not asserted: 7x16 and 7x24 computed from caiso_lmp_da_hourly by
# this definition reproduce the independently-built caiso_blocks_daily lane to
# the last decimal on every fully-covered day checked (2026-06-01..2026-06-09,
# SP15: 9.335716875, 14.5328445833333, ...). The reconciliation is what pins
# these hour sets as the house block grammar.
#
# 6x16 IS DELIBERATELY ABSENT from the v0 menu. The NERC on-peak block needs a
# holiday calendar, and the two calendars in this codebase disagree: main.py's
# locked _NERC_HOLIDAYS uses OBSERVED dates (2026-07-03) while the pantry's
# caiso_blocks_daily 6x16 lane uses ACTUAL dates (it publishes a 6x16 for Fri
# 2026-07-03 and withholds one for Sat 2026-07-04). Shipping 6x16 would mean
# shipping a block that silently disagrees with the pantry's own column. The
# three blocks below are holiday-calendar-independent, so they cannot. The
# divergence is filed as a data-roadmap item, not papered over here.

_HOURS_7x16 = tuple(range(6, 22))                       # HE7..HE22
_HOURS_7x8 = tuple(list(range(0, 6)) + [22, 23])        # HE1..HE6 + HE23..HE24
_HOURS_7x24 = tuple(range(0, 24))                       # ATC

BLOCKS: dict[str, dict] = {
    "7x16": {
        "id": "7x16",
        "label": "7x16 on-peak",
        "hours": _HOURS_7x16,
        "definition": "HE7-HE22 (PT hour-starts 6-21), every day of the week.",
        "nominal_hours_per_day": 16,
    },
    "7x8": {
        "id": "7x8",
        "label": "7x8 off-peak",
        "hours": _HOURS_7x8,
        "definition": (
            "HE1-HE6 plus HE23-HE24 (PT hour-starts 0-5, 22-23), every day. "
            "The exact complement of 7x16 within the day."
        ),
        "nominal_hours_per_day": 8,
    },
    "7x24": {
        "id": "7x24",
        "label": "7x24 ATC",
        "hours": _HOURS_7x24,
        "definition": "Every hour of every day (around-the-clock).",
        "nominal_hours_per_day": 24,
    },
}

# The partition identity that makes the menu self-arbitering:
#   7x16 x h16 + 7x8 x h8 == 7x24 x h24   (hour-weighted, exactly)
# 7x16 and 7x8 are disjoint and their union is 7x24. Pinned in the tests.
assert set(_HOURS_7x16) | set(_HOURS_7x8) == set(_HOURS_7x24)
assert not (set(_HOURS_7x16) & set(_HOURS_7x8))


def expected_block_hours(day: _date, block: str) -> int:
    """How many block hours a Pacific calendar day is SUPPOSED to contain.

    Derived per date from the tz database — never a hardcoded DST calendar.
    This is the completeness gate's expectation, and it is why the gate is
    correct across the PST/PDT switch:

      * 7x16 is 16 hours on all 365 days. Spring-forward skips 02:00 and
        fall-back repeats 01:00, both OUTSIDE HE7-HE22, so the on-peak block
        is untouched by DST.
      * 7x24 is 23 hours on spring-forward, 25 on fall-back, 24 otherwise.
      * 7x8 absorbs the whole DST effect: 7 hours on spring-forward, 9 on
        fall-back, 8 otherwise.

    Counted by walking the day's real UTC span and bucketing each instant by
    the PT wall-clock hour it lands on, so a repeated wall-clock hour counts
    TWICE (both 01:00 PDT and 01:00 PST are real, separately-priced hours) —
    the same reason the pantry's TB4 lane carries (he, price) PAIRS rather
    than a dict keyed by HE.
    """
    hours = _block_hours(block)
    start = _datetime.combine(day, _datetime.min.time(), tzinfo=_PT)
    end = _datetime.combine(day + _timedelta(days=1), _datetime.min.time(), tzinfo=_PT)
    # Walk in UTC so a 23- or 25-hour day is walked as it really is.
    cur = start.astimezone(_timezone.utc)
    stop = end.astimezone(_timezone.utc)
    n = 0
    while cur < stop:
        if cur.astimezone(_PT).hour in hours:
            n += 1
        cur += _timedelta(hours=1)
    return n


def expected_month_hours(year: int, month: int, block: str) -> tuple[int, int]:
    """(days_in_month, expected block hours for the whole month)."""
    dim = days_in_month(year, month)
    total = sum(
        expected_block_hours(_date(year, month, d), block) for d in range(1, dim + 1)
    )
    return dim, total


def days_in_month(year: int, month: int) -> int:
    first = _date(year, month, 1)
    nxt = _date(year + 1, 1, 1) if month == 12 else _date(year, month + 1, 1)
    return (nxt - first).days


def _block_hours(block: str) -> tuple[int, ...]:
    spec = BLOCKS.get(block)
    if spec is None:
        raise StructureError(
            "block", f"unknown block '{block}'; valid blocks are {sorted(BLOCKS)}."
        )
    return spec["hours"]


def block_hour_list(block: str) -> list[int]:
    """PT hour-starts for a block, as a plain list for SQL parameter binding."""
    return list(_block_hours(block))


# ═══════════════════════════════════════════════════════════════════════════
# The structures menu
# ═══════════════════════════════════════════════════════════════════════════
#
# All closed-form arithmetic on a monthly block average. Nothing here needs a
# volatility, a discount rate, or a forward curve — which is precisely the
# point of rail #1.

# Fields each structure requires beyond leg/block/size_mw.
STRUCTURES: dict[str, dict] = {
    "swap": {
        "id": "swap",
        "label": "Fixed-for-floating swap",
        "requires": ("fixed_price",),
        "settlement": "(block average - fixed price) x MWh, per month. Two-sided.",
        "needs_gas": False,
        "y_basis": "per_mwh",
    },
    "asian_call": {
        "id": "asian_call",
        "label": "Monthly-average (Asian) call",
        "requires": ("strike",),
        "settlement": "max(0, block average - strike) x MWh, per month.",
        "needs_gas": False,
        "y_basis": "per_mwh",
    },
    "asian_put": {
        "id": "asian_put",
        "label": "Monthly-average (Asian) put",
        "requires": ("strike",),
        "settlement": "max(0, strike - block average) x MWh, per month.",
        "needs_gas": False,
        "y_basis": "per_mwh",
    },
    "asian_digital_call": {
        "id": "asian_digital_call",
        "label": "Monthly-average digital call",
        "requires": ("strike", "payout"),
        "settlement": (
            "A fixed payout if the block average CLEARS the strike "
            "(average >= strike), otherwise zero. The payout is a lump sum per "
            "month in dollars — it does NOT scale with size_mw."
        ),
        "needs_gas": False,
        "y_basis": "total",
    },
    "asian_digital_put": {
        "id": "asian_digital_put",
        "label": "Monthly-average digital put",
        "requires": ("strike", "payout"),
        "settlement": (
            "A fixed payout if the block average falls to or below the strike "
            "(average <= strike), otherwise zero. Lump sum per month; does NOT "
            "scale with size_mw."
        ),
        "needs_gas": False,
        "y_basis": "total",
    },
    "hrco_monthly": {
        "id": "hrco_monthly",
        "label": "HRCO — monthly-average exercise",
        "requires": ("heat_rate", "gas_index"),
        "settlement": (
            "Strike = heat_rate x MONTHLY-AVERAGE gas index + VOM adder. "
            "Payoff = max(0, block average - strike) x MWh. One exercise "
            "decision for the whole month."
        ),
        "needs_gas": True,
        "y_basis": "per_mwh",
    },
    "hrco_daily": {
        "id": "hrco_daily",
        "label": "HRCO — daily exercise, summed monthly",
        "requires": ("heat_rate", "gas_index"),
        "settlement": (
            "Per day: strike = heat_rate x THAT DAY's gas index + VOM adder; "
            "payoff = max(0, that day's block average - strike) x size_mw x "
            "that day's block hours. Summed over the month. Daily exercise is "
            "always >= the monthly-average variant on the same inputs (a sum "
            "of maxes dominates the max of a sum) — the two are offered side "
            "by side because that gap is the whole point of the distinction."
        ),
        "needs_gas": True,
        "y_basis": "per_mwh",
    },
}

# Which structures settle on a monthly average (all of them, in v0) and which
# need a per-day gas print. Only hrco_daily exercises daily.
_DAILY_EXERCISE = frozenset({"hrco_daily"})


# ═══════════════════════════════════════════════════════════════════════════
# Definition validation + normalization
# ═══════════════════════════════════════════════════════════════════════════

_MAX_SIZE_MW = 10_000.0
_MAX_ABS_PRICE = 100_000.0
_MAX_HEAT_RATE = 100.0
_MAX_PAYOUT = 1_000_000_000.0
_MAX_WINDOW_MONTHS = 600


def _num(raw: dict, field: str, *, required: bool, lo: float, hi: float,
         default: float | None = None) -> float | None:
    if field not in raw or raw[field] is None:
        if required:
            raise StructureError(field, "required for this structure.")
        return default
    v = raw[field]
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise StructureError(field, f"must be a number; got {type(v).__name__}.")
    v = float(v)
    if v != v or v in (float("inf"), float("-inf")):
        raise StructureError(field, "must be finite.")
    if not (lo <= v <= hi):
        raise StructureError(field, f"must be between {lo} and {hi}; got {v}.")
    return v


def normalize_definition(raw: dict, *, known_gas_indices: set[str] | None = None) -> dict:
    """Validate a structure definition and return it canonicalized.

    THIS FUNCTION IS THE CONTRACT. The shape it accepts and the shape it
    returns are pinned in tests/test_structures.py alongside the Python mirror
    of the client's shape guard; the dashboard's builder panel emits exactly
    this and nothing else.

    Raises StructureError (which the route turns into a 400 naming the field).
    """
    if not isinstance(raw, dict):
        raise StructureError("structure", "definition must be an object.")

    kind = raw.get("structure")
    if not isinstance(kind, str) or kind not in STRUCTURES:
        raise StructureError(
            "structure",
            f"unknown structure '{kind}'; valid structures are {sorted(STRUCTURES)}.",
        )
    spec = STRUCTURES[kind]

    block = raw.get("block", "7x16")
    if not isinstance(block, str):
        raise StructureError("block", "must be a string.")
    _block_hours(block)  # raises StructureError on an unknown block

    leg = raw.get("leg")
    if not isinstance(leg, dict):
        raise StructureError("leg", "must be an object {kind, id}.")
    leg_kind = leg.get("kind")
    if leg_kind not in ("hub", "node"):
        raise StructureError("leg.kind", "must be 'hub' or 'node'.")
    leg_id = leg.get("id")
    if not isinstance(leg_id, str) or not leg_id.strip():
        raise StructureError("leg.id", "required; must be a non-empty string.")
    leg_id = leg_id.strip().upper()

    out: dict = {
        "structure": kind,
        "label": spec["label"],
        "leg": {"kind": leg_kind, "id": leg_id},
        "block": block,
        "size_mw": _num(raw, "size_mw", required=False, lo=0.0, hi=_MAX_SIZE_MW,
                        default=1.0),
        "settlement": spec["settlement"],
        "y_basis": spec["y_basis"],
    }

    if "fixed_price" in spec["requires"]:
        out["fixed_price"] = _num(raw, "fixed_price", required=True,
                                  lo=-_MAX_ABS_PRICE, hi=_MAX_ABS_PRICE)
    if "strike" in spec["requires"]:
        out["strike"] = _num(raw, "strike", required=True,
                             lo=-_MAX_ABS_PRICE, hi=_MAX_ABS_PRICE)
    if "payout" in spec["requires"]:
        out["payout"] = _num(raw, "payout", required=True, lo=0.0, hi=_MAX_PAYOUT)
    if "heat_rate" in spec["requires"]:
        out["heat_rate"] = _num(raw, "heat_rate", required=True, lo=0.0,
                                hi=_MAX_HEAT_RATE)
        # The VOM adder is optional and defaults to zero — an HRCO with no
        # adder is a legitimate structure, not an incomplete one.
        out["vom_adder"] = _num(raw, "vom_adder", required=False,
                                lo=-_MAX_ABS_PRICE, hi=_MAX_ABS_PRICE, default=0.0)
    if "gas_index" in spec["requires"]:
        gi = raw.get("gas_index")
        if not isinstance(gi, str) or not gi.strip():
            raise StructureError("gas_index", "required for an HRCO.")
        gi = gi.strip()
        if known_gas_indices is not None and gi not in known_gas_indices:
            # Offer exactly the banked indices BY NAME. An unbanked index is a
            # 400 that lists what is actually held — never a silent proxy.
            raise StructureError(
                "gas_index",
                f"'{gi}' is not a banked gas index; banked indices are "
                f"{sorted(known_gas_indices)}.",
            )
        out["gas_index"] = gi

    months = raw.get("window_months")
    if months is not None:
        if isinstance(months, bool) or not isinstance(months, int):
            raise StructureError("window_months", "must be an integer or null.")
        if not (1 <= months <= _MAX_WINDOW_MONTHS):
            raise StructureError(
                "window_months",
                f"must be between 1 and {_MAX_WINDOW_MONTHS}, or null for all "
                "banked months.",
            )
    out["window_months"] = months
    return out


def needs_gas(definition: dict) -> bool:
    return STRUCTURES[definition["structure"]]["needs_gas"]


def is_daily_exercise(definition: dict) -> bool:
    return definition["structure"] in _DAILY_EXERCISE


# ═══════════════════════════════════════════════════════════════════════════
# The payoff function — one settle in, one payoff out
# ═══════════════════════════════════════════════════════════════════════════

def payoff_per_mwh(definition: dict, settle: float, *, strike: float | None = None) -> float:
    """Payoff in $/MWh for one settle price.

    `strike` overrides the definition's own strike — that is how an HRCO gets
    priced off a gas print (its strike is DERIVED: heat_rate x gas + adder).
    Digitals return 0.0 here; their payoff is a lump sum, not a per-MWh rate
    (see payoff_total).
    """
    kind = definition["structure"]
    if kind == "swap":
        return settle - definition["fixed_price"]
    if kind == "asian_call":
        return max(0.0, settle - definition["strike"])
    if kind == "asian_put":
        return max(0.0, definition["strike"] - settle)
    if kind in ("asian_digital_call", "asian_digital_put"):
        return 0.0
    if kind in ("hrco_monthly", "hrco_daily"):
        k = definition["strike"] if strike is None else strike
        if k is None:
            raise StructureError("gas_index", "an HRCO strike needs a gas price.")
        return max(0.0, settle - k)
    raise StructureError("structure", f"no payoff rule for '{kind}'.")


def payoff_total(definition: dict, settle: float, mwh: float, *,
                 strike: float | None = None) -> float:
    """Payoff in dollars for one settle price over `mwh` of volume."""
    kind = definition["structure"]
    if kind == "asian_digital_call":
        return definition["payout"] if settle >= definition["strike"] else 0.0
    if kind == "asian_digital_put":
        return definition["payout"] if settle <= definition["strike"] else 0.0
    return payoff_per_mwh(definition, settle, strike=strike) * mwh


def breakeven(definition: dict, *, strike: float | None = None) -> float | None:
    """The settle at which payoff crosses zero — marked on the diagram.

    None when there is no single crossing (a digital steps rather than
    crosses; its threshold IS the strike and is reported separately).
    """
    kind = definition["structure"]
    if kind == "swap":
        return definition["fixed_price"]
    if kind in ("asian_call", "asian_put"):
        return definition["strike"]
    if kind in ("hrco_monthly", "hrco_daily"):
        return definition["strike"] if strike is None else strike
    return None


# ═══════════════════════════════════════════════════════════════════════════
# The payoff diagram — the Robinhood view
# ═══════════════════════════════════════════════════════════════════════════

DIAGRAM_POINTS = 61


def payoff_diagram(definition: dict, *, strike: float | None = None,
                   mwh: float | None = None,
                   settle_lo: float | None = None,
                   settle_hi: float | None = None) -> dict:
    """Payoff vs settle price for the configured structure.

    A PURE FUNCTION OF INPUTS — no banked data, no depth, no window. This is
    what lets the diagram render instantly and render even when the replay is
    honestly absent (a nodal leg with no banked month still draws its payoff
    diagram; it just cannot be replayed).

    `mwh` scales the dollar series. When the caller has no month in mind it
    passes None and gets a per-MWh series with `payoff_total` null — the
    client scales. It is NEVER filled with a guessed month length.
    """
    be = breakeven(definition, strike=strike)
    threshold = be
    if threshold is None:
        threshold = definition.get("strike", 0.0)

    if settle_lo is None or settle_hi is None:
        # Span wide enough that both wings of the payoff are visible, centred
        # on the threshold, and always including zero (negative settles are
        # real in CAISO — see the fuel-mix note in main.py).
        span = max(abs(threshold) * 0.6, 40.0)
        lo = min(0.0, threshold - span) if settle_lo is None else settle_lo
        hi = (threshold + span) if settle_hi is None else settle_hi
    else:
        lo, hi = settle_lo, settle_hi
    if hi <= lo:
        hi = lo + 1.0

    step = (hi - lo) / (DIAGRAM_POINTS - 1)
    settles = [lo + step * i for i in range(DIAGRAM_POINTS)]
    # Pin the kink exactly on a sample so the rendered elbow is the real elbow
    # rather than a chord across it.
    if lo < threshold < hi:
        settles.append(float(threshold))
        settles = sorted(set(round(s, 6) for s in settles))

    points = []
    for s in settles:
        per = payoff_per_mwh(definition, s, strike=strike)
        tot = payoff_total(definition, s, mwh if mwh is not None else 0.0, strike=strike)
        points.append({
            "settle": round(s, 6),
            "payoff_per_mwh": None if definition["y_basis"] == "total" else round(per, 6),
            "payoff_total": None if mwh is None and definition["y_basis"] != "total"
            else round(tot, 4),
        })

    return {
        "x_label": "settle — monthly block average ($/MWh)",
        "y_label": ("payoff ($ per month)" if definition["y_basis"] == "total"
                    else "payoff ($/MWh)"),
        "y_basis": definition["y_basis"],
        "unit": "$/MWh" if definition["y_basis"] == "per_mwh" else "$",
        "breakeven": None if be is None else round(be, 6),
        "threshold": None if threshold is None else round(float(threshold), 6),
        "strike_used": None if strike is None else round(strike, 6),
        "mwh": mwh,
        "points": points,
        "note": (
            "A pure function of the inputs — no market data, no valuation. "
            "This is what the structure pays AT a settle, not what the settle "
            "will be and not what the structure is worth."
        ),
    }


# ═══════════════════════════════════════════════════════════════════════════
# The historical replay
# ═══════════════════════════════════════════════════════════════════════════
#
# Input shape from the route layer — one row per (month, PT day):
#     {"month": date(2025, 11, 1), "day": date(2025, 11, 3),
#      "block_sum": Decimal|float, "n_hours": 16}
# plus, for an HRCO, a gas resolution per day:
#     {date(2025, 11, 3): {"price": 3.37, "trade_date": date(2025, 11, 3),
#                          "fill_span_days": 0}}
#
# The route reads; this function decides what is BANKED and does the
# arithmetic. Keeping the gate here (not in SQL) is deliberate: the expected
# hour count comes from the tz database per date, which SQL would have to
# hardcode.


def _month_key(d: _date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def replay(definition: dict, daily_rows: list[dict],
           gas_by_day: dict[_date, dict] | None = None,
           *, max_gas_fill_span_days: int = 4) -> dict:
    """Month-by-month replay of the structure over banked history.

    Returns the replay table, the cumulative series, the total, and — always —
    the depth statement and the list of months REJECTED by the completeness
    gate with the reason each was rejected. A caller cannot render this
    payload without being told what it excludes.

    `max_gas_fill_span_days` is the longest run of non-trade days a gas index
    may cover by carrying its last trade-day print forward. 4 covers a
    Thanksgiving or Christmas weekend. Carrying a daily gas index onto
    non-trade days is the real gas-day convention (the weekend package prices
    Sat/Sun/Mon), NOT interpolation — but a span longer than a holiday weekend
    means the index itself has a hole, and the month is gated out.
    """
    block = definition["block"]
    size_mw = definition["size_mw"]
    kind = definition["structure"]
    daily_exercise = is_daily_exercise(definition)
    wants_gas = needs_gas(definition)
    gas_by_day = gas_by_day or {}

    # ── Bucket the daily rows by month ────────────────────────────────────
    by_month: dict[tuple[int, int], list[dict]] = {}
    for r in daily_rows:
        d = r["day"]
        by_month.setdefault((d.year, d.month), []).append(r)

    rows: list[dict] = []
    rejected: list[dict] = []

    for (yr, mo) in sorted(by_month):
        days = sorted(by_month[(yr, mo)], key=lambda r: r["day"])
        dim, expected_hours = expected_month_hours(yr, mo, block)
        got_days = len({r["day"] for r in days})
        got_hours = sum(int(r["n_hours"]) for r in days)

        # ── RAIL 2: gate the month WHOLE. A partial month's average is a
        # different number than the month's average, and a monthly-average
        # structure settles on the month. No interpolation, no fills, never a
        # payoff over a partial month.
        if got_days != dim or got_hours != expected_hours:
            rejected.append({
                "month": f"{yr:04d}-{mo:02d}",
                "reason": "incomplete power month",
                "days_present": got_days,
                "days_in_month": dim,
                "block_hours_present": got_hours,
                "block_hours_expected": expected_hours,
            })
            continue

        # Hour-weighted month average. Weighting matters on DST months for
        # 7x24 and 7x8, where days carry 23/24/25 and 7/8/9 hours.
        block_sum = sum(float(r["block_sum"]) for r in days)
        block_avg = block_sum / got_hours
        mwh = size_mw * got_hours

        # ── Gas leg (HRCO only) ───────────────────────────────────────────
        gas_avg = None
        strike = None
        gas_days_held = None
        max_fill = None
        if wants_gas:
            prints = []
            missing = 0
            worst_fill = 0
            held = set()
            for r in days:
                g = gas_by_day.get(r["day"])
                if g is None or g.get("price") is None:
                    missing += 1
                    continue
                prints.append((r, float(g["price"])))
                worst_fill = max(worst_fill, int(g.get("fill_span_days") or 0))
                if int(g.get("fill_span_days") or 0) == 0:
                    held.add(g.get("trade_date"))
            if missing or worst_fill > max_gas_fill_span_days:
                rejected.append({
                    "month": f"{yr:04d}-{mo:02d}",
                    "reason": ("gas index does not cover the month"
                               if missing else
                               "gas index fill span exceeds a holiday weekend"),
                    "days_in_month": dim,
                    "gas_days_unresolved": missing,
                    "max_fill_span_days": worst_fill,
                    "max_fill_span_allowed": max_gas_fill_span_days,
                })
                continue
            # Day-weighted monthly gas average: every FLOW day counts once,
            # carrying its gas-day index. This is the monthly-index
            # convention, and it is the same input set the daily-exercise leg
            # walks — which is what makes the two variants comparable.
            gas_avg = sum(g for _, g in prints) / len(prints)
            gas_days_held = len(held)
            max_fill = worst_fill
            strike = definition["heat_rate"] * gas_avg + definition["vom_adder"]

        # ── Payoff ────────────────────────────────────────────────────────
        if daily_exercise:
            # Per-day exercise, summed. Each day's strike is that day's gas.
            total = 0.0
            for r, g in prints:
                day_hours = int(r["n_hours"])
                day_avg = float(r["block_sum"]) / day_hours
                day_strike = definition["heat_rate"] * g + definition["vom_adder"]
                total += max(0.0, day_avg - day_strike) * size_mw * day_hours
            per = (total / mwh) if mwh else 0.0
        else:
            total = payoff_total(definition, block_avg, mwh, strike=strike)
            per = (total / mwh) if mwh else 0.0
            if definition["y_basis"] == "total":
                per = None

        row = {
            "month": f"{yr:04d}-{mo:02d}",
            "days": dim,
            "block_hours": got_hours,
            "block_avg": round(block_avg, 6),
            "mwh": round(mwh, 6),
            "payoff_per_mwh": None if per is None else round(per, 6),
            "payoff": round(total, 4),
        }
        if wants_gas:
            row["gas_avg"] = round(gas_avg, 6)
            row["gas_days_held"] = gas_days_held
            row["max_gas_fill_span_days"] = max_fill
            row["strike"] = round(strike, 6)
        rows.append(row)

    # ── Window trim: keep the LAST n banked months ────────────────────────
    window_months = definition.get("window_months")
    trimmed = 0
    if window_months is not None and len(rows) > window_months:
        trimmed = len(rows) - window_months
        rows = rows[-window_months:]

    # ── Cumulative + total ────────────────────────────────────────────────
    cumulative = []
    running = 0.0
    for r in rows:
        running += r["payoff"]
        cumulative.append({"month": r["month"], "cumulative": round(running, 4)})

    n = len(rows)
    return {
        "available": n > 0,
        "reason": None if n > 0 else _absence_reason(rejected),
        "months": n,
        "window": {
            "requested_months": window_months,
            "banked_months_available": n + trimmed,
            "months_trimmed_by_window": trimmed,
            "first_month": rows[0]["month"] if rows else None,
            "last_month": rows[-1]["month"] if rows else None,
        },
        "rows": rows,
        "cumulative": cumulative,
        "total": round(running, 4),
        "total_mwh": round(sum(r["mwh"] for r in rows), 6),
        "months_rejected": rejected,
        "depth_statement": depth_statement(n, rows),
        "note": (
            "Realized history only. Every figure is what this structure WOULD "
            "HAVE PAID against banked settles over the stated window — not a "
            "forecast, not a valuation, and not a recommendation."
        ),
    }


def _absence_reason(rejected: list[dict]) -> str:
    if not rejected:
        return (
            "No banked history for this leg. Nothing was rejected — there are "
            "simply no rows in the window."
        )
    reasons = sorted({r["reason"] for r in rejected})
    return (
        f"No month passed the completeness gate. {len(rejected)} month(s) were "
        f"read and gated out ({'; '.join(reasons)}). A partial month is not "
        "replayed — see months_rejected for the per-month receipt."
    )


def depth_statement(n: int, rows: list[dict]) -> str:
    """RAIL 2 in one sentence. Every replay carries one; no exceptions."""
    if n == 0:
        return "0 banked complete months — nothing to replay."
    if n == 1:
        return f"1 banked complete month ({rows[0]['month']})."
    return (
        f"{n} banked complete months ({rows[0]['month']} through "
        f"{rows[-1]['month']}) — every month whole, none partial."
    )


# ═══════════════════════════════════════════════════════════════════════════
# The screener — a DESCRIPTIVE SORT (rail #3)
# ═══════════════════════════════════════════════════════════════════════════

def rank_legs(results: list[dict], *, direction: str = "desc") -> dict:
    """Rank one structure's realized payoff across legs.

    RAIL 3, in code and in the payload: this is `sorted()` over historical
    arithmetic. There is no score, no signal, no rating, and no ordering by
    anything other than a realized dollar total over a stated window. Legs
    that could not be replayed are returned in `unavailable` WITH their
    reason, never dropped — a leg missing from a ranking would read as a
    ranking, which is exactly the misreading rail 3 exists to prevent.
    """
    if direction not in ("desc", "asc"):
        raise StructureError("direction", "must be 'desc' or 'asc'.")

    ranked = [r for r in results if r.get("months", 0) > 0]
    unavailable = [
        {"leg": r["leg"], "reason": r.get("reason"), "months": r.get("months", 0)}
        for r in results if r.get("months", 0) == 0
    ]
    ranked.sort(key=lambda r: r["total"], reverse=(direction == "desc"))
    for i, r in enumerate(ranked, start=1):
        r["rank"] = i

    # Depth is NOT uniform across legs (hub strips run years, nodal legs run
    # weeks), so a ranking that mixed windows would be comparing a decade
    # against a fortnight. State the spread rather than hide it.
    months = {r["months"] for r in ranked}
    return {
        "ranked": ranked,
        "unavailable": unavailable,
        "direction": direction,
        "comparable_window": len(months) <= 1,
        "months_spread": (None if not months else
                          {"min": min(months), "max": max(months)}),
        "note": (
            "A descriptive sort of historical arithmetic — ranked by realized "
            "payoff over the stated banked months. Not a recommendation "
            "register, not a screen for what to trade."
            + ("" if len(months) <= 1 else
               " Legs in this sort do NOT share a window: compare totals only "
               "within an equal months count, or use the per-MWh column.")
        ),
    }
