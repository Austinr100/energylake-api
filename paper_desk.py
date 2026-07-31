"""
paper_desk.py — the Paper Desk blotter/book arithmetic.

THE POSTURE (load-bearing — this module is a LEDGER READER, not a trader and
not a marker). Five rails, and every one of them is pinned by a test in
tests/test_paper_desk.py:

  1. A BID IS THE ONLY POSITION. `paper_journal` holds two species keyed by
     `kind`: 'note' (the morning pre-bid read — prose, no position) and 'bid'
     (a paper position). Notes are NOT bids. Nothing filed as a note ever
     reaches the blotter, the book, the curve or either aggregate. The filter
     lives in the SQL and is re-asserted here so a widened query cannot
     silently leak prose into a position count.

  2. STATUS IS DERIVED SERVER-SIDE, FROM TWO COLUMNS, ONCE.
         settled = true                     -> SETTLED
         settled = false, no reason stamped -> OPEN
         settled = false, reason stamped    -> PENDING
     The reason is carried VERBATIM. This module never maps it to a code,
     never title-cases it, never truncates it — a reason the desk cannot read
     word-for-word is a reason the desk cannot act on. `settled` is terminal:
     a row that carries both `settled = true` and a stale `unsettled_reason`
     reads SETTLED, and the reason still ships so the contradiction is visible
     rather than swallowed.

  3. A POSITION MARKS ONLY AT SETTLEMENT. There is no live intraday mark in
     v0. `settled` is the sole gate on every P&L figure in this file: an OPEN
     or PENDING row contributes nothing to the book's cumulative dollars, the
     equity curve, or either aggregate — not even when the writer has stamped
     a P&L column on it. Banked-hours running mark is FILED as v1 debt in the
     handoff, not built here.

  4. P&L IS READ, NEVER RECOMPUTED. `pnl_dollars` and `pnl_per_mwh` arrive
     ALREADY SIGNED from the settlement writer, which owns the direction
     convention (INC profits when DA > FMM, DEC when FMM > DA). This module
     performs no arithmetic on `settle_da` or `settle_fmm` whatsoever; both
     columns are carried to the wire for display and are never operands.
     Re-deriving a sign here would put a second, competing settlement
     convention in the building.

  5. HONEST EMPTY AND THIN STATES. Every list is `[]` when empty, never null
     (the isOutlooks doctrine: a consumer's array guard fails on a null and
     blanks the whole surface). Every rate and average is null when it has no
     samples, never 0.0 — a 0.0 win rate reads "everything lost", which is a
     claim, while null reads "nothing has settled", which is the truth. Every
     count that a rate was computed over ships beside it.

ROUNDING, AND WHY THE TOTALS TIE. Row money is rounded to the cent and row
$/MWh to 4dp AS THE ROW IS NORMALIZED, and every aggregate is then summed from
those rounded row values. So the book's cumulative dollars is exactly the sum
of the dollars the blotter displays, and the curve's last point is exactly the
book's total. Summing raw and rounding late would be marginally more precise
and would let a desk add up the visible column and get a different answer.

Everything in this module is pure: no I/O, no clock, no database, no network.
The route layer in main.py reads the banked rows and hands them here.

VOCABULARY
    bid           one paper position: a node, a side, a size, a price limit
    status        OPEN | PENDING | SETTLED, derived (never stored)
    gross MW      sum of |size_mw| — magnitude, direction ignored
    zero-fill     a settled bid whose bid never cleared (filled_hours = 0);
                  no MWh transacted, so no P&L to book -> FLAT, never a loss
    play          the screen that fired the bid (persistence / phantom /
                  surprise). See PLAY_KEY_PATHS — v0 serves null, on purpose.
"""

from __future__ import annotations

from datetime import date as _date
from datetime import datetime as _datetime

# ═══════════════════════════════════════════════════════════════════════════
# Vocabulary constants — the wire's exact strings, in one place
# ═══════════════════════════════════════════════════════════════════════════

BID_KIND = "bid"

STATUS_OPEN = "OPEN"
STATUS_PENDING = "PENDING"
STATUS_SETTLED = "SETTLED"

STATUS_RULE = (
    "settled -> SETTLED; unsettled with no unsettled_reason -> OPEN; "
    "unsettled with an unsettled_reason -> PENDING (reason served verbatim)"
)

MARK_RULE = (
    "v0 marks a position ONLY at settlement — there is no live intraday mark. "
    "An OPEN or PENDING row contributes nothing to any P&L figure."
)

SIGN_RULE = (
    "pnl_dollars and pnl_per_mwh arrive already signed from the settlement "
    "writer and are read verbatim. settle_da / settle_fmm are carried for "
    "display only and are never operands — P&L is never recomputed from prices."
)

FLAT_RULE = (
    "win = pnl_dollars > 0, loss = pnl_dollars < 0, flat = pnl_dollars == 0. "
    "A zero-fill settlement (filled_hours = 0) transacts no MWh, so it books "
    "no P&L and counts FLAT, never a loss."
)

CURVE_RULE = (
    "settled rows only, one point per trade_date that actually carries a "
    "settlement. Consecutive points are NOT necessarily adjacent dates — gaps "
    "are real and are left as gaps. No interpolation, no zero-fill of a date "
    "with nothing settled, no carry-forward."
)

AVG_RULE = (
    "unweighted arithmetic mean of pnl_per_mwh over settled rows that carry "
    "one; not MW-weighted and not MWh-weighted. n_used ships beside it."
)

MONEY_DP = 2   # dollars are rounded to the cent, at the row
RATE_DP = 4    # $/MWh and rates carry 4dp, the department's precision
MW_DP = 4

# ═══════════════════════════════════════════════════════════════════════════
# The play key — READ WHAT IS THERE, and say so when it is not there
# ═══════════════════════════════════════════════════════════════════════════
#
# The by-play aggregate wants the SCREEN that fired each bid (persistence /
# phantom / surprise). Measured against the banked rows on 2026-07-31, the
# writer does not carry one in a machine-readable slot. What `inputs_as_of`
# actually holds on a bid row:
#
#   map.map_id            "desk_reader_prebid" — identical on every row in the
#                         table, notes included. That is the AGENT PROGRAM, not
#                         the screen; it cannot discriminate one play from
#                         another and grouping on it would produce one bucket
#                         mislabelled as a play.
#   bid.{size_mw, pnode_id, direction, conviction, hour_scope, price_limit,
#        constraint_id, rules, fingerprint, persistence, settle_reference,
#        conviction_reasons}
#   bid_screen.{facts[], positions[], bids_filed}
#
# The screen name appears in exactly one place: PROSE, in the `rationale`
# column, under the heading "WHICH SCREEN FIRED". Regexing an English sentence
# to key an aggregate makes the aggregate a property of the writer's prose
# style, so this module does not do it.
#
# THE NEAR MISS, NAMED SO NOBODY RE-DISCOVERS IT. `bid.persistence` exists as a
# metrics block ({dam_hours_7d, days_present}), and both banked bids fired off
# the persistence screen. Inferring play = "persistence" from the PRESENCE of
# that key is schema-shape divination, not a contract: there is no documented
# phantom or surprise counterpart to discriminate against, and both banked rows
# come from a single run on a single trade date, so the guess cannot even be
# checked against a counterexample. It is served as null instead.
#
# WHAT THE WRITER SHOULD ADD (one field, no migration of existing rows):
#   inputs_as_of.bid.screen : "persistence" | "phantom" | "surprise"
# The rationale reads "No phantom/surprise corroboration this run", so a bid
# CAN in principle be corroborated by more than one screen — if that is the
# intent, `bid.screens: [...]` is the right shape and v1 groups on it. v0 reads
# a scalar only; an array is reported as present-but-not-groupable rather than
# silently flattened.
#
# The lookup below is live. The day the writer stamps the field, this endpoint
# lights up with no code change here.

PLAY_KEY_PATHS = (
    ("bid", "screen"),
    ("bid", "play"),
    ("screen",),
    ("play",),
)

PLAY_KEY_ABSENT_REASON = (
    "no machine-readable play key on the bid rows: inputs_as_of carries "
    "map.map_id (the agent program, identical on every row — not a screen), "
    "bid.* position fields and bid_screen.facts[] (free prose). The screen "
    "name appears only in the rationale prose under 'WHICH SCREEN FIRED'. "
    "Parsing prose to key an aggregate would make the aggregate a property of "
    "the writer's sentence style, so play is served as null. Writer fix: stamp "
    "inputs_as_of.bid.screen ('persistence' | 'phantom' | 'surprise')."
)

PLAY_KEY_NON_SCALAR_REASON = (
    "a play key was found but is not a scalar string (a multi-screen array is "
    "a v1 shape); v0 groups on scalars only and serves play as null rather "
    "than flattening it"
)


# ═══════════════════════════════════════════════════════════════════════════
# Small coercions
# ═══════════════════════════════════════════════════════════════════════════

def _f(x):
    """Decimal/numeric -> float; None passes through (absence is not zero)."""
    return float(x) if x is not None else None


def _r(x, nd: int):
    """Round for the wire, preserving None."""
    return None if x is None else round(float(x), nd)


def _i(x):
    """Integer column -> int; None passes through."""
    return int(x) if x is not None else None


def _iso_d(d) -> "str | None":
    """A date column -> ISO date string. psycopg may hand back date or datetime."""
    if d is None:
        return None
    return (d.date() if isinstance(d, _datetime) else d).isoformat()


def _iso_ts(ts) -> "str | None":
    """A timestamptz column -> ISO instant string."""
    if ts is None:
        return None
    return ts.isoformat() if isinstance(ts, (_datetime, _date)) else str(ts)


def _text(x) -> "str | None":
    """A text column, VERBATIM. Blank/whitespace-only reads as absent."""
    if x is None:
        return None
    if isinstance(x, str) and x.strip() == "":
        return None
    return x


def _mean(vals):
    return (sum(vals) / len(vals)) if vals else None


# ═══════════════════════════════════════════════════════════════════════════
# Status — RAIL 2
# ═══════════════════════════════════════════════════════════════════════════

def status_of(row: dict) -> str:
    """OPEN | PENDING | SETTLED, from `settled` and `unsettled_reason` only.

    `settled` is terminal and checked first, so a row carrying both a true
    `settled` and a leftover reason reads SETTLED (and still ships the reason).
    """
    if bool(row.get("settled")):
        return STATUS_SETTLED
    return STATUS_PENDING if _text(row.get("unsettled_reason")) else STATUS_OPEN


# ═══════════════════════════════════════════════════════════════════════════
# Normalization — one banked row -> one blotter row
# ═══════════════════════════════════════════════════════════════════════════

def normalize_bid(row: dict) -> dict:
    """One `paper_journal` row -> one wire-shaped blotter row.

    Money is rounded to the cent HERE, once, so that every aggregate built
    downstream sums the same figures the blotter displays (see the module
    docstring). `settle_da` / `settle_fmm` are carried, never operated on.
    """
    return {
        "entry_id": _i(row.get("entry_id")),
        "entry_ts": _iso_ts(row.get("entry_ts")),
        "trade_date": _iso_d(row.get("trade_date")),
        "pnode_id": _text(row.get("pnode_id")),
        "constraint_id": _text(row.get("constraint_id")),
        "direction": _text(row.get("direction")),
        "size_mw": _r(_f(row.get("size_mw")), MW_DP),
        "price_limit": _r(_f(row.get("price_limit")), RATE_DP),
        "hour_scope": _text(row.get("hour_scope")),
        "conviction": _i(row.get("conviction")),
        "status": status_of(row),
        "settled": bool(row.get("settled")),
        "settle_da": _r(_f(row.get("settle_da")), RATE_DP),
        "settle_fmm": _r(_f(row.get("settle_fmm")), RATE_DP),
        "pnl_per_mwh": _r(_f(row.get("pnl_per_mwh")), RATE_DP),
        "pnl_dollars": _r(_f(row.get("pnl_dollars")), MONEY_DP),
        "filled_hours": _i(row.get("filled_hours")),
        "settled_at": _iso_ts(row.get("settled_at")),
        # VERBATIM, always present as a key so a client reads absence as null
        # rather than as a missing field.
        "unsettled_reason": _text(row.get("unsettled_reason")),
        # Additive beyond the named field list, and load-bearing: every banked
        # bid to date carries frontier_ok = false, meaning the position was
        # taken on inputs the writer's own freshness check flagged. A blotter
        # that hides that is a blotter that lies by omission.
        "frontier_ok": bool(row.get("frontier_ok")),
    }


def bid_rows(rows) -> list:
    """Bids only, normalized, newest first.

    RAIL 1 re-asserted in Python: the SQL filters `kind = 'bid'`, and so does
    this. A widened query cannot leak a note into a position count.
    """
    bids = [r for r in (rows or []) if (r.get("kind") or BID_KIND) == BID_KIND]
    out = [normalize_bid(r) for r in bids]
    out.sort(key=lambda b: (b["trade_date"] or "", b["entry_id"] or 0), reverse=True)
    return out


# ═══════════════════════════════════════════════════════════════════════════
# Settlement classification — RAIL 3 + RAIL 4 + the zero-fill rule
# ═══════════════════════════════════════════════════════════════════════════

def classify(b: dict) -> "str | None":
    """A settled row -> 'win' | 'loss' | 'flat' | None (unclassified).

    None is returned for a settled row that carries no P&L and is not a
    zero-fill: it is a real hole in the ledger, counted and surfaced rather
    than folded into flat. Unsettled rows are not classifiable at all in v0.
    """
    if b["status"] != STATUS_SETTLED:
        return None
    pnl = b["pnl_dollars"]
    if pnl is None:
        # A zero-fill transacted no MWh, so there is no P&L to book. FLAT.
        return "flat" if b["filled_hours"] == 0 else None
    if pnl > 0:
        return "win"
    if pnl < 0:
        return "loss"
    return "flat"


def booked_dollars(b: dict) -> "float | None":
    """The dollars a row contributes to a total, or None if it contributes
    nothing. RAIL 3: only a settled row books. A settled zero-fill books 0.00.
    """
    if b["status"] != STATUS_SETTLED:
        return None
    if b["pnl_dollars"] is not None:
        return b["pnl_dollars"]
    return 0.0 if b["filled_hours"] == 0 else None


def _tally(bids: list) -> dict:
    """The shared settled-row tally behind the book and both aggregates."""
    settled = [b for b in bids if b["status"] == STATUS_SETTLED]
    verdicts = [classify(b) for b in settled]
    booked = [booked_dollars(b) for b in settled]
    rates = [b["pnl_per_mwh"] for b in settled if b["pnl_per_mwh"] is not None]
    classified = [v for v in verdicts if v is not None]
    return {
        "settled_n": len(settled),
        "win": verdicts.count("win"),
        "loss": verdicts.count("loss"),
        "flat": verdicts.count("flat"),
        # A settled row with no P&L and a non-zero fill. Never silently
        # dropped: win + loss + flat + unclassified == settled_n, always.
        "unclassified": len(verdicts) - len(classified),
        "classified_n": len(classified),
        "pnl_dollars": _r(sum(d for d in booked if d is not None), MONEY_DP),
        "avg_pnl_per_mwh": _r(_mean(rates), RATE_DP),
        "avg_pnl_per_mwh_n": len(rates),
    }


def _win_rate(win: int, classified_n: int) -> "float | None":
    """Wins over CLASSIFIED settled rows, or null when nothing is classified.

    Never 0.0 on an empty denominator — 0.0 reads "everything lost".
    """
    return None if classified_n == 0 else round(win / classified_n, RATE_DP)


# ═══════════════════════════════════════════════════════════════════════════
# Stanza 1 — the blotter
# ═══════════════════════════════════════════════════════════════════════════

def blotter(bids: list) -> dict:
    """Every bid, newest first, each carrying its derived status."""
    by_status = {STATUS_OPEN: 0, STATUS_PENDING: 0, STATUS_SETTLED: 0}
    for b in bids:
        by_status[b["status"]] += 1
    return {
        "rows": bids,
        "n": len(bids),
        "by_status": by_status,
        "order": "trade_date DESC, entry_id DESC (newest first)",
        "status_rule": STATUS_RULE,
        "mark_rule": MARK_RULE,
        "sign_rule": SIGN_RULE,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Stanza 2 — the book
# ═══════════════════════════════════════════════════════════════════════════

def book(bids: list) -> dict:
    """Desk totals. Open exposure is a COUNT and a GROSS MW, never a mark."""
    t = _tally(bids)

    def _gross(status: str) -> dict:
        rows = [b for b in bids if b["status"] == status]
        mw = sum(abs(b["size_mw"]) for b in rows if b["size_mw"] is not None)
        return {
            "n": len(rows),
            "gross_mw": _r(mw, MW_DP),
            "sized_n": sum(1 for b in rows if b["size_mw"] is not None),
        }

    return {
        "open": _gross(STATUS_OPEN),
        "pending": _gross(STATUS_PENDING),
        "settled": {
            "n": t["settled_n"],
            "win": t["win"],
            "loss": t["loss"],
            "flat": t["flat"],
            "unclassified": t["unclassified"],
            "win_rate": _win_rate(t["win"], t["classified_n"]),
        },
        "pnl_dollars": t["pnl_dollars"],
        "avg_pnl_per_mwh": t["avg_pnl_per_mwh"],
        "avg_pnl_per_mwh_n": t["avg_pnl_per_mwh_n"],
        "bids_n": len(bids),
        "gross_mw_rule": "sum of |size_mw| — magnitude only, direction ignored",
        "flat_rule": FLAT_RULE,
        "avg_rule": AVG_RULE,
        "mark_rule": MARK_RULE,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Stanza 3 — the equity curve
# ═══════════════════════════════════════════════════════════════════════════

def equity_curve(bids: list) -> dict:
    """Cumulative settled P&L by trade_date. Gaps are gaps."""
    per_date: dict = {}
    for b in bids:
        if b["status"] != STATUS_SETTLED:
            continue
        d = b["trade_date"]
        if d is None:
            continue
        slot = per_date.setdefault(d, {"pnl": 0.0, "n": 0, "booked_n": 0})
        slot["n"] += 1
        booked = booked_dollars(b)
        if booked is not None:
            slot["pnl"] += booked
            slot["booked_n"] += 1

    points, running = [], 0.0
    for d in sorted(per_date):
        slot = per_date[d]
        running = round(running + slot["pnl"], MONEY_DP)
        points.append({
            "trade_date": d,
            "pnl_dollars": _r(slot["pnl"], MONEY_DP),
            "cumulative_pnl_dollars": running,
            "settled_n": slot["n"],
            "booked_n": slot["booked_n"],
        })

    return {
        "points": points,
        "n_points": len(points),
        "span": ({"first": points[0]["trade_date"], "last": points[-1]["trade_date"]}
                 if points else None),
        "curve_rule": CURVE_RULE,
        "mark_rule": MARK_RULE,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Stanza 4a — by node
# ═══════════════════════════════════════════════════════════════════════════

def _group_stats(key_name: str, key_value, rows: list) -> dict:
    t = _tally(rows)
    open_mw = sum(abs(b["size_mw"]) for b in rows
                  if b["status"] == STATUS_OPEN and b["size_mw"] is not None)
    return {
        key_name: key_value,
        "n": len(rows),
        "open_n": sum(1 for b in rows if b["status"] == STATUS_OPEN),
        "pending_n": sum(1 for b in rows if b["status"] == STATUS_PENDING),
        "open_gross_mw": _r(open_mw, MW_DP),
        "settled_n": t["settled_n"],
        "win": t["win"],
        "loss": t["loss"],
        "flat": t["flat"],
        "unclassified": t["unclassified"],
        "win_rate": _win_rate(t["win"], t["classified_n"]),
        "pnl_dollars": t["pnl_dollars"],
        "avg_pnl_per_mwh": t["avg_pnl_per_mwh"],
        "avg_pnl_per_mwh_n": t["avg_pnl_per_mwh_n"],
    }


def _rank(groups: list, key_name: str) -> list:
    """Most P&L first; nothing-settled groups sort last but STAY VISIBLE — a
    node dropped from a ranking would itself read as a ranking."""
    return sorted(
        groups,
        key=lambda g: (g["settled_n"] == 0, -(g["pnl_dollars"] or 0.0),
                       str(g[key_name] or "")),
    )


def by_node(bids: list) -> dict:
    """Per-pnode aggregates over every bid, settled or not."""
    buckets: dict = {}
    for b in bids:
        buckets.setdefault(b["pnode_id"], []).append(b)
    groups = [_group_stats("pnode_id", k, v) for k, v in buckets.items()]
    return {
        "nodes": _rank(groups, "pnode_id"),
        "n_nodes": len(groups),
        "order": "pnl_dollars DESC; groups with nothing settled sort last, never dropped",
        "flat_rule": FLAT_RULE,
        "mark_rule": MARK_RULE,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Stanza 4b — by play
# ═══════════════════════════════════════════════════════════════════════════

def _dig(obj, path: tuple):
    """Walk a dotted path through nested dicts; None if any hop is missing."""
    cur = obj
    for hop in path:
        if not isinstance(cur, dict) or hop not in cur:
            return None
        cur = cur[hop]
    return cur


def play_of(inputs_as_of) -> tuple:
    """(play, path, note) for one row's `inputs_as_of`.

    Returns a scalar play string and the dotted path it was read from, or
    (None, None, reason). Live lookup: the day the writer stamps a key at one
    of PLAY_KEY_PATHS, the by-play stanza groups on it with no change here.
    """
    if not isinstance(inputs_as_of, dict):
        return None, None, PLAY_KEY_ABSENT_REASON
    for path in PLAY_KEY_PATHS:
        found = _dig(inputs_as_of, path)
        if found is None:
            continue
        dotted = ".".join(path)
        if isinstance(found, str) and found.strip():
            return found, dotted, None
        return None, dotted, PLAY_KEY_NON_SCALAR_REASON
    return None, None, PLAY_KEY_ABSENT_REASON


def by_play(bids: list, plays: list) -> dict:
    """Per-play aggregates, keyed off the screen that fired each bid.

    `plays` is the per-bid (play, path, note) triple in the same order as
    `bids` — the route layer extracts it, because `inputs_as_of` is a heavy
    jsonb column that never belongs on the wire.

    When no bid carries a machine-readable key, every bid falls into ONE group
    with `play: null`, so the counts still tie to the blotter, and the stanza
    reports WHY as a finding rather than regexing the rationale prose. Zero
    bids -> `plays: []`, per the isOutlooks doctrine.
    """
    buckets: dict = {}
    for b, (play, _path, _note) in zip(bids, plays):
        buckets.setdefault(play, []).append(b)

    groups = [_group_stats("play", k, v) for k, v in buckets.items()]
    keyed_n = sum(len(v) for k, v in buckets.items() if k is not None)
    paths_seen = sorted({p for _pl, p, _n in plays if p})
    notes = sorted({n for _pl, _p, n in plays if n})

    return {
        "plays": _rank(groups, "play"),
        "n_plays": len(groups),
        "key": {
            "available": bool(bids) and keyed_n == len(bids),
            "keyed_n": keyed_n,
            "unkeyed_n": len(bids) - keyed_n,
            "paths_searched": [".".join(p) for p in PLAY_KEY_PATHS],
            "paths_found": paths_seen,
            "reasons": notes,
        },
        "order": "pnl_dollars DESC; groups with nothing settled sort last, never dropped",
        "flat_rule": FLAT_RULE,
        "mark_rule": MARK_RULE,
    }
