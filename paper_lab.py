"""
paper_lab.py — the Lab Paper Desk: one payload, one read, voids on the page.

This is the pure arithmetic behind `GET /api/lab/paper-desk`. It is a SECOND,
SEPARATELY-CONTRACTED view of the same table `paper_desk.py` serves under
`/api/desk/*`, and the two are deliberately not merged. `/api/desk/*` is five
stanzas with an OPEN/PENDING/SETTLED vocabulary; this lane is one stanza with a
settled/pending/VOID vocabulary. Collapsing them would force one of the two
contracts to change, and both have consumers.

WHAT IS DIFFERENT HERE, AND WHY EACH DIFFERENCE EXISTS:

  1. VOIDS ARE THE PRODUCT, NOT NOISE. `/api/desk/*` has no void concept: a
     voided position reads PENDING there, because a voided row is literally an
     unsettled row carrying a reason. That is wrong on a desk page — 8 of the
     14 banked bids are voided double-books, and a page that files them under
     "pending" says the desk is waiting on settlements that are never coming.
     So this lane derives a third status, VOIDS RIDE THE BLOTTER, the
     aggregates exclude them from every number, and `book` counts them.

  2. STATUS IS THREE-VALUED AND LOWERCASE.
         settled = true                        -> "settled"
         unsettled, reason starts "voided"     -> "void"
         anything else                         -> "pending"
     `settled` is checked FIRST and is terminal. A row carrying both
     `settled = true` and a `voided_*` reason reads "settled" — and the reason
     still ships on the row, so the contradiction is visible on the page
     instead of being resolved silently in here.

     There is NO "open" status. `/api/desk/*` splits unsettled-with-no-reason
     (OPEN) from unsettled-with-a-reason (PENDING); this contract does not, and
     both land on "pending". That is a contract choice, not an oversight, and
     it is pinned.

  3. THE EQUITY CURVE IS BY `settled_at`, NOT `trade_date`. `/api/desk/equity`
     draws by trade_date — when the position was taken. This draws by when the
     money was actually booked, which is the curve a desk reads. The two curves
     will NOT agree, and must not be expected to: the one settled bid was taken
     2026-07-31 and settled 2026-08-06. The date is the UTC calendar date of
     `settled_at`; a settled row with no `settled_at` cannot be placed on a
     curve at all and is EXCLUDED AND COUNTED (see `derivation.equity`), never
     dropped in silence and never parked on a made-up date.

  4. P&L IS READ, NEVER RECOMPUTED — unchanged from `/api/desk/*`, and worth
     restating because this lane puts `settle_da` and `settle_fmm` on the wire
     right next to `pnl_dollars`. They are DISPLAY COLUMNS. This module
     performs no arithmetic on them whatsoever. The settlement writer owns the
     direction convention (INC profits when DA > FMM, DEC when FMM > DA);
     re-deriving a sign here would put a second, competing convention in the
     building.

  5. A POSITION MARKS ONLY AT SETTLEMENT. A pending or void row contributes
     nothing to any dollar figure, even if the writer has stamped a P&L column
     on it. There is no running mark in this lane either.

  6. HONEST EMPTY AND THIN STATES. Every list is `[]` when empty, never null.
     Every average is null when it has no samples, never 0.0 — a 0.0 average
     reads "it averaged to nothing", null reads "nothing has settled".
     `total_pnl` is the one deliberate exception: it is a SUM, and the sum of
     no settled rows is 0.0, which is the arithmetic truth and stays summable
     across groups. `n_settled` sits beside it to disambiguate.

THE PLAY KEY IS LIVE — AND THE 2026-07-31 FINDING HAS BEEN OVERTAKEN.
`paper_desk.PLAY_KEY_PATHS` was shipped on 2026-07-31 as a live-but-dark
lookup, with the handoff filing "the writer carries no machine-readable play
key" as the lane's finding and serving `play: null`. THE WRITER TOOK THE FIX.
`inputs_as_of.bid.screen` is now stamped, and this lane groups on it. It is not
stamped on the two rows that predate the fix, so those honestly read
"unclassified". The single source of the lookup is still
`paper_desk.play_of` — this module does not re-implement it, and it does not
parse the rationale prose. See the PLAY block below for the measurement.

ROUNDING, AND WHY THE TOTALS TIE. Row money is rounded to the cent and row
$/MWh to 4dp AS THE ROW IS NORMALIZED, and every aggregate is summed from those
rounded row values. So `book.settled_pnl` is exactly the sum of the
`pnl_dollars` the blotter displays, and the equity curve's last
`cumulative_pnl` is exactly `book.settled_pnl`. Summing raw and rounding late
is marginally more precise and lets a desk add up the visible column and get a
different answer.

Everything here is pure: no I/O, no clock, no database, no network. The route
layer in main.py does the one read and hands the rows over.

VOCABULARY
    bid           one paper position: a node, a side, a size, a price limit
    status        settled | pending | void, derived (never stored)
    void          a position the writer or the settler withdrew; it never
                  settles and never will. Counted, shown, never aggregated.
    play          the screen that fired the bid (persistence / surprise / ...),
                  read from inputs_as_of.bid.screen — see PLAY below
    curve_points  how many rows the bid has in `paper_bid_curves` — the shape
                  of the bid that was filed, as a count, not the curve itself
"""

from __future__ import annotations

from datetime import datetime as _datetime
from datetime import timezone as _timezone

import paper_desk as _pd

# ═══════════════════════════════════════════════════════════════════════════
# Vocabulary constants — the wire's exact strings, in one place
# ═══════════════════════════════════════════════════════════════════════════

BID_KIND = _pd.BID_KIND

STATUS_SETTLED = "settled"
STATUS_PENDING = "pending"
STATUS_VOID = "void"

# The void predicate, stated once. Measured against the banked rows on
# 2026-08-06, `unsettled_reason` carries exactly three values:
#
#     voided_writer_alias_double_booked          6 rows
#     voided_settler_v1_double_booked_position    2 rows
#     awaiting_settlement                         5 rows
#
# so the prefix separates them cleanly. It is a PREFIX and not an equality
# check on purpose: the two void reasons already differ, and the settler will
# coin more of them. It is matched case-insensitively against the stripped
# string, and NOTHING ELSE about the reason is interpreted — the reason itself
# is served verbatim for the page to render.
VOID_REASON_PREFIX = "voided"

STATUS_RULE = (
    "settled = true -> 'settled' (terminal, checked first); otherwise an "
    "unsettled_reason starting 'voided' -> 'void'; anything else -> "
    "'pending'. There is no 'open' status in this contract — an unsettled row "
    "with no reason stamped is pending."
)

VOID_RULE = (
    "voids ride the blotter and are counted in `book.n_void`; they are "
    "excluded from every aggregate figure. A node or play whose every bid was "
    "voided still appears, with zeroes and an n_void, because dropping it "
    "would hide the void from the page entirely."
)

CURVE_RULE = (
    "one point per UTC calendar date of settled_at that actually carries a "
    "settlement; cumulative_pnl is the running total in that order. Dates with "
    "nothing settled produce NO point — consecutive points are not necessarily "
    "adjacent dates. No interpolation, no zero-fill, no carry-forward. Drawn "
    "by settled_at (when the money was booked), NOT by trade_date."
)

SIGN_RULE = (
    "pnl_dollars and pnl_per_mwh arrive already signed from the settlement "
    "writer and are read verbatim. settle_da / settle_fmm are carried for "
    "display only and are never operands — P&L is never recomputed from prices."
)

MARK_RULE = (
    "a position marks ONLY at settlement. A pending or void row contributes "
    "nothing to any dollar figure, even if a P&L column is stamped on it."
)

WIN_RULE = "win = pnl_dollars > 0 on a settled row. Voids cannot win or lose."

AVG_RULE = (
    "unweighted arithmetic mean of pnl_per_mwh over settled rows that carry "
    "one; not MW-weighted and not MWh-weighted. Null, never 0.0, with no "
    "samples."
)

MONEY_DP = _pd.MONEY_DP   # dollars, to the cent, at the row
RATE_DP = _pd.RATE_DP     # $/MWh, 4dp, the department's precision
MW_DP = _pd.MW_DP

# ═══════════════════════════════════════════════════════════════════════════
# PLAY — the 2026-07-31 finding, re-measured 2026-08-06, and OVERTAKEN
# ═══════════════════════════════════════════════════════════════════════════
#
# The Trading Room capture predicted this stanza would have to serve an
# unclassified play, because on 2026-07-31 the screen that fired a bid existed
# only in the `rationale` PROSE, under the heading "WHICH SCREEN FIRED". The
# `/api/desk/by-play` lane refused to regex an English sentence to key an
# aggregate, served `play: null`, and filed a one-field writer fix:
#
#     inputs_as_of.bid.screen : "persistence" | "phantom" | "surprise"
#
# THE WRITER SHIPPED IT. Re-measured against all 14 banked bids, 2026-08-06:
#
#   entries 21..37 (12 rows)   inputs_as_of.bid.screen present, jsonb type
#                              `string` -> "surprise" (6) / "persistence" (6)
#   entries 18, 19 (2 rows)    absent. Both were filed 2026-07-30T12:47:48Z,
#                              BEFORE the writer stamped the field, and both
#                              are voided_settler_v1_double_booked_position.
#
# So this lane groups on the real key for 12 of 14 rows and serves
# PLAY_UNCLASSIFIED for the 2 pre-fix rows. That is not a degraded mode and it
# is not a backfill request: those two rows were filed under a writer that did
# not carry the field, and inventing a play for them now would be a guess
# dressed as a record.
#
# CORROBORATION, NOT DEPENDENCE. On all 12 stamped rows the prose agrees with
# the key — the rationale's "screen <name> — the screen that set the side" line
# matches `bid.screen` exactly, 12 for 12. That is a receipt that the writer's
# two surfaces are consistent. It is NOT a fallback: this module does not read
# the rationale to derive a play, then or now, because an aggregate keyed off
# prose is an aggregate that changes when the writer edits a sentence.
#
# THE LOOKUP IS NOT RE-IMPLEMENTED HERE. `paper_desk.play_of` is the one
# implementation, already pinned by the /api/desk suite, and this lane calls
# it. Only the ABSENT case differs: /api/desk serves null (its contract),
# this lane serves the string below (its contract).
PLAY_UNCLASSIFIED = "unclassified"

PLAY_RULE = (
    "read from inputs_as_of at " + ", ".join(
        ".".join(p) for p in _pd.PLAY_KEY_PATHS) + " (first scalar string "
    "wins). A row carrying none of them is '" + PLAY_UNCLASSIFIED + "' — the "
    "rationale prose is NEVER parsed to key an aggregate."
)


# ═══════════════════════════════════════════════════════════════════════════
# Status — three-valued, derived, never stored
# ═══════════════════════════════════════════════════════════════════════════

def _iso_utc(ts) -> "str | None":
    """A timestamptz column -> ISO instant, NORMALIZED TO UTC.

    Load-bearing, and not the same as `paper_desk._iso_ts`. psycopg renders a
    timestamptz in the connection's session timezone, so the same instant can
    come back as `...T21:14:11+00:00` or `...T14:14:11-07:00` depending on how
    the pool was configured. The equity curve buckets on the CALENDAR DATE of
    this string, so leaving the offset to chance would let a settlement drift a
    day between deployments. Converting here pins it: every `settled_at` on the
    wire is UTC, and `derivation.equity.basis` says so.

    A naive datetime is assumed to be UTC — psycopg hands back aware values for
    timestamptz, so this only fires on a hand-built row.
    """
    if ts is None:
        return None
    if not isinstance(ts, _datetime):
        return _pd._iso_ts(ts)
    aware = ts if ts.tzinfo is not None else ts.replace(tzinfo=_timezone.utc)
    return aware.astimezone(_timezone.utc).isoformat()


def is_void_reason(reason) -> bool:
    """True when `unsettled_reason` marks a withdrawn position.

    Case-insensitive prefix match on the stripped string. Absence is not a
    void; a blank or whitespace-only reason is absence.
    """
    text = _pd._text(reason)
    if not isinstance(text, str):
        return False
    return text.strip().lower().startswith(VOID_REASON_PREFIX)


def status_of(row: dict) -> str:
    """'settled' | 'pending' | 'void', from `settled` and `unsettled_reason`.

    `settled` is terminal and checked FIRST, so a row carrying both a true
    `settled` and a voided_* reason reads 'settled' — and the reason still
    ships on the row, so the contradiction shows on the page rather than being
    resolved out of sight here.
    """
    if bool(row.get("settled")):
        return STATUS_SETTLED
    if is_void_reason(row.get("unsettled_reason")):
        return STATUS_VOID
    return STATUS_PENDING


def play_of(inputs_as_of) -> str:
    """The screen that fired the bid, or PLAY_UNCLASSIFIED.

    Delegates to `paper_desk.play_of` — one implementation of the lookup in the
    building — and maps its "no scalar key found" answer onto this contract's
    string. A non-scalar key (the v1 multi-screen array shape the /api/desk
    handoff anticipated) is also unclassified: this lane groups on scalars only
    and will not invent a flattening rule the writer never agreed to.
    """
    play, _path, _note = _pd.play_of(inputs_as_of)
    return play if isinstance(play, str) and play.strip() else PLAY_UNCLASSIFIED


# ═══════════════════════════════════════════════════════════════════════════
# Normalization — one banked row -> one blotter row
# ═══════════════════════════════════════════════════════════════════════════
#
# THE PINNED CONTRACT KEYS. Lane B builds against this list verbatim, so it is
# stated here as data and asserted key-for-key by the test suite: a rename or a
# quiet drop fails the build rather than blanking a column in someone's UI.
BLOTTER_CONTRACT_KEYS = (
    "entry_id", "trade_date", "pnode_id", "author",
    "direction", "size_mw", "price_limit", "hour_scope",
    "status", "unsettled_reason",
    "settle_da", "settle_fmm", "pnl_per_mwh", "pnl_dollars",
    "settled_at", "settled_by_version",
    "rationale", "inputs_as_of", "curve_points",
)

# Beyond the pinned list, and load-bearing. `play` is here because the by_play
# stanza groups on it and a client colouring a blotter row by play would
# otherwise have to re-derive it out of `inputs_as_of` — two implementations of
# the same rule, one of which will drift. It is served, not recomputed.
BLOTTER_ADDITIVE_KEYS = ("play",)


def normalize_bid(row: dict) -> dict:
    """One `paper_journal` row (+ its curve count) -> one wire-shaped row.

    Money is rounded to the cent HERE, once, so every aggregate downstream sums
    the same figures the blotter displays. `settle_da` / `settle_fmm` are
    carried for display and are never operated on.

    `inputs_as_of` GOES ON THE WIRE IN THIS LANE. That is a deliberate contract
    difference from `/api/desk/blotter`, which withholds it as a heavy jsonb
    blob. Measured 2026-08-06 it is 36 kB across all 14 bids (2.6 kB on the
    largest row) and the page is an audit surface, not a position list — the
    whole point is that a reader can see what the bid was taken on. Worth
    re-measuring if the journal reaches the low thousands of rows.
    """
    return {
        "entry_id": _pd._i(row.get("entry_id")),
        "trade_date": _pd._iso_d(row.get("trade_date")),
        "pnode_id": _pd._text(row.get("pnode_id")),
        "author": _pd._text(row.get("author")),
        "direction": _pd._text(row.get("direction")),
        "size_mw": _pd._r(_pd._f(row.get("size_mw")), MW_DP),
        "price_limit": _pd._r(_pd._f(row.get("price_limit")), RATE_DP),
        "hour_scope": _pd._text(row.get("hour_scope")),
        "status": status_of(row),
        # VERBATIM — never mapped to a code, never truncated. Naturally null on
        # a settled row (the writer clears it), and the contract says so; the
        # key is always present so absence reads as null, not as a missing
        # field.
        "unsettled_reason": _pd._text(row.get("unsettled_reason")),
        "settle_da": _pd._r(_pd._f(row.get("settle_da")), RATE_DP),
        "settle_fmm": _pd._r(_pd._f(row.get("settle_fmm")), RATE_DP),
        "pnl_per_mwh": _pd._r(_pd._f(row.get("pnl_per_mwh")), RATE_DP),
        "pnl_dollars": _pd._r(_pd._f(row.get("pnl_dollars")), MONEY_DP),
        # UTC-normalized — the curve buckets on this string's date. See _iso_utc.
        "settled_at": _iso_utc(row.get("settled_at")),
        "settled_by_version": _pd._i(row.get("settled_by_version")),
        "rationale": _pd._text(row.get("rationale")),
        # The jsonb, as psycopg hands it back. Not reshaped, not pruned.
        "inputs_as_of": row.get("inputs_as_of"),
        # A COUNT of `paper_bid_curves` rows for this entry, not the curve.
        # Absent (no join row) is 0, not null: a bid with no banked curve has
        # zero curve points, which is a fact, not an unknown.
        "curve_points": _pd._i(row.get("curve_points")) or 0,
        "play": play_of(row.get("inputs_as_of")),
    }


def bid_rows(rows) -> list:
    """Bids only, normalized, newest first.

    The SQL filters `kind = 'bid'` and so does this — a widened query cannot
    leak the journal's 24 prose notes into a position count.
    """
    bids = [r for r in (rows or []) if (r.get("kind") or BID_KIND) == BID_KIND]
    out = [normalize_bid(r) for r in bids]
    out.sort(key=lambda b: (b["trade_date"] or "", b["entry_id"] or 0),
             reverse=True)
    return out


# ═══════════════════════════════════════════════════════════════════════════
# The aggregate core — voids excluded, once, in one place
# ═══════════════════════════════════════════════════════════════════════════

def _settled(rows: list) -> list:
    return [b for b in rows if b["status"] == STATUS_SETTLED]


def _count(rows: list, status: str) -> int:
    return sum(1 for b in rows if b["status"] == status)


def _stats(rows: list) -> dict:
    """The shared per-group tally. VOIDS ARE EXCLUDED FROM EVERY FIGURE HERE —
    they are counted separately, by the caller, so the exclusion is impossible
    to forget in one aggregate and remember in another.
    """
    settled = _settled(rows)
    dollars = [b["pnl_dollars"] for b in settled if b["pnl_dollars"] is not None]
    rates = [b["pnl_per_mwh"] for b in settled if b["pnl_per_mwh"] is not None]
    return {
        "n_settled": len(settled),
        "n_pending": _count(rows, STATUS_PENDING),
        "n_void": _count(rows, STATUS_VOID),
        "wins": sum(1 for d in dollars if d > 0),
        # A SUM: zero settled rows sum to 0.0, which is the arithmetic truth
        # and stays addable across groups. n_settled sits beside it.
        "total_pnl": _pd._r(sum(dollars), MONEY_DP),
        # An AVERAGE: null with no samples, never 0.0.
        "avg_pnl_per_mwh": _pd._r(_pd._mean(rates), RATE_DP),
    }


def _rank(groups: list, key_name: str) -> list:
    """Most P&L first; groups with nothing settled sort last but STAY VISIBLE.

    A void-only node dropped from this list would take its voids off the page
    with it, which is exactly what this lane exists to prevent.
    """
    return sorted(
        groups,
        key=lambda g: (g["n_settled"] == 0, -(g["total_pnl"] or 0.0),
                       str(g[key_name] or "")),
    )


# ═══════════════════════════════════════════════════════════════════════════
# Stanza: equity — cumulative settled P&L by settled_at date
# ═══════════════════════════════════════════════════════════════════════════

def equity(bids: list) -> tuple:
    """-> (points, receipt).

    One point per UTC calendar date of `settled_at` that carries a settlement.
    A settled row with NO `settled_at` cannot be placed on a curve; it is
    excluded and counted in the receipt rather than parked on a fabricated
    date or dropped in silence.
    """
    per_date: dict = {}
    undated = 0
    for b in _settled(bids):
        day = (b["settled_at"] or "")[:10]
        if not day:
            undated += 1
            continue
        per_date[day] = per_date.get(day, 0.0) + (b["pnl_dollars"] or 0.0)

    points, running = [], 0.0
    for day in sorted(per_date):
        running = round(running + per_date[day], MONEY_DP)
        points.append({"date": day, "cumulative_pnl": running})

    return points, {
        "basis": "settled_at (UTC calendar date), not trade_date",
        "rule": CURVE_RULE,
        "settled_rows_without_settled_at": undated,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Stanza: by_node / by_play
# ═══════════════════════════════════════════════════════════════════════════

def _grouped(bids: list, key_name: str, key_fn, contract_keys: tuple) -> list:
    buckets: dict = {}
    for b in bids:
        buckets.setdefault(key_fn(b), []).append(b)
    groups = []
    for key, rows in buckets.items():
        s = _stats(rows)
        groups.append({key_name: key, **{k: s[k] for k in contract_keys},
                       "n_void": s["n_void"]})
    return _rank(groups, key_name)


# The pinned per-group key lists, stated as data for the same reason as
# BLOTTER_CONTRACT_KEYS. `n_void` is additive on both and load-bearing: without
# it a node whose every bid was voided renders as a row of zeroes
# indistinguishable from a node that filed nothing at all. Today that is
# LUNDY_7_N003 — 7 bids, every one of them voided.
BY_NODE_CONTRACT_KEYS = ("n_settled", "n_pending", "wins", "total_pnl",
                         "avg_pnl_per_mwh")
BY_PLAY_CONTRACT_KEYS = ("n_settled", "wins", "total_pnl")
BY_NODE_ADDITIVE_KEYS = ("n_void",)
# by_play also carries n_pending additively, so the two aggregate tables read
# the same way; a play with 5 pending bids and 1 settled is not "1 bid".
BY_PLAY_ADDITIVE_KEYS = ("n_pending", "n_void")


def by_node(bids: list) -> list:
    """Per-pnode aggregates over every bid the desk filed at that node."""
    return _grouped(bids, "pnode_id", lambda b: b["pnode_id"],
                    BY_NODE_CONTRACT_KEYS)


def by_play(bids: list) -> list:
    """Per-screen aggregates. The key is live — see the PLAY block."""
    return _grouped(bids, "play", lambda b: b["play"],
                    BY_PLAY_CONTRACT_KEYS + ("n_pending",))


# ═══════════════════════════════════════════════════════════════════════════
# Stanza: book — and the voids it counts
# ═══════════════════════════════════════════════════════════════════════════

BOOK_CONTRACT_KEYS = ("settled_pnl", "n_settled", "n_pending", "n_void")


def book(bids: list) -> dict:
    """Desk totals. `settled_pnl` is exactly the sum of the blotter's displayed
    `pnl_dollars` column, and exactly the curve's last `cumulative_pnl`.

    `n_void` is the count this whole lane exists to surface: it is NOT folded
    into pending, and n_settled + n_pending + n_void == len(blotter), always.
    """
    s = _stats(bids)
    return {
        "settled_pnl": s["total_pnl"],
        "n_settled": s["n_settled"],
        "n_pending": s["n_pending"],
        "n_void": s["n_void"],
    }


# ═══════════════════════════════════════════════════════════════════════════
# The payload
# ═══════════════════════════════════════════════════════════════════════════

def play_coverage(bids: list) -> dict:
    """How much of the blotter the play key actually reaches, and the finding.

    This is the stanza that keeps the lane honest about the one thing the
    Trading Room capture flagged. It reports the measurement, not a promise.
    """
    keyed = sum(1 for b in bids if b["play"] != PLAY_UNCLASSIFIED)
    return {
        "rule": PLAY_RULE,
        "paths_searched": [".".join(p) for p in _pd.PLAY_KEY_PATHS],
        "keyed_n": keyed,
        "unclassified_n": len(bids) - keyed,
        "available": bool(bids) and keyed == len(bids),
        "finding": (
            "the writer's play key is LIVE. inputs_as_of.bid.screen — filed as "
            "a one-field fix by the /api/desk/by-play handoff on 2026-07-31, "
            "when the screen existed only in the rationale prose — is now "
            "stamped, and this lane groups on it. Rows filed before the writer "
            "took the fix carry no key and read '" + PLAY_UNCLASSIFIED + "'; "
            "they are not backfilled and no play is guessed for them. The "
            "rationale prose is never parsed."),
    }


def payload(rows) -> dict:
    """The whole response body, minus the route layer's `as_of` stamp.

    ONE read in, one payload out. Every stanza is derived from the same list of
    normalized rows, so the blotter, the curve, both aggregates and the book
    can never disagree about the same instant.
    """
    bids = bid_rows(rows)
    points, curve_receipt = equity(bids)
    return {
        "blotter": bids,
        "equity": points,
        "by_node": by_node(bids),
        "by_play": by_play(bids),
        "book": book(bids),
        # Additive, and the reason this lane is readable without the handoff:
        # every derivation it performs, stated where the payload is served.
        "derivation": {
            "status": STATUS_RULE,
            "voids": VOID_RULE,
            "equity": curve_receipt,
            "play": play_coverage(bids),
            "win": WIN_RULE,
            "avg": AVG_RULE,
            "sign": SIGN_RULE,
            "mark": MARK_RULE,
        },
    }
